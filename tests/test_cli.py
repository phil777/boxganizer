"""The CLI is a thin shell, so these tests stay close to the terminal."""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
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
    result = invoke(db_path, "add", "Kitchen", "-d", "Room for cooking", "-m", "floor=0")
    assert result.exit_code == 0
    assert "added" in result.output
    listing = invoke(db_path, "list")
    assert "Kitchen" in listing.output
    assert "Room for cooking" in listing.output


def test_list_on_an_empty_store_is_polite(db_path: Path) -> None:
    result = invoke(db_path, "list")
    assert result.exit_code == 0
    assert "no boxes" in result.output


def test_list_as_json(db_path: Path) -> None:
    invoke(db_path, "add", "Garage")
    result = invoke(db_path, "list", "--json")
    assert '"Garage"' in result.output


def test_show_prints_relations(db_path: Path) -> None:
    invoke(db_path, "add", "House")
    invoke(db_path, "add", "Attic")
    invoke(db_path, "link", "Attic", "House")
    shown = invoke(db_path, "show", "Attic")
    assert "House" in shown.output
    assert "parents" in shown.output


def test_search_from_the_shell(db_path: Path) -> None:
    invoke(db_path, "add", "Books", "-d", "Novels and manuals")
    result = invoke(db_path, "search", "manual")
    assert "Books" in result.output


def test_remove(db_path: Path) -> None:
    invoke(db_path, "add", "Temp")
    result = invoke(db_path, "rm", "Temp", "-y")
    assert "deleted" in result.output
    with BoxStore(db_path) as store:
        assert store.count() == 0


def test_rm_says_what_survives(db_path: Path) -> None:
    """The shell answer names what the delete did not touch: the boxes inside, and the words file.

    `rm` deleting the contents of a folder as well would be unrecoverable from a confirmation nobody
    reads carefully, and a note whose file silently vanishes is worse. Both are stated instead of
    assumed — and the file is checked on disk, because a sentence is only worth reading if it is
    true.
    """
    from boxganizer import authored

    invoke(db_path, "add", "Drawer", "--type", "folder")
    invoke(db_path, "add", "Sole")
    invoke(db_path, "link", "Sole", "Drawer")
    invoke(db_path, "add", "Scrap", "--type", "note")
    with BoxStore(db_path) as store:
        words = authored.path_for(db_path, store.get("Scrap").uuid)
    words.parent.mkdir(parents=True, exist_ok=True)
    words.write_text("keep this", encoding="utf-8")

    result = invoke(db_path, "rm", "Drawer", "-y")
    assert result.exit_code == 0, result.output
    assert "1 filed inside stay in the library" in result.output
    with BoxStore(db_path) as store:
        assert store.get("Sole").name == "Sole", "the child outlived the box that held it"

    gone = invoke(db_path, "rm", "Scrap", "-y")
    assert "its own words are still in" in gone.output, gone.output
    assert words.exists() and words.read_text(encoding="utf-8") == "keep this"


