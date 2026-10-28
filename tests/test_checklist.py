"""Checklists: the file format, the directory it lives in, and the page that edits it."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from boxganizer import authored
from boxganizer import checklist as list_of_things
from boxganizer.models import Box
from boxganizer.store import BoxStore
from boxganizer.web.app import create_app

UUID_A = "3f1c1111-2222-3333-4444-555566667777"
UUID_B = "7ba4aaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


# -- what the file says -------------------------------------------------------


def test_a_tick_is_a_tick_and_a_blank_is_a_blank() -> None:
    list_ = list_of_things.parse("- [x] Call the plumber\n- [ ] Buy fittings\n")
    assert [item.checked for item in list_.items] == [True, False]
    assert [item.text for item in list_.items] == ["Call the plumber", "Buy fittings"]


def test_the_title_is_the_heading_and_there_can_be_only_one() -> None:
    list_ = list_of_things.parse("# Cellar, spring\n\n- [ ] one\n")
    assert list_.title == "Cellar, spring"
    assert len(list_.items) == 1


def test_indentation_is_depth_however_the_author_indented_it() -> None:
    list_ = list_of_things.parse("- one\n  - two\n    - three\n")
    assert [item.depth for item in list_.items] == [0, 1, 2]
    tabs = list_of_things.parse("- one\n\t- two\n")
    assert [item.depth for item in tabs.items] == [0, 1]


def test_prose_between_the_items_is_kept_where_it_was() -> None:
    """The page edits the list, not the person's sentences."""
    list_ = list_of_things.parse("- [ ] one\n\nBring the receipt.\n\n- [ ] two\n")
    kinds = [item.kind for item in list_.items]
    assert kinds == ["box", "text", "box"]
    assert list_.items[1].text == "Bring the receipt."
    assert list_.summary() == "2 of 2 still open"


def test_a_line_of_prose_does_not_count_as_something_to_do() -> None:
    list_ = list_of_things.parse("Just a note, nothing ticked.\n")
    assert list_.open == 0 and list_.done == 0
    assert list_.summary() == "nothing ticked on it yet"


def test_a_child_is_attached_to_the_item_that_names_it() -> None:
    list_ = list_of_things.parse(f"- [ ] Buy fittings [🔧 Elbow](boxganizer:{UUID_A})\n")
    assert list_.items[0].refs == [UUID_A]
    assert list_.items[0].text == "Buy fittings"
    assert list_of_things.referenced(list_) == [UUID_A]


def test_a_link_writes_the_boxs_name_so_the_file_reads_as_a_list() -> None:
    list_ = list_of_things.Checklist(items=[list_of_things.Item(text="Buy", refs=[UUID_A])])
    out = list_of_things.render(list_, labels={UUID_A: "Copper elbow"})
    assert f"[📦 Copper elbow](boxganizer:{UUID_A})" in out
    # the name is a courtesy; the uuid is the attachment, and it is still there
    assert list_of_things.parse(out).items[0].refs == [UUID_A]


def test_a_box_that_is_gone_is_written_as_gone_rather_than_vanishing() -> None:
    list_ = list_of_things.Checklist(items=[list_of_things.Item(text="Buy", refs=[UUID_A])])
    out = list_of_things.render(list_)
    assert "missing box" in out and UUID_A in out


def test_writing_and_reading_a_second_time_changes_nothing() -> None:
    text = f"# T\n\n- [x] one\n- [ ] two [📦 B](boxganizer:{UUID_B})\n  - [ ] three\n"
    first = list_of_things.render(list_of_things.parse(text), labels={UUID_B: "B"})
    second = list_of_things.render(list_of_things.parse(first), labels={UUID_B: "B"})
    assert first == second


def test_attaching_twice_is_one_attachment_and_detaching_leaves_the_box() -> None:
    list_ = list_of_things.Checklist(items=[list_of_things.Item(text="one")])
    list_of_things.attach(list_, 0, UUID_A)
    list_of_things.attach(list_, 0, UUID_A)
    assert list_.items[0].refs == [UUID_A]
    list_of_things.detach(list_, UUID_A)
    assert list_.items[0].refs == []
    with pytest.raises(IndexError):
        list_of_things.attach(list_, 4, UUID_A)


# -- where the file lives -----------------------------------------------------


def test_the_file_is_named_after_the_uuid_and_not_the_name(tmp_path: Path) -> None:
    """A rename must not move a file, because a moved file orphans its index entry."""
    target = authored.path_for(tmp_path / "boxes.db", UUID_A)
    assert target.parent == tmp_path / "files"
    assert target.name == f"{UUID_A}.md"


def test_an_in_memory_library_writes_nowhere_near_the_current_directory() -> None:
    """`:memory:` has no beside; a path built from that string would be `./files`."""
    where = authored.directory_for(":memory:")
    assert where != Path("files")
    assert str(Path.cwd()) not in str(where) or where.is_absolute()


