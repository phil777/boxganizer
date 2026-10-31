# 25. A folder that filters itself refuses the hand that files into it, and a tag move re-cuts it

Date: 2026-11-10
Status: accepted

## Context

A dynamic folder's members come from an expression over the whole library (ADR 20). Two things about
that were wrong in practice.

**A box filed into one and then lost.** Hand-filing was tolerated: `store.link()` accepted it, the box
appeared in the list, and the next re-list — the filter edited, the folder reopened, any box created —
took it back out. The person had seen it arrive. Nothing said which of their actions removed it, and
the folder's own docstring described the tolerance as a feature ("lasts until the folder is next
resolved"). A box that arrives and then leaves on its own is worse news than a drop that never
happened.

**A tag that moved and left folders behind.** `tag:home` is a question about the tag tree, but a tag
is not a box, so no box-sync path ran when the tree changed. Rename `home` to `house` and every folder
filtering on `home` kept exactly the members it had, holding boxes that no longer answer to it and
missing the ones that now do, until an unrelated box edit happened to stumble past and re-cut them.
The folder's contents depended on what else had been done lately.

## Decision

**Refuse hand-filing, in the type, at the one door.** `BoxType` gained two declarations beside
`allows_children`:

```python
accepts_filing: ClassVar[bool] = True
filing_refusal: ClassVar[str] = ""
```

`DynamicFolder` answers `False` and carries the sentence the user gets. `store.link()` raises
`HandFilingRefused` — after the child is resolved, before anything is written — so `box link`,
`POST /api/boxes/{ref}/children` and every drag and drop are refused by the same three lines that
`ChildrenNotAllowed` already guards. The resolver keeps writing its own links with plain SQL, which is
the point: the rule is about *choosing* a member, not about the links table.

`allows_children` was not reused. It answers "can this contain boxes at all"; the page reads it to
decide whether to draw a drop zone and whether a folder is worth listing, and a dynamic folder does
contain boxes — that is what its list shows. Setting it `False` would have hidden the folder's own
members. Two questions, two answers, two declarations.

**The cabinet is excepted, out loud.** A cabinet's filter selects everything, so a box filed into it
by hand is never taken back out: the hazard the refusal exists for cannot occur, and refusing would
break "New box in Cabinet" — the ordinary way to make a box while looking at the whole library. The
exception is declared in `Cabinet` and tested, rather than being a special case in the caller.

**A tag move re-lists the folders that ask about tags.** `BoxStore._relist_dynamic_folders()` runs at
the end of every tag mutation — add a path, rename, move, delete, assign, unassign, set the set. It
re-resolves each resolving folder. That is more work than the change strictly needs for a rename of a
tag no filter mentions, and it is done anyway: the alternative is to keep a set of folders that
"mention" a tag, which is a cache of a computation over expressions, and the failure mode of getting
it wrong is a folder that lies.

**The page says both halves.** A drop over a filtered folder's sheet puts `drop-refused` on it, sets
`dropEffect = "none"` and reads out the type's sentence — a missing highlight is too easy to read as
"missed", and the pointer is where the hand is looking. "＋ New box here" is disabled with that
sentence as its title, and the context menu's "New box in …" is not offered, because a box made there
could not be filed there. After any tag mutation the page shouts `boxganizer:relist-folders`; folder
views whose type resolves its own membership re-read their children and repaint in place, so the
folder on screen is the folder the filter now selects.

## Consequences

* A refusal at the drop point is *earlier* and *survives* a second tab: the server is the rule, the
  page is the courtesy. Both speak the same sentence because the page reads it from `/api/types`.
* Filing into a filtered folder is now impossible anywhere, including in scripts that used the
  tolerance as an ad-hoc override. There is no override. If one is ever needed it should be a rule the
  filter can express — a tag — not a hand-filed exception the next re-list eats.
* Tag mutations cost a re-cut of every resolving folder, the cabinet included: one pass over the
  library per folder. On a library of thousands of boxes and a dozen folders that is milliseconds, and
  it is the honest default. If it ever hurts, the fix is to make `resolve_folder` cheap for a filter
  it can answer with one indexed query, not to start guessing which folders a tag affects.
* `accepts_filing` is served by `/api/types` and read by the page. A new type that computes its own
  membership sets the flag and writes its own sentence; it does not need to touch the page.
* The cabinet keeps hand-filing, so the two filtered folder types differ in one behaviour. That is
  stated in the docs and covered by tests on both sides, because a rule nobody can demonstrate is a
  rule people will route around.
