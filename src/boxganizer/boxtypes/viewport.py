"""Viewport boxes: windows onto other boxes.

A viewport stores no content of its own — it decides what else to show. Nothing
is rendered yet; the types exist so that "put a tiling viewport here" is already
a thing you can say, and so renderers have something to attach to.
"""

from __future__ import annotations

from typing import ClassVar

from boxganizer.boxtypes.base import BoxType


class ViewportBox(BoxType):
    """Base for boxes that display other boxes instead of holding their own."""

    key: ClassVar[str] = "viewport"
    label: ClassVar[str] = "Viewport"
    icon: ClassVar[str] = "\U0001f39b️"  # 🎛️
    summary: ClassVar[str] = "A window on the rest of the graph."
    abstract: ClassVar[bool] = True
    allows_children: ClassVar[bool] = True


class SimpleViewport(ViewportBox):
    """One box, shown whole."""

    key: ClassVar[str] = "simple-viewport"
    label: ClassVar[str] = "Simple Viewport"
    icon: ClassVar[str] = "\U0001f441️"  # 👁️
    summary: ClassVar[str] = "Shows one target box."
    planned: ClassVar[str] = "Keep metadata['target'] and render that box here."


class TilingViewport(ViewportBox):
    """Several boxes, packed without overlap."""

    key: ClassVar[str] = "tiling-viewport"
    label: ClassVar[str] = "Tiling Viewport"
    icon: ClassVar[str] = "\U0001f537"  # 🔷
    summary: ClassVar[str] = "Tiles its children, no overlap."
    planned: ClassVar[str] = "Split rules (rows, columns, ratios) and a layout pass."


class WindowedViewport(ViewportBox):
    """Several boxes, floating and overlapping."""

    key: ClassVar[str] = "windowed-viewport"
    label: ClassVar[str] = "Windowed Viewport"
    icon: ClassVar[str] = "\U0001fa9f"  # 🪟
    summary: ClassVar[str] = "Windows that can overlap, drag and stack."
    planned: ClassVar[str] = "Per-child position and z-order; focus handling."
