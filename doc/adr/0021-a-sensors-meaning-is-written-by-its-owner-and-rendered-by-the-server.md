# 21. A sensor's meaning is written by its owner, in Jinja, and rendered by the server

Date: 2026-11-09
Status: accepted

## Context

A boiler publishes `{"pressure": 1.42, "unit": "bar"}`. A door sensor publishes the word `open`.
A power meter publishes three-phase text with the number on the second line. The old sensor box
showed the payload, which is like a book that shows you its ink.

The request was specific: **all** sensor boxes (MQTT included) extract a value from the raw
message with a template, then either process it numerically or word it with a second template, and
optionally dress it with a third template that outputs "an html-like styled expression" where
"only formatting directives like `<i>` `<b>` `<style ...>` are allowed".

Three forks had to be settled: what language the owner writes, where it runs, and what "only
formatting" can mean when the output is markup.

**Unit dropdowns and a fixed set of transforms.** Endless to support and never enough — the sensor
on the boiler shed is not the one the plan predicted, and a box that cannot say what *its* payload
means is a box that shows the payload.

**A small expression language of our own** (`$.pressure * 2`, say). One more language to design,
document, and get wrong; no error messages anybody recognises; and every person who already knows
Jinja, Ansible, or Home Assistant templates has to learn ours.

**Rendering in the page.** Cheaper per message, but then the CLI, the TUI and every other surface
needs its own Jinja — and the sanitiser, which is the security boundary, would be JavaScript,
where the box's HTML is also being inserted.

**Autoescaping the styling template instead of filtering it.** Autoescape turns the owner's `<b>`
into `&lt;b&gt;`: the feature would not exist. The output *is* markup, so markup has to be judged,
not escaped.

## Decision

### 1. Three templates, in the box's own config, in Jinja

`extract_template` → the value in what arrived. `display_template` → how that value is worded.
`style_template` → how those words are dressed. They live in `box.config` because what a reading
means is the owner's business, and every way of writing a box (the page's drawer, `box edit`, a raw
config write) then passes the same validation.

