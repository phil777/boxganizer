"""The box type hierarchy must match the source document, and enforce what it claims.

The document is `doc/box-types.md`; `DOCUMENTED` below is it transcribed as
parent links. If a type is added, renamed or moved, this file says so.
"""

from __future__ import annotations

from typing import Any

import pytest

from boxganizer import boxtypes
from boxganizer.boxtypes.base import BoxType
from boxganizer.errors import InvalidBoxContent, UnknownBoxType
from boxganizer.models import Box

#: key -> parent key, straight out of the source document.
DOCUMENTED: dict[str, str | None] = {
    "box": None,
    "content": "box",
    "label": "content",
    "resource": "box",
    "file": "resource",
    "text": "file",
    "markdown": "text",
    "image": "file",
    "pdf": "file",
    "archive": "file",
    "directory": "resource",
    "indexed-directory": "directory",
    "dynamic": "resource",
    "web": "dynamic",
    "browser": "web",
    "snapshot": "web",
    "terminal": "dynamic",
    "ssh": "terminal",
    "sensor": "dynamic",
    "mqtt": "sensor",
    "http": "sensor",
    "home-assistant": "sensor",
    "folder": "box",
    "dynamic-folder": "folder",
    "cabinet": "folder",
    "organizer": "box",
    "desktop": "organizer",
    "blueprint": "desktop",
    "3d-model": "organizer",
    "grid": "organizer",
    "mindmap": "organizer",
    "graph": "organizer",
    "manual-graph": "graph",
    "parental-graph": "graph",
    "dashboard": "organizer",
    "checklist": "organizer",
    "viewport": "box",
    "simple-viewport": "viewport",
    "tiling-viewport": "viewport",
    "windowed-viewport": "viewport",
}

GROUPING_LEVELS = {"content", "resource", "dynamic", "organizer", "viewport"}

#: Types this project added on top of the source document, with their parents.
#: `root` exists because a database needs one place for the graph to start.
ADDITIONS: dict[str, str] = {"root": "folder"}

ALL_KEYS = set(DOCUMENTED) | set(ADDITIONS)
PARENTS = {**DOCUMENTED, **ADDITIONS}


def test_registry_holds_exactly_the_documented_types() -> None:
    assert {cls.key for cls in boxtypes.all_types()} == ALL_KEYS


@pytest.mark.parametrize(("key", "parent"), sorted(PARENTS.items()))
def test_every_type_sits_where_the_document_puts_it(key: str, parent: str | None) -> None:
    cls = boxtypes.get(key)
    actual = boxtypes.parent_of(cls)
    if parent is None:
        assert actual is None
    else:
        assert actual is not None
        assert actual.key == parent


def test_only_the_grouping_levels_are_unselectable() -> None:
    assert {cls.key for cls in boxtypes.all_types() if cls.is_grouping()} == GROUPING_LEVELS
    assert set(boxtypes.selectable_keys()) == ALL_KEYS - GROUPING_LEVELS


def test_grouping_flag_does_not_leak_to_subclasses() -> None:
    assert boxtypes.ContentBox.is_grouping() is True
    assert boxtypes.Label.is_grouping() is False


def test_tree_is_indented_from_the_root() -> None:
    rows = boxtypes.tree()
    assert rows[0][0] == 0
    assert rows[0][1].key == "box"
    depths = {cls.key: depth for depth, cls in rows}
    for key, parent in PARENTS.items():
        expected = 0 if parent is None else depths[parent] + 1
        assert depths[key] == expected, key


def test_tree_children_follow_declaration_order() -> None:
    children = [cls.key for depth, cls in boxtypes.tree() if depth == 1]
    assert children == ["content", "resource", "folder", "organizer", "viewport"]


def test_names_icons_and_summaries_are_filled_in() -> None:
    for cls in boxtypes.all_types():
        assert cls.label, cls.key
        assert cls.icon, cls.key
        assert cls.summary, cls.key


def test_digit_prefixed_type_keeps_its_public_name() -> None:
    assert boxtypes.Model3D.__name__ == "Model3D"
    assert boxtypes.Model3D.key == "3d-model"
    assert boxtypes.Model3D.label == "3D Model"


def test_keys_are_lowercase_and_hyphenated() -> None:
    for cls in boxtypes.all_types():
        assert cls.key == cls.key.strip().lower()
        assert " " not in cls.key
        assert "_" not in cls.key


def test_hierarchy_paths_read_top_down() -> None:
    assert boxtypes.MarkdownResource.path() == "box/resource/file/text/markdown"
    assert boxtypes.BoxType.path() == "box"


def test_lookup_ignores_case_and_padding() -> None:
    assert boxtypes.get("  Markdown ") is boxtypes.MarkdownResource


def test_unknown_type_is_named_with_a_hint() -> None:
    with pytest.raises(UnknownBoxType) as excinfo:
        boxtypes.get("spreadsheet")
    assert excinfo.value.key == "spreadsheet"
    assert "try one of" in str(excinfo.value)


