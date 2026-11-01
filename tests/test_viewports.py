"""Tests for viewport boxes: the tiling tree, the windows, and what the API will do to them.

The behaviour under test is the one the request asked for: several boxes open at once, each pane
keeping its own path, and geometry that is still there after a reload. What a pane *is* is decided
in `boxganizer.viewports`, so most of these tests talk to that module directly; the API tests are
about the two things only the API can get wrong — who becomes a child of what, and what is refused
before it is saved.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from boxganizer import viewports
from boxganizer.errors import InvalidBoxContent
from boxganizer.models import Box
from boxganizer.store import BoxStore
from boxganizer.web.app import create_app


def tiling(child: str = "") -> viewports.Layout:
    return viewports.new_tiling(child)


def tree_of(layout: viewports.Layout) -> viewports.Node:
    assert layout.tree is not None
    return layout.tree


# ------------------------------------------------------------------ the tiling tree


def test_a_tiling_viewport_starts_as_one_pane_taking_everything() -> None:
    one = tiling()
    assert [leaf.node for leaf in viewports.leaves(one.tree)] == ["v1"]
    assert one.panes() == ["v1"]
    assert one.children() == []


def test_splitting_makes_two_panes_and_the_box_stays_in_the_one_you_clicked() -> None:
    """The click was on a pane showing something; that something does not move.

    The new pane is empty rather than a copy: a copy would make "compare two directories" a
    two-step job with an intermediate state nobody asked to look at.
    """
    opened, fresh = viewports.split(tiling("box-left"), "v1", "horizontal")
    assert [leaf.node for leaf in viewports.leaves(opened.tree)] == ["v1", fresh]
    assert viewports.find(opened.tree, "v1").child == "box-left"  # type: ignore[union-attr]
    assert viewports.find(opened.tree, fresh).child == ""  # type: ignore[union-attr]
    split = tree_of(opened)
    assert isinstance(split, viewports.Split)
    assert split.direction == "horizontal" and split.ratio == 0.5


def test_both_directions_are_real_and_a_direction_that_is_not_one_is_refused() -> None:
    across, _ = viewports.split(tiling(), "v1", "horizontal")
    down, _ = viewports.split(tiling(), "v1", "vertical")
    assert tree_of(across).direction == "horizontal"  # type: ignore[union-attr]
    assert tree_of(down).direction == "vertical"  # type: ignore[union-attr]
    assert tree_of(viewports.split(tiling(), "v1", "side by side")[0]).direction == "horizontal"  # type: ignore[union-attr]
    with pytest.raises(viewports.ViewportError, match="not a direction"):
        viewports.split(tiling(), "v1", "sideways")


def test_splitting_a_pane_of_a_pane_nests_the_tree_rather_than_flattening_it() -> None:
    """Splitting twice is a tree of three, not a row of three: the second split belongs inside the
    space the first one handed out, or the bars would not mean what they look like."""
    first, right = viewports.split(tiling(), "v1", "horizontal")
    second, lower = viewports.split(first, right, "vertical")
    assert [leaf.node for leaf in viewports.leaves(second.tree)] == ["v1", right, lower]
    inner = viewports.find(second.tree, "s2")
    assert isinstance(inner, viewports.Split) and inner.direction == "vertical"
    assert [inner.first.node, inner.second.node] == [right, lower]


def test_pane_ids_and_divider_ids_never_collide() -> None:
    """`v2` is a pane and `s1` is the bar between two of them; one tree has both, and a request
    that could mean either would make "split this" and "move that" the same instruction."""
    first, right = viewports.split(tiling(), "v1", "horizontal")
    second, _ = viewports.split(first, right, "horizontal")
    ids = viewports._all_ids(second)
    panes = {leaf.node for leaf in viewports.leaves(second.tree)}
    assert ids - panes == {"s1", "s2"}
    assert ids & panes == panes


def test_a_divider_moves_and_cannot_be_dragged_out_of_the_world() -> None:
    layout, _ = viewports.split(tiling("a"), "v1", "horizontal")
    moved = viewports.set_ratio(layout, "s1", 0.25)
    assert viewports.find(moved.tree, "s1").ratio == 0.25  # type: ignore[union-attr]
    for value, expected in (
        (0.0, viewports.MIN_RATIO),
        (1.0, viewports.MAX_RATIO),
        (-3, viewports.MIN_RATIO),
    ):
        assert viewports.set_ratio(layout, "s1", value).__dict__["tree"] is not None
        assert viewports.find(viewports.set_ratio(layout, "s1", value).tree, "s1").ratio == expected  # type: ignore[union-attr]
    with pytest.raises(viewports.ViewportError, match="no divider"):
        viewports.set_ratio(layout, "v1", 0.4)


def test_closing_a_pane_gives_the_space_back_to_its_sibling_and_reports_which_box_it_held() -> None:
    left = tiling("box-left")
    first, right = viewports.split(left, "v1", "horizontal")
    both = viewports.assign(first, right, "box-right")
    closed, held = viewports.close(both, right)
    assert held == "box-right", "the caller has to be able to unfile exactly that box"
    assert [leaf.node for leaf in viewports.leaves(closed.tree)] == ["v1"]
    assert viewports.find(closed.tree, "v1").child == "box-left"  # type: ignore[union-attr]


def test_closing_a_pane_inside_a_group_only_frees_that_group() -> None:
    """`recover the place occupied before by the closed viewport`: the outer ratio is untouched."""
    first, right = viewports.split(tiling("a"), "v1", "horizontal")
    second, lower = viewports.split(first, right, "vertical")
    moved = viewports.set_ratio(second, "s1", 0.3)
    closed, _held = viewports.close(moved, lower)
    outer = viewports.find(closed.tree, "s1")
    inner = viewports.find(closed.tree, "s2")
    assert outer is not None and outer.ratio == 0.3
    assert inner is None, "the group collapsed into its surviving pane"
    assert [leaf.node for leaf in viewports.leaves(closed.tree)] == ["v1", right]


def test_the_last_pane_refuses_to_be_closed() -> None:
    with pytest.raises(viewports.ViewportError, match="last viewport"):
        viewports.close(tiling("only"), "v1")
    single, _ = viewports.split(tiling("a"), "v1", "horizontal")
    back, _ = viewports.close(single, "v2")
    with pytest.raises(viewports.ViewportError, match="last viewport"):
        viewports.close(back, "v1")


def test_a_pane_holds_one_box_and_naming_an_unknown_pane_is_refused() -> None:
    layout, right = viewports.split(tiling("a"), "v1", "horizontal")
    with pytest.raises(viewports.ViewportError, match="no viewport"):
        viewports.assign(layout, "v9", "b")
    with pytest.raises(viewports.ViewportError, match="pair of viewports"):
        viewports.assign(layout, "s1", "b")
    assert viewports.assign(layout, right, "b").children() == ["a", "b"]


def test_the_same_box_may_be_open_twice_because_that_is_what_comparison_means() -> None:
    layout, right = viewports.split(tiling("same"), "v1", "horizontal")
    twice = viewports.assign(layout, right, "same")
    assert twice.children() == ["same", "same"]


# ------------------------------------------------------------------ the windows


def test_windows_are_placed_sized_raised_and_closed_without_touching_the_other_ones() -> None:
    start = viewports.new_windowed("box-a")
    second, node = viewports.open_window(start, "box-b")
    assert [(w.node, w.x, w.y) for w in second.windows] == [("v1", 24, 24), ("v2", 56, 56)]
    moved = viewports.move_window(second, node, 400, 250)
    assert [(w.x, w.y) for w in moved.windows if w.node == node] == [(400, 250)]
    assert [(w.x, w.y) for w in moved.windows if w.node != node] == [(24, 24)]
    sized = viewports.resize_window(moved, node, 900, 500)
    assert [(w.width, w.height) for w in sized.windows if w.node == node] == [(900, 500)]
    tiny = viewports.resize_window(moved, node, 10, 10)
    assert [(w.width, w.height) for w in tiny.windows if w.node == node] == [
        (viewports.MIN_WINDOW, viewports.MIN_WINDOW // 2)
    ], "a window smaller than a path line is not a window"
    raised = viewports.raise_window(sized, node)
    assert raised.order == ("v1", node)
    closed, held = viewports.close(raised, node)
    assert held == "box-b" and [w.node for w in closed.windows] == ["v1"]
    with pytest.raises(viewports.ViewportError, match="last window"):
        viewports.close(closed, "v1")


def test_a_tiling_layout_refuses_window_requests_and_the_other_way_round() -> None:
    with pytest.raises(viewports.ViewportError, match="changed by splitting it"):
        viewports.open_window(viewports.new_tiling())
    with pytest.raises(viewports.ViewportError, match="opening another window"):
        viewports.split(viewports.new_windowed(), "v1", "horizontal")


# ------------------------------------------------------------------ what may be saved


def test_a_layout_that_is_not_shaped_like_a_layout_is_refused() -> None:
    assert viewports.check({}) == ""
    assert "kind must be tiling or windowed" in viewports.check({"viewports": {"kind": "wobbly"}})
    assert "not shaped like a tree" in viewports.check(
        {"viewports": {"kind": "tiling", "tree": "flat"}}
    )
    assert "at least one window" in viewports.check(
        {"viewports": {"kind": "windowed", "windows": []}}
    )
    assert "not a direction" in viewports.check(
        {
            "viewports": {
                "kind": "tiling",
                "tree": {
                    "node": "s1",
                    "direction": "diagonal",
                    "first": {"node": "v1"},
                    "second": {"node": "v2"},
                },
            }
        }
    )


def test_a_pane_naming_a_box_the_viewport_does_not_hold_is_refused() -> None:
    """The store knows the children; the type's own check does not, so this rule runs there."""
    layout = viewports.assign(viewports.new_tiling(), "v1", "somebody-elses-box")
    assert viewports.check(layout.as_config({})) == "", "the shape is fine"
    problem = viewports.check(layout.as_config({}), {"a-child-of-mine"})
    assert "not a child of this box" in problem and "v1" in problem
    assert viewports.check(layout.as_config({}), {"somebody-elses-box"}) == ""


