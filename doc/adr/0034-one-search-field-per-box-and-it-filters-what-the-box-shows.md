# 34. One search field per box, and it filters what the box shows

Date: 2026-11-07
Status: accepted, in part — ADR 39 withdraws the unrolled window for any surface that has a list to
narrow. The one field, the `filterSink`, the per-surface handlers and "a match you cannot reach is a
dead end" all stand.

## Context

After ADR 33 a box had two search fields. The tag-line field unrolled a window of descendants to jump
to. The folder's own toolbar held a second field — `name or type` — that hid rows which did not contain
the term as a substring of the name or the type label. Both said "search". They did different things,
they were styled differently, they were in different places, and only one of them knew anything about
file contents. A reader typing a word had to know which box they meant.

The request was to make the *content displayed* filter by the search. The obvious build was to teach the
folder's field to use the scoped search and leave the two fields alone. That keeps the ambiguity it was
meant to resolve.

## Decision

**One field, on the tag line. It unrolls the jump list and it narrows the view underneath, from the same
answer to the same query.** The folder's `name or type` box is deleted — not hidden, not disabled: the
control, its CSS and its matcher are gone. Its remembered-searches list came along to the surviving
field, under the same `config.folder.history` key, because that memory belongs to the box and not to
whichever field happened to type it.

**The view is told, not re-queried.** Each render surface owns a `filterSink` — a set of handlers. A view
that can narrow itself adds a handler; the field calls them all with `(term, hits)`, the hits being the
response to the one scoped search it already made for its own popup. A view never asks the server again,
so the list and the popup can never disagree about what matched: they are the same array.

**Handlers, plural, and one sink per surface.** A folder can be embedded — a folder inside a grid cell,
the root's own block — so more than one view under one surface may answer to one term, and they should:
they are all part of what that box is showing. A viewport pane gets its own sink, created by the pane's
shell rather than by the per-draw render context, because the field outlives the draws and a neighbour
pane must not be narrowed by a term typed here. Measured: two panes, one searched, the other's list
byte-identical before and after, and exactly one results window open on the page.

**A match you cannot reach is a dead end, not a filter.** The visible set is the hits *plus every box on
the route to a hit*, taken from the `route` ADR 33 put on each result. Searching a folder for a word held
by a box three levels down keeps the drawers above it open on the screen. Hiding them would have made the
filter destroy the very thing it found, and the reader would have had to clear the search to navigate to
the result they were searching for.

**A file listing narrows by name, and by what the index found inside.** Files are not boxes, so the
scoped search cannot name them. But a hit on a directory carries `in_file` — the file whose words matched.
So a directory's listing keeps a row when the term is part of its name *or* when the index says the words
were found in it. The second half is the entire point of indexing contents: without it, a search for a
phrase could announce "this folder matches" and then list none of the files that made it match.

**The term is not remembered as a filter, only as a term.** `config.folder.history` grows, and a
reopened box offers past terms back; the view opens unfiltered. A folder that reopened already narrowed by
a search typed days ago shows a shelf with things missing and no live reason on screen — a look at the
shelf, not a saved view. Escape clears the field and the list together, which is the way out of a filter
and was the old field's way out too.

## Consequences

- The options row that used to hold the folder's field is collapsible chrome (it always was), so
  filtering moved from a row that needed the gear open to a field that is always on screen. That is the
  user-visible gain beyond having one box instead of two.
- `directory` and `indexed-directory` now share a filtering rule with `folder` and `cabinet` even though
  they draw entirely different views: the sink is the seam, not the view.
- The count in the options row says `2 of 4 inside`, and a directory's bar says `· 1 of 3`. A filter that
  silently reduces a list reads as missing data; the fraction is what tells the reader the rest is hidden,
  not gone.
- The widget's owner is read through a getter (ADR 33), so filtering follows a pane as it walks: search at
  the top of a tree, walk two levels down, and the field is now narrowing the deeper folder with the same
  term.
- Because one search serves both jobs, a keystroke costs exactly one indexed query. The popup was already
  debounced and token-guarded; the list now inherits that, and inherits the stale-generation rule that
  stops a reply for a box that has left the screen from painting on this one.
