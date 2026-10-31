"""Notes: the file beside the database, the toolbox, and the box named inside a sentence."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from boxganizer import authored, boxrefs
from boxganizer.models import Box
from boxganizer.store import BoxStore
from boxganizer.web.app import create_app

UUID_A = "3f1c1111-2222-3333-4444-555566667777"


# -- the link convention ------------------------------------------------------


def test_a_box_is_named_by_a_link_any_markdown_reader_knows() -> None:
    class Thing:
        uuid = UUID_A
        name = "Copper elbow"
        icon = "🔧"

    link = boxrefs.link_for(Thing())
    assert link == f"[🔧 Copper elbow](boxganizer:{UUID_A})"
    assert boxrefs.mentions(f"asked about {link} yesterday") == [UUID_A]


def test_the_label_is_a_courtesy_and_the_uuid_is_the_reference() -> None:
    assert boxrefs.mentions(f"[whatever](boxganizer:{UUID_A})") == [UUID_A]
    assert boxrefs.labels(f"[ Ruiz & Sons ](boxganizer:{UUID_A})") == {UUID_A: "Ruiz & Sons"}


def test_a_scheme_written_without_a_uuid_behaves_as_words() -> None:
    """A stray `boxganizer:` at the end of a sentence is not a reference to nothing."""
    assert boxrefs.mentions("something about boxganizer: notes") == []
    assert boxrefs.mentions("[a](boxganizer:short)") == []


def test_a_box_named_twice_is_still_one_box() -> None:
    text = f"[one](boxganizer:{UUID_A}) and [again](boxganizer:{UUID_A})"
    assert boxrefs.mentions(text) == [UUID_A]


# -- through the page ---------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> Iterator[BoxStore]:
    box_store = BoxStore(tmp_path / "boxes.db", content_root=tmp_path)
    yield box_store
    box_store.close()


@pytest.fixture
def client(store: BoxStore) -> TestClient:
    return TestClient(create_app(store, content_root=store.path.parent))


def test_a_note_writes_its_file_the_first_time_there_is_something(
    store: BoxStore, client: TestClient
) -> None:
    note = store.add(Box(name="Boiler", box_type="note"))
    assert note.resource_uri is None  # nothing to point at yet
    answer = client.put(f"/api/boxes/{note.uuid}/note", json={"source": "pressure is low"})
    assert answer.status_code == 200
    body = client.get(f"/api/boxes/{note.uuid}/note").json()
    on_disk = Path(body["file"])
    assert on_disk.exists()
    assert on_disk.name == f"{note.uuid}.md"
    assert on_disk.read_text(encoding="utf-8") == "pressure is low"
    # and the box now has a resource, which is what puts the words in the index
    assert store.get("Boiler").resource_uri == str(on_disk)
    record = store.resource(int(store.get("Boiler").resource_id or 0))
    assert record is not None and record.origin == "generated"


def test_the_words_of_a_note_are_found_by_search(store: BoxStore, client: TestClient) -> None:
    note = store.add(Box(name="Boiler", box_type="note"))
    client.put(f"/api/boxes/{note.uuid}/note", json={"source": "the anode needs replacing"})
    hits = client.get("/api/search", params={"q": "anode"}).json()
    assert "Boiler" in [hit["name"] for hit in hits]


def test_a_note_is_reindexed_every_time_it_is_written(store: BoxStore, client: TestClient) -> None:
    note = store.add(Box(name="Boiler", box_type="note"))
    client.put(f"/api/boxes/{note.uuid}/note", json={"source": "anode corroding badly"})
    client.put(f"/api/boxes/{note.uuid}/note", json={"source": "thermocouple replaced"})
    assert "Boiler" not in [
        hit["name"] for hit in client.get("/api/search", params={"q": "corroding"}).json()
    ]
    assert "Boiler" in [
        hit["name"] for hit in client.get("/api/search", params={"q": "thermocouple"}).json()
    ]


def test_a_markdown_note_comes_back_rendered(store: BoxStore, client: TestClient) -> None:
    note = store.add(Box(name="Boiler", box_type="note"))
    client.put(
        f"/api/boxes/{note.uuid}/note",
        json={"source": "# Boiler\n\nThe pressure is **low**.\n", "format": "markdown"},
    )
    body = client.get(f"/api/boxes/{note.uuid}/note").json()
    assert "<h1>Boiler</h1>" in body["html"]
    assert "<strong>low</strong>" in body["html"]
    assert body["format"] == "markdown"


def test_a_plain_note_is_not_rendered(store: BoxStore, client: TestClient) -> None:
    note = store.add(Box(name="Boiler", box_type="note"))
    client.put(f"/api/boxes/{note.uuid}/note", json={"source": "**low**", "format": "text"})
    body = client.get(f"/api/boxes/{note.uuid}/note").json()
    assert body["html"] == ""
    assert body["source"] == "**low**"


def test_the_format_lives_with_the_box_and_not_with_the_file_name(
    store: BoxStore, client: TestClient
) -> None:
    """Flipping a note's format must not move the file: the index knows the address."""
    note = store.add(Box(name="Boiler", box_type="note"))
    first = client.put(f"/api/boxes/{note.uuid}/note", json={"source": "one"})
    client.put(f"/api/boxes/{note.uuid}/note", json={"source": "one", "format": "text"})
    second = client.put(
        f"/api/boxes/{note.uuid}/note", json={"source": "one", "format": "markdown"}
    )
    assert first.json()["file"] == second.json()["file"]
    assert client.get(f"/api/boxes/{note.uuid}/note").json()["format"] == "markdown"