def test_a_stored_layout_that_makes_no_opens_one_pane_and_says_why() -> None:
    """Geometry that cannot be read degrades instead of exploding, and never silently."""
    broken = {"viewports": {"kind": "tiling", "tree": {"node": "v1", "direction": "diagonal"}}}
    layout = viewports.layout_of(broken)
    assert layout.panes() == ["v1"]
    assert layout.problems and "direction" in layout.problems[0]


def test_the_box_type_refuses_a_broken_tree_when_the_box_is_saved(tmp_path: Path) -> None:
    store = BoxStore(tmp_path / "vp.db", content_root=tmp_path)
    try:
        with pytest.raises(InvalidBoxContent, match="viewport"):
            store.add(
                Box(
                    name="Bad layout",
                    box_type="tiling-viewport",
                    config={"viewports": {"kind": "tiling", "tree": "not a tree"}},
                )
            )
        good = store.add(Box(name="Layout", box_type="tiling-viewport"))
        assert good.config == {}
    finally:
        store.close()


# ------------------------------------------------------------------ the API


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    store = BoxStore(tmp_path / "api.db", content_root=tmp_path)
    try:
        with TestClient(create_app(store)) as test_client:
            test_client.store = store  # type: ignore[attr-defined]
            yield test_client
    finally:
        store.close()


