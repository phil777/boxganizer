# 28. A delete is asked inside the menu, and the question says what survives

Date: 2026-11-01
Status: accepted

## Context

Nothing in the page deleted a box. The report asked for a menu item, and the obvious change was to
add an endpoint — but `DELETE /api/boxes/{ref}` had existed all along, and `box rm` had used the same
store call for as long as the CLI has existed. What was missing was the question, not the answer, and
that turned out to be the interesting half.

A delete is the one ordinary action in this app that cannot be undone from the interface, and three
things about it are easy to get silently wrong:

- **What a box is.** A box is a place a thing is filed, not the thing. Deleting it must not throw
  away what was inside — an unrecoverable act hidden behind a confirmation nobody reads carefully.
- **What a file is.** A note and a checklist write their words to a file beside the database, and ADR
  14 decided deliberately that those files outlive the box: a row can be re-created by name, the words
  cannot be re-created from nothing.
- **What the root is.** Not a box that happens to hold everything — the place everything else is
  filed in, so there is no state to arrive at afterwards.

## Decision

**Ask in the menu, with the second click, rather than in a dialog.** A menu entry that begins
`Delete…` rewrites itself on the first click to `Really delete — what was filed inside stays in the
library`, and only the second click runs. A dialog is dismissed reflexively; the menu is already where
the person was looking, and the armed entry *is* the question. The menu is rebuilt on every open, so
an armed entry that was not confirmed cannot linger waiting for a third click — which is also why the
arming click has to stop its bubble: a document-level listener closes the menu on any click, and a
question that closes the moment it is asked is a button that does nothing.

**Say what will and will not be deleted, in those words.** Not "are you sure?". The links from the box
to what it held go with the row; every child stays in the library and keeps any other parent it had;
the file the box pointed at is untouched; the words file the box wrote for itself is untouched (ADR 14
stands) and is named on the way out so nobody hunts for a note they did not lose.

**Two noes for the root.** The page does not offer deletion for a box whose type is `root` — offering a
control that can only be answered with "no" is worse than no control — and the store raises
`RootCannotBeDeleted` for anything that asks anyway, mapped to a 409 carrying that sentence. The page
and the server answer to different readers.

**One transaction, several statements.** `_execute` commits statement by statement, which is right for
one write and wrong here: a box whose row is gone while its indexed text is still offered by search is
a library that lies about what it holds. `delete_box` therefore runs its statements on the connection
and commits once, or rolls back whole.

**`box rm` asks the same question and answers with the same facts,** because the same action reached
from two interfaces must not mean two different things.

## Consequences

- The delete response stays `204` with no body, which two tests already pinned. The page therefore
  states the *rule* rather than a count it would have to fetch; the count matters less than the fact,
  and the fact is what a person can act on before clicking.
- **Deviation from the goal contract, flagged:** the confirmed contract listed "its own words file"
  among the things the transaction removes. That contradicts ADR 14 and a test that asserts the file
  outlives the row, with the reasoning quoted there. The non-destructive reading was kept — nothing of
  the reader's is thrown away — and the file is named in both interfaces instead. Flipping this is a
  one-line change plus an amendment to ADR 14; it should be a decision, not a side effect.
- A file cannot enlist in a SQL transaction. Deleting one would have to happen after the commit, so a
  failure would leave a box gone and a file behind — the residue ADR 14 already treats as a clue.
- Measured in Chromium (`/tmp/check_delete.js` against the throwaway library seeded by
  `/tmp/seed_task2.py`, `/tmp/run_task2.sh`): 6 cases, all clean — the menu offers it; the first click
  leaves the menu open, changes its own words and deletes nothing; the second deletes once and says so,
  and the box is gone from the library list, from search and from a dynamic folder that held it; the
  child with three parents is left with exactly the two that were not deleted (`["Doomed","Frames",
  "cabinet"] → ["Frames","cabinet"]`) and is listed under its surviving folder; the root answers 409
  with the store's sentence and is not offered the item at all; and a note's words file is still on
  disk after its box is deleted, checked from the shell rather than asserted by the page.
- Pinned by `tests/test_store.py` (children survive, root refused, a forced failure halfway through
  leaves the row, the link and the resource all present), `tests/test_web.py` (gone from list, search
  and dynamic folder; the menu wiring) and `tests/test_cli.py` (the shell says what survives; the root
  refuses without a traceback).
