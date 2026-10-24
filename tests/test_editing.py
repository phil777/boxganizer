"""The YAML round trip: a row out, an edited file in, nothing lost or invented.

No editor is involved here — `boxganizer.editing` is pure text in, `Box` out.
The CLI's use of `$EDITOR` is tested in `tests/test_cli.py`.
"""

from __future__ import annotations

import pytest

from boxganizer import boxtypes, editing
from boxganizer.errors import EditError
from boxganizer.models import Box


@pytest.fixture
def box() -> Box:
    return Box(
        id=7,
        name="Cellar",
        box_type="cabinet",
        icon="\U0001f5c4️",
        description="Below the stairs",
        resource_uri="/srv/cellar",
        metadata={"since": "1998"},
        config={"drawers": 4, "layout": {"rows": 2, "labels": ["tools", "wine"]}},
    )


# -- writing ---------------------------------------------------------------


def test_the_file_shows_every_column_of_the_row(box: Box) -> None:
    document = editing.dump(box)
    for field in (
        "id",
        "name",
        "box_type",
        "icon",
        "description",
        "resource_uri",
        "metadata",
        "config",
        "created_at",
        "updated_at",
    ):
        assert f"\n{field}:" in f"\n{document}", field


def test_the_header_orients_the_reader(box: Box) -> None:
    document = editing.dump(box, parents=["root", "House"])
    assert "# type      : Cabinet" in document
    assert "# key       : cellar" in document
    assert "# parents   : root, House" in document
    assert "stored as JSON" in document


def test_metadata_and_config_appear_as_yaml_not_as_json_strings(box: Box) -> None:
    document = editing.dump(box)
    assert "  since: '1998'" in document
    assert "    - tools" in document
    assert '{"drawers": 4}' not in document


def test_keys_keep_a_readable_order(box: Box) -> None:
    document = editing.dump(box)
    order = [
        line.split(":")[0]
        for line in document.splitlines()
        if ":" in line and not line.startswith((" ", "#"))
    ]
    assert order[:7] == [
        "id",
        "uuid",
        "name",
        "box_type",
        "icon",
        "description",
        "resource_uri",
    ]


# -- reading ---------------------------------------------------------------


def test_an_untouched_file_changes_nothing(box: Box) -> None:
    rebuilt = editing.load(editing.dump(box), original=box)
    assert rebuilt.to_dict() == box.to_dict()


def test_edits_land_on_the_box(box: Box) -> None:
    edited = editing.dump(box).replace("description: Below the stairs", "description: Damp")
    rebuilt = editing.load(edited, original=box)
    assert rebuilt.description == "Damp"
    assert rebuilt.name == "Cellar"


def test_yaml_edits_to_config_come_back_as_python_values(box: Box) -> None:
    edited = editing.dump(box).replace("  drawers: 4", "  drawers: 6\n  locked: true")
    rebuilt = editing.load(edited, original=box)
    assert rebuilt.config["drawers"] == 6
    assert rebuilt.config["locked"] is True


def test_renaming_a_box_is_allowed(box: Box) -> None:
    rebuilt = editing.load(editing.dump(box).replace("name: Cellar", "name: Attic"), original=box)
    assert rebuilt.name == "Attic"
    assert rebuilt.id == box.id


def test_changing_the_type_is_allowed(box: Box) -> None:
    rebuilt = editing.load(
        editing.dump(box).replace("box_type: cabinet", "box_type: directory"), original=box
    )
    assert rebuilt.box_type == "directory"


def test_a_blank_icon_falls_back_to_the_type_icon(box: Box) -> None:
    edited = editing.dump(box).replace("icon: \U0001f5c4️", "icon:")
    rebuilt = editing.load(edited, original=box)
    assert rebuilt.icon == boxtypes.Cabinet.icon == "\U0001f5c4️"


def test_empty_string_clears_an_optional_field(box: Box) -> None:
    edited = editing.dump(box).replace("resource_uri: /srv/cellar", "resource_uri:")
    assert editing.load(edited, original=box).resource_uri is None


# -- refusals --------------------------------------------------------------


@pytest.mark.parametrize(
    ("breakage", "reason"),
    [
        ("", "empty"),
        ("- just\n- a\n- list\n", "one mapping"),
        ("name: X\nunknown_field: 1\n", "unknown_field"),
        ("name: X\nparents:\n  - root\n", "box link"),
        ("name: X\nmetadata:\n  nested:\n    a: 1\n", "put nesting in config"),
        ("name: X\nmetadata: notamapping\n", "must be a mapping"),
        ("name: X\nconfig: notamapping\n", "must be a mapping"),
        ("name: X\nconfig:\n  when: 2026-01-31\n", "which JSON cannot store"),
        ("name:\n", "needs a name"),
        ("{] not yaml\n", "valid YAML"),
    ],
)
def test_a_file_the_row_cannot_hold_is_refused(box: Box, breakage: str, reason: str) -> None:
    with pytest.raises(EditError) as excinfo:
        editing.load(breakage, original=box)
    assert reason in str(excinfo.value)


def test_a_modified_timestamp_is_refused(box: Box) -> None:
    edited = editing.dump(box).replace(
        f"created_at: '{box.created_at.isoformat(timespec='seconds')}'",
        "created_at: '2000-01-01T00:00:00+00:00'",
    )
    assert edited != editing.dump(box)
    with pytest.raises(EditError, match="created_at is set by Boxganizer"):
        editing.load(edited, original=box)


def test_the_id_must_stay_the_same(box: Box) -> None:
    edited = editing.dump(box).replace(f"id: {box.id}", "id: 999")
    with pytest.raises(EditError, match="id is set by Boxganizer"):
        editing.load(edited, original=box)


def test_the_uuid_must_stay_the_same(box: Box) -> None:
    edited = editing.dump(box).replace(f"uuid: {box.uuid}", f"uuid: {'0' * 36}")
    with pytest.raises(EditError, match="uuid is set by Boxganizer"):
        editing.load(edited, original=box)


def test_the_uuid_survives_an_edit(box: Box) -> None:
    rebuilt = editing.load(editing.dump(box).replace("name: Cellar", "name: Loft"), original=box)
    assert rebuilt.uuid == box.uuid


def test_an_unknown_type_is_named(box: Box) -> None:
    from boxganizer.errors import UnknownBoxType

    edited = editing.dump(box).replace("box_type: cabinet", "box_type: spreadsheet")
    with pytest.raises(UnknownBoxType):
        editing.load(edited, original=box)


def test_an_error_can_carry_the_line_it_happened_on(box: Box) -> None:
    with pytest.raises(EditError) as excinfo:
        editing.load("name: X\nmetadata:\n  a: 1\n   b: 2\n", original=box)
    assert "at line" in str(excinfo.value) or "mapping values" in str(excinfo.value)
