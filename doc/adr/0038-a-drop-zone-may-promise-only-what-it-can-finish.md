# 38. A drop zone promises only what it can finish, and an address is never a filing instruction

Date: 2026-11-09
Status: accepted

## Context

Three reports about the same gesture, all of them true:

An image dragged from another window over the *drop resources here* strip **did not light it**, and
letting go opened the image in a browser tab. A file dropped on a grid cell answered **404**. A file
released anywhere else on the page — on a heading, a crumb, the gap between panes — could **navigate
the app away**, losing the pane walk ADR 29 was written to protect.

The strip's failure and the grid's failure are the same mistake pointing opposite ways. The gate read
`dataTransfer.files`, and during a drag from outside the window **that list is empty**: the browser
exposes `types: ["Files"]` and withholds the bytes until release. So the strip judged a real file
drag to be nothing, and because its `drop` handler returned *before* `preventDefault()`, the browser
did the default thing with a dropped image — opened it. The grid trusted the opposite field,
`text/plain`, which Chrome fills with the image's **URL**. It filed that string as a child reference
and posted it to `grid/place`, which answered 404 for a box named after a picture.

Both are guessing. The page was inferring what a drag *means* from whichever field happened to be
convenient, and every one of the fourteen drop handlers did it slightly differently.

## Decision

**A zone lights up for a payload it can finish, and the test is what a drag can promise, not what it
has yet delivered.** `carriesFiles()` reads `types.includes("Files")`, not `files.length`. `types` is
available for the whole drag; `files` is not, and a highlight that appears only at the instant of
release is not a highlight — the person has already let go. The cheap type test always runs before
the payload is read, because `dragover` fires around sixty times a second while a pointer moves and
reading `getData` on each one is work nobody asked for. ADR 27's clearing rule is untouched: a zone
that lights for a payload it cannot finish is the failure ADR 27 exists to clean up, so both are
needed, and neither is enough.

**One gate decides what may be treated as a box reference, and the reference is a uuid.**
`draggedBoxRef(event)` returns a reference only when the payload can be one: not a `Files` drag, not
the app's own `FILE_DRAG_TYPE` marker, nothing that looks like an address, and — in the end — nothing
that is not a **uuid**. The last rule was added after the first version of this ADR claimed every
handler went through the gate and a re-audit found three that read `text/plain` themselves. Two of
them were the mindmap board and a node tile, which lit for any drag at all and then posted an image's
URL to `mindmap/fill`; the third was the pane's fallback, which stands behind *every* view that did
not claim a drop, so it was the most reachable wrong handler in the page.

A uuid, and not a name, because the page writes a uuid into `text/plain` at both of its drag sources
and has never written anything else. Anything of another shape — prose, a name, a path, an address —
is evidence that the payload did not come from a box in this library. Checking membership in the
loaded list would be a stronger test and is not available: `/api/boxes` is a page of 200, so it would
start refusing legitimate drags the moment a library outgrew the page. What a name costs is nothing
the user can notice, because no gesture produces one; what it buys is that selected text from another
window cannot become a filing instruction.

The claim is now swept rather than asserted: a test walks every `dragover` and `drop` listener in the
file, fails if a `drop` handler reads `text/plain` outside the payload-inspecting helpers, and fails
if a `dragover` adds a highlight without first testing the payload. Sweeping is the only form this
rule can take — the next view is written by someone who has not read this page, and the mistake fits
on one line.

This is the whole of the 404 fix. It is also the fix for two worse things that had not been reported:
the note editor would have written that URL into a note's **own words** as though it were a box uuid,
and a checklist line would have pushed it into `item.refs` as an attachment. Both corrupt a document
while it is being edited, both survive the save, and both resolve to nothing later. The editor and
the checklist line keep their behaviour for boxes and now **recognise a file payload and refuse it
out loud**, naming the strip instead. They do not mutate anything on the way to saying so.

**Made before placed.** A grid cell and the desktop create the box first and place the uuid that
creation returned. A placement that runs on a string read off a drag cannot work in principle: the
box does not exist yet. The order is not an optimisation, it is the only sequence in which the second
call has an argument.

