# 23. A viewport lends its space: the layout is a tree the server owns, and a drop copies unless Shift says otherwise

Date: 2026-11-09
Status: accepted

## Context

Opening a box from another box replaces it: the parent survives only as a crumb in the path line.
That is right for filing and wrong for comparison — you cannot look at two directories, or a note
and the pdf it cites, and you cannot drag between two things when only one is on screen.

The request: a **viewport box** that is never replaced, offering subspaces ("viewports"), each with
its own path line and **at most one child box**; two kinds, **tiling** (split a viewport into two,
drag the bar between them, close one and its sibling reclaims the space, the last one cannot close,
modelled as a binary tree) and **windowed** (floating windows, moved, resized, closed, with the
positions and children saved). And a drop rule for the drag & drop this makes possible: **a plain
drop copies the link** — the box becomes a child of both — **while Shift moves it**, taking it off
the origin. The whole setup is saved in the configuration.

Four forks.

**Where the layout lives: the page or the box.** The page is where the geometry is visible, so a
`PUT` of the tree from the browser is the cheap design.

**A tree or a list of rectangles for tiling.** Rectangles are easier to draw.

**Who decides the drop rule.** The page knows whether Shift was held; the server knows the graph.

**Whether a viewport's boxes are links or references.** The layout could name boxes without filing
them, keeping filing untouched.

## Decision

### 1. The layout is a tree, and it is the server's

`boxganizer/viewports.py` owns both layouts; `config.viewports` stores them. Tiling is a **binary
tree** — a leaf is a pane showing at most one box, a node is a pair with a direction and a ratio.

A tree rather than rectangles because a rectangle list has no answer to *"what happens to the space
when this one goes away"*. With rectangles, closing a pane means somebody re-derives who inherits
the area, and that somebody turns out to be a rule with edge cases — which is a tree wearing a
disguise, drawn badly. With the tree, closing a leaf replaces its parent node with the surviving
sibling and the outer ratios are untouched, so "the sibling recovers the place occupied before by
the closed viewport" is the data structure's own behaviour rather than a routine that can be wrong.

**Panes are `v1, v2, …`; dividers are `s1, s2, …`.** One tree holds both kinds of thing, so one id
namespace would make "split this pane" and "move that bar" the same request; and ids are never
reused, so a divider can never be mistaken later for the pane it was born beside.

### 2. The page sends intent and adopts the answer

Seven routes — `split`, `close`, `ratio`, `open`, `window`, `move`, `resize`, `raise` — each take
`{node, direction, ratio, child, x, y, width, height}` and **return the whole layout**, including
each pane's box brief, so a viewport paints in one request.

The page does not post a tree. Two tabs open on one viewport box would each be deciding what the
other's click meant, and the tree model would exist twice, in two languages, with the same name —
and the first symptom would be a divider that snaps back to a ratio neither tab chose.

Two consequences the browser found: a gesture that adopts the answer does **not** repaint (the panes
are already where the person put them, and rebuilding them throws away every scroll position in
them); and `applyRatio` has to run **after** the children are in the DOM, because a ratio applied to
an empty band sizes nothing — which is how a divider dragged to 0.48 saved 0.48 and came back at the
CSS default. Similarly, the divider's `pointermove`/`pointerup` listeners live on `document` for the
length of the drag: the bar moves out from under the pointer, and a capture set on it ended the drag
at the first re-layout, saving the ratio the pointer had *started* on.

### 3. What a viewport shows, it holds

A box open in a pane is a **child** of the viewport box; the layout only says which pane draws it.
The alternative — a layout that names boxes it does not hold — would put a box on screen that
`box tree`, the side pane and `box in` say belongs to somebody else, and the page and the library
would disagree about filing, which is the one disagreement a person cannot resolve from outside.

So `check(config, child_uuids, kind)` runs where each half can be seen: the **type** checks shape
and that the config describes *this* kind of viewport (a windowed box holding a tiling tree is
refused), and the **API** checks the graph, because a type's `check` has no store to ask. Closing a
pane unfiles its box — `store.unlink` on that one link — and never deletes it: a layout change that
removed a box would be a layout change. The last pane refuses to close (`422`, and `closable` is in
the payload so the button is disabled instead of explaining afterwards). `show_in_window` fills an
empty window before making a new one, because "open this in a window" that left a blank window
behind would grow the pile on every click — and "+ window" with nothing to show makes a window, which
the first version answered by changing nothing at all.

