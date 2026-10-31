"""What happens between a message arriving and a value appearing on screen.

Every sensor box — MQTT today, HTTP and Home Assistant next — receives something and has to
decide what it *means*. A boiler publishes `{"pressure": 1.42, "unit": "bar"}`; the person who
filed it wants `1.42 bar`, in red above 1.3, and that decision belongs to them, not to a
dropdown of units this project guesses at.

Three questions, three answers, all written by the box's owner as templates:

* **extraction** — what part of what arrived is the value? `{{ data.pressure }}`
* **display** — how is that value worded? `{{ value }} {{ data.unit }}`
* **styling** — how are those words dressed?

    <b style="color: {{ 'red' if number > 1.3 else 'inherit' }}">{{ display }}</b>

Each step gets the steps before it in its context, so a styling template can see the raw
payload, the extracted value and the finished words. Templates are Jinja, evaluated in a
sandbox with no loader: `{% include %}` has nothing to include from, and there is no path to a
file on this machine.

**Two rules hold the whole thing together.**

*A template that fails says so, and the box still shows its value.* A sensor whose reading
vanished because somebody typed `{{ data.pressure` is a sensor that looks broken and a dashboard
with a hole in it. The sentence goes where the value would have been dressed, next to the value
itself, which is still on screen.

*Styling is markup the owner wrote, and it reaches the page through an allow-list.* The page
puts this HTML in the pane, so anything a template can emit, an attacker can emit if a box file
ever comes from somewhere unfriendly. Only formatting survives here, and the same rule about
clickable addresses that `markdown.py` uses — an address that would run something is not an
address — is imported from there rather than copied, so the two cannot drift.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from html import escape
from html.parser import HTMLParser
from typing import Any

from jinja2 import StrictUndefined, TemplateError
from jinja2.sandbox import SandboxedEnvironment

from boxganizer.markdown import safe_url
from boxganizer.models import Box

#: The three templates a sensor box may carry, in the order they run.
TEMPLATES = ("extract_template", "display_template", "style_template")

#: A template is not a document. Anything this long is a mistake or an attack, and the second
#: is the one that costs a machine its stack.
MAX_TEMPLATE_CHARS = 4000

#: Tags a styling template may use. Everything here changes how text looks; nothing here can
#: load a resource, run a script, navigate anywhere, or lay out somebody else's page.
FORMAT_TAGS = frozenset(
    {
        "b",
        "strong",
        "i",
        "em",
        "u",
        "ins",
        "s",
        "del",
        "mark",
        "small",
        "big",
        "sub",
        "sup",
        "span",
        "p",
        "div",
        "br",
        "hr",
        "code",
        "pre",
        "kbd",
        "samp",
        "var",
        "abbr",
        "font",
    }
)

#: Tags whose *contents* go with them. A `<script>` with its text left behind is only a
#: distraction; a `<style>` element can reach outside itself with `@import`.
DROP_TAGS = frozenset(
    {
        "script",
        "style",
        "iframe",
        "object",
        "embed",
        "svg",
        "math",
        "link",
        "meta",
        "form",
        "input",
        "button",
    }
)

#: CSS properties a `style` attribute may set, plus the prefixes below. Values are checked
#: separately: a property on the list with `url(` in it is still refused.
STYLE_PROPERTIES = frozenset(
    {
        "color",
        "background",
        "background-color",
        "background-image",
        "font",
        "font-family",
        "font-size",
        "font-style",
        "font-variant",
        "font-weight",
        "text-align",
        "text-decoration",
        "text-decoration-color",
        "text-transform",
        "text-shadow",
        "letter-spacing",
        "word-spacing",
        "line-height",
        "white-space",
        "vertical-align",
        "display",
        "width",
        "height",
        "min-width",
        "min-height",
        "max-width",
        "max-height",
        "margin",
        "padding",
        "border",
        "border-radius",
        "box-shadow",
        "opacity",
        "overflow",
    }
)

STYLE_PREFIXES = ("border-", "margin-", "padding-", "text-decoration-")

#: Classes a template may name. The page's own sensor styles are opt-in by name; a template
#: cannot reach an arbitrary rule in the stylesheet and re-draw the pane.
STYLE_CLASSES = frozenset(
    {
        "sensor-value",
        "sensor-unit",
        "sensor-good",
        "sensor-warn",
        "sensor-bad",
        "sensor-unknown",
        "sensor-mono",
    }
)

#: Anything in a `style` that loads, imports, or runs. `url()` is refused outright rather than
#: judged by scheme: a sensor's colour is not a thing that needs to fetch an image.
_UNSAFE_STYLE = re.compile(
    r"(url\s*\(|expression\s*\(|@import|behavior\s*:|javascript:|vbscript:)",
    re.IGNORECASE,
)

_env: SandboxedEnvironment | None = None


def environment() -> SandboxedEnvironment:
    """The one Jinja environment, built once.

    Sandboxed, undefined-is-an-error, and **without a loader**: `{% include %}` and
    `{% extends %}` need a loader, and giving them none means the only template a sensor can
    run is the one written in the box. `autoescape` is off because these templates produce
    values rather than pages — the styling step is made safe by :func:`sanitise`, which is a
    rule about what markup may exist, not a rule about escaping it.
    """
    global _env
    if _env is None:
        _env = SandboxedEnvironment(undefined=StrictUndefined, autoescape=False, auto_reload=False)
    return _env


@dataclass(frozen=True)
class Presentation:
    """What a sensor shows, and what went wrong on the way to showing it."""

    #: Exactly what arrived, as text. Never empty by accident: if a template fails, this is
    #: still the reading, and a person can see the number they are waiting for.
    raw: str
    #: The value the extraction template picked out of it (or the raw text when there is none).
    extracted: str
    #: The extracted value in words (or the extracted value when there is no display template).
    display: str
    #: The display text dressed by the styling template, sanitised. Plain-escaped text when
    #: there is no styling template.
    styled: str
    #: Why a template could not be used, shown on the box beside the value. "" when all three ran.
    problem: str
    #: Where the dressing came from: a styling template, the colour rules, or neither. Recorded
    #: rather than guessed at by the page, because the two textual choices are alternatives and a
    #: person looking at a red number is entitled to know which of them made it red.
    dressed_by: str = "none"
    #: The value on its owner's scale, when the box has one and the reading is a number.
    numeric: dict[str, object] | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "raw": self.raw,
            "extracted": self.extracted,
            "display": self.display,
            "styled": self.styled,
            "problem": self.problem,
            "dressed_by": self.dressed_by,
            "numeric": self.numeric,
        }


def context_for(raw: str, *, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """What a template may ask for.

    `value` is the current step's input (the raw text for extraction, the extracted value for
    display, the finished words for styling), and the earlier steps are named too, so a
    template never has to re-derive something the pipeline already worked out.

    `data` is the payload parsed as JSON when it is JSON, which most brokers are sent. It is
    `None` otherwise rather than an empty dict, because `{{ data.pressure }}` on a plain-text
    payload should say *there is no data here*, not silently render nothing.
    """
    text = raw if isinstance(raw, str) else "" if raw is None else str(raw)
    try:
        # `42`, `true` and `"a string"` are valid JSON, and a payload of `42` is a real thing a
        # sensor publishes: the scalar itself is the data. Anything that is not JSON is None, so
        # `{{ data.pressure }}` on plain text says *there is no data here*.
        data: Any = json.loads(text)
    except (ValueError, TypeError):
        data = None
    number = as_number(text)
    context: dict[str, Any] = {
        "value": text,
        "raw": text,
        "data": data,
        "number": number,
    }
    if extra:
        context.update(extra)
    return context


def as_number(text: str) -> float | None:
    """The number in this text, or None. Trailing units are ignored: `1.42 bar` is 1.42."""
    match = re.search(r"-?\d+(?:[.,]\d+)?", text or "")
    if match is None:
        return None
    return float(match.group(0).replace(",", "."))


def render_template(source: str, context: Mapping[str, Any]) -> str:
    """Run one template. Raises :class:`TemplateError` or :class:`ValueError` on failure.

    Split out so the write-time check (`is this template even compilable?`) and the read-time
    render share the same rules about size and environment.
    """
    if len(source) > MAX_TEMPLATE_CHARS:
        raise ValueError(
            f"a template longer than {MAX_TEMPLATE_CHARS} characters is not a template"
        )
    template = environment().from_string(source)
    return str(template.render(dict(context)))


def check_config(config: Mapping[str, Any]) -> str:
    """Every rule a sensor box's config has to satisfy, in one sentence or none.

    One function so a box cannot be saved with half its sensor rules checked: templates that
    compile, a scale that covers its range, colour rules whose patterns run.
    """
    for problem in (check_templates(config), check_numeric(config), check_text_colours(config)):
        if problem:
            return problem
    return ""


def check_templates(config: Mapping[str, Any]) -> str:
    """Compilable? "" when every template the box carries would run, else the reason.

    Called before a box is saved. A syntax error caught here is a sentence in a form; the same
    error found while rendering means a box that shows an explanation instead of a value, which
    is the right fallback but the wrong way to discover a typo.
    """
    for key in TEMPLATES:
        source = config.get(key)
        if source is None:
            continue
        if not isinstance(source, str):
            return f"{key} must be text"
        try:
            if len(source) > MAX_TEMPLATE_CHARS:
                raise ValueError(
                    f"a template longer than {MAX_TEMPLATE_CHARS} characters is not a template"
                )
            environment().compile(source)
        except TemplateError as exc:
            return f"{key}: {_plain(exc)}"
        except ValueError as exc:
            return f"{key}: {exc}"
    return ""


def present(box: Box, raw: object, *, extra: Mapping[str, Any] | None = None) -> Presentation:
    """Turn what arrived into what is shown, following the box's own templates.

    Never raises. Each step falls back to the previous step's text and records one sentence
    about why, because a sensor box that renders nothing is indistinguishable from a sensor
    that heard nothing.
    """
    text = raw if isinstance(raw, str) else "" if raw is None else str(raw)
    problems: list[str] = []
    extracted = text
    display = text

    extract = _template_of(box, "extract_template")
    if extract:
        try:
            extracted = _clean_output(render_template(extract, context_for(text, extra=extra)))
            # What the box shows is what the sensor said the reading *is*, until a display
            # template words it differently. Leaving the display on the raw payload here would
            # mean an extraction template changed the tooltip but not the number.
            display = extracted
        except Exception as exc:
            # Not a list of exception types: `{{ 1 / 0 }}` raises ZeroDivisionError, `{% include %}`
            # raises TypeError because this environment has no loader, and a filter a template names
            # that Jinja does not have raises something else again. The rule cannot be "catch what
            # we predicted" — it is "a box's own template never takes the reading off the screen".
            problems.append(f"extraction: {_plain(exc)}")

    display_template = _template_of(box, "display_template")
    if display_template:
        try:
            shown = render_template(
                display_template,
                {**context_for(text, extra=extra), "value": extracted, "extracted": extracted},
            )
            display = _clean_output(shown)
        except Exception as exc:
            problems.append(f"display: {_plain(exc)}")

    style = _template_of(box, "style_template")
    if style:
        try:
            dressed = render_template(
                style,
                {
                    **context_for(text, extra=extra),
                    "value": extracted,
                    "extracted": extracted,
                    "display": display,
                },
            )
            styled = sanitise(dressed)
        except Exception as exc:
            problems.append(f"styling: {_plain(exc)}")
            styled = escape(display)
    else:
        styled = escape(display)

    # Two ways to colour a textual value, and the owner picks one; a styling template is the more
    # explicit instruction, so it wins and the colour rules stay unused rather than being painted
    # underneath it.
    dressed_by = "none"
    if style:
        dressed_by = "template"
    else:
        colours = text_style(box, display)
        if colours:
            styled = f'<span style="{escape(colours)}">{escape(display)}</span>'
            dressed_by = "colours"

    return Presentation(
        raw=text,
        extracted=extracted,
        display=display,
        styled=styled,
        problem="; ".join(problems),
        dressed_by=dressed_by,
        numeric=numeric(box, as_number(extracted)),
    )


def _template_of(box: Box, key: str) -> str:
    value = box.config.get(key)
    return value if isinstance(value, str) and value.strip() else ""


def _clean_output(text: str) -> str:
    """One line of text, without the whitespace a template tends to leave around it."""
    return re.sub(r"\s+", " ", text).strip()


def _plain(exc: BaseException) -> str:
    """A Jinja complaint as a sentence a person can act on."""
    line = getattr(exc, "lineno", None)
    message = str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__
    where = f" (line {line})" if line else ""
    return f"{message}{where}"


class _Sanitiser(HTMLParser):
    """Rebuild a fragment out of only the parts a styling template may ask for.

    Unknown tags lose the tag and keep their contents, so a typo like `<blod>` still shows the
    word. Tags on `DROP_TAGS` lose their contents too: a `<script>` left as text is noise, and
    a `<style>` element can reach outside the pane.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._dropping = 0
        #: Tags we opened and may close, so a template that forgets `</b>` cannot leave the
        #: rest of the page bold.
        self._open: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.lower()
        if name in DROP_TAGS:
            self._dropping += 1
            return
        if name not in FORMAT_TAGS:
            return
        rendered = _attributes(name, attrs)
        self.out.append(f"<{name}{rendered}>")
        if name not in ("br", "hr"):
            self._open.append(name)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.lower()
        if name in DROP_TAGS or name not in FORMAT_TAGS:
            return
        self.out.append(f"<{name}{_attributes(name, attrs)}/>")

    def handle_endtag(self, tag: str) -> None:
        name = tag.lower()
        if name in DROP_TAGS:
            self._dropping = max(0, self._dropping - 1)
            return
        if name not in FORMAT_TAGS or name not in self._open:
            return
        # close whatever this ends, and anything it left hanging inside
        while self._open:
            opened = self._open.pop()
            self.out.append(f"</{opened}>")
            if opened == name:
                break

    def handle_data(self, data: str) -> None:
        if self._dropping:
            return
        self.out.append(escape(data))

    def handle_comment(self, data: str) -> None:
        return

    def handle_decl(self, decl: str) -> None:
        return

    def unknown_decl(self, data: str) -> None:
        return

    def finish(self) -> str:
        while self._open:
            self.out.append(f"</{self._open.pop()}>")
        return "".join(self.out)


