"""The server inventory, tested against a `/proc` that was built rather than read.

These tests hand `scan()` a directory tree instead of the live system on purpose: the numbers
on a real machine change between the assertion and the read, the machine may not be Linux at
all, and the case that matters most -- one process holding the real library while its neighbours
hold scratch copies -- is easier to assert exactly than to wait for.
"""

from __future__ import annotations

import os
from pathlib import Path

from boxganizer.servers import is_server, scan

#: `/proc/net/tcp` prints the local port in hex, and one LISTEN row per listening socket.
LIVE_DB = "/home/pbi/.local/share/boxganizer/boxes.db"
SCRATCH_DB = "/tmp/xdga/boxganizer/boxes.db"


def _proc(root: Path) -> Path:
    """A kernel-shaped tree: two servers and one innocent bystander."""
    net = root / "net"
    net.mkdir(parents=True)
    # 8123 = 0x1FBB, 9000 = 0x2328; ports are hex in these tables
    (net / "tcp").write_text(
        "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt"
        "   uid  timeout inode\n"
        "   0: 0100007F:1FBB 00000000:0000 0A 00000000:00000000 00:00000000 00000000"
        "     0        0 555 2 0000000000000000 100 0 0 10 0\n"
        "   1: 00000000:2328 00000000:0000 0A 00000000:00000000 00:00000000 00000000"
        "     0        0 556 2 0000000000000000 100 0 0 10 0\n"
        # an ESTABLISHED connection (01), which must not be read as a listener
        "   2: 0100007F:1FBB 0100007F:D431 01 00000000:00000000 00:00000000 00000000"
        "     0        0 557 2 0000000000000000 100 0 0 10 0\n",
        encoding="utf-8",
    )
    (net / "tcp6").write_text(
        "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt"
        "   uid  timeout inode\n",
        encoding="utf-8",
    )
    (root / "uptime").write_text("1000.00 900.00\n", encoding="utf-8")

    def process(
        pid: str,
        command: list[str],
        *,
        fds: dict[str, str] | None = None,
        environ: dict[str, str] | None = None,
        started_ticks: int = 10_000,  # 100 s after boot -> 900 s of age at 1000.00 uptime
        rss_kb: int = 2048,
    ) -> None:
        where = root / pid
        (where / "fd").mkdir(parents=True)
        (where / "cmdline").write_text("\0".join(command) + "\0", encoding="utf-8")
        # fields 1-2 then state, then 19 more before starttime lands at index 19
        tail = " ".join(["S"] + ["0"] * 18 + [str(started_ticks)] + ["0"] * 30)
        (where / "stat").write_text(f"{pid} (box) {tail}\n", encoding="utf-8")
        (where / "status").write_text(f"Name: box\nVmRSS:\t{rss_kb} kB\n", encoding="utf-8")
        for name, target in (fds or {}).items():
            os.symlink(target, where / "fd" / name)
        workdir = root / f"cwd-{pid}"
        workdir.mkdir()
        os.symlink(str(workdir), where / "cwd")
        if environ is not None:
            (where / "environ").write_text(
                "\0".join(f"{key}={value}" for key, value in environ.items()) + "\0",
                encoding="utf-8",
            )

    process(
        "4242",
        ["box", "serve", "--port", "8123"],
        fds={"3": "socket:[555]", "7": LIVE_DB},
        environ={"HOME": "/home/pbi", "XDG_DATA_HOME": "/tmp/ignored"},
        rss_kb=90_000,
    )
    process(
        "4343",
        ["/venv/bin/python3", ".venv/bin/box", "serve"],
        fds={"4": "socket:[556]"},
        environ={"XDG_DATA_HOME": "/tmp/xdga"},
        started_ticks=20_000,
    )
    process("4444", ["vim", "servertuning.md"], fds={"1": "/dev/null"})
    process(
        "4545",
        ["/usr/bin/python3.14", "-m", "boxganizer", "serve"],
        fds={"5": SCRATCH_DB},
    )
    # A shell wrapper around the command is not the server: the process that answers is its
    # child, and that child appears in its own right. Listing the shell too would double-count
    # every server started from a script, which is most of the 32 on this machine.
    process("4646", ["bash", "-c", "box serve --port 8123"], fds={"5": LIVE_DB})
    return root


