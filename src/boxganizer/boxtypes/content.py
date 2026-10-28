"""Content boxes: information that lives inside Boxganizer itself.

Scaffold only. What these types *will* do is recorded in ``planned``; today they
declare their shape and validation rules.
"""

from __future__ import annotations

from typing import ClassVar

from boxganizer.boxtypes.base import BoxType


class ContentBox(BoxType):
    """Base for boxes whose value is text the user typed here."""

    key: ClassVar[str] = "content"
    label: ClassVar[str] = "Content"
    icon: ClassVar[str] = "\U0001f4c4"  # 📄
    summary: ClassVar[str] = "Text written into Boxganizer rather than pointed at."
    abstract: ClassVar[bool] = True
    allows_children: ClassVar[bool] = False
    planned: ClassVar[str] = "Store the text itself (today it would go in description or metadata)."


class Note(ContentBox):
    """Words of one's own — a post-it with a place in the graph.

    Like the checklist, a note needs nothing when it is made and is given a file beside
    the database the first time there is something to write (see
    :mod:`boxganizer.authored` and ADR 14). Unlike the checklist, the text is free: plain,
    or markdown, with a toolbox that makes words bold and lines into lists — and a
    markdown note can be read rendered or as the text that made it.

    It holds children, and a child can be named *inside* the text as a
    `[📋 name](boxganizer:uuid)` link, which the page draws as an icon and opens on a
    click. The file stays readable, because the link is ordinary markdown on a scheme that
    reaches nothing by itself (see :mod:`boxganizer.boxrefs`). Naming a box in a note files
    it here too, so a note cannot quietly lose the thing it points at; taking the sentence
    away does not throw the box out.

    A note may also point at a file that already exists, and then it edits that file and
    nothing is written beside the database. Whether a note reads as markdown or as plain
    text is a property of the box, not of the file's ending: a note flipped from plain to
    markdown keeps its address, because moving the file would orphan the words already
    indexed under it.
    """

    key: ClassVar[str] = "note"
    label: ClassVar[str] = "Note"
    icon: ClassVar[str] = "\U0001f4dd"  # 📝
    summary: ClassVar[str] = "Words of your own, plain or markdown, with a toolbox."
    allows_children: ClassVar[bool] = True
    resource_uri_required: ClassVar[bool] = False
    #: The words are the point, so a note's file is read for the index by default.
    indexes_content: ClassVar[bool] = True
    writes_own_file: ClassVar[bool] = True


class Label(ContentBox):
    """A short piece of text, meant to be read next to the boxes around it."""

    key: ClassVar[str] = "label"
    label: ClassVar[str] = "Label"
    icon: ClassVar[str] = "\U0001f3f7️"  # 🏷️
    summary: ClassVar[str] = "One or two lines of text shown beside other boxes."
    planned: ClassVar[str] = "Render inline in the text UI and the web page."
