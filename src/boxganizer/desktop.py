"""Desktop layout: where on the surface each child sits.

A desktop box keeps `config.desktop` with three keys — `grid` (the magnetic grid
size in pixels), `snap` (whether icons stick to it) and `positions`, a map of
child uuid to a rectangle. Containment still lives in the links table, exactly as
in :mod:`boxganizer.grid`: this only remembers *where* a child is drawn, so a
child with no rectangle is still a child and stays visible as unplaced.

Positions are pixels from the top-left of the surface, never negative, and the
magnetic grid is applied here rather than in the browser so that a drag from the
CLI and a drag from the page land the icon in the same spot.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil, floor
from typing import Any

from boxganizer.errors import PlacementError

#: Layout section this module owns inside `Box.config`.
SECTION = "desktop"

#: The magnetic grid: how coarse it is, and whether it is engaged.
DEFAULT_GRID_SIZE = 32
MIN_GRID_SIZE = 4
MAX_GRID_SIZE = 256

#: An icon tile, in CSS pixels.
DEFAULT_WIDTH = 96
DEFAULT_HEIGHT = 96
MIN_SIZE = 32
MAX_SIZE = 4096

#: A desktop bigger than this is a mistake, not a design.
MAX_COORDINATE = 100_000


@dataclass(frozen=True)
class Rect:
    """Where one child sits, and how much of the surface it takes."""

    x: int = 0
    y: int = 0
    width: int = DEFAULT_WIDTH
    height: int = DEFAULT_HEIGHT

    def __post_init__(self) -> None:
        for name in ("x", "y"):
            value = _whole_number(name, getattr(self, name))
            if value < 0:
                raise PlacementError(f"{name} must not be negative")
            # Somewhere off the end of a sane surface is a slip of the mouse.
            object.__setattr__(self, name, min(value, MAX_COORDINATE))
        for name in ("width", "height"):
            value = _whole_number(name, getattr(self, name))
            if not MIN_SIZE <= value <= MAX_SIZE:
                raise PlacementError(f"{name} must be between {MIN_SIZE} and {MAX_SIZE} pixels")

    def as_dict(self) -> dict[str, int]:
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }

    def moved_to(self, x: int, y: int) -> Rect:
        """The same tile somewhere else."""
        return Rect(x=x, y=y, width=self.width, height=self.height)

    def sized_to(self, width: int, height: int) -> Rect:
        """The same tile, bigger or smaller."""
        return Rect(x=self.x, y=self.y, width=width, height=height)


def _whole_number(label: str, value: object) -> int:
    """An int, or a refusal that says which field was not one."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise PlacementError(f"{label} must be a whole number of pixels")
    return value


@dataclass(frozen=True)
class Settings:
    """The magnetic grid: its size, and whether it is engaged."""

    grid: int = DEFAULT_GRID_SIZE
    snap: bool = False

    def __post_init__(self) -> None:
        grid = _whole_number("the grid size", self.grid)
        if not MIN_GRID_SIZE <= grid <= MAX_GRID_SIZE:
            raise PlacementError(
                f"the grid size must be between {MIN_GRID_SIZE} and {MAX_GRID_SIZE} pixels"
            )
        if not isinstance(self.snap, bool):
            raise PlacementError("the magnetic grid is either on or off")

    def as_dict(self) -> dict[str, Any]:
        return {"grid": self.grid, "snap": self.snap}

    def magnetised(self, x: int, y: int) -> tuple[int, int]:
        """Where `x, y` lands when the grid has its way with it.

        Half a cell goes away from the origin, which is what a hand dragging an
        icon expects; `round()` would send 80/32 back to 64.
        """
        if not self.snap:
            return (x, y)
        return (
            floor(x / self.grid + 0.5) * self.grid,
            floor(y / self.grid + 0.5) * self.grid,
        )


def _as_int(value: object) -> int:
    """An int from JSON, where a number may arrive as a float or a string."""
    if isinstance(value, bool):
        raise PlacementError(f"{value!r} is not a number of pixels")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError as exc:
            raise PlacementError(f"{value!r} is not a number of pixels") from exc
    raise PlacementError(f"{value!r} is not a number of pixels")


def settings_of(config: dict[str, Any] | None) -> Settings:
    """The desktop settings in a box config, or the defaults. Junk falls back."""
    section = (config or {}).get(SECTION)
    if not isinstance(section, dict):
        return Settings()
    try:
        grid = _as_int(section.get("grid", DEFAULT_GRID_SIZE))
    except PlacementError:
        return Settings()
    # Clamped rather than refused: a stored size out of range is a file that
    # drifted, and the desktop still has to open.
    return Settings(
        grid=_clamp(grid, MIN_GRID_SIZE, MAX_GRID_SIZE), snap=bool(section.get("snap", False))
    )


