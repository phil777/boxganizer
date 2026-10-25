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
    """A folder whose members are computed from a filter over all the boxes.

    The filter lives in `config['filter']` and is read by :mod:`boxganizer.filters`.
    Membership is written into the links table rather than computed every time the
    folder is listed, so a dynamic folder appears in `box show`, in the tree and in
    every box's `in` list like any other container: the filter says who belongs and
    the links say who belongs *now*.

    What belongs is therefore not chosen by hand. Linking a box in by hand is
    allowed and lasts until the next time the folder is resolved — which happens
    whenever the filter is edited or any box changes — and a box the filter does not
    select is taken back out.
    """

    key: ClassVar[str] = "dynamic-folder"
    label: ClassVar[str] = "Dynamic Folder"
    icon: ClassVar[str] = "\U0001f300"  # 🌀
    summary: ClassVar[str] = "Members are every box matching a saved filter."
    #: The store keeps the links of this type in step with its filter.
    resolves_membership: ClassVar[bool] = True


class Cabinet(DynamicFolder):
    """The dynamic folder that filters nothing: it holds every box there is.

    It asks for everything, so it is the whole library in one place — including
    itself, which the structure allows. The root box is the one exception, because
    nothing may file the root: that rule holds the way in open, and the cabinet
    does not override it.

    A cabinet carries no filter of its own: whatever is left in its config is
    ignored, so an edited cabinet cannot quietly stop being the whole library.
    """

    key: ClassVar[str] = "cabinet"
    label: ClassVar[str] = "Cabinet"
    icon: ClassVar[str] = "\U0001f5c4️"  # 🗄️
    summary: ClassVar[str] = "Every box in the library, and itself."
    #: An empty filter means every box, and it is not open to editing.
    filter_is_fixed: ClassVar[bool] = True
