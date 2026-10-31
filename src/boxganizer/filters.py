"""The filter a dynamic folder is built from.

A dynamic folder holds whatever a filter selects out of all the boxes in the
library. The filter lives in the box's own `config` JSON under `filter`, so it
travels with the box through `box edit`, the API and a copy of the database:

    box add "All PDFs" -t dynamic-folder        # then `box edit "All PDFs"`, and
    #   config:                                 # in the YAML that opens:
    #     filter:
    #       type: resource
    #       name: report

The page writes the same shape from the filter bar above the folder's list.

Every clause present must match; a clause that is absent asks nothing. That makes
the clauses AND, which is what a narrowing search feels like, and it keeps one
obvious reading: an empty filter asks for everything, which is what a cabinet is.

The module knows nothing about the database. :func:`parse` turns stored JSON into
a :class:`Filter` and :meth:`Filter.matches` answers for one box, so the same code
decides membership when a box is created, when a filter is edited, and when the
page shows why a box is where it is.

A filter that cannot be read is not treated as an empty one. Empty means "all the
boxes", and a typo should not quietly dump the whole library into a folder: it
selects nothing, and carries the reason to say out loud.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from boxganizer import boxtypes
from boxganizer.models import Box

#: The key the filter is stored under in a box's config.
FILTER_KEY = "filter"

#: Clauses a filter may hold. Anything else in the mapping is a mistake to report.
CLAUSES = ("name", "type", "resource", "metadata")

#: The one mapping key that holds an expression instead of the old fixed clauses.
EXPRESSION_KEY = "expression"

#: Fields an expression may ask about. `metadata:` is deliberately absent: it was never
#: reachable from the page, and every clause here is one the filter bar can offer.
FIELDS = ("type", "name", "tag", "content", "resource")

#: Words that mean something between clauses and so cannot be a bare value.
KEYWORDS = ("and", "or", "not")


# -- what a clause needs from outside this module ------------------------------------


@dataclass(frozen=True)
class Lookup:
    """What a clause asks about that a box does not carry in itself.

    A tag's meaning lives in the tag tree — asking about `kitchen` means the boxes tagged
    `boiler` below it too — and a file's words live in the search index. Neither belongs in
    this module, which knows boxes and nothing else, so the caller resolves those two into
    sets of uuids once and hands them in. A clause with no lookup answered is a clause that
    admits nothing: an unresolvable `tag:urgent` must select nothing, never everything, for
    the same reason a typo does.
    """

    #: Tag name (canonical) -> uuids of boxes carrying it or anything below it.
    tags: Mapping[str, frozenset[str]] = field(default_factory=dict)
    #: Content query -> uuids of boxes whose indexed words answer it.
    content: Mapping[str, frozenset[str]] = field(default_factory=dict)

    def tagged(self, name: str) -> frozenset[str]:
        return self.tags.get(name.casefold(), frozenset())

    def containing(self, query: str) -> frozenset[str]:
        return self.content.get(query.casefold(), frozenset())


# -- the clause tree -------------------------------------------------------------------


class Node:
    """One node of a filter's tree.

    Small on purpose: four shapes cover and, or, not and a clause, and each answers the same
    two questions — does this box satisfy me, and what must the caller go and find out first.
    """

    def holds(self, box: Box, lookup: Lookup) -> bool:
        raise NotImplementedError

    def asks(self, into: Needs) -> Needs:
        """Gather into `into` what this subtree will need resolved by the caller."""
        return into

    def words(self) -> str:
        raise NotImplementedError

    def text(self) -> str:
        """The expression that would rebuild this node, exactly."""
        raise NotImplementedError


@dataclass(frozen=True)
class Needs:
    """What a whole tree needs, gathered in one walk so the caller fetches each once."""

    tags: tuple[str, ...] = ()
    content: tuple[str, ...] = ()

    def any(self) -> bool:
        return bool(self.tags or self.content)


@dataclass(frozen=True)
class Term(Node):
    """One field asked about one value, as plain text or as a regular expression."""

    field_name: str
    value: str
    regex: re.Pattern[str] | None = None
    #: How the value was written, kept so the expression round-trips byte for byte.
    quoted: bool = False

    @property
    def mode(self) -> str:
        return "regex" if self.regex is not None else "text"

    def holds(self, box: Box, lookup: Lookup) -> bool:
        if self.field_name == "name":
            return _match_text(box.name, self.value, self.regex)
        if self.field_name == "type":
            if self.regex is not None:
                return self.regex.search(box.box_type) is not None
            return _type_matches(box.box_type, self.value)
        if self.field_name == "resource":
            return _match_text(box.resource_uri or "", self.value, self.regex)
        if self.field_name == "tag":
            # The tag tree decides this, not the box: a box tagged `boiler` answers to the
            # `kitchen` it sits under. `Lookup` is where that answer was computed.
            return box.uuid in lookup.tagged(self.value)
        if self.field_name == "content":
            return box.uuid in lookup.containing(self.value)
        return False

    def asks(self, into: Needs) -> Needs:
        if self.field_name == "tag":
            return Needs(tags=(*into.tags, self.value), content=into.content)
        if self.field_name == "content":
            return Needs(tags=into.tags, content=(*into.content, self.value))
        return into

    def words(self) -> str:
        label = {
            "name": "name",
            "type": "type",
            "tag": "tag",
            "content": "words",
            "resource": "resource",
        }[self.field_name]
        if self.field_name == "content":
            return f"its indexed words contain {self.value!r}"
        if self.field_name == "tag":
            return f"tagged {self.value!r} (or something below it)"
        if self.regex is not None:
            return f"{label} matches /{self.value}/"
        return f"{label} contains {self.value!r}"

    def text(self) -> str:
        value = self.value
        if self.regex is not None:
            body = f"/{value}/"
        elif self.quoted or " " in value or value.casefold() in KEYWORDS or ":" in value:
            body = f'"{value}"'
        else:
            body = value
        return f"{self.field_name}:{body}"


@dataclass(frozen=True)
class All(Node):
    parts: tuple[Node, ...] = ()

    def holds(self, box: Box, lookup: Lookup) -> bool:
        return all(part.holds(box, lookup) for part in self.parts)

    def asks(self, into: Needs) -> Needs:
        for part in self.parts:
            into = part.asks(into)
        return into

    def words(self) -> str:
        if not self.parts:
            return "every box"
        return " and ".join(
            f"({part.words()})" if isinstance(part, Alternative) else part.words()
            for part in self.parts
        )

    def text(self) -> str:
        return " and ".join(_wrap(part) for part in self.parts)


@dataclass(frozen=True)
class Alternative(Node):
    parts: tuple[Node, ...] = ()

    def holds(self, box: Box, lookup: Lookup) -> bool:
        return any(part.holds(box, lookup) for part in self.parts)

    def asks(self, into: Needs) -> Needs:
        for part in self.parts:
            into = part.asks(into)
        return into

    def words(self) -> str:
        return " or ".join(part.words() for part in self.parts)

    def text(self) -> str:
        return " or ".join(_wrap(part) for part in self.parts)


@dataclass(frozen=True)
class Negation(Node):
    part: Node

    def holds(self, box: Box, lookup: Lookup) -> bool:
        return not self.part.holds(box, lookup)

    def asks(self, into: Needs) -> Needs:
        return self.part.asks(into)

    def words(self) -> str:
        # `not a or b` reads as (`not a`) or b to an eye in a hurry, and `not type contains
        # 'note'` reads as a comparison against a field called "not type". The brackets are
        # cheap and the misreading costs a person their filter.
        return f"not ({self.part.words()})"

    def text(self) -> str:
        return f"not {_wrap(self.part)}"


def _wrap(node: Node) -> str:
    """Parenthesise a combination nested inside a combination of another kind."""
    if isinstance(node, Alternative | All | Negation):
        return f"({node.text()})"
    return node.text()


def _match_text(held: str, wanted: str, pattern: re.Pattern[str] | None) -> bool:
    if pattern is not None:
        return pattern.search(held) is not None
    return wanted in held.casefold()


# -- reading an expression -------------------------------------------------------------


class ExpressionError(ValueError):
    """An expression that cannot be read, with the place it stopped making sense."""


def parse_expression(raw: str) -> Node:
    """Turn one line of filter text into a tree.

    The grammar, loosest binding first::

        expr    := or
        or      := and { ('or' | '|') and }
        and     := unary { ('and' | '&')? unary }      # juxtaposition means AND
        unary   := ('not' | '!') unary | '(' expr ')' | clause
        clause  := [field ':'] value
        value   := '/' regex '/' | '"' text '"' | bare word

    A bare word is a *name*: `boiler` means what `name:boiler` means, because that is what a
    person typing one word into a search box is asking for. Juxtaposition ANDs for the same
    reason in reverse — `type:note urgent` is read as two conditions, and nobody writes an
    `and` between two things they mean together.
    """
    tokens = _tokenise(raw)
    if not tokens:
        return All(())
    node, rest = _parse_or(tokens)
    if rest:
        raise ExpressionError(f"did not expect {rest[0]!r} there")
    return node


_TOKEN = re.compile(
    r"""
    (?P<space>\s+)           # whitespace, dropped below rather than here
    | (?P<lparen>\()
    | (?P<rparen>\))
    | (?P<or>\|\||\||\bor\b)
    | (?P<and>&&|\band\b)
    | (?P<not>!|\bnot\b)
    | (?P<regex>/[^/\n]+/)                     # /plain regex/
    | (?P<quoted>"[^"]*"|'[^']*')               # "a phrase with spaces"
    | (?P<word>[^\s()|"']+)                    # bare, or field:value
    """,
    re.VERBOSE | re.IGNORECASE,
)


def _tokenise(raw: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    at = 0
    while at < len(raw):
        hit = _TOKEN.match(raw, at)
        if hit is None:
            raise ExpressionError(f"cannot read {raw[at : at + 12]!r}")
        at = hit.end()
        kind = hit.lastgroup or "word"
        text = hit.group(kind)
        out.append((kind, text))
    return [(kind, text) for kind, text in out if kind != "space"]


def _parse_or(tokens: list[tuple[str, str]]) -> tuple[Node, list[tuple[str, str]]]:
    parts: list[Node] = []
    left, tokens = _parse_and(tokens)
    parts.append(left)
    while tokens and tokens[0][0] == "or":
        right, tokens = _parse_and(tokens[1:])
        parts.append(right)
    return (parts[0] if len(parts) == 1 else Alternative(tuple(parts))), tokens


def _parse_and(tokens: list[tuple[str, str]]) -> tuple[Node, list[tuple[str, str]]]:
    parts: list[Node] = []
    left, tokens = _parse_unary(tokens)
    parts.append(left)
    while True:
        if tokens and tokens[0][0] == "and":
            right, tokens = _parse_unary(tokens[1:])
            parts.append(right)
            continue
        # juxtaposition: the next thing is another condition, and nobody wrote a word between
        if tokens and tokens[0][0] in ("lparen", "word", "quoted", "regex", "not"):
            right, tokens = _parse_unary(tokens)
            parts.append(right)
            continue
        break
    return (parts[0] if len(parts) == 1 else All(tuple(parts))), tokens


def _parse_unary(tokens: list[tuple[str, str]]) -> tuple[Node, list[tuple[str, str]]]:
    if tokens and tokens[0][0] == "not":
        inner, rest = _parse_unary(tokens[1:])
        return Negation(inner), rest
    if tokens and tokens[0][0] == "lparen":
        inner, rest = _parse_or(tokens[1:])
        if not rest or rest[0][0] != "rparen":
            raise ExpressionError("a ( was never closed")
        return inner, rest[1:]
    if not tokens:
        raise ExpressionError("the expression ended too early")
    kind, text = tokens[0]
    if kind in ("rparen", "and", "or"):
        raise ExpressionError(f"{text!r} is not a condition")
    # `name:"boiler fix"` arrives as two tokens, because a quote is a token of its own and a
    # field name is allowed to touch one. Joining them here is one rule in one place, rather
    # than a tokeniser pattern that has to know which words are field names.
    if (
        kind == "word"
        and text.endswith(":")
        and tokens[1:2]
        and tokens[1][0] in ("quoted", "regex")
    ):
        field_name = text[:-1].casefold()
        if field_name not in FIELDS:
            raise ExpressionError(f"no filter asks about {field_name!r}; try {', '.join(FIELDS)}")
        value_kind, value_text = tokens[1]
        literal = value_text[1:-1]
        return (
            Term(
                field_name,
                literal,
                _compile(literal) if value_kind == "regex" else None,
                quoted=value_kind == "quoted",
            ),
            tokens[2:],
        )
    return _parse_term(kind, text), tokens[1:]


def _parse_term(kind: str, text: str) -> Node:
    if kind == "regex":
        return Term("name", text[1:-1], re.compile(text[1:-1], re.IGNORECASE), quoted=False)
    if kind == "quoted":
        return Term("name", text[1:-1], None, quoted=True)
    field_name, _, rest = text.partition(":")
    if field_name.casefold() in FIELDS and ":" in text:
        return _term_for(field_name.casefold(), rest)
    if ":" in text:
        # `nam:` and friends: a field nobody knows is a mistake to name, not a value to
        # search for — searching for the literal would quietly return nothing forever.
        raise ExpressionError(f"no filter asks about {field_name!r}; try {', '.join(FIELDS)}")
    if text.casefold() in KEYWORDS:
        raise ExpressionError(f"{text!r} means something else here")
    return Term("name", text, None, quoted=False)


#: Fields that answer in words rather than in characters, and so cannot take a regex: the tag
#: tree is a hierarchy, not a haystack, and the search index is stemmed full-text, which cannot
#: honour `^` or `$` honestly. Refusing beats a clause that selects nothing forever.
TEXT_ONLY_FIELDS = ("tag", "content")


def _term_for(field_name: str, body: str) -> Node:
    if body.startswith("/") and body.endswith("/") and len(body) > 2:
        if field_name in TEXT_ONLY_FIELDS:
            raise ExpressionError(
                f"{field_name}: is a question about words, not characters — a regular "
                f"expression belongs on name, type or resource"
            )
        return Term(field_name, body[1:-1], _compile(body[1:-1]))
    if body.startswith("/") and len(body) > 1:
        # One slash and no closing one is a regex that lost its end. Reading it as the literal
        # text "/report$" would search for a slash nobody typed on purpose; asking for a slash
        # on purpose has its own spelling, which is the quote.
        quoted = f'{field_name}:"{body}"'
        raise ExpressionError(
            f"a regular expression is written /like this/, not {body!r} — to search for that "
            f"text as text, write {quoted}"
        )
    if body[:1] in {'"', "'"} and body[-1:] == body[:1] and len(body) > 1:
        return Term(field_name, body[1:-1], None, quoted=True)
    if body.casefold().startswith("re:") and len(body) > 3:
        if field_name in TEXT_ONLY_FIELDS:
            raise ExpressionError(
                f"{field_name}: is a question about words, not characters — a regular "
                f"expression belongs on name, type or resource"
            )
        rest = body[3:]
        return Term(field_name, rest, _compile(rest))
    if not body:
        raise ExpressionError(f"{field_name}: asks about nothing")
    return Term(field_name, body, None)


def _compile(pattern: str) -> re.Pattern[str]:
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        raise ExpressionError(f"that is not a regular expression: {exc}") from exc


def render_expression(node: Node) -> str:
    """The expression that would rebuild *node*."""
    return node.text() if not isinstance(node, All) or node.parts else ""


def needs_of(node: Node) -> Needs:
    """Every tag and content query a tree will ask about, gathered once."""
    return node.asks(Needs())


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


@dataclass(frozen=True)
class Filter:
    """What a dynamic folder selects, and whether it can be read at all."""

    #: Case-insensitive part of the name.
    name: str = ""
    #: A box type key: matches that type and every type below it in the hierarchy.
    type: str = ""
    #: Case-insensitive part of the resource URI.
    resource: str = ""
    #: Each key must be set on the box, and hold this value (case-insensitive).
    metadata: dict[str, str] = field(default_factory=dict)
    #: The parsed expression, when the filter was written as one. Nothing else reads this
    #: field except :meth:`matches`, which is why the flat clauses above stay as they are: a
    #: filter written the old way keeps its old shape, and an old reader of a database can
    #: still see what a clause asked.
    root: Node | None = None
    #: The expression as it was written, so the filter bar can show a person their own text.
    expression: str = ""
    #: Why this filter selects nothing because it could not be read, or "" when it can.
    broken: str = ""

    @property
    def usable(self) -> bool:
        return not self.broken

    @property
    def empty(self) -> bool:
        """A filter that asks nothing of a box, so every box answers."""
        if self.root is not None:
            return self.usable and isinstance(self.root, All) and not self.root.parts
        return self.usable and not (self.name or self.type or self.resource or self.metadata)

    @property
    def needs(self) -> Needs:
        """What the caller must resolve before :meth:`matches` can answer honestly."""
        return needs_of(self.root) if self.root is not None else Needs()

    @property
    def clauses(self) -> tuple[str, ...]:
        found = []
        if self.name:
            found.append("name")
        if self.type:
            found.append("type")
        if self.resource:
            found.append("resource")
        if self.metadata:
            found.append("metadata")
        return tuple(found)

    def matches(self, box: Box, lookup: Lookup | None = None) -> bool:
        """Whether *box* is a member. A filter that cannot be read admits nothing.

        `lookup` carries the answers about tags and about file contents, which this module
        cannot work out on its own; leave it out and those clauses admit nothing rather than
        pretending.
        """
        if self.broken:
            return False
        if self.root is not None:
            return self.root.holds(box, lookup or Lookup())
        if self.name and self.name not in box.name.casefold():
            return False
        if self.type and not _type_matches(box.box_type, self.type):
            return False
        if self.resource:
            held = (box.resource_uri or "").casefold()
            if self.resource not in held:
                return False
        for key, want in self.metadata.items():
            got = str(box.metadata.get(key, "")).casefold()
            if not want:
                # A key with no value asks only whether the box sets it at all.
                if key not in box.metadata:
                    return False
            elif want not in got:
                return False
        return True

    def describe(self) -> str:
        """One line saying what this selects, for a terminal or a heading."""
        if self.broken:
            return f"filter not understood: {self.broken}"
        if self.root is not None:
            return "every box" if self.empty else self.root.words()
        if self.empty:
            return "every box"
        parts = []
        if self.name:
            parts.append(f"name contains {self.name!r}")
        if self.type:
            parts.append(f"type is {self.type} or one below it")
        if self.resource:
            parts.append(f"resource contains {self.resource!r}")
        for key, want in sorted(self.metadata.items()):
            parts.append(f"metadata {key}={want!r}" if want else f"metadata {key} is set")
        return " and ".join(parts)

    def as_config(self) -> dict[str, Any]:
        """Back to stored JSON: only the clauses that ask something."""
        if self.root is not None:
            return {EXPRESSION_KEY: self.expression}
        saved: dict[str, Any] = {}
        if self.name:
            saved["name"] = self.name
        if self.type:
            saved["type"] = self.type
        if self.resource:
            saved["resource"] = self.resource
        if self.metadata:
            saved["metadata"] = dict(sorted(self.metadata.items()))
        return saved


def parse(raw: object) -> Filter:
    """Read a filter out of stored JSON.

    Anything unexpected is reported in :attr:`Filter.broken` rather than raised:
    a database can hold a box written by an older build or by hand, and the page
    has to say why the folder is empty instead of failing to open.
    """
    if raw is None:
        return Filter()
    if not isinstance(raw, Mapping):
        # A string used to mean "unreadable" and still does. Accepting one would quietly give a
        # different meaning to a value this version rejected: `filter: pdf` was a typo with a
        # message, and would silently become "name contains pdf" in somebody's library. The
        # expression belongs under its own key, which is what `as_config` writes back.
        if isinstance(raw, str):
            return Filter(
                broken=(f"expected an object holding {EXPRESSION_KEY!r}, found bare text {raw!r}")
            )
        return Filter(broken=f"expected an object, found {_shape(raw)}")
    if EXPRESSION_KEY in raw:
        if len(raw) != 1:
            return Filter(
                broken=f"{EXPRESSION_KEY} is the whole filter; it cannot sit beside other clauses"
            )
        held = raw[EXPRESSION_KEY]
        if not isinstance(held, str):
            return Filter(broken=f"{EXPRESSION_KEY} must be text, found {_shape(held)}")
        return parse_expression_filter(held)

    unknown = sorted(str(key) for key in raw if key not in CLAUSES)
    if unknown:
        return Filter(
            broken=f"unknown clause{'s' if len(unknown) > 1 else ''}: {', '.join(unknown)}"
        )

    kind = _text(raw.get("type")).casefold()
    if kind and not boxtypes.known(kind):
        return Filter(broken=f"there is no box type {kind!r}")

    metadata = raw.get("metadata") or {}
    if not isinstance(metadata, Mapping):
        return Filter(broken=f"metadata must be an object, found {_shape(metadata)}")
    pairs: dict[str, str] = {}
    for key, value in metadata.items():
        if not isinstance(key, str) or not key.strip():
            return Filter(broken="metadata clause needs a key")
        if not isinstance(value, str) and not isinstance(value, int | float):
            return Filter(broken=f"metadata {key} must hold a string, found {_shape(value)}")
        pairs[key.strip()] = str(value).strip().casefold()

    return Filter(
        name=_text(raw.get("name")).casefold(),
        type=kind,
        resource=_text(raw.get("resource")).casefold(),
        metadata=pairs,
    )


def parse_expression_filter(raw: str) -> Filter:
    """Read one line of filter text, keeping the person's own wording on success.

    A broken expression is `broken`, and a broken filter selects nothing. That is the same rule
    the old clauses follow and it matters more here: an expression that half-parsed into "no
    conditions" would fill the folder with the whole library, so a typo would look like the
    library had changed rather than like the filter had.
    """
    text = raw.strip()
    if not text:
        return Filter(root=All(()), expression="")
    try:
        node = parse_expression(text)
    except ExpressionError as exc:
        return Filter(broken=str(exc), expression=text)
    return Filter(root=node, expression=text)


def parse_of(box: Box) -> Filter:
    """The filter a box carries, or an empty one when it carries none."""
    return parse((box.config or {}).get(FILTER_KEY))


def _type_matches(held: str, wanted: str) -> bool:
    """Whether a box of type *held* counts as *wanted*: the type itself, or below it.

    Asking for `resource` takes a `pdf` box, because a PDF is a resource; asking
    for `pdf` does not take a `box`, which is above it. This is the same hierarchy
    the view lookup walks, so a filter reads the way the type list is drawn.
    """
    if held == wanted:
        return True
    try:
        kind = boxtypes.get(held)
    except Exception:
        # A type in the database that no longer exists is not a reason to fail;
        # it simply is not what was asked for.
        return False
    return any(ancestor.key == wanted for ancestor in boxtypes.registry.ancestors_of(kind))


def _shape(value: object) -> str:
    """What kind of thing this is, in words a user can act on."""
    if value is None:
        return "nothing"
    if isinstance(value, bool):
        return "a yes/no"
    if isinstance(value, str):
        return f"text {value!r}"
    if isinstance(value, int | float):
        return f"a number ({value})"
    if isinstance(value, list):
        return "a list"
    return type(value).__name__
