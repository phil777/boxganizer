"""Box model rules: names, identity, serialisation."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from boxganizer.errors import InvalidBoxName
from boxganizer.models import MAX_NAME_LENGTH, Box


def test_name_is_stripped() -> None:
    assert Box(name="  Kitchen  ").name == "Kitchen"


@pytest.mark.parametrize("name", ["", "   ", "\t\n"])
def test_empty_name_is_rejected(name: str) -> None:
    with pytest.raises(InvalidBoxName):
        Box(name=name)


def test_control_characters_are_rejected() -> None:
    with pytest.raises(InvalidBoxName, match="control character"):
        Box(name="bad\x00name")


def test_overlong_name_is_rejected() -> None:
    with pytest.raises(InvalidBoxName, match="longer than"):
        Box(name="x" * (MAX_NAME_LENGTH + 1))


def test_key_is_case_insensitive() -> None:
    assert Box(name="Kitchen").key == Box(name="  kItChEn ").key


def test_blank_resource_becomes_none() -> None:
    assert Box(name="Box", resource_uri="   ").resource_uri is None


def test_metadata_is_copied() -> None:
    source = {"a": "1"}
    box = Box(name="Box", metadata=source)
    source["b"] = "2"
    assert box.metadata == {"a": "1"}


def test_to_dict_is_json_friendly() -> None:
    box = Box(name="House", created_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC))
    payload = box.to_dict()
    assert payload["name"] == "House"
    assert payload["created_at"] == "2026-01-02T03:04:05+00:00"
    assert set(payload) == {
        "id",
        "name",
        "description",
        "icon",
        "box_type",
        "resource_uri",
        "metadata",
        "config",
        "created_at",
        "updated_at",
    }


def test_box_type_defaults_to_plain_box() -> None:
    from boxganizer import boxtypes

    assert Box(name="Plain").box_type == boxtypes.DEFAULT_TYPE_KEY


def test_box_type_survives_the_json_view() -> None:
    box = Box(name="Notes", box_type="markdown")
    assert Box.from_mapping(box.to_dict()).box_type == "markdown"


def test_config_holds_nested_settings() -> None:
    box = Box(name="Grid", box_type="grid", config={"columns": 3, "slots": ["a", "b"]})
    assert box.to_dict()["config"] == {"columns": 3, "slots": ["a", "b"]}
    assert Box.from_mapping(box.to_dict()).config == {"columns": 3, "slots": ["a", "b"]}


def test_config_is_copied_not_aliased() -> None:
    source = {"a": 1}
    box = Box(name="Box", config=source)
    source["b"] = 2
    assert box.config == {"a": 1}


def test_from_mapping_ignores_unknown_keys() -> None:
    box = Box.from_mapping({"name": "Attic", "surprise": 1, "metadata": {"n": 3}})
    assert box.name == "Attic"
    assert box.metadata == {"n": "3"}
