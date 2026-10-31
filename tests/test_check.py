"""`box check`: what a box promises about its resource, and the repairs that apply."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from boxganizer import check as checker
from boxganizer.models import Box
from boxganizer.store import BoxStore
from boxganizer.web.app import create_app


@pytest.fixture
def library(tmp_path: Path) -> Iterator[BoxStore]:
    root = tmp_path / "files"
    root.mkdir()
    (root / "here.md").write_text("# Here\n\nthe boiler pressure gauge\n", encoding="utf-8")
    store = BoxStore(tmp_path / "db" / "c.db", content_root=root)
    store.add(Box(name="Here", box_type="markdown", resource_uri=str(root / "here.md")))
    store.add(Box(name="There", box_type="markdown", resource_uri=str(root / "not-here.md")))
    # a folder box aimed at a file: legal to store, wrong on the disk
    store.add(Box(name="Not a folder", box_type="directory", resource_uri=str(root / "here.md")))
    store.add(Box(name="Shelf", box_type="folder"))
    yield store
    store.close()


def kinds(store: BoxStore, confine: Path | None = None) -> dict[str, str]:
    """Every finding, keyed by the box it is about, so each case reads on its own."""
    found = {}
    for item in checker.run(store, confine=confine):
        found[f"{item.box_name or Path(item.path).name}:{item.kind}"] = item.kind
    return found


# -- what is noticed ----------------------------------------------------------


def test_a_resource_that_is_no_longer_there_is_reported_as_gone(library: BoxStore) -> None:
    assert kinds(library).get("There:gone") == "gone"


def test_a_file_where_a_folder_was_expected_is_reported_by_kind(library: BoxStore) -> None:
    item = next(f for f in checker.run(library) if f.box_name == "Not a folder")
    assert item.kind == "wrong_kind"
    assert "is a file" in item.detail and "directory" in item.detail


def test_a_file_that_became_a_folder_is_reported_the_other_way(library: BoxStore) -> None:
    """The disk is what changes: a box cannot be stored pointing at a folder it outgrew."""
    root = Path(library.content_root)
    grown = root / "was-a-file.md"
    library.add(Box(name="Grew", box_type="markdown", resource_uri=str(grown)))
    grown.mkdir()  # somebody replaced the file with a folder while Boxganizer was closed
    item = next(f for f in checker.run(library) if f.box_name == "Grew")
    assert item.kind == "wrong_kind" and "is a folder" in item.detail


def test_a_box_holding_nothing_is_a_broken_promise_only_for_types_that_need_one(
    library: BoxStore,
) -> None:
    """A folder with no address made no promise; a markdown box that lost one did."""
    reported = kinds(library)
    assert "Shelf:missing_address" not in reported
    # a resource-requiring box with nothing to point at cannot be stored, so make one by
    # emptying a stored box's address
    box = library.get("There")
    box.resource_uri = None
    library._execute("UPDATE boxes SET resource_id = NULL WHERE id = ?", (box.id,))
    assert "There:missing_address" in kinds(library)


def test_a_resource_outside_the_confine_is_said_to_be_out_of_range(
    library: BoxStore, tmp_path: Path
) -> None:
    far = tmp_path / "far.md"
    far.write_text("words, out of reach\n", encoding="utf-8")
    library.add(Box(name="Far", box_type="markdown", resource_uri=str(far)))
    assert "Far:out_of_range" in kinds(library)


def test_a_file_changed_since_it_was_read_is_reported_stale(library: BoxStore) -> None:
    """A check looks: the stored flag is what the panel shows, and this is the repair door."""
    from boxganizer import search as text_search

    root = Path(library.content_root)
    text_search.rebuild(library, root=root)
    (root / "here.md").write_text("# Here\n\nthe boiler is leaking now\n", encoding="utf-8")
    assert "Here:stale" in kinds(library, root)
    assert checker.REINDEX in next(f for f in checker.run(library) if f.box_name == "Here").fixes


def test_an_empty_file_that_was_read_is_not_called_broken(library: BoxStore) -> None:
    from boxganizer import search as text_search

    root = Path(library.content_root)
    (root / "blank.md").write_text("", encoding="utf-8")
    library.add(Box(name="Blank", box_type="markdown", resource_uri=str(root / "blank.md")))
    text_search.rebuild(library, root=root)
    blank = [item for item in checker.run(library) if item.box_name == "Blank"]
    assert not any(item.severity == "broken" for item in blank)


def test_a_file_that_holds_no_words_though_it_has_size_is_watched_not_broken(
    library: BoxStore,
) -> None:
    from boxganizer import search as text_search

    root = Path(library.content_root)
    secret = root / "binary.md"
    secret.write_bytes(b"\x00\x01\x02not text at all\x00")
    library.add(Box(name="Binary", box_type="markdown", resource_uri=str(secret)))
    text_search.rebuild(library, root=root)
    item = next(f for f in checker.run(library) if f.box_name == "Binary")
    assert item.kind == "nothing_indexed" and item.severity == "watch"


def test_a_note_naming_a_box_that_is_not_here_is_reported_without_a_fix(
    library: BoxStore,
) -> None:
    from boxganizer import authored

    note = library.add(Box(name="Plumbing", box_type="note"))
    authored.write(
        library.path,
        note.uuid,
        "ask the [📦 Ghost](boxganizer:00000000-1111-2222-3333-444455556666) about it\n",
    )
    item = next(f for f in checker.run(library) if f.box_name == "Plumbing")
    assert item.kind == "dangling_reference"
    assert item.fixes == []  # the sentence is the person's, not ours to rewrite
    assert "1 link" in item.detail


def test_a_file_boxganizer_wrote_for_a_box_that_is_gone_is_an_orphan(
    library: BoxStore,
) -> None:
    from boxganizer import authored

    note = library.add(Box(name="Temporary", box_type="note"))
    authored.write(library.path, note.uuid, "something worth keeping for a moment\n")
    library.remove("Temporary")  # ADR 14: the file outlives the box, on purpose
    item = next(f for f in checker.run(library) if f.kind == "orphan_file")
    assert item.severity == "watch"
    assert item.fixes == [checker.DELETE_FILE]


# -- what can be done ---------------------------------------------------------


def test_every_finding_offers_only_repairs_that_make_sense_for_it(
    library: BoxStore,
) -> None:
    for item in checker.run(library):
        assert set(item.fixes) <= {
            checker.RELOCATE,
            checker.DELETE_BOX,
            checker.REINDEX,
            checker.UNINDEX,
            checker.DELETE_FILE,
        }
        if item.fixes:
            assert item.command(), f"{item.kind} offers a fix and no way to run it"


def test_repointing_a_box_is_a_repair_and_it_takes_an_address(library: BoxStore) -> None:

    root = Path(library.content_root)
    item = next(f for f in checker.run(library) if f.box_name == "There" and f.kind == "gone")
    with pytest.raises(ValueError):
        checker.apply(library, item, checker.RELOCATE, value="  ")
    said = checker.apply(library, item, checker.RELOCATE, value=str(root / "here.md"))
    assert "now points at" in said
    assert library.get("There").resource_uri == str(root / "here.md")
    # and it is no longer gone
    assert not any(f.box_name == "There" and f.kind == "gone" for f in checker.run(library))


def test_a_repair_that_was_never_offered_is_refused(library: BoxStore) -> None:
    item = checker.run(library)[0]
    with pytest.raises(ValueError):
        checker.apply(library, item, "make-it-so")


def test_taking_a_file_out_of_the_index_is_a_repair(library: BoxStore) -> None:
    from boxganizer import search as text_search

    root = Path(library.content_root)
    text_search.rebuild(library, root=root)
    (root / "here.md").write_text("changed\n", encoding="utf-8")
    item = next(f for f in checker.run(library) if f.box_name == "Here")
    said = checker.apply(library, item, checker.UNINDEX)
    assert "no longer read" in said
    record = library.resource(str(root / "here.md"))
    assert record is not None and record.index_it is False


def test_deleting_the_box_is_a_repair_offered_for_a_broken_promise(library: BoxStore) -> None:
    item = next(f for f in checker.run(library) if f.box_name == "There" and f.kind == "gone")
    assert checker.DELETE_BOX in item.fixes
    assert "deleted the box" in checker.apply(library, item, checker.DELETE_BOX)
    from boxganizer.errors import BoxNotFound

    with pytest.raises(BoxNotFound):
        library.get("There")


def test_an_orphan_file_is_deleted_only_when_asked(library: BoxStore) -> None:
    from boxganizer import authored

    note = library.add(Box(name="Brief", box_type="note"))
    target = authored.write(library.path, note.uuid, "gone in a moment\n")
    library.remove("Brief")
    item = next(f for f in checker.run(library) if f.kind == "orphan_file")
    assert target.exists()
    assert "deleted" in checker.apply(library, item, checker.DELETE_FILE)
    assert not target.exists()


def test_the_summary_reads_as_a_sentence(tmp_path: Path) -> None:
    store = BoxStore(tmp_path / "quiet.db", content_root=tmp_path)
    try:
        store.add(Box(name="Shelf", box_type="folder"))
        assert checker.summary(checker.run(store)) == (
            "nothing to report — every box's resource is where it said it would be"
        )
    finally:
        store.close()


# -- the two doors ------------------------------------------------------------


def test_the_cli_reports_findings_with_the_command_that_fixes_them(
    library: BoxStore,
) -> None:
    from typer.testing import CliRunner

    from boxganizer.cli.main import app

    result = CliRunner().invoke(
        app,
        ["check"],
        env={
            "BOXGANIZER_DB": str(library.path),
            "BOXGANIZER_CONFIG": str(Path(library.path).parent / "config.json"),
            "BOXGANIZER_CONTENT_ROOT": str(library.content_root),
        },
    )
    assert result.exit_code == 0, result.output
    assert "broken" in result.output
    # quoted only when the name needs it, and never wrapped: it is meant to be copied
    assert "fix: box edit There --resource <new address>" in result.output


def test_the_cli_can_be_asked_for_json(library: BoxStore) -> None:
    import json

    from typer.testing import CliRunner

    from boxganizer.cli.main import app

    result = CliRunner().invoke(
        app,
        ["check", "--json"],
        env={
            "BOXGANIZER_DB": str(library.path),
            "BOXGANIZER_CONFIG": str(Path(library.path).parent / "config.json"),
            "BOXGANIZER_CONTENT_ROOT": str(library.content_root),
        },
    )
    payload = json.loads(result.output)
    assert {"summary", "confine", "findings"} <= set(payload)
    assert any(item["kind"] == "gone" for item in payload["findings"])
    assert all("fixes" in item and "command" in item for item in payload["findings"])


def test_the_page_lists_findings_and_repairs_one_of_them(library: BoxStore) -> None:
    with TestClient(create_app(library, content_root=library.content_root)) as client:
        report = client.get("/api/check").json()
        assert report["findings"]
        assert "confine" in report
        broken = next(item for item in report["findings"] if item["severity"] == "broken")
        answer = client.post(
            "/api/check/fix",
            json={
                "kind": broken["kind"],
                "action": checker.DELETE_BOX,
                "box": broken["box"]["uuid"],
                "path": broken["path"],
            },
        )
        assert answer.status_code == 200
        assert "deleted the box" in answer.json()["did"]


def test_a_repair_the_check_never_offered_is_refused_by_the_page(library: BoxStore) -> None:
    with TestClient(create_app(library, content_root=library.content_root)) as client:
        finding = client.get("/api/check").json()["findings"][0]
        refused = client.post(
            "/api/check/fix",
            json={
                "kind": finding["kind"],
                "action": "delete-everything",
                "box": finding["box"]["uuid"] if finding["box"] else "",
                "path": finding["path"],
            },
        )
        assert refused.status_code == 422
        assert refused.json()["detail"]["error"] == "cannot_apply"

        # and a finding that has gone away is a dialog that is out of date, not a repair
        missing = client.post(
            "/api/check/fix",
            json={"kind": "gone", "action": checker.DELETE_BOX, "box": "no-such-box"},
        )
        assert missing.status_code == 409
        assert missing.json()["detail"]["error"] == "already_fixed"