def viewport(client: TestClient, name: str = "Compare", kind: str = "tiling-viewport") -> dict:
    return client.post("/api/boxes", json={"name": name, "box_type": kind}).json()


def note(client: TestClient, name: str) -> str:
    return client.post("/api/boxes", json={"name": name, "box_type": "note"}).json()["uuid"]


def panes(client: TestClient, ref: str) -> list[dict]:
    return client.get(f"/api/boxes/{ref}/viewports").json()["panes"]


def test_the_viewport_endpoints_split_open_and_close_a_pane_for_real(client: TestClient) -> None:
    box = viewport(client)
    left, right = note(client, "Left"), note(client, "Right")
    url = f"/api/boxes/{box['uuid']}/viewports"

    assert [pane["node"] for pane in panes(client, box["uuid"])] == ["v1"]

    opened = client.post(f"{url}/open", json={"node": "v1", "child": left}).json()
    assert [pane["box"]["name"] for pane in opened["panes"]] == ["Left"]
    # each pane's box travels with the layout, so a viewport paints in one request
    assert opened["panes"][0]["box"]["uuid"] == left

    split = client.post(f"{url}/split", json={"node": "v1", "direction": "vertical"}).json()
    assert [pane["node"] for pane in split["panes"]] == ["v1", "v2"]
    assert split["closable"] is True

    client.post(f"{url}/open", json={"node": "v2", "child": right})
    assert sorted(
        child["name"] for child in client.get(f"/api/boxes/{box['uuid']}/children").json()
    ) == [
        "Left",
        "Right",
    ], "what a viewport shows is what it holds"

    closed = client.post(f"{url}/close", json={"node": "v2"}).json()
    assert [pane["box"]["name"] for pane in closed["panes"]] == ["Left"]
    assert [child["name"] for child in client.get(f"/api/boxes/{box['uuid']}/children").json()] == [
        "Left"
    ]
    assert client.get(f"/api/boxes/{right}").status_code == 200, "closed, not deleted"


