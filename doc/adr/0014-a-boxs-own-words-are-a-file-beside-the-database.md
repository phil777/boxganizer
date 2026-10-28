# 14. A box's own words are a file beside the database

Date: 2026-11-04
Status: accepted

## Context

Every box so far points at a thing that already exists elsewhere: a file, a directory, a
URL, an MQTT topic, an SSH host. A checklist and a note are the opposite. They are boxes
whose content is written *inside* Boxganizer, and when one is created there is nothing on
disk for it to point at.

Two places were available for those words.

**A column.** The `boxes` table has `description`, `metadata` and `config`; a `body`
column would be the fourth, and it is the short path. The words would be saved with the
rest of the row, and the page would read them back.

**A file.** The words go on disk, in a directory beside the database, and the box is given
a resource row pointing at it — which is exactly the shape every other box has, and the
shape ADR 13 exists to make cheap.

The column is shorter, and it is what the item did not ask for. But it loses three things
the item named, and one it did not:

- It is not a file, so `grep` finds nothing, a backup is a database dump rather than a
  directory copy, and a library whose database is lost has no notes left in it.
- The full-text index (ADR 13) is keyed on a *resource*, and a resource is a pointed-at
  thing with a modification time and a hash. A column has no mtime an outside editor could
  move, and no way to be anything other than the row. Indexing a column would mean
  inventing a second, worse kind of resource — a special case at the exact place where ADR
  13 had just removed the special cases.
- The words can only be what the page lets them be. A file can be edited by hand, or by
  the person's own editor on a machine that has never run Boxganizer, and be read back.
- And the thing the item did not name: a checklist is a list of things to do, and people
  keep lists in text files. A format that reads as `- [ ] Buy fittings` in `cat` output is
  a format the person owns. A column is ours.

The genuine cost of the file is a lifecycle. A row can be deleted with its box; a file
cannot be un-written on someone's behalf. And a file has a location, which brings the
content confine — the rule that says which paths this machine may open — into a place it
was never meant to reach.

## Decision

**Words a box writes itself live in `<directory-of-the-database>/files/<uuid>.md`.**
The module is `boxganizer.authored`; the format inside is markdown (task-list syntax for a
checklist, plain markdown or text for a note), and the box is given a resource row whose
`origin` is `generated`.

Four details are load-bearing:

**Named by uuid, not name.** A note called *Shopping* gets renamed. A file named after it
would have to move; a moved file orphans the index entry written under the old address,
which then answers searches for a file that is no longer there. A uuid does not move.

**Created on first use, never overwritten on open.** `ensure_file` writes a seed only when
the file is absent. Opening a note must not be able to empty it, and an edit made in a text
editor is more recent than anything the page remembers.

**Written through a temporary file, then `os.replace`.** Either the old content is there or
the new content is, never half of either.

**`origin = generated` is the only way out of the content confine, and it is not
reachable from a path.** `$BOXGANIZER_CONTENT_ROOT` names the directories this machine may
read, and it is routinely set to one project directory — which would put the notes
directory outside it and quietly make every note unindexable and every checklist unreadable
on the page. So `search.extract_resource` reads a resource without the confine test when,
and only when, the *row* says `generated`. Nobody can obtain that by typing an address:
`origin_of(path)` never returns it, and the flag is set in one place, `authored.adopt`,
for a path this module wrote. A box pointed at the notes directory by hand is `local`, and
is confined like any other file box.

## Consequences

- **The library is copyable and greppable.** `files/` beside the database is the notes. A
  restore that loses the database keeps them; the uuids in the filenames are the clue to
  putting them back.
- **Files outlive boxes.** Deleting a checklist box deletes the row and its index entry, and
  leaves the file. That is deliberate — the words cannot be reconstructed, the row can —
  and it is pinned by a test so it is not "fixed" into data loss later. The cost is orphan
  files, which are invisible to the app and obvious to whoever lists the directory.
- **Two sources of truth have to be kept in step, and the direction is one-way per fact.**
  The links table says a box is a child of the checklist; the file says it belongs to
  *item two*. The file owns the list and the tick marks; the graph owns filing. Attaching
  from the page does both. Detaching removes the link from the line and leaves the box
  filed. A child on no line is shown as unattached rather than hidden, and a line naming a
  box that has gone renders greyed and is reported — not deleted, because the box may be
  one restore away.
- **`writes_own_file` on the type is the question the endpoints ask**, in preference to
  comparing a type key against the string `"checklist"`. A note type keeps a file the same
  way and answers to the same endpoints.
- **The index is not automatically fresh for outside edits, and says so.** A generated file
  is watched like any local one — `stale()` compares mtime against `indexed_at` — so a note
  edited in a text editor shows as changed until the next `box index`. The page reindexes
  on every save, which is the common case.
- **An in-memory library needs somewhere to write**, and `./files` inferred from the string
  `:memory:` would be a surprise in exactly the places that use in-memory databases: tests
  and throwaway demos. It gets a temporary directory instead, whose lifetime matches the
  library's.
