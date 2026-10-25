"""The boxes every library starts with.

`box init` seeds two boxes: **root** (type `root`), the top of the graph, and
**cabinet** (type `cabinet`), filed inside it. Seeding is idempotent, so running
`box init` against an existing library adds whatever is missing and nothing else.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from boxganizer import boxtypes
from boxganizer.boxtypes.root import ROOT_BOX_NAME
from boxganizer.models import Box

if TYPE_CHECKING:  # pragma: no cover - typing only
    from boxganizer.store import BoxStore

#: Name of the starter cabinet, and the type it is created with.
CABINET_BOX_NAME = "cabinet"
CABINET_BOX_TYPE = "cabinet"


def bootstrap(store: BoxStore) -> list[str]:
    """Create the starter boxes and the link between them.

    Returns the names actually created, so the caller can say what happened
    instead of claiming it every time.
    """
    created: list[str] = []

    if not store.exists(ROOT_BOX_NAME):
        root = Box(
            name=ROOT_BOX_NAME,
            box_type="root",
            description="Everything in this library starts here.",
        )
        store.add(boxtypes.adopt_type_icon(root))
        created.append(ROOT_BOX_NAME)

    if not store.exists(CABINET_BOX_NAME):
        cabinet = Box(
            name=CABINET_BOX_NAME,
            box_type=CABINET_BOX_TYPE,
            description="Every box in the library, and itself.",
        )
        store.add(boxtypes.adopt_type_icon(cabinet))
        created.append(CABINET_BOX_NAME)

    parents = store.parents(CABINET_BOX_NAME)
    if not parents or CABINET_BOX_NAME in created:
        # A cabinet the user filed somewhere else stays where they put it.
        store.link(ROOT_BOX_NAME, CABINET_BOX_NAME)

    return created
