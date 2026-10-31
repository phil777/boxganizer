"""Tags: names a box carries, arranged in a hierarchy.

A tag answers a question the type cannot. `markdown` says what a box *is*; `boiler` says what
it is *about*, and a box is about several things at once. Everything else in this module follows
from that: tags are many, they are the person's own words, and they mean nothing to the box.

Three decisions are worth the reason rather than only the rule.

**A tag's name is its identity, and it is unique across the whole library.** A hierarchy makes
the same word under two parents look like two tags and read like one: a chip that says `a`, a
filter that says `tag:boiler`, and a manage window that has to ask *which* one. Paths are the
convenient way to say "boiler, under kitchen", so they are how tags are *written* — but they are
notation over unique names, not the identity itself. This is what lets `box ls --tag boiler` and
`tag:boiler` in a folder filter be unambiguous without either of them carrying a parent.

**A box tagged with a child is tagged with its parents too, and that is a fact rather than a
copy.** The explicit set is what somebody chose; the implicit set is what that choice commits
them to. They are kept apart everywhere they are shown, because "3 tags, 2 of them inherited"
is worth knowing and "5 tags" is a number that cannot be acted on — un-tagging an inherited tag
means editing the hierarchy, not the box.

**Colour is derived, not chosen, and it is a function of the tag.** A palette picked per tag is
five minutes of work per tag and a library that looks like a bag of marbles; deriving it from the
tag's own root means everything under `home` is a shade of the same hue, so the hierarchy is
visible without being read, and the same tag is the same colour in the chip, the filter and the
manage window — computed in Python, once, and handed to whoever asks. Nothing is stored that a
rename could strand.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Self

#: The separator in a path, and the word for a name that contains it.
SEPARATOR = "/"

#: Names a tag may not take, because they mean something in the interfaces.
RESERVED_NAMES = frozenset({"", "*", "all", "none", "any"})

#: How many names a tag path may hold before it is a filing cabinet rather than a tag.
#: Six is generous and one more than anyone remembers; the point is that `a/b/c/d/e/f/g/h/i`
#: cannot be typed into a chip field and produce a tree nobody can read.
MAX_DEPTH = 6


class TagError(ValueError):
    """A tag cannot be made or moved as asked, for a reason worth reading."""


@dataclass(frozen=True)
class Tag:
    """One name, and the name above it."""

    name: str
    parent: str | None = None
    id: int | None = None
    #: An explicit colour a person chose, or None to take the derived one.
    colour: str | None = None

    @property
    def is_root(self) -> bool:
        return self.parent is None


@dataclass(frozen=True)
class Hierarchy:
    """Every tag in the library, with the answers a hierarchy is asked for.

    Built once from the rows and then queried, because the same walk comes up three ways in any
    screen: what is above this tag, what are its children, and where does this tag sit in the
    tree. Doing it in SQL each time would be three recursive queries per box drawn; doing it
    here is one pass over a list nobody keeps thousands of.
    """

    tags: tuple[Tag, ...]
    _by_name: dict[str, Tag] = field(default_factory=dict, repr=False)

    @classmethod
    def build(cls, tags: Iterable[Tag]) -> Self:
        index: dict[str, Tag] = {}
        for tag in tags:
            index[canonical(tag.name)] = tag
        # A parent that no longer exists is not an error to show a person reading their own
        # tags: the row is treated as a root, and the manage window is where the hole gets
        # named. Deleting a tag re-parents its children, so this only happens if a database was
        # edited outside Boxganizer — which is allowed, and must not become a crash.
        healed: dict[str, Tag] = {}
        for key, tag in index.items():
            parent = tag.parent
            if parent is not None and canonical(parent) not in index:
                parent = None
            healed[key] = Tag(name=tag.name, parent=parent, id=tag.id, colour=tag.colour)
        return cls(tags=tuple(healed.values()), _by_name=healed)

    def __len__(self) -> int:
        return len(self.tags)

    def get(self, name: str) -> Tag | None:
        return self._by_name.get(canonical(name))

    def names(self) -> tuple[str, ...]:
        return tuple(tag.name for tag in sorted(self._by_name.values(), key=_sort_key))

    def children_of(self, name: str | None) -> tuple[Tag, ...]:
        wanted = None if name is None else canonical(name)
        return tuple(
            sorted(
                (tag for tag in self._by_name.values() if _parent_key(tag) == wanted),
                key=_sort_key,
            )
        )

    def ancestors(self, name: str) -> tuple[Tag, ...]:
        """The tags above this one, nearest first. Empty for a root tag.

        Cycle-safe by construction: a tag already walked is a stop, not a loop. A database
        written by hand can hold `a → b → a`, and a page that hung instead of rendering would
        be a worse outcome than a hierarchy with an honest edge cut.
        """
        out: list[Tag] = []
        seen: set[str] = set()
        current = self.get(name)
        if current is not None:
            seen.add(canonical(current.name))
        while current is not None and current.parent is not None:
            parent = self.get(current.parent)
            if parent is None or canonical(parent.name) in seen:
                break
            seen.add(canonical(parent.name))
            out.append(parent)
            current = parent
        return tuple(out)

    def implied(self, names: Iterable[str]) -> tuple[str, ...]:
        """Everything the given tags commit their holder to, the given tags first.

        This is the whole of "a box with a tag is implicitly tagged with the tag's parents": it
        is not stored, it is what the stored choice means.
        """
        ordered: list[str] = []
        seen: set[str] = set()
        for raw in names:
            tag = self.get(raw)
            if tag is None:
                # An unknown name is not nobody's business to invent here: it is kept as a
                # plain tag so a box never silently loses something its owner typed.
                tag = Tag(name=raw.strip())
            for candidate in (tag, *self.ancestors(tag.name)):
                key = canonical(candidate.name)
                if key not in seen:
                    seen.add(key)
                    ordered.append(candidate.name)
        return tuple(ordered)

    def path(self, name: str) -> tuple[str, ...]:
        """`boiler` under `home/kitchen` → `('home', 'kitchen', 'boiler')`."""
        tag = self.get(name)
        if tag is None:
            return (name,)
        return (*[ancestor.name for ancestor in reversed(self.ancestors(tag.name))], tag.name)

    def depth(self, name: str) -> int:
        return len(self.path(name)) - 1

    def root_of(self, name: str) -> str:
        """The tag at the top of this one, which is what a colour is keyed on."""
        chain = self.ancestors(name)
        return chain[-1].name if chain else (self.get(name) or Tag(name=name)).name

    def as_json(
        self,
        counts: Mapping[str, int] | None = None,
        inherited_counts: Mapping[str, int] | None = None,
    ) -> list[dict[str, object]]:
        """Every tag with its path, colour and counts — what the page and `--json` both draw."""
        out: list[dict[str, object]] = []
        for tag in sorted(self._by_name.values(), key=_sort_key):
            colour = colour_for(self, tag.name)
            key = canonical(tag.name)
            out.append(
                {
                    "name": tag.name,
                    "parent": tag.parent,
                    "path": list(self.path(tag.name)),
                    "depth": self.depth(tag.name),
                    "colour": colour,
                    "text": text_colour_for(colour),
                    "explicit_colour": tag.colour,
                    "count": (counts or {}).get(key, 0),
                    "includingBelow": (inherited_counts or {}).get(key, 0),
                }
            )
        return out

    def descendants_of(self, name: str) -> tuple[str, ...]:
        """Every tag below this one, nearest first — what deleting it will re-parent."""
        out: list[str] = []
        frontier = [child.name for child in self.children_of(name)]
        seen: set[str] = set()
        while frontier:
            current = frontier.pop(0)
            if canonical(current) in seen:
                continue
            seen.add(canonical(current))
            out.append(current)
            frontier.extend(child.name for child in self.children_of(current))
        return tuple(out)

    def would_cycle(self, name: str, proposed_parent: str | None) -> bool:
        """Whether moving `name` under `proposed_parent` would make a loop.

        Asked before the write, because a cycle in a hierarchy is not a shape that can be
        displayed: the manage window renders a tree, and a tree with a cycle renders forever.
        """
        if proposed_parent is None:
            return False
        wanted = canonical(name)
        if canonical(proposed_parent) == wanted:
            return True
        # A cycle is made exactly when the tag being moved is already *above* where it is
        # going: putting `Home` under `Boiler` means Boiler is inside Home is inside Boiler.
        # Asking the other question — is the destination above the tag — answers False for
        # every real cycle, which is how this first shipped and what the test now pins.
        return any(
            canonical(ancestor.name) == wanted for ancestor in self.ancestors(proposed_parent)
        )


# -- names ---------------------------------------------------------------------


def canonical(name: str) -> str:
    """The key two tag names are compared on.

    Case and surrounding space are the person's to keep and nobody else's to distinguish: `Home`
    and `home` in a list of chips is one tag that looks like two, and the difference between them
    is never the thing somebody meant.
    """
    return " ".join(name.strip().split()).casefold()


def split_path(text: str) -> tuple[str, ...]:
    """`home / kitchen / boiler` → the parts, trimmed, with empty parts dropped.

    Spaces around the separator are allowed and removed. Somebody typing a path into a field will
    type them, and `Kitchen / Boilers` becoming a tag literally named " Boilers" is the kind of
    surprise that ends with two chips that look identical.
    """
    return tuple(part.strip() for part in text.split(SEPARATOR) if part.strip())


def validate(name: str) -> str:
    """The name, cleaned, or a refusal that says what is wrong with it."""
    cleaned = " ".join(name.strip().split())
    if not cleaned:
        raise TagError("a tag needs a name")
    if SEPARATOR in cleaned:
        raise TagError(
            f"'{cleaned}' looks like a path; a tag is one word, and the path is how it is written"
        )
    if canonical(cleaned) in {canonical(word) for word in RESERVED_NAMES}:
        raise TagError(f"'{cleaned}' means something in the filters and cannot be a tag")
    if len(cleaned) > 48:
        raise TagError(f"'{cleaned[:24]}…' is too long for a chip; 48 characters is the limit")
    for forbidden in (",", '"'):
        if forbidden in cleaned:
            raise TagError(f"a tag may not contain {forbidden!r}: the interfaces separate them")
    return cleaned


def validate_colour(colour: str) -> str:
    """The colour, if a browser and this module both understand it; otherwise a refusal.

    Both have to agree: the chip's text colour is derived from the background, so a value that
    the browser would drop and this module cannot parse would silently produce white on white.
    """
    cleaned = (colour or "").strip()
    if _parse_colour(cleaned) is None:
        raise TagError(f"{colour!r} is not a colour; use #rrggbb or hsl(h s l)")
    return cleaned


# -- colour --------------------------------------------------------------------

#: Hue, saturation and lightness of the top-level tags' colours, and how much lighter each
#: level below gets. Derived rather than picked, so a library with forty tags still looks like
#: one thing, and so every place a tag appears agrees on its colour without being told.
_SATURATION = 62
_LIGHTNESS = 41
_LEVEL_STEP = 9


def hue_for(name: str) -> int:
    """A stable hue for a tag's root: the same name is the same colour on every machine.

    `hash()` would do this in fewer lines and change between runs and versions, which would make
    a tag change colour between two openings of the same page — so it is a digest, which is what
    "stable" has to mean for a thing people will come to recognise.
    """
    digest = hashlib.blake2b(canonical(name).encode(), digest_size=2).digest()
    return int.from_bytes(digest, "big") % 360


def colour_for(hierarchy: Hierarchy, name: str) -> str:
    """The chip colour: the root's hue, lighter as it goes down.

    An explicit colour on the tag wins, and its descendants still take the derived shade of that
    colour's hue, so a chosen colour propagates down the branch it belongs to.
    """
    root = hierarchy.root_of(name)
    chosen = (hierarchy.get(root) or Tag(name=root)).colour
    depth = hierarchy.depth(name)
    if chosen:
        base = _parse_colour(chosen)
        if base is not None:
            hue, saturation, lightness = base
            return f"hsl({hue} {saturation}% {min(lightness + depth * _LEVEL_STEP, 78)})"
    return f"hsl({hue_for(root)} {_SATURATION}% {min(_LIGHTNESS + depth * _LEVEL_STEP, 78)})"


def text_colour_for(background: str) -> str:
    """Black or white, whichever is readable on the chip's colour.

    Every colour in this file is derived, so this could have been one constant. It is not,
    because an explicit colour is the person's, and a tag someone chose as pale yellow with
    white text is a tag they cannot read.
    """
    parsed = _parse_colour(background)
    if parsed is None:
        return "#ffffff"
    return "#111111" if _luma(*parsed) > 150 else "#ffffff"


def _luma(hue: int, saturation: int, lightness: int) -> int:
    """How bright a colour *looks*, 0..255 — not its HSL lightness.

    The two disagree in exactly the place it matters: pure yellow is lightness 50 and is the
    most legible colour there is, and white text on it is unreadable. The Rec.601 weights are
    what a chip needs, since a chip's job is to be read.
    """
    red, green, blue = _rgb_of(hue, saturation, lightness)
    return round((red * 299 + green * 587 + blue * 114) / 1000)


def _rgb_of(hue: int, saturation: int, lightness: int) -> tuple[int, int, int]:
    """HSL back to 0..255 channels, for the one place that needs them."""
    sat, lit = saturation / 100, lightness / 100
    chroma = (1 - abs(2 * lit - 1)) * sat
    sector = (hue % 360) / 60
    inner = chroma * (1 - abs(sector % 2 - 1))
    r, g, b = {
        0: (chroma, inner, 0),
        1: (inner, chroma, 0),
        2: (0, chroma, inner),
        3: (0, inner, chroma),
        4: (inner, 0, chroma),
        5: (chroma, 0, inner),
    }[min(int(sector), 5)]
    shift = lit - chroma / 2
    return (round((r + shift) * 255), round((g + shift) * 255), round((b + shift) * 255))


def _parse_colour(text: str) -> tuple[int, int, int] | None:
    """(hue, saturation, lightness) from `hsl(...)` or `#rrggbb`, or None if it is neither.

    Only what this module can produce and a colour input produces. Anything else falls back to
    the derived colour rather than being passed to the browser to fail on — a chip that does not
    render is worse than a chip that is not the shade somebody meant.
    """
    value = text.strip().lower()
    if value.startswith("hsl"):
        parts = value.removeprefix("hsl").strip("()").replace(",", " ").split()
        try:
            hue = int(float(parts[0])) % 360
            saturation = int(float(parts[1].rstrip("%")))
            lightness = int(float(parts[2].rstrip("%")))
        except (IndexError, ValueError):
            return None
        return hue, max(0, min(saturation, 100)), max(0, min(lightness, 100))
    if value.startswith("#") and len(value) in (4, 7):
        digits = value[1:]
        if len(digits) == 3:
            digits = "".join(digit * 2 for digit in digits)
        try:
            red, green, blue = (int(digits[i * 2 : i * 2 + 2], 16) for i in range(3))
        except ValueError:
            return None
        return _hls_of(red, green, blue)
    return None


def _hls_of(red: int, green: int, blue: int) -> tuple[int, int, int]:
    """The hue of a hex colour, expressed the way the derived colours are.

    The textbook conversion, written out because the compact versions differ in how they treat
    greys and in whether lightness is 0..1 or 0..100, and a wrong one of those makes a chosen
    colour come out visibly different from the one the person picked.
    """
    r, g, b = (channel / 255 for channel in (red, green, blue))
    high, low = max(r, g, b), min(r, g, b)
    lightness = (high + low) / 2
    span = high - low
    if span == 0:
        return 0, 0, round(lightness * 100)
    saturation = span / (2 - high - low) if lightness > 0.5 else span / (high + low)
    # Every channel below is 0..1. Mixing the scaled and the raw forms is a silent bug of the
    # exact kind this function exists to avoid: single-channel colours (pure red, pure green)
    # still come out right, and yellow turns into cyan.
    if high == r:
        hue = (g - b) / span + (6 if g < b else 0)
    elif high == g:
        hue = (b - r) / span + 2
    else:
        hue = (r - g) / span + 4
    return round(hue / 6 * 360) % 360, round(saturation * 100), round(lightness * 100)


def _parent_key(tag: Tag) -> str | None:
    return None if tag.parent is None else canonical(tag.parent)


def _sort_key(tag: Tag) -> tuple[str, str]:
    return (canonical(tag.parent or ""), canonical(tag.name))


def render_tree(hierarchy: Hierarchy, *, counts: Mapping[str, int] | None = None) -> str:
    """The hierarchy as text, for `box tags` and for a terminal that has no tree control."""
    lines: list[str] = []

    def walk(name: str | None, indent: int) -> None:
        for tag in hierarchy.children_of(name):
            held = ""
            if counts is not None:
                amount = counts.get(canonical(tag.name), 0)
                held = f"  ({amount})"
            lines.append(f"{'  ' * indent}{tag.name}{held}")
            walk(tag.name, indent + 1)

    walk(None, 0)
    return "\n".join(lines)
