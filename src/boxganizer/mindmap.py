"""Mindmap layout: which side of the centre a branch grows on, and in what order.

A mindmap box keeps `config.mindmap` with two keys — `sides`, a map of child uuid
to `"left"` or `"right"`, and `orders`, a map of parent uuid to the ordered uuids of
its children. Containment still lives in the links table, exactly as in
:mod:`boxganizer.grid` and :mod:`boxganizer.desktop`: this remembers only *where* a
node is drawn, so a box that has never been placed is still a child and still shows up.

The tree of nodes is the containment graph seen from the mindmap box, so a node's
children are its children in the graph and a box can appear in two branches by having
two parents. The graph allows cycles, and a mindmap drawn round a cycle would never
finish, so the walk down stops at a box already on the way and marks it truncated.

Only the branches coming straight off the centre have a side. Everything below a
branch grows the same way as the branch it hangs on, which is what makes the two halves
of the picture hold two different sets of thoughts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from boxganizer.errors import PlacementError

#: Layout section this module owns inside `Box.config`.
SECTION = "mindmap"

#: The two directions a branch grows in.
LEFT = "left"
RIGHT = "right"
SIDES = (LEFT, RIGHT)

#: How far down and how wide the walk goes before it is called a mistake. A cycle is
#: legal in the graph; drawing it as a radial map is not.
MAX_DEPTH = 24
MAX_NODES = 2000

#: What a node is called before anyone has thought of a name for it.
NEW_NODE_NAME = "new idea"

#: The other side of the centre from each side.
OTHER: dict[str, str] = {LEFT: RIGHT, RIGHT: LEFT}


@dataclass(frozen=True)
class Layout:
    """What the page was told about where things go."""

    #: uuid -> side, for branches off the centre.
    sides: dict[str, str]
    #: parent uuid -> children in the order they are drawn from the middle outwards.
    orders: dict[str, list[str]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "sides": dict(sorted(self.sides.items())),
            "orders": {parent: list(children) for parent, children in sorted(self.orders.items())},
        }


def _section(config: dict[str, Any] | None) -> dict[str, Any]:
    section = (config or {}).get(SECTION)
    return section if isinstance(section, dict) else {}


def sides_of(config: dict[str, Any] | None) -> dict[str, str]:
    """The recorded sides, dropping anything that is not left or right."""
    raw = _section(config).get("sides")
    if not isinstance(raw, dict):
        return {}
    return {
        uuid.strip(): side.strip().lower()
        for uuid, side in raw.items()
        if isinstance(uuid, str) and uuid.strip() and side.strip().lower() in SIDES
    }


def orders_of(config: dict[str, Any] | None) -> dict[str, list[str]]:
    """The recorded sibling orders, dropping anything that is not a list of strings."""
    raw = _section(config).get("orders")
    if not isinstance(raw, dict):
        return {}
    orders: dict[str, list[str]] = {}
    for parent, children in raw.items():
        if isinstance(children, list):
            kept = [str(child).strip() for child in children if str(child).strip()]
            if kept:
                orders[str(parent).strip()] = kept
    return orders


def layout_of(config: dict[str, Any] | None) -> Layout:
    """Both halves of the layout, as recorded."""
    return Layout(sides=sides_of(config), orders=orders_of(config))


def default_side(index: int) -> str:
    """Which side branch number *index* grows on when nobody has said.

    Alternating from the right: a first branch appears to the right where the eye is
    already looking, and the picture stays balanced as it fills up instead of leaning
    on one side until someone intervenes.
    """
    return RIGHT if index % 2 == 0 else LEFT


def side_of(config: dict[str, Any] | None, uuid: str, *, index: int = 0) -> str:
    """The side one branch was put on, or the default for its position.

    A caller that is about to reorder or rename a branch should write the side down
    first: the default depends on the position, so a branch that never had one would
    cross the centre the moment a sibling moved past it.
    """
    return sides_of(config).get(uuid, default_side(index))


def flip_side(config: dict[str, Any] | None, uuid: str, *, index: int = 0) -> dict[str, Any]:
    """Send a branch to the other side of the centre."""
    return set_side(config, uuid, OTHER[side_of(config, uuid, index=index)])


def set_side(config: dict[str, Any] | None, uuid: str, side: str) -> dict[str, Any]:
    """Put a branch on a named side."""
    wanted = str(side).strip().lower()
    if wanted not in SIDES:
        raise PlacementError(f"a branch grows {LEFT} or {RIGHT}, not {side!r}")
    if not uuid.strip():
        raise PlacementError("changing a side needs the uuid of a box")
    sides = sides_of(config)
    sides[uuid.strip()] = wanted
    return to_config(config, sides=sides)


def pin_sides(config: dict[str, Any] | None, child_uuids: list[str]) -> dict[str, Any]:
    """Write down the side every branch of the centre is drawn on.

    A side left unwritten comes from the branch's position, which is fine while nothing
    moves and wrong the moment something does: reorder a sibling and the branch that had
    no side of its own takes the one its old neighbour left behind, crossing the centre
    for no reason anyone can see. So the sides are written down before anything moves.
    """
    sides = sides_of(config)
    for index, uuid in enumerate(child_uuids):
        if uuid not in sides:
            sides[uuid] = default_side(index)
    return to_config(config, sides=sides)


def ordered(children: list[Any], parent_uuid: str, config: dict[str, Any] | None) -> list[Any]:
    """Sort *children* into the order this mindmap draws them.

    Children the box has never ordered come last, in the order they arrived, and are
    not written to the config for it: a layout saved for one purpose should not be
    holding a list of everything that exists.
    """
    sequence = orders_of(config).get(parent_uuid, [])
    if not sequence:
        return list(children)
    rank = {uuid: position for position, uuid in enumerate(sequence)}
    return sorted(children, key=lambda child: rank.get(getattr(child, "uuid", ""), len(rank)))


def set_order(
    config: dict[str, Any] | None, parent_uuid: str, child_uuids: list[str]
) -> dict[str, Any]:
    """Record the order of one node's children."""
    if not parent_uuid.strip():
        raise PlacementError("an order needs the uuid of a parent")
    orders = orders_of(config)
    kept = [str(uuid).strip() for uuid in child_uuids if str(uuid).strip()]
    if kept:
        orders[parent_uuid.strip()] = kept
    else:
        orders.pop(parent_uuid.strip(), None)
    return to_config(config, orders=orders)


