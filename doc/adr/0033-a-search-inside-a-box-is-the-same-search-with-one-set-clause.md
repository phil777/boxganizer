# 33. A search inside a box is the same search with one set clause

Date: 2026-11-01
Status: accepted

## Context

Every organizer box needed a search of its own: type a word into the box you are looking at and be
offered what matches anywhere below it — children, grandchildren, and the words inside their files.
The obvious build was a second search: walk the subtree, gather the boxes, ask each one, recurse into
folders, read files as you go. That is a scan, and Boxganizer already owns an index built for exactly
this question (ADR 10), with widening and passages behind it, and already knows that a folder's index
is the files inside it (ADR 16), and that the graph may contain cycles (ADR 9). A scan would have
disagreed with the left pane about what a word finds, and would have cost a folder walk per keystroke.

The user's constraint was blunter: reuse the index, *"not a new kind of search where all resources
have to be scanned again"*.

Three things fell out of taking that seriously, and each one is a decision rather than an
implementation detail.

## Decision

**Scoped search is the ordinary search plus a set.** `GET /api/search?q=…&under=<box>` answers from
`store.descendants(under)` — a BFS over the graph that has already visited a box never visits it
again, so a cycle terminates instead of looping, and a box filed under two parents is listed once,
under its shortest route. That uuid set goes into the same `store.search` and the same
`store.content_search` as one extra clause. Nothing is re-parsed to answer a query: the proof is a
test that makes `search._extract` raise, and the search still answers.

**The restriction subtracts; it does not replace.** A scoped search returns the unrestricted answer
with out-of-set hits removed — same order, same `how`, same passage. Two queries that could rank or
snippet differently would mean the reader sees one word mean two things depending on where they typed
it.

**A directory's text is one link below its own resource row.** The set clause for the content half
must walk `resource_link`, and that table joins *resources to resources* — the shape ADR 16 describes:
a folder's files hang under the folder's resource, not under the box. Joining `parent_id` to `boxes.id`
returned nothing for a search inside an indexed-directory — and returned the right-looking answer from
a parent whenever some box happened to carry the integer the directory's resource owned, which in a
fresh library it did: root is box id 1 and the first resource is id 1. The clause starts at the scope
boxes' `resource_id` and follows the links. Recorded because the bug hid behind tests that pointed boxes
straight at files, which never cross that link, and because an id collision that produces correct
output is the worst kind of passing test.

**One widget, mounted per surface.** `mountBoxSearch(mount, owner, open)` is called from the tag line
and from `paneShell`, so a folder, a grid, a desktop, a mindmap, a cabinet, a checklist, a directory,
an indexed directory, a dynamic folder, a tiling pane and a windowed window share one field, one
window, one keyboard path and one blur rule. A copy per view is a bug per view: the folder would get
Arrow-Down and the grid would not, and that arrives later as two tickets. The owner is read through a
getter, because a pane's box changes under it — searching a pane after walking two levels deeper means
searching *there*.

**The same hit means two different journeys, and the origin decides which.** From the library list a
result opens as the current box and the box you searched from stays in the crumb line, because there
"how you got here" genuinely is the page's own trail. Inside a viewport a result replaces the pane's
box, and the crumb line shows the **route the server worked out** — not the pane's history with the
hit appended, which is a path nobody walked, and not the whole tree above the search either, which
would claim ground the search never covered. A hit therefore carries `route` as `{uuid, name}` pairs,
not names: names draw a crumb and cannot be clicked, and two boxes may share a name.

**A hit that is a file belongs to the box that holds it.** The FTS answers with a file; a person wants
a box. `boxes_for_text` resolves the file to every box pointing at it — and one file can be pointed at
by three boxes, all of them true answers, so all three are offered with the same passage. Clicking one
opens that box, refills the field with the term, and unrolls the window there, because a reader who
clicked a file wants the file, not a box they now have to search again. The pane path needs no handoff
(a pane repaints in place, so the widget that asked is still there); the main line does, because the
tag line is rebuilt from scratch, and the term waits in a page-level variable for the next widget to
pick up.

## Consequences

- A search inside a box can only find what the index holds. A box outside the content confine was
  never indexed (ADR 30), so it will not answer — which is now visible as "nothing in here matches"
  rather than being mysterious, because the same phrase is what an empty subtree says.
- Scoping to an empty box answers empty. Reading "no boxes in scope" as "no restriction" would make a
  search inside an empty folder return the whole library: the opposite of what was asked.
- Membership of a dynamic folder is cut into the link table by its filter (ADR 25), so a scoped search
  sees a dynamic folder's members as its descendants. That works, and it depends on a filter that
  actually matches: `_match_text` folded the held text but not the value, so `name contains 'Hit'` —
  every capitalised filter — selected nothing in silence. Fixed separately, because the search
  measurement was the thing that noticed.
- The results window is anchored to its own mount, so a pane's results open under that pane and can
  never cover a neighbour. Measured: two panes, one searched, the other's body byte-identical before
  and after.
- Every keystroke costs one indexed query, not one subtree walk. The debounce (180 ms), the stale
  keystroke token and the pane-generation check are all still needed, because the index is fast enough
  that replies now arrive out of order.
