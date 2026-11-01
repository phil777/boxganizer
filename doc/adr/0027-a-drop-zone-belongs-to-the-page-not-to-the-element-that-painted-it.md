# 27. A drop zone belongs to the page, not to the element that painted it

Date: 2026-11-01
Status: accepted

## Context

Every view that can receive a box paints an invitation while the pointer is over it: `dragover`
adds a class, `dragleave` or the view's own `drop` takes it off. That is a per-element contract,
and three ordinary things break it.

**A view that claims a drop stops the event.** A mindmap tile, a checklist line, a row in the tag
window and a viewport pane all call `stopPropagation()` so the box is not filed twice — once where
it landed and once in whatever is behind it. The element behind is usually `#canvas`, which is
never told the drag ended and keeps its dashed outline.

**`#canvas` and the folder's list outlive the box.** The pane is not re-created when you open
another box, so an outline left on it is still there three boxes later. That is exactly what was
reported: "after a drag&drop, the red landing zone stays on, even when opening other boxes".

**Some drags end with no `drop` at all.** Escape aborts a drag, and leading the pointer out of the
window aborts it too; neither fires `drop`, and `dragleave` does not fire on an element the pointer
left by the window edge.

Putting the fix in each view is the obvious move and the wrong one: it asks every view, including
ones written later, to remember a rule about a neighbour it cannot see.

## Decision

One function clears every landing zone, and **every path that can end a drag reaches it**.

- `DROP_ZONE_CLASSES` lists the invitation classes — `drop-target`, `drop-refuse`, `drop-refused`,
  `is-target`, `tag-target` — and nothing else. A class that answers *what happened* (a box appearing
  in a list, a sentence in the status line) is not a landing zone and is not in the list.
- Document-level listeners on `drop`, `dragend` and `dragstart`, **in the capture phase**, so a
  `stopPropagation()` inside a view cannot outrank them. Capture also means the clearing runs *before*
  the handler that takes the drop, so whatever that drop paints afterwards is untouched.
- `renderMain()` and `renderSide()` call it too. A drag whose source the render destroys reports
  nothing back — a detached element does not bubble — so no listener can be relied on there.
- `dragover` is deliberately not in the list. Clearing while a drag is still over the page would take
  the zone off under the pointer and the pane would look dead.

The views keep their own `dragleave` and `drop` removals. They are now redundant rather than wrong,
and a view that stopped painting a zone would have to stop clearing it as well.

## Consequences

- A new drop target gets its clearing for free, and cannot forget it.
- The invariant is testable in both directions: `tests/test_web.py::test_a_landing_zone_clears_on_every_way_a_drag_can_end`
  asserts the clearing exists for every ending *and* that at least three drop handlers still swallow
  the event — if the swallowing ever stops, the global clearing is no longer doing the work this ADR
  assigns it, and the test says so instead of passing quietly.
- Behaviour, measured in Chromium against a throwaway library (`/tmp/check_zone_clear.js`, 14 cases:
  folder row, grid cell, desktop, checklist line, mindmap tile, viewport pane, dynamic-folder refusal,
  root line, Escape, drag leaving the window, re-render with no ending, a claimed drop then opening
  another box, and a drop one pane claims while another is lit): **6/14 clean before this change,
  14/14 after**. Before it, `#canvas` was still outlined after the drop, after the view settled, and
  after opening two other boxes; pane A stayed lit when pane B took the drop.
- One 409 remains in the sweep: dropping a box where it is already filed. The server refuses a
  duplicate link, which is the correct answer, and the zone clears anyway.
