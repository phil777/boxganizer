# 41. A folder that is not here is not an empty folder

Date: 2026-11-13
Status: accepted

## Context

The library on the machine this was found on holds 842 watched resources. 64 of them live under
`/home/pbi/work/boxganizer.old`, a directory that was deleted weeks ago when the project was moved.
The panel had been saying "68 to reindex" for that.

Writing the performance report meant running an indexing pass against a copy of that library, and
the copy came back wrong. Not slowly — wrong:

| | before the pass | after one pass |
|---|---|---|
| resources watched (`wanted`) | 85 | 23 |
| documents in the index | 80 | 18 |

Nothing in the interface had been touched. No box had been deleted, no folder un-checked. A pass
over a library whose folder is missing *removes the folder's contents from the index*, and says
nothing while doing it.

The mechanism is in `expand_directory`. A walk answers two questions at once — what is in here now,
and by implication what is not — and the pass prunes whatever the walk no longer lists:

```python
files, note = address.walk_files(target, confine=confine)
...
for child in store.linked_resources(int(resource_id)):
    if address.normalise(child.reference) in known:
        continue
    store.record_index(int(child.id), body=None, seen=address.Fingerprint())
    store.unlink_resource(...)
    store.drop_resource_if_alone(...)
```

`walk_files` on a missing directory returns `([], "'…' is not a folder")`. An empty list. So `known`
is empty, every child is pruned, and the folder's own row is stamped as freshly walked. The pass
reports a clean run.

The same erasure happens one level down, through the file branch of `sync_resource`, whose
docstring has always promised it: *"a file that has changed, or has gone away, is read, and what
comes back replaces what the index held — including nothing at all."* A missing file reads as
nothing, so its text is deleted and its `stale` flag is cleared. Measured on a three-file folder,
one pass took the index from four documents to one and the stale count from four to one — the
library ended up *looking healthier* precisely because it had stopped remembering anything.

No test covered either path. That is how it shipped.

## Decision

**The absence of a folder is not evidence about the files inside it.** A walk that could not walk
teaches nothing, and nothing is written.

- `expand_directory` returns the walk's note and mutates nothing when `target.is_dir()` is false.
  The rows, their text and their staleness stand as they were.
- `sync_resource` applies the same rule to a child it cannot read, keyed on whether the *containing
  directory* is present (`_unreachable`): a file in a folder that is here and is not there was
  deleted, and its words leave the index with it — search must not go on answering for a note
  nobody has. A file whose folder is also missing was never observable, so its text is the only
  record that it said anything, and it stays.
- What is learned instead of destroyed is *reported*: `missing_directories()` and
  `gone_populations()` name each gone folder with the files and boxes under it, `/api/index/status`
  and `/api/index/pending` carry them, the panel says so in a sentence, and `needing_index()` marks
  those rows `why: "gone", fixable: false` so "Index what changed" stops offering 64 files no run
  will ever read.

The distinction is made from the filesystem, on every look, and stored nowhere. That is the second
half of the decision: **there is no `gone` flag.** Restoring the folder from a backup, or plugging
the drive back in, makes every row ordinary work again on the next pass, with nothing to un-set and
no state that could outlive the reason for it. The first draft of this change recorded a decision
in the rows; a person who restored the folder would have found their files still un-indexed for a
reason no screen explained.

`index_counts()` is deliberately unchanged. The counts stay honest about what the index is *for* —
842 files still wanted, 818 still holding words — while the panel separates "you have work to do"
from "a folder is not on this machine". Trimming the counts to match what a pass could do would be
the same silent decision wearing a different hat.

## Consequences

- A pass over a library with a disconnected drive is now a no-op against that subtree and reports
  the folder by name. Reindexing the rest of the library still works, so the failure is not sticky.
- Deleting a folder on purpose no longer cleans up its index on its own. It leaves a named line in
  the panel, and the folder's own box is where the choice gets made. That is strictly more typing
  and strictly less surprise, which is the trade this ADR is for.
- `missing_directories()` costs one `stat` per indexed folder — single digits on a real library —
  and runs on the two index endpoints. It is not in `index_counts()`, which stays a pure read.
- Anything that wants to *act* on a gone folder needs an explicit command with the folder named in
  it. Nothing in this ADR authorises one running by itself.
