"""Store behaviour: CRUD, search, links (many parents, cycles allowed), persistence."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from boxganizer.errors import BoxNotFound, DuplicateBoxName
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
        store.add(Box(name="Sensor", box_type="mqtt", metadata={"topic": "cellar/temp"}))
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
