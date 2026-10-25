"""Desktop placement: coordinates, the magnetic grid, and reading back junk."""

from __future__ import annotations

import pytest

from boxganizer import desktop
from boxganizer.errors import PlacementError

A, B = "uuid-aaaa", "uuid-bbbb"


def config_with(**section: object) -> dict[str, object]:
    return {desktop.SECTION: dict(section)}


# -- reading what is stored ---------------------------------------------------


def test_a_box_with_no_desktop_section_gets_the_defaults() -> None:
    settings = desktop.settings_of(None)
    assert settings.grid == desktop.DEFAULT_GRID_SIZE
    assert settings.snap is False
    assert desktop.rects_of({}) == {}


@pytest.mark.parametrize("junk", [None, [], "desktop", 7, {"desktop": "not a section"}])
def test_a_desktop_section_of_the_wrong_shape_is_ignored(junk: object) -> None:
    config = {"desktop": junk}
    assert desktop.settings_of(config) == desktop.Settings()
    assert desktop.rects_of(config) == {}


def test_stored_positions_are_read_back_as_rectangles() -> None:
    config = config_with(positions={A: {"x": 10, "y": 20, "width": 64, "height": 48}})
    assert desktop.rect_for(config, A) == desktop.Rect(10, 20, 64, 48)


def test_one_broken_entry_does_not_cost_the_whole_desktop() -> None:
    config = config_with(
        positions={
            A: {"x": 0, "y": 0},
            B: {"x": "north", "y": 0},
            "": {"x": 1, "y": 1},
            "uuid-cccc": "not a rectangle",
            "uuid-dddd": {"x": -5, "y": 4},
        }
    )
    rects = desktop.rects_of(config)
    assert set(rects) == {A, "uuid-dddd"}
    assert rects[A] == desktop.Rect(0, 0, desktop.DEFAULT_WIDTH, desktop.DEFAULT_HEIGHT)
    # A stored negative is unreadable in a different way: it cannot be drawn, so
    # it is pulled to the edge rather than losing the icon altogether.
    assert rects["uuid-dddd"] == desktop.Rect(0, 4, desktop.DEFAULT_WIDTH, desktop.DEFAULT_HEIGHT)


def test_numbers_may_arrive_as_json_floats_or_strings() -> None:
    config = config_with(positions={A: {"x": 12.0, "y": "32", "width": 100.0, "height": 90}})
    assert desktop.rect_for(config, A) == desktop.Rect(12, 32, 100, 90)


def test_a_size_out_of_range_in_the_file_is_brought_back_inside() -> None:
    config = config_with(positions={A: {"x": 0, "y": 0, "width": 4, "height": 999_999}})
    rect = desktop.rect_for(config, A)
    assert rect is not None
    assert (rect.width, rect.height) == (desktop.MIN_SIZE, desktop.MAX_SIZE)


# -- the rectangles themselves ------------------------------------------------


def test_a_coordinate_below_zero_is_refused() -> None:
    with pytest.raises(PlacementError, match="must not be negative"):
        desktop.Rect(x=-1, y=0)


def test_a_coordinate_far_off_the_surface_is_brought_back() -> None:
    rect = desktop.Rect(x=desktop.MAX_COORDINATE + 10, y=0)
    assert rect.x == desktop.MAX_COORDINATE


@pytest.mark.parametrize(("width", "height"), [(0, 96), (31, 96), (4097, 96)])
def test_an_icon_tile_outside_its_bounds_is_refused(width: int, height: int) -> None:
    with pytest.raises(PlacementError, match="width must be between"):
        desktop.Rect(0, 0, width, height)


@pytest.mark.parametrize("value", ["wide", None, True, 1.5])
def test_a_size_that_is_not_a_whole_number_of_pixels_is_refused(value: object) -> None:
    with pytest.raises(PlacementError, match="width must be a whole number"):
        desktop.Rect(0, 0, value, 96)  # type: ignore[arg-type]


# -- the magnetic grid --------------------------------------------------------


def test_with_the_grid_off_an_icon_stays_where_it_was_put() -> None:
    settings = desktop.Settings(grid=32, snap=False)
    assert settings.magnetised(37, 19) == (37, 19)


def test_with_the_grid_on_a_coordinate_jumps_to_the_nearest_line() -> None:
    settings = desktop.Settings(grid=32, snap=True)
    assert settings.magnetised(37, 19) == (32, 32)
    assert settings.magnetised(100, 100) == (96, 96)


def test_half_a_cell_pushes_away_from_the_origin() -> None:
    settings = desktop.Settings(grid=32, snap=True)
    assert settings.magnetised(80, 48) == (96, 64)


@pytest.mark.parametrize("grid", [3, 4096, -8, "coarse", None])
def test_a_grid_size_nobody_would_use_is_refused(grid: object) -> None:
    with pytest.raises(PlacementError, match="grid size"):
        desktop.Settings(grid=grid)  # type: ignore[arg-type]


