"""The mindmap: sides, sibling order, the walk down, and the endpoints that move nodes."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from boxganizer import mindmap
from boxganizer.errors import PlacementError
from boxganizer.models import Box
from boxganizer.store import BoxStore
from boxganizer.web.app import create_app


class Kid:
    """Just enough of a box for the layout functions, which only ever ask for a uuid."""

    def __init__(self, uuid: str) -> None:
        self.uuid = uuid


@pytest.fixture
def store(tmp_path: Path) -> Iterator[BoxStore]:
    box_store = BoxStore(tmp_path / "map.db")
    box_store.add(Box(name="root", box_type="root"))
    box_store.add(Box(name="cabinet", box_type="cabinet"))
    box_store.add(Box(name="Map", box_type="mindmap"))
    box_store.link("root", "Map")
    yield box_store
    box_store.close()


def branch(store: BoxStore, name: str, *, parent: str = "Map") -> Box:
    """A node: a box filed under another one."""
    box = store.add(Box(name=name, box_type="folder"))
    store.link(parent, box.uuid)
    return box


# -- sides --------------------------------------------------------------------


def test_the_first_branch_grows_right_and_they_alternate_from_there() -> None:
    assert mindmap.default_side(0) == mindmap.RIGHT
    assert mindmap.default_side(1) == mindmap.LEFT
    assert mindmap.default_side(2) == mindmap.RIGHT


def test_a_recorded_side_beats_the_default() -> None:
    config = mindmap.set_side({}, "abc", mindmap.LEFT)
    assert mindmap.side_of(config, "abc", index=0) == mindmap.LEFT
    assert mindmap.side_of(config, "other", index=0) == mindmap.RIGHT


def test_a_side_nobody_asked_for_is_refused() -> None:
    with pytest.raises(PlacementError):
        mindmap.set_side({}, "abc", "sideways")


def test_flipping_sends_a_branch_across_the_centre() -> None:
    config = mindmap.set_side({}, "abc", mindmap.RIGHT)
    assert mindmap.side_of(mindmap.flip_side(config, "abc"), "abc") == mindmap.LEFT


def test_junk_in_the_section_is_dropped_rather_than_trusted() -> None:
    config = {
        "mindmap": {
            "sides": {"a": "left", "b": "nowhere", "": "right"},
            "orders": {"p": "not a list", "q": [1, 2]},
        }
    }
    assert mindmap.sides_of(config) == {"a": "left"}
    # numbers are turned into text rather than dropped: JSON holds no uuids but strings,
    # and a hand-edited file writes them as anything it likes.
    assert mindmap.orders_of(config) == {"q": ["1", "2"]}


def test_pinning_leaves_a_written_side_alone() -> None:
    config = mindmap.set_side({}, "a", mindmap.LEFT)
    pinned = mindmap.pin_sides(config, ["a", "b", "c"])
    # "a" keeps the side it was given; the rest take the default for where they stand.
    assert mindmap.sides_of(pinned) == {"a": "left", "b": "left", "c": "right"}


# -- order --------------------------------------------------------------------


def test_a_recorded_order_wins_over_the_order_boxes_arrived_in() -> None:
    config = mindmap.set_order({}, "parent", ["c", "a", "b"])
    got = [kid.uuid for kid in mindmap.ordered([Kid("a"), Kid("b"), Kid("c")], "parent", config)]
    assert got == ["c", "a", "b"]


def test_a_child_nobody_ordered_comes_last_and_is_not_written_down() -> None:
    config = mindmap.set_order({}, "parent", ["a"])
    got = [kid.uuid for kid in mindmap.ordered([Kid("a"), Kid("new")], "parent", config)]
    assert got == ["a", "new"]
    assert mindmap.orders_of(config) == {"parent": ["a"]}


def test_a_sibling_moves_up_and_down_its_own_side_only() -> None:
    config: dict[str, object] = {}
    for index, uuid in enumerate(["one", "two", "three", "four"]):
        config = mindmap.set_side(config, uuid, mindmap.default_side(index))
    config = mindmap.set_order(config, "p", ["one", "two", "three", "four"])
    # "four" (left) moves up: its neighbour is "two" (left), never "three" (right)
    everyone = [Kid(uuid) for uuid in ["one", "two", "three", "four"]]
    moved = mindmap.move_sibling(config, "p", "four", everyone, -1)
    assert mindmap.orders_of(moved)["p"] == ["one", "four", "three", "two"]


def test_a_sibling_at_the_end_of_its_side_says_so_instead_of_wrapping() -> None:
    config = mindmap.set_order({}, "p", ["one", "two"])
    with pytest.raises(PlacementError, match="already at that end"):
        mindmap.move_sibling(config, "p", "one", [Kid("one"), Kid("two")], -1)


def test_a_box_that_is_not_a_child_cannot_be_moved_among_them() -> None:
    with pytest.raises(PlacementError, match="only a child"):
        mindmap.move_sibling({}, "p", "stranger", [Kid("one")], 1)


# -- placeholders -------------------------------------------------------------


def test_a_node_nobody_wrote_into_can_be_replaced() -> None:
    assert mindmap.placeholder(Box(name="new idea", box_type="folder"))
    assert mindmap.placeholder(Box(name="new idea 7", box_type="folder"))


def test_a_node_someone_has_used_cannot_be_replaced() -> None:
    written = Box(name="lighting", box_type="folder")
    described = Box(name="new idea", box_type="folder", description="under the stairs")
    resourced = Box(name="new idea", box_type="folder", resource_uri="/tmp/x")
    parented = Box(name="new idea", box_type="folder")
    assert not mindmap.placeholder(written)
    assert not mindmap.placeholder(described)
    assert not mindmap.placeholder(resourced)
    assert not mindmap.placeholder(parented, siblings=2)


def test_three_presses_of_a_key_need_three_names() -> None:
    assert mindmap.next_name(set()) == "new idea"
    assert mindmap.next_name({"new idea"}) == "new idea 2"
    assert mindmap.next_name({"new idea", "new idea 2"}) == "new idea 3"


# -- the walk down ------------------------------------------------------------


def test_a_branch_carries_its_side_down_with_it(store: BoxStore) -> None:
    deep = branch(store, "deep")
    lower = branch(store, "lower", parent=deep.name)
    tree = mindmap.node(store, "Map")
    branch_node = mindmap.find(tree, deep.uuid)
    assert branch_node is not None
    assert mindmap.find(branch_node, lower.uuid)["side"] == branch_node["side"]


def test_a_box_inside_itself_is_drawn_once_and_says_so(store: BoxStore) -> None:
    node = branch(store, "ouroboros")
    store.link(node.uuid, node.uuid)  # the graph allows this
    tree = mindmap.node(store, "Map")
    drawn = mindmap.find(tree, node.uuid)
    assert drawn is not None
    assert drawn["children"][0]["truncated"] is True


def test_a_cycle_between_two_nodes_does_not_run_forever(store: BoxStore) -> None:
    first = branch(store, "first")
    second = branch(store, "second")
    store.link(first.uuid, second.uuid)
    store.link(second.uuid, first.uuid)
    tree = mindmap.node(store, "Map")
    assert mindmap.parent_index(tree)[second.uuid] == first.uuid
    inner = mindmap.find(tree, second.uuid)
    assert inner is not None
    assert mindmap.find(inner, first.uuid)["truncated"] is True


def test_the_wider_map_is_still_drawn_but_stops_somewhere(store: BoxStore) -> None:
    for index in range(mindmap.MAX_NODES + 25):
        branch(store, f"idea {index}")
    tree = mindmap.node(store, "Map")

    def counted(node: dict[str, object]) -> int:
        children = node.get("children") or []
        return 1 + sum(counted(child) for child in children)  # type: ignore[arg-type]

    assert counted(tree) <= mindmap.MAX_NODES
    assert tree["truncated"] is True
    assert tree["omitted"] > 0  # and the page is told how much it did not get


def test_the_parent_of_a_node_is_the_one_it_is_drawn_under(store: BoxStore) -> None:
    left = branch(store, "left parent")
    right = branch(store, "right parent")
    shared = branch(store, "shared child")
    store.link(left.uuid, shared.uuid)
    store.link(right.uuid, shared.uuid)  # two parents, so two places in the picture
    tree = mindmap.node(store, "Map")
    parents = mindmap.parent_index(tree)
    assert parents[shared.uuid] in {left.uuid, right.uuid}
    assert mindmap.line_up(mindmap.find(tree, left.uuid)) == [shared.uuid]


# -- the two hierarchy moves --------------------------------------------------


def test_moving_in_hangs_a_node_off_its_grandparent(store: BoxStore) -> None:
    outer = branch(store, "outer")
    middle = branch(store, "middle", parent=outer.name)
    inner = branch(store, "inner", parent=middle.name)
    tree, note = mindmap.move_towards_centre(store, "Map", inner.uuid)
    assert note == "moved up the hierarchy"
    assert mindmap.parent_index(tree)[inner.uuid] == outer.uuid
    # it lands just after the parent it came from, not at the end of the row
    assert mindmap.line_up(mindmap.find(tree, outer.uuid)) == [middle.uuid, inner.uuid]


def test_a_branch_of_the_centre_changes_side_instead_of_moving_in(store: BoxStore) -> None:
    only = branch(store, "only branch")
    before = mindmap.side_of(store.get("Map").config, only.uuid, index=0)
    tree, note = mindmap.move_towards_centre(store, "Map", only.uuid)
    assert note == "changed side"
    assert mindmap.side_of(store.get("Map").config, only.uuid, index=0) != before
    assert mindmap.find(tree, only.uuid) is not None


def test_moving_out_hangs_a_node_off_the_sibling_above_it(store: BoxStore) -> None:
    first = branch(store, "first")
    second = branch(store, "second")
    tree, note = mindmap.move_away_from_centre(store, "Map", second.uuid)
    assert note == "moved down the hierarchy"
    assert mindmap.parent_index(tree)[second.uuid] == first.uuid
    assert mindmap.line_up(mindmap.find(tree, first.uuid)) == [second.uuid]


def test_a_first_sibling_has_nothing_above_it_to_hang_under(store: BoxStore) -> None:
    first = branch(store, "first")
    branch(store, "second")
    with pytest.raises(PlacementError, match="nothing above it"):
        mindmap.move_away_from_centre(store, "Map", first.uuid)


def test_the_centre_cannot_be_moved_anywhere(store: BoxStore) -> None:
    with pytest.raises(PlacementError, match="nothing above it"):
        mindmap.move_towards_centre(store, "Map", store.get("Map").uuid)
    with pytest.raises(PlacementError, match="not part of this map"):
        mindmap.move_away_from_centre(store, "Map", store.get("cabinet").uuid)


# -- the endpoints ------------------------------------------------------------


@pytest.fixture
def client(store: BoxStore) -> Iterator[TestClient]:
    with TestClient(create_app(store)) as test_client:
        yield test_client


def test_the_map_endpoint_draws_the_tree(client: TestClient) -> None:
    node = branch(client.app.state.store, "branch")  # type: ignore[union-attr]
    answer = client.get("/api/boxes/Map/mindmap")
    assert answer.status_code == 200
    tree = answer.json()["map"]
    assert tree["name"] == "Map"
    assert [child["name"] for child in tree["children"]] == ["branch"]
    assert tree["children"][0]["uuid"] == node.uuid


def test_only_a_box_that_draws_maps_answers(client: TestClient) -> None:
    assert client.get("/api/boxes/cabinet/mindmap").status_code == 415
    assert client.get("/api/boxes/Map/mindmap").status_code == 200


def test_a_new_node_is_a_box_filed_under_the_map(client: TestClient) -> None:
    answer = client.post("/api/boxes/Map/mindmap/node", json={})
    assert answer.status_code == 201
    assert answer.json()["node"]["name"] == "new idea"
    assert [child["name"] for child in answer.json()["map"]["children"]] == ["new idea"]


def test_pressing_the_key_three_times_makes_three_nodes(client: TestClient) -> None:
    for _ in range(3):
        assert client.post("/api/boxes/Map/mindmap/node", json={}).status_code == 201
    store = client.app.state.store
    names = [child.name for child in store.children("Map")]  # type: ignore[union-attr]
    assert names == ["new idea", "new idea 2", "new idea 3"]


def test_a_named_node_keeps_the_name_it_was_given(client: TestClient) -> None:
    answer = client.post("/api/boxes/Map/mindmap/node", json={"name": "plumbing"})
    assert answer.json()["node"]["name"] == "plumbing"
    # and a name already taken is refused, not quietly changed
    assert client.post("/api/boxes/Map/mindmap/node", json={"name": "plumbing"}).status_code == 409


def test_a_node_can_be_asked_for_beside_a_sibling(client: TestClient) -> None:
    store = client.app.state.store
    first = branch(store, "first")  # type: ignore[union-attr]
    branch(store, "second")
    answer = client.post("/api/boxes/Map/mindmap/node", json={"after": first.uuid})
    names = [child["name"] for child in answer.json()["map"]["children"]]
    assert names.index(answer.json()["node"]["name"]) == names.index("first") + 1
    assert "second" in names


def test_a_branch_keeps_its_side_when_a_sibling_is_reordered(client: TestClient) -> None:
    # made through the map, as the page makes them: that is when the sides are written
    # down, and a reorder is exactly the thing that must not disturb them.
    made: list[str] = []
    for name in ("one", "two", "three", "four"):
        answer = client.post("/api/boxes/Map/mindmap/node", json={"name": name})
        made.append(answer.json()["node"]["uuid"])
    before = {
        child["name"]: child["side"]
        for child in client.get("/api/boxes/Map/mindmap").json()["map"]["children"]
    }
    answer = client.post("/api/boxes/Map/mindmap/order", json={"child": made[3], "delta": -1})
    assert answer.status_code == 200
    after = {child["name"]: child["side"] for child in answer.json()["map"]["children"]}
    assert after == before
    order = [child["name"] for child in answer.json()["map"]["children"]]
    assert order == ["one", "four", "three", "two"]


def test_a_side_can_be_flipped_by_name_or_by_asking(client: TestClient) -> None:
    store = client.app.state.store
    node = branch(store, "branch")  # type: ignore[union-attr]
    left = client.post("/api/boxes/Map/mindmap/side", json={"child": node.uuid, "side": "left"})
    assert left.json()["map"]["children"][0]["side"] == "left"
    flipped = client.post("/api/boxes/Map/mindmap/side", json={"child": node.uuid, "flip": True})
    assert flipped.json()["map"]["children"][0]["side"] == "right"
    both = client.post(
        "/api/boxes/Map/mindmap/side", json={"child": node.uuid, "side": "left", "flip": True}
    )
    assert both.status_code == 422


def test_a_node_outside_the_map_is_refused_rather_than_moved(client: TestClient) -> None:
    answer = client.post("/api/boxes/Map/mindmap/side", json={"child": "cabinet", "flip": True})
    assert answer.status_code == 409
    assert answer.json()["detail"]["error"] == "not_a_node"


def test_the_hierarchy_endpoint_reports_which_way_it_went(client: TestClient) -> None:
    store = client.app.state.store
    outer = branch(store, "outer")  # type: ignore[union-attr]
    inner = branch(store, "inner", parent=outer.name)
    out = client.post(
        "/api/boxes/Map/mindmap/hierarchy", json={"child": inner.uuid, "direction": "in"}
    )
    assert out.status_code == 200
    assert out.json()["note"] == "moved up the hierarchy"
    bad = client.post(
        "/api/boxes/Map/mindmap/hierarchy", json={"child": inner.uuid, "direction": "sideways"}
    )
    assert bad.status_code == 422


def test_a_box_dropped_on_an_empty_node_takes_its_place(client: TestClient) -> None:
    store = client.app.state.store
    placeholder = branch(store, "new idea")  # type: ignore[union-attr]
    store.add(Box(name="Roof plan", box_type="folder"))
    answer = client.post(
        "/api/boxes/Map/mindmap/fill", json={"node": placeholder.uuid, "with": "Roof plan"}
    )
    assert answer.status_code == 200
    assert answer.json()["replaced"] is True
    assert answer.json()["removed"] == "new idea"
    children = answer.json()["map"]["children"]
    assert [child["name"] for child in children] == ["Roof plan"]
    assert children[0]["box_type"] == "folder"


def test_a_box_dropped_on_a_used_node_is_filed_inside_it(client: TestClient) -> None:
    store = client.app.state.store
    used = branch(store, "kitchen")  # type: ignore[union-attr]
    store.add(Box(name="Tiles", box_type="folder"))
    answer = client.post("/api/boxes/Map/mindmap/fill", json={"node": "kitchen", "with": "Tiles"})
    assert answer.json()["replaced"] is False
    tree = answer.json()["map"]
    assert [child["name"] for child in mindmap.find(tree, used.uuid)["children"]] == ["Tiles"]
    assert store.get("kitchen") is not None  # nobody's thought was thrown away


def test_a_node_can_be_taken_out_of_the_map_without_being_deleted(client: TestClient) -> None:
    store = client.app.state.store
    node = branch(store, "someday")  # type: ignore[union-attr]
    store.unlink("Map", node.uuid)
    assert "someday" in [box.name for box in store.list_boxes()]
    assert mindmap.find(mindmap.node(store, "Map"), node.uuid) is None


def test_the_mindmap_type_is_not_marked_unbuilt() -> None:
    from boxganizer import boxtypes

    kind = boxtypes.get("mindmap")
    assert kind.planned == ""
    assert kind.maps_nodes is True
    assert kind.allows_children is True
