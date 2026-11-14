# 45. The left pane folds on filing choices, not on containers — and every drop target paints

Date: 2026-11-14
Status: accepted
Related: ADR 38 (a zone promises only what it can finish), ADR 41/44 (gone resources, separate counts)

## Context

`work_to_do.md` asked for three changes to the left pane:

1. an icon-button group that switches between the current list and a **tree** in which "all the
   boxes with exactly 1 parent will not be displayed by default, but hidden under their parent…
   Boxes with 0 parent, 2 parents, or more are displayed as roots";
2. a right-aligned group that changes the **sort** (alphabetical, by type then alphabetical, by
   number of children), with an ascending/descending toggle at the top right;
3. the removal of the "drop resources here" strip, "let the whole left side pane be the place to
   drag&drop objects".

Each had a fact attached that made the literal reading wrong or the second request dangerous.

**The parent count includes boxes nobody filed.** `box init` creates two boxes: `root`, the way in,
and `cabinet`, and every box the page creates lands in the cabinet unless somewhere else is named
(`bootstrap`, and the create route's default parent). Measured on a live library: **65 of 66 boxes
have a container as a parent**, and 0 boxes have no parent at all. Folding on "exactly one parent"
therefore hides the *top level* and shows the leaves — the tree inverted — and the pane, the one
thing it was asked to shorten, gets no shorter.

**Sorting quietly overrode an older rule.** The pane has long pinned the root box first. Adding a
default alphabetical sort moved the first row to "Board", and a context-menu check that reaches for
the first row then demanded from the wrong box an entry that only the root lacks.

**The whole-pane drop had already been tried, and reverted.** A test asserted that `#side` must
*not* have a `drop-target` rule, with the reason written out: an earlier attempt widened the pane's
gate, the class landed on `#side` while the only CSS rule targeted `#box-list`, and the page
swallowed files it had never advertised taking. The measurement that approved it asked for
`.drop-target` with `closest()`, which an ancestor satisfies — it proved a class existed and said
nothing about whether anything changed on screen.

## Decision

**The tree folds on filing parents, not on parents.** `/api/boxes` carries both counts:
`parent_count` (every parent) and `filing_count` / `filing_parents` (parents excluding the root box
and the cabinet — the containers `box init` creates, which no person chose). The pane folds on the
filing counts. On that same live library the top level goes from 70 rows to 43 with 27 boxes
folding: a pane that gets shorter, which was the request. Containers are excluded by *type*, not by
name, so a cabinet renamed "attic" still counts as the container it is.

A box filed in two folders is a root. It has no single place to disappear into, and choosing one
would be the pane inventing a fact about the library.

**Root-first survives sorting; sorting happens underneath it.** `pinRootFirst()` is applied after
every order, in both views. Inside tree mode each opened level sorts itself, because "sort the
children of this folder" is the question a folded level actually asks. The default remains name
ascending — the order the server already returned — so the pane is unchanged until pressed.

**A pane may claim a drop only if it can show one.** The strip is gone and `#side` took its place,
with `#side.drop-target` (a dashed outline) written in the same change as the handler that adds the
class. The rule the reverted attempt produced stands, restated where it can fail: *every element
that can receive `drop-target` has a rule that paints it* — asserted for `#side` and `#box-list`
alike.

Precedence needs no new bookkeeping: nearer zones (rows, folder listings, viewports) are bound
deeper and run first in the bubble, and the pane stands down whenever `defaultPrevented` is set.
Boxes remain the list's business (ADR 38), so a bigger target did not widen what is promised — only
where a promised arrival may land.

**View, order, direction and opened folders are the person's, and are kept.** Stored in
`localStorage` as `boxganizer.pane`, next to the other working-style choices: they survive a reload
and do not travel with the database, so a library opened elsewhere starts as its own choice. Opened
folders are included because a tree without memory is a tree that is closed. Unreadable storage
falls back to the plain list. The controls state the restored truth (`applyPaneControls()` runs
first in `init`) — a pane folded into a tree under a button claiming "list" is a control that
contradicts its own list.

## Consequences

- `filing_count` is the number the pane folds on; `parent_count` stays for anything that means the
  literal graph. Anyone adding pane behaviour must use the filing counts, or the tree inverts again.
- Tree mode with a search term in the field shows the hits flat. A tree that folded away the boxes
  the search just found would be a search nobody trusts.
- The pane's outline now appears across a region the height of the window. That is the honest
  version of a claim the strip used to make in 12 pixels; if it ever stops painting, the test for
  it is the one written for the failure it replaced.
- Sorting is a client-side view of the page the server returned (`/api/boxes` limit 200). Ordering a
  library larger than a page is a server question and this decision does not pretend otherwise.