def test_a_stored_grid_size_out_of_range_is_clamped_not_fatal() -> None:
    assert desktop.settings_of(config_with(grid=4096)).grid == desktop.MAX_GRID_SIZE
    assert desktop.settings_of(config_with(grid=1)).grid == desktop.MIN_GRID_SIZE


# -- moving, sizing, removing -------------------------------------------------


def test_moving_an_icon_that_was_never_placed_gives_it_the_default_tile() -> None:
    saved = desktop.move({}, A, 40, 60)
    assert desktop.rect_for(saved, A) == desktop.Rect(
        40, 60, desktop.DEFAULT_WIDTH, desktop.DEFAULT_HEIGHT
    )


def test_moving_keeps_the_size_a_child_was_resized_to() -> None:
    config = desktop.resize({}, A, 200, 120)
    moved = desktop.move(config, A, 10, 10)
    assert desktop.rect_for(moved, A) == desktop.Rect(10, 10, 200, 120)


def test_moving_snaps_when_the_grid_is_engaged_and_not_otherwise() -> None:
    engaged = desktop.to_config({}, settings=desktop.Settings(grid=32, snap=True))
    assert desktop.rect_for(desktop.move(engaged, A, 45, 45), A) == desktop.Rect(
        32, 32, desktop.DEFAULT_WIDTH, desktop.DEFAULT_HEIGHT
    )
    free = desktop.to_config({}, settings=desktop.Settings(grid=32, snap=False))
    assert desktop.rect_for(desktop.move(free, A, 45, 45), A) == desktop.Rect(
        45, 45, desktop.DEFAULT_WIDTH, desktop.DEFAULT_HEIGHT
    )


def test_resizing_keeps_the_place_an_icon_was_left_in() -> None:
    config = desktop.move({}, A, 64, 128)
    resized = desktop.resize(config, A, 300, 200)
    rect = desktop.rect_for(resized, A)
    assert rect is not None and (rect.x, rect.y) == (64, 128)
    assert (rect.width, rect.height) == (300, 200)


def test_a_coordinate_the_desktop_cannot_accept_is_refused() -> None:
    with pytest.raises(PlacementError, match="must not be negative"):
        desktop.move({}, A, -4, 0)
    with pytest.raises(PlacementError, match="is not a number of pixels"):
        desktop.move({}, A, "left", 0)


def test_unplacing_one_icon_leaves_the_others_alone() -> None:
    config = desktop.with_rect(
        desktop.with_rect({}, A, desktop.Rect(0, 0, 96, 96)), B, desktop.Rect(96, 0, 96, 96)
    )
    saved = desktop.unplace(config, A)
    assert desktop.rects_of(saved) == {B: desktop.Rect(96, 0, 96, 96)}


def test_the_original_config_is_never_written_to() -> None:
    original: dict[str, object] = {}
    desktop.move(original, A, 10, 10)
    desktop.to_config(original, settings=desktop.Settings(grid=64))
    assert original == {}


def test_writing_settings_leaves_the_other_namespaces_of_the_config_alone() -> None:
    config = {"display": "content", "folder": {"layout": "grid"}}
    saved = desktop.to_config(config, settings=desktop.Settings(grid=16, snap=True))
    assert saved["display"] == "content"
    assert saved["folder"] == {"layout": "grid"}
    assert saved["desktop"] == {"grid": 16, "snap": True, "positions": {}}


def test_changing_the_grid_does_not_scatter_what_is_already_on_it() -> None:
    placed = desktop.move({}, A, 64, 64)
    saved = desktop.to_config(placed, settings=desktop.Settings(grid=16, snap=True))
    assert desktop.rect_for(saved, A) == desktop.rect_for(placed, A)


def test_a_placement_without_a_child_uuid_is_refused() -> None:
    with pytest.raises(PlacementError, match="uuid of a child"):
        desktop.with_rect({}, "   ", desktop.Rect())


# -- the children nobody has placed -------------------------------------------


def test_children_without_a_spot_keep_their_order() -> None:
    rects = {A: desktop.Rect()}
    assert desktop.unplaced(rects, [B, A, "uuid-cccc"]) == [B, "uuid-cccc"]


def test_a_new_icon_lands_below_the_lowest_one_already_there() -> None:
    settings = desktop.Settings(grid=32, snap=False)
    taken = [desktop.Rect(0, 40, 96, 96), desktop.Rect(200, 300, 96, 96)]
    assert desktop.first_free_spot(settings, taken) == (32, 416)


def test_an_empty_surface_offers_the_top_left_of_the_grid() -> None:
    assert desktop.first_free_spot(desktop.Settings(grid=32), []) == (32, 32)
