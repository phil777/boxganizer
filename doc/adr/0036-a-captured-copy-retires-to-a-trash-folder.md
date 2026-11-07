# 36. A captured copy retires to a trash folder, and only the last box puts it there

Date: 2026-11-07
Status: accepted

## Context

ADR 35 made a captured file the library's own copy. ADR 28 decided what deleting a box does: the links
go, the boxes filed inside stay, and a file the box wrote for itself is left alone (ADR 14). Neither
decision answers the new case.

A box that **pointed** at a document can be deleted without touching it — the card is not the thing. A
box that **captured** a copy is different: after the delete, the library is holding a file that nothing
points at, which nobody can see in the interface, in a directory that has no way out.

Two wrong answers were available. Deleting the copy with the box is the one way Boxganizer would destroy
content nobody asked it to destroy: a person clears a box on Tuesday and the copy they made on Monday is
gone, and the whole point of capture was that the copy outlives things. Leaving it in `files/` forever is
the other — a graveyard of orphans with no listing, no count, and no way to reclaim the space.

The page also had a sentence that was about to become a lie: after a delete it printed
*“`<path>` was left alone”*, guessed from the box object still in hand. For a captured copy that is
false.

## Decision

**Retire it to a `trash/` folder beside `files/`, keeping its bucket path.** Not deleted, not merged into
`files/`, and not left orphaned. Same relative path inside the trash as it had inside `files/`
(`trash/3f/9a/<sha256>.pdf`), so putting a copy back is a move home rather than a guess at where it
belonged.

**Only the last box out puts the file there.** A captured file is named for its content, and content can
be pointed at by several boxes — that is what deduplication *means*. The store already answers this
exactly (`BoxDeletion.resource_released`: the row was removed and nothing else references the resource),
so the rule is inherited rather than invented: delete one of two boxes sharing bytes and the file stays
in `files/` with a sentence saying why; delete the second and it moves.

**A name that no longer matches its bytes is left exactly where it is and reported.** `verify()` compares
a captured file against the digest in its own name. If somebody edited the library's copy by hand, that
file may now be the only instance of something, and moving it on the strength of a name that lies is how
a library loses a document twice. So `retire` returns it untouched with the reason, and nothing — not the
trash, not the delete — pretends otherwise.

**Symlinks are unlinked, never followed.** In `retire`, a symlink at the captured path is left alone. In
`empty`, a symlink inside the trash is removed as a link, and whatever it points at is not touched.
Without that, "empty the trash" would be a route out of the trash, and a planted link would turn a tidying
command into a deletion tool.

**`box check` counts it, including when the answer is zero.** The check command is where a person asks
whether the library is healthy, and "3 files in the trash" is part of that; printing nothing when the
count is zero leaves the question unanswered, so the line is always there.

**`box trash list` and `box trash empty` are the only way out, and emptying asks first.** Listing names
the files and their sizes. Emptying prints them, then asks — unless `--yes` — because this is the single
place in Boxganizer where captured content stops existing, and a command whose name suggests tidying
should not be the moment it happens.

**The page reports what the server did.** `DELETE /api/boxes/{ref}` now answers **200 with a body**
(it was 204 with nothing), carrying `captured_file: {path, trashed, reason, bytes}`. Only the server
knows whether that box was the last reference and whether the copy still verifies. Three existing tests
asserted 204 and were retargeted to assert the body's claims instead, which is a stronger check than a
status code. The message the page prints now distinguishes three outcomes: the copy went to the trash
(and says where), it stayed because another box points at it, and the ordinary case — a box that merely
pointed — where the document is left alone.

**A note's own words file is not a captured copy and keeps ADR 14's treatment.** It lives in `files/`
too, but it is named by box uuid, not by hash, so it is not `is_captured` and is never retired. Deleting
a note leaves its words where they are and says so.

## Consequences

- `files/` can no longer accumulate orphans silently: the count is printed by `box check`, and the only
  route out is a command that lists what it is deleting.
- A captured library's disk use does not fall the moment a box is deleted, which is the point and is
  visible: `box trash list` shows what is waiting.
- Two front ends retire copies through one function (`capture.retire_after_delete`), so deleting from the
  page and from `box rm` cannot drift.
- The trash is a sibling of `files/`, never inside it, so nothing that walks `files/` for live copies
  (indexing, search, the confine) ever sees trashed content.
- The delete response shape changed. That is a real cost, paid because the alternative was a page that
  prints a reassuring sentence it has no way to check.
