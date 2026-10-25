"""Tests for the full-text index: extraction, thesaurus, fuzzy, and the store's FTS5."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from boxganizer import search
from boxganizer.models import Box
from boxganizer.store import BoxStore

# --- a PDF with real text in it --------------------------------------------------


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
    out += (
        f"trailer\n<< /Size {len(body) + 1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode()
    )
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
    found = search.extract(_box("blob.png", "image", root / "blob.png"), root=root)
    assert not found.usable and "image" in found.note


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


def test_a_broken_user_file_is_ignored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
    store = BoxStore(root / "fuzzy.db")
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


def test_rebuild_indexes_the_boxes_that_have_text(root: Path) -> None:
    store = BoxStore(root / "lib.db")
    try:
        store.add(_box("siding", "text", root / "siding.txt"))
        store.add(_box("notes", "markdown", root / "notes.md"))
        store.add(_box("plan", "pdf", root / "plan.pdf"))
        store.add(_box("blob", "image", root / "blob.png"))
        store.add(Box(name="a folder", box_type="folder"))
        report = search.rebuild(store, root=root)
        assert report.indexed == 3
        assert store.indexed_boxes() == 3
        assert report.summary()
    finally:
        store.close()


def test_a_content_match_names_the_box_and_shows_where(root: Path) -> None:
    store = BoxStore(root / "lib.db")
    try:
        box = store.add(_box("siding", "text", root / "siding.txt"))
        search.rebuild(store, root=root)
        hits = store.content_search('"fence"', limit=5)
        assert [uuid for uuid, _score, _passage in hits] == [box.uuid]
        assert "fence" in hits[0][2].lower()  # a passage round the word
    finally:
        store.close()


def test_stemming_matches_a_different_form_of_the_word(root: Path) -> None:
    store = BoxStore(root / "lib.db")
    try:
        box = store.add(_box("siding", "text", root / "siding.txt"))
        search.rebuild(store, root=root)
        # the file says "runs"; porter stemming means "run" finds it
        assert [hit[0] for hit in store.content_search('"run"', limit=5)] == [box.uuid]
    finally:
        store.close()


def test_deleting_a_box_takes_its_text_with_it(root: Path) -> None:
    store = BoxStore(root / "lib.db")
    try:
        store.add(_box("siding", "text", root / "siding.txt"))
        search.rebuild(store, root=root)
        assert store.content_search('"fence"', limit=5)
        store.remove("siding")
        assert store.content_search('"fence"', limit=5) == []
        assert store.indexed_boxes() == 0
    finally:
        store.close()


def test_reindexing_a_box_replaces_what_is_held(root: Path) -> None:
    store = BoxStore(root / "lib.db")
    try:
        box = store.add(_box("siding", "text", root / "siding.txt"))
        search.rebuild(store, root=root)
        store.index_text(box.uuid, "something else entirely")
        assert store.content_search('"fence"', limit=5) == []
        assert store.content_search('"entirely"', limit=5)
        assert store.indexed_boxes() == 1  # replaced, not doubled
    finally:
        store.close()


def test_a_malformed_query_answers_with_nothing_rather_than_erroring(root: Path) -> None:
    store = BoxStore(root / "lib.db")
    try:
        store.add(_box("siding", "text", root / "siding.txt"))
        search.rebuild(store, root=root)
        assert store.content_search('cellar ANDOR "fence"', limit=5) == []
    finally:
        store.close()


# --- the two interfaces that use it ------------------------------------------------


def test_the_page_finds_a_box_by_what_its_file_says(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from boxganizer.web.app import create_app

    shelf = tmp_path / "library"
    shelf.mkdir()
    (shelf / "siding.txt").write_text("Paint the fence next spring.", encoding="utf-8")
    store = BoxStore(tmp_path / "lib.db")
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
    store = BoxStore(tmp_path / "lib.db")
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
    store = BoxStore(tmp_path / "lib.db")
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

    rebuilt = invoke(db_path, "index")
    assert rebuilt.exit_code == 0, rebuilt.output
    assert "1 box file indexed" in rebuilt.output

    found = invoke(db_path, "search", "fence")
    assert found.exit_code == 0, found.output
    assert "Siding" in found.output
    assert "fence" in found.output  # and it shows the words that answered


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
    assert "Missing" in rebuilt.output  # named, with the reason
    assert "1 box file indexed" in rebuilt.output
