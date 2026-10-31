"""The filter expression grammar: what it reads, what it refuses, and what it gives back."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from boxganizer.filters import Lookup, parse, parse_expression_filter
from boxganizer.models import Box
from boxganizer.store import BoxStore


def box_of(
    name: str = "Boiler report",
    box_type: str = "note",
    resource: str | None = None,
    uuid: str = "b1",
) -> Box:
    return Box(name=name, box_type=box_type, resource_uri=resource, uuid=uuid)


LOOKUP = Lookup(
    tags={"urgent": frozenset({"b1"}), "later": frozenset({"b2"})},
    content={"plumbing": frozenset({"b1"})},
)


def holds(expression: str, box: Box | None = None) -> bool:
    return parse_expression_filter(expression).matches(box or box_of(), LOOKUP)


# -- the clauses -----------------------------------------------------------------------


def test_each_field_asks_about_the_thing_it_names() -> None:
    assert holds("name:boiler")
    assert holds("type:note")
    assert holds(r"resource:/\.txt$/", box_of(resource="/tmp/report.txt"))
    assert not holds(r"resource:/\.pdf$/", box_of(resource="/tmp/report.txt"))
    assert holds('resource:"/tmp/"', box_of(resource="/tmp/report.txt")), (
        "a quoted value is text, slashes and all"
    )
    assert holds("tag:urgent")
    assert not holds("tag:later"), "another box's tag is not this box's tag"
    assert holds("content:plumbing")
    assert not holds("content:plumbing", box_of(uuid="b9"))


def test_type_reads_the_hierarchy_not_just_the_key() -> None:
    """Asking for `resource` takes a pdf box, because a pdf *is* a resource.

    The one rule the old clauses already had, kept: an expression that disagreed with the type
    list about what counts as what would make a folder's membership depend on which way you
    wrote the question.
    """
    assert holds("type:resource", box_of(box_type="pdf"))
    assert not holds("type:pdf", box_of(box_type="resource"))


def test_a_bare_word_is_a_name_and_a_quoted_phrase_keeps_its_space() -> None:
    assert holds("boiler")
    assert holds('"boiler report"')
    assert not holds('"boiler report"', box_of(name="Shed"))


def test_a_value_can_be_a_regex_in_any_of_the_three_spellings() -> None:
    assert holds("name:/^boi.*port$/")
    assert holds("name:re:^boi.*port$")
    assert holds("/^boi.*port$/"), "a bare regex is a regex on the name, like a bare word"
    assert not holds("name:/^shed/")


# -- and, or, not, grouping ------------------------------------------------------------


def test_a_missing_and_still_means_and() -> None:
    """`type:note urgent` is two conditions with the word left out, not one condition.

    Nobody types `and` between two things they mean together, and reading the pair as a single
    search term would silently widen a filter that was being narrowed.
    """
    assert holds("type:note tag:urgent")
    assert holds("type:note and tag:urgent")
    assert holds("type:note && tag:urgent")
    assert not holds("type:pdf tag:urgent")


def test_or_widens_and_parentheses_decide_who_wins() -> None:
    assert holds("name:shed or name:boiler")
    assert holds("name:shed|name:boiler")
    assert not holds("name:shed or tag:later")
    assert holds("(name:shed or name:boiler) and tag:urgent")
    assert not holds("name:shed or (name:boiler and tag:later)"), "the brackets mean what they say"


def test_not_negates_the_thing_in_front_of_it() -> None:
    assert holds("not type:pdf")
    assert not holds("not type:note")
    assert holds("not (name:shed or tag:later)")
    assert not holds("not (name:boiler or tag:later)")


def test_the_words_say_where_the_brackets_are() -> None:
    """A description that hides the grouping is a description of a different filter."""
    said = parse_expression_filter("not (name:a or name:b)").describe()
    assert said == "not (name contains 'a' or name contains 'b')"
    assert (
        parse_expression_filter("name:a and (name:b or name:c)").describe()
        == "name contains 'a' and (name contains 'b' or name contains 'c')"
    )


# -- what is refused -------------------------------------------------------------------


def test_a_broken_expression_says_why_and_admits_nothing() -> None:
    for bad in (
        "type:",
        "resource:/report$",
        "wat:x",
        "name:(a",
        "name:/[a-/",
        "not",
        "a and or b",
        "name:a) ",
    ):
        asked = parse_expression_filter(bad)
        assert not asked.usable, bad
        assert asked.broken, bad
        assert not asked.matches(box_of(), LOOKUP), bad
        assert "every box" not in asked.describe(), bad


def test_a_broken_filter_is_not_read_as_an_empty_one() -> None:
    """The whole library in the folder is the worst possible answer to a typo."""
    typo = parse_expression_filter("typo:x")
    empty = parse_expression_filter("")
    assert not typo.usable and typo.matches(box_of(name="anything"), LOOKUP) is False
    assert empty.usable and empty.empty and empty.matches(box_of(name="anything"), LOOKUP)


def test_an_unanswered_lookup_admits_nothing() -> None:
    """No lookup is not "everything": a tag nobody resolved is a clause nothing satisfies."""
    assert holds("tag:urgent") is True
    assert parse_expression_filter("tag:urgent").matches(box_of()) is False


# -- round-tripping --------------------------------------------------------------------


def test_the_expression_comes_back_as_it_was_written() -> None:
    for text in (
        'type:note and (tag:urgent or name:"boiler fix")',
        "not type:pdf",
        "name:/^boi/ and content:plumbing",
    ):
        assert parse_expression_filter(text).as_config() == {"expression": text}, text
    assert parse_expression_filter("").as_config() == {"expression": ""}


def test_what_a_tree_will_need_resolved_is_known_before_it_runs() -> None:
    needs = parse_expression_filter("tag:urgent and content:plumbing and name:x").needs
    assert needs.tags == ("urgent",)
    assert needs.content == ("plumbing",)
    assert not parse_expression_filter("name:x").needs.any()


# -- the old shape is untouched --------------------------------------------------------


def test_a_stored_mapping_still_parses_exactly_as_it_did() -> None:
    """Existing folders were written as clauses, and must mean the same thing today.

    Silently re-reading somebody's stored filter is the kind of migration that shows up weeks
    later as a folder that quietly changed contents.
    """
    asked = parse({"name": "boiler", "type": "note", "resource": "/tmp"})
    assert asked.root is None, "a legacy filter stays a legacy filter"
    assert asked.name == "boiler" and asked.type == "note" and asked.resource == "/tmp"
    assert asked.usable and asked.matches(box_of(resource="/tmp/x"))
    assert asked.as_config() == {"name": "boiler", "type": "note", "resource": "/tmp"}


def test_a_bare_string_is_still_a_mistake_rather_than_a_new_feature() -> None:
    """`filter: pdf` used to be an error with a message, and stays one.

    Accepting it would give a value this version rejected a meaning it never had: a typo would
    become a silent "name contains pdf" in a library somebody already filed.
    """
    asked = parse("pdf")
    assert not asked.usable
    assert "expression" in asked.broken


def test_the_expression_key_cannot_share_a_mapping_with_clauses() -> None:
    asked = parse({"expression": "boiler", "type": "note"})
    assert not asked.usable
    assert "cannot sit beside" in asked.broken
    assert parse({"expression": "boiler"}).usable


def test_a_folder_whose_filter_asks_about_tags_and_words_is_filed_by_the_store(
    tmp_path: Path,
) -> None:
    """The grammar only matters if membership follows it.

    Tag and content clauses are answered from the tag tree and the search index, which
    `filters.py` cannot see; the store resolves each once per pass and hands the answers in. This
    is the test that says those two clauses are features rather than vocabulary that selects
    nothing.
    """
    from boxganizer import search as indexing

    store = BoxStore(tmp_path / "grammar.db", content_root=tmp_path)
    try:
        store.add_tag_path("home/kitchen/boiler")
        note = tmp_path / "meter.md"
        note.write_text("the quagginess reading was high", encoding="utf-8")

        meter = store.add(Box(name="Meter", box_type="markdown", resource_uri=str(note)))
        shed = store.add(Box(name="Shed", box_type="note"))
        work = store.add(Box(name="Work order", box_type="note"))
        store.assign_tag(meter, "boiler")  # under home/kitchen, so `home` should reach it
        store.assign_tag(shed, "home")
        indexing.rebuild(store, root=tmp_path)

        folder = store.add(
            Box(
                name="Kitchen things",
                box_type="dynamic-folder",
                config={"filter": {"expression": "tag:home and content:quagginess"}},
            )
        )
        assert sorted(child.name for child in store.children(folder.uuid)) == ["Meter"]

        # the tag branch is what makes a box with no words of its own still answer to `home`
        store.set_box_tags(work, ["home"])
        store.resolve_folder(folder.uuid)
        assert sorted(child.name for child in store.children(folder.uuid)) == ["Meter"]

        # widen it on the tag alone and the box without words joins; the content clause excluded it
        store.update(
            replace(
                store.resolve(folder.uuid),
                config={"filter": {"expression": "tag:home"}},
            )
        )
        assert sorted(child.name for child in store.children(folder.uuid)) == [
            "Meter",
            "Shed",
            "Work order",
        ]
    finally:
        store.close()


def test_a_regex_where_words_are_asked_is_refused_rather_than_left_dead() -> None:
    """A clause nothing can answer would select nothing forever, silently."""
    for bad in ("tag:/^ur/", "tag:re:^ur", "content:/plumb.*/"):
        asked = parse_expression_filter(bad)
        assert not asked.usable, bad
        assert "words, not characters" in asked.broken, bad
