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

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from boxganizer import boxtypes
from boxganizer.models import Box

#: The key the filter is stored under in a box's config.
FILTER_KEY = "filter"

#: Clauses a filter may hold. Anything else in the mapping is a mistake to report.
CLAUSES = ("name", "type", "resource", "metadata")


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
    #: Why this filter selects nothing because it could not be read, or "" when it can.
    broken: str = ""

    @property
    def usable(self) -> bool:
        return not self.broken

    @property
    def empty(self) -> bool:
        """A filter that asks nothing of a box, so every box answers."""
        return self.usable and not (self.name or self.type or self.resource or self.metadata)

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

    def matches(self, box: Box) -> bool:
        """Whether *box* is a member. A filter that cannot be read admits nothing."""
        if self.broken:
            return False
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
        return Filter(broken=f"expected an object, found {_shape(raw)}")

    unknown = sorted(str(key) for key in raw if key not in CLAUSES)
    if unknown:
        return Filter(broken=f"unknown clause{'s' if len(unknown) > 1 else ''}: "
                             f"{', '.join(unknown)}")

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
