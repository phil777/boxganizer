"""Tests for capturing a resource: content-addressed copies beside the database."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from boxganizer import capture


@pytest.fixture
def library(tmp_path: Path) -> Path:
    """The `files/` directory of a library whose database sits in *tmp_path*."""
    return capture.files_dir_for(tmp_path / "boxes.db")


def write(path: Path, body: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return path


def stage(tmp_path: Path, name: str, body: bytes) -> str:
    """A file with these bytes in a scratch folder of its own, as a resource address."""
    return str(write(tmp_path / "in" / name, body))


def test_captured_bytes_land_under_their_own_hash_buckets(tmp_path: Path, library: Path) -> None:
    """files/<hex[0:2]>/<hex[2:4]>/<sha256><extension>."""
    source = write(tmp_path / "in" / "ledger.pdf", b"page one of the ledger")
    digest = hashlib.sha256(b"page one of the ledger").hexdigest()

    result = capture.capture(str(source), files_dir=library)

    assert result.digest == digest
    assert result.reused is False
    assert result.size == len(b"page one of the ledger")
    assert result.path == library / digest[:2] / digest[2:4] / f"{digest}.pdf"
    assert result.path.read_bytes() == b"page one of the ledger"
    # the original is left exactly where it was: capture copies, it does not move
    assert source.exists()


def test_the_same_bytes_captured_twice_are_one_file(tmp_path: Path, library: Path) -> None:
    """Deduplication is the filename: two boxes, one copy, and the second capture writes nothing."""
    body = b"identical content, twice"
    first = capture.capture(str(write(tmp_path / "a" / "one.md", body)), files_dir=library)
    second = capture.capture(stage(tmp_path, "different-name.md", body), files_dir=library)

    assert first.path == second.path
    assert second.reused is True
    copies = sorted(p.name for p in library.rglob("*") if p.is_file())
    assert copies == [first.path.name]


def test_the_extension_is_kept_and_cannot_smuggle_a_path(tmp_path: Path, library: Path) -> None:
    """Type inference and MIME read the suffix, so it stays — but only a suffix ever."""
    assert capture.suffix_of(str(tmp_path / "report.TABULAR")) == ".TABULAR"
    assert capture.suffix_of(str(tmp_path / "no-extension")) == ""
    assert capture.suffix_of("/tmp/evil.tar.gz") == ".gz"
    assert capture.suffix_of("file:///tmp/x/%2e%2e/sh") == ""
    assert capture.suffix_of("/tmp/weird." + "A" * 40) == "", "an extension is not a payload"

    png = write(tmp_path / "in" / "photo.png", b"\x89PNG")
    captured = capture.capture(str(png), files_dir=library)
    assert captured.path.suffix == ".png"


def test_a_captured_file_certifies_itself_and_a_hand_edit_shows(
    library: Path, tmp_path: Path
) -> None:
    """The name is a promise; `verify` checks it, so nothing trusts a file it is about to move."""
    result = capture.capture(stage(tmp_path, "note.txt", b"the real words"), files_dir=library)
    assert capture.is_captured(result.path, files_dir=library)
    assert capture.verify(result.path)

    result.path.write_bytes(b"somebody replaced this")
    assert capture.is_captured(result.path, files_dir=library), "the shape is still the shape"
    assert not capture.verify(result.path), "but the name no longer tells the truth"


def test_shape_recognition_refuses_everything_that_is_not_a_capture(
    tmp_path: Path, library: Path
) -> None:
    """A note file, a neighbour directory and a look-alike all answer "not captured"."""
    digest = "a" * 64
    lookalike = library / "aa" / "aa" / f"{digest}.md"
    lookalike.parent.mkdir(parents=True, exist_ok=True)
    lookalike.write_bytes(b"x")

    assert capture.is_captured(lookalike, files_dir=library)
    # Box's own words file: inside `files`, but not bucketed and named by uuid
    a_note = library / "5f4dcc3b-0000-0000-0000-000000000000.md"
    assert not capture.is_captured(a_note, files_dir=library)
    # one bucket level instead of two
    assert not capture.is_captured(library / "ab" / "short.txt", files_dir=library)
    # a short digest is not a digest
    assert not capture.is_captured(library / "ab" / "cd" / "beef.md", files_dir=library)
    # and something entirely elsewhere
    assert not capture.is_captured(tmp_path / "outside.txt", files_dir=library)


def test_a_folder_is_not_captured_and_says_why(tmp_path: Path, library: Path) -> None:
    folder = tmp_path / "a-folder"
    folder.mkdir()
    with pytest.raises(capture.CaptureError, match="is a folder"):
        capture.capture(str(folder), files_dir=library)


def test_a_remote_address_is_refused_rather_than_attempted(tmp_path: Path, library: Path) -> None:
    with pytest.raises(capture.CaptureError, match="not a local file"):
        capture.capture("ssh://host/etc/hosts", files_dir=library)


def test_a_missing_file_is_refused(tmp_path: Path, library: Path) -> None:
    with pytest.raises(capture.CaptureError, match="nothing at"):
        capture.capture(str(tmp_path / "gone.txt"), files_dir=library)
    with pytest.raises(capture.CaptureError, match="points at nothing"):
        capture.capture("   ", files_dir=library)


def test_a_hand_replaced_target_is_never_written_over(tmp_path: Path, library: Path) -> None:
    """Two different byte-strings cannot share a name, but a hand-edited file can wear one.

    Refusing is the only honest answer: overwriting destroys somebody's edit, and renaming ours
    hides that the store was tampered with.
    """
    body = b"original"
    result = capture.capture(str(write(tmp_path / "keep" / "one.txt", body)), files_dir=library)
    result.path.write_bytes(b"replaced by hand")

    # Re-capturing the same content lands on the same name — which is the tampered file. Different
    # bytes would hash elsewhere and never touch it, so this is the only way the collision happens,
    # and it is the one a person meets after editing the library's own copy.
    again = write(tmp_path / "keep" / "two.txt", body)
    with pytest.raises(capture.CaptureError, match="changed by hand"):
        capture.capture(str(again), files_dir=library)
    assert result.path.read_bytes() == b"replaced by hand", "the edit survives the refusal"

    # and content that hashes somewhere else is untouched by the tampering
    fresh = capture.capture(stage(tmp_path, "three.txt", b"other words"), files_dir=library)
    assert fresh.path != result.path
    assert fresh.reused is False


def test_an_empty_file_is_captured_like_any_other(tmp_path: Path, library: Path) -> None:
    """Zero bytes hash to a real digest; refusing them would be a rule nobody asked for."""
    empty = write(tmp_path / "e" / "empty.txt", b"")
    result = capture.capture(str(empty), files_dir=library)
    assert result.size == 0
    assert result.digest == hashlib.sha256(b"").hexdigest()
    assert capture.verify(result.path)


def test_a_file_uri_captures_the_same_bytes_as_a_plain_path(tmp_path: Path, library: Path) -> None:
    """`file:///x` and `/x` are one address, so they must land on one captured file."""
    body = b"same either way"
    source = write(tmp_path / "u" / "same.bin", body)
    by_path = capture.capture(str(source), files_dir=library)
    by_uri = capture.capture(source.as_uri(), files_dir=library)
    assert by_path.path == by_uri.path
    assert by_uri.reused is True


def test_a_failed_copy_leaves_no_file_lying_about_its_name(tmp_path: Path, library: Path) -> None:
    """The temp-then-move is the point: a partial file named by a hash would be a file that lies."""
    source = tmp_path / "broken" / "read-me.txt"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"fine on disk")

    real_copyfileobj = __import__("shutil").copyfileobj

    def exploding(src: object, dst: object, length: int = 0) -> None:
        raise OSError("device disappeared mid-copy")

    __import__("shutil").copyfileobj = exploding  # type: ignore[assignment]
    try:
        with pytest.raises(capture.CaptureError, match="could not capture"):
            capture.capture(str(source), files_dir=library)
    finally:
        __import__("shutil").copyfileobj = real_copyfileobj  # type: ignore[assignment]

    assert not list((library / "b0").rglob("*")) or all(
        not p.name.startswith(".capture-") for p in library.rglob("*")
    ), "a torn temp file must not survive the failure"
    digest = hashlib.sha256(b"fine on disk").hexdigest()
    assert not capture.target_for(library, digest).exists()


# -- the two frontends that offer it ----------------------------------------


@pytest.fixture
def api(tmp_path: Path):
    """A web app over a library in *tmp_path*, with the capture directory beside its database.

    The database path decides where captured files land — `files/` beside it — so a test that means
    to look at the copies needs the same store the app is using, not one it opened for itself.
    """
    from fastapi.testclient import TestClient

    from boxganizer.bootstrap import bootstrap
    from boxganizer.store import BoxStore
    from boxganizer.web.app import create_app

    store = BoxStore(tmp_path / "boxes.db")
    bootstrap(store)
    try:
        with TestClient(create_app(store)) as client:
            client.files_dir = capture.files_dir_for(store.path)  # type: ignore[attr-defined]
            yield client
    finally:
        store.close()


def create(client: object, **fields: object) -> dict:
    return client.post("/api/boxes", json=fields).json()  # type: ignore[attr-defined]


def test_a_box_created_with_capture_points_at_the_copy_not_the_original(
    api: object, tmp_path: Path
) -> None:
    """The address stored on the box is inside the library; the source is untouched."""
    source = write(tmp_path / "outside" / "invoice.pdf", b"amount due: 42")
    files_dir = api.files_dir  # type: ignore[attr-defined]

    box = create(
        api, name="Invoice", box_type="pdf", resource_uri=str(source), capture_resource=True
    )

    digest = hashlib.sha256(b"amount due: 42").hexdigest()
    expected = files_dir / digest[:2] / digest[2:4] / f"{digest}.pdf"
    assert box["resource_uri"] == str(expected)
    assert expected.read_bytes() == b"amount due: 42"
    assert source.read_bytes() == b"amount due: 42", "capture copies; it does not move"
    assert capture.is_captured(box["resource_uri"], files_dir=files_dir)


def test_two_boxes_capturing_the_same_bytes_hold_one_file(api: object, tmp_path: Path) -> None:
    """Dedup is the point of naming by hash: two boxes, one copy, both of them usable."""
    first = write(tmp_path / "one" / "notes.md", b"the same words")
    second = write(tmp_path / "two" / "renamed.md", b"the same words")

    a = create(api, name="A", resource_uri=str(first), capture_resource=True)
    b = create(api, name="B", resource_uri=str(second), capture_resource=True)

    assert a["resource_uri"] == b["resource_uri"]
    copies = [p for p in api.files_dir.rglob("*") if p.is_file()]  # type: ignore[attr-defined]
    assert len(copies) == 1


def test_capture_off_leaves_the_address_exactly_where_it_was(
    api: object, tmp_path: Path
) -> None:
    """Default off: a box points at the file. That is what a card index is for."""
    source = write(tmp_path / "keep" / "pointed.txt", b"leave me alone")

    box = create(api, name="Pointer", resource_uri=str(source))

    assert box["resource_uri"] == str(source)
    files_dir = api.files_dir  # type: ignore[attr-defined]
    copies = [p for p in files_dir.rglob("*") if p.is_file()] if files_dir.exists() else []
    assert copies == []


def test_an_existing_box_can_be_captured_by_editing_it(api: object, tmp_path: Path) -> None:
    """Not only for the birth of a box: pointing at a file is a decision you can change later."""
    source = write(tmp_path / "later" / "text.txt", b"capture me eventually")
    created = create(api, name="Later", box_type="text", resource_uri=str(source))
    assert created["resource_uri"] == str(source)

    answer = api.put(  # type: ignore[attr-defined]
        f"/api/boxes/{created['uuid']}", json={"name": "Later", "capture_resource": True}
    )
    assert answer.status_code == 200, answer.text
    assert capture.is_captured(
        answer.json()["resource_uri"], files_dir=api.files_dir  # type: ignore[attr-defined]
    )
    assert source.exists()


def test_a_folder_cannot_be_captured_and_the_answer_is_a_sentence(
    api: object, tmp_path: Path
) -> None:
    """A refusal a person can act on, not a stack trace and not a silent half-copy."""
    folder = tmp_path / "a-directory"
    folder.mkdir()

    answer = api.post(  # type: ignore[attr-defined]
        "/api/boxes",
        json={
            "name": "Nope",
            "box_type": "directory",
            "resource_uri": str(folder),
            "capture_resource": True,
        },
    )
    assert answer.status_code == 422
    reason = answer.json()["detail"]["reason"]
    assert "folder" in reason
    assert "index the folder" in reason


# -- the trash ----------------------------------------------------------------


def _store(tmp_path: Path) -> object:
    from boxganizer.bootstrap import bootstrap
    from boxganizer.store import BoxStore

    store = BoxStore(tmp_path / "boxes.db")
    bootstrap(store)
    return store


def _captured_box(store: object, tmp_path: Path, name: str, body: bytes) -> dict:
    from boxganizer.models import Box

    source = write(tmp_path / "src" / f"{name}.txt", body)
    got = capture.capture(str(source), files_dir=capture.files_dir_for(store.path))  # type: ignore[attr-defined]
    saved = store.add(Box(name=name, box_type="text", resource_uri=got.reference))  # type: ignore[attr-defined]
    return {"uuid": saved.uuid, "path": got.reference}


def _retire(store: object, deletion: object) -> object:
    return capture.retire_after_delete(
        deletion.box.resource_uri,
        db_path=store.path,  # type: ignore[attr-defined]
        last_reference=deletion.resource_released,
    )


def test_deleting_the_last_box_moves_the_captured_copy_to_the_trash(tmp_path: Path) -> None:
    """Not deleted: moved to `trash/` at the same bucket path, so it can be found and restored."""
    store = _store(tmp_path)
    body = b"the only copy of the boilerhouse log"
    box = _captured_box(store, tmp_path, "Log", body)
    files_dir = capture.files_dir_for(store.path)  # type: ignore[attr-defined]
    captured = Path(box["path"])
    assert captured.is_file()

    retired = _retire(store, store.delete_box(box["uuid"]))  # type: ignore[attr-defined]

    trash = capture.trash_dir_for(store.path)  # type: ignore[attr-defined]
    assert trash.parent == files_dir.parent, "trash is a sibling of files, never inside it"
    assert retired.moved is not None
    # the whole bucket path is preserved, so a trashed copy is put back where it came from by moving
    # it home — not guessed at
    assert retired.moved == trash / captured.relative_to(files_dir)
    assert retired.moved.read_bytes() == body, "the copy is intact, not merely relocated"
    assert not captured.exists()
    store.close()  # type: ignore[attr-defined]


def test_two_boxes_sharing_bytes_survive_each_others_deletion(tmp_path: Path) -> None:
    """A captured file is named for its content, and content can be pointed at twice.

    Only the last box out puts the file in the trash. Deleting one of the two and moving it would
    take the copy away from the box that still believes it holds it.
    """
    store = _store(tmp_path)
    body = b"shared attachment, two boxes"
    first = _captured_box(store, tmp_path, "First", body)
    second = _captured_box(store, tmp_path, "Second", body)
    assert first["path"] == second["path"], "identical bytes, one file"
    files_dir = capture.files_dir_for(store.path)  # type: ignore[attr-defined]

    retired_first = _retire(store, store.delete_box(first["uuid"]))  # type: ignore[attr-defined]
    assert retired_first.moved is None
    assert "another box" in retired_first.reason
    assert Path(first["path"]).is_file(), "the surviving box still has its copy"

    retired_second = _retire(store, store.delete_box(second["uuid"]))  # type: ignore[attr-defined]
    assert retired_second.moved is not None
    assert retired_second.moved.read_bytes() == body
    assert not any(p.is_file() for p in files_dir.rglob("*")), "files/ no longer holds it"
    store.close()  # type: ignore[attr-defined]


def test_a_hand_edited_copy_is_reported_and_left_exactly_where_it_is(tmp_path: Path) -> None:
    """The name is a promise; when it stops holding, nothing moves on the strength of it."""
    store = _store(tmp_path)
    box = _captured_box(store, tmp_path, "Edited", b"original words")
    captured = Path(box["path"])
    captured.write_bytes(b"somebody wrote over this")

    retired = _retire(store, store.delete_box(box["uuid"]))  # type: ignore[attr-defined]

    assert retired.moved is None
    assert "edited by hand" in retired.reason
    assert captured.is_file() and captured.read_bytes() == b"somebody wrote over this"
    trash = capture.trash_dir_for(store.path)  # type: ignore[attr-defined]
    assert not any(p.is_file() for p in trash.rglob("*")) if trash.exists() else True
    store.close()  # type: ignore[attr-defined]


def test_a_box_that_only_pointed_leaves_the_file_alone(tmp_path: Path) -> None:
    """The ordinary case, unchanged: deleting the card does not touch the document (ADR 14)."""
    from boxganizer.models import Box

    store = _store(tmp_path)
    document = write(tmp_path / "elsewhere" / "invoice.pdf", b" somebody else's file")
    saved = store.add(Box(name="Pointer", box_type="pdf", resource_uri=str(document)))  # type: ignore[attr-defined]

    retired = capture.retire_after_delete(
        saved.resource_uri, db_path=store.path, last_reference=True  # type: ignore[attr-defined]
    )
    assert retired is None, "not the library's copy, so not the library's decision"
    assert document.is_file()
    store.close()  # type: ignore[attr-defined]


def test_a_notes_own_words_file_is_not_trashed(tmp_path: Path) -> None:
    """A note writes `files/<uuid>.md`: its own file, and ADR 14 keeps it when the box goes."""
    from boxganizer.models import Box

    store = _store(tmp_path)
    saved = store.add(Box(name="Note", box_type="note"))  # type: ignore[attr-defined]
    # Words first: a note writes its file the first time there is something to keep, so an empty
    # note has nothing to survive it and this would assert about a file that never existed.
    from boxganizer import authored

    authored.ensure_file(store.path, saved.uuid, seed="a line worth keeping\n")  # type: ignore[attr-defined]
    deletion = store.delete_box(saved.uuid)  # type: ignore[attr-defined]
    assert deletion.words_file is not None, "the words file should be there by now"

    retired = capture.retire_after_delete(
        saved.resource_uri,
        db_path=store.path,  # type: ignore[attr-defined]
        last_reference=deletion.resource_released,
    )
    assert retired is None, "uuid-named words are not a captured copy"
    assert deletion.words_file.is_file()
    store.close()  # type: ignore[attr-defined]


def test_check_counts_the_trash_and_says_zero_when_there_is_none(tmp_path: Path) -> None:
    """`box check` answers 'is anything piling up in there?' even when the answer is nothing."""
    from typer.testing import CliRunner

    from boxganizer.cli.main import app

    runner = CliRunner()
    db = tmp_path / "boxes.db"
    quiet = runner.invoke(app, ["--db", str(db), "check"])
    assert quiet.exit_code == 0, quiet.output
    assert "trash: 0 file(s)" in quiet.output

    store = _store(tmp_path)
    box = _captured_box(store, tmp_path, "Counted", b"content worth counting")
    _retire(store, store.delete_box(box["uuid"]))  # type: ignore[attr-defined]
    store.close()  # type: ignore[attr-defined]

    loud = runner.invoke(app, ["--db", str(db), "check"])
    assert "trash: 1 file(s)" in loud.output
    assert "box trash empty --yes" in loud.output

    as_json = runner.invoke(app, ["--db", str(db), "check", "--json"])
    payload = json.loads(as_json.output)
    assert payload["trash"] == {"files": 1, "bytes": len(b"content worth counting")}


def test_emptying_the_trash_asks_first_and_then_really_goes(tmp_path: Path) -> None:
    """The one command that destroys content, so it names what it is about to lose first."""
    from typer.testing import CliRunner

    from boxganizer.cli.main import app

    runner = CliRunner()
    db = tmp_path / "boxes.db"
    store = _store(tmp_path)
    box = _captured_box(store, tmp_path, "Doomed", b"destiny")
    _retire(store, store.delete_box(box["uuid"]))  # type: ignore[attr-defined]
    store.close()  # type: ignore[attr-defined]
    trash = capture.trash_dir_for(db)

    refused = runner.invoke(app, ["--db", str(db), "trash", "empty"], input="n\n")
    assert refused.exit_code != 0, "declining the question aborts"
    assert any(p.is_file() for p in trash.rglob("*")), "nothing moved on a refusal"

    listed = runner.invoke(app, ["--db", str(db), "trash", "list"])
    assert listed.exit_code == 0
    assert "1 file(s)" in listed.output

    emptied = runner.invoke(app, ["--db", str(db), "trash", "empty", "--yes"])
    assert emptied.exit_code == 0, emptied.output
    assert not any(p.is_file() for p in trash.rglob("*"))
    assert "emptied" in emptied.output.lower()

    again = runner.invoke(app, ["--db", str(db), "trash", "empty", "--yes"])
    assert "already empty" in again.output


def test_emptying_the_trash_never_follows_a_symlink_out(tmp_path: Path) -> None:
    """A planted link must not turn 'tidy the trash' into 'delete what it points at'."""
    store = _store(tmp_path)
    trash = capture.trash_dir_for(store.path)  # type: ignore[attr-defined]
    trash.mkdir(parents=True, exist_ok=True)
    precious = write(tmp_path / "precious" / "keep.txt", b"do not delete me")
    (trash / "ab").mkdir(parents=True, exist_ok=True)
    os.symlink(precious, trash / "ab" / "planted")

    removed, _ = capture.empty(trash)

    assert precious.is_file(), "the file outside the trash survives"
    assert not (trash / "ab" / "planted").exists()
    assert removed == 0, "a symlink is not a trashed copy; it is unlinked, not counted"
    store.close()  # type: ignore[attr-defined]
