"""The CLI is a thin shell, so these tests stay close to the terminal."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from boxganizer import __version__
from boxganizer.cli.main import app
from boxganizer.errors import BoxNotFound
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
