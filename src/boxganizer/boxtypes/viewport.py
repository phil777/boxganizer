"""Viewport boxes: windows onto other boxes.

A viewport stores no content of its own — it decides what else to show. Opening a box in an
ordinary folder takes the whole pane and leaves the parent as a crumb, which is right for filing
and wrong for comparison: two directories, or a note and the pdf it cites, cannot be looked at
together. A viewport box keeps its space and lends it out, one box per pane, each pane with its
own path line.

The layout lives in the box's own `config` and the model behind it is
`boxganizer.viewports` — a binary tree for tiling, a list of rectangles for windows. The boxes a
viewport shows are its **children**, so `box tree`, the side pane and the page agree about the
filing; the config only says which pane draws which.
"""

from __future__ import annotations

from typing import ClassVar

from boxganizer import viewports
from boxganizer.boxtypes.base import BoxType
from boxganizer.models import Box


class ViewportBox(BoxType):
    """Base for boxes that display other boxes instead of holding their own."""

    key: ClassVar[str] = "viewport"
    label: ClassVar[str] = "Viewport"
    icon: ClassVar[str] = "\U0001f39b️"  # 🎛️
    summary: ClassVar[str] = "A window on the rest of the graph."
    abstract: ClassVar[bool] = True
    allows_children: ClassVar[bool] = True
    #: Which layout this kind of viewport draws: "tiling" or "windowed". The API asks the type
    #: rather than reading `config`, because the config is the person's and this is the type's.
    viewport_layout: ClassVar[str | None] = None

    @classmethod
    def check(cls, box: Box) -> None:
        """The layout in the config has to be one this kind of viewport could draw.

        One check on the base, asking `cls.viewport_layout` — not a copy per subclass. The tiling
        type used to override it and did so without the kind, which is how a windowed box holding a
        tiling tree slipped through: an override that replaces its parent's rule is a rule removed,
        and layout validation is exactly the kind of rule nobody wants quietly dropped.
        """
        problem = viewports.check(box.config, None, cls.viewport_layout)
        if problem:
            cls._reject(box, problem)


class SimpleViewport(ViewportBox):
    """One box, shown whole."""

    key: ClassVar[str] = "simple-viewport"
    label: ClassVar[str] = "Simple Viewport"
    icon: ClassVar[str] = "\U0001f441️"  # 👁️
    summary: ClassVar[str] = "Shows one target box."
    planned: ClassVar[str] = "Keep metadata['target'] and render that box here."


class TilingViewport(ViewportBox):
    """Several boxes, packed without overlap: split a pane, drag the bar, close a pane."""

    key: ClassVar[str] = "tiling-viewport"
    label: ClassVar[str] = "Tiling Viewport"
    icon: ClassVar[str] = "\U0001f537"  # 🔷
    summary: ClassVar[str] = "One space, split into panes that tile it."
    #: How the space is divided and what each pane shows (ADR 23).
    viewport_layout: ClassVar[str | None] = "tiling"


class WindowedViewport(ViewportBox):
    """Several boxes, floating and overlapping, where the person left them."""

    key: ClassVar[str] = "windowed-viewport"
    label: ClassVar[str] = "Windowed Viewport"
    icon: ClassVar[str] = "\U0001fa9f"  # 🪟
    summary: ClassVar[str] = "Windows that move, resize, and come back."
    viewport_layout: ClassVar[str | None] = "windowed"

    @classmethod
    def check(cls, box: Box) -> None:
        """The layout, and how see-through the windows are asked to be.

        A transparency that hides the box inside is not a window; the floor is where the contents
        stop being readable. It lives here rather than in the layout because the layout is rewritten
        on every split, move and resize, and this is not part of where the windows are.
        """
        problem = viewports.check(
            box.config, None, cls.viewport_layout
        ) or viewports.opacity_problem(box.config)
        if problem:
            cls._reject(box, problem)