def sanitise(fragment: str) -> str:
    """The styling a template asked for, with everything else taken out."""
    parser = _Sanitiser()
    parser.feed(fragment or "")
    parser.close()
    return parser.finish()


def _attributes(name: str, attrs: list[tuple[str, str | None]]) -> str:
    parts: list[str] = []
    for raw_name, raw_value in attrs:
        key = raw_name.lower()
        value = raw_value or ""
        if key == "style":
            clean = clean_style(value)
            if clean:
                parts.append(f' style="{escape(clean)}"')
        elif key == "class":
            kept = [word for word in value.split() if word in STYLE_CLASSES]
            if kept:
                parts.append(f' class="{escape(" ".join(kept))}"')
        elif key == "title":
            # The one free-text attribute: a tooltip explaining why something is red is part of
            # what a sensor says, and it carries no executable anything.
            if value.strip():
                parts.append(f' title="{escape(value.strip()[:200])}"')
        elif key == "color" and name == "font":
            colour = _safe_colour_word(value)
            if colour:
                parts.append(f' color="{escape(colour)}"')
        # everything else — `onclick`, `src`, `href`, `data-*`, `id` — is not copied out
    return "".join(parts)


def clean_style(value: str) -> str:
    """The declarations in this `style` attribute that are allowed to exist.

    A property is kept only if it is on the list *and* its value carries nothing that loads,
    imports, or runs. Unknown properties are dropped rather than passed through, because the
    list is short on purpose: `position: fixed` is a property, and it is how one sensor's value
    ends up painted over the whole page.
    """
    kept: list[str] = []
    for declaration in value.split(";"):
        name, sep, body = declaration.partition(":")
        if not sep:
            continue
        property_name = name.strip().lower()
        property_value = body.strip()
        if not property_value or _UNSAFE_STYLE.search(property_value):
            continue
        if not _property_allowed(property_name):
            continue
        if property_name in ("background", "background-image") and "url" in property_value.lower():
            continue
        if property_name == "font-family":
            property_value = _safe_font_family(property_value)
        if property_name in _MEASURE_PROPERTIES and not _is_measure(property_value):
            continue
        if _looks_like_address(property_value) and not safe_url(property_value):
            continue
        kept.append(f"{property_name}: {property_value}")
    return "; ".join(kept)


