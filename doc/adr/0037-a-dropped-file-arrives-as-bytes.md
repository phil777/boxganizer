# 37. A dropped file arrives as bytes, so the page posts them and the server decides what it is

Date: 2026-11-07
Status: accepted

## Context

The left pane needed a landing strip: drop a file there and a box appears — type guessed, resource
captured, named after the object. Drop it onto box A and the new box is filed in A as well.

The obstacle is not the layout. It is that **a browser will not tell a web app where anything on the
disk is.** A file dragged from the desktop or from a file manager arrives as a `File`: bytes, a name,
a size, a MIME type — and no path. Everything Boxganizer does with a resource starts from a path.

The page already had a path-shaped notion of a dropped file, because files dragged *out of a directory
box* carry one (`FILE_DRAG_TYPE`, written by the app itself, naming a file the server can already read).
So two different payloads need the same outcome, and only one of them can be expressed as a path.

## Decision

**One endpoint, `POST /api/capture-file`, and two ways in.**

*Bytes in, box out.* The page posts the file's content; the server writes it to a private temporary file
inside the library's own `files/` directory, runs the ordinary capture on it (ADR 35 — hash, buckets,
dedup), deletes the temporary, and files a new box pointing at the copy. The temporary exists so capture
sees a file and not a blob, and it is unlinked in a `finally`, because an orphan in `files/` is exactly
the clutter ADR 36 exists to prevent.

**Raw body, not `multipart/form-data`.** Multipart would mean adding `python-multipart` — a new runtime
dependency for one file, one name and one body. The name travels as a query parameter and the bytes as
the whole request body. The size ceiling is the one the app already applies to reading a resource whole
(`MAX_RAW_BYTES`, 32 MiB), answered with **413 and the limit**, so the failure names the number someone
needs.

**The type is guessed by the server, from the captured path.** `infer.suggest` is where the inference
lives; a second copy in the page would disagree with `box add` about what a `.md` is within one release.
The box's name comes from the dropped object's name, minus the extension, and a name with no extension
still makes a box.

**A dropped name is data, never a route.** `Path(name).name` gives the basename for the box; only the
*extension* is allowed to touch the stored filename, and `capture.suffix_of` refuses anything that is not
a short `.suffix`. A drop called `../../escape.md` produces a box named `escape` and a file at
`files/15/97/<sha256>.md` — measured, because the interesting question about a filename is not what it
says but where it makes you write.

**A zone claims a drag only when it can finish it.** The strip lights up for drags that carry files and
stays plain for a drag carrying a *box* — a highlight that promises "drop resources here" and then does
something else is a lie with a border on it. When it does claim, the drop calls `preventDefault()` and
`stopPropagation()`, because the pane behind answers drops too, and ADR 23's rule that one gesture makes
one parent is what keeps a box from appearing somewhere nobody put it.

**Dropping on a box is the same act plus a link.** The folder view answers files *before* it considers a
dragged box: `ingestDroppedResources` runs, and only if the drag held no files does the ordinary
`dropBox` filing happen. One code path each, in a stated order, rather than a handler that guesses.

**Only a drop keeps a copy.** The right-click "New box from this file" gesture uses the same inference
and the same creation, but does **not** capture: that gesture says *make a box out of this file*, and
quietly duplicating the file in it would surprise the person who meant to point at it. `keep` is a
parameter, not a global, and the distinction is pinned by a test.

## Consequences

- Files from outside the page can enter the library, which the app could not do before. The consequence
  is the one ADR 35 accepted: the library now holds bytes it did not hold, deduplicated, and ADR 36's
  trash is where they go when the last box pointing at them is deleted.
- The upload is a write to the library's own directory, so it is governed by the same owner-only
  permissions as everything else in `files/`, and by nothing else: it is not a read of the user's disk,
  because the browser never had a path to begin with.
- The 32 MiB ceiling means a large video cannot be dropped in one go. It says so, with the number,
  rather than streaming into a temporary file without limit.
- The strip is a target the size of three lines, deliberately: a landing area you must aim at is one
  people stop using, and a file released outside any zone is a file that silently did nothing.
- Nothing about ADR 27's clearing rule changes, and it is verified: the strip uses the page's own
  drop-zone classes, so a drag that dies halfway, ends elsewhere, or is cancelled with Escape leaves
  no outline behind on the strip.