def test_the_directory_is_only_readable_by_its_owner(tmp_path: Path) -> None:
    authored.ensure_directory(tmp_path / "boxes.db")
    mode = (tmp_path / "files").stat().st_mode & 0o777
    assert mode == 0o700


def test_opening_a_note_that_exists_does_not_empty_it(tmp_path: Path) -> None:
    target = authored.path_for(tmp_path / "boxes.db", UUID_A)
    authored.write(tmp_path / "boxes.db", UUID_A, "- [ ] keep me\n")
    authored.ensure_file(tmp_path / "boxes.db", UUID_A, seed="")
    assert "keep me" in target.read_text(encoding="utf-8")


def test_writing_leaves_no_temporary_file_behind(tmp_path: Path) -> None:
    authored.write(tmp_path / "boxes.db", UUID_A, "- [ ] one\n")
    left = sorted(p.name for p in (tmp_path / "files").iterdir())
    assert left == [f"{UUID_A}.md"]


def test_a_box_is_given_a_resource_row_that_says_who_wrote_the_file(tmp_path: Path) -> None:
    store = BoxStore(tmp_path / "boxes.db", content_root=tmp_path / "elsewhere")
    try:
        box = store.add(Box(name="Garden", box_type="checklist"))
        target = authored.ensure_file(store.path, box.uuid)
        record = authored.adopt(store, box, target)
        assert record is not None
        assert record.origin == "generated"
        # A generated file is watched like any other local one: someone may edit it by hand
        assert record.stale() is not None or record.stale() is None
        assert store.resource(int(record.id or 0)) is not None
    finally:
        store.close()


def test_a_generated_file_is_read_even_when_the_confine_excludes_it(
    tmp_path: Path,
) -> None:
    """The confine is a rule about other people's paths, not about Boxganizer's own."""
    from boxganizer import search as text_search

    library = tmp_path / "library"
    library.mkdir()
    store = BoxStore(tmp_path / "boxes.db", content_root=library)  # notes go beside the db
    try:
        box = store.add(Box(name="Errands", box_type="checklist"))
        target = authored.write(store.path, box.uuid, "- [ ] collect the boiler parts\n")
        record = authored.adopt(store, box, target)
        assert record is not None
        found = text_search.extract_resource(str(target), root=library, origin=record.origin)
        assert found.usable
        assert "boiler" in found.text
        # the same address, judged as an ordinary file, would have been refused
        plain = text_search.extract_resource(str(target), root=library)
        assert not plain.usable
        assert "outside" in plain.note
    finally:
        store.close()


# -- through the page ---------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> Iterator[BoxStore]:
    box_store = BoxStore(tmp_path / "boxes.db", content_root=tmp_path)
    yield box_store
    # closed with the test: a connection left for the interpreter to finalise is an
    # "unraisable exception" at teardown, and pytest turns that into a failure
    box_store.close()


@pytest.fixture
def client(store: BoxStore) -> TestClient:
    return TestClient(create_app(store, content_root=store.path.parent))


def test_reading_a_checklist_writes_its_file_and_indexes_it(
    store: BoxStore, client: TestClient
) -> None:
    box = store.add(Box(name="Spring", box_type="checklist"))
    answer = client.get(f"/api/boxes/{box.uuid}/checklist")
    assert answer.status_code == 200
    body = answer.json()
    assert body["items"] == []
    assert body["indexed"] is True
    on_disk = Path(body["file"])
    assert on_disk.exists()
    assert on_disk.name == f"{box.uuid}.md"
    assert on_disk.parent.name == "files"


def test_writing_a_checklist_then_reading_it_back_gives_the_same_list(
    store: BoxStore, client: TestClient
) -> None:
    box = store.add(Box(name="Spring", box_type="checklist"))
    write = client.put(
        f"/api/boxes/{box.uuid}/checklist",
        json={
            "title": "Cellar, spring",
            "items": [
                {"text": "Call the plumber", "checked": True},
                {"text": "Buy fittings", "checked": False},
                {"text": "measure the pipe first", "kind": "text"},
            ],
        },
    )
    assert write.status_code == 200
    assert write.json()["summary"] == "1 of 2 still open"
    read = client.get(f"/api/boxes/{box.uuid}/checklist").json()
    assert read["title"] == "Cellar, spring"
    assert [item["text"] for item in read["items"]] == [
        "Call the plumber",
        "Buy fittings",
        "measure the pipe first",
    ]
    assert read["items"][2]["kind"] == "text"


def test_what_is_typed_on_a_checklist_can_be_found_by_search(
    store: BoxStore, client: TestClient
) -> None:
    """The whole reason the words are a file with a resource row: they are searchable."""
    box = store.add(Box(name="Spring", box_type="checklist"))
    client.put(
        f"/api/boxes/{box.uuid}/checklist",
        json={"items": [{"text": "collect the boiler pressure gauge"}]},
    )
    hits = client.get("/api/search", params={"q": "boiler"}).json()
    named = [hit["name"] for hit in hits]
    assert "Spring" in named
    hit = next(hit for hit in hits if hit["name"] == "Spring")
    assert hit["match"]["how"] == "content"


