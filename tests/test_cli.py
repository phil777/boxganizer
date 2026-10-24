"""The CLI is a thin shell, so these tests stay close to the terminal."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from boxganizer import __version__
from boxganizer.cli.main import app, main
from boxganizer.errors import BoxNotFound, MissingDependency
from boxganizer.store import BoxStore

runner = CliRunner()


def invoke(db: Path, *args: str):  # type: ignore[no-untyped-def]
    return runner.invoke(app, ["--db", str(db), *args])


def test_version(db_path: Path) -> None:
    result = invoke(db_path, "version")
    assert result.exit_code == 0
    assert __version__ in result.output


def test_init_creates_the_database(db_path: Path) -> None:
    assert not db_path.exists()
    result = runner.invoke(app, ["--db", str(db_path), "init"])
    assert result.exit_code == 0
    assert db_path.exists()


def test_status_reports_the_path(db_path: Path) -> None:
    result = invoke(db_path, "status")
    assert result.exit_code == 0
    assert str(db_path) in result.output


def test_add_then_list(db_path: Path) -> None:
    result = invoke(db_path, "box", "add", "Kitchen", "-d", "Room for cooking", "-m", "floor=0")
    assert result.exit_code == 0
    assert "added" in result.output
    listing = invoke(db_path, "box", "list")
    assert "Kitchen" in listing.output
    assert "Room for cooking" in listing.output


def test_list_on_an_empty_store_is_polite(db_path: Path) -> None:
    result = invoke(db_path, "box", "list")
    assert result.exit_code == 0
    assert "no boxes" in result.output


def test_list_as_json(db_path: Path) -> None:
    invoke(db_path, "box", "add", "Garage")
    result = invoke(db_path, "box", "list", "--json")
    assert '"Garage"' in result.output


def test_show_prints_relations(db_path: Path) -> None:
    invoke(db_path, "box", "add", "House")
    invoke(db_path, "box", "add", "Attic")
    invoke(db_path, "link", "in", "Attic", "House")
    shown = invoke(db_path, "box", "show", "Attic")
    assert "House" in shown.output
    assert "parents" in shown.output


def test_search_from_the_shell(db_path: Path) -> None:
    invoke(db_path, "box", "add", "Books", "-d", "Novels and manuals")
    result = invoke(db_path, "box", "search", "manual")
    assert "Books" in result.output


def test_remove(db_path: Path) -> None:
    invoke(db_path, "box", "add", "Temp")
    result = invoke(db_path, "box", "rm", "Temp", "-y")
    assert "deleted" in result.output
    with BoxStore(db_path) as store:
        assert store.count() == 0


def test_bad_meta_is_rejected(db_path: Path) -> None:
    result = invoke(db_path, "box", "add", "Box", "-m", "no-equals-sign")
    assert result.exit_code != 0


def test_unknown_box_raises_the_core_error(db_path: Path) -> None:
    result = invoke(db_path, "box", "show", "Ghost")
    assert isinstance(result.exception, BoxNotFound)


@pytest.mark.parametrize("command", [["box"], ["link"], []])
def test_groups_ask_for_help(db_path: Path, command: list[str]) -> None:
    result = invoke(db_path, *command)
    assert result.exit_code in (0, 1, 2)


def test_help_lists_the_other_interfaces(db_path: Path) -> None:
    result = invoke(db_path, "--help")
    assert "serve" in result.output
    assert "tui" in result.output


def test_serve_hands_the_options_to_the_web_runner(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import boxganizer.web.server as web_server

    calls: dict[str, object] = {}
    monkeypatch.setattr(web_server, "run", lambda **kwargs: calls.update(kwargs))
    result = invoke(db_path, "serve", "--port", "9123")
    assert result.exit_code == 0
    assert callable(calls["on_start"]), "the runner must be told how to announce itself"
    del calls["on_start"]
    assert calls == {"host": "127.0.0.1", "port": 9123, "db": db_path, "reload": False}


def test_serve_accepts_host_and_reload(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import boxganizer.web.server as web_server

    calls: dict[str, object] = {}
    monkeypatch.setattr(web_server, "run", lambda **kwargs: calls.update(kwargs))
    result = invoke(db_path, "serve", "--host", "0.0.0.0", "--reload")
    assert result.exit_code == 0
    assert calls["host"] == "0.0.0.0"
    assert calls["reload"] is True


def test_serve_without_the_web_extra_says_what_to_install(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import boxganizer.web.server as web_server

    def missing(**kwargs: object) -> None:
        raise ModuleNotFoundError("No module named 'uvicorn'")

    monkeypatch.setattr(web_server, "run", missing)
    result = invoke(db_path, "serve")
    assert isinstance(result.exception, MissingDependency)
    assert result.exception.extra == "web"


def test_tui_opens_the_terminal_app(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import boxganizer.tui.app as tui_module

    opened: dict[str, object] = {}

    class FakeApp:
        def __init__(self, db: Path | None = None) -> None:
            opened["db"] = db

        def run(self) -> None:
            opened["ran"] = True

    monkeypatch.setattr(tui_module, "BoxganizerApp", FakeApp)
    result = invoke(db_path, "tui")
    assert result.exit_code == 0
    assert opened == {"db": db_path, "ran": True}


def test_markup_characters_in_box_data_stay_visible(db_path: Path) -> None:
    """Rich must never eat brackets that a human typed into a box."""
    invoke(db_path, "box", "add", "Plan [bold]A[/]", "-d", "cost [red]100[/] euros")
    invoke(db_path, "box", "add", "Budget [2026]")
    shown = invoke(db_path, "box", "show", "Plan [bold]A[/]")
    assert "Plan [bold]A[/]" in shown.output
    assert "cost [red]100[/] euros" in shown.output
    listing = invoke(db_path, "box", "list")
    assert "Budget [2026]" in listing.output
    tree = invoke(db_path, "tree")
    assert "Budget [2026]" in tree.output


def test_missing_dependency_names_the_extra_exactly() -> None:
    assert str(MissingDependency("web")) == (
        "this command needs the 'web' extra: pip install 'boxganizer[web]'"
    )


def test_the_install_hint_reaches_stderr_with_its_brackets(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import boxganizer.web.server as web_server

    def missing(**kwargs: object) -> None:
        raise ModuleNotFoundError("No module named 'uvicorn'")

    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setattr(web_server, "run", missing)
    monkeypatch.setattr(sys, "argv", ["box", "--db", str(db_path), "serve"])
    with pytest.raises(SystemExit):
        main()
    captured = capsys.readouterr()
    assert "boxganizer[web]" in captured.err
    assert "serving" not in captured.out  # nothing promised before the server exists


def test_main_turns_a_core_error_into_exit_code_1(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["box", "--db", str(db_path), "box", "show", "Ghost"])
    with pytest.raises(SystemExit) as exit_info:
        main()
    assert exit_info.value.code == 1
    assert "no box named 'Ghost'" in capsys.readouterr().err