def test_a_pane_being_closed_unfiles_its_box_rather_than_deleting_it(client: TestClient) -> None:
    """`the child is no longer a child` — and nothing more than that happens to it."""
    box = viewport(client)
    precious, kept = note(client, "Precious"), note(client, "Kept")
    url = f"/api/boxes/{box['uuid']}/viewports"
    client.post(f"{url}/open", json={"node": "v1", "child": precious})
    client.post(f"{url}/split", json={"node": "v1", "direction": "horizontal"})
    client.post(f"{url}/open", json={"node": "v2", "child": kept})
    closed = client.post(f"{url}/close", json={"node": "v1"}).json()

    assert [pane["child"] for pane in closed["panes"]] == [kept]
    assert client.get(f"/api/boxes/{precious}").status_code == 200, "the box is still a box"
    parents = client.get(f"/api/boxes/{precious}").json()["parents"]
    assert box["uuid"] not in {parent["uuid"] for parent in parents}, "unfiled from the viewport"
    still = client.get(f"/api/boxes/{kept}").json()["parents"]
    assert box["uuid"] in {parent["uuid"] for parent in still}, (
        "and the other pane keeps its filing"
    )


def test_the_last_pane_cannot_be_closed_through_the_api_either(client: TestClient) -> None:
    box = viewport(client)
    refused = client.post(f"/api/boxes/{box['uuid']}/viewports/close", json={"node": "v1"})
    assert refused.status_code == 422
    assert "last viewport" in refused.text


def test_the_geometry_lives_in_the_box_and_comes_back_from_the_store(client: TestClient) -> None:
    box = viewport(client)
    left, right = note(client, "Left"), note(client, "Right")
    url = f"/api/boxes/{box['uuid']}/viewports"
    client.post(f"{url}/open", json={"node": "v1", "child": left})
    client.post(f"{url}/split", json={"node": "v1", "direction": "horizontal"})
    client.post(f"{url}/open", json={"node": "v2", "child": right})
    client.post(f"{url}/ratio", json={"node": "s1", "ratio": 0.3})

    stored = client.store.get(box["name"])  # type: ignore[attr-defined]
    assert stored.config["viewports"]["tree"]["ratio"] == 0.3
    assert [pane["child"] for pane in panes(client, box["uuid"])] == [left, right]

    fresh = TestClient(create_app(client.store))  # a new app, same database
    assert [
        pane["node"] for pane in fresh.get(f"/api/boxes/{box['uuid']}/viewports").json()["panes"]
    ] == [
        "v1",
        "v2",
    ]


def test_a_raw_config_write_cannot_point_a_pane_at_somebody_elses_box(client: TestClient) -> None:
    box = viewport(client)
    stranger = note(client, "Stranger")
    refused = client.put(
        f"/api/boxes/{box['uuid']}",
        json={
            "config": {"viewports": {"kind": "tiling", "tree": {"node": "v1", "child": stranger}}}
        },
    )
    assert refused.status_code == 422, refused.text
    assert "not a child of this box" in refused.text


