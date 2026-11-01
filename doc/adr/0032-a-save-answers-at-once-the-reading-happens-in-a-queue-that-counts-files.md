# 32. A save answers at once; the reading happens in a queue that counts files

Date: 2026-11-01
Status: accepted

## Context

Saving a box read the file before it answered. `POST /api/boxes` and `PUT /api/boxes/{ref}` called
`text_search.sync(store, saved, …)`, and `BoxStore.add`/`update` read inline as well — so a box pointed
at a folder walked and extracted every file inside it, synchronously, with the request held open and
the page showing nothing at all until it finished. For a note that is nothing. For a directory the
reader has just aimed at a tree of logs it is a several-second freeze with no explanation.

The index line in the left pane could already say `indexing…` — but only for an explicit **pass**
(`box index`, or the modal's Re-apply), which the reader had to start themselves. Nothing about an
ordinary save was visible.

The request: index in the background on create and update, with a progress bar at the bottom of the
left pane.

## Decision

**One queue, one worker thread, per server process; the request answers when the rows are written.**

- `BoxStore.add`/`update` gained `read_now` (default `True`). The web app passes `read_now=False` and
  queues; the terminal keeps the old behaviour, because there the wait *is* the work the person asked
  for and `box add` prints what it read. The asymmetry is deliberate and stated, not incidental.
- The queue is on `app.state`, not in the database: it describes work happening now in this process.
  After a restart it is legitimately empty, and anything still unread is listed as **outstanding** by
  `index_counts()` — so nothing is lost, only not-yet-read.
- **Un-indexing stays synchronous.** Taking a box out of the index, or moving it off a file, reads
  nothing; deferring it would leave a document findable after the reader said it should not be, which
  is a wrong answer rather than a slow one. `test_taking_a_box_out_of_the_index_happens_in_the_request`
  pins both halves of that asymmetry.
- The worker is one thread, and never a second one: two readers of one folder would court again the
  race ADR 31 had to close, for no gain.
- **The denominator counts files, not queue items.** A folder is one item and eighty files, and "1 of
  1" while eighty files are being read is technically true and useless — it cannot be waited on. So
  `expand_directory` takes `on_progress(done, total)` (the walk's file list is complete before the
  first read, so the total is honest from the start), and the queue replaces the one-file estimate it
  enqueued for that item. An empty file still advances the bar: the reader is waiting on the walk, not
  on the harvest.
- A failure is a report, not a stall. The worker records `{name, note}` for each refusal (last five),
  carries on, and the line reads `index refused N files` with the file and the reason in the title. A
  bar that stops moving because one PDF is broken is the failure mode that teaches people to ignore
  the bar.
- The page asks the status once **itself** after a save (`afterWrite`), because its polling only
  starts while the server already says it is busy; without that, the first bar of a session appears a
  poll-cycle late and looks like a bar that missed the work.
- `running` on `/api/index/status` now means either kind of work — a pass or the queue — so the
  existing poller, the settling behaviour and the modal all keep working, and `queue` carries
  `{waiting, reading, done, total, errors}` for the bar.

## Consequences

- Measured in Chromium over a throwaway library of 80 files / ~700 KiB (`/tmp/check_background_index.js`,
  served by `/tmp/run_task6.sh`): **6/6 clean**. The save returns in **9 ms** and the box is usable
  immediately (its directory listing draws, 80 entries, while nothing has been read); the bar is
  visible with a real denominator (`reading 4 of 80…`, max 80) and clears by itself at **723 ms** —
  an 80× gap between the save and the reading, which is the contract's timing comparison rather than
  an assertion of intent; a word inside a file that flow brought in comes back from ordinary search
  (`how: "content"`, passage `… entry 0 mentions [kowhai] birds nesting on the shed roof`); two saves
  share one queue with `done ≤ total` at every sample; and the settled state arrives without a reload.
- Tests that assert on the index right after an API write now drain the queue first
  (`conftest.wait_for_index`), which waits on the real flag. A sleep would pass on an idle machine and
  fail on a loaded one, and a suite that needs `--rerun 3` stops being believed. Existing store-level
  and CLI index tests are unchanged: they keep `read_now=True`, so nothing was rewritten to
  accommodate the queue — the change is one parameter and its call sites.
- The queue is per process. Two servers on one library each read their own saves; the second server's
  line does not know about the first's queue. That matches how the index works already (ADR 13 keys it
  on the resource row, and ADR 31 makes concurrent adoption safe), and the counts in the line are read
  from the database, so the *state* is shared even where the progress is not.
- The worker is a daemon thread. On interpreter exit a read can be cut off mid-file; the resource
  stays outstanding and the next save or pass reads it again. Recording a resume position for a local
  tool's index read was not worth the state it would need.
- Pinned by `tests/test_web.py`: the timing comparison with the reader held, the refusal named without
  a stall, the synchronous un-index, the file-counted denominator, concurrent saves leaving one row per
  address and every save landed, the store's `read_now=False`, and the page wiring (words, bar,
  `afterWrite`).
