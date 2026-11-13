#!/usr/bin/env python3
"""Before/after measurement for the performance work in `pref_report.md`.

    # 1. copy the real library to a scratch directory on the SAME filesystem as the live one
    .venv/bin/python tools/measure_perf.py seed --from ~/.local/share/boxganizer --to ~/perf-lab

    # 2. serve that copy (a server this tool does not own: it measures whatever it is pointed at)
    BOXGANIZER_DB=~/perf-lab/boxes.db box serve --port 8450 &

    # 3. measure
    .venv/bin/python tools/measure_perf.py run --base http://127.0.0.1:8450 \
        --db ~/perf-lab/boxes.db --json /tmp/before.json

    # ... change the code, restart the server, then:
    .venv/bin/python tools/measure_perf.py run --base http://127.0.0.1:8450 \
        --db ~/perf-lab/boxes.db --label after --compare /tmp/before.json

**It refuses to run on a library that is not marked as a scratch copy.** One of the numbers it
takes -- `GET /api/index/status?check=1` -- *writes*: it re-derives every file's freshness flag and
stores the answer. Measuring that against a live library rewrites the library, which is precisely
the mistake `pref_report.md` §8 records being made by hand once already. The marker is written by
`seed`; `run` will not create it.

**Measure on a real disk, not `/tmp`.** Here `/tmp` is tmpfs, where fsync is nearly free, and the
slowest request in this project's history looked perfectly healthy there (0.025 s) while taking
5 s on ext4. The copy therefore goes wherever the live library lives.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import statistics
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

SCRATCH_MARKER = "PERF-SCRATCH"
WRITES_PER_WRITER = 40
WRITERS = 3

#: Lower is better, and more than this much worse fails the comparison. A single machine with two
#: cores and a browser open is not a quiet instrument, so the tolerance is honest rather than tight.
REGRESSION_RATIO = 1.25

#: Metrics that must be clean once the work is done, whatever the baseline said.
MUST_BE_CLEAN = ("lock_errors_total",)


# -------------------------------------------------------------------------- scratch guard


def scratch_marker(db: Path) -> Path:
    return db.parent / SCRATCH_MARKER


def require_scratch(db: Path) -> None:
    """Stop unless this database is a copy somebody declared safe to write to."""
    resolved = db.expanduser().resolve()
    live = Path.home() / ".local/share/boxganizer/boxes.db"
    if resolved == live.resolve():
        sys.exit(f"refusing: {resolved} is the live library. Copy it with the seed command first.")
    if not scratch_marker(resolved).exists():
        sys.exit(
            f"refusing: {resolved} has no {SCRATCH_MARKER} file in {resolved.parent}.\n"
            "These measurements write (a status check re-stores every file's freshness flag).\n"
            "Make a copy with:  measure_perf.py seed --from <live dir> --to <scratch dir>"
        )


def cmd_seed(args: argparse.Namespace) -> int:
    source = Path(args.source).expanduser().resolve()
    target = Path(args.target).expanduser().resolve()
    source_db = source if source.is_file() else source / "boxes.db"
    if not source_db.is_file():
        sys.exit(f"no database at {source_db}")
    target.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_db, target / "boxes.db")
    for folder in ("files", "thumbnails"):
        origin = source / folder
        if origin.is_dir():
            shutil.copytree(origin, target / folder, dirs_exist_ok=True)
    marker = target / SCRATCH_MARKER
    marker.write_text(
        f"copied from {source_db} at {time.strftime('%Y-%m-%dT%H:%M:%S')}\n"
        "Safe to write: this is a copy, and measure_perf.py run checks for this file.\n",
        encoding="utf-8",
    )
    digest = __import__("hashlib").sha256(source_db.read_bytes()).hexdigest()
    print(f"copied {source_db} -> {target / 'boxes.db'}")
    print(f"live source sha256: {digest}")
    print(f"marker: {marker}")
    return 0


# -------------------------------------------------------------------------- HTTP helpers


def fetch(base: str, path: str, *, payload: dict[str, Any] | None = None,
          headers: dict[str, str] | None = None,
          method: str | None = None) -> tuple[float, int, bytes, dict[str, str]]:
    """(seconds, status, body, headers) for one request; errors are answers too.

    A POST carries a JSON content type because it must: without one the server declines the body
    before reading it, and the first version of this measurement timed that refusal -- 0.7 ms, a
    flattering number for a request that never happened.
    """
    body = None if payload is None else json.dumps(payload).encode()
    sent = {"content-type": "application/json"} if body is not None else {}
    sent.update(headers or {})
    request = urllib.request.Request(f"{base}{path}", data=body, headers=sent, method=method)
    began = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            elapsed = time.perf_counter() - began
            return elapsed, response.status, response.read(), _lower(response.headers)
    except urllib.error.HTTPError as exc:  # a 4xx is a measurement, not a crash
        elapsed = time.perf_counter() - began
        return elapsed, exc.code, exc.read(), _lower(exc.headers)


def _lower(message: object) -> dict[str, str]:
    """Response headers, keyed in lower case.

    uvicorn sends `content-encoding` and `cache-control` in lower case, and a dict lookup
    cares: the first version of this file asked for `Content-Encoding` and was told "none"
    for every response, including the ones that were compressed. A header reader that can
    miss the thing it is looking for is worse than no reader, because it looks like an answer.
    """
    return {str(key).lower(): str(value) for key, value in dict(message).items()}  # type: ignore[arg-type]


def median_seconds(base: str, path: str, samples: int,
                   payload: dict[str, Any] | None = None) -> float:
    timings = [fetch(base, path, payload=payload)[0] for _ in range(samples)]
    return round(statistics.median(timings), 4)


def wait_for_server(base: str, seconds: float = 60.0) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{base}/api/health", timeout=3) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            time.sleep(0.5)
    sys.exit(f"no server answered at {base} within {seconds:g}s")


# -------------------------------------------------------------------------- the metrics


def measure_writes(base: str, samples: int) -> dict[str, float]:
    """A save, timed, and taken back out again -- median *and* spread.

    The box is deleted afterwards because a baseline has to be comparable with the run after it:
    boxes left behind here change the row count every other number depends on.

    The spread travels with the median on purpose. Seven samples of a ~100 ms request on this
    box ranged 77-320 ms across two runs of *identical* code, and a first comparison called that
    a 35% regression. A median with no spread next to it invites exactly that mistake.
    """
    timings = []
    for attempt in range(samples):
        name = f"Perf Probe {attempt}-{int(time.time())}"
        made = fetch(base, "/api/boxes", payload={"name": name, "box_type": "note",
                                                  "text": "measuring the write path"})
        if made[1] not in (200, 201):
            sys.exit(f"the write probe was refused ({made[1]}): {made[2][:160]!r}")
        timings.append(made[0])
        # Taken back out: a baseline has to be comparable with the run after it, and boxes left
        # behind here move the row count every other number depends on.
        uuid = json.loads(made[2])["uuid"]
        removed = fetch(base, f"/api/boxes/{uuid}", method="DELETE")
        if removed[1] not in (200, 204):
            print(f"  note: could not delete the probe box ({removed[1]})")
    return {
        "median_s": round(statistics.median(timings), 4),
        "min_s": round(min(timings), 4),
        "max_s": round(max(timings), 4),
    }


def measure_overlap(base: str) -> dict[str, float]:
    """Two freshness checks at once: they used to queue rather than share, doubling both."""
    each: list[float] = []
    lock = threading.Lock()

    def one() -> None:
        seconds = fetch(base, "/api/index/status?check=1")[0]
        with lock:
            each.append(seconds)

    threads = [threading.Thread(target=one) for _ in range(2)]
    began = time.perf_counter()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    wall = round(time.perf_counter() - began, 3)
    return {"overlap_each_max_s": round(max(each), 3), "overlap_wall_s": wall}


def measure_lock_errors(db: Path) -> int:
    """Concurrent writers against the real file, counting the ones SQLite turned away.

    The connections are opened the way the store opens them, with its own busy timeout read
    from the module rather than a number chosen here. Pinning a timeout in this file would
    have measured the tool instead of the code: the first version hardcoded ``timeout=0``,
    so it kept reporting the pre-fix behaviour after the fix landed and the number looked
    like 93 refusals that no caller could cause any more.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from boxganizer.store import BUSY_TIMEOUT_MS

    timeout_seconds = BUSY_TIMEOUT_MS / 1000
    errors: list[int] = []

    def writer() -> None:
        failed = 0
        connection = sqlite3.connect(str(db), timeout=timeout_seconds)
        try:
            for row in range(WRITES_PER_WRITER):
                try:
                    connection.execute("UPDATE resources SET stale = stale WHERE id = ?",
                                       (1 + (row % 50),))
                    connection.commit()
                except sqlite3.OperationalError:
                    failed += 1
                time.sleep(0.01)
        finally:
            connection.close()
        errors.append(failed)

    threads = [threading.Thread(target=writer) for _ in range(WRITERS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return sum(errors)


def measure_index_counts(db: Path, samples: int) -> float:
    """The query behind the panel's index line, in-process, median of `samples`."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from boxganizer.store import BoxStore

    store = BoxStore(db)
    try:
        store.index_counts()  # warm: first call pays the schema and the page cache
        timings = []
        for _ in range(samples):
            began = time.perf_counter()
            store.index_counts()
            timings.append(time.perf_counter() - began)
    finally:
        store.close()
    return round(statistics.median(timings) * 1000, 3)


def measure_static(base: str) -> dict[str, Any]:
    """Bytes on the wire, plain and offered-gzip, plus a binary that must stay alone.

    The plain size is requested *without* `Accept-Encoding` rather than read off a compressed
    response: the first version of this file quoted a compression ratio it had never measured,
    and a ratio off a guess is how a report ends up promising something the code does not do.
    """
    out: dict[str, Any] = {}
    for name, path in (("app_js", "/static/app.js"), ("app_css", "/static/app.css")):
        plain = fetch(base, path)
        offered = fetch(base, path, headers={"Accept-Encoding": "gzip"})
        out[f"{name}_bytes"] = len(plain[2])
        out[f"{name}_bytes_offered_gzip"] = len(offered[2])
        out[f"{name}_encoding"] = offered[3].get("content-encoding", "none")
    # A warm reload: the asset is asked for again with the validator the server handed out,
    # and the answer should be "unchanged", not another 436 KB.
    # Asked for again by name rather than reused from the loop above: the loop's `plain` is
    # whatever it last held, and one version of this file sent app.css's ETag against app.js,
    # which is a 200 with the whole body rather than the 304 it was reporting as proof.
    fresh = fetch(base, "/static/app.js")
    validator = fresh[3].get("etag")
    again = fetch(base, "/static/app.js",
                  headers={"If-None-Match": validator} if validator else None)
    out["warm_reload_status"] = again[1]
    out["warm_reload_bytes"] = len(again[2])
    out["static_cache_control"] = fresh[3].get("cache-control", "none")
    boxes = fetch(base, "/api/boxes")
    offered_api = fetch(base, "/api/boxes", headers={"Accept-Encoding": "gzip"})
    out["api_boxes_bytes"] = len(boxes[2])
    out["api_boxes_bytes_offered_gzip"] = len(offered_api[2])
    if boxes[1] == 200:
        pdf = next((b for b in json.loads(boxes[2]) if b.get("box_type") == "pdf"), None)
        if pdf:
            raw = fetch(base, f"/api/boxes/{pdf['uuid']}/raw",
                        headers={"Accept-Encoding": "gzip"})
            out["pdf_bytes"] = len(raw[2])
            out["pdf_encoding"] = raw[3].get("content-encoding", "none")
    return out


def cmd_run(args: argparse.Namespace) -> int:
    base = args.base.rstrip("/")
    db = Path(args.db).expanduser()
    require_scratch(db)
    wait_for_server(base)

    print(f"measuring {base} against {db} ({args.label})")
    results: dict[str, Any] = {
        "label": args.label,
        "base": base,
        "db": str(db.resolve()),
        "taken_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "index_status_plain_s": median_seconds(base, "/api/index/status", args.samples),
        "index_status_check_s": median_seconds(
            base, "/api/index/status?check=1", args.check_samples),
        "index_pending_s": median_seconds(base, "/api/index/pending", args.samples),
        "write_probe": measure_writes(base, args.write_samples),
        "search_content_s": median_seconds(base, "/api/search?q=fence", args.samples),
        "index_counts_ms": measure_index_counts(db, args.samples),
        "static": measure_static(base),
    }
    probe = results.pop("write_probe")
    results["write_s"] = probe["median_s"]
    results["write_spread_s"] = [probe["min_s"], probe["max_s"]]
    results.update(measure_overlap(base))
    results["lock_errors_total"] = measure_lock_errors(db)

    for key, value in results.items():
        if key not in ("static", "write_spread_s"):
            print(f"  {key:24s} {value}")
    spread = results.get("write_spread_s")
    if spread:
        print(f"  {'write spread (min..max)':24s} {spread[0]:.3f} .. {spread[1]:.3f}"
              "   <- a median inside this spread is not a difference")
    for key, value in results["static"].items():
        print(f"  {'static.' + key:24s} {value}")

    if args.json:
        Path(args.json).expanduser().write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"wrote {args.json}")
    if args.compare:
        return compare(Path(args.compare).expanduser(), results)
    return 0


# -------------------------------------------------------------------------- comparison

#: Which numbers are "lower is better" and therefore comparable run to run.
LOWER_BETTER = (
    "index_status_plain_s",
    "index_status_check_s",
    "index_pending_s",
    "write_s",
    "search_content_s",
    "index_counts_ms",
    "overlap_each_max_s",
    "overlap_wall_s",
    "lock_errors_total",
)


def compare(baseline_path: Path, current: dict[str, Any]) -> int:
    """Print before/after side by side; fail if anything got materially worse."""
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    print(f"\n{'metric':26s} {'before':>12s} {'after':>12s} {'change':>9s}")
    problems: list[str] = []
    before_spread = baseline.get("write_spread_s")
    after_spread = current.get("write_spread_s")
    if before_spread and after_spread:
        print(f"{'write spread min..max':26s} {before_spread[0]:.3f}..{before_spread[1]:.3f}"
              f"   {after_spread[0]:.3f}..{after_spread[1]:.3f}")
    for key in LOWER_BETTER:
        if key not in baseline or key not in current:
            continue
        before, after = float(baseline[key]), float(current[key])
        ratio = (after / before) if before else float("inf")
        mark = ""
        # A noisy metric gets a noisy threshold: for the save path, "worse" means the after
        # median landed beyond where the before runs were ever observed, not merely that two
        # medians differ -- they differed by 35% on unchanged code.
        floor = max(before * REGRESSION_RATIO, float(before_spread[1]) if before_spread else 0.0)
        if key == "write_s":
            worse = after > floor and after - before > 0.02
        else:
            worse = after > before * REGRESSION_RATIO and after - before > 0.02
        if worse:
            mark = "  WORSE"
            problems.append(f"{key}: {before} -> {after}")
        elif ratio < 1 / REGRESSION_RATIO:
            mark = "  better"
        print(f"{key:26s} {before:12.4g} {after:12.4g} {ratio:8.2f}x{mark}")
    for key in MUST_BE_CLEAN:
        if current.get(key):
            problems.append(f"{key} must be 0, measured {current[key]}")
    for label, key in (
        ("app.js plain bytes", "app_js_bytes"),
        ("app.js gzip bytes", "app_js_bytes_offered_gzip"),
        ("app.css plain bytes", "app_css_bytes"),
        ("app.css gzip bytes", "app_css_bytes_offered_gzip"),
        ("api /boxes gzip bytes", "api_boxes_bytes_offered_gzip"),
    ):
        was = baseline.get("static", {}).get(key)
        now = current.get("static", {}).get(key)
        if was and now:
            print(f"{label:26s} {was:12d} {now:12d} {now / was:8.2f}x")
    if current.get("static", {}).get("pdf_encoding", "none") != "none":
        problems.append(f"the PDF was compressed ({current['static']['pdf_encoding']})")
    if current.get("static", {}).get("warm_reload_status") not in (200, 304):
        warm = current.get("static", {}).get("warm_reload_status")
        problems.append(f"warm reload did not revalidate: {warm}")
    if problems:
        print("\nREGRESSION:" + "\n  - ".join(["", *problems]))
        return 1
    print("\nno regression detected")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)

    seed = commands.add_parser("seed", help="copy a library and mark the copy as safe to measure")
    seed.add_argument("--from", dest="source", required=True, help="live library directory or db")
    seed.add_argument("--to", dest="target", required=True, help="scratch directory to create")

    run = commands.add_parser("run", help="measure a running server and its database")
    run.add_argument("--base", required=True, help="http://127.0.0.1:PORT")
    run.add_argument("--db", required=True, help="the scratch database it is serving")
    run.add_argument("--label", default="before")
    run.add_argument("--samples", type=int, default=7)
    run.add_argument("--write-samples", dest="write_samples", type=int, default=15,
                     help="more than the others: a save is ~100 ms and the box is busy")
    run.add_argument("--check-samples", dest="check_samples", type=int, default=3,
                     help="fewer, because the slow one is the point of the wait")
    run.add_argument("--json", help="write these numbers here")
    run.add_argument("--compare", help="a previous --json file, to print before/after")

    args = parser.parse_args()
    if args.command == "seed":
        return cmd_seed(args)
    return cmd_run(args)


if __name__ == "__main__":
    raise SystemExit(main())
