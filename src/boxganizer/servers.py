"""Which `box serve` processes are running, on which port, holding which database.

Written because a claim I made in `pref_report.md` was wrong and this is what corrected it. I
wrote that 29 leftover servers were sharing the live library; opening each process's file
descriptors showed **exactly one** had the real database open, and the rest held scratch paths
from an `XDG_DATA_HOME` they were started with. That is a different problem — a pile of
forgotten processes rather than 29 writers on one file — and the difference is only visible
from `/proc`, because a command line does not say what a process actually opened.

So the rule here: **the file descriptors are the answer, the environment is a hint.** Each
served process reports its database as *held open* when a `.db` file descriptor proves it, and
as *declared* when only `BOXGANIZER_DB` or `XDG_DATA_HOME` says so. The two are labelled
differently and never merged, because they mean different things: one is what the process
knows, the other is what somebody asked for.

Every function takes `proc_root` so a test can hand it a directory of made-up processes. On
Linux it defaults to `/proc`; anywhere else the scan comes back empty rather than guessing.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

#: Ticks per second in `/proc` timestamps. POSIX guarantees 100 on Linux; read it anyway.
CLK_TCK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100

#: Where the kernel keeps process information. `scan()` takes an argument for the same reason.
PROC_ROOT = Path("/proc")

#: A process whose command line contains one of these words as an argument is a server.
SERVE_ARGUMENT = "serve"


@dataclass(frozen=True)
class ServeProcess:
    """One running server, as the operating system describes it."""

    pid: int
    ports: tuple[int, ...]
    #: How the port is known: "listening" from the socket table, "command line" as asked for.
    port_source: str
    database: str | None
    #: "held open" (a `.db` file descriptor), "declared" (an environment variable), or "none".
    database_source: str
    cwd: str
    started_at: float
    uptime_seconds: float
    command: str
    #: Resident memory at the moment of the scan. Read during the scan rather than lazily,
    #: so the number belongs to the `/proc` root the scan was told to use -- a property that
    #: reached for a global would report the live system even inside a test.
    rss_bytes: int = 0

    @property
    def uptime_text(self) -> str:
        """Age in the unit a person compares by, not in seconds."""
        minutes = int(self.uptime_seconds // 60)
        if minutes < 60:
            return f"{minutes} min"
        hours, minutes = divmod(minutes, 60)
        if hours < 48:
            return f"{hours} h {minutes} min"
        return f"{hours // 24} d {hours % 24} h"

def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def is_server(cmdline: str) -> bool:
    """Whether this command line is a Boxganizer server.

    Matched on the argument list rather than a substring of the whole line: a person named
    Serve, or a path containing the word, is not a server, and `box serve` started through a
    venv wrapper is.
    """
    parts = [part for part in cmdline.replace("\0", " ").split() if part]
    if SERVE_ARGUMENT not in parts:
        return False
    head = Path(parts[0]).name if parts else ""
    # `python3.14`, `python3`, `python`: the version in the name is not a different program,
    # and a venv interpreter started as `-m boxganizer` says so in the module name.
    return head.startswith("box") or head.startswith("python") or "boxganizer" in cmdline


def _command_line(proc_dir: Path) -> str:
    return _read(proc_dir / "cmdline").strip("\0")


def _started_and_age(proc_dir: Path, uptime_now: float) -> tuple[float, float]:
    """(start time as a wall-clock stamp, age in seconds) for one pid.

    `/proc/<pid>/stat` field 22 is the start in clock ticks since boot, and the `comm` field
    that comes before it is wrapped in parentheses and may contain spaces — so the split is
    anchored on the *last* `)`, which is the only reliable way to count fields from there.
    """
    stat = _read(proc_dir / "stat")
    if ")" not in stat:
        return 0.0, 0.0
    fields = stat.rsplit(")", 1)[1].split()
    if len(fields) < 20:
        return 0.0, 0.0
    try:
        started_ticks = float(fields[19])  # field 22 overall; field 20 after `state`
    except ValueError:
        return 0.0, 0.0
    age = max(0.0, uptime_now - started_ticks / CLK_TCK)
    return time.time() - age, age


def _listening_ports(proc_dir: Path, proc_root: Path) -> tuple[int, ...]:
    """Ports this process is actually listening on, from the kernel's socket table.

    The command line says what was asked for; this says what happened, which is the difference
    between a server on the port you think and the one it answered on. Matching is by socket
    inode: `/proc/net/tcp*` lists an inode per socket, `/proc/<pid>/fd` links to `socket:[n]`.
    """
    inodes = set()
    descriptors = proc_dir / "fd"
    try:
        for entry in descriptors.iterdir():
            try:
                target = os.readlink(entry)
            except OSError:
                continue
            if target.startswith("socket:["):
                inodes.add(target[8:-1])
    except OSError:
        return ()
    if not inodes:
        return ()
    ports: set[int] = set()
    # `/proc/net/tcp`, not `/proc/tcp` -- which is what the first version asked for, found
    # nothing, and reported every port as "command line" instead of failing loudly. A read that
    # returns "" on a missing file cannot tell "no listeners" from "wrong path", so the table
    # path is built once, here, where the missing piece is visible.
    tables = [proc_root / "net" / name for name in ("tcp", "tcp6")]
    if not any(table.is_file() for table in tables):
        return ()
    for table in tables:
        for line in _read(table).splitlines()[1:]:
            columns = line.split()
            # `tx_queue rx_queue` is one token and `tr tm->when` is one token, so the columns
            # are: sl, local, remote, st, txrx, tr, retrnsmt, uid, timeout, inode -> 9.
            # Counting them by eye off the header once cost a whole feature being wrong: the
            # pointer column at 11 reads as "0000000000000000", which is a plausible-looking
            # answer to "who owns this socket" and belongs to nobody.
            if len(columns) < 10 or columns[9] not in inodes:
                continue
            if columns[3] != "0A":  # 0A is TCP_LISTEN; anything else is a connection
                continue
            try:
                ports.add(int(columns[1].rsplit(":", 1)[1], 16))
            except ValueError:  # pragma: no cover - a malformed table is not ours to fix
                continue
    return tuple(sorted(ports))


def _requested_port(cmdline: str) -> int | None:
    """`--port 8123` or `--port=8123` or `-p 8123`, from the command line."""
    parts = [part for part in cmdline.replace("\0", " ").split() if part]
    for index, part in enumerate(parts):
        if part in {"--port", "-p"} and index + 1 < len(parts):
            try:
                return int(parts[index + 1])
            except ValueError:
                return None
        if part.startswith("--port="):
            try:
                return int(part.split("=", 1)[1])
            except ValueError:
                return None
    return None


def _open_databases(proc_dir: Path) -> tuple[str, ...]:
    """`.db` files this process holds open. The only honest answer to "which library"."""
    found: list[str] = []
    descriptors = proc_dir / "fd"
    try:
        for entry in descriptors.iterdir():
            try:
                target = os.readlink(entry)
            except OSError:
                continue
            if target.endswith((".db", ".sqlite", ".sqlite3")):
                found.append(target)
    except OSError:
        return ()
    return tuple(sorted(set(found)))


def _declared_database(proc_dir: Path) -> str | None:
    """Where the environment said the library would be, when nothing proves it is open.

    This is the field that turned my wrong claim into a right one: the leftover servers had no
    database descriptor at all, because each had been started with `XDG_DATA_HOME=/tmp/xdg…`.
    Reporting that as "the live database" — which is what a `ps` listing invites — described 29
    writers that did not exist.
    """
    environ = _read(proc_dir / "environ")
    values: dict[str, str] = {}
    for pair in environ.split("\0"):
        if "=" in pair:
            key, _, value = pair.partition("=")
            values[key] = value
    if values.get("BOXGANIZER_DB"):
        return f"$BOXGANIZER_DB={values['BOXGANIZER_DB']}"
    if values.get("XDG_DATA_HOME"):
        return f"$XDG_DATA_HOME={values['XDG_DATA_HOME']}/boxganizer/boxes.db"
    return None


def _rss_bytes(proc_dir: Path) -> int:
    for line in _read(proc_dir / "status").splitlines():
        if line.startswith("VmRSS:"):
            parts = line.split()
            try:
                return int(parts[1]) * 1024
            except (IndexError, ValueError):  # pragma: no cover - depends on kernel format
                return 0
    return 0


def _cwd(proc_dir: Path) -> str:
    try:
        return os.readlink(proc_dir / "cwd")
    except OSError:
        return ""


def scan(proc_root: Path | None = None) -> list[ServeProcess]:
    """Every running `box serve`, described from `/proc`. Newest first.

    Empty on a system without `/proc`, and empty when a process dies mid-scan: each entry is
    read defensively because these are other people's processes, changing underneath us.
    """
    root = proc_root or PROC_ROOT
    if not root.is_dir():
        return []
    uptime_now = float(_read(root / "uptime").split()[0]) if _read(root / "uptime") else 0.0
    found: list[ServeProcess] = []
    for entry in root.iterdir():
        if not entry.name.isdigit():
            continue
        command = _command_line(entry)
        if not command or not is_server(command):
            continue
        started, age = _started_and_age(entry, uptime_now)
        ports = _listening_ports(entry, root)
        requested = _requested_port(command)
        databases = _open_databases(entry)
        declared = _declared_database(entry)
        found.append(
            ServeProcess(
                pid=int(entry.name),
                ports=ports or ((requested,) if requested else ()),
                port_source=(
                    "listening" if ports else ("command line" if requested else "unknown")
                ),
                database=databases[0] if databases else declared,
                database_source=(
                    "held open" if databases else ("declared" if declared else "none")
                ),
                cwd=_cwd(entry),
                started_at=started,
                uptime_seconds=age,
                command=command.replace("\0", " ").strip(),
                rss_bytes=_rss_bytes(entry),
            )
        )
    return sorted(found, key=lambda item: item.uptime_seconds)
