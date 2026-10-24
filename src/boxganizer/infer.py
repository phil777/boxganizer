"""Guess the most specific box type for a resource, and say when to propose it.

Creating a `file` box that points at `report.pdf` is almost never what someone
meant: a `pdf` box gets the PDF icon, the PDF viewer, the PDF rules. So when a
resource is named, the type is analysed and a better type is **proposed** — never
applied on its own, because the person may have had a reason.

Two cases earn a proposal:

* the suggested type is a *descendant* of the one chosen — `file` -> `pdf` is more
  specific, and nothing was lost by being more specific;
* the chosen type would *reject* the resource — `mqtt` with an `https://` address
  cannot be filed as it stands, so say so and offer what fits.

Anything else stays quiet: `image` with a `.png` needs no advice.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from boxganizer import boxtypes, paths
from boxganizer.boxtypes.base import BoxType

#: Scheme -> the type a plain address of that scheme should be.
_SCHEME_TYPES = {
    "http": "web",
    "https": "web",
    "mqtt": "mqtt",
    "mqtts": "mqtt",
    "ssh": "ssh",
    "file": "file",
}


@dataclass(frozen=True)
class Suggestion:
    """A type that fits the resource better than the one chosen."""

    key: str
    label: str
    icon: str
    reason: str

    def as_dict(self) -> dict[str, str]:
        return {"key": self.key, "label": self.label, "icon": self.icon, "reason": self.reason}


# One parser for resource URIs, shared with the file server and the file type.
split_resource = paths.split_resource


def _by_extension(path: str) -> tuple[type[BoxType], str] | None:
    """The concrete type that claims this file extension, if any."""
    lowered = path.lower()
    candidates = [
        kind
        for kind in boxtypes.all_types(include_abstract=False)
        if kind.extensions and any(lowered.endswith(ext) for ext in kind.extensions)
    ]
    if not candidates:
        return None
    # Extensions are disjoint across types; if that ever stops being true, the
    # most specific class wins rather than the first one found.
    best = max(candidates, key=lambda kind: len(boxtypes.ancestors_of(kind)))
    return best, f"ends with {Path(lowered).suffix or best.extensions}"


def best_type_for(
    resource_uri: str, *, is_dir: Callable[[Path], bool] = Path.is_dir
) -> tuple[type[BoxType], str] | None:
    """The type that best fits *resource_uri*, or None if nothing fits."""
    cleaned = resource_uri.strip()
    if not cleaned:
        return None

    scheme, path = split_resource(cleaned)

    if scheme in _SCHEME_TYPES:
        kind = boxtypes.get(_SCHEME_TYPES[scheme])
        return kind, f"a {scheme}:// address is a {kind.label}"

    if scheme:
        # An unknown protocol: nothing registered claims it.
        return None

    local = Path(path).expanduser()
    if path and is_dir(local):
        return boxtypes.DirectoryResource, f"{local} is a directory on disk"

    return _by_extension(path)


def _accepts(
    kind: type[BoxType],
    resource_uri: str,
    *,
    is_dir: Callable[[Path], bool] = Path.is_dir,
) -> bool:
    """Would this type accept a box pointing at *resource_uri*?"""
    scheme, path = split_resource(resource_uri)
    if kind.resource_uri_schemes is not None:
        allowed = kind.resource_uri_schemes
        if scheme and scheme not in allowed:
            return False
        if not scheme and "file" not in allowed:
            return False
    if kind.extensions is not None and not path.lower().endswith(tuple(kind.extensions)):
        return False
    # The same rule FileResource.check enforces when the box is written: a box
    # that claims one file cannot point at a directory.
    claims_one_file = issubclass(kind, boxtypes.FileResource)
    return not (claims_one_file and paths.is_directory(resource_uri, is_dir=is_dir))


def suggest(
    resource_uri: str | None,
    current_type: str = "box",
    *,
    is_dir: Callable[[Path], bool] = Path.is_dir,
) -> Suggestion | None:
    """Propose a better type for the resource, or None when the choice is fine."""
    if not resource_uri or not resource_uri.strip():
        return None
    found = best_type_for(resource_uri, is_dir=is_dir)
    if found is None:
        return None
    kind, reason = found

    try:
        current = boxtypes.get(current_type)
    except Exception:  # an unknown current type: any proposal is an improvement
        current = None

    if current is not None and kind.key == current.key:
        return None

    more_specific = (
        current is not None and kind is not current and current in boxtypes.ancestors_of(kind)
    )
    chosen_would_reject = current is not None and not _accepts(current, resource_uri, is_dir=is_dir)
    if not (more_specific or chosen_would_reject):
        return None

    return Suggestion(key=kind.key, label=kind.label, icon=kind.icon, reason=reason)
