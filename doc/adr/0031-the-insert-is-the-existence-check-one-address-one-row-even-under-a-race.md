# 31. The insert is the existence check: one address, one row, even under a race

Date: 2026-11-01
Status: accepted

## Context

The report was a 500:

```
PUT /api/boxes/de1bef11-…  → 500 Internal Server Error
sqlite3.IntegrityError: UNIQUE constraint failed: resources.ref_key
  store.py:1734 _execute   ← expand_directory → register_resource → _ensure_resource
```

Those paths are from the pipx-installed build, so the first job was to check the defect is here too.
It is. `tests/test_store.py::test_two_threads_adopting_one_file_make_one_row` runs six index passes over
one directory at once and gets, five of six times:

```
StoreError: write refused: UNIQUE constraint failed: resources.ref_key
```

and over HTTP — the reported flow, a save while other saves are landing
(`/tmp/check_refkey_race.py` against `box serve`) — five `500 Internal Server Error` responses out of
120 saves.

**How the check and the insert disagree.** `_ensure_resource` asked, then wrote:

```python
found = self._rows("SELECT id FROM resources WHERE ref_key = ?", (key,))
if found: … return existing
return self._execute("INSERT INTO resources (reference, ref_key, …) VALUES (…)")
```

Each `_execute` takes the store lock and **commits on its own**. Two statements are not one decision.
The store is shared across threads deliberately (`check_same_thread=False`), and index passes already
run in `asyncio.to_thread`, so two walks of the same folder can both be told "no such row" for the same
file; the second INSERT then lands on the UNIQUE index and the request that happened to be saving a box
at that moment answers 500. Nothing was corrupted and nothing was lost — the row the loser wanted
already existed by then, made by the winner.

The same shape sat one call later, in `link_resource`: a `SELECT` for the pair, then a plain `INSERT`,
colliding on `PRIMARY KEY (parent_id, child_id)`. And a third face of it: while one walk adopts a file,
another walk can conclude that the same file has disappeared from disk and drop its row, so the link
`INSERT` trips the foreign key instead. Reproducing the first two removed the 500s and left
`409 write refused: FOREIGN KEY constraint failed` on the occasional save — a better sentence, still a
failure the reader did not cause.

## Decision

**Ask the insert, not a preceding SELECT.**

- `resources`: `INSERT … ON CONFLICT (ref_key) DO NOTHING`, then read the row by key. The loser of the
  race reads the row the winner made, which is the answer it wanted anyway. The caller's stated wish
  (`index_it`) and the `is_dir` correction are applied to whichever row won, so a lost race does not
  silently drop a request to opt a file into the index.
- `resource_link`: `INSERT … ON CONFLICT (parent_id, child_id) DO NOTHING`, with novelty read from
  `cursor.rowcount` (a new `changed=` mode on `_execute`) instead of a pre-flight query.
- A link whose child row vanished mid-flight returns `False` — *no link made this time* — rather than
  raising. The file is still on disk and the next pass adopts it again; the save that happened to be in
  the middle of it is not the thing that should fail. A different refusal (a stopped disk, a constraint
  nobody expected) still travels up as a refusal, unchanged.
- `StoreError` gets an HTTP handler: **409 with the store's own sentence**. Before this, every write the
  storage layer refused was a 500 with a traceback in the log and "Internal Server Error" on the page —
  which sends a reader to file a bug about a library that merely declined.

Multi-statement transactions across threads were considered and left alone: `_execute` commits per
statement everywhere, and wrapping a whole folder walk would serialise the library's writes behind one
directory scan to fix a bug that the conflict clauses already close.

## Consequences

- 360 saves across three hammer runs land as 120/120 `200`s each, with **no 4xx or 5xx write refusals**;
  the only 409s are the index-pass guard answering "a pass is already running", which is what it is for.
  The same probe on the code before this change: five `500`s.
- Re-walking an unchanged tree mints nothing: `test_re_expanding_the_same_tree_changes_nothing` asserts
  identical `ref_key → id` maps, an unchanged link count, nothing skipped and nothing outstanding
  (ADR 13's "one file, one row" is now true under concurrency, which is where it was being broken).
- The race is still visible in principle — two threads can adopt the same file in one instant — but its
  outcome is now the intended one: one row, one link, no error. The unique index keeps its job as the
  arbiter instead of being the thing that throws.
- `tags` still inserts plainly after a lookup, so two tabs creating the same tag name in the same
  instant produce `409 write refused: UNIQUE constraint failed: tags.name` rather than one winner. Left
  deliberately: a tag name carries a parent and a colour with it, so silently adopting the other tab's
  row could apply the wrong colour, and a refusal the reader can see beats a quiet guess. It is no
  longer a 500 either way.
- Pinned by `tests/test_store.py` (six threads, one row; the vanished-child link declines quietly;
  re-expansion is idempotent) and `tests/test_web.py` (a store refusal answers 409 with its sentence,
  forced rather than raced).
