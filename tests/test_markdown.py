"""Markdown rendering: what comes out, and what is stopped from coming out."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from boxganizer import markdown as md
from boxganizer.store import BoxStore

_URLS = re.compile(r'(?:href|src)="([^"]*)"')


def urls_in(html: str) -> list[str]:
    return _URLS.findall(html)


# -- what a file turns into ---------------------------------------------------


def test_a_heading_is_a_heading() -> None:
    assert "<h1>Pump schedule</h1>" in md.render("# Pump schedule")
    assert "<h3>Valves</h3>" in md.render("### Valves")


def test_emphasis_and_code_survive() -> None:
    html = md.render("Some **bold**, a *thing*, and `code`.")
    assert "<strong>bold</strong>" in html
    assert "<em>thing</em>" in html
    assert "<code>code</code>" in html


def test_lists_nest_as_written() -> None:
    html = md.render("- one\n- two\n  - nested\n")
    assert html.count("<ul>") == 2  # the nested list is its own list, not text
    assert "nested" in html[html.index("<ul>", html.index("<ul>") + 1) :]


def test_a_table_lines_up() -> None:
    html = md.render("| day | runs |\n| --- | ---: |\n| Mon | 2 |\n")
    assert "<table>" in html
    assert "<th>day</th>" in html
    assert 'style="text-align:right"' in html


def test_strikethrough_is_one_of_the_two_github_isms_kept() -> None:
    assert "<s>old plan</s>" in md.render("~~old plan~~")


def test_a_quote_is_a_quote_and_a_rule_is_a_rule() -> None:
    assert "<blockquote>" in md.render("> report any overflow")
    assert "<hr" in md.render("---")  # markdown-it writes the void element closed


def test_a_fenced_block_keeps_its_shape_and_its_content_literal() -> None:
    html = md.render("```python\nif x < 3 and y > 2:\n    pass\n```")
    assert "<pre>" in html and "<code" in html
    assert "&lt;" in html  # the comparison signs are text, not markup


# -- what is stopped ----------------------------------------------------------


def test_raw_html_in_a_file_stays_text_on_the_screen() -> None:
    html = md.render("<script>document.title = 'hijacked'</script>")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_an_attribute_cannot_be_smuggled_through_an_image_tag() -> None:
    html = md.render('<img src=x onerror=alert(1)>')
    assert "onerror" not in html or "<img" not in html


@pytest.mark.parametrize(
    "source",
    [
        "[click](javascript:alert(1))",
        "[click](<javascript:alert(1)>)",
        "[click](JaVaScRiPt:alert(1))",
        "[click][1]\n\n[1]: javascript:alert(1)",
        "![i](data:text/html;base64,PHNjcmlwdD4=)",
        "[i](vbscript:msgbox)",
    ],
)
def test_no_address_that_runs_something_reaches_the_page(source: str) -> None:
    """The one hole a markdown parser leaves, closed for every shape it comes in."""
    assert urls_in(md.render(source)) == []


def test_an_ordinary_link_is_left_alone() -> None:
    html = md.render("[the manual](https://example.com/manual)")
    assert urls_in(html) == ["https://example.com/manual"]


def test_relative_and_anchor_links_work_inside_the_library() -> None:
    assert urls_in(md.render("[other note](./other.md)")) == ["./other.md"]
    assert urls_in(md.render("[section](#valves)")) == ["#valves"]


def test_a_picture_kept_as_data_is_still_a_picture() -> None:
    # `data:image/...` is an inline picture, not a page written inside another page
    urls = urls_in(md.render("![dot](data:image/png;base64,iVBORw0KGgo=)"))
    assert urls == ["data:image/png;base64,iVBORw0KGgo="]


def test_the_rendering_never_raises_even_on_nonsense() -> None:
    for weird in ("", "|", "\x00\x01\x02", "#" * 400, "[", "![](", "|a|\n|-|\n|b|c|d|"):
        out = md.render(weird)
        assert isinstance(out, str)


# -- the one-line form --------------------------------------------------------


def test_a_summary_is_the_words_without_the_markup() -> None:
    text = "# Cellar pump\n\nThe **sump** starts twice a night.\n\n- one\n- two\n"
    assert md.summary(text) == "Cellar pump The sump starts twice a night. one two"


def test_a_long_summary_is_cut_and_says_so() -> None:
    long = "word " * 100
    short = md.summary(long, length=40)
    assert len(short) <= 41
    assert short.endswith("…")


def test_an_empty_file_summarises_to_nothing() -> None:
    assert md.summary("") == ""
    assert md.summary("   \n\n  ") == ""


# -- the parser is built once -------------------------------------------------


def test_the_same_parser_answers_every_time() -> None:
    assert md.parser() is md.parser()


def test_the_extra_rules_are_the_only_ones_switched_on() -> None:
    # autolink is a flavour choice nobody asked for: a bare domain stays text
    assert 'href="www.example.com"' not in md.render("www.example.com")


# -- through the page ---------------------------------------------------------


def test_the_page_asks_the_server_for_the_rendering(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from boxganizer.models import Box
    from boxganizer.store import BoxStore
    from boxganizer.web.app import create_app

    note = tmp_path / "note.md"
    note.write_text("# Boilermaker\n\nCheck the **pressure**.", encoding="utf-8")
    store = BoxStore(tmp_path / "md.db", content_root=tmp_path)
    try:
        store.add(Box(name="Note", box_type="markdown", resource_uri=str(note)))
        with TestClient(create_app(store, content_root=tmp_path)) as client:
            answer = client.get("/api/boxes/Note/markdown")
            assert answer.status_code == 200
            body = answer.json()
            assert "<h1>Boilermaker</h1>" in body["html"]
            assert body["note"] == ""
            # the source came with it, so switching views costs nothing
            assert body["source"].startswith("# Boilermaker")
    finally:
        store.close()


def test_a_file_that_cannot_be_read_says_so_instead_of_rendering_nothing(
    tmp_path: Path,
) -> None:
    from fastapi.testclient import TestClient

    from boxganizer.models import Box
    from boxganizer.store import BoxStore
    from boxganizer.web.app import create_app

    store = BoxStore(tmp_path / "md2.db", content_root=tmp_path)
    try:
        store.add(Box(name="Gone", box_type="markdown", resource_uri=str(tmp_path / "gone.md")))
        # a box that may hold no resource still has to say why it renders nothing
        store.add(Box(name="Empty", box_type="folder"))
        with TestClient(create_app(store, content_root=tmp_path)) as client:
            gone = client.get("/api/boxes/Gone/markdown").json()["note"]
            assert "not there any more" in gone
            empty = client.get("/api/boxes/Empty/markdown").json()["note"]
            assert empty == "the box points at no file"
    finally:
        store.close()


def test_a_file_outside_the_confine_is_not_rendered(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from boxganizer.models import Box
    from boxganizer.store import BoxStore
    from boxganizer.web.app import create_app

    inside = tmp_path / "inside"
    inside.mkdir()
    elsewhere = tmp_path / "elsewhere.md"
    elsewhere.write_text("# private", encoding="utf-8")
    store = BoxStore(inside / "md3.db", content_root=inside)
    try:
        store.add(Box(name="Outside", box_type="markdown", resource_uri=str(elsewhere)))
        with TestClient(create_app(store, content_root=inside)) as client:
            answer = client.get("/api/boxes/Outside/markdown").json()
            assert answer["html"] == ""
            assert "outside" in answer["note"]
    finally:
        store.close()


# -- a file inside a directory box -------------------------------------------


def _library(tmp_path: Path) -> BoxStore:
    """A directory box over a folder with a markdown file in it, and one outside it."""
    from boxganizer.models import Box

    library = tmp_path / "library"
    (library / "notes").mkdir(parents=True)
    (library / "notes" / "plan.md").write_text(
        "# Pump\n\nbled the **radiator**\n", encoding="utf-8"
    )
    (library / "outside.md").write_text("# not yours\n", encoding="utf-8")
    store = BoxStore(tmp_path / "dirs.db", content_root=library)
    store.add(Box(name="Notes", box_type="indexed-directory", resource_uri=str(library / "notes")))
    store.add(Box(name="Cellar", box_type="folder", resource_uri=str(library / "notes")))
    return store


def test_a_markdown_file_inside_a_directory_box_renders(tmp_path: Path) -> None:
    """It is not a box, so it is asked for by the directory that holds it.

    Before this, the preview sent a `null` uuid as the reference and the answer was
    `box_not_found` — which reads like a lost box and is really a file that never was one.
    """
    from fastapi.testclient import TestClient

    from boxganizer.web.app import create_app

    store = _library(tmp_path)
    try:
        with TestClient(create_app(store, content_root=store.path.parent)) as client:
            answer = client.get("/api/boxes/Notes/markdown", params={"sub": "plan.md"})
            assert answer.status_code == 200
            assert "<h1>Pump</h1>" in answer.json()["html"]
            assert "radiator" in answer.json()["source"]
    finally:
        store.close()


def test_a_preview_cannot_walk_out_of_the_directory_box(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from boxganizer.web.app import create_app

    store = _library(tmp_path)
    try:
        with TestClient(create_app(store, content_root=store.path.parent)) as client:
            for attempt in ("../outside.md", "notes/../../outside.md", "/etc/passwd"):
                answer = client.get("/api/boxes/Notes/markdown", params={"sub": attempt})
                assert answer.status_code == 403, attempt
                assert answer.json()["detail"]["error"] == "path_out_of_range"
    finally:
        store.close()


def test_a_directory_box_that_holds_no_folder_says_which_problem_it_is(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from boxganizer.web.app import create_app

    store = _library(tmp_path)
    try:
        with TestClient(create_app(store, content_root=store.path.parent)) as client:
            # a folder box holds boxes, not a file tree: it has no such thing as a sub
            wrong = client.get("/api/boxes/Cellar/markdown", params={"sub": "plan.md"})
            assert wrong.status_code == 415
            missing = client.get("/api/boxes/Notes/markdown", params={"sub": "gone.md"})
            assert missing.status_code == 404
            assert missing.json()["detail"]["error"] == "no_such_path"
    finally:
        store.close()
