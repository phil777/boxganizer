# 35. A captured file is named by its own hash, so the copy is the record

Date: 2026-11-07
Status: accepted

## Context

A box normally *points* at a file. That is the honest default — the file is the thing, Boxganizer is the
card index to it — but the box dies when the file moves, the disk is reformatted, or the machine the path
names is gone.

The request: when creating a resource, offer to **capture** it — make a copy beside the database, renamed
by its sha256 so identical bytes are stored once, and filed in a hash-table-like hierarchy so no single
directory ends up holding thousands of files.

Three decisions hide in that sentence, and each could have gone the other way.

## Decision

**Capture is per box, offered at the moment of pointing, and off by default.** A checkbox in the create
and edit dialogs, `--capture` on `box add`. Not a library-wide setting, and not automatic: a card index
that quietly swallowed copies of everything it was ever shown is a backup tool wearing an indexer's
clothes, and it would double the disk cost of a library whose whole purpose is pointing at files elsewhere.

**The filename is the sha256, and that is the entire deduplication mechanism.**

```
~/.local/share/boxganizer/
    boxes.db
    files/            <- the same directory notes and checklists already live in
        3f/9a/
            3f9a4c…b7.pdf
```

Two boxes capturing the same bytes compute the same name, so the second capture finds the file already
there, writes nothing, and points at it. There is no reference count to get wrong and no table to keep in
step with the disk, because the identity of a copy *is* its content.

The hierarchy is **two levels of two hex digits** — 65 536 buckets. One flat directory would make `ls`,
backup tools and the filesystem's own lookup the slow thing, which is exactly what the request called out.
Two levels is enough that a bucket only gets crowded in a library of hundreds of thousands of files, and
three would make a human reading the tree lose the name inside the path.

**The original extension is kept.** Type inference, MIME guesses and "open this in an editor" all read it;
a captured PDF would otherwise be indistinguishable from a captured text file. The suffix is sanitised to
`\.[A-Za-z0-9]{1,12}`, so an extension cannot smuggle a path.

**A captured file certifies itself, and the name is trusted only after it is checked.** Because the name
*is* the hash, `verify(path)` answers whether the bytes still match. That is what makes the library's copy
identifiable without a database lookup — which matters most at the moment a box is deleted, when the box
that pointed at the file no longer exists to ask. It also means a hand-edited copy is *detectable*: the
name no longer matches, and `capture` refuses rather than writing over somebody's work or silently renaming
ours. The library's own store being tampered with should surface as an error, not be smoothed over.

**The write is staged and moved, never written in place.** A half-copied file named by a hash it does not
match would be a file that lies about what it is, forever. So: temp file in the destination bucket, then
`os.replace`.

**Capture reads what it is told to read, even outside the confine.** ADR 30 gates *writes to the library*
and the browsing of folders, because the concern was the library wandering the disk by itself. Capture is
a person naming one file and asking for a copy of it. It is also the only deliberate way to bring an
outside file in under `--confined`: the copy happens first, so the address the confine is asked about is
the library's own.

**Nothing is captured that is not a single readable file.** A folder, a remote address (`ssh:`), an absent
path and a tampered target are each refused with a sentence written to be read in the dialog that caused
it, and the box is **not created**. A box pointing at a copy that was never made is a worse outcome than
no box at all.

## Consequences

- A captured box survives deleting the original. It does not survive `files/` being deleted — capture is
  not a backup strategy, it is one copy in one place, and `box check` counts what is there.
- Two boxes can now share one file *without* sharing a parent, a folder or anything else in the graph.
  That is what makes deletion subtle, and is why deleting a box moves a captured file to a trash folder
  only when nothing else still points at it (ADR 36).
- The storage cost of a captured library is the size of its content, deduplicated. Capturing the same
  attachment into fifty boxes costs one copy.
- `files/` now holds two different families: Boxganizer's own words (named by box uuid) and captured
  copies (bucketed, named by hash). They are distinguished by shape, and the shape is verifiable — a
  captured file's name is checkable against its bytes, a uuid file's is not.
- Capture is a front-end act on purpose: `capture.capture()` is a library function with no notion of
  HTTP or argv, and the web app and the CLI each call it before handing the box to the store. The store
  stays storage.
- The measurement caught a harness bug worth recording, because it masqueraded as a product bug: the probe
  deleted its own data directory on startup while the server was already running on it. The database
  handle stayed open on an unlinked inode and every write failed with "attempt to write a readonly
  database". Fixed by giving the runner sole ownership of the data directory's lifecycle.
