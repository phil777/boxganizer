# 22. A numeric sensor's scale belongs to its owner, is checked to cover itself, and clamps out loud

Date: 2026-11-09
Status: accepted

## Context

ADR 21 settled how a reading becomes a value and how that value is worded. A number is still only a
number on the screen: the boiler reads `1.42` and the person in front of the page has to remember
that anything over 1.3 means going down to the cellar. The request was a range (`min`/`max`), the
range split into intervals each with a colour of the owner's choosing, **fully covering it**, a
palette of 16 usual gauge colours beside the colour picker, and a choice of picture: gauge,
horizontal bar, vertical bar, progression bar. For text instead of numbers: a list of regular
expressions with a text and background colour each, and a default, as the alternative to a styling
template.

Three decisions mattered.

**Where the scale lives: the page, or the server.** Drawing is the page's job, but *where a value
sits* is a fact: `8.4` on a `0–10` scale is 84 % along, in the interval that ends at 10, coloured
`#d0021b`. Computed in the page, the CLI, the TUI and a grid cell would each need their own copy,
and a dashboard and `box value` would disagree about whether a boiler is about to burst.

**What happens to a value outside the range.** Options: draw it clipped and silent, draw it past the
end, refuse the reading, or clamp it and say so.

**Whether an incomplete set of colours is a mistake worth refusing.** "Fully covering it" was in the
request; the question is whether that is a suggestion or a rule.

## Decision

### 1. The server computes the position; the page paints

`sensors.numeric(box, number)` returns `{min, max, value, position, colour, interval,
representation, clamped, clamp_text}`. Every surface draws from that — the opened pane, a grid cell,
anything added later — and none of them decides anything. The CSS in `app.css` sizes the four
pictures; `sensorPicture()` in `app.js` sets a width, a height, or an arc, from `position`, and puts
`colour` on it.

That split also means the picture cannot be wrong in a way the value is not: when the browser showed
every interval as grey, the *measurement* of `dataset.colour` found it, and the server's own answer
was still correct — which localised the bug to the control, in minutes, instead of inviting a
rewrite of the model.

### 2. Out of range is clamped, and says which

A bar never draws past its track — a gauge at 140 % is a picture that leaves the pane — but the box
adds `above 10` beside it, and the element carries `data-clamped="high"`. Silent clamping is the
failure mode worth refusing to ship: a sensor pinned at the end of its scale looks *exactly* like a
sensor at the top of its range, and the one is an emergency while the other is a broken wire or a
scale set wrong. The number itself is still shown in words (`14 bar`), because the picture is there
to be read at a glance and the text is there when the glance is not enough.

### 3. Colours that do not cover the range are refused, and the hole is named

`check_numeric` runs on every write path (the drawer, `box edit`, a raw config write) because it
hangs off `SensorResource.check`:

* `min` below `max`, both numbers; representation one of the four.
* at least one interval; every interval has a numeric `upto` above the previous one; every colour is
  a CSS name or a `#rgb`/`#rrggbb`.
* **the first stop must reach above `min`, and the last stop must reach `max`** — otherwise:

      numeric: the colours stop at 6 but the range runs to 10 — a reading of 8 would have no colour

The message names a reading that would fall through, because "the intervals must cover the range"
is a sentence about a rule, and the person editing the form needs the sentence about their form.
Without the rule, a scale with a hole is invisible: the colour changes correctly for the values
that arrive, and the one value that lands in the gap arrives with no colour at all — usually the
value that mattered.

### 4. Sixteen presets beside the picker, and both from the server

`PALETTE` is sixteen colours in the order a scale reads — green, lime, yellow, orange, red, maroon,
purple, magenta, pink, blue, navy, cyan, teal, brown, grey, black — the cool-to-hot run a person
expects on a gauge, plus the neutrals a sensor needs (grey for unknown, black for off). It is served
at `GET /api/sensors/palette` and the swatches draw from that, because the same list is what
`check_numeric` accepts: a picker offering a colour the store would refuse is a control that lies
about what it saves. The `<input type="color">` and the text field beside it cover the colour that
is not on the list; all three are one control with one state (`choose()`), not three inputs with
three opinions.

### 5. Text: a styling template or a list of patterns, and the template wins if both exist

`config.text_colours` is `{rules: [{pattern, colour, background}], default: {...}}`, **first match
wins** — two patterns can both match `2.4 bar`, and a list whose order did not matter would let a
person reorder rules and change nothing, which is not how they will read it. Patterns are compiled
at save time. When a styling template also exists it takes precedence and the presentation records
`dressed_by: "template" | "colours" | "none"`, so the box can say — and a test can assert — which
instruction made a value red. Nobody should have to guess that from a screenshot.

## Consequences

**Costs.** A validator with a dozen messages to keep honest (they are tested, including the one that
names the fall-through value); `position` rounded to four places on the wire; four drawings to keep
looking right at pane size and at cell size. The clamp label costs a line of the pane.

**What it buys.** A sensor that can be read across a room, in four shapes, with the colour decided
by the person who knows what the number means — and one arithmetic behind all of it, which the
page, the CLI and the tests all read from the same call.

**Found on the way, and fixed here.** Two rules in the stylesheet and the picker made this feature
lie until it was measured: `.box-config { max-height: 14rem }` clipped any settings row taller than
14rem, so a scale editor with colour stops overflowed its own box and the message list underneath it
swallowed the clicks on *Save*; and `colourChooser`'s redraw wrote the control's original value back
after every pick, saving grey for all three intervals. Both were invisible in a screenshot of the
value and obvious the moment the DOM was measured — which is why the contract for this feature is
written in measurements. ADR 21 covers the templates; this one covers the scale.
