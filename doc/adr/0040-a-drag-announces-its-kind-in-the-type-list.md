# 40. A drag announces its kind in the type list, because values arrive only at the drop

Date: 2026-11-11
Status: accepted
Extends: 38 (the gate reads `types`, not `files`)

## Context

ADR 38 recorded that a zone must decide from `dataTransfer.types`, not `dataTransfer.files`, because
a browser withholds a cross-window file until the person lets go. That rule was applied to *incoming*
files and quietly not applied to the page's **own** drags — and the result was the most-used gesture in
the app dying outright: dragging a box from the left panel onto a folder lit nothing, claimed nothing,
and filed nothing.

The mechanism, measured in the page during a real pointer drag:

    dragover → types: ["text/plain"], getData("text/plain") → ""      (and no `drop` event ever fires)

A gate written as `if (!draggedBoxRef(event)) return;` looks correct — it calls the same function the
drop handler calls — and returns `false` for every real drag, because the value it reads does not exist
yet. Since nothing called `preventDefault()`, Chromium fired no `drop` at all. The feature was not
broken at the drop; it was never allowed to reach one.

Two things made this hard to see. `text/plain` is what a dragged box and a highlighted sentence in
another window have in common, so the type list alone cannot tell them apart — and every probe that
"exercised" a box drag built its `DataTransfer` inside the page and dispatched it, where synthetic
transfers *can* read their own values mid-drag. The suite stayed green, the probes stayed green, and the
feature was dead.

## Decision

**A drag declares what it carries by putting a private type in the list.** `dragBox()` writes
`application/x-boxganizer-box` next to the uuid in `text/plain`, exactly as a file dragged out of a
directory box already writes `application/x-boxganizer-file`. A zone asking "may I light for this?"
answers from the type list, which is the only thing available while the pointer moves:

- `dragIsResource` — `Files`, the in-page file marker, or `text/uri-list`;
- `carriesABox` — the box marker;
- `claimableHere` — either, and it is the only thing a `dragover` may consult.

**Values are read at the drop, and nowhere earlier.** `draggedBoxRef` still decides what a reference
*is* — a uuid, and only a uuid — and it runs where the value exists. Splitting the question in two
(type list for the promise, value for the action) is what makes both halves honest; asking the value
while the pointer is still moving forces a choice between lying about prose and breaking boxes.

**The invariant is checked structurally, because it is invisible to behaviour tests.** A `dragover`
body that mentions `getData`, `carriesResources`, `draggedBoxRef` or any of the payload readers fails
the suite, naming the function at fault. A browser-level test cannot catch this class at all unless it
uses a real pointer, and a codebase cannot rely on every future test author knowing that. One line —
`if (!draggedBoxRef(event)) return;` — is a plausible, readable, wrong thing to write in a new view.

**A bare `text/plain` drag lights nothing.** It might be a path typed in a terminal, and that case is
now unsupported; it is more likely somebody's selected sentence, and a zone that highlights for prose
is the exact lie ADR 38 was written against. `text/uri-list` is what file managers and browsers
actually use, and it is accepted.

**One drag source, and it goes through the helper.** The library rows used to write `text/plain` by
hand with `effectAllowed = "move"`. They call `dragBox()` now: one place writes the marker, and the
Shift copy/move rule means the same thing in both lists.

**Rows are targets.** "Drag a box into a folder" has no answer in a list whose rows claim nothing, so a
row in the left panel and a row in a folder's own list both accept a drop and file into **the box on
that row**. Previously the row claimed nothing and the sheet behind it answered for the whole pane, so
the folder under the pointer lost to the folder being displayed.

## Consequences

- Every view that adds a drop zone has one line of homework: gate on `claimableHere(event)`. The
  structural test enforces it in both directions — a `dragover` that reads values fails, and a
  `dragover` that paints a highlight without a payload test and a way out fails too.
- A box dragged from a **different tab or application** into this page is not a box reference; it is
  text. Nothing in this app made such a drag, and if one ever does it will be filed nowhere rather than
  filed wrongly.
- A bare path in a terminal's selection is no longer a supported way to bring a file in. It can be
  dropped as a file (bytes, `Files`) or reached through a directory box. The cost is stated because
  `droppedFilePath` still handles the case when a drop arrives with a uri-list — which is what file
  managers send.
- The synthetic-`DataTransfer` habit is now known to be unsafe for drag evidence specifically: it can
  prove a drop handler works and simultaneously prove nothing about whether the drop was ever allowed
  to happen. Real-pointer drags (`page.dragAndDrop`, or CDP `Input.dispatchMouseEvent`) are the
  evidence for anything on this path, and the harness keeps a probe that uses them.

## Amendment, found while making the harness keep that promise

The sentence above was written before it was true. `tools/verify_web_ui.py` had no box drag at all,
and while adding one it turned out the harness could not finish a run against the current page: it
still clicked `#mode-toggle`, a control the app no longer draws, so the first section after the shell
spent thirty seconds timing out and the rest of the file never executed. Two more stale steps behind
it asked a desktop's settings row for a button that is folded away until the gear is turned, and
read the wallpaper off `.desktop-view` when it hangs on `.desktop-surface`.

The chip strip was a real defect, not a stale test. `.desktop-surface` is `position: absolute`, so it
is painted over the strip that follows it in the flow, and `elementFromPoint` at the centre of a chip
answered `desktop-surface`. An unplaced icon could be seen and could not be grabbed: every pointer
press aimed at it landed on the wallpaper, which is the ADR 38 failure wearing a different hat -- a
target that is drawn, and unreachable. The strip is now lifted above the surface and made transparent
to events where it draws nothing, so the chips take their presses and a drop on the wallpaper is
still a drop on the desk.

`make test` stayed green through all of this, which is the point of the rule above: none of it is
reachable from a synthetic event, and none of it is in CI, because a browser download in the test
pipeline is a dependency nobody asked for. So the harness is where this class of evidence lives, and
the harness has to be run. It re-seeds nothing: several checks read absolute state that the previous
run moved, so the recipe now says to re-seed between runs.
- The two lists painting row targets need their own CSS — `#box-list li.drop-target`,
  `.child.drop-target`, and the amber `-drop-refused` pair — because a handler that highlights nothing
  is this page's recurring failure, not a new one.