def test_a_box_opened_in_a_pane_arrives_with_its_own_settings(client: TestClient) -> None:
    """A pane shows the box, not a photograph of it.

    The layout answer used to carry a brief per pane — uuid, name, icon, type — and every view
    reads its settings out of `config`, so a grid in a pane opened as the default 4x3 with none of
    its children placed, and a desktop lost its icons. The fix is one line; the test is the reason
    it stays.
    """
    grid = client.post("/api/boxes", json={"name": "Panned grid", "box_type": "grid"}).json()
    child = client.post("/api/boxes", json={"name": "Placed", "box_type": "note"}).json()
    client.post(f"/api/boxes/{grid['uuid']}/children", json={"child": child["uuid"]})
    client.post(
        f"/api/boxes/{grid['uuid']}/grid/place",
        json={"child": child["uuid"], "cell": [1, 2]},
    )

    box = client.post(
        "/api/boxes", json={"name": "Grid holder", "box_type": "tiling-viewport"}
    ).json()
    opened = client.post(
        f"/api/boxes/{box['uuid']}/viewports/open",
        json={"node": "v1", "child": grid["uuid"]},
    )
    assert opened.status_code == 200, opened.text

    pane = opened.json()["panes"][0]
    # what a view reads: the box's own settings, and the boxes on either side of it
    assert pane["box"]["config"]["grid"]["cells"], "the grid keeps its placements"
    assert "resource" in pane["box"] and "metadata" in pane["box"]
    assert pane["box"]["parents"], "and the path line has something to walk"
    assert pane["box"]["children"], "the pane does not have to ask again to draw them"


def test_an_ordinary_box_is_not_a_viewport_and_says_so(client: TestClient) -> None:
    folder = client.post("/api/boxes", json={"name": "Folder", "box_type": "folder"}).json()
    answer = client.get(f"/api/boxes/{folder['uuid']}/viewports")
    assert answer.status_code == 415
    assert answer.json()["detail"]["error"] == "not_a_viewport"


def test_windows_move_resize_and_raise_through_the_api_and_come_back(client: TestClient) -> None:
    box = viewport(client, "Windows", "windowed-viewport")
    one, two = note(client, "One"), note(client, "Two")
    url = f"/api/boxes/{box['uuid']}/viewports"

    started = client.post(f"{url}/window", json={"child": one}).json()
    assert [pane["node"] for pane in started["panes"]] == ["v1"], (
        "the first window was already there"
    )
    assert started["panes"][0]["box"]["name"] == "One"
    added = client.post(f"{url}/window", json={"child": two}).json()
    assert [pane["node"] for pane in added["panes"]] == ["v1", "v2"]
    assert [pane["box"]["name"] for pane in added["panes"]] == ["One", "Two"]

    client.post(f"{url}/move", json={"node": "v2", "x": 320, "y": 200})
    client.post(f"{url}/resize", json={"node": "v2", "width": 700, "height": 460})
    client.post(f"{url}/raise", json={"node": "v2"})
    after = client.get(f"/api/boxes/{box['uuid']}/viewports").json()
    assert after["order"] == ["v1", "v2"]
    moved = next(pane for pane in after["panes"] if pane["node"] == "v2")
    assert (moved["x"], moved["y"], moved["width"], moved["height"]) == (320, 200, 700, 460)

    again = TestClient(create_app(client.store))
    back = again.get(f"/api/boxes/{box['uuid']}/viewports").json()
    assert [(pane["x"], pane["y"], pane["width"], pane["height"]) for pane in back["panes"]] == [
        (24, 24, 640, 420),
        (320, 200, 700, 460),
    ], "where the windows were, and how big they were, is the box's business, not the page's"
    assert back["order"] == ["v1", "v2"]


def test_a_box_whose_config_is_the_other_kinds_layout_is_refused_on_write(tmp_path: Path) -> None:
    """The type decides how the space is laid out, so a config cannot quietly change its mind."""
    store = BoxStore(tmp_path / "kind.db", content_root=tmp_path)
    try:
        with pytest.raises(InvalidBoxContent, match="windowed viewport"):
            store.add(
                Box(
                    name="Contradiction",
                    box_type="windowed-viewport",
                    config={
                        "viewports": {
                            "kind": "tiling",
                            "tree": {"node": "v1", "child": ""},
                        }
                    },
                )
            )
        # and the same box, described as windows, is accepted
        added = store.add(
            Box(
                name="Agreement",
                box_type="windowed-viewport",
                config={"viewports": {"kind": "windowed", "windows": [{"node": "v1"}]}},
            )
        )
        assert added.config["viewports"]["kind"] == "windowed"
    finally:
        store.close()


