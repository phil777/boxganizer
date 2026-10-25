"""The filter a dynamic folder lives by: reading it, matching a box, saying what it asks."""

from __future__ import annotations

from boxganizer import filters
from boxganizer.models import Box


def box_of(**changes: object) -> Box:
    """A box with the given fields, so each test says only what it is about."""
    fields: dict[str, object] = {"name": "Cellar temp", "box_type": "box"}
    fields.update(changes)
    return Box(**fields)  # type: ignore[arg-type]


# -- what a filter asks ------------------------------------------------------


def test_an_empty_filter_asks_for_every_box() -> None:
    asked = filters.parse(None)
    assert asked.usable
    assert asked.empty
    assert asked.matches(box_of())
    assert asked.matches(box_of(box_type="pdf", resource_uri="/tmp/a.pdf"))
    assert asked.describe() == "every box"


def test_a_name_clause_is_a_case_insensitive_part_of_the_name() -> None:
    asked = filters.parse({"name": "CELLAR"})
    assert asked.matches(box_of(name="My Cellar Shelf"))
    assert not asked.matches(box_of(name="Attic"))


def test_a_type_clause_takes_the_type_and_every_type_below_it() -> None:
    asked = filters.parse({"type": "resource"})
    assert asked.matches(box_of(box_type="pdf", resource_uri="/tmp/a.pdf"))
    assert asked.matches(box_of(box_type="image", resource_uri="/tmp/a.png"))
    # ...but not a box above it, or a sibling
    assert not asked.matches(box_of(box_type="box"))
    assert not asked.matches(box_of(box_type="folder"))


def test_a_type_clause_names_the_leaf_exactly_too() -> None:
    asked = filters.parse({"type": "pdf"})
    assert asked.matches(box_of(box_type="pdf", resource_uri="/tmp/a.pdf"))
    assert not asked.matches(box_of(box_type="image", resource_uri="/tmp/a.png"))


def test_a_resource_clause_reads_the_uri_and_leaves_boxes_without_one_out() -> None:
    asked = filters.parse({"resource": "mqtts://"})
    assert asked.matches(box_of(box_type="mqtt", resource_uri="mqtts://broker:8883/t"))
    assert not asked.matches(box_of(box_type="mqtt", resource_uri="mqtt://broker:1883/t"))
    assert not asked.matches(box_of())


def test_a_metadata_clause_asks_for_the_key_to_be_set() -> None:
    asked = filters.parse({"metadata": {"floor": ""}})
    assert asked.matches(box_of(metadata={"floor": "0"}))
    assert not asked.matches(box_of(metadata={"country": "FR"}))


def test_a_metadata_clause_with_a_value_asks_for_that_value() -> None:
    asked = filters.parse({"metadata": {"country": "fr"}})
    assert asked.matches(box_of(metadata={"country": "FR"}))
    assert not asked.matches(box_of(metadata={"country": "DE"}))


def test_clauses_narrow_rather_than_choose() -> None:
    asked = filters.parse({"name": "report", "type": "resource"})
    assert asked.matches(box_of(name="report.pdf", box_type="pdf", resource_uri="/tmp/r.pdf"))
    assert not asked.matches(box_of(name="report", box_type="box"))
    assert not asked.matches(box_of(name="invoice.pdf", box_type="pdf", resource_uri="/tmp/i.pdf"))


# -- a filter that cannot be read -------------------------------------------


def test_a_filter_that_cannot_be_read_selects_nothing_and_says_why() -> None:
    """Empty would mean "the whole library", so a typo must not read as empty."""
    for broken in (["not", "a", "mapping"], "pdf", 7):
        asked = filters.parse(broken)
        assert not asked.usable
        assert not asked.matches(box_of())
        assert "filter not understood" in asked.describe()


def test_an_unknown_clause_is_reported_rather_than_ignored() -> None:
    asked = filters.parse({"name": "a", "colour": "red"})
    assert not asked.usable
    assert "colour" in asked.broken


def test_a_type_that_does_not_exist_is_reported() -> None:
    asked = filters.parse({"type": "spreadsheet-ish"})
    assert not asked.usable
    assert "spreadsheet-ish" in asked.broken


def test_a_box_whose_type_vanished_is_simply_not_the_type_asked_for() -> None:
    """A filter naming a real type meets a box holding a key nobody registered."""
    asked = filters.parse({"type": "pdf"})
    assert not asked.matches(box_of(box_type="retired-type", resource_uri="/x"))


# -- round trip --------------------------------------------------------------


def test_a_filter_writes_back_only_what_it_asks() -> None:
    original = {"name": "cellar", "type": "resource", "metadata": {"floor": "0"}}
    asked = filters.parse(original)
    assert asked.as_config() == original
    assert filters.parse({}).as_config() == {}


def test_the_filter_is_read_from_the_config_of_the_box_that_carries_it() -> None:
    holder = Box(name="All PDFs", box_type="dynamic-folder", config={"filter": {"type": "pdf"}})
    assert filters.parse_of(holder).type == "pdf"
    assert filters.parse_of(Box(name="plain")).empty


def test_describe_says_the_clauses_in_the_order_a_person_would_read_them() -> None:
    asked = filters.parse({"name": "report", "type": "pdf", "metadata": {"owner": "me"}})
    assert asked.describe() == (
        "name contains 'report' and type is pdf or one below it and metadata owner='me'"
    )


def test_the_state_a_folder_shows_when_its_filter_is_broken_is_the_broken_one() -> None:
    """The page shows `parse_of`, so a bad stored filter must not read as "all boxes"."""
    holder = Box(name="Ouch", box_type="dynamic-folder", config={"filter": {"nope": 1}})
    asked = filters.parse_of(holder)
    assert not asked.empty
    assert not asked.matches(box_of())