_MEASURE_PROPERTIES = frozenset(
    {"width", "height", "min-width", "min-height", "max-width", "max-height"}
)


def _property_allowed(name: str) -> bool:
    return name in STYLE_PROPERTIES or any(name.startswith(prefix) for prefix in STYLE_PREFIXES)


def _is_measure(value: str) -> bool:
    return bool(re.fullmatch(r"\d+(\.\d+)?(%|px|em|rem|ch|ex|pt|vw|vh)?", value))


def _looks_like_address(value: str) -> bool:
    return bool(re.match(r"^\s*[a-z][a-z0-9+.-]*:", value, re.IGNORECASE))


def _safe_font_family(value: str) -> str:
    """Names and quoted names, commas and nothing else. `font-family: x; position: fixed` is
    the classic way out of an attribute, and it stops being one here."""
    parts = [part.strip().strip("'\"") for part in value.split(",")]
    kept = [part for part in parts if re.fullmatch(r"[A-Za-z0-9 _-]{1,40}", part)]
    return ", ".join(kept)


def _safe_colour_word(value: str) -> str:
    if not re.fullmatch(r"#?[0-9a-zA-Z(),.%\s-]{1,40}", value or ""):
        return ""
    return "" if _UNSAFE_STYLE.search(value or "") else value


