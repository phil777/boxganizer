# 44. A resource that is gone is counted as gone, not as work

Date: 2026-11-14
Status: accepted
Amends: ADR 41 (the reporting half — the rule that nothing is pruned stands)

## Context

ADR 41 decided that a folder which is not on this machine is not an empty folder: a pass must not
erase the index under it, and what is learned is reported instead. It also subtracted the hopeless
files from the panel's headline so the button would stop offering them.

The complaint that led here is the residue of that decision:

> I have "833 to reindex". When I ask for reindexing, it does something … then it stops, and the
> message displays again "833 to reindex".

Measured on the library the complaint came from (`reindex_diagnosis.md` has every command):

```
stale total = 67
   under /home/pbi/work/boxganizer.old   64     the folder does not exist
   deleted file, folder present           1
   changed, still present                 2
reindex CAN fix = 3   |   reindex can NEVER fix = 64
```

**96% of the number on the screen was work that no reindex can do.** Subtracting it from the
headline (ADR 41) hides it from the button but leaves three things wrong: the count is still
`stale`, so the number the dialog prints and the list it shows disagree about what is left; the
unreachable population has no number of its own, so nothing on screen says *why* the button will not
help; and the subtraction happened in the page, from `missing_roots` entries the page cannot
verify, which is how two numbers that should describe one set drift apart.

There is a second arithmetic hole ADR 41 left. `stale` counts the gone **folder's own row** — it is
watched, it is unreadable, so it is stale — but the folder-level counts covered only files. On the
copy used here that left one resource in `stale` that no subtraction knew about: a headline of 1
with nothing listed to explain it.

## Decision

**The store splits its own work list, and the page reads the split instead of computing it.**

`BoxStore.missing_watch()` answers one question about the rows the panel is already offering:

| field | meaning |
| --- | --- |
| `candidates` | watched rows that are stale or never read — exactly `stale + untouched` |
| `work` | the subset a pass could actually read. **This is the headline.** |
| `resources` | watched resources not on this machine — **including a gone folder itself**, because `stale` counts its row |
| `roots` | each gone folder and how many watched resources it holds |
| `stray` | gone files under a folder that is still here (a deletion, not a missing tree) |
| `boxes` | boxes that still depend on any of it — pointing at the file directly, **or at the gone folder containing it** |

`GET /api/index/status` and `/api/index/pending` both return it, so the two endpoints cannot
disagree, and `indexWaiting()` in the page uses `missing.work` rather than subtracting a figure it
cannot check.

**Two numbers, neither adjusted.** `wanted` still counts every unreachable resource, `stale` keeps
its meaning, no row or flag is edited to make the headline come down. The panel says, in its own
sentence: *65 watched resources are gone from this machine (65 under boxganizer.old) and 1 box still
points at them; they are not counted as work. Nothing was removed from the index: put them back and
ask again.*

**Folders are counted by membership, files by looking.** Everything under a gone folder is
unreachable whether or not anything has re-checked it, so that population comes from SQL. Only the
stray case — one deleted file in a folder that is present — needs a `stat`, and only among the
candidates: 67 rows on the library above, not 842. A stray deletion nobody has re-checked since is
absent from the count until the next look at the files, which is what the dialog's own age line
("the files were last looked at 3 h ago", ADR 42) is for.

**A gone folder is a resource.** Counting it is what makes `work + unreachable == stale + untouched`
hold exactly, which is the property the whole change exists to guarantee; a headline that adds up is
the difference between "the number is wrong" and "the number is smaller than I expected".

## Consequences

- The panel can now say "0 to reindex" beside "65 gone": the honest end state of a library whose
  backlog is unreachable. A pass that changes nothing is no longer presented as a failure.
- `stale` remains a superset of the work. Anything reading it (CLI, tests, the dialog's own line)
  must ask `missing_watch` for the split rather than inventing its own.
- `missing_roots` (ADR 41's folder-level view) stays in the payload for the dialog's caveat list;
  `missing` is the authoritative count, and the headline no longer reads the older field.
- Nothing here authorises removing a gone resource. That remains an explicit action a person takes
  on the folder's own box, named — ADR 41's rule, untouched.
- Re-measured on the copy of the live library: offered work **65 → 0**, `wanted` **839 → 839**, the
  pending list **0 fixable** with 64 rows marked `why: "gone"`, and the dialog's button disabled
  because there is genuinely nothing it would do.
