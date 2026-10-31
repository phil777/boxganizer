# 19. A tag's name is its identity, and its parents are never stored

Date: 2026-11-09
Status: accepted

## Context

The type says what a box *is*. Nothing said what a box was *about*, and a box is about several
things at once. Tags are the person's own words for that, and five decisions follow, each of
which has a cheaper-looking alternative that is worse.

The hierarchy is what makes most of them hard: `boiler` under `kitchen` under `home` means a box
tagged `boiler` is also about the kitchen and the home, and every screen that draws a tag has to
agree about that.

## Decision

### 1. A name is the identity, and it is unique across the whole library

| | |
| --- | --- |
| chosen | `boiler` is one tag. `home/kitchen/boiler` is notation for three unique names in a tree. |
| rejected | path-keyed tags, so `a/boiler` and `b/boiler` are different rows |

Path-keyed tags make the same word under two parents two things, which then has to be resolved
everywhere: a chip that reads `boiler` with no context, a filter `tag:boiler` that means two, a
manage window that has to ask *which*. Unique names make `box ls --tag boiler` and (later)
`tag:boiler` unambiguous without either carrying a parent.

The same reasoning gives `COLLATE NOCASE`: `Home` and `home` in a list of chips is one tag that
looks like two, and the difference between them is never what somebody meant. Case is the owner's
to display and nobody else's to distinguish.

### 2. Inheritance is computed, never stored

A box given `boiler` **means** `kitchen` and `home`. The explicit set is what was chosen; the
implied set is what that choice commits the box to.

Storing the inherited rows too would be two copies of one fact, and a tag moved in the hierarchy
would strand the old parents on every box that had inherited them — a corruption that looks
exactly like the user's own data. `Hierarchy.implied()` is asked when it is needed, over a list
nobody keeps thousands of.

The split is visible in every response and in the page: **an inherited chip is dashed and has no
cross**, because removing it means moving the tag in the hierarchy, not editing the box. A
control that cannot do the thing it looks like it does is worse than no control — this is the
same rule that hides the gear when a box has no settings row.

### 3. Deleting a tag re-parents its children

`delete_tag("home")` lifts `kitchen` and `tools` to the top and takes `home` off the boxes that
were given it directly. The alternative — cascading — silently untags hundreds of boxes, and a
delete that large is not reversible from a confirmation dialog nobody reads carefully. The
response reports both numbers so the manage window can say what moved and how many boxes
changed.

### 4. Colour is derived from the branch root, in Python

* A hue per top-level tag, lightening as it goes down, so **the hierarchy is visible without
  reading it** and a forty-tag library still looks like one thing rather than a bag of marbles.
* Computed server-side and sent with the chip (`colour`, `text`): the page would otherwise have
  to reimplement the rule to draw one chip, and a chip that disagrees with the manage window
  about a tag's colour is a bug report waiting to happen.
* The hue comes from a blake2b digest, not `hash()`, which changes between runs and versions —
  that would repaint a library between two openings of the same page.
* `text_colour_for` uses perceived brightness (Rec.601), not HSL lightness. Pure yellow is
  lightness 50 and is the most legible colour there is; the first version of this function
  offered white text on it.
* An explicit colour is allowed, parsed by this module before it is accepted, and its branch
  inherits its hue. Anything unparsable is refused rather than handed to the browser, because
  the text colour is derived from it and a silent failure there is white on white.

### 5. The chip line changes one tag at a time

`POST /api/boxes/{ref}/tags` and `DELETE /api/boxes/{ref}/tags/{name}` exist alongside the
whole-set `PUT`, for one reason: a cross on a chip must remove *that* tag. The first version
rebuilt the set from the list the page had drawn, so a click could silently delete a tag added a
second earlier — including by the same page, in another window. A delta cannot be stale that way.

The whole-set write stays for the dialog, where the person *is* replacing the set.

The chip line lives in `#main`, **outside the canvas**, so no view knows tags exist. Thirteen
views each painting chips is thirteen chances to paint them differently.

## Consequences

**Costs.** A second table pair and a hierarchy walk per rendered box (cheap: it is a list of a
person's own words, not an index). Two counts on every tag rather than one, because "what was
typed onto a box" and "what a filter would find" are different questions — shown together they
explain the hierarchy without a paragraph about it.

**Limits, said rather than hidden.** One parent per tag: two parents would make the implied set
depend on the path walked, and would turn the manage window from a tree into a graph editor.
`MAX_DEPTH` is six names. Tags may not contain `/`, a comma, or a quote, and may not be `*`,
`all`, `none`, `any` — words the filter grammar will mean.

**Verification.** In the browser on a clean library: typing `boiler` produces one solid chip
with a cross and two dashed ones without, in three shades of one hue; typing `urgent, plumbing`
creates both and invents neither tag twice; a cross removes exactly one name; the manage window
renames `home` to `Household` and every chip follows; moving `Household` under `boiler` is
refused *on its own row*, because a window with a dozen inputs and one status line at the bottom
says nothing about which field failed.