def test_the_root_refuses_to_be_deleted_from_the_shell(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No traceback, one sentence, and the root still there afterwards.

    Run through `main`, because that is the layer that turns a core error into a message; the bare
    app would let the exception escape into a traceback nobody asked for.
    """
    invoke(db_path, "init")
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setattr(sys, "argv", ["box", "--db", str(db_path), "rm", "root", "-y"])
    with pytest.raises(SystemExit) as exit_info:
        main()
    assert exit_info.value.code == 1
    # one line, then unwrapped: the terminal wraps a sentence, not the meaning
    said = " ".join(capsys.readouterr().err.split())
    assert "nowhere to file anything" in said, said
    assert "Traceback" not in said
    with BoxStore(db_path) as store:
        assert store.get("root").box_type == "root"


def test_the_shell_files_any_path_without_asking_about_confines(
    db_path: Path, tmp_path: Path
) -> None:
    """`box add -r` files any path: the confine belongs to a started server, not to the store.

    The CLI is where the reader already *is* this user, with a terminal. A flag on a web server has
    no business reaching into it, and a `--confined` server refusing a write must not read as though
    the library itself had lost the path.
    """
    far = tmp_path / "mount"
    far.mkdir()
    target = far / "thing.txt"
    target.write_text("anything at all\n", encoding="utf-8")
    result = invoke(db_path, "add", "Far away", "--resource", str(target))
    assert result.exit_code == 0, result.output
    with BoxStore(db_path) as store:
        assert store.get("Far away").resource_uri == str(target)


def test_bad_meta_is_rejected(db_path: Path) -> None:
    result = invoke(db_path, "add", "Box", "-m", "no-equals-sign")
    assert result.exit_code != 0


def test_unknown_box_raises_the_core_error(db_path: Path) -> None:
    result = invoke(db_path, "show", "Ghost")
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
    assert calls == {
        "host": "127.0.0.1",
        "port": 9123,
        "db": db_path,
        "reload": False,
        # off unless asked for: a server that confines paths is a server you started on purpose
        "confined": False,
    }


def test_serve_confined_reaches_the_web_runner(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--confined` is one flag with one destination; a fourth place to set it would drift."""
    import boxganizer.web.server as web_server

    calls: dict[str, object] = {}
    monkeypatch.setattr(web_server, "run", lambda **kwargs: calls.update(kwargs))
    result = invoke(db_path, "serve", "--confined")
    assert result.exit_code == 0
    assert calls["confined"] is True


def test_serve_accepts_host_and_reload(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import boxganizer.web.server as web_server

    calls: dict[str, object] = {}
    monkeypatch.setattr(web_server, "run", lambda **kwargs: calls.update(kwargs))
    result = invoke(db_path, "serve", "--host", "0.0.0.0", "--reload")
    assert result.exit_code == 0
    assert calls["host"] == "0.0.0.0"
    assert calls["reload"] is True


def test_serve_without_the_web_library_says_the_install_is_broken(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import boxganizer.web.server as web_server

    def missing(**kwargs: object) -> None:
        raise ModuleNotFoundError("No module named 'uvicorn'")

    monkeypatch.setattr(web_server, "run", missing)
    result = invoke(db_path, "serve")
    assert isinstance(result.exception, MissingDependency)
    assert result.exception.interface == "web"


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
    invoke(db_path, "init")  # the cabinet is what the tree starts from below
    invoke(db_path, "add", "Plan [bold]A[/]", "-d", "cost [red]100[/] euros")
    invoke(db_path, "add", "Budget [2026]")
    shown = invoke(db_path, "show", "Plan [bold]A[/]")
    assert "Plan [bold]A[/]" in shown.output
    assert "cost [red]100[/] euros" in shown.output
    listing = invoke(db_path, "list")
    assert "Budget [2026]" in listing.output
    tree = invoke(db_path, "tree")
    assert "Budget [2026]" in tree.output


def test_missing_dependency_says_the_install_is_broken_rather_than_incomplete() -> None:
    """The old sentence told people to install an extra they had never been offered."""
    message = str(MissingDependency("web"))
    assert "boxganizer[" not in message
    assert "Repair the install" in message
    assert "pipx install --force boxganizer" in message
    assert "no 'web' interface" in message


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
    assert "Repair the install" in captured.err
    assert "boxganizer[" not in captured.err
    assert "serving" not in captured.out  # nothing promised before the server exists


def test_main_turns_a_core_error_into_exit_code_1(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["box", "--db", str(db_path), "show", "Ghost"])
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
    # a topic comes from the address, so nothing is demanded in metadata; the hint is what
    # the dialog shows so a bare host is not typed into a field whose path means something
    assert by_key["mqtt"]["required_metadata"] == []
    assert by_key["mqtt"]["resource_hint"].startswith("mqtt://")
    assert by_key["mqtt"]["resource_is_local"] is False
    assert by_key["text"]["resource_is_local"] is True
    assert by_key["home-assistant"]["required_metadata"] == ["entity_id"]
    assert by_key["image"]["allows_children"] is False
    # Which live source a box reads is asked of its type, never guessed from a string.
    assert by_key["mqtt"]["live_source"] == "mqtt"
    assert by_key["sensor"]["live_source"] is None
    assert by_key["image"]["live_source"] is None


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
    result = invoke(db_path, "add", "Notes", "-t", "markdown", "-r", "/tmp/notes.md")
    assert result.exit_code == 0
    shown = invoke(db_path, "show", "Notes")
    assert "Markdown (markdown)" in shown.output


def test_the_type_is_in_the_json_view(db_path: Path) -> None:
    invoke(db_path, "add", "Pic", "-t", "image", "-r", "/tmp/a.png")
    result = invoke(db_path, "list", "--json")
    assert '"box_type": "image"' in result.output


def test_an_unknown_type_is_refused_by_the_store(db_path: Path) -> None:
    from boxganizer.errors import UnknownBoxType

    result = invoke(db_path, "add", "Odd", "-t", "spreadsheet")
    assert isinstance(result.exception, UnknownBoxType)


def test_a_box_that_fails_its_type_is_refused(db_path: Path) -> None:
    from boxganizer.errors import InvalidBoxContent

    result = invoke(db_path, "add", "Fake", "-t", "pdf", "-r", "/tmp/a.png")
    assert isinstance(result.exception, InvalidBoxContent)


def test_the_shell_refuses_to_put_a_box_inside_an_image(db_path: Path) -> None:
    from boxganizer.errors import ChildrenNotAllowed

    invoke(db_path, "add", "Pic", "-t", "image", "-r", "/tmp/a.png")
    invoke(db_path, "add", "Note")
    result = invoke(db_path, "link", "Note", "Pic")
    assert isinstance(result.exception, ChildrenNotAllowed)


def test_the_shell_refuses_to_file_a_box_into_a_dynamic_folder(db_path: Path) -> None:
    """The refusal is the store's, so every door says the same thing — the shell included.

    Not `ChildrenNotAllowed`: the folder could hold the box quite happily. It is the choosing that
    is refused, and the sentence names what to do instead, because "no" from a folder full of
    boxes is a strange answer to receive without one.
    """
    from boxganizer.errors import HandFilingRefused

    invoke(db_path, "add", "Kitchen things", "-t", "dynamic-folder")
    invoke(db_path, "add", "Hammer", "-t", "note")
    result = invoke(db_path, "link", "Hammer", "Kitchen things")
    assert isinstance(result.exception, HandFilingRefused)
    assert "ordinary folder" in str(result.exception)

    # an ordinary folder is untouched by any of this
    invoke(db_path, "add", "Papers", "-t", "folder")
    assert invoke(db_path, "link", "Hammer", "Papers").exit_code == 0


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

    shown = invoke(db_path, "show", "Future thing")
    assert shown.exit_code == 0
    assert "hologram (unknown type" in shown.output
    assert invoke(db_path, "list").exit_code == 0

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


def test_a_new_box_has_no_parent_by_default(db_path: Path) -> None:
    """The root holds one box; filing every new box there would keep evicting it.

    The cabinet is not counted here: holding every box is what a cabinet is, and it
    says nothing about where the box was filed by hand.
    """
    invoke(db_path, "init")
    invoke(db_path, "add", "Kitchen")
    with BoxStore(db_path) as store:
        filed = [box.name for box in store.parents("Kitchen") if box.name != "cabinet"]
        assert filed == []
        assert [box.name for box in store.children("root")] == ["cabinet"]


def test_the_root_slot_can_be_taken_on_purpose(db_path: Path) -> None:
    invoke(db_path, "init")
    invoke(db_path, "add", "Workshop", "--parent", "root")
    with BoxStore(db_path) as store:
        assert [box.name for box in store.children("root")] == ["Workshop"]
        assert store.exists("cabinet")


def test_parent_names_the_container(db_path: Path) -> None:
    invoke(db_path, "add", "House")
    result = invoke(db_path, "add", "Kitchen", "--parent", "House")
    assert "in House" in result.output
    with BoxStore(db_path) as store:
        assert [box.name for box in store.children("House")] == ["Kitchen"]
        assert [box.name for box in store.parents("Kitchen")] == ["House"]
        # nothing was seeded, so House itself sits at the top
        assert store.parents("House") == []


def test_no_parent_leaves_the_box_at_the_top(db_path: Path) -> None:
    invoke(db_path, "init")
    invoke(db_path, "add", "Loose", "--no-parent")
    with BoxStore(db_path) as store:
        filed = [box.name for box in store.parents("Loose") if box.name != "cabinet"]
        assert filed == []


def test_an_unknown_parent_is_refused_before_anything_is_written(db_path: Path) -> None:
    result = invoke(db_path, "add", "Kitchen", "--parent", "Nowhere")
    assert result.exit_code != 0
    with BoxStore(db_path) as store:
        assert store.count() == 0


def test_parent_and_no_parent_cannot_be_combined(db_path: Path) -> None:
    invoke(db_path, "add", "House")
    result = invoke(db_path, "add", "X", "--parent", "House", "--no-parent")
    assert result.exit_code != 0


def test_a_box_cannot_be_its_own_parent(db_path: Path) -> None:
    result = invoke(db_path, "add", "Ouroboros", "--parent", "Ouroboros")
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
    invoke(db_path, "add", "Thing", "-d", "first")
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
    invoke(db_path, "add", "Thing", "-d", "first")
    _use_editor(EDIT_SCRIPT, tmp_path, monkeypatch)
    assert invoke(db_path, "edit", "Thing").exit_code == 0
    with BoxStore(db_path) as store:
        assert store.get("Thing").description == "second"


def test_edit_can_be_told_which_editor_to_use(
    db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    editor = tmp_path / "nano-ish.py"
    editor.write_text(EDIT_SCRIPT.lstrip())
    invoke(db_path, "add", "Thing", "-d", "first")
    result = invoke(db_path, "edit", "Thing", "--editor", f"{sys.executable} {editor}")
    assert result.exit_code == 0
    with BoxStore(db_path) as store:
        assert store.get("Thing").description == "second"


def test_editing_nothing_changes_nothing(
    db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    invoke(db_path, "add", "Thing", "-d", "first")
    _use_editor("import sys\n", tmp_path, monkeypatch)  # the editor merely closes
    result = invoke(db_path, "edit", "Thing")
    assert "left unchanged" in result.output
    with BoxStore(db_path) as store:
        assert store.get("Thing").description == "first"


def test_a_broken_file_leaves_the_box_alone(
    db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    invoke(db_path, "add", "Thing", "-d", "first")
    _use_editor(BROKEN_SCRIPT, tmp_path, monkeypatch)
    result = invoke(db_path, "edit", "Thing")
    assert isinstance(result.exception, EditError)
    with BoxStore(db_path) as store:
        assert store.get("Thing").description == "first"


def test_a_failing_editor_aborts(
    db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    invoke(db_path, "add", "Thing", "-d", "first")
    _use_editor(FAILING_SCRIPT, tmp_path, monkeypatch)
    result = invoke(db_path, "edit", "Thing")
    assert isinstance(result.exception, EditError)
    with BoxStore(db_path) as store:
        assert store.get("Thing").description == "first"


def test_no_editor_configured_is_reported_clearly(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from boxganizer.errors import EditError as _EditError

    invoke(db_path, "add", "Thing")
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.delenv("VISUAL", raising=False)
    result = invoke(db_path, "edit", "Thing")
    assert isinstance(result.exception, _EditError)
    assert "no editor" in str(result.exception)


def test_edit_does_not_touch_links(
    db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    invoke(db_path, "add", "House")
    invoke(db_path, "add", "Thing", "-d", "first", "--parent", "House")
    _use_editor(EDIT_SCRIPT, tmp_path, monkeypatch)
    invoke(db_path, "edit", "Thing")
    with BoxStore(db_path) as store:
        assert [box.name for box in store.parents("Thing")] == ["House"]
        assert store.get("Thing").description == "second"


# -- cycles in the graph ----------------------------------------------------


def test_tree_survives_a_cycle(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLUMNS", "200")
    invoke(db_path, "add", "Mirror")
    invoke(db_path, "add", "Reflection")
    invoke(db_path, "link", "Reflection", "Mirror")
    result = invoke(db_path, "link", "Mirror", "Reflection")
    assert result.exit_code == 0
    shown = invoke(db_path, "tree", "Mirror")
    assert "cycle" in shown.output
    assert shown.exit_code == 0


def test_tree_of_a_box_inside_itself(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLUMNS", "200")
    invoke(db_path, "add", "Ouroboros", "--no-parent")
    invoke(db_path, "link", "Ouroboros", "Ouroboros")
    shown = invoke(db_path, "tree", "Ouroboros")
    assert "cycle" in shown.output


def test_tree_without_a_root_box_says_the_library_is_not_initialised(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No root box is a fact to report, not a reason to guess at a starting point."""
    monkeypatch.setenv("COLUMNS", "200")
    invoke(db_path, "add", "X", "--no-parent")
    invoke(db_path, "add", "Y", "--no-parent")
    invoke(db_path, "link", "Y", "X")
    invoke(db_path, "link", "X", "Y")
    shown = invoke(db_path, "tree")
    assert shown.exit_code == 1
    combined = shown.output + getattr(shown, "stderr", "")
    assert "no root box in this library" in combined


def test_tree_starts_at_the_root_box_when_no_name_is_given(db_path: Path) -> None:
    invoke(db_path, "init")
    invoke(db_path, "add", "Attic")
    invoke(db_path, "link", "Attic", "cabinet")
    shown = invoke(db_path, "tree")
    assert shown.exit_code == 0
    assert shown.output.strip().splitlines()[0].strip() == "\U0001f332 root"


def test_tree_still_starts_where_it_is_told(db_path: Path) -> None:
    invoke(db_path, "init")
    invoke(db_path, "add", "Garage")
    invoke(db_path, "add", "Drill", "--parent", "Garage")
    shown = invoke(db_path, "tree", "Garage")
    assert shown.exit_code == 0
    assert shown.output.strip().splitlines()[0].strip().endswith("Garage")


def test_the_old_box_group_and_link_spellings_are_gone(db_path: Path) -> None:
    """The commands moved; the old forms must fail rather than quietly work."""
    assert invoke(db_path, "box", "add", "X").exit_code != 0
    assert invoke(db_path, "link", "in", "X", "Y").exit_code != 0


def test_ls_and_list_answer_the_same_question(db_path: Path) -> None:
    invoke(db_path, "add", "Kitchen")
    assert invoke(db_path, "ls").output == invoke(db_path, "list").output


# -- the value of an mqtt sensor (MQTTResource) -------------------------------


def _broker(monkeypatch: pytest.MonkeyPatch, payloads: list[bytes]) -> None:
    """The broker replaced by a script: it sends what a test wrote, then hangs up."""
    from boxganizer import mqtt

    async def scripted(target: mqtt.Target) -> AsyncIterator[mqtt.Event]:
        yield mqtt.Event("connecting")
        yield mqtt.Event("live")
        for payload in payloads:
            yield mqtt.Event("value", mqtt.reading_from(target.topic, payload))

    monkeypatch.setattr(mqtt, "aiomqtt_events", scripted)


def _add_cellar_sensor(db_path: Path) -> None:
    result = invoke(
        db_path,
        "add",
        "Cellar",
        "-t",
        "mqtt",
        "-r",
        "mqtt://watcher:secretpass@cellar.example:1883",
        "-m",
        "topic=cellar/temp",
    )
    assert result.exit_code == 0


def test_box_value_prints_what_the_broker_sent(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _add_cellar_sensor(db_path)
    _broker(monkeypatch, [b"21.4"])
    result = invoke(db_path, "value", "Cellar")
    assert result.exit_code == 0
    assert "21.4" in result.output


def test_box_value_keeps_the_password_out_of_the_terminal(db_path: Path) -> None:
    _add_cellar_sensor(db_path)
    shown = invoke(db_path, "show", "Cellar")
    assert shown.exit_code == 0
    assert "secretpass" not in shown.output
    assert "cellar.example" in shown.output


def test_box_value_fails_when_nothing_arrives(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _add_cellar_sensor(db_path)
    _broker(monkeypatch, [])
    result = invoke(db_path, "value", "Cellar", "--wait", "0.2")
    assert result.exit_code == 1


def test_box_value_refuses_a_box_that_listens_to_nothing(db_path: Path) -> None:
    invoke(db_path, "add", "Kitchen")
    result = invoke(db_path, "value", "Kitchen")
    assert result.exit_code == 1


def test_index_reports_what_has_changed_without_reading_it(
    db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--check` answers from a stat, which is why it can be run on a timer."""
    import os
    import time

    monkeypatch.setenv("BOXGANIZER_CONTENT_ROOT", str(tmp_path))
    notes = tmp_path / "notes.txt"
    notes.write_text("the cellar pump", encoding="utf-8")
    invoke(db_path, "init")
    invoke(db_path, "add", "Notes", "-t", "text", "-r", str(notes), "--no-parent")

    clean = invoke(db_path, "index", "--check")
    assert clean.exit_code == 0, clean.output
    assert "nothing has changed" in clean.output

    notes.write_text("the cellar pump, and a new fact", encoding="utf-8")
    stamp = time.time() + 120
    os.utime(notes, (stamp, stamp))
    changed = invoke(db_path, "index", "--check")
    assert "1 file(s) changed" in changed.output
    assert "notes.txt" in changed.output
    assert "--stale" in changed.output  # and it says what to do about it

    reread = invoke(db_path, "index", "--stale")
    assert reread.exit_code == 0, reread.output
    assert "1 file read into the index" in reread.output
    assert "nothing has changed" in invoke(db_path, "index", "--check").output


def test_the_terminal_can_opt_a_file_in_and_out(
    db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The dialog's checkbox, said from the command line."""
    monkeypatch.setenv("BOXGANIZER_CONTENT_ROOT", str(tmp_path))
    archive = tmp_path / "bundle.zip"
    archive.write_text("not words, but say otherwise", encoding="utf-8")
    invoke(db_path, "init")
    invoke(db_path, "add", "Bundle", "-t", "file", "-r", str(archive), "--no-parent")

    assert "left out of the search" in invoke(db_path, "show", "Bundle").output

    added = invoke(db_path, "index", "--add", "Bundle")
    assert added.exit_code == 0, added.output
    assert "Bundle: in the index" in added.output

    dropped = invoke(db_path, "index", "--drop", "Bundle")
    assert "Bundle: out of the index" in dropped.output
    assert "left out of the search" in invoke(db_path, "show", "Bundle").output


def test_types_says_which_types_this_install_cannot_run(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same admission the page makes, for the person working in a terminal."""
    from boxganizer import mqtt as mqtt_module

    monkeypatch.setattr(mqtt_module, "has_dependency", lambda: False)
    result = invoke(db_path, "types")
    # rich wraps at the console width, so compare the sentence with its line breaks gone
    flat = " ".join(result.stdout.split())
    assert "mqtt" in flat
    assert "this install cannot run it: aiomqtt is missing" in flat

    import json

    as_json = json.loads(invoke(db_path, "types", "--json").stdout)
    sensor = next(kind for kind in as_json if kind["key"] == "mqtt")
    assert sensor["available"] is False
    assert sensor["unavailable_reason"]
    folder = next(kind for kind in as_json if kind["key"] == "folder")
    assert folder["available"] is True and folder["unavailable_reason"] == ""