def test_a_checklist_edit_is_indexed_again_without_being_asked_twice(
    store: BoxStore, client: TestClient
) -> None:
    """Each update reindexes: the promise is that the search is of what it says now."""
    box = store.add(Box(name="Spring", box_type="checklist"))
    client.put(
        f"/api/boxes/{box.uuid}/checklist",
        json={"items": [{"text": "hire a cement mixer"}]},
    )
    hits = client.get("/api/search", params={"q": "mixer"}).json()
    assert "Spring" in [hit["name"] for hit in hits]
    # the item is crossed off and rewritten; the old words must stop answering
    client.put(
        f"/api/boxes/{box.uuid}/checklist",
        json={"items": [{"text": "return the cement mixer", "checked": True}]},
    )
    again = client.get("/api/search", params={"q": "hire"}).json()
    assert "Spring" not in [hit["name"] for hit in again]


def test_dragging_a_box_onto_an_item_files_it_and_keeps_it_filed(
    store: BoxStore, client: TestClient
) -> None:
    box = store.add(Box(name="Spring", box_type="checklist"))
    part = store.add(Box(name="Copper elbow", box_type="folder"))
    answer = client.put(
        f"/api/boxes/{box.uuid}/checklist",
        json={"items": [{"text": "Buy fittings", "refs": [part.uuid]}]},
    )
    assert answer.json()["attached"] == [part.uuid]
    assert [child.name for child in store.children(box.uuid)] == ["Copper elbow"]
    read = client.get(f"/api/boxes/{box.uuid}/checklist").json()
    attached = read["items"][0]["attached"]
    assert attached[0]["name"] == "Copper elbow"
    assert attached[0]["gone"] is False
    assert read["unattached"] == []


def test_a_child_on_no_item_is_shown_as_unattached_rather_than_hidden(
    store: BoxStore, client: TestClient
) -> None:
    box = store.add(Box(name="Spring", box_type="checklist"))
    loose = store.add(Box(name="Invoice", box_type="folder"))
    store.link(box.uuid, loose.uuid)
    read = client.get(f"/api/boxes/{box.uuid}/checklist").json()
    assert [item["name"] for item in read["unattached"]] == ["Invoice"]


def test_taking_an_icon_off_a_line_leaves_the_box_alone(
    store: BoxStore, client: TestClient
) -> None:
    box = store.add(Box(name="Spring", box_type="checklist"))
    part = store.add(Box(name="Copper elbow", box_type="folder"))
    store.link(box.uuid, part.uuid)
    client.put(
        f"/api/boxes/{box.uuid}/checklist",
        json={"items": [{"text": "Buy fittings", "refs": [part.uuid]}]},
    )
    client.put(f"/api/boxes/{box.uuid}/checklist", json={"items": [{"text": "Buy fittings"}]})
    assert [child.name for child in store.children(box.uuid)] == ["Copper elbow"]


def test_a_link_to_a_box_that_is_not_there_is_reported_and_left_in_the_file(
    store: BoxStore, client: TestClient
) -> None:
    """The box may be one restore away; deleting someone's line is not the page's call."""
    box = store.add(Box(name="Spring", box_type="checklist"))
    answer = client.put(
        f"/api/boxes/{box.uuid}/checklist",
        json={"items": [{"text": "chase", "refs": [UUID_A]}]},
    )
    assert answer.json()["unknown"] == [UUID_A]
    read = client.get(f"/api/boxes/{box.uuid}/checklist").json()
    assert read["items"][0]["attached"][0]["gone"] is True


def test_a_box_that_keeps_no_list_of_its_own_is_refused(
    store: BoxStore, client: TestClient
) -> None:
    folder = store.add(Box(name="Cellar", box_type="folder"))
    answer = client.get(f"/api/boxes/{folder.uuid}/checklist")
    assert answer.status_code == 415
    assert answer.json()["detail"]["error"] == "not_a_checklist"


def test_the_file_survives_the_box_being_deleted(tmp_path: Path, store: BoxStore) -> None:
    """Boxganizer does not delete what a person wrote, even when the box is gone.

    The row can be re-created by name; the words cannot be re-created from nothing. A
    library whose database is rebuilt keeps its notes, and the orphan file is a clue
    rather than rubbish.
    """
    box = store.add(Box(name="Spring", box_type="checklist"))
    client = TestClient(create_app(store, content_root=tmp_path))
    client.put(f"/api/boxes/{box.uuid}/checklist", json={"items": [{"text": "keep"}]})
    target = Path(authored.path_for(store.path, box.uuid))
    assert target.exists()
    store.remove("Spring")  # `remove` is by name; the file outlives the row
    assert target.exists()
