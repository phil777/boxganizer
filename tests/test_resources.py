"""What a resource reference means, and when a file is worth indexing."""

from __future__ import annotations

import os
from pathlib import Path

from boxganizer import resources as R


def test_the_same_path_written_differently_is_one_resource() -> None:
    assert R.normalise("/tmp/x/../x.txt") == R.normalise("/tmp/x.txt")
    assert R.normalise("file:///tmp/y") == R.normalise("/tmp/y")
    assert R.normalise("~/notes.md") == R.normalise(os.path.expanduser("~/notes.md"))


def test_a_remote_address_is_folded_where_folding_is_safe(tmp_path: Path) -> None:
    # the host has no case, and a slash after it is not a path
    assert R.normalise("HTTPS://Builder.LAN/") == "https://builder.lan"
    # the path of a URL does have case, and its trailing slash means something
    assert R.normalise("https://x/Path?Q=1") == "https://x/Path?Q=1"
    assert R.normalise("https://x/a/") != R.normalise("https://x/a")
    # and who is logging in is part of the identity of an ssh resource
    assert R.normalise("ssh://ci@h") != R.normalise("ssh://other@h")


def test_a_path_is_local_and_a_scheme_is_not() -> None:
    assert R.origin_of("/srv/backups/notes.txt") == R.LOCAL
    assert R.origin_of("https://example.com/page") == R.REMOTE
    assert R.origin_of("mqtt://broker/topic") == R.REMOTE
    assert R.origin_of("ssh://ci@builder.lan") == R.REMOTE


def test_words_are_worth_indexing_and_other_things_are_not(tmp_path: Path) -> None:
    assert R.index_by_default("notes.txt") is True
    assert R.index_by_default("plan.PDF") is True
    assert R.index_by_default("wallpaper.png") is False
    assert R.index_by_default("clip.mp4") is False
    # a remote thing is not a document to read here, whatever it is called
    assert R.index_by_default("https://example.com/notes.txt") is False


def test_a_generated_file_is_local_and_gets_indexed(tmp_path: Path) -> None:
    assert R.origin_of(str(tmp_path / "checklist.md")) == R.LOCAL
    assert R.index_by_default(str(tmp_path / "checklist.md")) is True


def test_a_fingerprint_comes_from_the_file_itself(tmp_path: Path) -> None:
    here = tmp_path / "a.txt"
    here.write_text("hello", encoding="utf-8")
    seen = R.stat(str(here))
    assert seen.bytes == 5
    assert seen.mtime is not None
    # a file that is not there has no fingerprint, which is an observation, not an error
    assert R.stat(str(tmp_path / "gone.txt")).empty is True
    # and a remote thing has nothing to stat here
    assert R.stat("https://example.com/x").empty is True


def test_the_digest_of_the_same_text_twice_is_the_same() -> None:
    assert R.digest("the cellar pump") == R.digest("the cellar pump")
    assert R.digest("a") != R.digest("b")


def _resource(path: object, **kwargs: object) -> R.Resource:
    """As the store builds one: the origin is read off the reference, never guessed."""
    reference = str(path)
    return R.Resource(id=1, reference=reference, origin=R.origin_of(reference), **kwargs)  # type: ignore[arg-type]


def test_staleness_is_asked_of_the_file_not_assumed(tmp_path: Path) -> None:
    live = tmp_path / "live.txt"
    live.write_text("first", encoding="utf-8")
    seen = R.stat(str(live))
    fresh = _resource(live, index_it=True, fingerprint=seen, indexed_at="2026-01-01T00:00:00+00:00")
    assert fresh.stale() is False
    live.write_text("second, and longer", encoding="utf-8")
    assert fresh.stale() is True


def test_a_file_that_vanished_is_stale_because_the_index_lies_about_it(tmp_path: Path) -> None:
    gone = tmp_path / "gone.txt"
    gone.write_text("was here", encoding="utf-8")
    seen = R.stat(str(gone))
    record = _resource(
        gone, index_it=True, fingerprint=seen, indexed_at="2026-01-01T00:00:00+00:00"
    )
    gone.unlink()
    assert record.stale() is True


def test_a_resource_nobody_asks_to_index_is_never_stale(tmp_path: Path) -> None:
    quiet = tmp_path / "quiet.png"
    quiet.write_bytes(b"not words")
    assert _resource(quiet, index_it=False).stale() is None
    assert _resource("https://example.com/page", index_it=True).stale() is None


# -- in the database ---------------------------------------------------------

