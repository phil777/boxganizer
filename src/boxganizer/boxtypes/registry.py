"""Registry of box types, keyed by their public ``key``.

Every subclass of :class:`boxganizer.boxtypes.base.BoxType` registers itself
when the class object is created, so the class hierarchy is the only source of
truth: ``box types``, the web API and store validation all read it back.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from boxganizer.errors import UnknownBoxType

if TYPE_CHECKING:  # pragma: no cover - typing only
    from boxganizer.boxtypes.base import BoxType

_BY_KEY: dict[str, type[BoxType]] = {}

#: Keys whose class body itself said ``abstract = True``. A grouping level marks
#: itself; its subclasses are selectable unless they say otherwise, so this is
#: read from the class ``__dict__`` at registration time and never inherited.
_GROUPING_KEYS: set[str] = set()


def register(cls: type[BoxType]) -> None:
    """Register *cls* under its own ``key``; called on subclass creation.

    A class that forgets to declare ``key`` would silently share its parent's
    name, so that is a hard ``TypeError`` at import time.
    """
    key = cls.__dict__.get("key")
    if not isinstance(key, str) or not key.strip():
        raise TypeError(f"{cls.__name__} must declare its own non-empty key")
    previous = _BY_KEY.get(key)
    if previous is not None and previous is not cls:
        raise TypeError(
            f"box type key {key!r} is claimed by both {previous.__name__} and {cls.__name__}"
        )
    _BY_KEY[key] = cls
    if cls.__dict__.get("abstract", False):
        _GROUPING_KEYS.add(key)
    else:
        _GROUPING_KEYS.discard(key)


def is_abstract(cls: type[BoxType]) -> bool:
    """True for the grouping levels of the hierarchy, which no box uses directly."""
    return cls.key in _GROUPING_KEYS


def get(key: str) -> type[BoxType]:
    """Return the type registered under *key*, ignoring case and padding."""
    cleaned = key.strip().lower()
    found = _BY_KEY.get(cleaned)
    if found is None:
        raise UnknownBoxType(key, sorted(_BY_KEY))
    return found


def try_get(key: str) -> type[BoxType] | None:
    """The type named *key*, or ``None``.

    Reads use this, writes use :func:`get`. A database written by a newer
    Boxganizer may hold a type this one has never heard of: showing those boxes
    must still work.
    """
    return _BY_KEY.get(key.strip().lower())


def known(key: str) -> bool:
    """True when *key* names a registered type."""
    return key.strip().lower() in _BY_KEY


def all_types(*, include_abstract: bool = True) -> list[type[BoxType]]:
    """Registered types in declaration order."""
    types = list(_BY_KEY.values())
    if include_abstract:
        return types
    return [cls for cls in types if not is_abstract(cls)]


def parent_of(cls: type[BoxType]) -> type[BoxType] | None:
    """The nearest registered base class, or ``None`` for the root."""
    registered = set(_BY_KEY.values())
    for base in cls.__mro__[1:]:
        if base in registered:
            return base
    return None


def children_of(cls: type[BoxType]) -> list[type[BoxType]]:
    """Direct subclasses of *cls*, in declaration order."""
    return [candidate for candidate in _BY_KEY.values() if parent_of(candidate) is cls]


def ancestors_of(cls: type[BoxType]) -> list[type[BoxType]]:
    """Parent chain from just above *cls* down to the root."""
    chain: list[type[BoxType]] = []
    current = parent_of(cls)
    while current is not None:
        chain.append(current)
        current = parent_of(current)
    return chain


def tree(*, include_abstract: bool = True) -> list[tuple[int, type[BoxType]]]:
    """The hierarchy flattened to ``(depth, type)`` pairs, root first."""
    rows: list[tuple[int, type[BoxType]]] = []

    def walk(cls: type[BoxType], depth: int) -> None:
        shown = include_abstract or not is_abstract(cls)
        if shown:
            rows.append((depth, cls))
        for child in children_of(cls):
            walk(child, depth + 1 if shown else depth)

    for root in (cls for cls in _BY_KEY.values() if parent_of(cls) is None):
        walk(root, 0)
    return rows
