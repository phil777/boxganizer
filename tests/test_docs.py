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
    local = [t for t in targets if not t.startswith(("http://", "https://", "mailto:", "#"))]
    assert local, "the README should link into doc/ at all"
    for target in local:
        path_part, _, fragment = target.partition("#")
        resolved = (ROOT / path_part).resolve()
        assert resolved.exists(), f"README links a file that is not there: {target}"
        if fragment:
            assert fragment in anchors_of(resolved), (
                f"README links #{fragment} in {path_part}, which has no such heading"
            )


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


def test_the_readme_keeps_the_three_disclosures_at_the_top() -> None:
    """The warnings are the first thing the project says about itself; they stay first.

    They were added by hand above the title, where a rewrite would otherwise smooth them out.
    """
    opening = README.read_text(encoding="utf-8").splitlines()[:4]
    assert opening[0] == "- *Warning*: 100% vibe-coded"
    assert opening[1] == "- *Warning*: access to web app = shell with web server uid"
    assert opening[2] == "- *Disclaimer*: Works-for-me^TM"
    assert opening[3] == ""