def test_opening_a_window_fills_the_empty_one_before_making_another(tmp_path: Path) -> None:
    """ "Open this in a window" should not leave an empty window behind."""
    layout = viewports.new_windowed()
    first, node = viewports.show_in_window(layout, "box-a")
    assert [(window.node, window.child) for window in first.windows] == [(node, "box-a")]
    second, _ = viewports.show_in_window(first, "box-b")
    assert [(window.node, window.child) for window in second.windows] == [
        ("v1", "box-a"),
        ("v2", "box-b"),
    ]
    third, fresh = viewports.show_in_window(second, "")
    assert [window.node for window in third.windows] == [
        "v1",
        "v2",
        fresh,
    ], "empty means another window"


# ---- window states: maximize, minimize, restore, and splitting a window -----


def _window(layout: viewports.Layout, node: str) -> viewports.Window:
    return next(window for window in layout.windows if window.node == node)


def test_maximizing_keeps_the_rectangle_underneath() -> None:
    """A maximized window is drawn over its own rect, not instead of it."""
    layout = viewports.new_windowed()
    before = _window(layout, "v1")
    maximized = viewports.maximize_window(layout, "v1")
    window = _window(maximized, "v1")
    assert window.state == "maximized"
    assert window.restore == (before.x, before.y, before.width, before.height)
    assert window.x == before.x and window.width == before.width  # the rect is untouched

    back = viewports.restore_window(maximized, "v1")
    assert _window(back, "v1").state == "normal"
    assert _window(back, "v1").restore is None
    assert (_window(back, "v1").x, _window(back, "v1").width) == (before.x, before.width)


def test_a_state_change_brings_that_window_to_the_front() -> None:
    """The window just acted on is the one being looked at, so the pile says so.

    Maximizing one that stays under three others has not maximized anything, and a minimized title
    buried in the pile cannot be clicked to bring the window back.
    """
    layout = viewports.new_windowed()
    layout, _second = viewports.open_window(layout, "")
    layout, _third = viewports.open_window(layout, "")
    assert layout.order[-1] != "v1"
    for changed in (
        viewports.maximize_window(layout, "v1"),
        viewports.minimize_window(layout, "v1"),
    ):
        assert changed.order[-1] == "v1"
        assert _window(changed, "v1").state in ("maximized", "minimized")


def test_minimizing_keeps_the_rect_and_a_maximized_history() -> None:
    """Minimize changes how it is drawn, not where it lives."""
    layout = viewports.maximize_window(viewports.new_windowed(), "v1")
    minimized = viewports.minimize_window(layout, "v1")
    window = _window(minimized, "v1")
    assert window.state == "minimized"
    assert window.width == 640 and window.height == 420  # the rect it had before maximizing
    assert window.restore == (24, 24, 640, 420)
    assert _window(viewports.restore_window(minimized, "v1"), "v1").state == "normal"


def test_moving_or_resizing_a_window_does_not_reset_its_state() -> None:
    """A layout write is not the person pressing the restore button.

    Move and resize rebuild the window; if they forgot the state, dragging a minimized title across
    the board would silently open it again, and a write that undoes a button cannot be diagnosed
    from the screen.
    """
    layout = viewports.minimize_window(viewports.new_windowed(), "v1")
    moved = viewports.move_window(layout, "v1", 300, 200)
    window = _window(moved, "v1")
    assert window.state == "minimized"
    assert (window.x, window.y) == (300, 200)

    sized = viewports.resize_window(layout, "v1", 700, 500)
    kept = _window(sized, "v1")
    assert kept.state == "minimized"
    assert (kept.width, kept.height) == (700, 500)

    maximized = viewports.resize_window(viewports.maximize_window(layout, "v1"), "v1", 800, 600)
    deep = _window(maximized, "v1")
    assert deep.state == "maximized"
    assert deep.restore == (24, 24, 640, 420)  # still the rect it will come back to


