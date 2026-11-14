# Persistence

Where the library lives, how the file journals, and what a complete backup is.

## The files

| Path | What it is |
| --- | --- |
| `$BOXGANIZER_DB`, default `~/.local/share/boxganizer/boxes.db` | the SQLite database: boxes, resources, tags, links, the full-text index |
| `boxes.db-wal` | recent writes that have not been folded into the database file yet |
| `boxes.db-shm` | shared memory index for the `-wal` file. Ephemeral; SQLite recreates it |
| the authored files (notes, checklists) | written beside the database, not inside it — see ADR 14 |

A library is the database **and** its authored files. Copying only the database copies the
index of things whose text lives elsewhere.

## Journal mode: WAL, with a full synchronous

Opened stores run:

```sql
PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 2000;
```

`synchronous` is left at SQLite's default, which is FULL. Both choices are deliberate and they
answer different problems:

- **WAL is for concurrency.** With the rollback journal, a writer that needed the file
  exclusively while a reader still held a read transaction waited out `busy_timeout` and then
  failed with `database is locked`. Measured: that writer gave up after **2.00 s**; the same
  writer against the same file in WAL mode committed in **0.00 s**. The web app records a
  refused write as a per-file note and continues, so the visible symptom of the old behaviour
  was a library that had quietly done less than the panel claimed.
- **It is not a speed fix, and was not bought as one.** WAL alone took the slow status endpoint
  from 4.964 s to 2.334 s. The 54-second cost was one `fsync` per statement, and that is what
  batching fixed (54 s → 0.02 s). Quoting WAL for the latency would be claiming credit for
  someone else's work.
- **`NORMAL` was considered and refused.** It would roughly double commit speed by letting the
  operating system decide when the write reaches the disk. That trades a save you were told
  about for a save that may not survive a power cut, for a two-fold on an operation that
  already answers in 60 ms. Say so again if the numbers change.

To revert, with nothing else holding the library open:

```sh
sqlite3 ~/.local/share/boxganizer/boxes.db 'PRAGMA wal_checkpoint(TRUNCATE); PRAGMA journal_mode = DELETE;'
```

The setting is stored in the database file, so this is permanent until a store opens it again
with the line above.

## Backups

**Do not copy `boxes.db` with the app running.** In WAL mode recent writes sit in `boxes.db-wal`
until a checkpoint folds them into the main file. A bare `cp` of the database file can restore a
library from before the last save — and it restores *cleanly*, so nothing tells you. Measured
while writing this: a copy of a database with 60 rows and an uncheckpointed sidecar opened as a
file with **no tables at all**.

Use the connection, which knows what it has written:

```sh
sqlite3 ~/.local/share/boxganizer/boxes.db "VACUUM INTO '/backup/boxes-$(date +%F).db'"
```

`VACUUM INTO` reads through SQLite, so it includes what is in the WAL and comes out as one
self-contained file with no sidecars. It is safe to run while the app is serving. Then copy the
authored files beside it.

`test_a_copy_of_the_database_file_alone_is_not_a_backup` in `tests/test_store.py` asserts the
hazard and the fix, so this page cannot quietly become wrong.