def move_sibling(
    config: dict[str, Any] | None,
    parent_uuid: str,
    child_uuid: str,
    siblings: list[Any],
    delta: int,
) -> dict[str, Any]:
    """Slide one node up or down among the siblings it is drawn with.

    `siblings` is what the node is among — its parent's children — because the order
    that matters is the drawn one, not whatever was stored. A delta of -1 moves it
    towards the first, +1 towards the last; going past either end is refused rather
    than silently wrapping, so a key held too long stops at the edge.

    Only the siblings on *its side of the centre* count. A branch of the mindmap itself
    has siblings on both sides, and swapping a left-hand thought with a right-hand one
    would move it nowhere at all while telling the person pressing the key that
    something had happened.
    """
    line = [str(getattr(sibling, "uuid", "")) for sibling in ordered(siblings, parent_uuid, config)]
    if child_uuid not in line:
        raise PlacementError("only a child of that box can be moved among its siblings")
    mine = side_of(config, child_uuid, index=line.index(child_uuid))
    same_side = [uuid for uuid in line if side_of(config, uuid, index=line.index(uuid)) == mine]
    if child_uuid not in same_side:  # pragma: no cover - it is in the line, so it is here
        raise PlacementError("it is already at that end of the branch")
    position = same_side.index(child_uuid)
    target = position + delta
    if target < 0 or target >= len(same_side):
        raise PlacementError("it is already at that end of the branch")
    line[line.index(child_uuid)], line[line.index(same_side[target])] = (
        line[line.index(same_side[target])],
        line[line.index(child_uuid)],
    )
    return set_order(config, parent_uuid, line)


