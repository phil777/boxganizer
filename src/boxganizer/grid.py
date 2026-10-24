"""Grid layout: which child occupies which cell.

A grid box keeps `config.grid` with three keys — `rows`, `columns`, and `cells`,
a map of `"row,column"` to a child uuid. Containment itself lives in the links
table like everywhere else; this map only remembers *where* a child is drawn, so
the two can be compared, and a child that is linked but has no cell stays
visible as unplaced rather than disappearing when the grid shrinks.

Children are keyed by uuid, not name: a layout that stored names would break on
the first rename.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from boxganizer.errors import GridError

#: Layouts larger than this are a mistake, not a design.
MAX_ROWS = 24
MAX_COLUMNS = 24
DEFAULT_ROWS = 3
DEFAULT_COLUMNS = 4

_CELL_KEY = re.compile(r"^\s*(\d+)\s*,\s*(\d+)\s*$")

Cell = tuple[int, int]


@dataclass(frozen=True)
class GridSize:
    """How many rows and columns a grid has, clamped to sane bounds."""

    rows: int = DEFAULT_ROWS
    columns: int = DEFAULT_COLUMNS

    def __post_init__(self) -> None:
        if self.rows < 1 or self.columns < 1:
            raise GridError("a grid needs at least one row and one column")
        object.__setattr__(self, "rows", min(self.rows, MAX_ROWS))
        object.__setattr__(self, "columns", min(self.columns, MAX_COLUMNS))

    @property
    def capacity(self) -> int:
        return self.rows * self.columns

    def contains(self, cell: Cell) -> bool:
        row, column = cell
        return 0 <= row < self.rows and 0 <= column < self.columns

    def as_dict(self) -> dict[str, int]:
        return {"rows": self.rows, "columns": self.columns}


def key(cell: Cell) -> str:
    """The JSON key for a cell: `"row,column"`."""
    return f"{cell[0]},{cell[1]}"


def parse_cell(value: object) -> Cell:
    """Read a cell reference from JSON, `[row, column]` or `"row,column"`."""
    if isinstance(value, (list | tuple)) and len(value) == 2:
        try:
            return (int(value[0]), int(value[1]))
        except (TypeError, ValueError) as exc:
            raise GridError(f"{value!r} is not a cell") from exc
    match = _CELL_KEY.match(str(value))
    if match is None:
        raise GridError(f"{value!r} is not a cell; expected row,column")
    return (int(match.group(1)), int(match.group(2)))


def parse_cells(raw: object) -> dict[Cell, str]:
    """Read `cells` from stored config, dropping anything unrecognisable."""
    if not isinstance(raw, dict):
        return {}
    placed: dict[Cell, str] = {}
    for name, uuid in raw.items():
        match = _CELL_KEY.match(str(name))
        if match is None or not isinstance(uuid, str) or not uuid.strip():
            continue
        placed[(int(match.group(1)), int(match.group(2)))] = uuid.strip()
    return placed


def size_of(config: dict[str, Any] | None) -> GridSize:
    """The grid size recorded in a box config, or the default."""
    section = (config or {}).get("grid")
    if not isinstance(section, dict):
        return GridSize()
    try:
        rows = int(section.get("rows", DEFAULT_ROWS))
        columns = int(section.get("columns", DEFAULT_COLUMNS))
    except (TypeError, ValueError):
        return GridSize()
    return GridSize(rows=max(1, rows), columns=max(1, columns))


def cells_of(config: dict[str, Any] | None) -> dict[Cell, str]:
    """The occupancy map recorded in a box config."""
    section = (config or {}).get("grid")
    if not isinstance(section, dict):
        return {}
    return parse_cells(section.get("cells"))


def place(
    *,
    cells: dict[Cell, str],
    size: GridSize,
    child_uuid: str,
    cell: Cell,
    known_children: set[str],
) -> dict[Cell, str]:
    """Put `child_uuid` in `cell`, returning a new map.

    An occupant is swapped out into the cell the child came from. A child that
    was not in the grid at all (dragged in from the side panel) sends its
    occupant to the first free cell; if there is none, the grid is full.
    """
    if child_uuid not in known_children:
        raise GridError("that box is not one this grid can hold")
    if not size.contains(cell):
        raise GridError(
            f"cell {key(cell)} is outside a grid of {size.rows} rows and {size.columns} columns"
        )

    placed = dict(cells)
    origin = next((existing for existing, uuid in placed.items() if uuid == child_uuid), None)
    occupant = placed.get(cell)

    if origin is not None:
        del placed[origin]
    placed[cell] = child_uuid
    if occupant is not None and occupant != child_uuid:
        if origin is not None:
            placed[origin] = occupant  # both had a cell: swap them
        else:
            free = first_free(placed, size)
            if free is None:
                raise GridError("the grid is full: no free cell for the box it displaces")
            placed[free] = occupant
    return placed


def first_free(cells: dict[Cell, str], size: GridSize) -> Cell | None:
    """The top-left cell with nothing in it, or None."""
    taken = set(cells)
    for row in range(size.rows):
        for column in range(size.columns):
            if (row, column) not in taken:
                return (row, column)
    return None


def unplace(cells: dict[Cell, str], child_uuid: str) -> dict[Cell, str]:
    """Take a child out of the grid. The box stays where it is in the graph."""
    return {cell: uuid for cell, uuid in cells.items() if uuid != child_uuid}


def unplace_outside(cells: dict[Cell, str], size: GridSize) -> tuple[dict[Cell, str], list[str]]:
    """Drop entries outside `size`, as when the grid shrinks.

    Returns the new map and the uuids that lost their cell, so the caller can
    keep showing them as unplaced instead of losing them.
    """
    kept = {cell: uuid for cell, uuid in cells.items() if size.contains(cell)}
    lost = sorted({uuid for uuid in cells.values() if uuid not in set(kept.values())})
    return kept, lost


def to_config(
    config: dict[str, Any] | None,
    *,
    size: GridSize | None = None,
    cells: dict[Cell, str] | None = None,
) -> dict[str, Any]:
    """A copy of `config` with the grid section replaced."""
    merged: dict[str, Any] = dict(config or {})
    section: dict[str, Any] = (
        dict(merged.get("grid") or {}) if isinstance(merged.get("grid"), dict) else {}
    )
    if size is not None:
        section.update(size.as_dict())
    if cells is not None:
        section["cells"] = {key(cell): uuid for cell, uuid in sorted(cells.items())}
    merged["grid"] = section
    return merged


def unplaced(cells: dict[Cell, str], child_uuids: list[str]) -> list[str]:
    """Children with no cell of their own, in the order they were given."""
    taken = set(cells.values())
    return [uuid for uuid in child_uuids if uuid not in taken]