def test_a_window_split_lands_its_two_halves_where_the_one_was() -> None:
    """No neighbour gives up room in a pile of windows: a split divides this rectangle."""
    layout = viewports.new_windowed()  # (24, 24) 640x420
    halves, fresh = viewports.split_window(layout, "v1", "horizontal")
    left = _window(halves, "v1")
    right = _window(halves, fresh)
    assert (left.x, left.width) == (24, 320)
    assert (right.x, right.width) == (344, 320)
    assert left.height == right.height == 420
    assert right.child == ""  # the second thing to look at is chosen next, not guessed
    assert halves.order[-1] == fresh  # and it is on top, or the split looks like it did nothing

    stacked, second = viewports.split_window(halves, "v1", "vertical")
    upper = _window(stacked, "v1")
    lower = _window(stacked, second)
    assert (upper.y, upper.height) == (24, 210)
    assert (lower.y, lower.height) == (234, 210)


@pytest.mark.parametrize(
    ("width", "height", "edge"),
    [(150, 400, "wide"), (600, 100, "tall")],
)
def test_a_window_too_small_to_divide_says_which_way_to_grow_it(
    width: int, height: int, edge: str
) -> None:
    sized = viewports.resize_window(viewports.new_windowed(), "v1", width, height)
    with pytest.raises(viewports.ViewportError) as raised:
        viewports.split_window(sized, "v1", "horizontal" if edge == "wide" else "vertical")
    assert f"not {edge} enough" in str(raised.value)
    assert "drag its corner out first" in str(raised.value)


def test_splitting_a_window_says_restore_first_when_it_is_not_normal() -> None:
    """Half of a maximized board is not a window the person placed."""
    maximized = viewports.maximize_window(viewports.new_windowed(), "v1")
    with pytest.raises(viewports.ViewportError) as raised:
        viewports.split_window(maximized, "v1", "horizontal")
    assert "restore that window before splitting it" in str(raised.value)


def test_window_states_survive_the_config_round_trip() -> None:
    layout = viewports.maximize_window(viewports.new_windowed(), "v1")
    layout, _fresh = viewports.open_window(layout, "")
    layout = viewports.minimize_window(layout, "v1")
    read = viewports.layout_of(layout.as_config({}), "windowed")
    assert _window(read, "v1").state == "minimized"
    assert _window(read, "v1").restore == (24, 24, 640, 420)
    # a config from before any of this existed reads as normal windows, not as problems
    old = {
        "viewport": {"kind": "windowed", "windows": [{"node": "v1", "child": "", "x": 5, "y": 5}]}
    }
    plain = viewports.layout_of(old, "windowed")
    assert _window(plain, "v1").state == "normal"
    assert plain.problems == ()


def test_transparency_runs_from_invisible_to_solid_and_old_numbers_mean_what_they_did() -> None:
    """0 is invisible, 1 is solid, and nothing is the same as 0.

    The setting used to stop at 0.2 solid on the grounds that a window you cannot read is no use.
    That was the wrong lesson: the window you read is the focused one, which CSS keeps solid
    whatever the number says, so the rest of the pile may recede all the way out — that is what the
    control is for. The stored number is the same `windowed.opacity` an existing box already
    carries, so opening an old library neither flips its windows nor needs a repair pass.
    """
    assert viewports.opacity_of(None) == 0.0
    assert viewports.opacity_of({"windowed": {}}) == 0.0
    assert viewports.opacity_of({"windowed": {"opacity": "not a number"}}) == 0.0
    assert viewports.opacity_of({"windowed": {"opacity": 0}}) == 0.0
    assert viewports.opacity_of({"windowed": {"opacity": 0.4}}) == 0.4  # an old box, unchanged
    assert viewports.opacity_of({"windowed": {"opacity": 1}}) == 1.0
    assert viewports.opacity_of({"windowed": {"opacity": -3}}) == 0.0
    assert viewports.opacity_of({"windowed": {"opacity": 9}}) == 1.0

    assert viewports.transparency_problem({}) == ""
    assert viewports.transparency_problem({"windowed": {}}) == ""
    assert viewports.transparency_problem({"windowed": {"opacity": 0}}) == ""
    assert viewports.transparency_problem({"windowed": {"opacity": 1}}) == ""
    assert "between 0 and 1" in viewports.transparency_problem({"windowed": {"opacity": 1.4}})
    assert "between 0 and 1" in viewports.transparency_problem({"windowed": {"opacity": -0.2}})
    assert "a number" in viewports.transparency_problem({"windowed": {"opacity": "solid"}})
    assert viewports.transparency_problem({"windowed": 3}) == (
        "'windowed' must hold a settings block"
    )

