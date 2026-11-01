# 30. A confine is something you name: the server is unrestricted until you ask

Date: 2026-11-01
Status: accepted

## Context

The report was blunt: *"By default there is no path confinement (path_out_of_range). There is a
`--confined` option to `box serve` to activate this behavior."* Establishing what existed first turned
up something different from the obvious reading.

**There was no write-side gate at all.** `POST /api/boxes` and `PUT /api/boxes/{ref}` accept any
`resource_uri` this account can name, and still do. The refusals the reader hit came from three other
places, and one of them was not even a boundary:

| Where | What it did before |
| --- | --- |
| `paths.content_root()` | Fell back to **the home directory** when nobody named a root — and returned a `Path`, so "no confine" was not expressible |
| `BoxStore.content_root` | Set from that call, so every library carried an implicit home confine |
| `search._extract` | Skipped any file outside that confine, with a note that was then thrown away: the resource was marked *read* with nothing in it |
| `/api/fs/browse` | Bounded by `content_root` **or home**, and refused to climb above it |
| `/api/boxes/{ref}/raw`, `_box_file` | Refused with `resource_out_of_range` when `$BOXGANIZER_CONTENT_ROOT` was set |
| `box check` | Reported `out_of_range` against whatever `content_root` answered |

So the default was not "confined, with a hole". It was **confined to the home directory by a fallback
nobody chose**, in the two places that matter least (reading a box you already filed, browsing for a
file to add) and *not* in the place a confine is supposed to be (deciding what may be filed). The
result was the worst combination: a photo on another mount could be filed, served, and quietly never
indexed — search missed it and nothing said why, because the skip note was discarded and the resource
was stamped as read.

## Decision

**A confine exists when someone names one. Otherwise there is none.**

- `paths.confine(explicit)` returns the named root, or `$BOXGANIZER_CONTENT_ROOT`, **or None**.
  `paths.content_root()` stays, and its job is now only what its callers actually want from it: the
  directory the file selector *opens on*. Opening somewhere friendly is not a boundary, and conflating
  the two is why a picker that helpfully started at home also refused the directory above it.
- **`box serve --confined`** turns confinement on using the default root (the content root, i.e. home
  when nothing else is named). `$BOXGANIZER_CONTENT_ROOT` still confines by itself — a reader who set
  it as a safeguard must not find it quietly lifted — and naming `content_root=` when embedding the app
  counts as the same opt-in.
- **Confinement gates writes and the picker, not reading what is already filed.** A new or changed
  `resource_uri` outside the root is refused with `resource_out_of_range` and a sentence naming the
  flag; the picker will not climb above the root; and the indexer reads inside it. A box already
  stored outside the root stays servable, listable and editable — including renaming it, which must
  work precisely so that a stray box can be *moved in* from the interface that holds it. `fslist`'s
  traversal guard inside a box's own directory is untouched: that prevents `../` escaping, which is a
  different question from what may be filed.
- **The store carries None, not home.** One value flows to the page, the index pass and the checker, so
  they cannot disagree about whether a file is reachable. Before this change they did: the create path
  read a `/tmp` file with no confine while the index pass skipped the same file as outside home.
- **`box check` can only call a file out of range when a range was named.** No confine, no such finding.

## Consequences

- **The CLI's default changes too.** `box index` and `box check` used to read inside home and now read
  anywhere this account can; `--root` restores the boundary. This is the same fallback being removed
  once rather than kept alive in one interface, and `box index` says which it did:
  `reading anywhere this account can` or `reading inside /path`.
- The second warning at the top of the README — *access to web app = shell with web server uid* — is
  more true than it was, and says so plainly rather than being soothed by a flag. The confine was never
  a sandbox: the same server can be told to file `/etc/passwd` today, and `--confined` does not stop a
  determined request either, because the process already runs as you. What the flag buys is (a) the
  accidents — a picker that wanders, a drag that files from an unexpected mount, an index that reads
  the whole home directory into searchable text — and (b) a machine that serves more people than the
  one who started it. Anyone who needs a real boundary should run the server as a user who can read
  only what the library may read, or in a container; `--confined` is not that.
- A confined read of an out-of-range file still produces an honest refusal, but the **record is
  misleading today**: the resource is stamped read-with-no-text, and the outstanding list calls it
  *"has bytes but gave up no text to index"* instead of naming the confine. Pinned as observed in
  `tests/test_search.py::test_a_named_confine_still_declines_to_read_outside_it` rather than quietly
  asserted to be nicer than it is; the fix belongs with the index work that follows.
- Measured in Chromium against **two servers on one library** — `box serve` on 8841 and
  `box serve --confined` on 8842, both with the env var unset, over a directory on `/tmp` (`/tmp/
  check_confinement.js`, seeded by `/tmp/seed_task4.py`, run by `/tmp/run_task4.sh`): **14/14 clean.**
  Free: the picker reaches `/etc` and the mount and still opens at home; the stored out-of-range box is
  readable and editable; the directory box on the mount lists; a real file drag files the outside path
  and its words come back through the ordinary content search (`how: "content"`, passage
  `"quokka runs at [eleven] [knots]"`); the API files an outside path. Wall: the picker stops at the
  root (`/etc` → 403), the *same drag* is refused with the sentence naming `--confined`, the API says
  the same, and the already-stored box is still readable, listable and editable. The shell is
  unaffected: `box add --resource /tmp/…` still files it, because a flag on a web server does not
  reach into a terminal.
- Pinned by `tests/test_search.py` (a named confine is the only kind; an outside file is indexed by
  default; a named confine still declines), `tests/test_web.py` (both settings on both write paths, the
  refusal sentence, the stored box still readable and renameable, the picker's wall and its freedom)
  and `tests/test_cli.py` (the shell files any path; `--confined` reaches the runner; the default does
  not).