import sqlite3  # noqa: E402

from boxganizer.models import Box  # noqa: E402
from boxganizer.store import SCHEMA_VERSION, BoxStore  # noqa: E402


def _store(tmp_path: Path) -> BoxStore:
    return BoxStore(tmp_path / "r.db", content_root=tmp_path)


def test_two_boxes_on_one_file_share_one_row(tmp_path: Path) -> None:
    notes = tmp_path / "notes.txt"
    notes.write_text("one file, two boxes", encoding="utf-8")
    store = _store(tmp_path)
    try:
        first = store.add(Box(name="First", box_type="text", resource_uri=str(notes)))
        second = store.add(Box(name="Second", box_type="folder", resource_uri=str(notes)))
        assert first.resource_id == second.resource_id
        connection = sqlite3.connect(tmp_path / "r.db")
        try:
            assert int(connection.execute("SELECT count(*) FROM resources").fetchone()[0]) == 1
        finally:
            connection.close()
    finally:
        store.close()


def test_the_same_file_written_three_ways_is_still_one_row(tmp_path: Path) -> None:
    notes = tmp_path / "notes.txt"
    notes.write_text("spellings", encoding="utf-8")
    store = _store(tmp_path)
    try:
        direct = store.add(Box(name="Direct", box_type="text", resource_uri=str(notes)))
        roundabout = store.add(
            Box(name="Roundabout", box_type="text", resource_uri=str(tmp_path / "." / "notes.txt"))
        )
        as_url = store.add(Box(name="AsUrl", box_type="text", resource_uri=f"file://{notes}"))
        assert direct.resource_id == roundabout.resource_id == as_url.resource_id
    finally:
        store.close()


def test_a_box_keeps_its_address_without_a_query_for_it(tmp_path: Path) -> None:
    """The address is read with the box, because every read of a box joins its resource."""
    notes = tmp_path / "notes.txt"
    notes.write_text("joined", encoding="utf-8")
    store = _store(tmp_path)
    try:
        stored = store.add(Box(name="Notes", box_type="text", resource_uri=str(notes)))
        assert stored.resource_uri == str(notes)
        assert store.get("Notes").resource_uri == str(notes)
        assert [box.resource_uri for box in store.list_boxes()] == [str(notes)]
        assert [box.resource_uri for box in store.children("Notes")] == []
    finally:
        store.close()


def test_deleting_a_box_leaves_a_shared_file_alone(tmp_path: Path) -> None:
    notes = tmp_path / "notes.txt"
    notes.write_text("shared", encoding="utf-8")
    store = _store(tmp_path)
    try:
        first = store.add(Box(name="First", box_type="text", resource_uri=str(notes)))
        store.add(Box(name="Second", box_type="text", resource_uri=str(notes)))
        store.remove("First")
        assert store.resource(int(first.resource_id or 0)) is not None
        assert store.resource(notes) is not None
    finally:
        store.close()


def test_the_last_box_out_is_when_the_resource_row_goes_too(tmp_path: Path) -> None:
    notes = tmp_path / "notes.txt"
    notes.write_text("only one", encoding="utf-8")
    store = _store(tmp_path)
    try:
        only = store.add(Box(name="Only", box_type="text", resource_uri=str(notes)))
        resource_id = int(only.resource_id or 0)
        assert store.resource(resource_id) is not None
        store.remove("Only")
        assert store.resource(resource_id) is None
    finally:
        store.close()


def test_pointing_a_box_at_something_else_releases_the_old_file(tmp_path: Path) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("one", encoding="utf-8")
    second.write_text("two", encoding="utf-8")
    store = _store(tmp_path)
    try:
        box = store.add(Box(name="Mover", box_type="text", resource_uri=str(first)))
        was = int(box.resource_id or 0)
        box.resource_uri = str(second)
        store.update(box)
        assert store.resource(was) is None, "the file it stopped pointing at should be let go"
        assert store.resource(second) is not None
    finally:
        store.close()