def style_for_colour(colour: str) -> str:
    """A `style` attribute worth writing for a colour a person picked.

    Used by the numeric and regex colour paths, which are not templates: the colour came from a
    picker or a name, and this says what shape it has to be to reach the page.
    """
    value = (colour or "").strip()
    if (
        re.fullmatch(r"#[0-9a-fA-F]{3,8}", value)
        or re.fullmatch(r"[a-zA-Z]{3,20}", value)
        or value == "inherit"
    ):
        return f"color: {value}"
    return ""


# --------------------------------------------------------------------------- the owner's scale

#: Sixteen colours, in the order a scale reads: cool through hot, then the neutrals a sensor also
#: wants (a grey "unknown" and a black "off"). Gauges, bars and level indicators have converged on
#: roughly this set, so a person picking from it is picking something they have seen before — and
#: the picker is still there for the colour that is not on the list.
PALETTE: tuple[tuple[str, str], ...] = (
    ("green", "#2e8b57"),
    ("lime", "#7fbf3f"),
    ("yellow", "#f2c200"),
    ("orange", "#f08a24"),
    ("red", "#d0021b"),
    ("maroon", "#8b1a1a"),
    ("purple", "#7d3c98"),
    ("magenta", "#c0397b"),
    ("pink", "#e58fb1"),
    ("blue", "#1f5fbf"),
    ("navy", "#1b3a6b"),
    ("cyan", "#2aa8b8"),
    ("teal", "#0f7a72"),
    ("brown", "#8b5a2b"),
    ("grey", "#7f8c8d"),
    ("black", "#111111"),
)

