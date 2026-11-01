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

---

## Amendment (2026-11-10): a pane folds its own settings, and its own only

The first build left one thing shared that should not have been. The settings row of every view is
folded behind a gear in the page's top line, and the fold was a class on `#canvas` — written for a
screen showing one box. Inside a viewport that meant a single gear for four boxes: opening the grid's
columns also unfolded the checklist beside it, and a second person's viewport would move under the
first's click.

Each pane carries the decision now. The pane's path line holds its own gear, and the rows take the mark
their own pane made (`pane-folded`) rather than a selector guessing which container a row belongs to —
in a viewport inside a viewport there are two answers, and the innermost is the one that governs.
`#canvas:not(.config-open)` therefore excludes anything inside a pane (`:not(.vp-pane *)`), so the
header gear folds the box on the main screen and nothing else.

The gear hides itself in a pane whose box has no settings row, exactly as it does on the main screen:
a control with nothing to control is decoration. A checklist shows none — its head reports a tally, it
does not configure — and neither does a note, whose toolbar is how you write (ADR on the note switch),
nor a viewport, whose panes each have one of their own.

---

## Amendment (2026-11-11): a window has a state, and the window you chose is the one on top

The first build let a window be moved, resized, raised and closed. It could not be minimized,
maximized or split, and it could not be made transparent. Four decisions came out of building those.

**A window carries a state and, only while maximized, the rectangle underneath.** `normal`,
`maximized`, `minimized` — and the rectangle is never discarded when one of the other two is on.
Maximizing keeps the rect it covers; minimizing keeps the rect it had, including the one from before a
maximize. So "unmaximize" and "put the minimized window back" need no bookkeeping beyond the window
itself, and both survive a reload because `as_dict` writes them. The consequence worth naming:
`move_window` and `resize_window` must rebuild a window *with* its state. They did not, and dragging a
minimized title across the board reopened it — a layout write undoing a button, which is invisible from
the screen and merciless in a test.

**Transparency is not part of the layout.** It lives in its own config section (`windowed.opacity`,
0.2 to 1) because the layout section is rewritten wholesale on every split, move and resize, and how
solid your windows are is not where they are. The floor is where the box inside stops being readable,
and it is refused by the type's `check` — so a raw config write, `box edit` and the slider pass the same
door. The slider fades the whole window, contents included, because the reason to make a window
transparent is the box underneath it; a window whose frame is see-through while its contents stay
painted is a window you still cannot see past. **The focused window is exempt and always drawn solid**:
the one you clicked is the one you are reading, and the rest may recede.

**Splitting a window is a different act from splitting a pane, behind the same verb.** A tiling pane
takes its half from the space a divider already governs; a window has no neighbour whose room it could
take, so the two halves sit exactly where the one was, and the new half is empty and on top — the
second thing to look at is chosen next, not guessed. A window too small to halve is refused with the
edge to grow (`not wide enough… drag its corner out first`), and one that is maximized or minimized is
asked to be restored first: half of a maximized board is not a window anyone placed.

**Choosing a window raises it, and the pile must move now.** Three faults, all found by measuring: the
path line is also the drag handle and it stopped propagation, so clicking a window never raised it;
raising rewrote the order on the server while the page left `z-index` as it was, so the chosen window
sat under the pile until some later repaint; and a button press raced the raise it caused, so the window
you acted on could finish underneath everything. Now the frame listens in the capture phase and lifts
the window above its siblings immediately, buttons are excluded from that path because the actions that
come with them raise the window server-side (maximize and minimize that stay buried do neither job), and
a maximized window's title does not drag — it is the board.

---

## Amendment (2026-11-11): a context is two things, and an embedded box needs both halves

A pane renders its box with a context of its own — its trail, its status line, its reload. Only its
*own* box. Everything the view embedded (a folder inside a grid cell, the child under the root view)
was drawn through `renderContent`, which handed over the page's context. So the deepest box asked the
page to navigate, and a double-click on a grandchild replaced the viewport that was holding it. The
write side was wrong in the other direction: `saveSetting`/`saveSection` read `state.current`, so a
folder embedded in a grid cell saved its layout onto the grid, and on the root view the root box
accumulated its child's settings.

The fix splits the pair at the one place every view is entered from — `renderContentIn` — rather than
in each view, because a rule every view has to remember is a rule one view will forget:

* **Where a click goes** belongs to the space the box is drawn in: the page, or one pane. That is the
  caller's context, now threaded through `renderContent` instead of replaced by the page's.
* **What a setting writes** belongs to the box whose view is writing it. `contextFor(box, ctx)` aims
  `saveSetting` and `saveSection` at the box in hand when the context is about a different box, and
  leaves it alone otherwise — so nothing changes for the depth-zero case that was already right.

For a context to know what it is about, both surfaces say so: `owner: () => state.current` on the
page, `owner: () => target` on a pane.

One consequence measured rather than assumed: a gesture that a nested view claims must stop bubbling,
or one double-click performs two navigations — the embedded row *and* the cell around it. Folder rows,
grid chips, desktop tiles, directory rows, note mentions and checklist chips now say so.

The root view is the case where the two halves disagree in the open: it is the main screen, so
navigation there *should* replace the page — and its child's settings still belong to the child.

---

## Amendment (2026-11-11): transparency goes all the way, a window is not split, and close keeps the corner

Three changes to the windowed viewport, two of them corrections to this ADR's own first judgement.

**Transparency runs the whole way, and it starts at invisible.** The control stopped at 0.2 solid with
a default of 1, on the grounds that a window you cannot read is no use. That was the wrong lesson: the
window you *are* reading is the focused one, and it is drawn solid whatever the slider says — so the
rest of the pile is allowed to recede entirely, which is what the control is for. It is now named
**transparency**, runs from 0 % (invisible) to 100 % (solid), and **defaults to 0**: opening a windowed
box shows one solid window on the desk and the others as titles and edges until you touch them. The
type refuses only what falls outside 0…1.

**One number, one key, no migration.** The value stored is still `windowed.opacity`, meaning exactly
what it meant before: the proportion of the window that is painted. Renaming a stored key to match a
control's label is how a library ends up with two answers to one question, and the direction the
control reads in (0 invisible → 100 solid) is the same direction the number already went in, so a box
written yesterday keeps its look untouched and needs no repair pass. The label and its tooltip say
which way is which; the title spells it out because the word "transparency" is honestly ambiguous
about it.

**A window is not divided, so it is not offered dividing.** Tiling panes keep both split buttons;
windows lost them. A window floats — the person already knows where its edges are, and the split that
would mean something there cuts *that window* in two, which the button used to promise and the API used
to refuse.

**Close lives in the corner.** The `×` is the last control in a window's path line and flush to the
frame's top-right border, because that is where a hand already goes. The state buttons are inserted
before it, so a window growing a button does not move the one thing people look for.
