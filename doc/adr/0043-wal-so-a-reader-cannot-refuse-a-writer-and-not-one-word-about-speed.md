# 43. WAL so a reader cannot refuse a writer — and not one word about speed

Date: 2026-11-13
Status: accepted
Related: ADR 42 (what a read may do), `doc/persistence.md`

## Context

`pref_report.md` listed "WAL + `synchronous=NORMAL`" as one proposal, P7. Measured separately,
the two halves are worth radically different things:

- WAL alone on the slow endpoint: **4.964 s → 2.334 s**.
- `synchronous=NORMAL`: another ~2×, by letting the operating system decide when a commit
  reaches the disk.
- Batching the writes of the same pass (P1): **54.10 s → 0.017 s**.

So the endpoint's cost was never the journal mode. Presenting WAL as the latency fix would have
been claiming credit for another change's work, and the durability half of it would have been
sold as if it were free.

What WAL is actually for was assumed rather than measured in the report — "readers stop blocking
on a writer". Tested, that phrasing is wrong in a way worth recording. A reader that opens the
database, selects, and closes **never failed** under the rollback journal either; thirty rounds of
a writer holding a transaction open while a second connection polled produced zero refusals. The
collision is the other way round and needs the read to still be *open*:

| journal mode | a writer commits while a reader holds a read transaction |
| --- | --- |
| `delete` (the old behaviour) | waits out `busy_timeout`, then `database is locked` after **2.00 s** |
| `wal` | commits in **0.00 s** |

The writer needs the file exclusively to commit, a reader holding a shared lock prevents that,
and ADR 42's `busy_timeout` turns the instant refusal into a two-second wait that ends in the
same refusal. The web app swallows a refused write as a per-file note and carries on — so the
symptom is not an error message, it is a library that did less than the panel said it did. The
application does hold long reads: listing the library, an export, a search over 818 documents,
while the save queue writes what people just filed.

## Decision

**Open every store with `PRAGMA journal_mode = WAL`, and leave `synchronous` alone.**

- `synchronous` stays at FULL (SQLite's `2`), so a save the person was told about survives the
  machine losing power. `NORMAL` is refused: it buys roughly a factor of two on a commit that
  already answers in 60 ms, and spends durability to do it. `test_the_library_journals_in_wal_and_still_syncs_every_commit`
  pins the two settings as a pair, because the pair is the decision.
- The mode is set on every open. It persists in the file header, so this is a guarantee the
  first time and a no-op after, and a library written by an older version gets the behaviour
  this code assumes without a migration step.
- `busy_timeout` stays at two seconds. It is no longer the thing that makes concurrency work,
  but it is still what makes a genuinely wedged writer into a slow, visible failure instead of a
  fast, invisible one.

**The cost is written down where somebody will hit it.** In WAL mode, recent writes live in
`boxes.db-wal` until a checkpoint, so copying the database file while the application is running
can produce a backup from before the last save — one that restores without complaint. Measured
while writing this: a bare copy of a database holding 60 rows opened as a file with no tables at
all. `doc/persistence.md` gives `VACUUM INTO` as the backup path and the one statement that
reverts the mode, and a test asserts both halves so the page cannot quietly rot.

## Consequences

- Two sidecar files appear beside the library. Anything that greps, syncs or archives the
  directory has to know they exist; a synchronisation tool that copies `boxes.db` alone will
  produce the incomplete backup described above.
- A writer can no longer be refused by a reader. Writer against writer is still serialised — that
  is what `busy_timeout` is for, and the three-writer measurement (104 refusals → 0) stands
  unchanged by this ADR.
- Nothing in the live user library is converted by this change. The mode is applied when a store
  opens the file, so the user's own database becomes WAL the next time they run this version —
  which is why the revert statement and the backup path are documented rather than mentioned.
- No latency claim belongs to this decision, and none is made.
