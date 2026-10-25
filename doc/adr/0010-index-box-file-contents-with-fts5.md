# 10. Index box file contents in the database, with FTS5

Date: 2026-10-25
Status: accepted

## Context

Boxes point at things. Most of the time a name is enough to find one, but a library
of notes, transcripts and scanned-in paperwork is searched by what it *says*, not by
what it is called — and a name search over 900 boxes that cannot look inside them is
the tool people wanted in the first place.

Searching the files on every query is not on: a library pointing at a slow disk or a
large PDF turns a keystroke into a minute. So the text has to be read once and kept
somewhere, which raises two questions the rest of this decision is about — where the
kept text lives, and what to do about the words a person types not being the words in
the file.

The library is one SQLite file by [ADR 2](0002-store-boxes-in-a-single-sqlite-file.md),
which means any index Boxganizer keeps has to travel with that file, and must not be
the kind of thing that goes silently out of step with it.

## Decision

Index into the same SQLite file, using the FTS5 module: a `box_text` virtual table
keyed by box **uuid** (not by name — a rename must not orphan an entry), with the
porter stemmer, and a companion `fts5vocab` table so the words actually present can be
asked for.

Reading a file is `boxganizer.search`'s job, not the store's. The store keeps rows:
`index_text`, `drop_text`, `content_search`, `vocabulary`. What is worth reading, how
big a file may be, and where the confine is, all stay outside it.

Boxes are indexed as they are created and changed, from whichever interface did it
(CLI and web both call `search.sync`). `box index` re-reads everything.

A query is widened by three means before it is asked, and every widening is reported
back to the person who typed it:

- **stems**, from the porter tokenizer, which is applied at index time and again to
  the words being searched, so the two meet in the middle;
- **synonyms**, from a list in the code merged with the user's `thesaurus.json`;
- **near-miss spellings**, compared only against `fts5vocab` — the words this library
  really holds.

Names, descriptions, resources and metadata keep being searched as substrings, and
are deliberately **not** widened.

## Consequences

- A search answers in milliseconds whatever the disk is doing, and works offline.
- The index can disagree with the files, and three ordinary things make it: a library
  copied to another machine, a database written before the index existed, and a file
  edited outside Boxganizer. `box index` is the stated remedy, and it is why the
  reading is a separate call rather than a trigger.
- Keying by uuid means the entry follows the box through a rename. Deleting a box
  deletes its text in the same call — a search that keeps offering a deleted box's
  file is not a stale cache but a broken promise.
- Widening only against words that are really indexed keeps fuzzy matching honest:
  it can offer "cellar" because you keep a cellar, and cannot offer "chela" because
  nothing here says that.
- Because a name is not widened, `Cellar Plans` does not turn up for "basement" on its
  name alone — but the file inside it is searched, so nothing is lost, only mislabelled
  no longer.
- Reporting every widening costs a line of output and buys the only answer to "why is
  this here?" that lets a person trust the result, or correct the thesaurus.
- Text lands in the database, so a library containing secrets now has them in two
  places. The confine, the size cap and the type list are the mitigation; anyone
  encrypting the database file is encrypting the index too, which is the right shape.
