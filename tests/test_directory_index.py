"""Indexing a folder: the files inside become resources, and the folder answers for them.

See ADR 16. What is being held to here is that a folder's `index_it` means "bring in what is
inside", that the ordinary change-detection works on those files unchanged, and that nothing
the folder brought in outlives the reason it was there.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from boxganizer import search as text_search
from boxganizer.models import Box
from boxganizer.store import BoxStore
from boxganizer.web.app import create_app

TEXT = ("# Notes\n\nthe boiler pressure gauge leaks\n", "an order for cement mixer parts\n")


@pytest.fixture
def library(tmp_path: Path) -> Iterator[BoxStore]:
    """A folder of notes, nested, with the things a walk must not read inside it.

    `notes/.git/`, `notes/escape.md` (a link to a file outside the folder), `notes/photo.png`
    and the `outside.md` it points at are all there to be *absent* from the index.
    """
    root = tmp_path / "library"
    notes = root / "notes"
    (notes / "deep" / "deeper").mkdir(parents=True)
    (notes / ".git").mkdir()
    (root / "elsewhere").mkdir()

    (notes / "a.md").write_text(TEXT[0], encoding="utf-8")
    (notes / "deep" / "b.txt").write_text(TEXT[1], encoding="utf-8")
    (notes / "deep" / "deeper" / "c.md").write_text(
        "the anode needs replacing soon\n", encoding="utf-8"
    )
    (notes / ".git" / "config.txt").write_text("git internals about the anode\n", encoding="utf-8")
    (notes / "photo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 40)
    outside = root / "outside.md"
    outside.write_text("a file outside the folder, about the anode\n", encoding="utf-8")
    (notes / "escape.md").symlink_to(outside)

    store = BoxStore(tmp_path / "dirs.db", content_root=root)
    yield store
    store.close()


def folder_box(store: BoxStore, name: str = "Notes", path: str = "notes") -> Box:
    where = store_root(store) / path
    box = store.add(Box(name=name, box_type="indexed-directory", resource_uri=str(where)))
    store.set_index_flag(box.resource_id, True)
    return box


def store_root(store: BoxStore) -> Path:
    return Path(store.content_root)  # type: ignore[arg-type]


def names(store: BoxStore, query: str) -> list[str]:
    """Which boxes answer for this word, through their own files or their folders'."""
    answers: list[str] = []
    for resource_id, _score, _passage in store.content_search(query, limit=20):
        for box, _inside in store.boxes_for_text(resource_id):
            if box.name not in answers:
                answers.append(box.name)
    return answers


# -- what a walk brings in ----------------------------------------------------


def test_a_folder_brought_into_the_index_brings_its_files(library: BoxStore) -> None:
    box = folder_box(library)
    report = text_search.rebuild(library)
    assert report.in_index == 3  # a.md, deep/b.txt, deeper/c.md — and not the folder
    parent = library.resource(int(box.resource_id))
    assert parent is not None
    inside = sorted(
        child.reference.replace(f"{store_root(library)}/notes/", "")
        for child in library.linked_resources(int(parent.id))
    )
    assert inside == ["a.md", "deep/b.txt", "deep/deeper/c.md"]
    assert names(library, "cement") == ["Notes"]


def test_the_folder_itself_is_not_counted_as_an_unreadable_file(library: BoxStore) -> None:
    """It has no words because it is a folder, which is a different thing from a failure."""
    folder_box(library)
    text_search.rebuild(library)
    counts = library.index_counts()
    assert counts["folders"] == 1
    assert counts["unreadable"] == 0
    assert counts["indexed"] == 3


def test_what_a_walk_must_leave_out_is_left_out(library: BoxStore) -> None:
    folder_box(library)
    text_search.rebuild(library)
    references = [item.reference for item in library.indexed_resources()]
    assert not any(".git" in ref for ref in references), "version control metadata is not a note"
    assert not any("outside.md" in ref for ref in references), (
        "a link out of the folder indexes nothing"
    )
    assert not any(ref.endswith(".png") for ref in references), "no words come out of a picture"
    assert names(library, "anode") == ["Notes"]  # the real nested note, not the .git one