def test_the_inventory_names_each_server_its_port_and_its_library(tmp_path: Path) -> None:
    root = _proc(tmp_path)
    rows = {row.pid: row for row in scan(root)}
    assert sorted(rows) == [4242, 4343, 4545], (
        "the editor and the shell wrapper must not count as servers"
    )

    live = rows[4242]
    # 0x1FBB, from the socket table -- not from `--port`, which only says what was asked for
    assert live.ports == (8123,)
    assert live.port_source == "listening"
    assert live.database == LIVE_DB
    assert live.database_source == "held open"
    assert live.uptime_seconds == 900.0
    assert live.rss_bytes == 90_000 * 1024

    scratch = rows[4343]
    assert scratch.ports == (9000,)
    assert scratch.database_source == "declared"
    assert scratch.database == "$XDG_DATA_HOME=/tmp/xdga/boxganizer/boxes.db"
    assert scratch.uptime_seconds == 800.0

    assert rows[4545].database == SCRATCH_DB
    assert rows[4545].database_source == "held open"


def test_a_held_descriptor_beats_a_declared_path(tmp_path: Path) -> None:
    """The environment says what was asked for; the descriptor says what is true.

    Process 4242 in the fixture declares `XDG_DATA_HOME=/tmp/ignored` and holds the real
    library. Reporting the declaration there would be the exact mistake this module exists to
    stop, so it gets its own test rather than hiding inside the one above.
    """
    rows = {row.pid: row for row in scan(_proc(tmp_path))}
    assert rows[4242].database == LIVE_DB
    assert rows[4242].database_source == "held open"


def test_one_server_holds_the_live_library_and_the_neighbours_do_not(tmp_path: Path) -> None:
    """The correction, reproducible: `ps` said 29 servers shared the live library.

    They did not. One held it open; the rest were started with `XDG_DATA_HOME=/tmp/xdg…` and
    hold scratch paths, which is a forgotten-processes problem rather than 29 writers on one
    file. Asserted as a count, because that is what was wrong: a number, not a description.
    """
    rows = scan(_proc(tmp_path))
    holding_live = [row for row in rows if row.database == LIVE_DB]
    assert len(holding_live) == 1
    assert len(rows) == 3, "the inventory is about all of them, not only the dangerous one"


def test_a_process_that_vanished_mid_scan_is_skipped_not_fatal(tmp_path: Path) -> None:
    """These are other people's processes: they can disappear between two reads."""
    root = _proc(tmp_path)
    import shutil

    shutil.rmtree(root / "4343" / "fd")
    rows = {row.pid: row for row in scan(root)}
    assert 4343 in rows, "a server whose descriptors went away is still a server"
    assert rows[4343].ports == ()


def test_the_command_line_is_read_as_arguments_not_as_a_substring() -> None:
    """A person, a file, or a directory called `serve` is not a running server."""
    assert is_server("box\0serve\0--port\08000\0")
    assert is_server("/venv/bin/python3\0.venv/bin/box\0serve\0")
    assert not is_server("vim\0serve.py\0")
    assert not is_server("bash\0-c\0box serve --port 8123\0")  # the child is listed instead
    assert not is_server("/home/pbi/serve/box\0status\0")
    assert is_server("/usr/bin/python3.14\0-m\0boxganizer\0serve\0")
    assert not is_server("ps\0-ef\0")


def test_a_system_without_proc_reports_nothing_instead_of_guessing(tmp_path: Path) -> None:
    assert scan(tmp_path / "nowhere") == []