def test_naming_a_box_in_a_note_files_it_there(store: BoxStore, client: TestClient) -> None:
    note = store.add(Box(name="Boiler", box_type="note"))
    part = store.add(Box(name="Copper elbow", box_type="folder"))
    answer = client.put(
        f"/api/boxes/{note.uuid}/note",
        json={"source": f"ask about [🔧 Copper elbow](boxganizer:{part.uuid})"},
    )
    assert answer.json()["attached"] == [part.uuid]
    assert [child.name for child in store.children(note.uuid)] == ["Copper elbow"]
    body = client.get(f"/api/boxes/{note.uuid}/note").json()
    assert body["mentioned"] == [
        {
            "uuid": part.uuid,
            "name": "Copper elbow",
            "icon": body["mentioned"][0]["icon"],
            "box_type": "folder",
            "gone": False,
        }
    ]


def test_the_sentence_can_go_without_the_box(store: BoxStore, client: TestClient) -> None:
    """Deleting the mention is editing prose, not un-filing a box."""
    note = store.add(Box(name="Boiler", box_type="note"))
    part = store.add(Box(name="Copper elbow", box_type="folder"))
    client.put(f"/api/boxes/{note.uuid}/note", json={"source": f"[x](boxganizer:{part.uuid})"})
    client.put(f"/api/boxes/{note.uuid}/note", json={"source": "no mention here"})
    assert [child.name for child in store.children(note.uuid)] == ["Copper elbow"]


def test_a_note_about_a_box_that_is_not_there_says_so(store: BoxStore, client: TestClient) -> None:
    note = store.add(Box(name="Boiler", box_type="note"))
    answer = client.put(
        f"/api/boxes/{note.uuid}/note", json={"source": f"[gone](boxganizer:{UUID_A})"}
    )
    assert answer.json()["unknown"] == [UUID_A]
    body = client.get(f"/api/boxes/{note.uuid}/note").json()
    assert body["mentioned"][0]["gone"] is True