**A highlight is a promise made while the pointer is still moving, so it is tested against the
payload.** Most drop zones on this page were written when the only draggable thing was another box,
and their `dragover` called `preventDefault()` unconditionally. Once a file could arrive that became
a lie — the mindmap board outlined itself around an image it then ignored. Every zone that paints a
welcome now asks first (`claimableHere`: a resource, or a uuid), and the two zones that take a box and
refuse a file — the note editor and a checklist line — no longer light in the accent colour for a
file; they show the amber refusal the dynamic folder already used. An absent outline reads as "I
missed", and the person tries again; a drawn refusal reads as a decision, and they do not.

**A path is a file, and has to survive being reachable.** `droppedFilePath` existed since the first
drop work but nothing let a path-only drag past `carriesResources`, so dropping `file:///notes.md`
from a file manager did nothing at all — and the code that would have handled it was never executed,
which is how a `TypeError` came to be sitting in it: `placeMade` iterates its argument, and the path
branch handed it one box instead of a list. Making the route reachable exposed the crash in the same
hour. `file:` URIs are now decoded to a plain path before anything else reads them, because
`infer.suggest` looks at the suffix and `/tmp/notes.md` is a markdown box while `file:///tmp/notes.md`
is only a file; the box is named from the last segment without its extension. The lesson is general
and is the reason this ADR is longer than the change: **dead code is not safe code, it is untested
code with a claim on it.**

**A stray file drop is cancelled at document level, silently.** One listener on `document` claims any
`Files` drop no zone took, prevents the default, and says nothing. It says nothing because the person
was not aiming at the page — they were moving a file and missed — and a red status line for a miss
you did not mean to make is noise that teaches people to distrust the status line. It is prevented
because the alternative is losing the tab's contents to an image viewer, and a browser that navigates
on a near-miss is one the person stops using for file work. Zones still `stopPropagation()` first, so
this only ever fires on a drop nothing else wanted.

**A dropped link is filed as a link box, and nothing is fetched.** A drag carrying an address and no
bytes makes one `web` box whose `resource_uri` is the address — the same thing a typed URL makes. The
page asks the dropped host for nothing and the server fetches nothing. Two reasons, one of them not
about taste. Boxganizer is a card index: it points at things, and an index that downloaded whatever it
was shown is a scraper that happens to have a search box. The other is that fetching a dragged URL
lets any page in the browser induce this server into making requests to machines only its **owner**
can reach — a home NAS, an admin port on localhost. Verification that a link still works is a thing a
person asks for; it is not a side effect of a mouse release.

