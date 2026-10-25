"""Organizer boxes: they arrange the boxes they contain.

Nothing here draws anything yet. Each type declares how it intends to lay its
children out, which is what the interfaces will read once they grow renderers.
"""

from __future__ import annotations

from typing import ClassVar

from boxganizer.boxtypes.base import BoxType


class OrganizerBox(BoxType):
    """Base for boxes whose point is the arrangement of their children."""

    key: ClassVar[str] = "organizer"
    label: ClassVar[str] = "Organizer"
    icon: ClassVar[str] = "\U0001f9f0"  # 🧰
    summary: ClassVar[str] = "Puts its children somewhere on purpose."
    abstract: ClassVar[bool] = True
    allows_children: ClassVar[bool] = True


class Desktop(OrganizerBox):
    """Free placement: children sit at coordinates the user chose."""

    key: ClassVar[str] = "desktop"
    label: ClassVar[str] = "Desktop"
    icon: ClassVar[str] = "\U0001f5a5️"  # 🖥️
    summary: ClassVar[str] = "An open surface where each box goes where you put it."
    #: The resource is the wallpaper, so the interfaces may read it.
    serves_content: ClassVar[bool] = True
    #: Children are placed at chosen coordinates, which is what makes "put the new
    #: box where I right-clicked" a question this type can answer.
    arranges_icons: ClassVar[bool] = True


class Blueprint(Desktop):
    """A saved layout that other desktops can start from."""

    key: ClassVar[str] = "blueprint"
    label: ClassVar[str] = "Blueprint"
    icon: ClassVar[str] = "\U0001f4d0"  # 📐
    summary: ClassVar[str] = "A desktop layout, kept to be reused."
    allows_children: ClassVar[bool] = True
    planned: ClassVar[str] = (
        "Instantiate a Desktop from this layout, keeping or cloning the children."
    )


class Model3D(OrganizerBox):
    """Place boxes in a 3D environment.

    Named ``Model3D`` because a Python class name cannot start with a digit;
    its public key and label are still ``3d-model`` and "3D Model".
    """

    key: ClassVar[str] = "3d-model"
    label: ClassVar[str] = "3D Model"
    icon: ClassVar[str] = "\U0001f9ca"  # 🧊
    summary: ClassVar[str] = "Children positioned in a 3D scene."
    planned: ClassVar[str] = (
        "Store x/y/z per child; needs a renderer, which is a decision not taken yet."
    )


class Grid(OrganizerBox):
    """Children on rows and columns."""

    key: ClassVar[str] = "grid"
    label: ClassVar[str] = "Grid"
    icon: ClassVar[str] = "\U0001f533"  # 🔳
    summary: ClassVar[str] = "A regular grid of children."
    planned: ClassVar[str] = (
        "Column count in metadata['columns']; order follows the link timestamp."
    )


class Mindmap(OrganizerBox):
    """Children radiate from the centre as nodes of a map.

    The map *is* the containment graph: a node is a box, a branch is that box's
    children, and everything the page does to the picture — a new node, a box dragged
    onto one, a branch sent to the other side — is an ordinary link and an ordinary
    box. Only which side a branch grows on, and the order of siblings, are the map's
    own business, and they live in `config.mindmap` (see :mod:`boxganizer.mindmap`).
    """

    key: ClassVar[str] = "mindmap"
    label: ClassVar[str] = "Mindmap"
    icon: ClassVar[str] = "\U0001f578️"  # 🕸️
    summary: ClassVar[str] = "One idea in the middle, branches of thought around it."
    maps_nodes: ClassVar[bool] = True


class Graph(OrganizerBox):
    """A network of boxes and edges."""

    key: ClassVar[str] = "graph"
    label: ClassVar[str] = "Graph"
    icon: ClassVar[str] = "\U0001f9e9"  # 🧩
    summary: ClassVar[str] = "Nodes and edges, drawn however the subtype decides."
    planned: ClassVar[str] = "A shared drawing model that both subtypes feed."


class ManualGraph(Graph):
    """The user draws the edges."""

    key: ClassVar[str] = "manual-graph"
    label: ClassVar[str] = "Manual Graph"
    icon: ClassVar[str] = "\u270d️"  # ✍️
    summary: ClassVar[str] = "Edges the user placed by hand, independent of containment."
    planned: ClassVar[str] = "Store hand-drawn edges apart from parent/child links."


class ParentalGraph(Graph):
    """The edges are the containment links."""

    key: ClassVar[str] = "parental-graph"
    label: ClassVar[str] = "Parental Graph"
    icon: ClassVar[str] = "\U0001f333"  # 🌳
    summary: ClassVar[str] = "Draws the parent/child links it already has."
    planned: ClassVar[str] = "Render the subtree rooted at this box."


class Dashboard(OrganizerBox):
    """Several small views, one screen."""

    key: ClassVar[str] = "dashboard"
    label: ClassVar[str] = "Dashboard"
    icon: ClassVar[str] = "\U0001f4ca"  # 📊
    summary: ClassVar[str] = "Widgets fed by other boxes."
    planned: ClassVar[str] = "Widget list per child plus a layout, once renderers exist."


class CheckList(OrganizerBox):
    """Items you tick off."""

    key: ClassVar[str] = "checklist"
    label: ClassVar[str] = "Checklist"
    icon: ClassVar[str] = "\u2705"  # ✅
    summary: ClassVar[str] = "Children are items; the tick lives on the child."
    planned: ClassVar[str] = "Read and set metadata['done'] on children; show a progress count."