def test_known_only_answers_for_registered_keys() -> None:
    assert boxtypes.known("mqtt") is True
    assert boxtypes.known("carrier-pigeon") is False


def test_a_subclass_must_declare_its_own_key() -> None:
    with pytest.raises(TypeError, match="must declare its own"):

        class Nameless(BoxType):
            pass


def test_two_types_cannot_share_a_key() -> None:
    with pytest.raises(TypeError, match="claimed by both"):

        class Clone(BoxType):
            key = "markdown"


def test_default_type_key_matches_the_model_default() -> None:
    assert Box(name="Plain").box_type == boxtypes.DEFAULT_TYPE_KEY
    assert boxtypes.DEFAULT_TYPE_KEY == "box"


# -- what the types enforce -------------------------------------------------


def make(**kwargs: Any) -> Box:
    defaults: dict[str, Any] = {"name": "Thing"}
    defaults.update(kwargs)
    return Box(**defaults)


def test_a_plain_box_accepts_anything() -> None:
    boxtypes.get("box").validate(make())


def test_resource_is_mandatory_where_the_type_says_so() -> None:
    with pytest.raises(InvalidBoxContent, match="needs a resource"):
        boxtypes.get("image").validate(make())


def test_extension_rules_are_checked_only_when_declared() -> None:
    boxtypes.get("file").validate(make(resource_uri="/notes/todo"))  # file: any extension
    with pytest.raises(InvalidBoxContent, match="must end with"):
        boxtypes.get("pdf").validate(make(resource_uri="/docs/scan.png"))
    boxtypes.get("pdf").validate(make(resource_uri="/docs/scan.PDF"))


def test_scheme_rules_reject_the_wrong_protocol() -> None:
    with pytest.raises(InvalidBoxContent, match="needs http: or https:"):
        boxtypes.get("web").validate(make(resource_uri="ftp://example.com"))
    boxtypes.get("web").validate(make(resource_uri="https://example.com"))


def test_plain_paths_count_as_file_resources() -> None:
    boxtypes.get("file").validate(make(resource_uri="/home/me/notes.md"))
    boxtypes.get("file").validate(make(resource_uri="file:///home/me/notes.md"))
    boxtypes.get("file").validate(make(resource_uri="C:/notes/todo.txt"))


def test_sensor_types_demand_their_wiring() -> None:
    with pytest.raises(InvalidBoxContent, match="metadata topic is required"):
        boxtypes.get("mqtt").validate(make())
    with pytest.raises(InvalidBoxContent, match="metadata host is required"):
        boxtypes.get("ssh").validate(make())
    with pytest.raises(InvalidBoxContent, match="metadata entity_id is required"):
        boxtypes.get("home-assistant").validate(make())
    boxtypes.get("mqtt").validate(make(metadata={"topic": "cellar/temp"}))


def test_blank_metadata_counts_as_missing() -> None:
    with pytest.raises(InvalidBoxContent, match="metadata topic is required"):
        boxtypes.get("mqtt").validate(make(metadata={"topic": "   "}))


def test_leaf_types_are_named_by_the_hierarchy_itself() -> None:
    leaves = {cls.key for cls in boxtypes.all_types() if not cls.allows_children}
    assert {"content", "label", "file", "image", "pdf", "archive", "markdown", "text"} <= leaves
    assert "directory" not in leaves
    assert "folder" not in leaves


def test_describe_exposes_the_rules() -> None:
    described = boxtypes.SnapshotResource.describe()
    assert described["key"] == "snapshot"
    assert described["parent"] == "web"
    assert described["ancestors"] == ["web", "dynamic", "resource", "box"]
    assert described["required_metadata"] == []
    assert described["abstract"] is False


def test_describe_all_covers_the_whole_tree() -> None:
    assert len(boxtypes.describe_all()) == len(ALL_KEYS)
    assert len(boxtypes.describe_all(include_abstract=False)) == len(ALL_KEYS) - len(
        GROUPING_LEVELS
    )


def test_root_is_the_one_type_that_may_exist_only_once() -> None:
    assert {cls.key for cls in boxtypes.all_types() if cls.singleton} == {"root"}


def test_try_get_returns_none_instead_of_raising() -> None:
    assert boxtypes.try_get("markdown") is boxtypes.MarkdownResource
    assert boxtypes.try_get("hologram") is None


def test_a_typed_box_inherits_its_type_icon() -> None:
    box = boxtypes.adopt_type_icon(Box(name="Notes", box_type="markdown"))
    assert box.icon == boxtypes.MarkdownResource.icon


def test_an_explicit_icon_is_never_overwritten() -> None:
    box = boxtypes.adopt_type_icon(Box(name="Notes", box_type="markdown", icon="🐧"))
    assert box.icon == "🐧"


def test_a_plain_box_keeps_the_generic_icon() -> None:
    from boxganizer.models import DEFAULT_ICON

    assert boxtypes.adopt_type_icon(Box(name="Thing")).icon == DEFAULT_ICON
