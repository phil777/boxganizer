"""Store behaviour: CRUD, search, links (many parents, cycles allowed), persistence."""

from __future__ import annotations

import os
import sqlite3
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from boxganizer import search as indexing
from boxganizer.errors import BoxNotFound, DuplicateBoxName, HandFilingRefused, StoreError
from boxganizer.models import Box
from boxganizer.store import SCHEMA_VERSION, BoxStore


def test_add_then_get(store: BoxStore) -> None:
    saved = store.add(Box(name="Attic", description="Dusty", icon="🕸️"))
    assert saved.id is not None
    loaded = store.get("Attic")
    assert loaded.description == "Dusty"
    assert loaded.icon == "🕸️"


def test_name_is_unique_case_insensitively(store: BoxStore) -> None:
    store.add(Box(name="Kitchen"))
    with pytest.raises(DuplicateBoxName):
        store.add(Box(name="kitchen"))


def test_get_unknown_raises(store: BoxStore) -> None:
    with pytest.raises(BoxNotFound):
        store.get("Nothing")


def test_exists(store: BoxStore) -> None:
    store.add(Box(name="Garage"))
    assert store.exists("GARAGE") is True
    assert store.exists("Shed") is False


def test_update_persists_fields(store: BoxStore) -> None:
    box = store.add(Box(name="Books"))
    box.description = "Novels"
    box.metadata["count"] = "12"
    store.update(box)
    reloaded = store.get("books")
    assert reloaded.description == "Novels"
    assert reloaded.metadata == {"count": "12"}


def test_remove_deletes_box_and_its_links(populated: BoxStore) -> None:
    populated.remove("Passport")
    assert populated.exists("Passport") is False
    assert [b.name for b in populated.children("Kitchen")] == []


def test_list_is_ordered_by_name(store: BoxStore) -> None:
    for name in ["zeta", "Alpha", "beta"]:
        store.add(Box(name=name))
    assert [b.name for b in store.list_boxes()] == ["Alpha", "beta", "zeta"]


def test_list_honours_limit(store: BoxStore) -> None:
    for index in range(5):
        store.add(Box(name=f"Box{index}"))
    assert len(store.list_boxes(limit=3)) == 3


@pytest.mark.parametrize(
    ("term", "expected"),
    [
        ("cook", ["Kitchen"]),
        ("FR", ["Passport"]),
        ("2031", ["Passport"]),
        ("house", ["House"]),
    ],
)
def test_search_hits_every_field(populated: BoxStore, term: str, expected: list[str]) -> None:
    assert [b.name for b in populated.search(term)] == expected


def test_search_treats_wildcards_literally(store: BoxStore) -> None:
    store.add(Box(name="100% cotton"))
    store.add(Box(name="100 days"))
    assert [b.name for b in store.search("100%")] == ["100% cotton"]


def test_empty_search_returns_everything(populated: BoxStore) -> None:
    assert len(populated.search("  ")) == populated.count()


def test_link_children_and_parents(populated: BoxStore) -> None:
    assert [b.name for b in populated.children("House")] == ["Garage", "Kitchen"]
    assert [b.name for b in populated.parents("Passport")] == ["Kitchen"]


def test_linking_twice_is_idempotent(store: BoxStore) -> None:
    store.add(Box(name="A"))
    store.add(Box(name="B"))
    store.link("A", "B")
    store.link("A", "B")
    assert len(store.children("A")) == 1


def test_unlink(store: BoxStore) -> None:
    store.add(Box(name="A"))
    store.add(Box(name="B"))
    store.link("A", "B")
    store.unlink("A", "B")
    assert store.children("A") == []
    store.unlink("A", "B")  # removing an absent link is not an error


def test_a_box_may_be_its_own_child(store: BoxStore) -> None:
    store.add(Box(name="A"))
    store.link("A", "A")
    assert [box.name for box in store.children("A")] == ["A"]
    assert [box.name for box in store.parents("A")] == ["A"]


def test_cycles_are_allowed(store: BoxStore) -> None:
    for name in ["A", "B", "C"]:
        store.add(Box(name=name))
    store.link("A", "B")
    store.link("B", "C")
    store.link("C", "A")
    assert [box.name for box in store.children("C")] == ["A"]
    assert [box.name for box in store.parents("A")] == ["C"]


def test_a_child_can_have_many_parents(store: BoxStore) -> None:
    for name in ["House", "Studio", "Painting"]:
        store.add(Box(name=name))
    store.link("House", "Painting")
    store.link("Studio", "Painting")
    assert sorted(box.name for box in store.parents("Painting")) == ["House", "Studio"]
    # removing one parent leaves the other
    store.unlink("House", "Painting")
    assert [box.name for box in store.parents("Painting")] == ["Studio"]


def test_linking_the_same_pair_twice_is_not_an_error(store: BoxStore) -> None:
    store.add(Box(name="A"))
    store.add(Box(name="B"))
    store.link("A", "B")
    store.link("A", "B")
    assert len(store.children("A")) == 1


def test_tree_stops_at_a_cycle(store: BoxStore) -> None:
    store.add(Box(name="A"))
    store.add(Box(name="B"))
    store.link("A", "B")
    store.link("B", "A")
    node = store.tree("A")
    children = node["children"]
    assert isinstance(children, list) and len(children) == 1
    inner = children[0]
    assert inner["box"].name == "B"
    assert inner["children"][0]["truncated"] is True


def test_deleting_a_box_in_a_cycle_leaves_the_others(store: BoxStore) -> None:
    store.add(Box(name="A"))
    store.add(Box(name="B"))
    store.link("A", "B")
    store.link("B", "A")
    store.remove("B")
    assert store.children("A") == []
    assert store.parents("A") == []


def test_tree_shape(populated: BoxStore) -> None:
    node = populated.tree("House")
    assert node["box"].name == "House"
    kids = node["children"]
    assert isinstance(kids, list) and len(kids) == 2
    kitchen = next(k for k in kids if k["box"].name == "Kitchen")
    assert [c["box"].name for c in kitchen["children"]] == ["Passport"]


