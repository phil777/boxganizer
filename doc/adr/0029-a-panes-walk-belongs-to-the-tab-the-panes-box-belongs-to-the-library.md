# 29. A pane's walk belongs to the tab; the pane's box belongs to the library

Date: 2026-11-01
Status: accepted

## Context

Every operation on a viewport — split, close a neighbour, drag the divider, drop a box in — sent every
pane back to the box it started at. Reported as "all the viewports are reset to their direct child",
and measured before this change as **4/18** navigation checks passing.

The cause was not in the operations. A pane's navigation lived in its own closure:

```js
const trail = shown ? [shown] : [];   // app.js, paneShell()
```

`shown` is what the **layout** names for that node, so any repaint — which is what all five operations
end with, since each adopts the layout the action route answered with — rebuilt the pane from scratch
and with it the crumb line. Nothing had been asked to change; the reader just lost their place.

Two obvious homes for that state are both wrong.

**Writing the walk into the layout** would put the path in the library, which is shared: two tabs on one
library would then hold two truths about one pane, and the last writer would silently move the other's
content. The layout already means something specific — what this space was *told* to show — and it is
the server's to own (ADR 23).

**Keeping it in the closure** is where it was, and is exactly what failed: the closure is the thing a
repaint throws away.

## Decision

Navigation is a fact about **the space in this tab**, so it is kept there: a per-tab store keyed by
library box and node, holding the trail.

```js
const walked = panePathFor(box.uuid, node);
const stillHere = Boolean(walked && shown && walked[0].uuid === shown.uuid);
const trail = stillHere ? walked.slice() : shown ? [shown] : [];
```

- The **layout outranks the walk whenever they disagree**. A path is kept only while it *starts at* the
  box the layout names, so an explicit "show this in that pane" — a drop, a crumb clicked in another
  tab, an `open` action — replaces the walk rather than being ignored by it. The rule needs no
  arbitration protocol because there is only one authority about what a pane shows.
- **Navigation never writes the library.** `open()` remembers the path and draws; it issues no viewport
  action. Pinned by a test that fails if the word `viewportAction` ever appears in that body.
- `sessionStorage`, not `localStorage`: a reload keeps the reader's place, a second tab starts from what
  the library says. Persisting a walk across tabs would be the shared-truth problem wearing a different
  hat.
- A node that no longer exists stops holding a path (`forgetMissingPanePaths`, called by both painters),
  and a walk that leads to a box since deleted is forgotten on the spot and falls back to the layout's
  box rather than painting an error where content used to be.
- What is drawn is the **end of the walk**, not the layout's box, and it is re-read by uuid — so a
  rename shows, and the pane cannot be stuck on a stale copy of the box.

## Consequences

- The five operations, a reload, and all six windowed gestures (move, resize, raise, maximize, minimize,
  and un-minimize by clicking the title, which is how a minimized window comes back) leave the crumb
  line and the content alone: **18/18** measured in Chromium against a throwaway library
  (`/tmp/check_pane_nav.js`, seeded and served by `/tmp/run_task3.sh`), where the same harness scores
  **4/18** on the code before this change.
- A pane deep in a walk proves it in the layout too: after split, drop, close and reload,
  `GET /api/boxes/Tiles/viewports` still names `Deep` for `v1` — the library was never told about
  `Deepest`. That is the assertion that keeps this from quietly becoming a second source of truth.
- Two tabs can show different boxes in the same pane. That is the honest consequence of the walk being
  the reader's, and it beats the alternative: one tab moving another tab's content.
- Closing the tab forgets the walks. Nothing was promised beyond a reload, and a walk is not worth
  migrating.
- Pinned by `tests/test_web.py::test_a_pane_keeps_the_walk_after_everything_the_viewport_does`, which
  asserts the recall rule, the remembering, "draw the end of the walk", the per-tab store, the pruning,
  and — the load-bearing one — that navigation issues no layout write.
