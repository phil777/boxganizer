# 11. A mindmap node is a box, and an unwritten node may be replaced

Date: 2026-10-25
Status: accepted

## Context

The mindmap box was asked for in these words: a node in the middle, `Insert` making a
node linked to its parent, `Enter` making a sibling, and — the sentence that decides
this ADR — *"the new node is a placeholder for a box, that can be filled with a
drag&drop or with a right-click contextual menu to create a new box"*.

Two ways out of that sentence exist, and they are not compatible.

Nodes could be **the map's own things**: entries in the mindmap's config, each with a
label and a parent, one of which may eventually be attached to a real box. The map then
has a private graph of its own, drawn instantly, and boxes come later.

Or nodes could be **boxes**, always: `Insert` creates a box and links it, and what looks
like a placeholder is a real box whose name nobody has written yet.

The first is cheaper to draw and fatal to everything else. A node that is not a box
cannot be opened, searched, filed in a cabinet, linked from a second map, carry
metadata, or appear in `box tree`. It would be the one kind of thing in Boxganizer that
is not a box, in the one view whose whole subject is boxes — and a user who makes eight
nodes, then wants one of them to be a PDF, would find that "attaching" it means the
placeholder and the box are two entities with two lives.

## Decision

**Every node is a box.** A mindmap's tree is the containment graph seen from the
mindmap box: a node's children are its children in the graph, `Insert` creates a box
(`folder`, the one type that holds boxes and asks for nothing) and links it, `Enter`
does the same beside the node in hand, and moving a node between branches moves a link.

A node made by `Insert` and never written into is recognisable — the name
`"new idea"` (or `"new idea 2"`, …), the container type, no resource, no description, no
children of its own — and only such a node may be **replaced**. Dropping a box onto it
puts that box exactly where the placeholder stood, in the branch and on the side it was
on, and the empty node is deleted: that is what the node was waiting for. Dropping a
box onto any *other* node files it inside, because a thought somebody wrote down is not
thrown away to make room.

The map keeps two things for itself, in `config.mindmap` and nowhere else: which side of
the centre a branch grows on, and the order of siblings within it. Both are layout,
which is where [ADR 8](0008-layout-and-view-settings-live-in-the-box-config.md) says
layout belongs. A side is **written down the moment a branch is created**, because its
default comes from the branch's position, and a position-derived side would send an
unpinned branch across the centre the first time a sibling was reordered past it.

## Consequences

- Nodes are boxes: they open, search, index and link like anything else, and a mindmap
  is a *view of the library* rather than an island in it.
- The cycle rules matter here more than anywhere else, because the map is drawn by
  walking the graph and the graph legally contains cycles ([ADR 9](0009-the-graph-may-contain-cycles.md)).
  A box already on the way down is drawn once and marked truncated, and the walk stops
  at a node budget — a map of a library with a thousand boxes in it still opens.
- Deleting the replaced placeholder is the one destructive thing the map does, and it is
  bounded by the placeholder test above: an empty node with a generated name. A node
  with a name, a resource, a description or children is never deleted by a drop.
- A node's identity is its uuid, so the picture survives renames; a box appearing under
  two parents is simply drawn twice, once per branch, which is what two parents means.
- `folder` as the default node type is a deliberate compromise: the node has to be able
  to hold the nodes below it, and the alternative — inventing a type for the sake of the
  map — would add a kind of box that exists for one view.
- The page cannot express "the box that node became" after a replace, so it says what
  happened in the status line rather than leaving the node to vanish silently.