def test_a_box_with_no_address_has_no_resource_row(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        box = store.add(Box(name="Plain", box_type="folder"))
        assert box.resource_id is None
        assert box.resource_uri is None
    finally:
        store.close()


def test_the_index_default_follows_what_the_thing_is(tmp_path: Path) -> None:
    words = tmp_path / "words.txt"
    picture = tmp_path / "picture.png"
    words.write_text("words", encoding="utf-8")
    picture.write_bytes(b"\x89PNG\r\n\x1a\n")
    store = _store(tmp_path)
    try:
        store.add(Box(name="Words", box_type="folder", resource_uri=str(words)))
        store.add(Box(name="Picture", box_type="image", resource_uri=str(picture)))
        store.add(Box(name="Page", box_type="web", resource_uri="https://example.com/note.txt"))
        written = store.resource(words)
        image = store.resource(picture)
        assert written is not None and written.index_it is True
        assert image is not None and image.index_it is False
        page = store.resource("https://example.com/note.txt")
        assert page is not None and page.index_it is False
    finally:
        store.close()


def test_the_checkbox_overrules_the_default(tmp_path: Path) -> None:
    picture = tmp_path / "caption.txt"
    picture.write_text("a file nobody asked to read", encoding="utf-8")
    store = _store(tmp_path)
    try:
        store.add(Box(name="Quiet", box_type="folder", resource_uri=str(picture)), index_it=False)
        assert (store.resource(picture) or R.Resource(0, "")).index_it is False
        store.set_index_flag(picture, True)
        after = store.resource(picture)
        assert after is not None and after.index_it is True
        assert after.indexed is False  # opting in is a wish; the reading is the next step
    finally:
        store.close()


# -- the upgrade ------------------------------------------------------------

_V6_SCHEMA = """
CREATE TABLE boxes (
    id INTEGER PRIMARY KEY, uuid TEXT, name TEXT NOT NULL, key TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '', icon TEXT NOT NULL DEFAULT 'x',
    box_type TEXT NOT NULL DEFAULT 'box', resource_uri TEXT,
    metadata TEXT NOT NULL DEFAULT '{}', config TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX boxes_key_idx ON boxes (key);
CREATE TABLE links (
    parent_id INTEGER NOT NULL, child_id INTEGER NOT NULL, added_at TEXT NOT NULL,
    PRIMARY KEY (parent_id, child_id)
);
CREATE VIRTUAL TABLE box_text USING fts5(box_id UNINDEXED, body, tokenize = 'porter unicode61');
PRAGMA user_version = 6;
"""


def test_an_older_library_keeps_its_addresses_and_its_index(tmp_path: Path) -> None:
    """The address moves into `resources` and the indexed words move with it.

    An index that emptied on upgrade would not be a migration but a loss: the words come
    back only by reading every file again, which is exactly what a person with 900 boxes
    on a slow disk did not ask for.
    """
    path = tmp_path / "legacy.db"
    notes = tmp_path / "notes.txt"
    notes.write_text("The cellar pump runs twice a night.", encoding="utf-8")
    connection = sqlite3.connect(path)
    connection.executescript(_V6_SCHEMA)
    connection.execute(
        "INSERT INTO boxes (uuid, name, key, box_type, resource_uri, created_at, updated_at)"
        " VALUES ('u-one', 'Notes', 'notes', 'text', ?, '2026-01-01T00:00:00+00:00',"
        " '2026-01-01T00:00:00+00:00')",
        (str(notes),),
    )
    connection.execute("INSERT INTO box_text (box_id, body) VALUES ('u-one', ?)", ("old words",))
    connection.commit()
    connection.close()

    with BoxStore(path, content_root=tmp_path) as store:
        box = store.get("Notes")
        assert box.resource_uri == str(notes)
        assert box.resource_id is not None
        assert store.indexed_count() == 1
        assert store.content_search('"old words"', limit=5)
        # and it behaves like a new database from here on
        store.add(Box(name="Another", box_type="text", resource_uri=str(notes)))

    again = sqlite3.connect(path)
    try:
        columns = {row[1] for row in again.execute("PRAGMA table_info(boxes)")}
        tables = {row[0] for row in again.execute("SELECT name FROM sqlite_master")}
        version = int(again.execute("PRAGMA user_version").fetchone()[0])
    finally:
        again.close()
    assert "resource_uri" not in columns, "no second copy of an address to disagree with"
    assert "resource_id" in columns
    assert "box_text" not in tables
    assert version == SCHEMA_VERSION


def test_upgrade_can_be_opened_twice(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    connection.executescript(_V6_SCHEMA)
    connection.execute(
        "INSERT INTO boxes (uuid, name, key, box_type, created_at, updated_at)"
        " VALUES ('u', 'Plain', 'plain', 'folder',"
        " '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
    )
    connection.commit()
    connection.close()
    with BoxStore(path):
        pass
    with BoxStore(path) as again:
        assert again.get("Plain").resource_id is None
