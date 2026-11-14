# 46. A directory's search field asks the index, and answers with files

Date: 2026-11-14
Status: accepted
Related: ADR 33 (a search inside a box is the same search with one set clause), ADR 39 (a field with a list under it narrows the list), ADR 16 (a folder's index is the files inside it), ADR 44

## Context

`work_to_do.md` reported the field as broken: *"The search field from an indexed directory box does
not work. It only filters on file names containing the research terms. It should also do FTS on all
the document inside this directory and sub-directories, and display files matching in this directory,
and directories containing files or directory matching."*

Measured before changing anything, on a library built for the purpose — one folder with
`mortgage.md` (containing `boiler`) and `drawings/plan.md` (containing `cement`):

| term | whole library | scoped with `under=<box>` |
| --- | --- | --- |
| `boiler` (only inside `mortgage.md`) | `['Site']` | `['Site']` |
| `cement` (only inside `drawings/plan.md`) | `['Site']` | `['Site']` |
| `mortgage` (the file's own **name**) | `[]` | `[]` |
| `drawings` (a **directory's** name) | `[]` | `[]` |
| `anode` (nowhere) | `[]` | `[]` |

The report's diagnosis was wrong in one direction and right in two others.

**Full-text search already reached the files, and the subdirectories.** The index is not the field's
problem. What the index answers with is: the *box*. Inside the folder you are standing in, "the folder
matches" is not an answer — and the field beside it only narrowed the rows on the screen, which are
one directory level deep, so a file two levels down had no row to appear in.

**A name is not searchable anywhere in the library.** `mortgage` returns nothing. A folder listing is
mostly a hunt for a name, and the one thing a reader is sure of — the filename they can see — was the
one thing the index could not be asked about. `resources.reference` holds the path, but the full-text
index holds only file *bodies* (`resource_text(resource_id, body)`).

Two mechanical facts decide the shape of the fix:

- `/api/search?under=<uuid>` scopes by a set of **box uuids** (`store.descendants`) and answers boxes.
  A file inside an indexed folder is not a box: it is a resource with no box of its own, which is
  precisely why the folder answers for its words (ADR 16).
- The walk files leaves. A directory *inside* an indexed folder is not a resource row anywhere, so it
  has no name to match against — only the path every file beneath it shares.

The user was offered the fork explicitly and chose the harder half: the field **always** asks the
index, and the listing becomes the hit set — accepting that a partially typed filename no longer
narrows the rows instantly.

## Decision

**One new endpoint answers at file granularity: `GET /api/boxes/{ref}/search?q=`.** It returns the
files and folders under that box that answer, each with `sub` (its path inside the directory),
`how` (`name`, `content`, or both), and the passage the index produced; plus `directories`, the
folders on the route to a match with their counts. `GET /api/search?under=` is unchanged and stays the
way the page searches *the graph below a box* — it answers boxes, which is what the left pane lists.

**The expression is the library's, not a new one.** The handler calls `text_search.plan()` once and
hands its `content_match` to the same full-text index, so widening, synonyms, bm25 ranking and the
`[bracketed]` passage are shared rather than reinvented. Only the set is smaller, and the set is a
clause on the same query — which is the rule ADR 33 set, even though the set here is of resource ids
rather than box uuids (the answers are files; see Consequences for what that costs).

**Names are matched as strings, not stemmed.** `mort` must find `mortgage.md`, which a porter-stemmed
index would not do, and a name match should behave like the completion a reader expects from a
listing. Directory names are matched out of the path components shared by the files beneath them, and
a folder that answers on its own name is a row with a count, not a route to a row that isn't shown.

**Whether a field may ask is answered by the server, in the response the view already holds.**
`/listing` now carries `indexed`, read from the resource's `index_it`. It is *not* the box type's
`indexes_content`: that flag means "this type reads its own file", and `indexed-directory` answers
`False` while every file inside it is in the index — the first version of this change asked the wrong
flag and the probe caught it. A plain `directory` box, whose contents were never read, keeps ADR 39's
local narrowing, because pretending to search words the library does not hold is worse than a field
that admits what it can do.

**The wait is visible.** While the request is in flight the previous rows stay on screen and the bar
says `asking the index about X…`; there is no second debounce (the search widget's 180 ms already
governs the field), and the widget's results window — which ADR 39 withdrew for hits the list
displayed — retires itself when the rows land (`sink.dismiss`), because the answer is now on the list.

## Consequences

- ADR 39 is *not* superseded. Its rule — a field with a list under it narrows that list rather than
  drawing a window over it — still governs every surface that lists boxes, and still governs the
  contents of an indexed directory when the term matches a name on the level being looked at. What
  changes is what "the list" is allowed to contain: rows the index named, including rows that are not
  on this level. Where the index has nothing to say (a plain `directory`), the old branch stands and
  its test still pins it.
- The cost the user accepted is real and stays accepted: between the last keystroke and the answer the
  listing shows the *previous* state. The mitigation is disclosure in the bar, not a fake instant
  result. A term that matches nothing answers with an empty listing and no error.
- Content hits and name hits are ranked by different rulers, so they are ordered by kind (names
  first) rather than by mixing bm25 scores from different sets. This is a listing, not the search
  page; a reader looking for `mort` knows exactly what they typed.
- Scoping by resource ids costs one indexed prefix scan of `resources` for the box's own subtree, plus
  the FTS query. Measured on a copy of a 842-resource library: `boiler` 121 files / 58 folders in
  87 ms, `invoice` 79 / 44 in 41 ms, `anode` capped at 200 in 171 ms. Nothing is opened to decide
  membership — the index answers, which is the reason it exists.
- A file that matches both ways says `content+name` once, with one row. Two rows for one file would
  make the count of matches a lie about the number of files.
- Because the answer can name folders that are not on the displayed level, a folder row survives the
  filter *because of something below it* and says so (`1 match inside`). Without that, the deepest
  match is counted, reported, and unreachable — which is the failure ADR 39 was written for, arriving
  from the opposite direction.