def test_a_file_the_person_asked_to_be_left_alone_is_not_brought_in(library: BoxStore) -> None:
    """The walk reads the same rule everyone else does: `index_it` means what it says."""
    notes = store_root(library) / "notes"
    (notes / "private.md").write_text("my medical results about the anode\n", encoding="utf-8")
    box = folder_box(library)
    text_search.rebuild(library)
    parent = library.resource(int(box.resource_id))
    assert parent is not None
    library.set_index_flag(str(notes / "private.md"), False)
    # an explicit no survives the next walk: the folder does not quietly re-opt it in
    text_search.rebuild(library, only_stale=False)
    private = library.resource(str(notes / "private.md"))
    assert private is not None and private.index_it is False


# -- what a walk notices later ------------------------------------------------


def test_a_new_file_two_levels_down_is_found_by_a_pass(library: BoxStore) -> None:
    """A nested addition does not touch the top folder's mtime, so a walk is what finds it."""
    folder_box(library)
    text_search.rebuild(library)
    assert names(library, "flush") == []
    (store_root(library) / "notes" / "deep" / "deeper" / "d.md").write_text(
        "a fresh note about the flush valve\n", encoding="utf-8"
    )
    library.check_observations()
    text_search.rebuild(library, only_stale=True)
    assert names(library, "flush") == ["Notes"]


def test_a_deleted_file_stops_answering_searches(library: BoxStore) -> None:
    """Words from a file that is gone must not go on being offered."""
    folder_box(library)
    text_search.rebuild(library)
    assert names(library, "anode") == ["Notes"]
    (store_root(library) / "notes" / "deep" / "deeper" / "c.md").unlink()
    library.check_observations()
    text_search.rebuild(library, only_stale=True)
    assert names(library, "anode") == []


def test_a_file_inside_two_folders_survives_one_of_them_being_unindexed(library: BoxStore) -> None:
    """Folders inside folders: one file, two links, one copy of its words.

    `notes` and `notes/deep` are both indexed, so `deep/b.txt` was found by each of them and
    is one resource with two links. Unindexing the outer folder must keep the file and its
    text, because the inner one still holds it up — and unindexing both must let it go.
    """
    outer = folder_box(library, name="Notes")
    inner = folder_box(library, name="Deep", path="notes/deep")
    text_search.rebuild(library)
    shared = store_root(library) / "notes" / "deep" / "b.txt"
    assert names(library, "cement") == ["Notes", "Deep"] or names(library, "cement") == [
        "Deep",
        "Notes",
    ]

    library.set_index_flag(int(outer.resource_id or 0), False)
    text_search.rebuild(library)
    kept = library.resource(str(shared))
    assert kept is not None and kept.index_it is True
    assert names(library, "cement") == ["Deep"]

    library.set_index_flag(int(inner.resource_id or 0), False)
    text_search.rebuild(library)
    assert library.resource(str(shared)) is None
    assert names(library, "cement") == []


def test_opting_a_folder_out_lets_go_the_files_only_it_brought_in(library: BoxStore) -> None:
    box = folder_box(library)
    text_search.rebuild(library)
    parent = library.resource(int(box.resource_id))
    assert parent is not None
    child = library.linked_resources(int(parent.id))[0]

    library.set_index_flag(int(parent.id), False)
    text_search.rebuild(library)

    assert library.linked_resources(int(parent.id)) == []
    # nothing points at that file and no folder claims it now, so its row is gone with them
    assert library.resource(int(child.id)) is None


