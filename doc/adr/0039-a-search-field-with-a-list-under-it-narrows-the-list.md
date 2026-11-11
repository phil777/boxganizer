# 39. A search field that has a list under it narrows the list; the window is only for when there is none

Date: 2026-11-11
Status: accepted
Supersedes: part of 34 (the field unrolls a jump list *and* narrows the view → for a surface that has
a list, it narrows and does not unroll)

## Context

ADR 34 made one field do two things at once: unroll a window of matching descendants, and narrow the
list underneath to the same answer. That was the right first step after two competing search boxes,
but with both running the window sits **on top of the thing it is describing**. Type three letters in
a folder and the rows you are about to click — in place, with their icons, their type, their position
in the shelf — are covered by a second, smaller, worse-rendered copy of the same matches.

The request was flat: for folder-type boxes (folder, directory, indexed-directory, cabinet) there is
to be no dropdown, and the displayed list filters in real time.

## Decision

**Ask the surface, not the type list.** The widget shows the window only when nothing underneath is
listening: `sink.handlers.size > 0` means a view is narrowing itself in response to this term, so the
window is withdrawn. A sink is replaced on every render of the box and views register as they draw, so
"who answers now" is exactly "who is registered now", and no type catalogue has to be maintained. A
hard-coded list of folder-ish types would have drifted the first time a view learned to filter — and
drifted silently, because the failure mode is a popup nobody asked for rather than an error.

So: folder, directory, indexed-directory, cabinet and every other view with a narrowing handler filter
live and show nothing else; a note, an image, a sensor, an SSH box — no list to narrow — keep the
window, because for them a hit is somewhere else in the library and there is nothing on screen to
dim. `aria-expanded` follows the window rather than the keystroke, so the field does not claim a popup
that a screen reader cannot find. Keyboard navigation of the window is untouched where the window
still exists.

**A deep match is shown by the direct child that holds it, and the row says so.** The visible set is
unchanged from ADR 34 — hits plus every box on the route to a hit — which is what keeps a match three
levels down reachable rather than hidden. What the removed window used to say is now said on the row:
*"1 match inside: T3-Deep-Target"* for a folder, *"1 match inside"* for a directory subtree. Without
that line a kept row looks like a filter that does not work, because the term appears nowhere on it,
and the reader's next move is to widen the search rather than open the drawer.

**Which step of `route` is the child of this folder.** `route` starts at the box that was searched
(ADR 33), so the child on the way to a match is the step *after* this box, not the first step. Taking
`steps[0]` reads the folder's own uuid, never matches a row, and the marker silently never appears —
a missing feature that looks like working code. The general rule: a route owned by the searcher must be
offset before it is read as a path of children, and the same trap in the per-origin click work took a
hit that drew twice.

**A directory's filter keeps the folders its matches live under.** `entryShows` matched a file row by
name and against the paths the index reported, but not against a *directory* that contains one, so
typing a word held only in `sub/boiler.md` removed `sub` — the single row that could reach the match.
The listing now keeps an entry when a reported path starts with `entry.sub + "/"`, which is the
directory-view twin of the rule two paragraphs above.

**A Set is not an Array.** The first version called `.filter()` on `filter.files`, which is a Set, and
threw inside the draw: the directory went blank and the symptom read "the filter finds nothing" rather
than "the filter crashed". A blank list and an empty result look the same to a reader and are nothing
like the same to whoever has to fix it, so the conversion is explicit and this sentence exists.

## Consequences

- One gesture, one answer, drawn once. The page has fewer moving parts on screen while searching, and
  the thing being searched stays visible — which is the point of searching inside a box rather than
  searching the library.
- Jumping to a *specific* deep descendant from a folder field now needs one more click: narrow to the
  drawer, open it, narrow again. That is accepted deliberately. The row's "matches inside" line names
  what is below, so the click is informed rather than exploratory, and the list shows the position the
  popup never could.
- The remembered-term history and the `config.folder.history` behaviour from ADR 34 are unchanged, as
  is the pane isolation rule (a neighbour's list is never narrowed by a term typed here) and ADR 25's
  refusal to file by hand into a filtering folder.
- A view that wants to filter must register a `filterSink` handler; it gets the window withdrawn for
  free, which is the correct default. A view that registers nothing gets the window, which is also
  correct. Nothing has to be remembered about which types are which, and that is why the rule is
  written this way.
- `childTile` gained an optional fourth argument (the "below" note). It defaults to empty, so every
  existing caller still compiles and draws nothing extra; the marker appears only where a filter is
  active and the match is genuinely below.

## Amendment, found while re-verifying the whole tree

The rule above — withdraw the window when a surface underneath is listening — is **wrong for a
view whose list is not made of boxes**, and the error is the exact one this ADR exists to prevent.

A directory, and an indexed directory, answer their field by filtering **file entries**. The library
search those fields run answers with **boxes**. So a box filed under a directory, matching the term,
had no row it could be drawn into: the listing narrowed, the window had been withdrawn, and the hit
existed in no surface at all. Measured after the change, against the pre-change fixture: `directory:
via "nothing"`, zero rows, no window, no route — a match the reader was told about and could never
reach, which is ADR 34's dead end with the sign turned around.

**The predicate is therefore "did the surface take this hit", not "is a surface listening".** A
handler returns the number of hits it put on screen; the window paints for whatever is left:

    const shownLive = emit(term, hits);
    if (shownLive >= hits.length) close(); else paint();

Two consequences worth stating plainly:

- **A handler that returns no number counts as having shown nothing.** The failure mode of an
  unreported handler is a window nobody needed; the failure mode of an over-reported one is an answer
  nobody can open. The default points at the harmless one, and the structural test names both views
  that report today, so a third that forgets shows up as a window rather than a lost hit.
- **A filesystem listing claims only its own box.** It can show the directory's own content hits (the
  file is the row), and nothing else — not another box filed underneath. Measured on the corrected
  rule: `folder / cabinet / dynamic-folder → via "live list"` with no window, `directory /
  indexed-directory → via "window"` for a box hit, and the same two directory types closing the
  window again when the term matches their own files (`carryall`: `windowOpen: false`,
  `carried: true`, 87 assertions true).

**Counting on the handler's return value changed an argument the views never read, and that bit
once.** Clearing the field has always emitted `("", null)` — null meaning "no answer, show
everything" — and `null.length` inside a counting handler threw three `TypeError`s on the first
Backspace. `emit` normalises the list in one place now, because the alternative is every future view
having to remember a quirk of a funnel it does not own.
