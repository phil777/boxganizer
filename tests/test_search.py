"""Tests for the full-text index: extraction, thesaurus, fuzzy, and the store's FTS5."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from boxganizer import paths, search
from boxganizer.models import Box
from boxganizer.store import BoxStore

# --- a PDF with real text in it --------------------------------------------------


PDF_TEXT = "celery and the basement flood"


def _pdf_with(text: str) -> bytes:
    """One-page PDF whose only content is *text* (no compression, so pdfium reads it)."""
    stream = f"BT /F1 12 Tf 20 700 Td ({text}) Tj ET".encode()
    body = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, chunk in enumerate(body, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + chunk + b"\nendobj\n"
    start = len(out)
    out += f"xref\n0 {len(body) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(body) + 1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode()
    return bytes(out)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    library = tmp_path / "library"
    library.mkdir()
    (library / "siding.txt").write_text(
        "The cellar pump runs twice a day. Paint the fence next spring.", encoding="utf-8"
    )
    (library / "notes.md").write_text("# Meeting\nAgreed to widen the lane.", encoding="utf-8")
    (library / "plan.pdf").write_bytes(_pdf_with("Basement flooding report for the annex"))
    (library / "blob.png").write_bytes(bytes(range(256)) * 4)
    return library


def _box(name: str, box_type: str, resource: Path) -> Box:
    return Box(name=name, box_type=box_type, resource_uri=str(resource))


# --- extraction ------------------------------------------------------------------


def test_text_and_markdown_and_pdf_are_all_readable(root: Path) -> None:
    for name, kind in (("siding.txt", "text"), ("notes.md", "markdown"), ("plan.pdf", "pdf")):
        found = search.extract(_box(name, kind, root / name), root=root)
        assert found.usable, f"{name}: {found.note}"


def test_the_pdf_text_actually_arrives(root: Path) -> None:
    found = search.extract(_box("plan.pdf", "pdf", root / "plan.pdf"), root=root)
    assert "Basement" in found.text and "flooding" in found.text


def test_a_picture_is_not_read_for_text(root: Path) -> None:
    # the type says no, and so does the file when someone overrides it
    assert search.indexes(_box("blob.png", "image", root / "blob.png")) is False
    found = search.extract(_box("blob.png", "image", root / "blob.png"), root=root)
    assert not found.usable
    assert "not text" in found.note


def test_a_missing_file_is_reported_not_raised(root: Path) -> None:
    found = search.extract(_box("gone", "text", root / "gone.txt"), root=root)
    assert not found.usable and "file" in found.note


def test_nothing_outside_the_confine_is_read(root: Path, tmp_path: Path) -> None:
    secret = tmp_path / "outside.txt"
    secret.write_text("private", encoding="utf-8")
    found = search.extract(_box("outside", "text", secret), root=root)
    assert not found.usable and "outside" in found.note


def test_a_box_without_a_resource_is_said_so() -> None:
    found = search.extract(Box(name="nothing", box_type="folder"))
    assert not found.usable


def test_an_oversized_file_is_refused(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(search, "MAX_TEXT_BYTES", 10)
    found = search.extract(_box("siding.txt", "text", root / "siding.txt"), root=root)
    assert not found.usable and "bytes" in found.note


# --- the thesaurus ----------------------------------------------------------------


def test_a_bundled_group_expands_both_ways() -> None:
    assert "basement" in search.synonyms("cellar")
    assert "cellar" in search.synonyms("basement")


def test_a_word_not_in_the_thesaurus_expands_to_nothing() -> None:
    assert search.synonyms("flibble") == ()


def test_a_user_file_is_merged_with_the_bundled_words(
    root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    (data / "thesaurus.json").write_text(
        json.dumps({"annex": ["outbuilding", "wing"]}), encoding="utf-8"
    )
    monkeypatch.setattr(search.config, "data_dir", lambda: data)
    search.reset_thesaurus()
    try:
        assert "outbuilding" in search.synonyms("annex")
        assert "annex" in search.synonyms("wing")
        assert "basement" in search.synonyms("cellar")  # the built-in list still works
    finally:
        search.reset_thesaurus()


def test_a_broken_user_file_is_ignored(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    (data / "thesaurus.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(search.config, "data_dir", lambda: data)
    search.reset_thesaurus()
    try:
        assert search.synonyms("cellar")  # bundled words survive a bad file
    finally:
        search.reset_thesaurus()


# --- planning ---------------------------------------------------------------------


def test_a_query_keeps_its_words_and_their_synonyms() -> None:
    plan = search.plan("cellar pump", lambda term: [])
    assert '"cellar"' in plan.content_match
    assert '"pump"' in plan.content_match
    assert " AND " in plan.content_match  # both words must be present
    assert '"basement"' in plan.content_match  # and the synonyms may stand in


def test_a_phrase_in_quotes_is_one_thing_to_find() -> None:
    plan = search.plan('"next spring"', lambda term: [])
    assert "next spring" in plan.content_match


def test_an_operator_word_is_not_looked_for_as_a_word() -> None:
    plan = search.plan("cellar AND pump", lambda term: [])
    # 'and' is the operator; it must not also be demanded as a term of its own
    assert plan.content_match.count('"and"') == 0


def test_quotes_cannot_break_the_expression() -> None:
    plan = search.plan('cellar "untclosed', lambda term: [])
    assert plan.content_match.count('"') % 2 == 0  # balanced, whatever was typed


def test_fuzzy_forms_come_from_the_index_vocabulary(root: Path) -> None:
    store = BoxStore(root / "fuzzy.db", content_root=root)
    try:
        store.add(_box("siding", "text", root / "siding.txt"))
        search.rebuild(store, root=root)
        plan = search.plan("cellrr", lambda term: store.vocabulary(prefix=term[:1]))
        assert '"cellar"' in plan.content_match  # one key off, still found
        assert plan.expanded  # and the caller is told what was widened
    finally:
        store.close()


def test_a_short_word_is_not_fuzzied() -> None:
    # 'cat' would otherwise be offered 'car', 'bat', 'cot' and drown the real answer
    plan = search.plan("cat", lambda term: ["car", "cot", "catalogue"])
    assert '"car"' not in plan.content_match


# --- the store's index --------------------------------------------------------------


def _resource_of(store: BoxStore, name: str) -> int:
    """The row a box points at, which is what the index is keyed on."""
    box = store.get(name)
    assert box.resource_id is not None
    return int(box.resource_id)


def test_one_file_two_boxes_is_one_entry_in_the_index(tmp_path: Path) -> None:
    store = BoxStore(tmp_path / "shared.db", content_root=tmp_path)
    try:
        notes = tmp_path / "notes.txt"
        notes.write_text("The cellar pump runs twice a night.", encoding="utf-8")
        first = store.add(Box(name="First", box_type="text", resource_uri=str(notes)))
        second = store.add(Box(name="Second", box_type="folder", resource_uri=str(notes)))
        assert first.resource_id == second.resource_id  # one file, one row
        assert store.indexed_count() == 1

        planned = search.plan("cellar")
        hits = store.content_search(planned.content_match, limit=5)
        assert [hit[0] for hit in hits] == [first.resource_id]
        # and the answer comes back as both boxes, because both are true
        boxes = store.boxes_by_resources([first.resource_id])[first.resource_id]
        assert sorted(box.name for box in boxes) == ["First", "Second"]
    finally:
        store.close()


def test_rebuild_reads_every_resource_the_index_is_wanted_for(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text(
        "The cellar pump runs every night at three.", encoding="utf-8"
    )
    (tmp_path / "report.pdf").write_bytes(_pdf_with(PDF_TEXT))
    store = BoxStore(tmp_path / "index.db", content_root=tmp_path)
    try:
        store.add(Box(name="Notes", box_type="text", resource_uri=str(tmp_path / "notes.txt")))
        store.add(Box(name="Report", box_type="pdf", resource_uri=str(tmp_path / "report.pdf")))
        store.add(Box(name="Shelf", box_type="folder"))  # an organiser: nothing to read
        # the adds already read both files in, so the rebuild finds nothing to do and
        # says which of the two that is
        report = search.rebuild(store, root=tmp_path)
        assert report.resources == 2
        assert report.indexed + report.unchanged == 2
        assert store.indexed_count() == 2

        # "cellar" reaches the PDF too, through the synonym "basement" inside it —
        # which is the whole point of widening, and worth asserting on rather than hiding
        planned = search.plan("cellar", lambda term: store.vocabulary(prefix=term[:1]))
        hits = {hit[0] for hit in store.content_search(planned.content_match, limit=10)}
        assert hits == {_resource_of(store, "Notes"), _resource_of(store, "Report")}
        # a word that only the PDF contains finds the PDF, and nothing else
        alone = store.content_search(search.plan("celery").content_match, limit=10)
        assert [hit[0] for hit in alone] == [_resource_of(store, "Report")]
    finally:
        store.close()


def test_a_file_that_has_not_changed_is_not_read_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The observations on the resource row are what make watching a library cheap."""
    notes = tmp_path / "notes.txt"
    notes.write_text("The cellar pump runs twice a night.", encoding="utf-8")
    store = BoxStore(tmp_path / "watch.db", content_root=tmp_path)
    try:
        store.add(Box(name="Notes", box_type="text", resource_uri=str(notes)))
        search.sync_resource(store, _resource_of(store, "Notes"))  # the add read it in
        reads: list[str] = []
        real = search.extract_resource

        def counting(reference: str, **kwargs: object) -> search.Extracted:
            reads.append(reference)
            return real(reference, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(search, "extract_resource", counting)
        search.sync_resource(store, _resource_of(store, "Notes"))
        assert reads == []  # same size, same mtime: the index already says what it says
        notes.write_text("The cellar pump runs twice an hour now.", encoding="utf-8")
        os.utime(notes, (notes.stat().st_mtime + 30, notes.stat().st_mtime + 30))
        search.sync_resource(store, _resource_of(store, "Notes"))
        assert reads == [str(notes)]
        assert "hour" in store.content_search(search.plan("hour").content_match, limit=5)[0][2]
    finally:
        store.close()


def test_opting_a_resource_out_takes_it_out_of_the_index(tmp_path: Path) -> None:
    notes = tmp_path / "notes.txt"
    notes.write_text("Something private about the cellar.", encoding="utf-8")
    store = BoxStore(tmp_path / "opt.db", content_root=tmp_path)
    try:
        store.add(Box(name="Notes", box_type="text", resource_uri=str(notes)))
        assert store.content_search(search.plan("cellar").content_match, limit=5)
        changed = store.set_index_flag(_resource_of(store, "Notes"), False)
        assert changed is not None and changed.index_it is False
        assert store.content_search(search.plan("cellar").content_match, limit=5) == []
        assert store.indexed_count() == 0
    finally:
        store.close()


def test_a_content_match_shows_the_words_that_answered(tmp_path: Path) -> None:
    (tmp_path / "siding.txt").write_text("Paint the fence next spring.", encoding="utf-8")
    store = BoxStore(tmp_path / "lib.db", content_root=tmp_path)
    try:
        store.add(_box("Siding", "text", tmp_path / "siding.txt"))
        hits = store.content_search('"fence"', limit=5)
        assert [hit[0] for hit in hits] == [_resource_of(store, "Siding")]
        assert "fence" in hits[0][2].lower()
    finally:
        store.close()


def test_stemming_matches_a_different_form_of_the_word(tmp_path: Path) -> None:
    (tmp_path / "runs.txt").write_text("The pump runs twice a night.", encoding="utf-8")
    store = BoxStore(tmp_path / "stem.db", content_root=tmp_path)
    try:
        store.add(_box("Runs", "text", tmp_path / "runs.txt"))
        search.rebuild(store, root=tmp_path)
        assert store.content_search('"run"', limit=5)  # the index holds the stem
        assert store.content_search('"running"', limit=5)  # and folds the query too
    finally:
        store.close()


def test_deleting_the_last_box_takes_the_text_with_it(tmp_path: Path) -> None:
    notes = tmp_path / "notes.txt"
    notes.write_text("Paint the fence next spring.", encoding="utf-8")
    store = BoxStore(tmp_path / "del.db", content_root=tmp_path)
    try:
        store.add(Box(name="First", box_type="text", resource_uri=str(notes)))
        resource_id = _resource_of(store, "First")
        assert store.content_search('"fence"', limit=5)
        store.add(Box(name="Second", box_type="text", resource_uri=str(notes)))
        store.remove("First")
        # the file still has a box pointing at it, so the index keeps it
        assert store.content_search('"fence"', limit=5)
        store.remove("Second")
        assert store.content_search('"fence"', limit=5) == []
        assert store.resource(resource_id) is None
    finally:
        store.close()


def test_reindexing_a_resource_replaces_what_is_held(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("Paint the fence next spring.", encoding="utf-8")
    store = BoxStore(tmp_path / "replace.db", content_root=tmp_path)
    try:
        store.add(Box(name="Notes", box_type="text", resource_uri=str(tmp_path / "notes.txt")))
        search.rebuild(store, root=tmp_path)
        resource_id = _resource_of(store, "Notes")
        store.record_index(resource_id, body="something else entirely")
        assert store.content_search('"fence"', limit=5) == []
        assert store.content_search('"entirely"', limit=5)
        assert store.indexed_count() == 1  # replaced, not doubled
    finally:
        store.close()


def test_a_malformed_query_answers_with_nothing_rather_than_erroring(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("Paint the fence.", encoding="utf-8")
    store = BoxStore(tmp_path / "bad.db", content_root=tmp_path)
    try:
        store.add(Box(name="Notes", box_type="text", resource_uri=str(tmp_path / "notes.txt")))
        search.rebuild(store, root=tmp_path)
        assert store.content_search('cellar ANDOR "fence"', limit=5) == []
    finally:
        store.close()


def test_checking_observations_notices_a_file_edited_elsewhere(tmp_path: Path) -> None:
    notes = tmp_path / "notes.txt"
    notes.write_text("Paint the fence next spring.", encoding="utf-8")
    store = BoxStore(tmp_path / "stale.db", content_root=tmp_path)
    try:
        store.add(Box(name="Notes", box_type="text", resource_uri=str(notes)))
        search.rebuild(store, root=tmp_path)
        assert store.check_observations() == []
        notes.write_text("Paint the fence and the gate next spring.", encoding="utf-8")
        os.utime(notes, (notes.stat().st_mtime + 30, notes.stat().st_mtime + 30))
        stale = store.check_observations()
        assert [item.reference for item in stale] == [str(notes)]
        # and a pass over the stale ones only is enough to fix it
        report = search.rebuild(store, root=tmp_path, only_stale=True)
        assert report.resources == 1
        assert store.check_observations() == []
    finally:
        store.close()


# --- the two interfaces that use it ------------------------------------------------


def test_the_page_finds_a_box_by_what_its_file_says(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from boxganizer.web.app import create_app

    shelf = tmp_path / "library"
    shelf.mkdir()
    (shelf / "siding.txt").write_text("Paint the fence next spring.", encoding="utf-8")
    store = BoxStore(tmp_path / "lib.db", content_root=tmp_path)
    try:
        with TestClient(create_app(store, content_root=shelf)) as client:
            created = client.post(
                "/api/boxes",
                json={
                    "name": "Siding",
                    "box_type": "text",
                    "resource_uri": str(shelf / "siding.txt"),
                },
            )
            assert created.status_code == 201
            hits = client.get("/api/search?q=fence").json()
            assert [hit["name"] for hit in hits] == ["Siding"]
            assert hits[0]["match"]["how"] == "content"
            assert "fence" in hits[0]["match"]["passage"].lower()
    finally:
        store.close()


def test_the_page_says_which_words_it_was_widened_to(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from boxganizer.web.app import create_app

    shelf = tmp_path / "library"
    shelf.mkdir()
    (shelf / "cellar.txt").write_text("The basement floods every February.", encoding="utf-8")
    store = BoxStore(tmp_path / "lib.db", content_root=tmp_path)
    try:
        with TestClient(create_app(store, content_root=shelf)) as client:
            client.post(
                "/api/boxes",
                json={
                    "name": "Damp",
                    "box_type": "text",
                    "resource_uri": str(shelf / "cellar.txt"),
                },
            )
            asked = client.get("/api/search?q=cellar")
            assert [hit["name"] for hit in asked.json()] == ["Damp"]
            # the file says basement, never cellar: the page has to own up to that
            assert "basement" in asked.headers.get("X-Search-Expanded", "")
            # and a search that needed no widening says nothing about widening
            plain = client.get("/api/search?q=february")
            assert plain.headers.get("X-Search-Expanded") is None  # a known word is not guessed at
    finally:
        store.close()


def test_a_box_retyped_away_from_text_leaves_the_index(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from boxganizer.web.app import create_app

    shelf = tmp_path / "library"
    shelf.mkdir()
    (shelf / "siding.txt").write_text("Paint the fence next spring.", encoding="utf-8")
    store = BoxStore(tmp_path / "lib.db", content_root=tmp_path)
    try:
        with TestClient(create_app(store, content_root=shelf)) as client:
            created = client.post(
                "/api/boxes",
                json={
                    "name": "Siding",
                    "box_type": "text",
                    "resource_uri": str(shelf / "siding.txt"),
                },
            ).json()
            assert client.get("/api/search?q=fence").json()
            client.put(
                f"/api/boxes/{created['uuid']}",
                json={"box_type": "web", "resource_uri": "https://example.com"},
            )
            assert client.get("/api/search?q=fence").json() == []
    finally:
        store.close()


def test_the_command_line_indexes_and_then_finds_the_words(
    db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from boxganizer.cli.main import app as cli_app

    def invoke(db: Path, *args: str):  # type: ignore[no-untyped-def]
        return CliRunner().invoke(cli_app, ["--db", str(db), *args])

    shelf = tmp_path / "library"
    shelf.mkdir()
    (shelf / "siding.txt").write_text("Paint the fence next spring.", encoding="utf-8")
    monkeypatch.setenv("BOXGANIZER_CONTENT_ROOT", str(shelf))

    added = invoke(db_path, "add", "Siding", "-t", "text", "-r", str(shelf / "siding.txt"))
    assert added.exit_code == 0, added.output

    # adding the box read it in, so a rebuild the same day has nothing to do — and says
    # so, rather than pretending to work
    first = invoke(db_path, "index")
    assert first.exit_code == 0, first.output
    assert "already up to date" in first.output

    (shelf / "siding.txt").write_text(
        "Paint the fence next spring, and the gate too.", encoding="utf-8"
    )
    os.utime(shelf / "siding.txt", (time.time() + 30, time.time() + 30))
    rebuilt = invoke(db_path, "index")
    assert rebuilt.exit_code == 0, rebuilt.output
    assert "1 file read into the index" in rebuilt.output

    found = invoke(db_path, "search", "gate")
    assert found.exit_code == 0, found.output
    assert "Siding" in found.output
    assert "gate" in found.output  # and it shows the words that answered


def test_the_command_line_reports_a_file_it_could_not_read(
    db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from boxganizer.cli.main import app as cli_app

    def invoke(db: Path, *args: str):  # type: ignore[no-untyped-def]
        return CliRunner().invoke(cli_app, ["--db", str(db), *args])

    shelf = tmp_path / "library"
    shelf.mkdir()
    (shelf / "present.txt").write_text("here it is", encoding="utf-8")
    monkeypatch.setenv("BOXGANIZER_CONTENT_ROOT", str(shelf))
    invoke(db_path, "add", "Good", "-t", "text", "-r", str(shelf / "present.txt"))
    invoke(db_path, "add", "Missing", "-t", "text", "-r", str(shelf / "absent.txt"))

    rebuilt = invoke(db_path, "index")
    assert rebuilt.exit_code == 0, rebuilt.output
    # named by the last part of its path, with the reason, and counted as unread
    assert "absent.txt" in rebuilt.output
    assert "not there any more" in rebuilt.output
    # (rich wraps the summary line at the terminal width, so only the parts of it that
    # fit on one line are looked for)
    assert "1 nothing" in rebuilt.output


# -- a confine is something you name ----------------------------------------


def test_a_named_confine_is_the_only_kind_of_confine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`paths.confine` answers None when nothing was named: no boundary is the default.

    The old default was the home directory, which read as safety but behaved as a bug — a photo on
    another mount could be filed, shown, and quietly never indexed. Naming a root (by argument, by
    `box serve --confined`, or by `$BOXGANIZER_CONTENT_ROOT`) is the opt-in. ADR 30.
    """
    monkeypatch.delenv("BOXGANIZER_CONTENT_ROOT", raising=False)
    assert paths.confine(None) is None
    assert paths.confine("") is None
    assert paths.confine(tmp_path) == tmp_path
    monkeypatch.setenv("BOXGANIZER_CONTENT_ROOT", str(tmp_path / "from-env"))
    assert paths.confine(None) == tmp_path / "from-env"
    # and the *starting place* keeps its home fallback: opening somewhere friendly is not a boundary
    assert paths.content_root(None) == tmp_path / "from-env"


def test_a_file_outside_home_is_indexed_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The word in a file on another mount reaches the ordinary content search.

    Asserted through the index rather than through the read path: `/raw` serving a file proves only
    that the file is openable, and the bug this replaces was exactly a file that was served and
    never searchable, with nothing saying so. `tmp_path` sits under /tmp: outside any home.
    """
    monkeypatch.delenv("BOXGANIZER_CONTENT_ROOT", raising=False)
    elsewhere = tmp_path / "mount"
    elsewhere.mkdir()
    kettle = elsewhere / "kettle.md"
    kettle.write_text("the kettle boils at ninety eight degrees\n", encoding="utf-8")

    with BoxStore(tmp_path / "free.db") as store:
        assert store.content_root is None, "the store carries no confine unless one is named"
        store.add(Box(name="Kettle", box_type="markdown", resource_uri=str(kettle)))
        hits = store.content_search(search.plan("kettle").content_match, limit=5)
        assert hits, "a filed file that never reaches the index is a search that lies"
        assert "ninety eight" in hits[0][2]


def test_a_named_confine_still_declines_to_read_outside_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Confined means confined: a file outside the root stays out of the index.

    It is *named* in the outstanding list, which is what the reader has to go on. What it is not
    today is explained: the resource is marked read with nothing in it, so the list calls it "has
    bytes but gave up no text to index" rather than "outside /…". Recorded rather than fixed here,
    because the sentence belongs with the index work that follows this item.
    """
    monkeypatch.delenv("BOXGANIZER_CONTENT_ROOT", raising=False)
    inside = tmp_path / "library"
    inside.mkdir()
    (inside / "in.md").write_text("the boiler ticks at night\n", encoding="utf-8")
    (tmp_path / "out.md").write_text("the turbine hums at dawn\n", encoding="utf-8")

    with BoxStore(tmp_path / "wall.db", content_root=inside) as store:
        store.add(Box(name="In", box_type="markdown", resource_uri=str(inside / "in.md")))
        store.add(Box(name="Out", box_type="markdown", resource_uri=str(tmp_path / "out.md")))
        search.rebuild(store, root=inside)
        assert store.content_search(search.plan("turbine").content_match, limit=5) == []
        assert store.content_search(search.plan("boiler").content_match, limit=5)
        named = {item["reference"]: item for item in store.needing_index()}
        assert "out.md" in named, named
        # the file the confine declined is still pointed at, and still called what it is not
        assert named["out.md"]["boxes"][0]["name"] == "Out"