def test_data_survives_reopen(db_path: Path) -> None:
    with BoxStore(db_path) as store:
        store.add(Box(name="Keep me", metadata={"n": "1"}))
    with BoxStore(db_path) as again:
        assert again.get("keep me").metadata == {"n": "1"}


def test_count(populated: BoxStore) -> None:
    assert populated.count() == 5


# -- box types --------------------------------------------------------------


def test_box_type_round_trips(store: BoxStore) -> None:
    store.add(Box(name="Notes", box_type="markdown", resource_uri="/tmp/notes.md"))
    assert store.get("notes").box_type == "markdown"


def test_add_rejects_an_unknown_type(store: BoxStore) -> None:
    from boxganizer.errors import UnknownBoxType

    with pytest.raises(UnknownBoxType):
        store.add(Box(name="Whatever", box_type="spreadsheet"))


def test_add_enforces_the_rules_of_the_type(store: BoxStore) -> None:
    from boxganizer.errors import InvalidBoxContent

    with pytest.raises(InvalidBoxContent, match="needs a resource"):
        store.add(Box(name="No picture", box_type="image"))
    with pytest.raises(InvalidBoxContent, match="must end with"):
        store.add(Box(name="Wrong", box_type="archive", resource_uri="/tmp/plain.zip.txt"))


def test_typed_boxes_survive_reopening(db_path: Path) -> None:
    with BoxStore(db_path) as store:
        store.add(
            Box(
                name="Sensor",
                box_type="mqtt",
                resource_uri="mqtt://broker:1883/cellar/temp",
                metadata={"topic": "cellar/temp"},  # an override, still honoured
            )
        )
    with BoxStore(db_path) as again:
        box = again.get("sensor")
        assert box.box_type == "mqtt"
        assert box.metadata == {"topic": "cellar/temp"}


def test_update_can_change_the_type(store: BoxStore) -> None:
    box = store.add(Box(name="Thing"))
    assert box.box_type == "box"
    box.box_type = "label"
    store.update(box)
    assert store.get("thing").box_type == "label"


def test_update_still_enforces_the_type(store: BoxStore) -> None:
    """A box may not be retyped into something it does not satisfy."""
    from boxganizer.errors import InvalidBoxContent

    box = store.add(Box(name="Note"))
    box.box_type = "pdf"
    with pytest.raises(InvalidBoxContent, match="needs a resource"):
        store.update(box)


def test_linking_into_a_leaf_type_is_refused(store: BoxStore) -> None:
    from boxganizer.errors import ChildrenNotAllowed

    store.add(Box(name="Picture", box_type="image", resource_uri="/tmp/a.png"))
    store.add(Box(name="Notes", box_type="markdown", resource_uri="/tmp/a.md"))
    with pytest.raises(ChildrenNotAllowed, match="holds a resource"):
        store.link("Picture", "Notes")
    assert store.children("Picture") == []


def test_a_directory_may_hold_boxes(store: BoxStore) -> None:
    store.add(Box(name="Trip", box_type="directory", resource_uri="/tmp"))
    store.add(Box(name="Photos", box_type="indexed-directory", resource_uri="/tmp/photos"))
    store.link("Trip", "Photos")
    assert [b.name for b in store.children("Trip")] == ["Photos"]


# -- schema migration -------------------------------------------------------

_V1_SCHEMA = """
CREATE TABLE boxes (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, key TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '', icon TEXT NOT NULL DEFAULT 'x',
    resource_uri TEXT, metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX boxes_key_idx ON boxes (key);
CREATE TABLE links (
    parent_id INTEGER NOT NULL, child_id INTEGER NOT NULL, added_at TEXT NOT NULL,
    PRIMARY KEY (parent_id, child_id)
);
PRAGMA user_version = 1;
"""