#: The four pictures a numeric sensor may be drawn as. Named for what they look like, because they
#: are what the settings call them.
REPRESENTATIONS = ("gauge", "hbar", "vbar", "progress")

_COLOUR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$|^[a-zA-Z]{3,20}$|^inherit$")

NUMERIC_KEY = "numeric"
TEXT_COLOURS_KEY = "text_colours"


def palette() -> list[dict[str, str]]:
    """The picker's presets, as data, so the page draws what this module says rather than a
    second list that can disagree about what `green` is."""
    return [{"name": name, "colour": colour} for name, colour in PALETTE]


def _is_colour(value: object) -> bool:
    return isinstance(value, str) and bool(_COLOUR.match(value.strip()))


def check_numeric(config: Mapping[str, Any]) -> str:
    """Does this scale hold every value it claims to? "" when it does, else the reason.

    The coverage rule is the whole point of the check: the intervals must **fully cover** the
    range, so a reading between two stops cannot fall through to no colour at all. A gap is not a
    cosmetic mistake — half of a gauge silently never changes colour, and the person watching it
    has no way to know which half.
    """
    raw = config.get(NUMERIC_KEY)
    if raw is None:
        return ""
    if not isinstance(raw, dict):
        return "numeric must be an object with min, max, representation and intervals"
    scale = raw
    low, high = scale.get("min"), scale.get("max")
    if not _is_number(low) or not _is_number(high):
        return "numeric needs min and max as numbers"
    low_f, high_f = _as_float(low), _as_float(high)
    if low_f >= high_f:
        return "numeric: min must be below max"
    representation = scale.get("representation", "hbar")
    if representation not in REPRESENTATIONS:
        return f"numeric: representation must be one of {', '.join(REPRESENTATIONS)}"
    intervals = scale.get("intervals")
    if not isinstance(intervals, list) or not intervals:
        return "numeric needs at least one interval, or the range has no colour at all"
    stops: list[float] = []
    for position, item in enumerate(intervals, start=1):
        if not isinstance(item, dict):
            return f"numeric: interval {position} must be an object with upto and colour"
        if not _is_number(item.get("upto")):
            return f"numeric: interval {position} needs a number for upto"
        if not _is_colour(item.get("colour", "")):
            return f"numeric: interval {position} needs a colour (a name or #rrggbb)"
        stops.append(_as_float(item["upto"]))
    for index in range(1, len(stops)):
        if stops[index] <= stops[index - 1]:
            return f"numeric: interval {index + 1} must end above interval {index}"
    if stops[0] <= float(low):  # type: ignore[arg-type]
        return (
            f"numeric: the first colour must reach above {low}, or the bottom of the scale has none"
        )
    if stops[-1] < float(high):  # type: ignore[arg-type]
        return (
            f"numeric: the colours stop at {stops[-1]:g} but the range runs to {high:g} — "
            f"a reading of {(stops[-1] + float(high)) / 2:g} would have no colour"  # type: ignore[arg-type]
        )
    return ""


