"""The root of a library.

Not in the source document: every Boxganizer database needs one place where the
graph starts, so `box init` creates a box of this type. It is a singleton — a
second one is refused by the store.
"""

from __future__ import annotations

from typing import ClassVar

from boxganizer.boxtypes.folder import FolderBox

#: Name every initialised library gives its root box.
ROOT_BOX_NAME = "root"


class RootBox(FolderBox):
    """The box everything else hangs from."""

    key: ClassVar[str] = "root"
    label: ClassVar[str] = "Root"
    icon: ClassVar[str] = "\U0001f332"  # 🌲
    summary: ClassVar[str] = "The top of this library; created by `box init`."
    allows_children: ClassVar[bool] = True
    #: Only one root per database.
    singleton: ClassVar[bool] = True
    #: Nothing may contain this box.
    is_root: ClassVar[bool] = True
    planned: ClassVar[str] = (
        "Already the default parent of new boxes and the view the app opens on."
    )
