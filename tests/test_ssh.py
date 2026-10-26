"""SSH: reading an address, refusing a password, and running one command through argv."""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from boxganizer import boxtypes, ssh
from boxganizer.boxtypes.base import BoxType
from boxganizer.cli.main import app as cli_app
from boxganizer.errors import InvalidBoxContent
from boxganizer.models import Box
from boxganizer.store import BoxStore
from boxganizer.web.app import create_app


def box_with(resource: str | None = None, **metadata: Any) -> Box:
    return Box(
        name="far",
        box_type="ssh",
        resource_uri=resource,
        metadata={"host": "builder.lan", **metadata},
    )


# -- reading the address ------------------------------------------------------


def test_the_address_gives_the_host_the_user_and_the_port() -> None:
    where = ssh.destination(box_with("ssh://ci@builder.lan:2222"))
    assert (where.host, where.user, where.port) == ("builder.lan", "ci", 2222)
    assert where.target == "ci@builder.lan"


def test_metadata_says_the_same_thing_as_an_address() -> None:
    where = ssh.destination(box_with(None, host="db.lan", user="postgres", port="5432"))
    assert (where.host, where.user, where.port) == ("db.lan", "postgres", 5432)


def test_a_bare_address_is_read_as_one_and_not_as_a_path() -> None:
    where = ssh.destination(box_with("builder.lan"))
    assert where.host == "builder.lan"
    assert where.port == 22


def test_an_address_that_is_not_ssh_is_refused() -> None:
    with pytest.raises(ssh.SshError, match="http"):
        ssh.destination(box_with("http://builder.lan"))


def test_a_port_that_is_not_a_port_is_refused() -> None:
    with pytest.raises(ssh.SshError, match="port"):
        ssh.destination(box_with(None, port="twenty"))


def test_no_host_anywhere_is_refused() -> None:
    box = Box(name="nowhere", box_type="ssh", metadata={})
    with pytest.raises(ssh.SshError, match="host"):
        ssh.destination(box)


# -- the rule about passwords -------------------------------------------------


def test_a_password_in_the_address_is_refused_loudly() -> None:
    with pytest.raises(ssh.SshError, match="keeps no password"):
        ssh.destination(box_with("ssh://u:s3cret@builder.lan"))


def test_a_percent_encoded_password_is_refused_too() -> None:
    with pytest.raises(ssh.SshError, match="keeps no password"):
        ssh.destination(box_with("ssh://u%40example:s3%63ret@builder.lan"))


def test_the_type_refuses_a_password_before_the_box_is_stored() -> None:
    kind = boxtypes.get("ssh")
    assert isinstance(kind, type) and issubclass(kind, BoxType)
    with pytest.raises(InvalidBoxContent, match="keeps no password"):
        kind.validate(box_with("ssh://u:pw@builder.lan"))
    # and a box with nothing secret about it passes
    kind.validate(box_with("ssh://ci@builder.lan:2222"))


def test_nothing_about_an_ssh_box_names_a_password_field() -> None:
    """A secret cannot be stored in a place nobody thought to redact."""
    where = ssh.destination(box_with("ssh://ci@builder.lan"))
    assert "password" not in str(where.as_dict()).lower()
    assert "password" not in " ".join(where.argv("uptime")).lower()


# -- building the command -----------------------------------------------------


def test_a_command_is_asked_non_interactively_and_as_one_argument() -> None:
    where = ssh.destination(box_with("ssh://ci@builder.lan:2222"))
    argv = where.argv("df -h /var")
    assert argv[0] == "ssh"
    assert "BatchMode=yes" in " ".join(argv)  # nothing may stop and ask a question
    assert argv[-2] == "ci@builder.lan"
    assert argv[-1] == "df -h /var"  # whole, not split into the local argv
    assert "-c" not in argv and "bash" not in argv  # no shell on this side


def test_the_options_a_box_adds_are_one_option_each() -> None:
    where = ssh.destination(
        box_with("ssh://ci@builder.lan", options=["StrictHostKeyChecking=accept-new"])
    )
    argv = where.argv("uptime")
    index = argv.index("StrictHostKeyChecking=accept-new")
    assert argv[index - 1] == "-o"


def test_an_option_line_cannot_smuggle_a_whole_switch() -> None:
    with pytest.raises(ssh.SshError, match="not an ssh option line"):
        ssh.destination(box_with("ssh://ci@builder.lan", options=["-oProxyCommand=evil"]))


def test_a_host_cannot_smuggle_a_command_or_a_path() -> None:
    for bad in ("builder.lan; rm -rf /", "builder.lan/../etc", "-oProxyCommand=evil"):
        with pytest.raises(ssh.SshError):
            ssh.destination(box_with(None, host=bad))


