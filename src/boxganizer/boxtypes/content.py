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


class Label(ContentBox):
    """A short piece of text, meant to be read next to the boxes around it."""

    key: ClassVar[str] = "label"
    label: ClassVar[str] = "Label"
    icon: ClassVar[str] = "\U0001f3f7️"  # 🏷️
    summary: ClassVar[str] = "One or two lines of text shown beside other boxes."
    planned: ClassVar[str] = "Render inline in the text UI and the web page."
