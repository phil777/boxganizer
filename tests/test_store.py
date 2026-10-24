"""Store behaviour: CRUD, search, links, cycles, persistence."""

from __future__ import annotations

from pathlib import Path

import pytest

from boxganizer.errors import BoxNotFound, CyclicLink, DuplicateBoxName
from boxganizer.models import Box
from boxganizer.store import BoxStore


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
