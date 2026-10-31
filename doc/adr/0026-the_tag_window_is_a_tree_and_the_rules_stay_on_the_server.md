# 26. The tag window is a tree, and the rules stay on the server

Date: 2026-11-11
Status: accepted

## Context

The tag management window was one `<li>` per tag carrying seven controls: name, count, a parent
`<select>`, a colour well, "auto", Save, Delete. With thirty tags that is two hundred controls on
screen, the hierarchy was drawn as indentation on a form, and every edit was a trip — change a field,
find that row's Save, notice whether the row you meant moved. It also ordered tags the way the endpoint
returns them, which is **by level**, not by branch: `home/kitchen` arrived after `work`, so the window
indented a child under a sibling. Nothing was broken in the library; the window just did not show it.

The brief was "redesign it with state of the art UI", with two bounds held from the start: the tag rules
are the server's (ADR 19 — a name is the identity, parents are never stored, a cycle is refused), and no
new endpoint would be invented to make a screen easier.

## Decision

**A row is the tag.** Caret, swatch, name, counts — and the row is ordered depth-first and indented by
the depth the server already sends. Ordering is presentation, so it happens in the window; the endpoint
still answers by level and nothing else that reads it has to change.

**Everything about *this* tag lives beside the tree**, for the tag you have selected: its colour and the
"auto" that hands it back to the branch above, what is above it, delete, and the boxes to give it to.
Renaming and colouring commit on the field's own change, so there is no Save to lose and no dialog
between a thought and the library.

**Reparenting is a drag.** Dropping a tag on a row moves it under that tag; dropping it in the space
below the list pulls it out to the top level, which is the one move a row cannot receive. A drop that
would bury a tag inside its own descendant is refused on the spot — the server refuses it anyway, and
saying so while the tag is still in the air is cheaper than a red line after the fact. The dragged name
is kept in a page variable as well as in the drag payload, because `DataTransfer.getData()` answers the
empty string during `dragover` and "may I drop here" is a `dragover` question.

**One keyboard model, written on the window.** `↑ ↓` choose, `→` opens a branch, `←` closes it or steps
to the parent, `Enter`/`F2` rename, `Space` jumps to the box list, `/` starts filtering, `Escape` out of
a name returns to the row rather than to the page. `Delete` moves the caret **to** the delete button and
stops; the button then asks once. A keystroke that removed a branch would be a typo away from a
redistribution of the library.

**Bulk assign, with the endpoints that exist.** The detail pane lists boxes (filtered, capped, "keep
typing") and gives the selected tag to the ticked ones using the same `POST /api/boxes/{ref}/tags` the
chip field has always used — idempotent, so ticking a box that already carries the tag costs nothing.
It is an *assign* pane and is labelled that way: no endpoint answers "which boxes carry this tag" (the
counts come from `GET /api/tags`), and inventing one to make a pane symmetrical is the decision this
bound exists to prevent.

**One mutation path.** Every write goes through `tagMutation`, which performs the call, redraws the
tree, repaints the chips, and shouts `boxganizer:relist-folders` so the dynamic folders that filter on
tags re-cut (ADR 25) — a tag edit that updated the tree but left a folder open on screen showing the
members from before the edit was the bug that path was built for.

## Consequences

* The window is the only place a tag's colour, parent and existence are edited; the chip line keeps
  giving and taking tags on one box, which is what it was always for.
* A test lists the tag routes the served OpenAPI reports, so adding one for convenience becomes a
  decision somebody has to notice rather than a side effect of the next redesign.
* Selection follows any click in a row, including on the name field, but that path does not focus the
  row: the click was the person pointing at the tag *and* asking for the field.
* Depth-first ordering happens per open. A library with tens of thousands of tags would want the order
  from the endpoint; nothing in the current design pretends that scale is here.