def to_config(
    config: dict[str, Any] | None,
    *,
    sides: dict[str, str] | None = None,
    orders: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """A copy of `config` with the mindmap section replaced.

    Written whole each time, so a write also clears whatever an older version or a
    hand-edited config left lying in the section.
    """
    merged: dict[str, Any] = dict(config or {})
    section = layout_of(merged).as_dict()
    if sides is not None:
        section["sides"] = dict(sorted(sides.items()))
    if orders is not None:
        section["orders"] = {parent: list(kids) for parent, kids in sorted(orders.items())}
    merged[SECTION] = section
    return merged


# -- the map itself -----------------------------------------------------------


def node(store: Any, uuid: str) -> dict[str, Any]:
    """The map as the page needs it: the tree below *uuid*, with sides and order.

    Each entry is the box, the side its branch grows on, and its children in the order
    they are drawn. A box already met on the way down is marked `truncated` instead of
    being followed again, and the whole walk stops at :data:`MAX_NODES` so a library
    with a thousand boxes in it still opens.
    """
    root = store.resolve(uuid)
    # The layout belongs to the mindmap box and governs the whole picture: a node three
    # levels down is ordered by the map it appears in, not by a config of its own.
    budget = {"left": MAX_NODES}
    return _node(
        store, root, config=root.config, side=RIGHT, index=0, path=frozenset(), budget=budget
    )


def _node(
    store: Any,
    box: Any,
    *,
    config: dict[str, Any] | None,
    side: str,
    index: int,
    path: frozenset[str],
    budget: dict[str, int],
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "uuid": box.uuid,
        "name": box.name,
        "icon": box.icon,
        "box_type": box.box_type,
        "side": side,
        "children": [],
    }
    if box.uuid in path:
        entry["truncated"] = True  # a cycle: the box is shown, its subtree is not
        return entry
    if budget["left"] <= 0:
        entry["truncated"] = True
        return entry
    if len(path) >= MAX_DEPTH:
        entry["truncated"] = True
        return entry

    budget["left"] -= 1
    next_path = path | {box.uuid}
    at_centre = not path
    kids = children_of(store, box, config)
    drawn: list[dict[str, Any]] = []
    for position, child in enumerate(kids):
        if budget["left"] <= 0:
            # Every node costs one from the budget, breadth included: a box with three
            # thousand children is drawn as many as fit, and the number left out is
            # said rather than hidden.
            entry["truncated"] = True
            entry["omitted"] = len(kids) - position
            break
        drawn.append(
            _node(
                store,
                child,
                config=config,
                # Only a branch off the centre chooses a side; below that a node grows
                # the way the branch it hangs on is growing, which is what keeps a
                # thought on one side of the picture instead of scattered over both.
                side=side_of(config, child.uuid, index=position) if at_centre else side,
                index=position,
                path=next_path,
                budget=budget,
            )
        )
    entry["children"] = drawn
    return entry


def children_of(store: Any, box: Any, config: dict[str, Any] | None) -> list[Any]:
    """A box's children as this map draws them: stored order, sides aside.

    The root box of a mindmap carries the layout in its own config; deeper nodes are
    ordered by that same map, because one mindmap owns the shape of the whole picture.
    """
    return ordered(store.children(box.uuid), box.uuid, config)


# -- reading a map back -------------------------------------------------------

#: What kind of box a bare node is. A node has to be able to hold the nodes below it,
#: and a folder is the one type that holds boxes and asks for nothing else: the idea
#: itself is what the name carries, until the node is filled with a real box.
NODE_TYPE = "folder"


def find(root: dict[str, Any], uuid: str) -> dict[str, Any] | None:
    """The node with this uuid, or None when it is not in the map."""
    if root.get("uuid") == uuid:
        return root
    for child in root.get("children", []):
        found = find(child, uuid)
        if found is not None:
            return found
    return None


def parent_index(root: dict[str, Any]) -> dict[str, str]:
    """Which node each node hangs from, in this map.

    A box may have several parents in the graph and so may appear in two branches. The
    map needs one answer — the parent it is drawn under — and walking the picture gives
    exactly that, where the links table would have to guess.
    """
    parents: dict[str, str] = {}

    def descend(node: dict[str, Any]) -> None:
        for child in node.get("children", []):
            uuid = str(child.get("uuid", ""))
            if uuid and uuid not in parents:
                parents[uuid] = str(node.get("uuid", ""))
                descend(child)

    descend(root)
    return parents


def line_up(node: dict[str, Any]) -> list[str]:
    """The uuids of a node's children, in the order they are drawn."""
    return [str(child.get("uuid", "")) for child in node.get("children", [])]


def move_towards_centre(
    store: Any, map_uuid: str, node_uuid: str
) -> tuple[dict[str, Any], str]:
    """Promote a node: hang it off its parent's parent, just after its parent.

    Returns the fresh map and a note about what happened, because at the centre there
    is nothing above — the requirement's own answer there is to change side, which this
    does, and says.
    """
    box = store.resolve(map_uuid)
    drawn_first = [child.uuid for child in children_of(store, box, box.config)]
    box.config = pin_sides(box.config, drawn_first)
    store.update(box)
    tree = node(store, box.uuid)
    parents = parent_index(tree)
    current = find(tree, node_uuid)
    if current is None:
        raise PlacementError("that node is not part of this map")
    parent_uuid = parents.get(node_uuid)
    if parent_uuid is None:
        raise PlacementError("the centre of a mindmap has nothing above it")

    grand_uuid = parents.get(parent_uuid)
    if grand_uuid is None:  # the parent is the centre: nowhere up to go, so across
        branch = line_up(find(tree, parent_uuid) or {})
        index = branch.index(node_uuid) if node_uuid in branch else 0
        box.config = flip_side(box.config, node_uuid, index=index)
        store.update(box)
        return node(store, box.uuid), "changed side"

    grand = find(tree, grand_uuid)
    parent_siblings = line_up(grand or {})
    box.config = set_order(
        box.config, grand_uuid, _insert_after(parent_siblings, parent_uuid, [node_uuid])
    )
    store.update(box)
    store.unlink(parent_uuid, node_uuid)
    store.link(grand_uuid, node_uuid)
    return node(store, box.uuid), "moved up the hierarchy"


def move_away_from_centre(store: Any, map_uuid: str, node_uuid: str) -> tuple[dict[str, Any], str]:
    """Demote a node: hang it off the sibling above it, at the start of that branch.

    This is how a thought that turned out to be a detail of the previous one is filed,
    and it needs a sibling to become a child of — at the top of a branch there is
    nothing to hang under, which is said rather than done silently.
    """
    box = store.resolve(map_uuid)
    drawn_first = [child.uuid for child in children_of(store, box, box.config)]
    box.config = pin_sides(box.config, drawn_first)
    store.update(box)
    tree = node(store, box.uuid)
    parents = parent_index(tree)
    current = find(tree, node_uuid)
    if current is None:
        raise PlacementError("that node is not part of this map")
    parent_uuid = parents.get(node_uuid)
    if parent_uuid is None:
        raise PlacementError("the centre of a mindmap has nothing above it")

    parent = find(tree, parent_uuid)
    siblings = line_up(parent or {})
    position = siblings.index(node_uuid) if node_uuid in siblings else -1
    if position <= 0:
        raise PlacementError("nothing above it in the branch to hang it under")
    above = siblings[position - 1]

    box.config = set_order(
        box.config, parent_uuid, [uuid for uuid in siblings if uuid != node_uuid]
    )
    store.update(box)
    store.unlink(parent_uuid, node_uuid)
    store.link(above, node_uuid)
    below = line_up(find(tree, above) or {})
    box = store.resolve(map_uuid)
    box.config = set_order(box.config, above, [node_uuid, *below])
    store.update(box)
    return node(store, box.uuid), "moved down the hierarchy"


def placeholder(box: Any, *, siblings: int = -1) -> bool:
    """Whether this node is a box Insert made and nothing was ever written into.

    Only such a node may be replaced by a box dragged onto it. A node with a name of
    its own, a resource, a description or children of its own is somebody's thought,
    and dropping a box on it files that box inside it instead.
    """
    if getattr(box, "box_type", "") != NODE_TYPE:
        return False
    if getattr(box, "resource_uri", None) or str(getattr(box, "description", "")).strip():
        return False
    if siblings > 0:
        return False
    name = str(getattr(box, "name", ""))
    rest = name[len(NEW_NODE_NAME) :] if name.startswith(NEW_NODE_NAME) else ""
    return bool(rest.strip().isdigit()) or name == NEW_NODE_NAME


def next_name(taken: set[str]) -> str:
    """A name for a new node: "new idea", then "new idea 2", then 3, and so on.

    Box names are unique, so three presses of the Insert key need three names; the
    numbering is the least anyone can do for a box that has not been thought about yet.
    """
    if NEW_NODE_NAME not in taken:
        return NEW_NODE_NAME
    suffix = 2
    while f"{NEW_NODE_NAME} {suffix}" in taken:
        suffix += 1
    return f"{NEW_NODE_NAME} {suffix}"


def _insert_after(sequence: list[str], anchor: str, extras: list[str]) -> list[str]:
    """`sequence` with `extras` put in straight after `anchor`."""
    kept = [uuid for uuid in sequence if uuid not in extras]
    if anchor not in kept:
        return [*kept, *extras]
    at = kept.index(anchor) + 1
    return [*kept[:at], *extras, *kept[at:]]
