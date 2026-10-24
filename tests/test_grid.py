"""Grid placement: swapping, displacing, resizing, and reading back junk."""

from __future__ import annotations

import pytest

from boxganizer import grid
from boxganizer.errors import GridError

A, B, C = "uuid-aaaa", "uuid-bbbb", "uuid-cccc"
SIZE = grid.GridSize(rows=2, columns=3)


def test_cells_round_trip_through_json_keys() -> None:
    cells = {(0, 1): A, (1, 2): B}
    assert grid.key((0, 1)) == "0,1"
    assert grid.parse_cells({"0,1": A, "1,2": B}) == cells


def test_junk_in_the_stored_map_is_dropped() -> None:
    parsed = grid.parse_cells({"0,0": A, "nonsense": B, "1,x": C, "2,2": "  ", "3,3": 7})
    assert parsed == {(0, 0): A}


def test_parse_cells_on_wrong_types_is_empty() -> None:
    assert grid.parse_cells(None) == {}
    assert grid.parse_cells(["a"]) == {}


@pytest.mark.parametrize(
    ("value", "cell"),
    [((1, 2), (1, 2)), ([1, 2], (1, 2)), ("1,2", (1, 2)), (" 1 , 2 ", (1, 2))],
)
def test_a_cell_can_be_read_from_either_shape(value: object, cell: tuple[int, int]) -> None:
    assert grid.parse_cell(value) == cell


def test_a_nonsense_cell_is_refused() -> None:
    with pytest.raises(GridError, match="not a cell"):
        grid.parse_cell("somewhere")


def test_placing_into_an_empty_cell() -> None:
    placed = grid.place(cells={}, size=SIZE, child_uuid=A, cell=(0, 0), known_children={A, B})
    assert placed == {(0, 0): A}


def test_cell_outside_the_grid_is_refused() -> None:
    with pytest.raises(GridError, match="outside a grid"):
        grid.place(cells={}, size=SIZE, child_uuid=A, cell=(2, 0), known_children={A})


def test_an_unknown_box_cannot_be_placed() -> None:
    with pytest.raises(GridError, match="not one this grid can hold"):
        grid.place(cells={}, size=SIZE, child_uuid=A, cell=(0, 0), known_children={B})


def test_moving_into_an_empty_cell_leaves_the_old_one_free() -> None:
    placed = grid.place(cells={(0, 0): A}, size=SIZE, child_uuid=A, cell=(1, 1), known_children={A})
    assert placed == {(1, 1): A}


def test_two_children_swap_cells() -> None:
    cells = {(0, 0): A, (0, 1): B}
    placed = grid.place(cells=cells, size=SIZE, child_uuid=A, cell=(0, 1), known_children={A, B})
    assert placed == {(0, 1): A, (0, 0): B}
    assert cells == {(0, 0): A, (0, 1): B}  # the input is not mutated


def test_dragging_in_from_outside_displaces_to_the_first_free_cell() -> None:
    cells = {(0, 0): A}
    placed = grid.place(cells=cells, size=SIZE, child_uuid=B, cell=(0, 0), known_children={A, B})
    assert placed[(0, 0)] == B
    assert placed[(0, 1)] == A


def test_a_full_grid_refuses_an_outsider() -> None:
    full = {(0, c): f"x{c}" for c in range(3)} | {(1, c): f"y{c}" for c in range(3)}
    with pytest.raises(GridError, match="grid is full"):
        grid.place(
            cells=full,
            size=SIZE,
            child_uuid=A,
            cell=(0, 0),
            known_children={A} | set(full.values()),
        )


def test_dropping_a_child_keeps_it_in_the_graph_but_unplaced() -> None:
    assert grid.unplace({(0, 0): A, (0, 1): B}, A) == {(0, 1): B}


def test_shrinking_unplaces_what_no_longer_fits() -> None:
    cells = {(0, 0): A, (0, 1): B, (1, 0): C}
    kept, lost = grid.unplace_outside(cells, grid.GridSize(rows=1, columns=2))
    assert kept == {(0, 0): A, (0, 1): B}
    assert lost == [C]


def test_a_grid_size_is_clamped_to_something_sane() -> None:
    size = grid.GridSize(rows=999, columns=500)
    assert size.rows == grid.MAX_ROWS and size.columns == grid.MAX_COLUMNS


def test_a_grid_needs_at_least_one_cell() -> None:
    with pytest.raises(GridError):
        grid.GridSize(rows=0, columns=3)


def test_first_free_walks_left_to_right_top_to_bottom() -> None:
    cells = {(0, 0): A, (0, 1): B, (1, 0): C}
    assert grid.first_free(cells, SIZE) == (0, 2)


def test_size_and_cells_are_read_from_a_box_config() -> None:
    config = {"grid": {"rows": 2, "columns": 2, "cells": {"1,1": A}}, "display": "content"}
    assert grid.size_of(config) == grid.GridSize(rows=2, columns=2)
    assert grid.cells_of(config) == {(1, 1): A}


def test_a_config_without_a_grid_section_gets_the_defaults() -> None:
    assert grid.size_of({}) == grid.GridSize()
    assert grid.cells_of({}) == {}
    assert grid.size_of(None) == grid.GridSize()
    assert grid.size_of({"grid": {"rows": "many"}}) == grid.GridSize()


def test_to_config_replaces_only_the_grid_section() -> None:
    config = {"display": "content", "text": {"wrap": False}}
    merged = grid.to_config(config, size=SIZE, cells={(0, 0): A})
    assert merged["display"] == "content"
    assert merged["text"] == {"wrap": False}
    assert merged["grid"] == {"rows": 2, "columns": 3, "cells": {"0,0": A}}


def test_children_without_a_cell_are_reported_in_order() -> None:
    assert grid.unplaced({(0, 0): A}, [A, B, C]) == [B, C]


def test_capacity_follows_the_size() -> None:
    assert grid.GridSize(rows=3, columns=4).capacity == 12
