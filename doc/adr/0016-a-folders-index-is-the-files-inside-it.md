# 16. A folder's index is the files inside it

Date: 2026-11-06
Status: accepted

## Context

The panel now says what the index holds — how much of it, what is out of date, what a pass
read — and that number has to mean something. Which exposed a row in the `resources` table
that had no honest answer: a folder, marked `index_it`, with `indexed_at` set and nothing in
the index. A folder has no words. Two ways out of that existed.

**Count folders as neither indexed nor failed** — a special case in the counts, and a panel
line that says "2 could not be read" about two folders forever, because nothing will ever
read them. That is what the first version of the status line did, on a library with a folder
box in it, and it was useless.

**Say what `index_it` on a folder means: index what is inside.** A folder box names a
directory of files. In the library of a person who keeps notes in a folder, that folder *is*
the material, and searching it is the reason they asked for full-text search at all. A
folder that cannot be searched is the failure mode where the search finds nothing and the
person has to work out why.

The second answer raises the question the schema has to settle. The files inside a folder are
not boxes — the library holds the folder — but the index needs them as if they were: each
with its own modification time, its own text, its own "has this changed since it was read"
answer, because they change independently of each other and of the folder.

## Decision

**A folder marked for indexing is walked, and the files it contains become resources of their
own**, each with its own row, its own fingerprint and its own entry in `resource_text`.

**`resource_link(parent_id, child_id)` records which folders a file was found inside.** Links,
not ownership: `notes/` and `notes/deep/` both indexed means `notes/deep/b.txt` is one row
with two links and one copy of its words. Deleting a folder row cascades its links and leaves
the files, because a folder stopping being indexed is not a statement about the files.

Because the files are ordinary resource rows, **the freshness mechanism is untouched**: a
nested file edited since it was read goes stale on its own, and `check_observations` still
costs one `stat` per file. This is the property that made the design right before it was
finished — nothing new had to be invented to answer "is this one out of date".

Four rules the walk follows, none of them free:

- **An explicit no survives a walk.** A file the person opted out of is skipped on every
  pass. `index_it` on the folder says what the folder wants for the files inside it; a no
  said about one particular file is a better answer than a walk that overrules it.
- **What a folder brought in, the folder lets go.** A linked file that has vanished from
  disk has its words taken out of the index: a search that keeps offering a note somebody
  deleted is the failure that stops people trusting the search box. Its row goes only if
  nothing else needs it — no box points at it, and no other folder still claims it.
- **Hidden entries, symlinks and unreadable kinds are not walked in.** `.git` and `.venv` are
  not what a folder of notes means; a symlink out of the folder is a way of indexing half the
  disk through a door the content confine was set up to keep shut, and a symlink into an
  ancestor is an infinite walk; and a `.png` given a row is only ever a "could not be read"
  line on the panel.
- **The walk is capped** (`MAX_DIRECTORY_FILES`) and says so when it stops. The alternative to
  a number is a first pass over a mistaken folder that never finishes.

**Discovery and cost are separated by intent.** Saving a box calls the cheap door, which
refreshes what the folder already knows and re-lists only if the folder's own mtime moved.
A pass over the library — `box index`, or the panel's "Index what changed" — always lists
every folder it reaches, because a file created two levels down does not touch the mtime of
the folder above, and a button named for changes that misses new files is a button nobody
believes. Listing a folder is not reading it.

**A hit on a file inside a folder answers with the folder, and names the file.**
`store.boxes_for_text` returns `(box, path_inside_or_None)`; the page shows
`deep/b.txt: an order for [cement] mixer parts` and the CLI shows `🗂️ Notes → a.md`.
Otherwise the search returns a folder for a word that is not in it, which looks like a bug.

## Consequences

- `indexed-directory` now means what its name says and is opted in by default; its `planned`
  line lost "index contents" and kept the part still missing — noticing changes on their own
  rather than on a pass.
- The index counts gained `folders`, `unreadable` and `untouched`, because "indexed" has to
  mean *holds words*. A folder that was walked and a file that could not be read both have an
  `indexed_at` and neither has any text, and counting them as indexed is how a panel ends up
  saying everything is fine about a library where most of it was never opened.
- A library can now hold far more resource rows than boxes. `resource_link` has an index on
  the child, the counts are one query, and `boxes_for_text` costs a lookup per hit; nothing on
  the panel walks the disk except when it is asked to.
- Text files that are *not* inside any indexed folder still need a box, or `box index --add`,
  to be searched. The folder is how a directory comes in, not a way of indexing the machine.
