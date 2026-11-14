# 47. A folder that went away is listed as absent, not answered with a 404

Date: 2026-11-14
Status: accepted
Related: ADR 41 (a folder that is not here is not an empty folder), ADR 44 (a gone resource is counted as gone), ADR 22

## Context

Making the Index status dialog's gone line clickable (each box that still points at files that are not
on this machine opens when you press it) meant, for the first time, actually *opening* one of those
boxes. The pane answered:

```
Cannot list /tmp/verify-gone-dir-…/shelved: no_such_path
```

and the browser logged a **404** for `GET /api/boxes/{uuid}/listing`.

The endpoint had been truthful in the narrow sense and wrong in the one that matters. `_box_file()`
resolves the box, then the address, and when the address is not on the disk it raises
`404 {"error": "no_such_path"}` — the same answer it gives when someone asks for `sub=gone.md` inside
a folder that is perfectly present.

Those are two different absences:

- **a sub-path that is not there** — the box is here, the folder is here, and the thing you asked for
  is not. No such path. A 404 is exactly right; a test already pins it, and the traversal guard in
  front of it is unrelated.
- **the box's own directory is not there** — the box *is* here. Its address is known, it is still in
  the index, and the library still counts it as watched (ADR 41 refuses to un-index it, ADR 44 counts
  it as gone rather than as work). 404 on `/api/boxes/{uuid}/…` is the verb for *there is no such
  box*, which is false, and it is the one sentence the page must not reinforce: the reader is looking
  at the box.

ADR 41 already drew this line for counts — "a folder that is not here is not an empty folder" — and
the dialog had inherited the distinction. The HTTP layer had not, because until a chip made the state
navigable, nobody walked into it.

## Decision

**A missing root is a state the listing reports; a missing sub-path stays a 404.**

`GET /api/boxes/{ref}/listing` with no `sub`, where the box's own directory is not on this machine,
answers **200** with:

```json
{"root": "…", "entries": [], "missing": true, "reason": "folder_not_here", "indexed": true}
```

The request was answered truthfully: there is nothing to show, and here is why. The view says so in
the library's own words — the folder is not on this machine, nothing was removed from the index, put
it back and ask again — instead of rendering a load failure for a condition the database already
knows. `_box_file()` grew `missing_root_ok`, which the listing sets only for an empty `sub`, so every
other consumer of that helper keeps its existing answer.

**An empty listing and an absent one stay different on screen.** The same rule ADR 41 applies to a
count applies to a pane: `entries: []` with no reason would tell the reader the folder is empty, and
that is the sentence that makes people delete things they should have restored.

## Consequences

- The console stops filling with errors for a state. That is not cosmetic: `make uitest` fails on any
  console error, so a legitimately navigable state had to be answerable without one, and the check
  that surfaced this (`no console errors`) is the reason it was caught at all rather than shipped as
  a red herring in the developer tools.
- Anything reading `/listing` must now look at `missing` rather than at the status code alone to tell
  "empty" from "absent". The page does; the check that pins it asserts the words on screen, not the
  JSON, because the words are what a person reads.
- A box whose folder is absent still opens, still shows its metadata, and still answers for its
  indexed documents — the index keeps working on files that are temporarily off this machine, which is
  the property ADR 41 was protecting.
- Restoring the folder needs no repair step and no flag to clear: the next listing finds the directory
  and answers normally, because the state is read from the disk each time rather than stored.
- If some future screen wants the HTTP failure (a CLI that treats a vanished folder as an error), it
  asks for a sub-path or reads `missing`; it does not get to reinterpret the box as nonexistent.
