# 8. Layout and view settings live in the box's config JSON

Date: 2026-10-24
Status: accepted

## Context

The web interface is not one page: a folder is browsed as a list or as icons, a
grid has rows, columns and cells, a text file has a font size and a wrapping
choice. All of that is per-box state, and it has to survive a reload, a different
browser and a rename. It also has to coexist with a graph that already has its
own structure: which box contains which, in the `links` table.

Storing a grid layout raised the question sharply. A cell holds a child; the
child is already a row, linked by a parent id. Do we add a `cell` column to
`links`, do we make a table for layouts, or do we keep the arrangement with the
box that draws it?

## Decision

Layout and view settings go in the box's own `config` JSON, one namespace per
view: `config.display`, `config.folder.layout`, `config.text.{wrap,crop,font_size}`,
`config.grid.{rows,columns,cells}`. Containment is never duplicated — the
`links` table stays the only statement of who contains whom — and `cells`
references children **by uuid**, the identifier added for exactly this reason.

Placement rules (swap, displace to the first free cell, unplace on shrink) live
in the core module `boxganizer/grid.py`, not in JavaScript. The web layer exposes
`grid/place`, `grid/resize` and `grid/unplace`; the browser asks, the core
decides, and the result is one `config` write.

Views on the front end mirror the back end: a view registers under a box type key
and is looked up by walking the type's ancestors, so `cabinet` gets the folder
view without saying so, and `markdown` gets the text view from `text`.

## Consequences

* A layout follows the box: export the row, open it elsewhere, the grid is still
  arranged. No extra table, no migration per view added.
* Renaming a box cannot scramble a layout, because cells hold uuids. Names would
  have made `box box edit` a destructive operation.
* Two sources of truth for the same children — `links` and `config.grid.cells` —
  can disagree. The rule is that `links` wins: a child with no cell is still a
  child, is still shown, and the UI says "Not placed" rather than hiding it.
  Shrinking a grid unplaces; it never deletes.
* Settings are unvalidated JSON in a column. Accepted for view preferences; a
  grid size is clamped (1..24) and a malformed `cells` map degrades to empty
  rather than raising, so one hand-edited row cannot break the page.
* The core owns placement, so the same rules will serve a second interface (the
  text UI, or a script) without reimplementing them in another language.
* Namespaces are a convention, not enforced: two views choosing the same key
  would overwrite each other. Worth a check if the set of views grows.