def check_text_colours(config: Mapping[str, Any]) -> str:
    """Are the textual colour rules usable? A pattern that does not compile is refused here."""
    raw = config.get(TEXT_COLOURS_KEY)
    if raw is None:
        return ""
    if not isinstance(raw, dict):
        return f"{TEXT_COLOURS_KEY} must be an object with rules and a default"
    rules = raw.get("rules", [])
    if not isinstance(rules, list):
        return f"{TEXT_COLOURS_KEY}: rules must be a list"
    for position, rule in enumerate(rules, start=1):
        if not isinstance(rule, dict):
            return f"{TEXT_COLOURS_KEY}: rule {position} must be an object with pattern and colours"
        pattern = rule.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            return f"{TEXT_COLOURS_KEY}: rule {position} needs a pattern to look for"
        try:
            re.compile(pattern)
        except re.error as exc:
            return f"{TEXT_COLOURS_KEY}: rule {position} is not a regular expression: {exc}"
        for key in ("colour", "background"):
            if key in rule and not _is_colour(rule[key]):
                return f"{TEXT_COLOURS_KEY}: rule {position} has no valid {key}"
    default = raw.get("default", {})
    if not isinstance(default, dict):
        return f"{TEXT_COLOURS_KEY}: default must be an object with colours"
    for key in ("colour", "background"):
        if key in default and not _is_colour(default[key]):
            return f"{TEXT_COLOURS_KEY}: default has no valid {key}"
    return ""


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _as_float(value: object) -> float:
    """A number that has already been checked by `_is_number`.

    The config came out of JSON and mypy cannot see the check that ran two lines earlier, so the
    one unchecked cast in this module lives here instead of being sprinkled over every use.
    """
    return float(value)  # type: ignore[arg-type]