def test_a_note_can_point_at_a_file_that_already_exists(tmp_path: Path) -> None:
    """The box owns its words — unless it was told whose file they are."""
    existing = tmp_path / "minutes.md"
    existing.write_text("# Meeting\n\n- bleed the radiator\n", encoding="utf-8")
    store = BoxStore(tmp_path / "nest" / "boxes.db", content_root=tmp_path)
    try:
        note = store.add(Box(name="Minutes", box_type="note", resource_uri=str(existing)))
        client = TestClient(create_app(store, content_root=tmp_path))
        body = client.get(f"/api/boxes/{note.uuid}/note").json()
        assert body["file"] == str(existing)
        assert "<h1>Meeting</h1>" in body["html"]
        # writing it writes that file, and does not create one beside the database
        client.put(f"/api/boxes/{note.uuid}/note", json={"source": "# Meeting\n\n- done\n"})
        assert "done" in existing.read_text(encoding="utf-8")
        assert not (tmp_path / "nest" / "files" / f"{note.uuid}.md").exists()
    finally:
        store.close()


def test_a_box_that_keeps_no_words_of_its_own_is_refused(
    store: BoxStore, client: TestClient
) -> None:
    folder = store.add(Box(name="Cellar", box_type="folder"))
    answer = client.put(f"/api/boxes/{folder.uuid}/note", json={"source": "hello"})
    assert answer.status_code == 415
    assert answer.json()["detail"]["error"] == "keeps_no_words"


def test_reopening_a_note_does_not_empty_it(store: BoxStore, client: TestClient) -> None:
    note = store.add(Box(name="Boiler", box_type="note"))
    client.put(f"/api/boxes/{note.uuid}/note", json={"source": "a dozen things"})
    for _ in range(3):
        client.get(f"/api/boxes/{note.uuid}/note")
    assert client.get(f"/api/boxes/{note.uuid}/note").json()["source"] == "a dozen things"


def test_reading_a_note_written_by_hand_outside_the_page(
    store: BoxStore, client: TestClient
) -> None:
    """The file is the truth. An edit made in an editor is read back, not overwritten."""
    note = store.add(Box(name="Boiler", box_type="note"))
    client.get(f"/api/boxes/{note.uuid}/note")  # creates the file
    target = authored.path_for(store.path, note.uuid)
    target.write_text("# from my own editor\n", encoding="utf-8")
    body = client.get(f"/api/boxes/{note.uuid}/note").json()
    assert body["source"] == "# from my own editor\n"


def _who_says(store: BoxStore, word: str) -> list[str]:
    """Box names whose indexed words answer for `word` — the page's own path."""
    found: list[str] = []
    for resource_id, _score, _passage in store.content_search(word):
        for box, _inside in store.boxes_for_text(resource_id):
            if box.name not in found:
                found.append(box.name)
    return found


def test_a_saved_note_is_searchable_without_any_index_pass(tmp_path: Path) -> None:
    """The save and the index are one gesture, not two.

    A word typed into a note and saved is a word the person will search for immediately, and
    "search finds it after you press Re-index" would make the index a thing you have to remember
    rather than a property of the library. Removal is the same promise in the other direction: a
    word taken out of the text must stop answering to a search, or the index quietly keeps
    sentences the note no longer says.
    """
    store = BoxStore(tmp_path / "immediate.db", content_root=tmp_path)
    try:
        note = store.add(Box(name="Memo", box_type="note"))
        with TestClient(create_app(store, content_root=tmp_path)) as client:
            client.put(
                f"/api/boxes/{note.uuid}/note",
                json={"source": "quagginess valve", "format": "text"},
            )
            assert _who_says(store, "quagginess") == ["Memo"]
            assert store.index_counts()["indexed"] == 1, "indexed by the save, not by a later pass"

            client.put(
                f"/api/boxes/{note.uuid}/note",
                json={"source": "nothing of the sort", "format": "text"},
            )
            assert _who_says(store, "quagginess") == [], "the index kept a deleted sentence"
            client.put(
                f"/api/boxes/{note.uuid}/note",
                json={"source": "a grommet fitting", "format": "text"},
            )
            assert _who_says(store, "grommet") == ["Memo"]
    finally:
        store.close()
