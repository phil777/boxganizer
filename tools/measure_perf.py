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
            return elapsed, response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as exc:  # a 4xx is a measurement, not a crash
        elapsed = time.perf_counter() - began
        return elapsed, exc.code, exc.read(), dict(exc.headers)


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


def measure_writes(base: str, samples: int) -> float:
    """A save, timed, and taken back out again.

    The box is deleted afterwards because a baseline has to be comparable with the run after it:
    boxes left behind here change the row count every other number depends on.
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
    return round(statistics.median(timings), 4)


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
    """Concurrent writers against the real file, counting the ones SQLite turned away."""
    errors: list[int] = []

    def writer() -> None:
        failed = 0
        connection = sqlite3.connect(str(db), timeout=0.0)
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
    """Bytes on the wire, plain and offered-gzip, plus a binary that must stay alone."""
    plain = fetch(base, "/static/app.js")
    offered = fetch(base, "/static/app.js", headers={"Accept-Encoding": "gzip"})
    out: dict[str, Any] = {
        "app_js_bytes": len(plain[2]),
        "app_js_bytes_offered_gzip": len(offered[2]),
        "app_js_encoding": offered[3].get("Content-Encoding", "none"),
    }
    boxes = fetch(base, "/api/boxes")
    if boxes[1] == 200:
        pdf = next((b for b in json.loads(boxes[2]) if b.get("box_type") == "pdf"), None)
        if pdf:
            raw = fetch(base, f"/api/boxes/{pdf['uuid']}/raw",
                        headers={"Accept-Encoding": "gzip"})
            out["pdf_bytes"] = len(raw[2])
            out["pdf_encoding"] = raw[3].get("Content-Encoding", "none")
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
        "write_s": measure_writes(base, args.samples),
        "search_content_s": median_seconds(base, "/api/search?q=fence", args.samples),
        "index_counts_ms": measure_index_counts(db, args.samples),
        "static": measure_static(base),
    }
    results.update(measure_overlap(base))
    results["lock_errors_total"] = measure_lock_errors(db)

    for key, value in results.items():
        if key not in ("static",):
            print(f"  {key:24s} {value}")
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
    for key in LOWER_BETTER:
        if key not in baseline or key not in current:
            continue
        before, after = float(baseline[key]), float(current[key])
        ratio = (after / before) if before else float("inf")
        mark = ""
        if after > before * REGRESSION_RATIO and after - before > 0.02:
            mark = "  WORSE"
            problems.append(f"{key}: {before} -> {after}")
        elif ratio < 1 / REGRESSION_RATIO:
            mark = "  better"
        print(f"{key:26s} {before:12.4g} {after:12.4g} {ratio:8.2f}x{mark}")
    for key in MUST_BE_CLEAN:
        if current.get(key):
            problems.append(f"{key} must be 0, measured {current[key]}")
    before_bytes = baseline.get("static", {}).get("app_js_bytes")
    after_bytes = current.get("static", {}).get("app_js_bytes_offered_gzip")
    if before_bytes and after_bytes:
        print(f"{'app.js bytes (plain/gzip)':26s} {before_bytes:12d} {after_bytes:12d}"
              f" {after_bytes / before_bytes:8.2f}x")
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
