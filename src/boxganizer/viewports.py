"""The layout inside a viewport box: which box is open in which pane, and how big each pane is.

A viewport box is the one kind of box that is never replaced by what you open in it. Opening a box
in an ordinary folder takes the whole pane and leaves the parent as a crumb in the path line, which
is right for filing and wrong for comparison: you cannot look at two directories, or a note and the
pdf it cites, at the same time.

Two kinds, two layouts, one place where the truth lives — the box's own `config`.

**Tiling.** A binary tree. It starts as one leaf taking everything; splitting a leaf replaces it
with a node holding two leaves, and the thin bar between them is what sets their ratio. Closing a
leaf replaces its parent node with the surviving sibling, so the space comes back exactly. A tree
was chosen over a list of rectangles because a list has no answer to "what happens to the space when
this one goes away" — the rectangles have to be re-derived by somebody, and that somebody turns out
to be a rule with edge cases, which is a tree wearing a disguise.

**Windowed.** Floating windows at a position and size each, which the person moves and resizes and
which come back where they were.

Both keep, per pane, the *uuid of at most one box* — the box that pane is showing. The links stay
the store's business: a box shown in a viewport is a child of the viewport box, so `box tree`, the
side pane and `box show` all see the same filing that the page paints. This module only answers
where things are drawn, and refuses a layout that says otherwise.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: The config section both kinds share.
SECTION = "viewports"

#: Ratios are clamped: a pane you cannot see is not a pane, and a divider dragged to the edge of
#: the world should leave something to drag back.
MIN_RATIO = 0.05
MAX_RATIO = 0.95

#: A ratio this far off the middle refuses to be rounded away, so a saved 0.5 and a saved 0.50001
#: are the same layout.
RATIO_PLACES = 4

Direction = str  # "horizontal" | "vertical"
Node = Any  # Leaf | Split, kept loose so the recursion stays readable


class ViewportError(ValueError):
    """A layout, or a request to change one, that does not make sense."""


@dataclass(frozen=True)
class Leaf:
    """One viewport: a pane on screen, showing at most one box."""

    node: str
    child: str = ""

    @property
    def is_leaf(self) -> bool:
        return True

    def as_dict(self) -> dict[str, object]:
        return {"node": self.node, "child": self.child}


@dataclass(frozen=True)
class Split:
    """Two viewports side by side (`horizontal`) or stacked (`vertical`)."""

    node: str
    direction: Direction
    ratio: float
    first: Node
    second: Node

    @property
    def is_leaf(self) -> bool:
        return False

    def as_dict(self) -> dict[str, object]:
        return {
            "node": self.node,
            "direction": self.direction,
            "ratio": round(self.ratio, RATIO_PLACES),
            "first": self.first.as_dict(),
            "second": self.second.as_dict(),
        }


@dataclass(frozen=True)
class Window:
    """One floating window in a windowed viewport."""

    node: str
    child: str = ""
    x: int = 0
    y: int = 0
    width: int = 480
    height: int = 320

    def as_dict(self) -> dict[str, object]:
        return {
            "node": self.node,
            "child": self.child,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }

    def moved_to(self, x: int, y: int) -> Window:
        return Window(self.node, self.child, max(0, x), max(0, y), self.width, self.height)

    def sized_to(self, width: int, height: int) -> Window:
        return Window(
            self.node,
            self.child,
            self.x,
            self.y,
            max(MIN_WINDOW, width),
            max(MIN_WINDOW // 2, height),
        )


#: A window smaller than this cannot hold a path line, let alone a box.
MIN_WINDOW = 120


@dataclass(frozen=True)
class Layout:
    """Everything a viewport box knows: which kind it is, and where its panes are."""

    kind: str
    tree: Node | None = None
    windows: tuple[Window, ...] = ()
    #: Window order, bottom to top. The last is the focused one; a click raises without changing
    #: what is where, because "raise" is about the pile, not about the layout.
    order: tuple[str, ...] = ()
    problems: tuple[str, ...] = field(default_factory=tuple)

    def panes(self) -> list[str]:
        """Every node id that draws a pane, left to right or bottom to top."""
        if self.kind == "windowed":
            return [window.node for window in self.windows]
        return [leaf.node for leaf in leaves(self.tree)] if self.tree else []

    def children(self) -> list[str]:
        """The boxes on screen, in pane order, with repeats kept.

        Repeats are kept on purpose: opening the same directory twice to compare it with itself
        after a rename is a thing people do, and a layout that silently merged the two would be
        the page deciding what a person may look at.
        """
        if self.kind == "windowed":
            return [window.child for window in self.windows if window.child]
        return [leaf.child for leaf in leaves(self.tree) if leaf.child] if self.tree else []

    def as_config(self, config: dict[str, Any] | None = None) -> dict[str, Any]:
        merged: dict[str, Any] = dict(config or {})
        section: dict[str, Any] = {"kind": self.kind}
        if self.tree is not None:
            section["tree"] = self.tree.as_dict()
        if self.kind == "windowed":
            section["windows"] = [window.as_dict() for window in self.windows]
            section["order"] = list(self.order)
        merged[SECTION] = section
        return merged


def new_tiling(child: str = "") -> Layout:
    """A tiling viewport box starts as one pane taking everything."""
    return Layout(kind="tiling", tree=Leaf("v1", child))


def new_windowed(child: str = "") -> Layout:
    """A windowed one starts with a single window, placed where a first window goes."""
    window = Window("v1", child, 24, 24, 640, 420)
    return Layout(kind="windowed", windows=(window,), order=("v1",))


# --------------------------------------------------------------------- reading a stored layout


def layout_of(config: dict[str, Any] | None, kind: str | None = None) -> Layout:
    """The layout a box carries, or a fresh one when it carries nothing usable.

    `kind` comes from the box's **type**: a windowed viewport with an empty config is windowed, and
    waiting for the config to say so would make every new windowed box open as a tiling one. A
    stored `kind` is still read (it is what older boxes carry); the type only fills the silence.

    Unreadable geometry degrades to a single pane and records the reason rather than raising: a box
    whose config was hand-edited into nonsense should open and show *something*, and the reason
    belongs on the box, not in a traceback. `check` is where nonsense is refused on the way in.
    """
    section = (config or {}).get(SECTION)
    if not isinstance(section, dict):
        return new_windowed() if kind == "windowed" else new_tiling()
    kind = str(section.get("kind") or kind or "tiling")
    if kind == "windowed":
        windows: list[Window] = []
        problems: list[str] = []
        for raw in section.get("windows", []) if isinstance(section.get("windows"), list) else []:
            try:
                windows.append(_window_from(raw))
            except ViewportError as exc:
                problems.append(str(exc))
        raw_order = section.get("order")
        listed = raw_order if isinstance(raw_order, list) else []
        known = {window.node for window in windows}
        order = [str(node) for node in listed if str(node) in known]
        # A window with no place in the saved order is not lost: it goes on top, where the person
        # who hand-edited the config would look for it first.
        order += [window.node for window in windows if window.node not in order]
        return Layout(
            kind="windowed",
            windows=tuple(windows),
            order=tuple(order),
            problems=tuple(problems),
        )
    try:
        tree = _node_from(section.get("tree"), "v1")
    except ViewportError as exc:
        return Layout(kind="tiling", tree=Leaf("v1"), problems=(str(exc),))
    return Layout(kind="tiling", tree=tree)


def _window_from(raw: object) -> Window:
    if not isinstance(raw, dict):
        raise ViewportError("a window needs a node, a child and a rectangle")
    node = _node_id(raw.get("node"))
    return Window(
        node,
        str(raw.get("child") or ""),
        _whole(raw.get("x"), 0),
        _whole(raw.get("y"), 0),
        max(MIN_WINDOW, _whole(raw.get("width"), 480)),
        max(MIN_WINDOW // 2, _whole(raw.get("height"), 320)),
    )


def _node_id(value: object) -> str:
    """A pane is `v1`, `v2`, …; a divider is `s1`, `s2`, … — two namespaces, because one tree has
    both and an id that could mean either would make "split this" and "move that bar" the same
    request."""
    text = str(value or "").strip()
    if not re.fullmatch(r"[vs]\d{1,6}", text):
        raise ViewportError(f"{text!r} is not a viewport id")
    return text


def _whole(value: object, fallback: int) -> int:
    try:
        return round(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback


def _node_from(raw: object, node: str) -> Node:
    if not isinstance(raw, dict):
        raise ViewportError("the viewport tree is not shaped like a tree")
    if "first" in raw or "second" in raw or "direction" in raw:
        direction = str(raw.get("direction") or "horizontal")
        if direction not in ("horizontal", "vertical"):
            raise ViewportError(f"{direction!r} is not a direction; try horizontal or vertical")
        # The stored ids win; the fallback only covers a hand-written config that left them out.
        here = str(raw.get("node") or node)
        first = _node_from(raw.get("first"), "v2")
        second = _node_from(raw.get("second"), "v3")
        return Split(here, direction, _ratio(raw.get("ratio")), first, second)
    return Leaf(str(raw.get("node") or node), str(raw.get("child") or ""))


def _ratio(value: object) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.5
    return min(max(number, MIN_RATIO), MAX_RATIO)


# ---------------------------------------------------------------------------------- operations


def leaves(tree: Node | None) -> list[Leaf]:
    if tree is None:
        return []
    if isinstance(tree, Leaf):
        return [tree]
    if isinstance(tree, Split):
        return [*leaves(tree.first), *leaves(tree.second)]
    return []


def find(tree: Node | None, node: str) -> Node | None:
    if tree is None:
        return None
    if tree.node == node:
        return tree
    if isinstance(tree, Split):
        return find(tree.first, node) or find(tree.second, node)
    return None


def next_node_id(layout: Layout, prefix: str = "v") -> str:
    """A fresh id: a letter and a number nobody is using, however many panes have been opened and
    shut. Ids are never reused, so a divider that a pane was named after cannot be mistaken for it
    later."""
    used = _all_ids(layout)
    highest = max(
        (
            int(node[1:])
            for node in used
            if node.startswith(prefix) and re.fullmatch(rf"{prefix}\d+", node)
        ),
        default=0,
    )
    return f"{prefix}{highest + 1}"


def _all_ids(layout: Layout) -> set[str]:
    if layout.kind == "windowed":
        return {window.node for window in layout.windows}
    found: set[str] = set()

    def walk(node: Node | None) -> None:
        if node is None:
            return
        found.add(node.node)
        if isinstance(node, Split):
            walk(node.first)
            walk(node.second)

    walk(layout.tree)
    return found


def split(layout: Layout, node: str, direction: object) -> tuple[Layout, str]:
    """Split one viewport into two. The box that was open stays in the first; the second is empty.

    The new pane is empty rather than a copy of the first: two panes showing the same thing is a
    layout the person asked for by clicking split twice, not the meaning of a single click, and a
    copy would make "compare two directories" a two-step job with an intermediate state nobody
    wanted to look at.
    """
    wanted = str(direction or "").strip().lower()
    if wanted in ("h", "horizontal", "row", "side by side"):
        wanted = "horizontal"
    elif wanted in ("v", "vertical", "column", "stacked"):
        wanted = "vertical"
    else:
        raise ViewportError(f"{direction!r} is not a direction; try horizontal or vertical")
    if layout.kind == "windowed":
        raise ViewportError("a windowed viewport is split by opening another window")
    tree = layout.tree
    target = find(tree, node)
    if target is None:
        raise ViewportError(f"no viewport {node!r} in this box")
    if not isinstance(target, Leaf):
        raise ViewportError(f"viewport {node!r} is a pair of viewports; split one of them")
    # The new pane and the new divider both need names that nothing else answers to: the box that
    # was open stays in the pane you clicked, the divider is a separate thing you can grab.
    fresh = next_node_id(layout)
    divider = next_node_id(layout, "s")
    replaced = _replace(
        tree, node, Split(divider, wanted, 0.5, Leaf(target.node, target.child), Leaf(fresh))
    )
    return Layout(kind="tiling", tree=replaced), fresh


def close(layout: Layout, node: str) -> tuple[Layout, str]:
    """Close one viewport: its sibling takes the space back, and its box is unfiled, not deleted.

    The last one cannot be closed — a viewport box with no viewport is a box that shows nothing and
    offers no way to get one back, so the request is refused and the page leaves the button disabled
    rather than explaining it afterwards.
    """
    if layout.kind == "windowed":
        window = next((item for item in layout.windows if item.node == node), None)
        if window is None:
            raise ViewportError(f"no viewport {node!r} in this box")
        if len(layout.windows) == 1:
            raise ViewportError("the last window cannot be closed; close the box instead")
        windows = tuple(item for item in layout.windows if item.node != node)
        order = tuple(item for item in layout.order if item != node)
        return Layout(kind="windowed", windows=windows, order=order), window.child
    tree = layout.tree
    if tree is None:
        raise ViewportError("this box has no viewports")
    if isinstance(tree, Leaf):
        if tree.node != node:
            raise ViewportError(f"no viewport {node!r} in this box")
        raise ViewportError("the last viewport cannot be closed; split it first")
    if find(tree, node) is None:
        raise ViewportError(f"no viewport {node!r} in this box")
    parent = _parent_of(tree, node)
    if parent is None:
        raise ViewportError(f"viewport {node!r} has no sibling to take its space")
    survivor = parent.second if parent.first.node == node else parent.first
    # The caller gets back the *box* that was in the closed pane, not the pane's id: the box is the
    # thing that stops being a child, and a caller handed "v2" would unlink a box named v2.
    closed = find(tree, node)
    child = closed.child if isinstance(closed, Leaf) else ""
    return Layout(kind="tiling", tree=_replace(tree, parent.node, survivor)), child


def set_ratio(layout: Layout, node: str, ratio: object) -> Layout:
    """Move the divider between two viewports. The bar is the only control for this."""
    tree = layout.tree
    target = find(tree, node)
    if not isinstance(target, Split):
        raise ViewportError(f"viewport {node!r} has no divider to move")
    number = _ratio(ratio)
    return Layout(kind="tiling", tree=_replace(tree, node, _retarget(target, number)))


def _retarget(split_node: Split, ratio: float) -> Split:
    return Split(split_node.node, split_node.direction, ratio, split_node.first, split_node.second)


def assign(layout: Layout, node: str, child: str) -> Layout:
    """Show `child` in one viewport, replacing whatever was there.

    Replacing means the old box stops being *drawn*, not that it stops being filed: the links are
    the store's, and a layout change that deleted a box would be a layout change.
    """
    wanted = (child or "").strip()
    if layout.kind == "windowed":
        found = False
        windows = []
        for window in layout.windows:
            if window.node == node:
                found = True
                windows.append(
                    Window(window.node, wanted, window.x, window.y, window.width, window.height)
                )
            else:
                windows.append(window)
        if not found:
            raise ViewportError(f"no viewport {node!r} in this box")
        return Layout(kind="windowed", windows=tuple(windows), order=layout.order)
    tree = layout.tree
    target = find(tree, node)
    if target is None:
        raise ViewportError(f"no viewport {node!r} in this box")
    if not isinstance(target, Leaf):
        raise ViewportError(f"viewport {node!r} is a pair of viewports; pick one")
    return Layout(
        kind="tiling",
        tree=_replace(tree, node, Leaf(node, wanted)),
    )


WINDOW_STEP = 32


def open_window(
    layout: Layout,
    child: str = "",
    *,
    x: int | None = None,
    y: int | None = None,
    width: int = 640,
    height: int = 420,
) -> tuple[Layout, str]:
    """Add a window, stepped down-right from the last one so two never land exactly on each other.

    The step comes from the *last* window rather than from a fixed grid: a person who has dragged
    their pile into a corner gets the next window beside that pile, not back at the corner the
    first window left.
    """
    if layout.kind != "windowed":
        raise ViewportError("a tiling viewport is changed by splitting it")
    node = next_node_id(layout)
    last = layout.windows[-1] if layout.windows else None
    placed_x = 24 if last is None else last.x + WINDOW_STEP
    placed_y = 24 if last is None else last.y + WINDOW_STEP
    window = Window(
        node,
        (child or "").strip(),
        max(0, placed_x if x is None else x),
        max(0, placed_y if y is None else y),
        max(MIN_WINDOW, width),
        max(MIN_WINDOW // 2, height),
    )
    windows = (*layout.windows, window)
    return Layout(kind="windowed", windows=windows, order=(*layout.order, node)), node


def show_in_window(layout: Layout, child: str) -> tuple[Layout, str]:
    """Show a box in a window: the first empty one if there is one, a new one if there is not.

    "Open this box in a window" should not leave an empty window behind. A windowed box starts with
    one window precisely so there is somewhere to drop the first box, and a request that added a
    second, leaving the first blank, would make the pile grow every time someone opens something.
    """
    if layout.kind != "windowed":
        raise ViewportError("a tiling viewport shows a box in a pane you name")
    wanted = (child or "").strip()
    if not wanted:
        # Nothing to show is "+ window": an empty window asked for is an empty window given. The
        # fill-an-empty-window rule is only for a box, and applying it here answered the request by
        # changing nothing at all.
        return open_window(layout, "")
    for window in layout.windows:
        if not window.child:
            return assign(layout, window.node, wanted), window.node
    return open_window(layout, wanted)


def move_window(layout: Layout, node: str, x: object, y: object) -> Layout:
    return _map_window(
        layout, node, lambda item: item.moved_to(_whole(x, item.x), _whole(y, item.y))
    )


def resize_window(layout: Layout, node: str, width: object, height: object) -> Layout:
    return _map_window(
        layout,
        node,
        lambda item: item.sized_to(_whole(width, item.width), _whole(height, item.height)),
    )


def raise_window(layout: Layout, node: str) -> Layout:
    """Bring one window to the front. Position and size are untouched: the pile, not the map."""
    if layout.kind != "windowed":
        raise ViewportError("only a windowed viewport has a pile to raise")
    if not any(window.node == node for window in layout.windows):
        raise ViewportError(f"no viewport {node!r} in this box")
    order = (*tuple(item for item in layout.order if item != node), node)
    return Layout(kind="windowed", windows=layout.windows, order=order)


def _map_window(layout: Layout, node: str, change: Any) -> Layout:
    if layout.kind != "windowed":
        raise ViewportError("only a windowed viewport has windows to move")
    found = False
    windows = []
    for window in layout.windows:
        if window.node == node:
            found = True
            windows.append(change(window))
        else:
            windows.append(window)
    if not found:
        raise ViewportError(f"no viewport {node!r} in this box")
    return Layout(kind="windowed", windows=tuple(windows), order=layout.order)


def _parent_of(tree: Node | None, node: str) -> Split | None:
    if not isinstance(tree, Split):
        return None
    if tree.first.node == node or tree.second.node == node:
        return tree
    return _parent_of(tree.first, node) or _parent_of(tree.second, node)


def _replace(tree: Node, node: str, replacement: Node) -> Node:
    if tree.node == node:
        return replacement
    if isinstance(tree, Split):
        return Split(
            tree.node,
            tree.direction,
            tree.ratio,
            _replace(tree.first, node, replacement),
            _replace(tree.second, node, replacement),
        )
    return tree


# ------------------------------------------------------------------------- validation on write


def check(
    config: dict[str, Any] | None,
    child_uuids: set[str] | None = None,
    kind: str | None = None,
) -> str:
    """Is this layout one the page could draw, with boxes the box actually holds? "" when yes.

    Called from the type's `check` for the shape, and from the API with `child_uuids` for the
    rest: the page's drawer, `box edit` and a raw config write are then refused in the same words.
    A layout that points at a box the box does not hold would show a pane with a box in it that
    `box tree` says belongs to someone else, and the two views of the library would part company —
    which is why the membership rule is checked where the children can actually be seen.
    """
    section = (config or {}).get(SECTION)
    if section is None:
        return ""
    if not isinstance(section, dict):
        return f"{SECTION} must be an object describing the viewports"
    stored = str(section.get("kind") or "")
    if stored and kind and stored != kind:
        return (
            f"{SECTION}: this box is a {kind} viewport and its config says {stored!r}; "
            "the type decides how the space is laid out"
        )
    kind = stored or kind or "tiling"
    if kind not in ("tiling", "windowed"):
        return f"{SECTION}: kind must be tiling or windowed"
    seen: set[str] = set()
    if kind == "windowed":
        raw_windows = section.get("windows")
        if not isinstance(raw_windows, list) or not raw_windows:
            return f"{SECTION}: a windowed viewport needs at least one window"
        for position, raw in enumerate(raw_windows, start=1):
            try:
                window = _window_from(raw)
            except ViewportError as exc:
                return f"{SECTION}: window {position}: {exc}"
            if window.node in seen:
                return f"{SECTION}: window {window.node} appears twice"
            seen.add(window.node)
            if child_uuids is not None and window.child and window.child not in child_uuids:
                return (
                    f"{SECTION}: window {window.node} shows a box that is not a child of this box; "
                    "open it in the viewport or drop it in first"
                )
        return ""

    try:
        tree = _node_from(section.get("tree"), "v1")
    except ViewportError as exc:
        return f"{SECTION}: {exc}"
    for leaf in leaves(tree):
        if child_uuids is not None and leaf.child and leaf.child not in child_uuids:
            return (
                f"{SECTION}: viewport {leaf.node} shows a box that is not a child of this box; "
                "open it in the viewport or drop it in first"
            )
    seen_ids = [leaf.node for leaf in leaves(tree)]
    if len(seen_ids) != len(set(seen_ids)):
        return f"{SECTION}: two viewports share an id, and only one of them could be picked"
    if len(_all_ids(Layout(kind="tiling", tree=tree))) != len(leaves(tree)) + _splits(tree):
        return f"{SECTION}: a viewport and a divider share an id"
    return ""


def _splits(tree: Node | None) -> int:
    if tree is None:
        return 0
    if isinstance(tree, Split):
        return 1 + _splits(tree.first) + _splits(tree.second)
    return 0
