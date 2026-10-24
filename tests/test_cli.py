"""The CLI is a thin shell, so these tests stay close to the terminal."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from boxganizer import __version__
from boxganizer.cli.main import app, main
from boxganizer.errors import BoxNotFound, EditError, MissingDependency
from boxganizer.models import Box
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


# -- box types through the shell -------------------------------------------


def test_types_prints_the_hierarchy(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLUMNS", "200")
    result = invoke(db_path, "types")
    assert result.exit_code == 0
    assert "(group)" in result.output
    for key in ("markdown", "parental-graph", "home-assistant", "windowed-viewport"):
        assert key in result.output


def test_types_json_carries_the_rules(db_path: Path) -> None:
    import json

    result = invoke(db_path, "types", "--json")
    assert result.exit_code == 0
    described = json.loads(result.output)
    by_key = {entry["key"]: entry for entry in described}
    assert by_key["pdf"]["extensions"] == [".pdf"]
    assert by_key["mqtt"]["required_metadata"] == ["topic"]
    assert by_key["image"]["allows_children"] is False


def test_types_selectable_leaves_out_the_grouping_levels(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COLUMNS", "200")
    result = invoke(db_path, "types", "--selectable")
    assert "(group)" not in result.output
    listed = {line.split()[1] for line in result.output.splitlines() if line.strip()}
    assert listed.isdisjoint({"content", "resource", "dynamic", "organizer", "viewport"})
    assert "markdown" in listed


def test_add_a_box_of_a_type_and_show_it(db_path: Path) -> None:
    result = invoke(db_path, "box", "add", "Notes", "-t", "markdown", "-r", "/tmp/notes.md")
    assert result.exit_code == 0
    shown = invoke(db_path, "box", "show", "Notes")
    assert "Markdown (markdown)" in shown.output


def test_the_type_is_in_the_json_view(db_path: Path) -> None:
    invoke(db_path, "box", "add", "Pic", "-t", "image", "-r", "/tmp/a.png")
    result = invoke(db_path, "box", "list", "--json")
    assert '"box_type": "image"' in result.output


def test_an_unknown_type_is_refused_by_the_store(db_path: Path) -> None:
    from boxganizer.errors import UnknownBoxType

    result = invoke(db_path, "box", "add", "Odd", "-t", "spreadsheet")
    assert isinstance(result.exception, UnknownBoxType)


def test_a_box_that_fails_its_type_is_refused(db_path: Path) -> None:
    from boxganizer.errors import InvalidBoxContent

    result = invoke(db_path, "box", "add", "Fake", "-t", "pdf", "-r", "/tmp/a.png")
    assert isinstance(result.exception, InvalidBoxContent)


def test_the_shell_refuses_to_put_a_box_inside_an_image(db_path: Path) -> None:
    from boxganizer.errors import ChildrenNotAllowed

    invoke(db_path, "box", "add", "Pic", "-t", "image", "-r", "/tmp/a.png")
    invoke(db_path, "box", "add", "Note")
    result = invoke(db_path, "link", "in", "Note", "Pic")
    assert isinstance(result.exception, ChildrenNotAllowed)


def test_a_box_from_a_newer_version_is_still_readable(db_path: Path) -> None:
    """Reading must not need every type to be known; writing must."""
    import sqlite3

    from boxganizer.errors import UnknownBoxType

    with BoxStore(db_path) as store:
        store.add(Box(name="Future thing"))
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("UPDATE boxes SET box_type = 'hologram' WHERE name = 'Future thing'")
        connection.commit()
    finally:
        connection.close()

    shown = invoke(db_path, "box", "show", "Future thing")
    assert shown.exit_code == 0
    assert "hologram (unknown type" in shown.output
    assert invoke(db_path, "box", "list").exit_code == 0

    with BoxStore(db_path) as store, pytest.raises(UnknownBoxType):
        store.update(store.get("Future thing"))


# -- `box init` seeds root and cabinet -------------------------------------


def test_init_creates_exactly_two_boxes(db_path: Path) -> None:
    result = runner.invoke(app, ["--db", str(db_path), "init"])
    assert result.exit_code == 0
    assert "created root" in result.output
    assert "created cabinet" in result.output
    with BoxStore(db_path) as store:
        assert store.count() == 2
        assert [box.box_type for box in store.list_boxes()] == ["cabinet", "root"]


def test_tree_shows_cabinet_under_root(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLUMNS", "200")
    invoke(db_path, "init")
    tree = invoke(db_path, "tree")
    assert "root" in tree.output
    assert "  🗄️ cabinet" in tree.output


def test_init_on_a_seeded_library_adds_nothing(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COLUMNS", "300")
    invoke(db_path, "init")
    again = invoke(db_path, "init")
    assert "already initialised" in again.output
    assert "nothing added" in again.output
    with BoxStore(db_path) as store:
        assert store.count() == 2


# -- --parent ---------------------------------------------------------------


def test_a_new_box_goes_into_root_by_default(db_path: Path) -> None:
    invoke(db_path, "init")
    invoke(db_path, "box", "add", "Kitchen")
    with BoxStore(db_path) as store:
        assert [box.name for box in store.parents("Kitchen")] == ["root"]


def test_parent_names_the_container(db_path: Path) -> None:
    invoke(db_path, "box", "add", "House")
    result = invoke(db_path, "box", "add", "Kitchen", "--parent", "House")
    assert "in House" in result.output
    with BoxStore(db_path) as store:
        assert [box.name for box in store.children("House")] == ["Kitchen"]
        assert [box.name for box in store.parents("Kitchen")] == ["House"]
        # nothing was seeded, so House itself sits at the top
        assert store.parents("House") == []


def test_no_parent_leaves_the_box_at_the_top(db_path: Path) -> None:
    invoke(db_path, "init")
    invoke(db_path, "box", "add", "Loose", "--no-parent")
    with BoxStore(db_path) as store:
        assert store.parents("Loose") == []


def test_an_unknown_parent_is_refused_before_anything_is_written(db_path: Path) -> None:
    result = invoke(db_path, "box", "add", "Kitchen", "--parent", "Nowhere")
    assert result.exit_code != 0
    with BoxStore(db_path) as store:
        assert store.count() == 0


def test_parent_and_no_parent_cannot_be_combined(db_path: Path) -> None:
    invoke(db_path, "box", "add", "House")
    result = invoke(db_path, "box", "add", "X", "--parent", "House", "--no-parent")
    assert result.exit_code != 0


def test_a_box_cannot_be_its_own_parent(db_path: Path) -> None:
    result = invoke(db_path, "box", "add", "Ouroboros", "--parent", "Ouroboros")
    assert result.exit_code != 0
    with BoxStore(db_path) as store:
        assert store.count() == 0


# -- box edit ---------------------------------------------------------------

EDIT_SCRIPT = """
import pathlib, sys
path = pathlib.Path(sys.argv[1])
text = path.read_text()
text = text.replace("description: first", "description: second")
text = text.replace("config: {}", "config:\\n  racks: 4\\n  label: A-B\\n")
path.write_text(text)
"""

BROKEN_SCRIPT = """
import pathlib, sys
path = pathlib.Path(sys.argv[1])
path.write_text(path.read_text() + "\\n  dangling: [\\n")
"""

FAILING_SCRIPT = """
import sys
sys.exit(3)
"""


def _use_editor(script: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    editor = tmp_path / "editor.py"
    editor.write_text(script.lstrip())
    monkeypatch.setenv("EDITOR", f"{sys.executable} {editor}")


def test_edit_rewrites_the_row_through_the_editor(
    db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    invoke(db_path, "box", "add", "Thing", "-d", "first")
    _use_editor(EDIT_SCRIPT, tmp_path, monkeypatch)

    result = invoke(db_path, "edit", "Thing")
    assert result.exit_code == 0
    assert "saved" in result.output

    with BoxStore(db_path) as store:
        box = store.get("Thing")
        assert box.description == "second"
        assert box.config == {"racks": 4, "label": "A-B"}


def test_box_box_edit_is_the_same_command(
    db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    invoke(db_path, "box", "add", "Thing", "-d", "first")
    _use_editor(EDIT_SCRIPT, tmp_path, monkeypatch)
    assert invoke(db_path, "box", "edit", "Thing").exit_code == 0
    with BoxStore(db_path) as store:
        assert store.get("Thing").description == "second"


def test_edit_can_be_told_which_editor_to_use(
    db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    editor = tmp_path / "nano-ish.py"
    editor.write_text(EDIT_SCRIPT.lstrip())
    invoke(db_path, "box", "add", "Thing", "-d", "first")
    result = invoke(db_path, "edit", "Thing", "--editor", f"{sys.executable} {editor}")
    assert result.exit_code == 0
    with BoxStore(db_path) as store:
        assert store.get("Thing").description == "second"


def test_editing_nothing_changes_nothing(
    db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    invoke(db_path, "box", "add", "Thing", "-d", "first")
    _use_editor("import sys\n", tmp_path, monkeypatch)  # the editor merely closes
    result = invoke(db_path, "edit", "Thing")
    assert "left unchanged" in result.output
    with BoxStore(db_path) as store:
        assert store.get("Thing").description == "first"


def test_a_broken_file_leaves_the_box_alone(
    db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    invoke(db_path, "box", "add", "Thing", "-d", "first")
    _use_editor(BROKEN_SCRIPT, tmp_path, monkeypatch)
    result = invoke(db_path, "edit", "Thing")
    assert isinstance(result.exception, EditError)
    with BoxStore(db_path) as store:
        assert store.get("Thing").description == "first"


def test_a_failing_editor_aborts(
    db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    invoke(db_path, "box", "add", "Thing", "-d", "first")
    _use_editor(FAILING_SCRIPT, tmp_path, monkeypatch)
    result = invoke(db_path, "edit", "Thing")
    assert isinstance(result.exception, EditError)
    with BoxStore(db_path) as store:
        assert store.get("Thing").description == "first"


def test_no_editor_configured_is_reported_clearly(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from boxganizer.errors import EditError as _EditError

    invoke(db_path, "box", "add", "Thing")
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.delenv("VISUAL", raising=False)
    result = invoke(db_path, "edit", "Thing")
    assert isinstance(result.exception, _EditError)
    assert "no editor" in str(result.exception)


def test_edit_does_not_touch_links(
    db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    invoke(db_path, "init")
    invoke(db_path, "box", "add", "Thing", "-d", "first")
    _use_editor(EDIT_SCRIPT, tmp_path, monkeypatch)
    invoke(db_path, "edit", "Thing")
    with BoxStore(db_path) as store:
        assert [box.name for box in store.parents("Thing")] == ["root"]
