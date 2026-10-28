"""The `boxganizer:` link: how a box is named inside somebody's text.

A note wants to say *the plumber I told you about* and mean a particular box. A checklist
wants a box hung on one line. Both need the same thing — a way to write a box's identity
into text that is also a file — and the answer is a markdown link whose scheme belongs to
this program:

    the plumber is [📋 Ruiz & Sons](boxganizer:2f6c…c19)

Four reasons it is written this way rather than as a comment, a custom tag, or a marker
like `{{box:2f6c…}}`.

**It is valid markdown, so nothing else has to be taught.** The renderer already emits an
`<a>` for it, the syntax highlighters already colour it, and `grep -o 'boxganizer:[0-9a-f-]*'`
over the notes directory lists every reference in the library. A private marker would work
in Boxganizer and be noise everywhere else, and the whole point of ADR 14 is that these
files are not Boxganizer's private property.

**The uuid is the reference; the words beside it are a courtesy.** The label is whatever
the author wrote or the box was called at the moment it was inserted, and it is allowed to
be wrong after a rename — a label is text, and text is allowed to age. The uuid is what
decides which box opens. Nothing here rewrites a label behind the author's back.

**The scheme survives the safety filter because it is inert.** `boxganizer:` names nothing
a browser can reach — no host, no file, no script — so `boxganizer.markdown` passes it
through untouched, alongside `http:`, `mailto:` and `#anchor`. The page turns those anchors
into chips and wires them to `openBox()` after the server's rendering is in the DOM, which
is the only place the uuid gains the power to navigate. A file that merely *mentions* a
uuid cannot reach out and open anything.

**A reference is not a filing.** The graph still decides what is inside what. When the page
writes text that mentions a box, it files the box here as well, because a note that names
a box and then loses it would be a broken link; but deleting the sentence does not unlink
the box, and unlinking the box leaves the sentence standing, greyed. The mention is a
quotation, and quotations are kept.
"""

from __future__ import annotations

import re
from typing import Any

#: The scheme a box reference is written with.
SCHEME = "boxganizer"

#: What a reference looks like once rendered: an anchor whose href starts with the scheme.
HTML_PREFIX = f'href="{SCHEME}:'

#: `[label](boxganizer:uuid)` in source text. The label may hold anything but a bracket;
#: the uuid is deliberately strict, so a hand-typed `boxganizer:` at the end of a sentence
#: does not read as a reference to nothing.
LINK = re.compile(rf"\[(?P<label>[^\]]*)\]\({SCHEME}:(?P<uuid>[0-9a-fA-F-]{{8,}})\)")


def link_for(box: Any, *, label: str | None = None) -> str:
    """The markdown that names *box* inside somebody's text.

    The label defaults to the icon and the name, which is what makes the raw file readable
    by eye and not only by the program that wrote it.
    """
    icon = (getattr(box, "icon", "") or "").strip()
    name = (label or getattr(box, "name", "") or "").strip()
    shown = f"{icon} {name}".strip() if icon else name
    return f"[{shown}]({SCHEME}:{box.uuid})"


def mentions(text: str) -> list[str]:
    """Every uuid referenced in *text*, in order of first mention, duplicates removed."""
    seen: list[str] = []
    for found in LINK.finditer(text or ""):
        uuid = found.group("uuid").lower()
        if uuid not in seen:
            seen.append(uuid)
    return seen


def labels(text: str) -> dict[str, str]:
    """The words written beside each reference, first mention wins.

    Used by nothing in the program itself: it is there so a caller can tell a uuid what it
    was called when the sentence was written, which is not always what it is called now.
    """
    found: dict[str, str] = {}
    for match in LINK.finditer(text or ""):
        uuid = match.group("uuid").lower()
        found.setdefault(uuid, match.group("label").strip())
    return found