def test_the_copyable_line_lets_a_person_answer_a_question() -> None:
    """The batch options are for a command with nobody in front of it.

    A line pasted into a real terminal is the opposite case: it should be able to ask
    for a passphrase, and should not open with two options nobody typed.
    """
    where = ssh.destination(box_with("ssh://ci@builder.lan:2222"))
    line = where.command_line()
    assert line == "ssh -p 2222 ci@builder.lan"
    assert "BatchMode" not in line


def test_quoting_for_the_screen_keeps_one_argument_one_argument() -> None:
    assert ssh.shell_join(["ssh", "it's here"]) == "ssh 'it'\\''s here'"
    assert ssh.shell_join(["ssh", "plain-host"]) == "ssh plain-host"


def test_a_jump_host_and_a_key_file_reach_the_command_line() -> None:
    where = ssh.destination(
        box_with("ssh://ci@builder.lan", identity_file="~/.ssh/ci", jump_host="bastion.lan")
    )
    argv = where.argv("uptime")
    assert argv[argv.index("-i") + 1] == "~/.ssh/ci"
    assert argv[argv.index("-J") + 1] == "bastion.lan"


# -- running ------------------------------------------------------------------


class FakeCompleted:
    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0) -> None:
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


@pytest.fixture
def no_real_ssh(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Catch what would have been run, and answer with something scripted."""
    seen: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> FakeCompleted:
        seen["argv"] = list(argv)
        seen["kwargs"] = kwargs
        return seen.get("answer") or FakeCompleted("ok\n", "", 0)

    def timeout(argv: list[str], **kwargs: Any) -> FakeCompleted:
        raise subprocess.TimeoutExpired(argv, kwargs.get("timeout", 1))

    seen["fake_run"] = fake_run
    monkeypatch.setattr(ssh.subprocess, "run", fake_run)
    monkeypatch.setattr(ssh.shutil, "which", lambda name: "/usr/bin/ssh")
    return seen


def test_a_command_runs_and_reports_what_came_back(no_real_ssh: dict[str, Any]) -> None:
    no_real_ssh["answer"] = FakeCompleted(" 14:02:01 up 3 days\n", "", 0)
    result = ssh.run(box_with("ssh://ci@builder.lan"), "uptime")
    assert result.ok
    assert "up 3 days" in result.stdout
    assert no_real_ssh["argv"][-1] == "uptime"
    # no shell, and the output is captured rather than inherited by the page
    assert no_real_ssh["kwargs"]["capture_output"] is True
    assert no_real_ssh["kwargs"].get("shell") is not True


def test_a_failing_command_keeps_its_exit_code(no_real_ssh: dict[str, Any]) -> None:
    no_real_ssh["answer"] = FakeCompleted("", "Permission denied (publickey).\n", 255)
    result = ssh.run(box_with("ssh://ci@builder.lan"), "uptime")
    assert not result.ok
    assert result.exit_code == 255
    assert "publickey" in result.stderr


def test_a_machine_that_does_not_answer_is_cut_off(
    no_real_ssh: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def never_answers(argv: list[str], **kwargs: Any) -> FakeCompleted:
        raise subprocess.TimeoutExpired(argv, kwargs.get("timeout", 1))

    monkeypatch.setattr(ssh.subprocess, "run", never_answers)
    result = ssh.run(box_with("ssh://ci@builder.lan"), "slowme", timeout=1)
    assert result.timed_out
    assert not result.ok
    assert result.exit_code is None  # there was no exit status to report
    assert "no answer" in result.stderr


def test_output_past_the_limit_is_cut_and_says_so(no_real_ssh: dict[str, Any]) -> None:
    no_real_ssh["answer"] = FakeCompleted("y" * (ssh.MAX_OUTPUT_BYTES + 1000), "", 0)
    result = ssh.run(box_with("ssh://ci@builder.lan"), "yes")
    assert len(result.stdout.encode()) < ssh.MAX_OUTPUT_BYTES + 200
    assert "cut off" in result.stdout


def test_an_empty_command_is_refused_before_anything_is_run(no_real_ssh: dict[str, Any]) -> None:
    with pytest.raises(ssh.SshError, match="no command"):
        ssh.run(box_with("ssh://ci@builder.lan"), "   ")
    assert "argv" not in no_real_ssh


def test_a_machine_without_ssh_says_so(
    no_real_ssh: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ssh.shutil, "which", lambda name: None)
    assert ssh.available() is False
    with pytest.raises(ssh.SshError, match="no ssh command"):
        ssh.run(box_with("ssh://ci@builder.lan"), "uptime")


# -- the endpoints ------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> Iterator[BoxStore]:
    box_store = BoxStore(tmp_path / "ssh.db")
    box_store.add(Box(name="root", box_type="root"))
    box_store.add(
        Box(
            name="Build",
            box_type="ssh",
            resource_uri="ssh://ci@builder.lan:2222",
            metadata={"host": "builder.lan"},
        )
    )
    box_store.add(Box(name="Plain", box_type="folder"))
    yield box_store
    box_store.close()


@pytest.fixture
def client(store: BoxStore) -> Iterator[TestClient]:
    with TestClient(create_app(store)) as test_client:
        yield test_client


def test_the_address_endpoint_answers_with_the_destination(client: TestClient) -> None:
    answer = client.get("/api/boxes/Build/ssh")
    assert answer.status_code == 200
    body = answer.json()
    assert body["destination"]["host"] == "builder.lan"
    assert body["destination"]["port"] == 2222
    assert body["destination"]["command_line"] == "ssh -p 2222 ci@builder.lan"


def test_a_box_that_does_not_reach_anywhere_is_refused(client: TestClient) -> None:
    assert client.get("/api/boxes/Plain/ssh").status_code == 415
    assert client.post("/api/boxes/Plain/ssh/run", json={"command": "uptime"}).status_code == 415


def test_running_a_command_from_the_page_answers_with_the_result(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ssh.shutil, "which", lambda name: "/usr/bin/ssh")
    monkeypatch.setattr(
        ssh.subprocess, "run", lambda argv, **kwargs: FakeCompleted("up 3 days\n", "", 0)
    )
    answer = client.post("/api/boxes/Build/ssh/run", json={"command": "uptime"})
    assert answer.status_code == 200
    assert answer.json()["ok"] is True
    assert "up 3 days" in answer.json()["stdout"]


def test_a_box_broken_at_creation_cannot_be_made_through_the_api(client: TestClient) -> None:
    answer = client.post(
        "/api/boxes",
        json={
            "name": "Secret",
            "box_type": "ssh",
            "resource_uri": "ssh://u:pw@builder.lan",
            "metadata": {"host": "builder.lan"},
        },
    )
    assert answer.status_code == 422
    assert "password" in answer.text


# -- the command line ---------------------------------------------------------


def invoke(db: Path, *args: str):  # type: ignore[no-untyped-def]
    return CliRunner().invoke(cli_app, ["--db", str(db), *args])


def test_the_cli_describes_the_box_and_offers_the_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BOXGANIZER_DB", str(tmp_path / "cli.db"))
    monkeypatch.setenv("BOXGANIZER_CONFIG", str(tmp_path / "config.json"))
    db = tmp_path / "cli.db"
    invoke(db, "init")
    invoke(
        db,
        "add",
        "Build",
        "-t",
        "ssh",
        "-r",
        "ssh://ci@builder.lan:2222",
        "-m",
        "host=builder.lan",
        "--no-parent",
    )
    answer = invoke(db, "ssh", "Build")
    assert answer.exit_code == 0, answer.output
    assert "ssh://ci@builder.lan:2222" in answer.output
    assert "ssh -p 2222 ci@builder.lan" in answer.output


def test_the_cli_runs_a_command_and_passes_its_exit_code_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ssh.shutil, "which", lambda name: "/usr/bin/ssh")
    monkeypatch.setattr(
        ssh.subprocess, "run", lambda argv, **kwargs: FakeCompleted("hello there\n", "", 0)
    )
    db = tmp_path / "cli.db"
    monkeypatch.setenv("BOXGANIZER_DB", str(db))
    monkeypatch.setenv("BOXGANIZER_CONFIG", str(tmp_path / "config.json"))
    invoke(db, "init")
    invoke(db, "add", "Build", "-t", "ssh", "-m", "host=builder.lan", "--no-parent")

    good = invoke(db, "ssh", "Build", "uptime")
    assert good.exit_code == 0, good.output
    assert "hello there" in good.output

    monkeypatch.setattr(
        ssh.subprocess, "run", lambda argv, **kwargs: FakeCompleted("", "nope\n", 3)
    )
    bad = invoke(db, "ssh", "Build", "false")
    assert bad.exit_code == 3
    assert "nope" in bad.output


def test_the_cli_refuses_a_box_that_is_not_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "cli.db"
    monkeypatch.setenv("BOXGANIZER_DB", str(db))
    monkeypatch.setenv("BOXGANIZER_CONFIG", str(tmp_path / "config.json"))
    invoke(db, "init")
    invoke(db, "add", "Cupboard", "-t", "folder", "--no-parent")
    answer = invoke(db, "ssh", "Cupboard", "uptime")
    assert answer.exit_code == 1
    assert "does not reach another machine" in answer.output
