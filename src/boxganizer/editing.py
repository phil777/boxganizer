"""Turn a box row into YAML and back, so it can be edited in a text editor.

The database keeps ``metadata`` and ``config`` as JSON text. In the file they are
ordinary YAML mappings, because that is what a person can read and nest; on the
way back they are validated, converted to JSON, and only then written.

Nothing here runs an editor: :func:`dump` and :func:`load` are pure, which is why
they can be tested without a terminal. The CLI owns the file and the subprocess.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import yaml

from boxganizer import boxtypes
from boxganizer.errors import EditError
from boxganizer.models import Box

#: Fields a person may change in the file.
EDITABLE_FIELDS = ("name", "description", "icon", "box_type", "resource_uri", "metadata", "config")

#: Fields that are shown but belong to the database.
READ_ONLY_FIELDS = ("id", "uuid", "created_at", "updated_at")

_JSON_SCALARS = (str, int, float, bool)


def dump(box: Box, *, parents: Sequence[str] = ()) -> str:
    """The box as a YAML document, with a short header of guidance."""
    document = {
        "id": box.id,
        "uuid": box.uuid,
        "name": box.name,
        "box_type": box.box_type,
        "icon": box.icon,
        "description": box.description,
        "resource_uri": box.resource_uri,
        "metadata": dict(box.metadata),
        "config": dict(box.config),
        "created_at": box.created_at.isoformat(timespec="seconds"),
        "updated_at": box.updated_at.isoformat(timespec="seconds"),
    }
    body = yaml.safe_dump(
        document, sort_keys=False, allow_unicode=True, default_flow_style=False, width=1000
    )
    return _header(box, parents) + body


def load(text: str, *, original: Box) -> Box:
    """Build the edited box from *text*, refusing anything the row cannot hold.

    The returned box keeps ``id``, ``created_at`` and ``updated_at`` from
    *original*; :meth:`boxganizer.store.BoxStore.update` stamps the rest.
    """
    data = _parse(text)
    _check_keys(data)
    _check_read_only(data, original)

    name = str(data.get("name") or "").strip()
    if not name:
        raise EditError("a box needs a name — 'name' is empty")

    box_type = str(data.get("box_type") or original.box_type)
    kind = boxtypes.get(box_type)
    icon = data.get("icon")
    return Box(
        id=original.id,
        uuid=original.uuid,
        name=name,
        description=str(data.get("description") or ""),
        icon=str(icon) if icon else kind.icon,
        box_type=box_type,
        resource_uri=_optional_text(data.get("resource_uri")),
        metadata=_string_map(data.get("metadata")),
        config=_json_object(data.get("config")),
        created_at=original.created_at,
        updated_at=original.updated_at,
    )


# -- reading ---------------------------------------------------------------


def _parse(text: str) -> dict[str, Any]:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:  # pragma: no cover - message shape varies
        mark = getattr(exc, "problem_mark", None)
        raise EditError(
            f"this is not valid YAML: {getattr(exc, 'problem', exc)}",
            line=mark.line + 1 if mark is not None else None,
        ) from exc
    if data is None:
        raise EditError("the file is empty — close the editor without saving to change nothing")
    if not isinstance(data, dict):
        raise EditError("the file must hold one mapping of box fields, not a list or a scalar")
    return data


def _check_keys(data: Mapping[str, Any]) -> None:
    allowed = set(EDITABLE_FIELDS) | set(READ_ONLY_FIELDS)
    unknown = [str(key) for key in data if key not in allowed]
    if not unknown:
        return
    hint = (
        " (links are edited with `box link`)"
        if "parents" in unknown or "children" in unknown
        else ""
    )
    raise EditError(f"unknown field(s): {', '.join(sorted(unknown))}{hint}")


def _check_read_only(data: Mapping[str, Any], original: Box) -> None:
    current = {
        "id": original.id,
        "uuid": original.uuid,
        "created_at": original.created_at.isoformat(timespec="seconds"),
        "updated_at": original.updated_at.isoformat(timespec="seconds"),
    }
    for field in READ_ONLY_FIELDS:
        if field in data and data[field] != current[field]:
            raise EditError(f"{field} is set by Boxganizer and cannot be edited")


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _string_map(value: object) -> dict[str, str]:
    """`metadata` is a flat map of strings; say so instead of mangling it."""
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise EditError("metadata must be a mapping of key: value")
    result: dict[str, str] = {}
    for key, item in value.items():
        if isinstance(item, bool):
            result[str(key)] = "true" if item else "false"
        elif isinstance(item, _JSON_SCALARS) or item is None:
            result[str(key)] = "" if item is None else str(item)
        else:
            raise EditError(f"metadata.{key} must be a single value; put nesting in config")
    return result


def _json_object(value: object) -> dict[str, Any]:
    """`config` may nest, but only with values JSON can store."""
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise EditError("config must be a mapping")
    converted: dict[str, Any] = {}
    for key, item in value.items():
        converted[str(key)] = _json_value(item, f"config.{key}")
    return converted


def _json_value(value: object, path: str) -> Any:
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item, f"{path}.{key}") for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_value(item, f"{path}[{index}]") for index, item in enumerate(value)]
    raise EditError(
        f"{path} is a {type(value).__name__}, which JSON cannot store — write it as text"
    )


# -- writing ---------------------------------------------------------------


def _header(box: Box, parents: Sequence[str]) -> str:
    kind = boxtypes.try_get(box.box_type)
    kind_note = kind.label if kind is not None else f"{box.box_type} (unknown type)"
    lines = [
        f"# Boxganizer — box #{box.id}: {box.name}",
        "#",
        f"# type      : {kind_note}",
        f"# key       : {box.key}  (derived from name; not editable)",
        f"# parents   : {', '.join(parents) if parents else '—'}  (use `box link`)",
        "#",
        "# metadata and config are stored as JSON; here they are YAML.",
        "# Everything above marked read-only is set by Boxganizer.",
        "# Save and close the editor to write the box back, or quit without",
        "# saving to change nothing.",
        "#",
    ]
    return "\n".join(lines) + "\n"