def rects_of(config: dict[str, Any] | None) -> dict[str, Rect]:
    """The placement map recorded in a box config, dropping what is unreadable."""
    section = (config or {}).get(SECTION)
    if not isinstance(section, dict):
        return {}
    positions = section.get("positions")
    if not isinstance(positions, dict):
        return {}
    rects: dict[str, Rect] = {}
    for uuid, raw in positions.items():
        if not isinstance(uuid, str) or not uuid.strip():
            continue
        if not isinstance(raw, dict):
            continue
        try:
            rects[uuid.strip()] = Rect(
                x=max(0, _as_int(raw.get("x", 0))),
                y=max(0, _as_int(raw.get("y", 0))),
                width=_clamp(_as_int(raw.get("width", DEFAULT_WIDTH)), MIN_SIZE, MAX_SIZE),
                height=_clamp(_as_int(raw.get("height", DEFAULT_HEIGHT)), MIN_SIZE, MAX_SIZE),
            )
        except PlacementError:
            continue  # one broken entry must not lose the whole desktop
    return rects


def _clamp(value: int, low: int, high: int) -> int:
    return min(max(value, low), high)


def rect_for(config: dict[str, Any] | None, child_uuid: str) -> Rect | None:
    """Where one child sits, or None when it has never been placed."""
    return rects_of(config).get(child_uuid)


def move(
    config: dict[str, Any] | None,
    child_uuid: str,
    x: object,
    y: object,
    *,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Put a child's icon at `x, y`, magnetised if the grid is engaged.

    A child with no rectangle yet keeps the default tile size; one that has been
    resized keeps its size and only changes place.
    """
    active = settings or settings_of(config)
    existing = rects_of(config).get(child_uuid) or Rect()
    target = existing.moved_to(*active.magnetised(_as_int(x), _as_int(y)))
    return with_rect(config, child_uuid, target)


def resize(
    config: dict[str, Any] | None, child_uuid: str, width: object, height: object
) -> dict[str, Any]:
    """Change how much surface a child's icon takes."""
    existing = rects_of(config).get(child_uuid) or Rect()
    return with_rect(config, child_uuid, existing.sized_to(_as_int(width), _as_int(height)))


def with_rect(config: dict[str, Any] | None, child_uuid: str, rect: Rect) -> dict[str, Any]:
    """A copy of `config` with one child's rectangle set."""
    if not child_uuid.strip():
        raise PlacementError("a placement needs the uuid of a child")
    rects = rects_of(config)
    rects[child_uuid.strip()] = rect
    return to_config(config, rects=rects)


def unplace(config: dict[str, Any] | None, child_uuid: str) -> dict[str, Any]:
    """Take a child off the surface. It stays a child of the box."""
    rects = rects_of(config)
    rects.pop(child_uuid.strip(), None)
    return to_config(config, rects=rects)


def to_config(
    config: dict[str, Any] | None,
    *,
    settings: Settings | None = None,
    rects: dict[str, Rect] | None = None,
) -> dict[str, Any]:
    """A copy of `config` with the desktop section replaced."""
    merged: dict[str, Any] = dict(config or {})
    section: dict[str, Any] = settings_of(merged).as_dict()
    if settings is not None:
        section.update(settings.as_dict())
    kept = rects_of(merged) if rects is None else rects
    # Written whole, so a write also cleans whatever junk an older version or a
    # hand-edited config left in the section.
    section["positions"] = {uuid: rect.as_dict() for uuid, rect in sorted(kept.items())}
    merged[SECTION] = section
    return merged


def unplaced(rects: dict[str, Rect], child_uuids: list[str]) -> list[str]:
    """Children with no spot of their own, in the order they were given."""
    return [uuid for uuid in child_uuids if uuid not in rects]


def first_free_spot(settings: Settings, taken: list[Rect]) -> tuple[int, int]:
    """A spot for a newly filed icon: the grid row after the last one used.

    Dropping a box on an empty area of the surface is the common case and lands
    where it was dropped; this is the answer to "put it in" from a frontend that
    has no coordinates to offer.
    """
    cell = settings.grid
    if not taken:
        return (cell, cell)
    lowest = max(rect.y + rect.height for rect in taken)
    return (cell, ceil(lowest / cell) * cell)