Each step sees the steps before it: `raw` and `data` (the payload, parsed when it is JSON),
`number`, `value` (this step's input), `extracted`, `display`. A template never re-derives what the
pipeline already worked out — and if a later step disagreed with an earlier one, the number on the
screen would depend on which template you happened to read.

### 2. Jinja, in a sandbox, with no loader

`SandboxedEnvironment(undefined=StrictUndefined)`, built once, **with no loader**: `{% include %}`
and `{% extends %}` need one, so the only template a sensor can run is the one written in the box.
The sandbox blocks attribute games on objects that were never handed to the template. Undefined is
an error rather than the empty string, because `{{ data.pressrue }}` rendering as nothing is how a
sensor starts lying.

We did not write a template language, and we did not wrap Jinja in a "safe subset" of it: Jinja's
own sandbox plus a filter output that is *sanitised after the fact* (below) is two well-understood
mechanisms rather than a third one nobody has attacked.

### 3. The server renders, and says what failed

`sensors.present(box, reading)` produces `{raw, extracted, display, styled, problem}`, and the
live endpoints put it beside the reading — on the snapshot and on every SSE message, so a page that
joined late says the same thing as one that has been open for an hour.

**A template that fails says so, and the reading stays on the screen.** The sentence appears where
the dressing would have been, next to the value, and the value falls back one step: a broken
styling template still shows the words, a broken extraction still shows the payload. This is not
generosity about other people's typos — a dashboard whose number vanished is indistinguishable from
a sensor that stopped reporting, and those two need opposite responses. The exception is caught
broadly (`except Exception`, at each of the three steps) because a template evaluates the owner's
expressions: `{{ 1 / 0 }}` raises `ZeroDivisionError`, `{% include %}` raises `TypeError`, and
naming the types we predicted is not a rule, it is a guess.

Syntax errors are also refused at **save** time (`SensorResource.check` → `check_templates`), so
the usual case is a sentence in the form that made the mistake. A subtype that overrides `check`
must call `super().check` — MQTT's own address check silently replaced the sensor's template check
until the test for it was written, which is what a hook that is easy to override looks like when
nobody remembers to chain it.

### 4. Styling is filtered by an allow-list, in the language that owns the rule

The page inserts `styled` as HTML — once, deliberately, and it is the only place a box's own text
goes in as markup. Before that, `sensors.sanitise` rebuilds the fragment with an HTML parser:

* **Tags:** formatting only (`b, i, em, u, s, del, mark, small, sub, sup, span, p, div, br, hr,
  code, pre, kbd, samp, var, abbr, font`). An unknown tag loses the tag and **keeps its contents**,
  so a typo like `<blod>` still shows the word rather than swallowing it.
* **Gone with contents:** `script, style, iframe, object, embed, svg, math, link, meta, form,
  input, button`. A `<style>` element is not formatting — `@import` reaches outside the pane.
* **Attributes:** `style` (see below), `class` (only the page's own `sensor-*` names), `title`
  (a tooltip explaining why something is red is part of what a sensor says), and `color` on
  `<font>`. `onclick`, `href`, `src`, `id`, `data-*` are simply not copied out.
* **Style properties:** a named list, and a property is kept only if its value contains no `url(`,
  `expression(`, `@import`, `behavior:` or script scheme. `position: fixed` is a real CSS property
  and it is refused: it is how one sensor's value gets painted over somebody's whole page.
  `width`/`height` must be a plain number and unit, so `calc()` and `var()` cannot smuggle a
  layout. `font-family` is re-parsed into names and commas, because `font-family: x; position:
  fixed` is the classic way out of an attribute.
* **Unclosed tags are closed** at the end of the fragment: a template that forgets `</b>` must not
  leave the rest of the page bold.

The one rule shared with `markdown.py` is `safe_url` — an address that would run something is not
an address. It is imported there rather than copied, because two lists of dangerous schemes drift
the first time one of them is updated. `markdown.py` gets its safety from `html=False` (raw markup
never becomes markup at all); a styling template's whole purpose is to emit markup, so it needs the
other half of the same discipline: decide what markup may exist.

### 5. Where the owner writes them

The box's settings drawer: three labelled fields — *The value in what arrived*, *How the value is
worded*, *How the words are dressed* — saving the config as one object, because the three are one
pipeline and a form that could save a display template against a half-typed extraction template
would let a person leave a box in a state it cannot render.

## Consequences

**Costs.** jinja2 is now a base dependency (it was already arriving through typer's `rich`; that is
someone else's choice to reverse). A 40-entry property list will occasionally refuse something
harmless, and the fix is to add it to the list, in one place, with a test. `styled` is a string the
page must trust, and that trust rests on one function — so it is the function the tests aim at.

**What it buys.** Every sensor type gains the behaviour by inheritance, and the CLI, TUI and API
read values through the same call the page does. `type:resource`-style honesty applies here too: a
box says what its templates said, and what it could not say.

**Verified.** 14 tests in `tests/test_sensors.py` (extraction from JSON, plain text and a bare
`42`; the display step seeing raw and extracted; the styling step seeing all three; `<script>`
gone with its body; `<img onerror>`, `onclick`, `href="javascript:"`, `id`, `data-*` refused;
`position: fixed`, `url(javascript:)`, `calc()` refused; a forgotten `</b>` closed; a failing
template reporting itself at the right step while the reading stays; save-time refusal; `{% include
%}` and `{{ ''.__class__ }}` reaching nothing) plus API tests that a live MQTT reading arrives
`shown` and sanitised. In the browser against a real broker: `1.42 bar — high` drawn as
`<b class="sensor-bad">` at rgb(208, 2, 27), `0.61 bar` as `sensor-good` at green; a non-JSON
payload left the sentence "the sensor is talking about the weather" on screen with all three
failures named; and a styling template carrying `<script>`, `<img onerror>`, a `javascript:` link
and `position: fixed` left `<b>1.42 bar</b>x<span>reached out</span>` with the style attribute
stripped, no script or img node anywhere in the pane, no dialog fired, and the global it tried to
set never set at all.
