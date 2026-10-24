# 9. The graph may contain cycles

Date: 2026-10-24
Status: accepted
Supersedes: the cycle rule described in `doc/architecture.md` before this date

## Context

`BoxStore.link` originally refused any edge that would let a box reach itself,
and a box inside itself outright, on the assumption that a containment graph with
a cycle is a mistake waiting to happen — the shape of "this folder contains
itself" nonsense.

That assumption turned out to be wrong about the domain. A box is a *node in a
knowledge graph*, not a directory entry: "the photograph of the painting in the
room that is painted from the photograph" is a thing a person means, and the same
box legitimately sits in several parents at once. Refusing the second edge pushed
the user into inventing duplicate boxes to say what they meant, which is worse
than a cycle: duplicates silently split the information.

## Decision

Cycles are allowed. A box may have many parents, may be its own child, and the
store no longer checks reachability before inserting a link. `CyclicLink` is
removed, along with the recursive-CTE query that existed only to detect cycles.

What the store still refuses, and why:

- `ChildrenNotAllowed` — a box whose type holds a resource (an image, a PDF) gets
  no children. That is a fact about the type, not a shape constraint.
- `RootCannotBeFiled` — the root box stays at the top, because something has to
  be the way in.

Every graph walk must now terminate on its own:

| Walker                 | How it stops                                              |
| ---------------------- | --------------------------------------------------------- |
| `BoxStore.tree()`      | a box already on the path is marked `truncated`           |
| `box tree`             | a box on the path prints `⟲ … (cycle: already above)`     |
| web nested content     | `MAX_EMBED_DEPTH`, past which a child renders as its icon |
| `box serve` search     | no recursion; one flat query                              |

## Consequences

* Expressiveness: real relationships can be recorded without duplicating boxes.
* `box tree NAME` with no root to start from now says so and prints each box once
  instead of giving up.
* Deleting a box in a cycle can leave other boxes with no path from the root:
  they still exist, are still searchable, and appear in the side pane. Nothing
  finds them by walking down — a "where is it reachable from" query is now a
  feature someone may want, and is easy to write with a recursive CTE that
  dedupes (`UNION`, not `UNION ALL`, terminates on cycles).
* Cycle bugs are now silent rather than refused: a new walker that forgets a
  guard hangs instead of erroring. Tests cover the three walkers above.
* A self-child box lists itself as parent and child, which the UI renders as
  depth-bounded previews rather than looping.
