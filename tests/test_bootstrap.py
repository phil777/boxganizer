"""`box init` leaves exactly two boxes behind: root, and a cabinet inside it."""

from __future__ import annotations

from pathlib import Path

from boxganizer import bootstrap, boxtypes
from boxganizer.models import Box
from boxganizer.store import BoxStore


def test_an_empty_library_gets_root_and_cabinet(db_path: Path) -> None:
    with BoxStore(db_path) as store:
        created = bootstrap.bootstrap(store)

        assert sorted(created) == ["cabinet", "root"]
        assert [box.name for box in store.list_boxes()] == ["cabinet", "root"]
        assert store.count() == 2

        root = store.get("root")
        cabinet = store.get("cabinet")
        assert root.box_type == "root"
        assert cabinet.box_type == "cabinet"
        assert [box.name for box in store.children("root")] == ["cabinet"]
        assert [box.name for box in store.parents("cabinet")] == ["root"]


def test_the_two_boxes_wear_their_type_icons(db_path: Path) -> None:
    with BoxStore(db_path) as store:
        bootstrap.bootstrap(store)
        assert store.get("root").icon == boxtypes.RootBox.icon
        assert store.get("cabinet").icon == boxtypes.Cabinet.icon


def test_seeding_twice_adds_nothing(db_path: Path) -> None:
    with BoxStore(db_path) as store:
        bootstrap.bootstrap(store)
        assert bootstrap.bootstrap(store) == []
        assert store.count() == 2
        assert len(store.children("root")) == 1


def test_a_library_missing_only_the_root_gets_one(db_path: Path) -> None:
    with BoxStore(db_path) as store:
        store.add(Box(name="cabinet", box_type="cabinet"))
        assert bootstrap.bootstrap(store) == ["root"]
        assert store.count() == 2


def test_a_cabinet_the_user_filed_elsewhere_stays_there(db_path: Path) -> None:
    with BoxStore(db_path) as store:
        store.add(Box(name="root", box_type="root"))
        store.add(Box(name="Workshop", box_type="folder"))
        store.add(Box(name="cabinet", box_type="cabinet"))
        store.link("Workshop", "cabinet")

        bootstrap.bootstrap(store)

        assert [box.name for box in store.parents("cabinet")] == ["Workshop"]
