"""The documentation is a claim about the repository, so check it like one.

A README is the one file that rots silently: nothing fails when a link moves, an image is
deleted, or a screenshot stops being referenced. These tests are deliberately about the
mechanics — paths, anchors, and pictures that exist — because those are the failures nobody
notices until a reader hits them.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"

#: Where the markdown link syntax puts a target, and where an image puts its path.
LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)")
HEADING = re.compile(r"^#{1,6}\s+(.*)$", re.MULTILINE)
SCHEME = "boxganizer"

#: The screenshots the README is built around. `make shots` writes these names; the tour
#: breaks visibly if one is missing, so the pairing is asserted in both directions.
SHOTS = (
    "web-library-note.png",
    "web-grid.png",
    "web-filter.png",
    "web-viewport.png",
)


def anchors_of(path: Path) -> set[str]:
    """The `#fragment` values a file can be linked to, the way GitHub computes them."""
    found = set()
    for title in HEADING.findall(path.read_text(encoding="utf-8")):
        slug = re.sub(r"[^\w\s-]", "", title.lower()).strip().replace(" ", "-")
        found.add(slug)
    return found


def test_every_local_link_in_the_readme_resolves() -> None:
    """No link to a file that moved, and no anchor to a heading that was renamed.

    The anchor check is the one worth having: a renamed section leaves every link to it looking
    perfectly ordinary in the editor.
    """
    targets = LINK.findall(README.read_text(encoding="utf-8"))
    # `boxganizer:` is the app's own scheme, quoted in prose as a literal the reader types.
    external = ("http://", "https://", "mailto:", "#", f"{SCHEME}:")
    local = [t for t in targets if not t.startswith(external)]
    assert local, "the README should link into doc/ at all"
    for target in local:
        path_part, _, fragment = target.partition("#")
        resolved = (ROOT / path_part).resolve()
        assert resolved.exists(), f"README links a file that is not there: {target}"
        if fragment:
            assert fragment in anchors_of(resolved), (
                f"README links #{fragment} in {path_part}, which has no such heading"
            )


def test_every_local_link_in_the_docs_resolves() -> None:
    """The same mechanic for the files the README hands the reader off to.

    Relative links inside `doc/` are resolved from the file they are written in, which is the
    mistake this catches: a link to `doc/web-app.md#…` copied into `doc/web-app.md` itself points at
    `doc/doc/`, looks fine in the editor, and 404s for the reader.
    """
    checked = 0
    for page in sorted((ROOT / "doc").rglob("*.md")):
        for target in LINK.findall(page.read_text(encoding="utf-8")):
            if target.startswith(("http://", "https://", "mailto:", "#", f"{SCHEME}:")):
                continue
            path_part, _, fragment = target.partition("#")
            resolved = (page.parent / path_part).resolve()
            assert resolved.exists(), (
                f"{page.relative_to(ROOT)} links a file that is not there: {target}"
            )
            if fragment:
                assert fragment in anchors_of(resolved), (
                    f"{page.relative_to(ROOT)} links #{fragment} in {path_part}, "
                    "which has no such heading"
                )
            checked += 1
    assert checked, "no local links found anywhere under doc/ — the check would be vacuous"


def test_the_four_screenshots_exist_and_are_pictures_of_something() -> None:
    """A committed PNG that is a few kilobytes is a broken capture, not a screenshot.

    The size floor is loose on purpose: it catches the empty frame, not the composition. What
    keeps each picture honest about its subject is the DOM assertions in `tools/shot_docs.py`.
    """
    assets = ROOT / "doc" / "assets"
    for name in SHOTS:
        shot = assets / name
        assert shot.exists(), f"{name} is missing — run `make shots`"
        assert shot.stat().st_size > 50_000, (
            f"{name} is {shot.stat().st_size} bytes; that is not a screenshot"
        )
        assert shot.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n", f"{name} is not a PNG"


def test_the_readme_references_each_screenshot_once() -> None:
    """An orphaned asset is a lie about the tour, and a missing one is a broken image.

    Both directions are checked: all four pictures the script takes appear, and nothing else
    from `doc/assets/` is referenced — so a picture that stops belonging to the tour is noticed
    rather than left to age quietly.
    """
    text = README.read_text(encoding="utf-8")
    referenced = re.findall(r"\((doc/assets/[^)]+)\)", text)
    for name in SHOTS:
        count = referenced.count(f"doc/assets/{name}")
        assert count == 1, f"{name} should appear exactly once, saw {count}"
    for target in referenced:
        leaf = target.rsplit("/", 1)[-1]
        assert leaf in SHOTS, f"{target} is not one of the four the script takes"


def test_the_readme_keeps_the_three_disclosures_at_the_top() -> None:
    """The warnings are the first thing the project says about itself; they stay first.

    They were added by hand above the title, where a rewrite would otherwise smooth them out.
    """
    opening = README.read_text(encoding="utf-8").splitlines()[:4]
    assert opening[0] == "- *Warning*: 100% vibe-coded"
    assert opening[1] == "- *Warning*: access to web app = shell with web server uid"
    assert opening[2] == "- *Disclaimer*: Works-for-me^TM"
    assert opening[3] == ""


def test_the_docs_answer_all_three_drop_questions() -> None:
    """Drop a file, drop a link, drop something that is neither: the docs say what happens.

    A drag is the one gesture whose outcome depends on a payload nobody can see, so the page's
    promise has to be written down where a reader can check it before trying. This asserts the three
    answers exist in both the tour and the reference, and that the ADR that made them is reachable —
    the wording drifts otherwise, and the drift is invisible until someone drops an image and gets a
    browser tab.
    """
    web_app = (ROOT / "doc" / "web-app.md").read_text(encoding="utf-8")
    readme = README.read_text(encoding="utf-8")

    for label, text in (("doc/web-app.md", web_app), ("README.md", readme)):
        assert "link" in text.lower(), f"{label} never says what a dropped link does"
        assert "nothing is fetched" in text.lower() or "fetches nothing" in text.lower(), (
            f"{label} does not say the server stays away from a dropped address"
        )
        assert "captured" in text.lower(), f"{label} does not say a dropped file is captured"
        assert "stray" in text.lower() or "Miss every zone" in text, (
            f"{label} does not say what a drop that hits no zone does"
        )

    adr = ROOT / "doc" / "adr" / "0038-a-drop-zone-may-promise-only-what-it-can-finish.md"
    assert adr.exists(), "ADR 38 is gone, and it is the only place the gate's reasoning lives"
    body = adr.read_text(encoding="utf-8")
    for heading in ("## Context", "## Decision", "## Consequences"):
        assert heading in body, f"ADR 38 has no {heading}"
    assert "types" in body and "preventDefault" in body, "ADR 38 lost the actual technical fork"
    assert "0038" in (ROOT / "doc" / "adr" / "README.md").read_text(encoding="utf-8"), (
        "ADR 38 is not in the index, so nobody finds it"
    )


def test_the_docs_answer_the_five_questions_this_round_asked() -> None:
    """Five behaviours changed; each has to be readable in the tour *and* the reference.

    Not prose policing — these are the sentences a person needs before they try the gesture, and the
    kind that rot by paraphrase rather than deletion: a document that still describes the old popup,
    or the old `duplicate_name` error, is worse than none because it is trusted.
    """
    # Prose is compared with runs of whitespace collapsed: a sentence that wraps mid-phrase is the
    # same sentence, and a test that fails on a line break trains everyone to delete it.
    def flat(text: str) -> str:
        # markdown emphasis stripped too: **About** last and About last are one sentence
        return " ".join(text.replace("*", "").split())

    readme = flat(README.read_text(encoding="utf-8"))
    web_app = flat((ROOT / "doc" / "web-app.md").read_text(encoding="utf-8"))

    checks = {
        # a dropped resource whose name is taken is settled by its bytes
        "content decides a taken name": ("already in the library", "next number"),
        # folder-type search narrows the list; no window over it
        "live filter, no dropdown": ("nothing unrolls", "match inside"),
        # the term a box was opened with is carried, for every kind of box
        "the term is carried": ("carrying the term with it", "no list to narrow"),
        # the mouse back button climbs the main pane and never leaves
        "back button climbs": ("back button", "up one level"),
        # ☰ order, with About last
        "menu order": ("About last", "a line, and About last"),
    }
    for label, needles in checks.items():
        for text, name in ((readme, "README.md"), (web_app, "doc/web-app.md")):
            for needle in needles:
                assert needle in text, (
                    f"{name} does not say anything about {label} (looked for {needle!r})"
                )

    # the two ADRs this round turned on exist and are indexed
    index = (ROOT / "doc" / "adr" / "README.md").read_text(encoding="utf-8")
    for number, slug in (
        ("0039", "0039-a-search-field-with-a-list-under-it-narrows-the-list"),
        ("0040", "0040-a-drag-announces-its-kind-in-the-type-list"),
    ):
        assert (ROOT / "doc" / "adr" / f"{slug}.md").exists(), f"ADR {number} is missing"
        assert number in index, f"ADR {number} is not in the index"


def test_no_document_still_points_at_a_control_that_was_deleted() -> None:
    """A guide that describes a removed control is worse than no guide, because it is trusted.

    The "drop resources here" strip is gone (ADR 45): the whole left pane took its place. Two
    shipped documents told the reader to aim at the strip, in its own words, and nothing failed.
    That is the rot this catches — not the phrasing of a sentence, but a sentence that sends a
    person looking for something that is not on the screen.
    """
    for rel in ("README.md", "doc/web-app.md"):
        text = (ROOT / rel).read_text(encoding="utf-8").lower()
        assert "drop resources here" not in text, f"{rel} still advertises the deleted strip"
        assert "landing strip" not in text, f"{rel} still describes the pane as having a strip"


def test_the_pane_and_its_index_dialog_are_readable_in_both_documents() -> None:
    """Three behaviours changed this round; each must be findable in the tour *and* the reference.

    Same rule as the round before it: these are the sentences a person needs before trying the
    gesture, and they rot by paraphrase, not by deletion. Compared with whitespace runs collapsed
    and markdown emphasis stripped, because a sentence that wraps mid-phrase is the same sentence.
    """

    def flat(text: str) -> str:
        return " ".join(text.replace("*", "").split())

    readme = flat(README.read_text(encoding="utf-8"))
    web_app = flat((ROOT / "doc" / "web-app.md").read_text(encoding="utf-8"))

    checks = {
        # the strip is gone and the pane is the target
        "whole-pane drop": ("whole left pane", "no strip to aim at"),
        # the dialog is named, and gone resources are named separately from work
        "Index status, with gone kept out of the offer": (
            "Index status",
            "gone from this machine",
        ),
        # the tree folds on filing, not on containers
        "a tree that folds": ("folded under the folder it was filed in",),
        # and the pane keeps the person's own shape
        "the pane remembers": ("which folders were open",),
    }
    for label, needles in checks.items():
        for text, name in ((readme, "README.md"), (web_app, "doc/web-app.md")):
            for needle in needles:
                assert needle in text, f"{name} says nothing about {label} (looked for {needle!r})"


def test_every_adr_written_is_in_the_index() -> None:
    """An ADR nobody can find is a decision nobody follows.

    The index stopped at 0040 while five more were written, and the only guard was a list of
    numbers this round happened to mention. This reads the directory instead: a new record must be
    indexed, whoever wrote it and whatever it is about.
    """
    index = (ROOT / "doc" / "adr" / "README.md").read_text(encoding="utf-8")
    numbers = sorted(path.name[:4] for path in (ROOT / "doc" / "adr").glob("0*.md"))
    assert len(numbers) >= 45, f"the ADR directory looks truncated: {len(numbers)} files"
    missing = [number for number in numbers if number not in index]
    assert not missing, f"ADR {', '.join(missing)} is written but not in doc/adr/README.md"


#: Wording the tour and the reference put in code spans, quoted as what the page says, with the
#: template in app.js that produces it. A document that quotes wording the page no longer emits is
#: the failure this exists for: `2 out of date` sat in both files after the code stopped saying it,
#: and the only copy that still did was an untracked `build/` directory nobody reads.
QUOTED_PAGE_WORDING = {
    "`75 indexed`": "} indexed",
    "`2 to reindex`": " to reindex",
    "`reading 7 of 12…`": "reading ${queue.done} of ${queue.total}",
    "`7 / 12`": "${queue.done} / ${queue.total}",
    "`0 read`": "${queue.done} read",
    "`reading…`": '"reading…"',
    "`2 of 4 inside`": "of ${children.length} inside",
    "`1 match inside`": "1 match inside",
}


def test_wording_quoted_as_the_pages_own_comes_from_the_page() -> None:
    """Every code-spanned sentence fragment in the docs has to be reachable in app.js."""
    script = (ROOT / "src" / "boxganizer" / "web" / "static" / "app.js").read_text(
        encoding="utf-8"
    )
    docs = {
        "README.md": " ".join(README.read_text(encoding="utf-8").replace("*", "").split()),
        "doc/web-app.md": " ".join(
            (ROOT / "doc" / "web-app.md").read_text(encoding="utf-8").replace("*", "").split()
        ),
    }
    for quoted, template in QUOTED_PAGE_WORDING.items():
        assert template in script, f"app.js no longer produces {quoted} ({template!r} is gone)"
        somewhere = any(quoted in text for text in docs.values())
        assert somewhere, f"nothing quotes {quoted} any more; retire it from this list"

    # the stale wording, named so it cannot come back through a document
    assert "out of date`" not in script, "app.js emits `out of date` again — check the docs"
    for name, text in docs.items():
        assert "out of date`" not in text, f"{name} quotes wording the page does not produce"