Each pane walks on **its own path**. A shared trail would mean that opening a box in the left pane
changed what "back" means in the right one, and the entire purpose of the box is that two things are
open at once. A pane's own settings are written to the box inside the pane, not to the viewport
(`saveSettingFor`), and re-render only that pane.

### 4. A plain drop copies; Shift says otherwise — and the server makes it one change

`POST /api/boxes/{ref}/children` takes `move_from`. Without it the box is filed in both places.
With it, the box is filed here **and** taken off the origin, in one request:

- Two requests would not be one action. Link-then-unlink can leave the box filed twice after the
  unlink failed, or nowhere at all after the link failed.
- `move_from` is checked **before** anything is filed: naming a box that never held it is `409
  not_filed_there` and changes nothing at all. A box pulled out of a parent it never had is a lie
  about the graph; a drop that did nothing is a fact.
- The origin travels with the drag (`application/x-boxganizer-from`), not in a page-wide "current
  drag" variable, because the drop may land in another pane or after a re-render, and a variable set
  at `dragstart` would describe a drag that already happened. A box dragged out of the **library**
  has no origin, so Shift there cannot unfile anything from anywhere.
- Every drop handler routes through one `dropBox` helper, or files the move itself. Eleven handlers
  each deciding what Shift means is eleven chances to invent a meaning — and the gesture that looks
  identical to a copy must never be the one that deletes filing.
- The side list is the exception worth naming: every box is already in the list, so a plain drop
  onto it changes nothing, and Shift means "take it out of the box I dragged it from".

The page's status line says which happened — `filed here` against `moved here; it is no longer filed
where you dragged it from` — because the two gestures are indistinguishable by sight and people
need to know, afterwards, whether the box is still where it was.

## Consequences

**Costs.** The viewport box is never *not* on screen, so a pane's view needs a context of its own
(`renderContentIn`, `saveSettingFor`) — the pane machinery grew a seam. The layout lives in config
and is validated on every write path, so `box edit` and a raw config write are held to the same
rules as the page. And `move_from` is a parameter that a page which forgets to send silently turns
into a copy: it is pinned by an assertion on the client source for exactly that reason (that is how
this was first broken — the page said "moved here" while filing a copy).

**What it buys.** Two boxes open at once, side by side or in a pile, with the arrangement stored on
the box, so a comparison survives a reload; and a drag that does what it says, with the difference
between "also here" and "now here" decided once, in the language that owns the graph.

**Verified.** 26 tests in `tests/test_viewports.py` (both split directions and nesting, the two id
namespaces, the ratio clamp at both edges, close reclaiming exactly the sibling's space with the
outer ratio untouched, the last pane refusing, one box per pane, the same box twice — that is what
comparison means — windows moved/resized/raised/closed with a floor on size, `show_in_window`'s two
rules, unreadable stored geometry degrading to one pane with the reason kept, and persistence read
through a second app on the same database), plus API tests that a copy leaves both parents, a move
takes one off, and a move out of a box that never held it changes nothing. In the browser, measured:
one pane at 1204 px, split → 599 / 7 / 599, a second split inside the right pane → 438 px tall each,
the divider dragged 260 px → 698 px and **698 px after a reload** with `ratio 0.7055` stored; closing
a pane returned its space to the sibling and left its box existing with the viewport off its parents;
the last pane's close was disabled and the API refused it in words. Three windows stepped by 32 px,
one moved to (600, 368), one resized to 816×536, both back exactly there after a reload. Drops into a
folder, a grid cell, a desktop, a checklist line, the mindmap board, a viewport pane and the side
list, each plain and with Shift: plain leaves the box a child of both (`["SrcMap","TheMap","cabinet"]`
after a drop onto a map, with the status naming it), Shift takes it off the origin
(`["TheMap","cabinet"]`), and nothing was ever deleted. A mindmap **node** keeps its own gesture — it
fills the node with a box that already belongs to the map, and says so when one does not — which is
why the board is where filing happens. No page errors.