def numeric(box: Box, number: float | None) -> dict[str, object] | None:
    """Where this number sits on the box's own scale, and what colour that is.

    A reading outside the range is **clamped, and says so**: the bar goes to its end (a gauge that
    drew 140% would be a bar that left the pane) and the box adds `above 10`. A value that quietly
    drew as full-scale is a sensor told people the wrong thing about a boiler.
    """
    scale = box.config.get(NUMERIC_KEY)
    if not isinstance(scale, dict) or number is None:
        return None
    low, high = scale.get("min"), scale.get("max")
    if not _is_number(low) or not _is_number(high):
        return None
    low_f, high_f = _as_float(low), _as_float(high)
    if low_f >= high_f:
        return None
    intervals = [item for item in scale.get("intervals", []) if isinstance(item, dict)]
    if not intervals:
        return None
    span = high_f - low_f
    clamped = "low" if number < low_f else "high" if number > high_f else None
    inside = min(max(number, low_f), high_f)
    position = (inside - low_f) / span
    chosen = intervals[-1]
    index = len(intervals) - 1
    for place, item in enumerate(intervals):
        upto = item.get("upto")
        if _is_number(upto) and number <= _as_float(upto):
            chosen, index = item, place
            break
    colour = str(chosen.get("colour", ""))
    return {
        "min": low,
        "max": high,
        "value": number,
        "position": round(position, 4),
        "colour": colour if _is_colour(colour) else "",
        "interval": index,
        "representation": str(scale.get("representation", "hbar")),
        "clamped": clamped or "",
        "clamp_text": ""
        if clamped is None
        else (f"below {low_f:g}" if clamped == "low" else f"above {high_f:g}"),
        "label": str(chosen.get("label", "") or ""),
    }


def text_style(box: Box, display: str) -> str:
    """The colours for a textual value: the first rule whose pattern matches, else the default.

    First match wins, and that is the rule the page has to say out loud — two patterns can both
    match `2.4 bar`, and a list whose order did not matter would let a person reorder rules and
    change nothing, which is not how they will read it.
    """
    raw = box.config.get(TEXT_COLOURS_KEY)
    if not isinstance(raw, dict):
        return ""
    rules = raw.get("rules", [])
    for rule in (
        [item for item in rules if isinstance(item, dict)] if isinstance(rules, list) else []
    ):
        pattern = rule.get("pattern")
        if not isinstance(pattern, str):
            continue
        try:
            if re.search(pattern, display):
                return _style_pair(rule)
        except re.error:
            continue
    default = raw.get("default")
    return _style_pair(default) if isinstance(default, dict) else ""


def _style_pair(rule: Mapping[str, Any]) -> str:
    parts = []
    colour = rule.get("colour")
    background = rule.get("background")
    if _is_colour(colour):
        parts.append(f"color: {str(colour).strip()}")
    if _is_colour(background):
        parts.append(f"background-color: {str(background).strip()}")
    return "; ".join(parts)
