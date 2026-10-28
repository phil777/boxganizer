# 13. A resource is a row, and the index is keyed on it

Date: 2026-10-28
Status: accepted

## Context

Full-text search arrived as `box_text`, keyed by box uuid: when a box was created, its
file was read and the words were filed under that box. It worked, and it was wrong in a
way that only showed afterwards.

The wrongness is that a box is not a document. Two boxes can point at the same PDF — one
filed under *Invoices*, one under *2026* — and the index then holds that PDF twice, read
twice, scored twice, and answering a search twice with two identical passages. A box can
be renamed, retyped, unlinked and re-filed, and none of that says a word about the file.
And a box can be deleted while the file is still on disk, still pointed at by another
box, whose index entry vanishes with its twin.

The item that follows this one — notes and checklists kept as files Boxganizer writes,
reindexed every time they change — needs something that a box cannot be: a thing with a
modification time, a hash, a "should this be indexed" flag, and a record of when it was
last read. If those lived on the box, every box pointing at the same file would have to
agree about them, and there is no mechanism for that.

So the question was where the truth about a pointed-at thing belongs.

## Decision

**`boxes.resource_uri` becomes `boxes.resource_id`**, a pointer to a new `resources`
table, and the full-text index becomes `resource_text`, keyed by that row.

A resource row holds:

| column | what it answers |
| ------ | --------------- |
| `reference` | the address as it was written |
| `ref_key` | the normalised form two addresses are compared on, unique |
| `origin` | `local`, `remote`, or `generated` — can this be opened here at all |
| `index_it` | is its text wanted in the index (the dialog's checkbox) |
| `indexed_at` | when the words were last read in |
| `mtime`, `bytes` | what the file looked like then — a stat answers "read it again?" |
| `digest` | hash of the text that went into the index, so a touched-but-unchanged file is not read a third time |
| `stale` | something has been noticed that makes the index wrong |

**The address is normalised before it is compared.** A path is made absolute without
following symlinks; a URL has its host folded and its trailing slash dropped, and
nothing else touched — the path of a URL has case and its query means something.
`/a/../a.txt`, `./a.txt` from `/a`, and `file:///a/a.txt` are one resource. Usernames in
an `ssh://` address are *not* folded: who logs in is part of the identity.

**The box keeps `resource_uri`.** Ninety-five uses across seventeen modules ask a box for
its address; all of them now get it from the join the store performs on every box query,
in the same statement, so a panel listing 500 boxes is still one query. The Python model
gained `resource_id` and `resource`; nothing outside the store needed to learn that the
address lives elsewhere.

**Deleting a box lets its resource go, and drops the row only when nothing points at the
file any more.** The file on disk is nobody's to delete; the row that remembered it, and
the words read out of it, are, once the last reference is gone.

**The confine is the store's.** Reading a file needs to know where reading is allowed, and
a box being created does not know who created it. `BoxStore` is handed a `content_root`
(and falls back to `$BOXGANIZER_CONTENT_ROOT`, then home) and keeps it, so the page's
confine, `box index --root`, and the automatic read-on-create all agree.

## Consequences

- One file, one read, one index entry, however many boxes point at it — and a search that
  offers each of those boxes with the same passage, which is the honest answer: it is the
  file that matched, and each box is a way in to it.
- "Does this need reindexing?" becomes a question with an answer — `store.check_observations()`
  stats the wanted set, `search.rebuild(..., only_stale=True)` reads those alone, and
  `box index --check` can be run on a timer without reading anything. Before this, the
  only option was to re-read the whole library.
- Re-pointing a second box at a file cannot quietly change what is indexed for the first:
  `index_it` is only overwritten when someone names a wish, and the dialog says so —
  *"this file is already in the search index, for every box that points at it"* — instead
  of silently agreeing with the box's type.
- The migration carries the words across rather than dropping them. An index that empties
  on upgrade is not a migration but a loss: refilling it means reading 900 files again,
  on a disk that may be slow, from a machine that may be a laptop.
- `boxes.resource_uri` is dropped at the end of the migration, so there is no second copy
  of an address free to disagree with the first. The cost is that any query written against
  the old column breaks loudly rather than quietly — which is the point.
- Two boxes with different types on one file disagree about nothing here, because reading
  is decided by the thing itself (a `.pdf` name, otherwise text) rather than by the box.
  The type only ever supplies the *default* for a brand-new row.
- Notes and checklists, which the next items ask for, now have somewhere to keep "I changed,
  read me again": a file Boxganizer writes is a resource like any other, `generated` rather
  than merely `local`, and part of the index on the same terms.
