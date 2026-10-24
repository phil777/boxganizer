# 2. Store boxes in a single SQLite file

Date: 2026-10-24
Status: accepted

## Context

The store must work offline, need no daemon, survive a crash, keep a graph of
boxes, and be small enough that a developer can hold the whole persistence layer
in their head. Three interfaces hit the same data, and one of them (the web app)
serves requests from a thread pool.

## Decision

Use SQLite with one database file per library, reached through a single
`BoxStore` class. Two tables: `boxes` and the `links` join table. A unique index
on a Python-computed case-folded `key` column enforces name uniqueness. One
connection per `BoxStore`, opened with `check_same_thread=False` and guarded by
an `RLock`. Path resolution: argument, then `$BOXGANIZER_DB`, then XDG.

## Consequences

* Installing and backing up the application is copying a file; no service to run.
* Foreign keys plus `ON DELETE CASCADE` keep the graph consistent for free.
* Writes serialise on one lock. Fine for one user; two processes writing hard
  will see `StoreError` (SQLITE_BUSY) and must retry.
* Scale ceiling is a desktop library, not a shared multi-tenant database. If
  that ceiling is ever reached, only `store.py` changes.
* Every SQL statement lives in one module, which keeps coverage meaningful and
  makes the FTS5 upgrade a local change.