def _write_v1_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(_V1_SCHEMA)
    connection.execute(
        "INSERT INTO boxes (name, key, created_at, updated_at) VALUES (?, ?, ?, ?)",
        ("Old note", "old note", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
    )
    connection.commit()
    connection.close()


def test_an_older_database_gains_the_box_type_column(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    _write_v1_database(path)

    with BoxStore(path) as store:
        assert store.get("Old note").box_type == "box"
        assert store.count() == 1
        # the upgraded database behaves like a new one
        store.add(Box(name="Picture", box_type="image", resource_uri="/tmp/a.png"))

    connection = sqlite3.connect(path)
    try:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        columns = {row[1] for row in connection.execute("PRAGMA table_info(boxes)")}
    finally:
        connection.close()
    assert version == SCHEMA_VERSION
    assert "box_type" in columns


def test_migrating_twice_is_not_an_error(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    _write_v1_database(path)
    with BoxStore(path):
        pass
    with BoxStore(path) as again:
        assert again.get("Old note").box_type == "box"


# -- config ----------------------------------------------------------------


def test_config_round_trips(store: BoxStore) -> None:
    store.add(
        Box(name="Grid", box_type="grid", config={"columns": 3, "slots": ["a", {"deep": True}]})
    )
    assert store.get("grid").config == {"columns": 3, "slots": ["a", {"deep": True}]}


def test_config_defaults_to_empty(store: BoxStore) -> None:
    store.add(Box(name="Plain"))
    assert store.get("Plain").config == {}


def test_update_writes_config(store: BoxStore) -> None:
    box = store.add(Box(name="Grid", box_type="grid"))
    box.config["columns"] = 4
    store.update(box)
    assert store.get("grid").config == {"columns": 4}


def test_config_holds_what_json_can_hold(store: BoxStore) -> None:
    from boxganizer.errors import StoreError

    with pytest.raises(StoreError, match="JSON cannot store"):
        store.add(Box(name="Bad", config={"when": object()}))


def test_search_reaches_into_config(store: BoxStore) -> None:
    store.add(Box(name="Cellar", box_type="cabinet", config={"contents": "old wine"}))
    store.add(Box(name="Attic", box_type="cabinet", config={"contents": "ski boots"}))
    assert [box.name for box in store.search("wine")] == ["Cellar"]


def test_broken_config_text_degrades_to_empty(db_path: Path) -> None:
    with BoxStore(db_path) as store:
        store.add(Box(name="Thing"))
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("UPDATE boxes SET config = 'not json' WHERE name = 'Thing'")
        connection.commit()
    finally:
        connection.close()
    with BoxStore(db_path) as store:
        assert store.get("Thing").config == {}


# -- singletons ------------------------------------------------------------


def test_only_one_root_box_may_exist(store: BoxStore) -> None:
    from boxganizer.errors import InvalidBoxContent

    store.add(Box(name="root", box_type="root"))
    with pytest.raises(InvalidBoxContent, match="only one root box is allowed"):
        store.add(Box(name="second root", box_type="root"))


def test_a_singleton_may_be_edited_as_itself(store: BoxStore) -> None:
    root = store.add(Box(name="root", box_type="root", description="top"))
    root.description = "the top of everything"
    assert store.update(root).description == "the top of everything"


# -- schema v3 -------------------------------------------------------------


def _write_v2_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(_V1_SCHEMA.replace("user_version = 1", "user_version = 2"))
    connection.execute("ALTER TABLE boxes ADD COLUMN box_type TEXT NOT NULL DEFAULT 'box'")
    connection.execute(
        "INSERT INTO boxes (name, key, created_at, updated_at, box_type) VALUES (?, ?, ?, ?, ?)",
        ("From v2", "from v2", "2026-02-02T00:00:00+00:00", "2026-02-02T00:00:00+00:00", "box"),
    )
    connection.commit()
    connection.close()


def test_a_v2_database_gains_the_config_column(tmp_path: Path) -> None:
    path = tmp_path / "v2.db"
    _write_v2_database(path)

    with BoxStore(path) as store:
        assert store.get("From v2").config == {}
        box = store.get("From v2")
        box.config["note"] = "written after the upgrade"
        store.update(box)

    with BoxStore(path) as again:
        assert again.get("from v2").config == {"note": "written after the upgrade"}
        assert again.count() == 1


def test_upgrading_from_v1_adds_both_columns(tmp_path: Path) -> None:
    path = tmp_path / "v1.db"
    _write_v1_database(path)
    with BoxStore(path) as store:
        box = store.get("Old note")
        assert box.box_type == "box" and box.config == {}


# -- uuid ------------------------------------------------------------------


def test_every_box_gets_a_uuid(store: BoxStore) -> None:
    saved = store.add(Box(name="Thing"))
    assert len(saved.uuid) == 36
    assert store.get("Thing").uuid == saved.uuid


def test_uuids_are_unique(store: BoxStore) -> None:
    uuids = {store.add(Box(name=f"B{i}")).uuid for i in range(25)}
    assert len(uuids) == 25


def test_a_uuid_survives_a_rename(store: BoxStore) -> None:
    saved = store.add(Box(name="Before"))
    box = store.get("Before")
    box.name = "After"
    store.update(box)
    assert store.get("After").uuid == saved.uuid


def test_a_box_is_found_by_name_or_uuid(store: BoxStore) -> None:
    saved = store.add(Box(name="Kitchen"))
    assert store.resolve("Kitchen").uuid == saved.uuid
    assert store.resolve("kitchen").uuid == saved.uuid
    assert store.resolve(saved.uuid).name == "Kitchen"
    assert store.resolve(f"  {saved.uuid.upper()}  ").name == "Kitchen"


def test_resolving_an_unknown_reference_raises(store: BoxStore) -> None:
    with pytest.raises(BoxNotFound):
        store.resolve("nope")


def test_uuid_is_kept_when_supplied() -> None:
    box = Box(name="Fixed", uuid="0" * 36)
    assert box.uuid == "0" * 36


def test_an_older_database_backfills_uuids(tmp_path: Path) -> None:
    path = tmp_path / "v2.db"
    _write_v2_database(path)

    with BoxStore(path) as store:
        box = store.get("From v2")
        assert len(box.uuid) == 36
        assert store.resolve(box.uuid).name == "From v2"
        added = store.add(Box(name="Written after"))
        assert added.uuid != box.uuid

    # and the upgrade is a one-time event
    with BoxStore(path) as again:
        assert again.get("From v2").uuid == box.uuid


def test_links_accept_uuid_references(store: BoxStore) -> None:
    parent = store.add(Box(name="House"))
    child = store.add(Box(name="Kitchen"))
    store.link(parent.uuid, child.uuid)
    assert [box.name for box in store.children(parent.uuid)] == ["Kitchen"]
    assert [box.name for box in store.parents(child.name)] == ["House"]
    store.unlink(parent.uuid, child.uuid)
    assert store.children(parent.name) == []


# -- the root stays at the top ---------------------------------------------


def test_the_root_box_cannot_be_filed_inside_another(store: BoxStore) -> None:
    from boxganizer.errors import RootCannotBeFiled

    store.add(Box(name="root", box_type="root"))
    store.add(Box(name="Something", box_type="folder"))
    with pytest.raises(RootCannotBeFiled, match="cannot go inside"):
        store.link("Something", "root")
    assert store.children("Something") == []


def test_a_box_can_still_be_linked_under_the_root(store: BoxStore) -> None:
    store.add(Box(name="root", box_type="root"))
    store.add(Box(name="Notes", box_type="text", resource_uri="/tmp/a.txt"))
    store.link("root", "Notes")
    assert [box.name for box in store.children("root")] == ["Notes"]


# -- the root box has one child slot ---------------------------------------


def test_the_root_holds_one_child_and_replaces_it(store: BoxStore) -> None:
    store.add(Box(name="root", box_type="root"))
    store.add(Box(name="First", box_type="folder"))
    store.add(Box(name="Second", box_type="folder"))
    store.link("root", "First")
    assert [box.name for box in store.children("root")] == ["First"]

    store.link("root", "Second")

    assert [box.name for box in store.children("root")] == ["Second"]
    assert store.root_child() is not None
    assert store.root_child().name == "Second"
    # the displaced box still exists, it is just not the way in any more
    assert store.exists("First")
    assert store.parents("First") == []


def test_the_root_slot_can_be_read_when_empty(store: BoxStore) -> None:
    store.add(Box(name="root", box_type="root"))
    assert store.root_child() is None


def test_relinking_the_same_box_into_the_root_keeps_it(store: BoxStore) -> None:
    store.add(Box(name="root", box_type="root"))
    store.add(Box(name="Only", box_type="folder"))
    store.link("root", "Only")
    store.link("root", "Only")
    assert [box.name for box in store.children("root")] == ["Only"]


def test_a_box_displaced_from_the_root_keeps_its_other_parent(store: BoxStore) -> None:
    store.add(Box(name="root", box_type="root"))
    store.add(Box(name="Archive", box_type="folder"))
    store.add(Box(name="Shared", box_type="folder"))
    store.link("Archive", "Shared")
    store.link("root", "Shared")
    store.link("root", store.add(Box(name="Newcomer", box_type="folder")).name)
    assert [box.name for box in store.parents("Shared")] == ["Archive"]


def test_a_file_box_may_not_point_at_a_directory(store: BoxStore, tmp_path: Path) -> None:
    from boxganizer.errors import InvalidBoxContent

    a_directory = tmp_path / "drawers"
    a_directory.mkdir()
    with pytest.raises(InvalidBoxContent, match="is a directory"):
        store.add(Box(name="Wrong", box_type="file", resource_uri=str(a_directory)))
    # the same path is fine as a directory box
    store.add(Box(name="Right", box_type="directory", resource_uri=str(a_directory)))
    assert store.get("Right").box_type == "directory"


def test_retyping_a_box_that_holds_boxes_is_refused(store: BoxStore) -> None:
    """A resource type cannot contain other boxes, so it cannot be retyped into one."""
    from boxganizer.errors import ChildrenNotAllowed

    parent = store.add(Box(name="holder", box_type="folder"))
    store.add(Box(name="held", box_type="box"))
    store.link(parent.uuid, "held")
    parent.box_type = "image"
    parent.resource_uri = "/tmp/pic.png"
    with pytest.raises(ChildrenNotAllowed):
        store.update(parent)
    assert store.get("holder").box_type == "folder"


def test_retyping_a_leaf_box_that_happens_to_have_parents_is_fine(store: BoxStore) -> None:
    """Three parents is legal; retyping the *child* is still allowed."""
    from boxganizer.models import Box as _Box

    store.add(_Box(name="one", box_type="folder"))
    store.add(_Box(name="two", box_type="folder"))
    leaf = store.add(_Box(name="leaf", box_type="box"))
    store.link("one", leaf.uuid)
    store.link("two", leaf.uuid)
    leaf.box_type = "text"
    leaf.resource_uri = "/tmp/a.txt"
    assert store.update(leaf).box_type == "text"


def test_an_integrity_error_that_is_not_a_name_clash_says_so(store: BoxStore) -> None:
    """The blanket mapping used to blame the name for every integrity failure."""
    from boxganizer.errors import StoreError

    with pytest.raises(StoreError) as caught:
        store._execute("INSERT INTO boxes (uuid, name, key) VALUES ('u', NULL, 'k')")
    assert "NOT NULL" in str(caught.value)
    assert "already exists" not in str(caught.value)


# -- the root box, found by what it is ---------------------------------------


def test_the_root_box_is_found_when_the_library_outgrows_a_page(store: BoxStore) -> None:
    """A page of names is not the library: the root must be found anyway."""
    root = store.add(Box(name="root", box_type="root"))
    for index in range(250):
        store.add(Box(name=f"a{index:03d}"))
    assert store.root_box().uuid == root.uuid
    # the page the interfaces ask for does not hold it
    page = [box.name for box in store.list_boxes(limit=200)]
    assert "root" not in page


def test_the_root_box_is_found_whatever_it_is_named(store: BoxStore) -> None:
    root = store.add(Box(name="way-in", box_type="root"))
    store.add(Box(name="aardvark"))
    assert store.root_box().uuid == root.uuid


def test_a_library_without_a_root_has_no_root_box(store: BoxStore) -> None:
    store.add(Box(name="only"))
    assert store.root_box() is None


# -- dynamic folders: the filter decides, the link table records -------------


def names(store: BoxStore, parent: str) -> list[str]:
    return sorted(box.name for box in store.children(parent))


def test_a_dynamic_folder_takes_the_boxes_that_already_match_it(store: BoxStore) -> None:
    store.add(Box(name="report-one", box_type="pdf", resource_uri="/tmp/one.pdf"))
    store.add(Box(name="report-two", box_type="pdf", resource_uri="/tmp/two.pdf"))
    store.add(Box(name="note", box_type="text", resource_uri="/tmp/note.txt"))
    store.add(Box(name="PDFs", box_type="dynamic-folder", config={"filter": {"type": "pdf"}}))
    assert names(store, "PDFs") == ["report-one", "report-two"]


def test_a_box_created_afterwards_joins_the_folder_that_wants_it(store: BoxStore) -> None:
    store.add(Box(name="PDFs", box_type="dynamic-folder", config={"filter": {"type": "pdf"}}))
    store.add(Box(name="late", box_type="pdf", resource_uri="/tmp/late.pdf"))
    assert "late" in names(store, "PDFs")


def test_editing_the_filter_updates_the_link_table(store: BoxStore) -> None:
    store.add(Box(name="a", box_type="pdf", resource_uri="/tmp/a.pdf"))
    store.add(Box(name="b", box_type="text", resource_uri="/tmp/b.txt"))
    store.add(Box(name="PDFs", box_type="dynamic-folder", config={"filter": {"type": "pdf"}}))
    assert names(store, "PDFs") == ["a"]

    folder = store.get("PDFs")
    folder.config = {"filter": {"type": "text"}}
    store.update(folder)
    # b in, a out: the filter is the authority, not what was linked once
    assert names(store, "PDFs") == ["b"]


def test_retyping_a_box_moves_it_out_of_the_folder_it_stopped_matching(
    store: BoxStore,
) -> None:
    store.add(Box(name="doc", box_type="pdf", resource_uri="/tmp/doc.pdf"))
    store.add(Box(name="PDFs", box_type="dynamic-folder", config={"filter": {"type": "pdf"}}))
    assert names(store, "PDFs") == ["doc"]

    doc = store.get("doc")
    doc.box_type = "text"
    doc.resource_uri = "/tmp/doc.txt"
    store.update(doc)
    assert names(store, "PDFs") == []


def test_renaming_a_box_can_pull_it_into_a_folder(store: BoxStore) -> None:
    store.add(Box(name="misc", box_type="box"))
    # named so it does not select itself: a folder whose filter fits its own name is
    # its own member, which other tests cover on purpose
    store.add(
        Box(name="Billing", box_type="dynamic-folder", config={"filter": {"name": "invoice"}})
    )
    assert names(store, "Billing") == []

    misc = store.get("misc")
    misc.name = "invoice 2026"
    store.update(misc)
    assert names(store, "Billing") == ["invoice 2026"]


def test_a_broken_filter_holds_nothing_rather_than_everything(store: BoxStore) -> None:
    store.add(Box(name="one"))
    store.add(Box(name="Ouch", box_type="dynamic-folder", config={"filter": {"colour": "red"}}))
    assert names(store, "Ouch") == []


def test_a_dynamic_folder_matching_itself_holds_itself(store: BoxStore) -> None:
    store.add(Box(name="everything", box_type="dynamic-folder"))
    assert names(store, "everything") == ["everything"]


def test_the_cabinet_holds_every_box_and_is_not_given_the_root(store: BoxStore) -> None:
    store.add(Box(name="attic", box_type="box"))
    store.add(Box(name="way-in", box_type="root"))
    store.add(Box(name="cabinet", box_type="cabinet", config={"filter": {"name": "nothing"}}))
    # a cabinet ignores any filter it is handed: it is defined as the whole library
    assert names(store, "cabinet") == ["attic", "cabinet"]
    # ...and the root is filed nowhere, so it stays out of the cabinet
    assert store.parents("way-in") == []


def test_deleting_a_box_takes_its_dynamic_membership_with_it(store: BoxStore) -> None:
    store.add(Box(name="gone", box_type="pdf", resource_uri="/tmp/gone.pdf"))
    store.add(Box(name="PDFs", box_type="dynamic-folder", config={"filter": {"type": "pdf"}}))
    assert names(store, "PDFs") == ["gone"]
    store.remove("gone")
    assert names(store, "PDFs") == []


def test_resolving_leaves_a_plain_folders_links_alone(store: BoxStore) -> None:
    """A folder filled by hand is not a filter's business."""
    store.add(Box(name="box", box_type="pdf", resource_uri="/tmp/b.pdf"))
    store.add(Box(name="Handmade", box_type="folder"))
    store.link("Handmade", "box")
    assert store.resolve_folder("Handmade") is None
    assert names(store, "Handmade") == ["box"]


def test_several_dynamic_folders_each_keep_their_own_membership(store: BoxStore) -> None:
    store.add(Box(name="pdf-one", box_type="pdf", resource_uri="/tmp/one.pdf"))
    store.add(Box(name="pic-one", box_type="image", resource_uri="/tmp/one.png"))
    store.add(Box(name="PDFs", box_type="dynamic-folder", config={"filter": {"type": "pdf"}}))
    store.add(Box(name="Pictures", box_type="dynamic-folder", config={"filter": {"type": "image"}}))
    assert names(store, "PDFs") == ["pdf-one"]
    assert names(store, "Pictures") == ["pic-one"]

    store.add(Box(name="pdf-two", box_type="pdf", resource_uri="/tmp/two.pdf"))
    assert names(store, "PDFs") == ["pdf-one", "pdf-two"]
    assert names(store, "Pictures") == ["pic-one"]


def test_resolve_folder_reports_the_member_count_it_settled_on(store: BoxStore) -> None:
    store.add(Box(name="x", box_type="pdf", resource_uri="/tmp/x.pdf"))
    store.add(Box(name="PDFs", box_type="dynamic-folder", config={"filter": {"type": "pdf"}}))
    assert store.resolve_folder("PDFs") == 1
    with pytest.raises(BoxNotFound):
        store.resolve_folder("no-such-box")


def test_a_dynamic_folder_refuses_a_box_filed_into_it_by_hand(store: BoxStore) -> None:
    """A filtered folder decides its own members; a drop is not a vote.

    Hand-filing used to be allowed and lasted until the next re-list took the box back out. The
    person saw the box arrive and then vanish, with nothing saying which of their actions did it.
    Refusing the drop is worse news delivered sooner, and the sentence names the way out.
    """
    store.add(Box(name="square", box_type="box"))
    store.add(Box(name="PDFs", box_type="dynamic-folder", config={"filter": {"type": "pdf"}}))

    with pytest.raises(HandFilingRefused) as raised:
        store.link("PDFs", "square")

    assert "dynamic folder" in str(raised.value)
    assert "ordinary folder" in str(raised.value)  # the sentence offers somewhere to put it
    assert names(store, "PDFs") == []
    # The refusal is about being filed there, not about the box existing anywhere: filing it in an
    # ordinary folder is untouched by any of this.
    store.add(Box(name="Papers", box_type="folder"))
    store.link("Papers", "square")
    assert names(store, "Papers") == ["square"]


def test_a_cabinet_still_takes_a_box_filed_into_it_by_hand(store: BoxStore) -> None:
    """The exception is drawn where refusing would protect nobody.

    A cabinet's filter selects every box, so nothing filed there is ever taken back out: the
    hazard the refusal exists for cannot happen, and refusing would break "New box in Cabinet".
    """
    store.add(Box(name="loose", box_type="box"))
    store.add(Box(name="Everything", box_type="cabinet"))
    store.link("Everything", "loose")
    assert "loose" in names(store, "Everything")


def test_a_new_box_that_matches_a_folder_lands_in_it_without_being_asked(store: BoxStore) -> None:
    """Creating a box checks it against every folder's filter; it does not re-cut them.

    Membership is written rather than computed on read, so a dynamic folder shows up in `box show`,
    in the tree and in every box's `in` list like any other container. The cheap direction is the
    new box: one box against every filter. The other direction — every box against one filter —
    happens when that folder is opened or its filter edited, because doing it on every creation
    would cost the whole library each time. Tag changes go the other way and re-list the folders
    that ask about tags; see `test_a_tag_change_re_lists_the_dynamic_folders_that_ask_about_tags`.
    """
    store.add(Box(name="PDFs", box_type="dynamic-folder", config={"filter": {"type": "pdf"}}))
    store.add(Box(name="square", box_type="box"))
    assert names(store, "PDFs") == []

    store.add(Box(name="anything", box_type="pdf", resource_uri="/tmp/a.pdf"))
    assert names(store, "PDFs") == ["anything"]

    # A box that stops matching leaves the same instant it changes.
    pdf = store.get("anything")
    store.update(replace(pdf, box_type="box"))
    assert names(store, "PDFs") == []


def test_upgrading_an_older_library_fills_the_cabinet(db_path: Path) -> None:
    """A cabinet written before it meant "everything" is re-cut on the way in."""
    with BoxStore(db_path) as store:
        store.add(Box(name="one"))
        store.add(Box(name="two"))
        store.add(Box(name="cabinet", box_type="cabinet"))
        # an old library kept such a cabinet empty: wipe the membership the new
        # build wrote on the way in, and mark the file as written before it existed
        # reaching past the API on purpose: this test is about the file on disk
        with store._conn:
            store._conn.execute("DELETE FROM links")
            store._conn.execute("PRAGMA user_version = 4")
        assert store.children("cabinet") == []

    with BoxStore(db_path) as reopened:
        filled = sorted(box.name for box in reopened.children("cabinet"))
        assert filled == ["cabinet", "one", "two"]
        assert reopened.get("one").name == "one"


def test_a_dynamic_folder_is_recut_on_upgrade_too(db_path: Path) -> None:
    with BoxStore(db_path) as store:
        store.add(Box(name="pic", box_type="image", resource_uri="/tmp/p.png"))
        store.add(Box(name="txt", box_type="text", resource_uri="/tmp/t.txt"))
        store.add(Box(name="Pics", box_type="dynamic-folder", config={"filter": {"type": "image"}}))
        with store._conn:
            parent = "(SELECT id FROM boxes WHERE key = 'pics')"
            store._conn.execute(f"DELETE FROM links WHERE parent_id = {parent}")
            store._conn.execute("PRAGMA user_version = 4")
        assert store.children("Pics") == []

    with BoxStore(db_path) as reopened:
        assert [box.name for box in reopened.children("Pics")] == ["pic"]


def test_which_boxes_are_waiting_on_the_index(tmp_path: Path) -> None:
    """The names behind the number in the status line.

    Three states are told apart because the advice differs, and one of them must not be
    offered as work at all: an empty file has no words to bring in, and a list that said
    otherwise would grow by one item every time somebody created a note.
    """
    store = BoxStore(tmp_path / "pending.db", content_root=tmp_path)
    try:
        changed = tmp_path / "changed.md"
        changed.write_text("plumbing", encoding="utf-8")
        unreadable = tmp_path / "binary.bin"
        unreadable.write_bytes(b"\x00\x01\x02binary\x00\x01" * 200)
        empty = tmp_path / "empty.md"
        empty.write_text("", encoding="utf-8")
        later = tmp_path / "later.md"
        later.write_text("boiler", encoding="utf-8")

        store.add(Box(name="Changed", box_type="markdown", resource_uri=str(changed)))
        store.add(Box(name="Binary", box_type="text", resource_uri=str(unreadable)))
        store.add(Box(name="Empty", box_type="note", resource_uri=str(empty)))
        store.add(Box(name="Later", box_type="markdown", resource_uri=str(later)))
        indexing.rebuild(store, root=tmp_path)
        # The binary is already in the list, and that is the point: it is the one kind of
        # item a pass cannot clear, which is why the status line counts only the fixable
        # ones and this list says which is which.
        after_pass = store.needing_index()
        assert [item["reference"] for item in after_pass] == ["binary.bin"]
        assert after_pass[0]["fixable"] is False

        # one file edited outside Boxganizer, which a pass will catch
        changed.write_text("plumbing and boilers", encoding="utf-8")
        os.utime(changed, (changed.stat().st_atime, changed.stat().st_mtime + 5))
        store.check_observations()
        # one file the index has never opened: the row says so, whatever put it there
        untouched(store, "later.md")
        # one file that was read, has bytes, and gave up no words: named, not promised
        read_and_empty_handed(store, "binary.bin")

        listed = {item["reference"].split("/")[-1]: item for item in store.needing_index()}
        assert set(listed) == {"changed.md", "later.md", "binary.bin"}, listed
        assert "empty.md" not in listed, "an empty file has nothing to index, forever"

        assert listed["changed.md"]["why"] == "changed"
        assert listed["changed.md"]["fixable"] is True
        assert [box["name"] for box in listed["changed.md"]["boxes"]] == ["Changed"]

        assert listed["later.md"]["why"] == "never"
        assert listed["later.md"]["fixable"] is True
        assert listed["later.md"]["words"] == "has not been read yet"

        assert listed["binary.bin"]["fixable"] is False
        assert "no text" in listed["binary.bin"]["words"]
    finally:
        store.close()


def untouched(store: BoxStore, name: str) -> None:
    """Put a resource back to the state a never-read one is in."""
    store._execute(
        "UPDATE resources SET indexed_at = NULL, mtime = NULL, bytes = NULL, digest = NULL,"
        " stale = 0 WHERE reference LIKE ?",
        (f"%{name}%",),
    )
    store._execute(
        "DELETE FROM resource_text WHERE resource_id IN (SELECT id FROM resources"
        " WHERE reference LIKE ?)",
        (f"%{name}%",),
    )


def read_and_empty_handed(store: BoxStore, name: str) -> None:
    """A file that was read, has bytes, and yielded nothing searchable."""
    store._execute(
        "UPDATE resources SET stale = 0, indexed_at = '2026-01-01T00:00:00+00:00',"
        " bytes = 3200 WHERE reference LIKE ?",
        (f"%{name}%",),
    )


def test_a_file_inside_an_indexed_folder_is_named_by_its_folder(tmp_path: Path) -> None:
    """A folder's files have no box of their own, so the folder lends them its name.

    The walk brings in text files and skips everything else, so a file inside a folder is a
    row once a pass has seen it — and the only useful thing left to ask is which of those rows
    have gone stale, which is what a search would get wrong in silence. Notably *not* tested
    here: a file added since the last pass. It has no row yet, no query can name it, and the
    page says so rather than implying the list is the whole truth.
    """
    store = BoxStore(tmp_path / "folder-pending.db", content_root=tmp_path)
    try:
        folder = tmp_path / "notes"
        folder.mkdir()
        one = folder / "one.md"
        one.write_text("first words", encoding="utf-8")
        (folder / "bad.bin").write_bytes(b"\x00\x02binary\x00" * 400)
        holder = store.add(
            Box(name="Notes", box_type="indexed-directory", resource_uri=str(folder))
        )
        # A folder is indexed when somebody marks it, not because a folder box exists: the
        # request was "when a directory box is marked for indexing", and the tick means it.
        store.set_index_flag(holder, True)
        indexing.rebuild(store, root=tmp_path)
        assert store.needing_index() == [], "the pass read what it could and skipped the rest"

        one.write_text("first words, and then some more", encoding="utf-8")
        os.utime(one, (one.stat().st_atime, one.stat().st_mtime + 5))
        store.check_observations()

        listed = {item["reference"].split("/")[-1]: item for item in store.needing_index()}
        assert set(listed) == {"one.md"}, listed
        boxes = listed["one.md"]["boxes"]
        assert boxes and boxes[0]["name"] == "Notes" and boxes[0]["inside"] is True
        assert boxes[0]["uuid"] == holder.uuid
        assert listed["one.md"]["fixable"] is True
    finally:
        store.close()


def test_deleting_a_box_files_its_children_nowhere_new(store: BoxStore) -> None:
    """A box is a place a thing is filed, not the thing: `rm` takes the place, not the contents.

    The links *from* the deleted box are its own business and go with it. A child filed elsewhere
    keeps that filing; a child filed nowhere is still in the library, just not inside anything. The
    delete that emptied a folder onto the floor and burned the floor too would be unrecoverable from
    a confirmation dialog nobody reads carefully.
    """
    store.add(Box(name="Kitchen"))
    store.add(Box(name="Attic"))
    store.add(Box(name="Drawer"))
    store.add(Box(name="Passport"))
    store.add(Box(name="Loose"))
    store.link("Kitchen", "Drawer")
    store.link("Attic", "Drawer")  # two parents: the other one must survive
    store.link("Drawer", "Passport")
    store.link("Drawer", "Loose")

    gone = store.delete_box("Drawer")

    assert (gone.box.name, gone.children, gone.parents) == ("Drawer", 2, 2)
    assert [box.name for box in store.children("Kitchen")] == []
    assert [box.name for box in store.children("Attic")] == [], "the deleted link was the Drawer's"
    with pytest.raises(BoxNotFound):
        store.get("Drawer")
    # the children are untouched, in the library, and filed in nothing that did not hold them
    assert {box.name for box in store.list_boxes()} >= {"Passport", "Loose"}
    assert sorted(box.name for box in store.parents("Passport")) == []


def test_the_root_box_cannot_be_deleted(store: BoxStore) -> None:
    """Deleting the root is refused in a sentence, not performed.

    The root is not a box that happens to hold everything: it is where everything else is filed, so
    there is no state to arrive at afterwards. `RootCannotBeFiled` already refuses the other
    direction, and the two need different words.
    """
    from boxganizer.bootstrap import bootstrap
    from boxganizer.errors import RootCannotBeDeleted

    bootstrap(store)
    with pytest.raises(RootCannotBeDeleted, match="nowhere to file anything"):
        store.delete_box("root")
    assert store.get("root").box_type == "root"


def test_a_half_failed_deletion_leaves_the_library_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The row, its links and the indexed text are one decision or none.

    `_execute` commits statement by statement, which is right for a single write and wrong here: a
    box whose row is gone while its indexed text is still offered by search is a library that lies
    about what it holds. So `delete_box` runs its statements on the connection and commits once —
    and this forces the last one to fail to show that nothing half-happened.
    """
    book = tmp_path / "words.md"
    book.write_text("a file two boxes point at", encoding="utf-8")
    with BoxStore(tmp_path / "boxes.db", content_root=tmp_path) as store:
        store.add(Box(name="Kitchen"))
        store.add(Box(name="Passport"))
        store.link("Kitchen", "Passport")
        # The deleted box has to be the last one pointing at a resource, or there is no second
        # statement to fail on — the failure must land after the box row is already gone.
        holder = store.add(Box(name="Holder", resource_uri=str(book)))
        assert holder.resource_id is not None
        real = store._conn

        class _FailingAtTheLastStep:
            """Lets the box row go and refuses to let the resource row follow it."""

            def __init__(self, inner: sqlite3.Connection) -> None:
                self._inner = inner

            def execute(self, sql: str, params: object = ()) -> object:
                if "DELETE FROM resources" in sql:
                    raise sqlite3.OperationalError("the disk stopped")
                return self._inner.execute(sql, params)  # type: ignore[misc]

            def __getattr__(self, name: str) -> object:
                return getattr(self._inner, name)

        monkeypatch.setattr(store, "_conn", _FailingAtTheLastStep(real))
        try:
            with pytest.raises(StoreError, match="write failed"):
                store.delete_box("Holder")
        finally:
            monkeypatch.setattr(store, "_conn", real)

        assert store.get("Holder").name == "Holder", "the row came back"
        assert store.get("Passport").name == "Passport"
        kids = [box.name for box in store.children("Kitchen")]
        assert kids == ["Passport"], "and so did the link"
        assert store.resource(book) is not None, "and so did the resource it was the last box on"


def test_two_threads_adopting_one_file_make_one_row(tmp_path: Path) -> None:
    """One address, one row — even when two passes reach the same file at the same moment.

    `_ensure_resource` asked "is there a row?" and then inserted if not, as two statements that
    each committed on their own. The store is shared across threads on purpose
    (`check_same_thread=False`), and index passes already run in `asyncio.to_thread`, so two
    passes could both be told "no" and the second INSERT then hit the UNIQUE index:
    `write refused: UNIQUE constraint failed: resources.ref_key`, a 500 on whatever request
    happened to be saving a box at that moment.
    """
    tree = tmp_path / "notes"
    tree.mkdir()
    for index in range(40):
        (tree / f"n{index}.md").write_text(f"word{index} searchable prose\n", encoding="utf-8")

    from boxganizer import search as indexing
    from boxganizer.models import Box as _Box

    store = BoxStore(tmp_path / "race.db")
    try:
        store.add(_Box(name="Notes", box_type="directory", resource_uri=str(tree)), index_it=True)
        failures: list[str] = []
        barrier = threading.Barrier(6)

        def pass_worker() -> None:
            barrier.wait()
            try:
                for _ in range(3):
                    indexing.rebuild(store, root=None)
            except Exception as exc:
                failures.append(f"{type(exc).__name__}: {exc}")

        threads = [threading.Thread(target=pass_worker) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert failures == [], failures[:2]
        keys = [row[0] for row in store._rows("SELECT ref_key FROM resources")]
        assert len(keys) == len(set(keys)), "the UNIQUE index keeps the rows straight"
        assert store.content_search(indexing.plan("word7").content_match, limit=5), "words are in"
    finally:
        store.close()


def test_re_expanding_the_same_tree_changes_nothing(tmp_path: Path) -> None:
    """A second walk over an unchanged folder is idempotent: same rows, same ids, no new links."""
    from boxganizer import search as indexing
    from boxganizer.models import Box as _Box

    tree = tmp_path / "again"
    (tree / "a").mkdir(parents=True)
    (tree / "a" / "deep.md").write_text("deep words here\n", encoding="utf-8")
    (tree / "top.md").write_text("top words here\n", encoding="utf-8")

    store = BoxStore(tmp_path / "again.db")
    try:
        store.add(_Box(name="Tree", box_type="directory", resource_uri=str(tree)), index_it=True)
        first = {row[0]: row[1] for row in store._rows("SELECT ref_key, id FROM resources")}
        links = store._rows("SELECT count(*) FROM resource_link")[0][0]
        first_pass = indexing.rebuild(store, root=None)
        second = {row[0]: row[1] for row in store._rows("SELECT ref_key, id FROM resources")}
        again = indexing.rebuild(store, root=None)

        assert first == second, "walking the same tree twice does not mint rows"
        assert store._rows("SELECT count(*) FROM resource_link")[0][0] == links
        # nothing left to do, nothing refused: the walk finished both times
        assert first_pass.skipped == () and again.skipped == (), (first_pass.skipped, again.skipped)
        assert again.indexed == 0 and again.resources == first_pass.resources
        assert store.index_counts()["outstanding"] == 0, store.index_counts()
    finally:
        store.close()


def test_linking_a_resource_that_just_vanished_declines_quietly(tmp_path: Path) -> None:
    """A file another pass decided had disappeared is not a reason to fail this save.

    The same race seen from the other side: one walk finds the file on disk and reaches for a link,
    while a walk in another thread concludes the file is gone and drops its row. The INSERT then
    trips the foreign key. Nothing has been lost — the file is still on disk and the next pass
    adopts it again — so the answer is "no link made this time", not an error blamed on the reader.
    """
    tree = tmp_path / "folder"
    tree.mkdir()
    (tree / "one.md").write_text("one file worth indexing\n", encoding="utf-8")

    store = BoxStore(tmp_path / "vanish.db")
    try:
        store.add(Box(name="Folder", box_type="directory", resource_uri=str(tree)), index_it=True)
        parent = store.resource(str(tree))
        child = store.register_resource(str(tree / "one.md"), index_it=True)
        assert parent is not None and child is not None and child.id is not None
        parent_id, child_id = int(parent.id), int(child.id)

        # A fresh pair links, and linking it again is nothing new rather than an error.
        store._execute("DELETE FROM resource_link WHERE parent_id = ? AND child_id = ?",
                       (parent_id, child_id))
        assert store.link_resource(parent_id, child_id) is True
        assert store.link_resource(parent_id, child_id) is False, "a second link is nothing new"

        # The child row goes away from under the caller, as another thread's sweep would do.
        store._execute("DELETE FROM resource_link WHERE parent_id = ? AND child_id = ?",
                       (parent_id, child_id))
        store._execute("DELETE FROM resources WHERE id = ?", (child_id,))
        assert store.link_resource(parent_id, child_id) is False
        assert store._rows("SELECT 1 FROM resource_link WHERE parent_id = ?", (parent_id,)) == []
    finally:
        store.close()