def test_an_ordinary_box_pointing_at_a_file_keeps_it_when_the_folder_lets_go(
    library: BoxStore,
) -> None:
    """A file the library also holds directly is not the folder's to discard."""
    box = folder_box(library)
    target = store_root(library) / "notes" / "a.md"
    library.add(Box(name="The gauge note", box_type="markdown", resource_uri=str(target)))
    text_search.rebuild(library)
    parent = library.resource(int(box.resource_id))
    assert parent is not None
    library.set_index_flag(int(parent.id), False)
    text_search.rebuild(library)
    assert library.resource(str(target)) is not None
    assert names(library, "gauge") == ["The gauge note"]


def test_a_walk_that_reaches_its_limit_so_says(tmp_path: Path) -> None:
    from boxganizer import resources as address

    root = tmp_path / "many"
    root.mkdir()
    for index in range(12):
        (root / f"n{index}.md").write_text(f"note {index}\n", encoding="utf-8")
    files, note = address.walk_files(root, confine=tmp_path, limit=5)
    assert len(files) == 5
    assert "stopped after 5 files" in note


def test_the_everyday_door_does_not_walk_an_unchanged_folder(library: BoxStore) -> None:
    """Saving a box must not relist a big tree; the deliberate pass does.

    Discovery is the price, and it is paid at the one moment a person asks for an
    indexation — which is why the panel's action says what it runs.
    """
    box = folder_box(library)
    text_search.rebuild(library)  # walks
    parent = library.resource(int(box.resource_id))
    assert parent is not None
    (store_root(library) / "notes" / "deep" / "deeper" / "c.md").unlink()
    # touch nothing else: the folder's own mtime is what the cheap door looks at
    note = text_search.sync_resource(library, int(parent.id), root=store_root(library))
    assert note == ""
    assert len(library.linked_resources(int(parent.id))) == 3  # nothing re-walked


# -- the answers --------------------------------------------------------------


def test_the_folder_answers_for_its_files_and_names_the_file(library: BoxStore) -> None:
    folder_box(library)
    text_search.rebuild(library)
    hits = library.content_search("cement", limit=5)
    assert hits
    resource_id, _score, _passage = hits[0]
    answers = library.boxes_for_text(resource_id)
    assert [(box.name, inside) for box, inside in answers] == [("Notes", "deep/b.txt")]


def test_a_box_that_points_at_a_file_answers_without_a_file_name(library: BoxStore) -> None:
    target = store_root(library) / "notes" / "a.md"
    library.add(Box(name="Gauge", box_type="markdown", resource_uri=str(target)))
    text_search.rebuild(library)
    hits = library.content_search("gauge", limit=5)
    resource_id, _score, _passage = hits[0]
    assert [(box.name, inside) for box, inside in library.boxes_for_text(resource_id)] == [
        ("Gauge", None)
    ]


def test_the_page_reports_which_file_in_the_folder_answered(library: BoxStore) -> None:
    folder_box(library)
    with TestClient(create_app(library, content_root=store_root(library))) as client:
        text_search.rebuild(library)
        hits = client.get("/api/search", params={"q": "cement"}).json()
        assert [hit["name"] for hit in hits] == ["Notes"]
        assert hits[0]["match"]["how"] == "content"
        assert hits[0]["match"]["in_file"] == "deep/b.txt"
        status = client.get("/api/index/status", params={"check": True}).json()
        assert status["folders"] == 1
        assert status["unreadable"] == 0


def test_a_folder_reports_the_files_it_holds_when_asked_to_be_indexed(
    library: BoxStore,
) -> None:
    """The dialog should not promise words from a folder it cannot read."""
    with TestClient(create_app(library, content_root=store_root(library))) as client:
        answer = client.get(
            "/api/index-suggestion",
            params={"resource": str(store_root(library) / "notes"), "box_type": "directory"},
        ).json()
        assert answer["index_it"] is True
        assert "folder" in answer["reason"].lower() and "inside" in answer["reason"].lower()
        assert answer["shared"] is False
