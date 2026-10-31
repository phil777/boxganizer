"""Tags: names, hierarchy, derived colour, and the rows that hold them."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from boxganizer import tags
from boxganizer.models import Box
from boxganizer.store import SCHEMA_VERSION, BoxStore
from boxganizer.web.app import create_app

# -- the hierarchy -----------------------------------------------------------


def hierarchy_of(*pairs: tuple[str, str | None]) -> tags.Hierarchy:
    return tags.Hierarchy.build([tags.Tag(name=name, parent=parent) for name, parent in pairs])


SAMPLE = (("Home", None), ("Kitchen", "Home"), ("Boiler", "Kitchen"), ("Work", None))


def test_a_tag_is_implicitly_tagged_with_everything_above_it() -> None:
    """The whole point of the hierarchy, stated once, in one place.

    Nothing stores this: a box given `boiler` means `kitchen` and `home` because that is what
    the words say, and storing the inheritance as well would be a second copy that a move in
    the tree would leave behind on every box in the library.
    """
    built = hierarchy_of(*SAMPLE)
    assert built.implied(["Boiler"]) == ("Boiler", "Kitchen", "Home")
    assert built.implied(["Home"]) == ("Home",)
    # one tag named twice, and a child named before its parent, both collapse to one list
    assert built.implied(["Boiler", "Kitchen", "Boiler"]) == ("Boiler", "Kitchen", "Home")
    assert built.ancestors("Home") == ()
    assert [tag.name for tag in built.ancestors("Boiler")] == ["Kitchen", "Home"]
    assert built.path("Boiler") == ("Home", "Kitchen", "Boiler")
    assert built.depth("Boiler") == 2
    assert built.root_of("Boiler") == "Home"


def test_an_unknown_name_survives_rather_than_being_dropped() -> None:
    """A box must not silently lose something its owner typed because a row is missing."""
    built = hierarchy_of(*SAMPLE)
    assert built.implied(["Mystery"]) == ("Mystery",)


def test_a_hierarchy_with_a_cycle_stops_instead_of_hanging() -> None:
    """A database written by hand can hold `a → b → a`, and a page must still draw.

    The tree is rendered by walking up; without a stop the walk is infinite and the symptom is
    a browser tab that never paints — which no error message would ever explain.
    """
    looped = hierarchy_of(("A", "B"), ("B", "A"))
    assert [tag.name for tag in looped.ancestors("A")] == ["B"]
    assert looped.implied(["A"]) == ("A", "B")


def test_a_parent_that_no_longer_exists_leaves_the_child_a_root() -> None:
    built = tags.Hierarchy.build([tags.Tag("Kept"), tags.Tag("Orphan", parent="Deleted")])
    assert built.get("Orphan") is not None
    assert built.get("Orphan").parent is None
    assert built.path("Orphan") == ("Orphan",)
    assert sorted(built.names()) == ["Kept", "Orphan"]


def test_moving_a_tag_inside_its_own_branch_is_refused() -> None:
    """A cycle cannot be drawn, so it is refused at the write rather than at the render."""
    built = hierarchy_of(*SAMPLE)
    assert built.would_cycle("Home", "Boiler") is True
    assert built.would_cycle("Kitchen", "Boiler") is True
    assert built.would_cycle("Boiler", "Boiler") is True
    # the legal moves, including back to the top
    assert built.would_cycle("Boiler", "Home") is False
    assert built.would_cycle("Boiler", "Work") is False
    assert built.would_cycle("Boiler", None) is False


def test_descendants_are_everything_below_a_tag() -> None:
    built = hierarchy_of(*SAMPLE, ("Garden", "Home"))
    assert set(built.descendants_of("Home")) == {"Kitchen", "Boiler", "Garden"}
    assert built.descendants_of("Boiler") == ()


# -- names -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(" home ", "home"), ("HOME", "home"), ("Home  Kitchen", "home kitchen")],
)
def test_two_names_that_look_alike_are_one_tag(raw: str, expected: str) -> None:
    assert tags.canonical(raw) == expected


def test_a_path_is_read_as_a_branch_with_the_spaces_taken_out() -> None:
    """Someone typing a path types the spaces, and ` Boilers` as a tag name is a surprise."""
    assert tags.split_path(" home / kitchen / boiler ") == ("home", "kitchen", "boiler")
    assert tags.split_path("home//kitchen") == ("home", "kitchen")


@pytest.mark.parametrize(
    ("bad", "reason"),
    [
        ("", "needs a name"),
        ("  ", "needs a name"),
        ("a/b", "looks like a path"),
        ("*", "filters"),
        ("none", "filters"),
        ("x,y", "may not contain"),
        ("x" * 60, "too long"),
    ],
)
def test_a_name_that_would_break_the_interfaces_is_refused(bad: str, reason: str) -> None:
    with pytest.raises(tags.TagError) as caught:
        tags.validate(bad)
    assert reason in str(caught.value)


# -- colour ------------------------------------------------------------------


def test_a_branch_shares_one_hue_and_lightens_as_it_goes_down() -> None:
    """The hierarchy should be visible without reading it."""
    built = hierarchy_of(*SAMPLE)
    home = tags.colour_for(built, "Home")
    kitchen = tags.colour_for(built, "Kitchen")
    boiler = tags.colour_for(built, "Boiler")
    assert home.split()[0] == kitchen.split()[0] == boiler.split()[0], "one hue per branch"
    assert _lightness(home) < _lightness(kitchen) < _lightness(boiler)
    # a different root is a different hue, and the same root is always the same hue
    assert tags.colour_for(built, "Work").split()[0] != home.split()[0]
    assert tags.colour_for(built, "Home") == home


def _lightness(colour: str) -> int:
    return int(colour.split("%")[1].rstrip(")"))


@pytest.mark.parametrize(
    ("hex_colour", "hue"),
    [("#ff0000", 0), ("#00ff00", 120), ("#0000ff", 240), ("#ffff00", 60), ("#ff7f00", 30)],
)
def test_a_chosen_colour_keeps_its_own_hue(hex_colour: str, hue: int) -> None:
    """Yellow in, yellow out.

    The first version of this conversion mixed 0..255 and 0..1 channels, which left the pure
    colours looking right and turned yellow into cyan — the worst shape a colour bug can take,
    because three quarters of the palette seems fine.
    """
    built = tags.Hierarchy.build([tags.Tag("t", colour=hex_colour)])
    assert int(tags.colour_for(built, "t").split()[0].removeprefix("hsl(")) == hue


@pytest.mark.parametrize(
    ("colour", "ink"),
    [
        ("#ffff00", "#111111"),
        ("#ffffff", "#111111"),
        ("#000080", "#ffffff"),
        ("#111111", "#ffffff"),
    ],
)
def test_the_ink_on_a_chip_is_the_one_that_can_be_read(colour: str, ink: str) -> None:
    """Perceived brightness, not HSL lightness: pure yellow is lightness 50 and blinding."""
    built = tags.Hierarchy.build([tags.Tag("t", colour=colour)])
    painted = tags.colour_for(built, "t")
    assert tags.text_colour_for(painted) == ink


def test_a_colour_neither_this_module_nor_a_browser_understands_is_refused() -> None:
    """The text colour is derived from the background, so an unparsable one means white on white."""
    with pytest.raises(tags.TagError, match="not a colour"):
        tags.validate_colour("nonsense")
    assert tags.validate_colour("#abc") == "#abc"


# -- the store ---------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> Any:
    held = BoxStore(tmp_path / "tags.db", content_root=tmp_path)
    try:
        yield held
    finally:
        held.close()


def test_the_schema_now_carries_tags() -> None:
    assert SCHEMA_VERSION == 9


def test_tags_are_unique_whatever_case_they_were_typed_in(store: BoxStore) -> None:
    store.add_tag("Home")
    with pytest.raises(tags.TagError, match="already a tag"):
        store.add_tag("home")


def test_a_parent_must_exist_before_a_child_can_sit_in_it(store: BoxStore) -> None:
    with pytest.raises(tags.TagError, match="no tag"):
        store.add_tag("Boiler", parent="Kitchen")


def test_a_path_creates_the_branch_it_names(store: BoxStore) -> None:
    """Typing `home/kitchen/boiler` should make a branch, not three commands."""
    made = store.add_tag_path("home/kitchen/boiler")
    assert made.name == "boiler"
    assert store.hierarchy().path("boiler") == ("home", "kitchen", "boiler")
    # an existing level is joined, not duplicated
    again = store.add_tag_path("home/kitchen/sinks")
    assert again.name == "sinks"
    assert store.hierarchy().path("sinks") == ("home", "kitchen", "sinks")


def test_a_tag_path_has_a_depth(store: BoxStore) -> None:
    with pytest.raises(tags.TagError, match="levels"):
        store.add_tag_path("a/b/c/d/e/f/g")
    store.add_tag_path("a/b/c/d/e/f")
    with pytest.raises(tags.TagError, match="levels"):
        store.add_tag("g", parent="f")


def test_a_box_carries_the_tags_it_was_given_and_means_the_ones_above(
    store: BoxStore,
) -> None:
    store.add_tag_path("home/kitchen/boiler")
    boiler = store.add(Box(name="Boiler report", box_type="note"))
    desk = store.add(Box(name="Desk note", box_type="note"))
    assert store.assign_tag(boiler, "boiler") == ("boiler",)
    # the path created `home` in lower case, and asking for "Home" joins that tag: the name
    # is the identity, and its case is the owner's to display, not a difference to store
    assert store.assign_tag(desk, "Home") == ("home",)
    assert store.box_tags(boiler) == ("boiler",)
    assert store.hierarchy().implied(store.box_tags(boiler)) == ("boiler", "kitchen", "home")
    # a name nobody has used is invented by the person typing it, not refused at them
    assert store.assign_tag(desk, "urgent") == ("home", "urgent")
    # and typing the same word in another case joins the tag that exists rather than starting
    # a second one that looks identical in the list
    assert store.assign_tag(desk, "HOME") == ("home", "urgent")
    assert store.tag_counts()["home"] == 1


def test_a_filter_on_a_parent_finds_the_children_too(store: BoxStore) -> None:
    """Otherwise the hierarchy is a drawing and `tag:home` is a trap."""
    store.add_tag_path("home/kitchen/boiler")
    direct = store.add(Box(name="Direct", box_type="note"))
    deep = store.add(Box(name="Deep", box_type="note"))
    store.assign_tag(direct, "home")
    store.assign_tag(deep, "boiler")
    assert sorted(store.boxes_with_tag("home")) == sorted([direct.uuid, deep.uuid])
    assert store.boxes_with_tag("boiler") == [deep.uuid]
    assert store.boxes_with_tag("home", descendants=False) == [direct.uuid]


def test_two_counts_because_two_questions_are_being_asked(store: BoxStore) -> None:
    """What was typed onto a box, and what a filter would find."""
    store.add_tag_path("home/kitchen")
    box = store.add(Box(name="Any", box_type="note"))
    store.assign_tag(box, "kitchen")
    assert store.tag_counts() == {"home": 0, "kitchen": 1}
    assert store.tag_counts_including_inherited() == {"home": 1, "kitchen": 1}


def test_renaming_a_tag_keeps_every_box_holding_it(store: BoxStore) -> None:
    store.add_tag("Gardening")
    box = store.add(Box(name="Shed", box_type="note"))
    store.assign_tag(box, "Gardening")
    store.rename_tag("gardening", "Garden")
    assert store.box_tags(box) == ("Garden",)
    # renaming onto its own name in another case is nothing to refuse
    assert store.rename_tag("Garden", "garden  ").name == "garden"
    store.add_tag("Office")
    with pytest.raises(tags.TagError, match="already a tag"):
        store.rename_tag("garden", "office")


def test_deleting_a_tag_moves_its_children_up_instead_of_taking_them_with_it(
    store: BoxStore,
) -> None:
    """A delete that quietly empties hundreds of boxes is not undoable from a dialog.

    `home` goes; `kitchen` and `tools` become roots and keep every box they had; only the boxes
    that named `home` itself stop carrying it.
    """
    store.add_tag_path("home/kitchen/boiler")
    store.add_tag_path("home/tools/wrench")
    box = store.add(Box(name="Rates", box_type="note"))
    store.assign_tag(box, "home")
    deep = store.add(Box(name="Deep note", box_type="note"))
    store.assign_tag(deep, "boiler")

    done = store.delete_tag("home")
    assert done["tag"] == "home"
    assert set(done["reparented"]) == {"kitchen", "tools"}
    assert done["boxes"] == 1
    assert store.box_tags(box) == ()
    assert store.box_tags(deep) == ("boiler",)
    assert store.hierarchy().get("kitchen").parent is None
    assert store.hierarchy().path("boiler") == ("kitchen", "boiler")


def test_moving_a_tag_inside_itself_is_refused_before_the_write(store: BoxStore) -> None:
    store.add_tag_path("home/kitchen/boiler")
    with pytest.raises(tags.TagError, match="inside itself"):
        store.move_tag("home", "boiler")
    moved = store.move_tag("boiler", None)
    assert moved.parent is None
    assert store.hierarchy().path("boiler") == ("boiler",)
    assert store.hierarchy().path("kitchen") == ("home", "kitchen")


def test_a_box_can_be_set_to_a_whole_tag_set_at_once(store: BoxStore) -> None:
    store.add_tag("Home")
    box = store.add(Box(name="Any", box_type="note"))
    store.assign_tag(box, "Home")
    assert store.set_box_tags(box, ["Office", "urgent"]) == ("Office", "urgent")
    assert store.tag_counts()["home"] == 0
    assert store.set_box_tags(box, []) == ()


def test_a_tag_is_taken_off_only_the_box_it_was_given_to(store: BoxStore) -> None:
    store.add_tag("Home")
    first = store.add(Box(name="First", box_type="note"))
    second = store.add(Box(name="Second", box_type="note"))
    store.assign_tag(first, "Home")
    store.assign_tag(second, "Home")
    assert store.unassign_tag(first, "home") == ()
    assert store.box_tags(second) == ("Home",)


# -- the API -----------------------------------------------------------------


@pytest.fixture
def client(store: BoxStore) -> Any:
    with TestClient(create_app(store)) as test_client:
        yield test_client


def test_the_tag_list_carries_the_colour_a_chip_needs(client: TestClient, store: BoxStore) -> None:
    """Derived server-side, so the chip, the filter and the manage window cannot disagree."""
    store.add_tag_path("home/kitchen/boiler")
    tags_ = client.get("/api/tags").json()["tags"]
    by_name = {item["name"]: item for item in tags_}
    assert by_name["boiler"]["path"] == ["home", "kitchen", "boiler"]
    assert by_name["boiler"]["colour"].startswith(by_name["home"]["colour"].split()[0])
    assert by_name["boiler"]["text"] in {"#ffffff", "#111111"}
    assert by_name["boiler"]["explicit_colour"] is None


def test_a_box_reports_what_it_carries_and_what_that_means(
    client: TestClient, store: BoxStore
) -> None:
    store.add_tag_path("home/kitchen/boiler")
    box = store.add(Box(name="Report", box_type="note"))
    store.assign_tag(box, "boiler")
    body = client.get(f"/api/boxes/{box.uuid}/tags").json()
    assert body["explicit"] == ["boiler"]
    assert body["inherited"] == ["kitchen", "home"]
    # the chip says whether it can be removed here, which is the difference between a cross
    # that works and a cross that does nothing
    assert [chip["explicit"] for chip in body["chips"]] == [True, False, False]


def test_creating_and_editing_tags_through_the_api(client: TestClient) -> None:
    assert client.post("/api/tags", json={"name": "home/kitchen"}).status_code == 201
    assert client.patch("/api/tags/kitchen", json={"colour": "#226688"}).status_code == 200
    assert client.patch("/api/tags/kitchen", json={"colour": "nonsense"}).status_code == 422
    moved = client.patch("/api/tags/kitchen", json={"parent": ""})
    assert moved.status_code == 200
    assert client.post("/api/tags", json={"name": "boiler", "parent": "kitchen"}).status_code == 201
    cycle = client.patch("/api/tags/kitchen", json={"parent": "boiler"})
    assert cycle.status_code == 422
    assert cycle.json()["detail"]["error"] == "bad_tag"
    assert "inside itself" in cycle.json()["detail"]["reason"]
    gone = client.delete("/api/tags/kitchen").json()
    assert gone["reparented"] == ["boiler"]


def test_a_boxs_tags_are_written_whole(client: TestClient, store: BoxStore) -> None:
    store.add_tag("Home")
    box = store.add(Box(name="Any", box_type="note"))
    written = client.put(f"/api/boxes/{box.uuid}/tags", json={"tags": ["Home", "new-thing"]})
    assert written.status_code == 200
    assert sorted(written.json()["explicit"]) == ["Home", "new-thing"]
    assert client.put(f"/api/boxes/{box.uuid}/tags", json={"tags": []}).json()["explicit"] == []


def test_a_tag_can_be_given_and_taken_one_at_a_time(client: TestClient, store: BoxStore) -> None:
    """The chip line needs a delta, not a whole-set write.

    Rebuilding the set from a snapshot is how a cross on one chip silently deletes a tag that
    was added in another window a second earlier — which is what the first version did, and what
    a page with three ways to edit the same box will keep doing.
    """
    store.add_tag("Home")
    box = store.add(Box(name="Any", box_type="note"))
    assert client.post(f"/api/boxes/{box.uuid}/tags", json={"name": "Home"}).json()["explicit"] == [
        "Home"
    ]
    assert client.post(f"/api/boxes/{box.uuid}/tags", json={"name": "urgent"}).json()[
        "explicit"
    ] == [
        "Home",
        "urgent",
    ]
    gone = client.delete(f"/api/boxes/{box.uuid}/tags/home").json()
    assert gone["explicit"] == ["urgent"]
    # an inherited tag has no row of its own to remove, and asking removes nothing
    store.assign_tag(box, "Home")
    assert client.delete(f"/api/boxes/{box.uuid}/tags/Home").json()["explicit"] == ["urgent"]
    refused = client.post(f"/api/boxes/{box.uuid}/tags", json={"name": "a/b/c/d/e/f/g"})
    assert refused.status_code == 422
    assert refused.json()["detail"]["error"] == "bad_tag"


def test_a_tag_change_re_lists_the_dynamic_folders_that_ask_about_tags(tmp_path: Path) -> None:
    """A folder that filters on a tag has to hear about the tag tree moving.

    Nothing here calls the box-sync path when a tag changes — a tag is not a box — so without an
    explicit re-list the folder keeps a box it no longer matches and misses the one it now does,
    until some unrelated edit happens to stumble past and fix it. That is a folder whose contents
    depend on luck.
    """
    from boxganizer import search as indexing  # noqa: F401  (kept out of this test on purpose)

    store = BoxStore(tmp_path / "tags-folder.db", content_root=tmp_path)
    try:
        store.add_tag_path("home/kitchen")
        folder = store.add(
            Box(
                name="Kitchen things",
                box_type="dynamic-folder",
                config={"filter": {"expression": "tag:home"}},
            )
        )
        pot = store.add(Box(name="Pot", box_type="note"))
        pan = store.add(Box(name="Pan", box_type="note"))

        def members() -> list[str]:
            return sorted(child.name for child in store.children(folder.uuid))

        assert members() == []

        store.assign_tag(pot, "kitchen")  # under home, so the folder must want it
        assert members() == ["Pot"]

        store.assign_tag(pan, "home")
        assert members() == ["Pan", "Pot"]

        store.rename_tag("kitchen", "bathroom")  # still below home: nothing leaves
        assert members() == ["Pan", "Pot"]

        store.move_tag("bathroom", None)  # now a root tag, out of the home branch
        assert members() == ["Pan"]

        store.set_box_tags(pan, [])  # unassigning takes it out the same instant
        assert members() == []

        store.assign_tag(pan, "home")
        assert members() == ["Pan"]
        store.delete_tag("home")
        assert members() == []
    finally:
        store.close()
