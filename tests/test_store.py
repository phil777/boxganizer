"""Store behaviour: CRUD, search, links, cycles, persistence."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from boxganizer.errors import BoxNotFound, CyclicLink, DuplicateBoxName
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


def test_self_link_is_a_cycle(store: BoxStore) -> None:
    store.add(Box(name="A"))
    with pytest.raises(CyclicLink):
        store.link("A", "A")


def test_deep_cycle_is_refused(store: BoxStore) -> None:
    for name in ["A", "B", "C"]:
        store.add(Box(name=name))
    store.link("A", "B")
    store.link("B", "C")
    with pytest.raises(CyclicLink):
        store.link("C", "A")


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
