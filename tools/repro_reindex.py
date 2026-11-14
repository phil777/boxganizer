#!/usr/bin/env python3
"""Reproduce the reindex loop: press Reindex, poll the way the page does, print what changes.

Written for one report in `work_to_do.md`: *"there are 833 to reindex; when I ask for reindexing
it does something, about 10 calls to /api/index/status, then it stops and says 833 to reindex
again"*. The polling here mirrors the page — 1.2 seconds, the same endpoint, the same headline
formula from `app.js` — so the log can be read against the symptom directly instead of against a
guess about it.

Two things it measures that a screen does not show:

* `--hammer` issues status reads *while* the pass runs and reports the latency distribution,
  which is how the page came to look like it had stopped (`pref_report.md` §8 of
  `reindex_diagnosis.md`): nothing failed, the reads just slowed to seconds.
* `--old-build` runs the code from the commit before the performance work, from a tree staged
  with `git archive`, so a claim about what the user's running server does can be tested rather
  than inferred from its uptime.

The library it points at must be a scratch copy: a pass *writes* (that is the whole job), and the
tool refuses an unmarked target for the same reason `measure_perf.py` does — the first run of this
file's ancestor wrote to a real library by accident.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

SCRATCH_MARKER = "PERF-SCRATCH"
OLD_BUILD_SRC = "/tmp/old-build/src"


def require_scratch(db: Path) -> None:
    """Refuse to run a pass against anything not marked as a copy."""
    marker = db.parent / SCRATCH_MARKER
    if not marker.exists():
        sys.exit(f"{db.parent} has no {SCRATCH_MARKER} file; seed a copy with measure_perf.py seed")


def get(base: str, path: str, timeout: float = 600.0) -> tuple[int, float, dict]:
    """(status, seconds, body) for one read; a failure is an answer, not a crash."""
    began = time.perf_counter()
    try:
        with urllib.request.urlopen(f"{base}{path}", timeout=timeout) as reply:
            return reply.status, time.perf_counter() - began, json.loads(reply.read())
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            detail = raw[:200].decode(errors="replace")
            return exc.code, time.perf_counter() - began, {"detail": detail}
        except Exception:  # pragma: no cover - a non-JSON error body
            return exc.code, time.perf_counter() - began, {"detail": "<not json>"}
    except Exception as exc:
        return type(exc).__name__, time.perf_counter() - began, {"detail": str(exc)[:200]}


def post(base: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    request = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(payload or {}).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=600) as reply:
            return reply.status, json.loads(reply.read())
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw)
        except Exception:  # pragma: no cover - a non-JSON error body
            return exc.code, {"detail": raw[:200].decode(errors="replace")}


def headline(body: dict) -> str:
    """What the left pane says at this tree: the offer is the server's work count (ADR 44).

    Mirrors `indexWords`/`indexWaiting` in app.js line for line, including the two nouns: "reindex"
    only when something was read and has since changed. It reads `missing` rather than subtracting
    gone folders here, because the whole point of ADR 44 is that the split is the server's to make —
    a tool that re-derived it would drift from the page the way the old formula already did.
    """
    missing = body.get("missing") or {}
    waiting = (
        int(missing["work"]) if isinstance(missing.get("work"), int) else headline_offered(body)
    )
    reindex_work = (
        int(missing["work_stale"])
        if isinstance(missing.get("work_stale"), int)
        else int(body.get("stale", 0))
    )
    if reindex_work:
        return f"{waiting} to reindex"
    if waiting:
        return f"{waiting} to index"
    if not int(body.get("wanted", 0)):
        return "nothing to index"
    return f"{int(body.get('indexed', 0))} indexed"


def headline_offered(body: dict) -> int:
    """The pre-ADR-44 arithmetic: stale plus untouched, minus what sits under a missing root."""
    waiting = int(body.get("stale", 0)) + int(body.get("untouched", 0))
    gone = sum(int(root.get("waiting", 0)) for root in body.get("missing_roots", []) or [])
    return max(waiting - gone, 0)


def headline_pre44(body: dict) -> str:
    """The wording the pane used before ADR 44, kept so both are printed and compared.

    On a library whose backlog is mostly folders that are not on this machine, this is the
    number that never comes down — it counts resources no reindex can reach. It is still the only
    reading available from `--old-build`, whose status answer has no `missing` at all, and it is
    what the un-amended tables in `reindex_diagnosis.md` were measured with.
    """
    offered = headline_offered(body)
    if int(body.get("stale", 0)):
        return f"{offered} to reindex"
    if offered:
        return f"{offered} to index"
    return "nothing to index"


def counts(body: dict) -> str:
    keys = ("wanted", "indexed", "outstanding", "stale", "untouched", "folders", "unreadable")
    return " ".join(f"{k}={body.get(k, '?')}" for k in keys)


def hammer(base: str, seconds: float, out: list[tuple[int, float]]) -> None:
    """Read the status endpoint every 1.2 s until told to stop, recording (code, seconds)."""
    stop = time.perf_counter() + seconds
    while time.perf_counter() < stop:
        code, secs, _body = get(base, "/api/index/status", timeout=40)
        out.append((code if isinstance(code, int) else -1, secs))
        if not isinstance(code, int):
            out[-1] = (-1, secs)
        time.sleep(1.2)


def report_hammer(samples: list[tuple[int, float]]) -> str:
    if not samples:
        return "no samples"
    codes: dict[int, int] = {}
    for code, _secs in samples:
        codes[code] = codes.get(code, 0) + 1
    lat = sorted(secs for _code, secs in samples)
    return (
        f"{len(samples)} reads, codes={codes}, latency "
        f"p50={statistics.median(lat):.3f}s max={lat[-1]:.3f}s"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--db", required=True, help="the scratch database to serve")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument(
        "--old-build",
        action="store_true",
        help=f"run the pre-P1 code staged at {OLD_BUILD_SRC} (git archive of 8a52cf7)",
    )
    parser.add_argument("--scope", default="outstanding", choices=("outstanding", "everything"))
    parser.add_argument("--max-polls", type=int, default=600)
    parser.add_argument(
        "--hammer",
        action="store_true",
        help="poll the status endpoint from a second thread during the pass and report latencies",
    )
    parser.add_argument("--log", default="", help="also write this log here")
    args = parser.parse_args()

    db = Path(args.db).expanduser().resolve()
    require_scratch(db)
    env = dict(os.environ, BOXGANIZER_DB=str(db))
    if args.old_build:
        if not Path(OLD_BUILD_SRC).is_dir():
            sys.exit(
                f"{OLD_BUILD_SRC} missing; stage the old code with: "
                "(cd <repo> && git archive 8a52cf7 | tar -x -C /tmp/old-build)"
            )
        env["PYTHONPATH"] = OLD_BUILD_SRC

    base = f"http://127.0.0.1:{args.port}"
    lines: list[str] = []

    def say(line: str) -> None:
        print(line, flush=True)
        lines.append(line)

    build = "pre-P1 (old)" if args.old_build else "HEAD"
    server = subprocess.Popen(
        [sys.executable, "-m", "boxganizer", "serve", "--port", str(args.port)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    samples: list[tuple[int, float]] = []
    try:
        for _ in range(80):
            if get(base, "/api/health", 5)[0] == 200:
                break
            time.sleep(0.5)
        else:
            say(f"[{args.label}] the server never answered (is another process holding {db}?)")
            return 2

        _code, _secs, before = get(base, "/api/index/status")
        say(f"[{args.label}] build={build}  db={db}")
        say(
            f"[{args.label}] before: headline={headline(before)!r} (old rule "
            f"{headline_pre44(before)!r})  {counts(before)}"
        )
        if "observed" in before:
            say(f"[{args.label}] observed (HEAD only): {before['observed']}")

        if args.hammer:
            thread = threading.Thread(
                target=hammer, args=(base, args.max_polls * 1.3, samples), daemon=True
            )
            thread.start()

        code, started = post(base, "/api/index/run", {"scope": args.scope})
        say(f"[{args.label}] POST /api/index/run scope={args.scope} -> {code} {started}")

        polls = 0
        running_seen = 0
        began = time.perf_counter()
        last_running: bool | None = None
        while polls < args.max_polls:
            polls += 1
            _c, _s, body = get(base, "/api/index/status")
            running = bool(body.get("running"))
            running_seen += int(running)
            queue = body.get("queue", {})
            if running != last_running or polls % 20 == 1:
                say(
                    f"[{args.label}] poll {polls:3d} t={time.perf_counter() - began:6.1f}s "
                    f"running={int(running)} scope={body.get('scope') or '-':11s} "
                    f"queue={queue.get('done', '?')}/{queue.get('total', '?')} "
                    f"headline={headline(body)!r}"
                )
                last_running = running
            if polls > 3 and not running:
                break
            time.sleep(1.2)

        _c, _s, after = get(base, "/api/index/status")
        say(f"[{args.label}] polls={polls} (running true on {running_seen}) "
            f"wall={time.perf_counter() - began:.1f}s")
        say(f"[{args.label}] job.last = {json.dumps(after.get('last'))}")
        say(
            f"[{args.label}] after:  headline={headline(after)!r} (old rule "
            f"{headline_pre44(after)!r})  {counts(after)}"
        )
        same = headline(before) == headline(after)
        say(
            f"[{args.label}] VERDICT: {headline(before)!r} -> {headline(after)!r} "
            f"({'UNCHANGED — symptom reproduced' if same else 'changed'})"
        )
        wanted_before, wanted_after = before.get("wanted"), after.get("wanted")
        if isinstance(wanted_before, int) and isinstance(wanted_after, int):
            moved = wanted_after - wanted_before
            say(
                f"[{args.label}] wanted {wanted_before} -> {wanted_after} "
                f"({moved:+d}); negative means the pass un-indexed rows"
            )
        errors = (after.get("queue") or {}).get("errors") or []
        if errors:
            say(f"[{args.label}] queue errors ({len(errors)}): {json.dumps(errors[:3])}")
        if args.hammer:
            say(f"[{args.label}] concurrent reads: {report_hammer(samples)}")
    finally:
        server.terminate()
        try:
            server.wait(timeout=15)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)
    if args.log:
        Path(args.log).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