Naming comes from the drag: the anchor text in `text/html` when there is one, tags stripped by
regexp (never parsed into this page's DOM on the way to becoming a label), otherwise the last path
segment. Bytes beat an address when a drag carries both, because an image dragged out of a page is
the person filing **the picture**, not a pointer to somebody else's copy of it — which is the
difference that matters a year later when that site stops serving it. Dropping the same link twice
makes a second box with a visible ` 2` on the end, because names are unique here and reusing the first
box would mean the drop did nothing, and *nothing* is the one answer this gesture cannot afford.

**An unresolvable reference answers in words, at the layer that has to know.** Twelve routes resolve a
box ref, so one `BoxNotFound` handler does it: the body carries a sentence, the shape it decided the
value had (`address`, `file`, `uuid`, `name`, `empty`) and where the value came from. A reference from
the **body** is 422 — the route exists, the content cannot be processed — while a box named in the
**URL** stays 404, and a route that does not exist at all is left to the router, because the page
turns the router's own body into "restart `box serve`" and a stale server is a different emergency
from a missing box. The five shapes need five sentences: "you lost a box" and "you sent an address"
look identical as `box_not_found` and want opposite remedies.

**A refusal changes nothing.** `POST /api/boxes` with an unresolvable parent used to answer an error
*and* create the box, at the top of the library: the person read "failed", retried, and collected
orphans only they could notice. The parent is now resolved, and the target cell checked, before
anything is captured, written or indexed; and for what a pre-check cannot see — a grid whose cells are
all full, discoverable only once there is a child to place — the created box is taken back out. Half a
create is worse than none of it. `grid.ensure_fits` was split out of `grid.place` so the pre-check
uses the same predicate and the same sentence rather than paraphrasing the rule and drifting.

**A name clash says what clashed.** Every handler in this family now carries a `reason`, including
`duplicate_name`, which used to reach a person as three words of internal vocabulary when a dropped
file's name was already taken. The envelope around it — `{"detail": {…}}` — is asserted by a test,
because while writing the sentence it was briefly dropped, and every refusal in the page went back to
reading `HTTP 409`: the shape is the contract, and a nicer message inside the wrong shape is worth
nothing.

**A filtering folder still receives a file** (a boundary against ADR 25, not an exception to it).
ADR 25 refuses to *file a box into* a dynamic folder whose filter would immediately contradict the
filing. That rule is about membership. A file dropped on such a folder is brought into the library
and left unfiled, with a sentence naming both facts, because refusing the file would mean the pointer
was over the wrong target for a reason that has nothing to do with the filter.

**Only a drop captures** (ADR 37, reaffirmed against the new gates). A `dragover` creates nothing, a
highlight writes nothing, and the right-click "New box from this file" gesture still points without
copying.

## Consequences

- The strip highlights for drags it used to ignore, including drags whose files arrive only at the
  release. If the browser then delivers nothing — a drag from an application that pretends — the drop
  says so rather than failing quietly, because the promise was already made visibly.
- An accidental drop outside any zone has no visible effect at all. That is deliberate and it is also
  the change's one real cost: a person who *wanted* to open that file in a browser now uses the file
  manager for it. The page's own rule, kept: something that looks like a filing gesture either files
  or says what it did.
- "Nothing is fetched" means at the moment of the drop, and it is worth being precise about the
  boundary, because a `web` box still has a view that shows the page when you **open** it — that is what
  a browser box is for, and it predates this change. The distinction is the human action: opening a box
  is asking to see the thing behind it. A drop is not that question, so a drop stores the address and
  asks nothing of the network. If the view is ever changed to prefetch, or a list of link boxes starts
  rendering thumbnails, this sentence is the thing that has to be argued with first.
- The library now holds `web` boxes whose targets may rot. It always held pointers to files that move.
  What changed is that a pointer can be created by a drag rather than only typed, so rot becomes more
  likely in proportion. A "check this link" affordance is a separate piece of work; fetching at drop
  time would not have prevented rot anyway, only moved it to a moment the person was not watching.
- Every drop handler now shares one vocabulary: *claimed* means this zone can finish it, *refused*
  means a sentence, and *unclaimed* means the document guard ate it. Those three are the whole surface
  of the gesture, and each has exactly one implementation.
- Server-side ordering is now a rule rather than an accident: resolve, then write. Anything that can
  fail after the first write needs an undo, and the undo has to cover the index too — a box nobody can
  find but that `search` still returns is a bug that shows up as a ghost, not as an error.
- Two zones refuse files by design. If they ever start accepting them, this ADR is the thing that has
  to change first, and the tests that pin "the note's words did not move" will fail loudly rather than
  letting a phantom link into somebody's note.
- A box can no longer be filed by dropping selected text that spells its name. No gesture in this app
  ever produced that payload, so nothing that worked before stops working; what stops is a class of
  accident. If typing-a-name-into-a-drop is ever wanted, it wants its own affordance with its own
  completion, not a field that guesses from `text/plain`.
- The sweep makes new drop zones cost one line of attention each: a `dragover` that paints without
  testing the payload fails the test, and the fix is `claimableHere(event)`, not an exemption.
- A zone that no longer lights for a payload may read to a regular as a lost feature when it is the
  lie being withdrawn. The amber refusal is there for the cases where the zone still has something to
  say; for the rest, an unlit target that never claimed anything is the honest default.
