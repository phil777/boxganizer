"""Folder boxes: containers whose only job is to hold other boxes."""

from __future__ import annotations

from typing import ClassVar

from boxganizer.boxtypes.base import BoxType


class FolderBox(BoxType):
    """A plain folder. This is what an untyped box already behaves like."""

    key: ClassVar[str] = "folder"
    label: ClassVar[str] = "Folder"
    icon: ClassVar[str] = "\U0001f4c2"  # 📂
    summary: ClassVar[str] = "Holds boxes, nothing else."
    allows_children: ClassVar[bool] = True


class DynamicFolder(FolderBox):
    """A folder whose members are computed, not chosen."""

    key: ClassVar[str] = "dynamic-folder"
    label: ClassVar[str] = "Dynamic Folder"
    icon: ClassVar[str] = "\U0001f300"  # 🌀
    summary: ClassVar[str] = "Members come from a saved query."
    planned: ClassVar[str] = (
        "Store a search expression in metadata['query'] and resolve it on read."
    )


class Cabinet(FolderBox):
    """A folder with a fixed shape: named slots, filled one at a time."""

    key: ClassVar[str] = "cabinet"
    label: ClassVar[str] = "Cabinet"
    icon: ClassVar[str] = "\U0001f5c4️"  # 🗄️
    summary: ClassVar[str] = "Drawers with a defined order."
    planned: ClassVar[str] = "Ordered slots; refuse a child that names no slot."
