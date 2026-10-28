"""Domain model. One type: :class:`Box`.

A box holds one resource and any number of other boxes. Boxes are identified
by name, case-insensitively, and the name is the only mandatory field.

Two JSON fields travel with a box: `metadata`, a flat map of strings the user
labels freely, and `config`, arbitrary nested settings belonging to the box
type.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from boxganizer.errors import InvalidBoxName

#: Longest name we accept. Longer strings are almost certainly a paste accident.
MAX_NAME_LENGTH = 200

#: Control characters (and the newline family) never belong in a name.
_FORBIDDEN = re.compile(r"[\x00-\x1f\x7f]")

DEFAULT_ICON = "\U0001f4e6"  # 📦


def _utcnow() -> datetime:
    return datetime.now(UTC)


def new_uuid() -> str:
    """A fresh identity for a box: stable, unique, safe to put in a URL."""
    return str(uuid4())


def canonical_name(name: str) -> str:
    """Return the identity key of *name*: stripped and case-folded.

    Two names with the same canonical form name the same box.
    """
    return name.strip().casefold()


@dataclass(slots=True)
class Box:
    """A named container for one resource and any number of other boxes."""

    name: str
    description: str = ""
    icon: str = DEFAULT_ICON
    #: Key of a class in :mod:`boxganizer.boxtypes`. Plain boxes use ``"box"``.
    box_type: str = "box"
    #: What the box points at, as written. The database keeps this in the `resources`
    #: table and this field is filled from there on every read, so the rest of the code
    #: can go on asking a box for its address without knowing about the join.
    resource_uri: str | None = None
    #: The row of `resources` this box points at, once it has been stored. Assigned by
    #: the store: two boxes on one file get the same id, and one file means one entry in
    #: the full-text index and one set of observations about whether it has changed.
    resource_id: int | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    #: Type-specific settings. Arbitrary JSON, unlike the flat string metadata.
    config: dict[str, Any] = field(default_factory=dict)
    id: int | None = None
    #: Permanent identifier. `id` is a row number; this is what links, saved
    #: layouts and URLs refer to, so it survives a rename and an export.
    uuid: str = field(default_factory=new_uuid)
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)

    def __post_init__(self) -> None:
        self.name = self._validate_name(self.name)
        if self.resource_uri is not None:
            self.resource_uri = self.resource_uri.strip() or None
        self.metadata = dict(self.metadata)
        self.config = dict(self.config)

    @staticmethod
    def _validate_name(name: str) -> str:
        if not isinstance(name, str):  # pragma: no cover - defensive
            raise InvalidBoxName(str(name), "must be a string")
        cleaned = name.strip()
        if not cleaned:
            raise InvalidBoxName(name, "name is empty")
        if _FORBIDDEN.search(cleaned):
            raise InvalidBoxName(name, "name contains a control character")
        if len(cleaned) > MAX_NAME_LENGTH:
            raise InvalidBoxName(name, f"name is longer than {MAX_NAME_LENGTH} characters")
        return cleaned

    @property
    def key(self) -> str:
        """Case-insensitive identity of this box."""
        return canonical_name(self.name)

    def touch(self) -> None:
        """Mark this box as modified now."""
        self.updated_at = _utcnow()

    def to_dict(self) -> dict[str, object]:
        """Plain JSON-serialisable view, used by the web API and the TUI."""
        return {
            "id": self.id,
            "uuid": self.uuid,
            "name": self.name,
            "description": self.description,
            "icon": self.icon,
            "box_type": self.box_type,
            "resource_uri": self.resource_uri,
            "resource_id": self.resource_id,
            "metadata": dict(self.metadata),
            "config": dict(self.config),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> Box:
        """Build a box from a parsed JSON body, ignoring unknown keys."""
        raw_meta = data.get("metadata") or {}
        if not isinstance(raw_meta, Mapping):
            raise InvalidBoxName(str(data.get("name", "")), "metadata must be an object")
        raw_config = data.get("config") or {}
        if not isinstance(raw_config, Mapping):
            raise InvalidBoxName(str(data.get("name", "")), "config must be an object")
        return cls(
            uuid=str(data["uuid"]) if data.get("uuid") else new_uuid(),
            name=str(data["name"]),
            description=str(data.get("description", "")),
            icon=str(data.get("icon") or DEFAULT_ICON),
            box_type=str(data.get("box_type") or "box"),
            resource_uri=(None if data.get("resource_uri") is None else str(data["resource_uri"])),
            metadata={str(k): str(v) for k, v in raw_meta.items()},
            config=dict(raw_config),
        )
