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
