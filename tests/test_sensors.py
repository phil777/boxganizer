"""Tests for what a sensor does with a value before it is shown.

The contract these hold is narrow and deliberate: an owner's template must be able to say almost
anything about *their* value, and must not be able to say anything about the page showing it.
"""

from __future__ import annotations

from boxganizer import sensors
from boxganizer.models import Box

PAYLOAD = '{"pressure": 1.42, "unit": "bar", "state": "ok"}'


def box(**config: str) -> Box:
    return Box(
        name="Boiler pressure",
        box_type="mqtt",
        resource_uri="mqtt://b:1883/boiler",
        config=dict(config),
    )


def present(config: dict[str, str], payload: str = PAYLOAD) -> sensors.Presentation:
    return sensors.present(box(**config), payload)


def test_an_extraction_template_picks_the_value_out_of_the_payload() -> None:
    picked = present({"extract_template": "{{ data.pressure }}"})
    assert picked.extracted == "1.42"
    assert picked.raw == PAYLOAD, "the reading itself is never thrown away"
    assert picked.problem == ""


def test_a_plain_payload_is_the_value_and_a_number_still_parses() -> None:
    assert present({"extract_template": "{{ value }}"}, "1.42 bar").extracted == "1.42 bar"
    assert (
        present({"extract_template": "{{ number }}", "display_template": "{{ value }}"}).display
        == "1.42"
    )
    assert present({"extract_template": "{{ value }}"}, "42").extracted == "42"
    assert present({}, "not a number at all").display == "not a number at all"


def test_the_display_template_sees_the_raw_payload_and_the_extracted_value() -> None:
    shown = present(
        {
            "extract_template": "{{ data.pressure }}",
            "display_template": "{{ extracted }} {{ data.unit }} (from {{ raw }})",
        }
    )
    assert shown.display.startswith("1.42 bar (from ")
    assert "'pressure': 1.42" in shown.display or '"pressure": 1.42' in shown.display
    assert shown.problem == ""


def test_the_styling_template_sees_every_step_before_it() -> None:
    shown = present(
        {
            "extract_template": "{{ data.pressure }}",
            "display_template": "{{ extracted }} bar",
            "style_template": (
                '{% if extracted|float > 1.3 %}<b style="color: red">{% else %}<i>{% endif %}'
                "{{ display }}"
                "{% if extracted|float > 1.3 %}</b>{% else %}</i>{% endif %}"
            ),
        }
    )
    assert shown.styled == '<b style="color: red">1.42 bar</b>'


def test_only_formatting_survives_a_styling_template() -> None:
    dressed = present(
        {
            "style_template": (
                "<p>hello</p>"
                "<script>alert(1)</script>"
                "<img src=x onerror=alert(1)>"
                '<a href="javascript:alert(1)">click</a>'
                '<span onclick="steal()" id="s" data-x="1" class="sensor-good">value</span>'
            )
        }
    )
    out = dressed.styled
    assert "hello" in out
    assert "value" in out, "an unknown or stripped tag keeps the words inside it"
    assert "alert" not in out
    assert "onerror" not in out
    assert "onclick" not in out
    assert "javascript:" not in out
    assert "data-x" not in out
    assert 'id="s"' not in out
    assert 'class="sensor-good"' in out, "the page's own sensor classes are opt-in by name"
    assert "<script" not in out and "<img" not in out and "<a " not in out


def test_a_style_attribute_keeps_colour_and_font_and_refuses_the_rest() -> None:
    dressed = present(
        {
            "style_template": (
                '<span style="color: #ff0000; font-size: 2rem; position: fixed; top: 0; '
                'background: url(javascript:alert(1)); width: calc(100vw - 10px)">x</span>'
            )
        }
    )
    out = dressed.styled
    assert "color: #ff0000" in out
    assert "font-size: 2rem" in out
    assert "position" not in out, "a property that lays out somebody else's page is not formatting"
    assert "url(" not in out
    assert "calc(" not in out, "a measure is a number and a unit, which is all width may be"


def test_a_style_attribute_is_read_one_declaration_at_a_time_and_kept_within_the_list() -> None:
    """Two allowed properties are fine; the trick is to smuggle a third one in past a quote."""
    dressed = present(
        {
            "style_template": (
                '<span style="font-family: Arial, sans-serif; color: red; position: fixed">v</span>'
                '<span style="color: red", "onclick": "x">w</span>'
            )
        }
    )
    out = dressed.styled
    assert "font-family: Arial, sans-serif" in out
    assert "color: red" in out
    assert "position" not in out
    assert "onclick" not in out


def test_a_styling_template_that_forgets_to_close_a_tag_cannot_leave_the_page_bold() -> None:
    dressed = present({"style_template": "<b>loud</b> and <b>still bold"})
    assert dressed.styled.endswith("</b>")
    assert dressed.styled.count("<b>") == dressed.styled.count("</b>")


def test_a_template_that_raises_says_so_and_still_shows_the_value() -> None:
    broken = present({"extract_template": "{{ data.pressure"})
    assert not broken.problem.startswith("display")
    assert "extraction:" in broken.problem
    assert broken.extracted == PAYLOAD, "the reading is what a person came to see"

    missing = present({"extract_template": "{{ nothing_here }}"})
    assert "extraction:" in missing.problem and missing.extracted == PAYLOAD

    explosion = present({"style_template": "{{ 1 / 0 }}<b>x</b>"})
    assert "styling:" in explosion.problem
    assert explosion.styled, "the display text is dressed plainly instead of not at all"


def test_a_broken_template_is_refused_when_the_box_is_saved() -> None:
    assert sensors.check_templates({"extract_template": "{{ data.x }}"}) == ""
    assert "display_template" in sensors.check_templates({"display_template": "{% if %}"})
    assert "characters" in sensors.check_templates({"style_template": "{{ value }}" * 400})


def test_a_template_cannot_reach_outside_the_box() -> None:
    """Jinja without a loader has nothing to include, and the sandbox has no private attributes."""
    for attempt in (
        "{% include '/etc/passwd' %}",
        "{% extends 'x' %}",
        "{{ ''.__class__ }}",
        "{{ config }}",
    ):
        shown = present({"style_template": attempt})
        assert shown.problem.startswith("styling:") or "passwd" not in shown.styled, attempt
        assert "class_" not in shown.styled
        assert "boot" not in shown.styled.lower()


def test_a_styling_template_is_not_allowed_to_be_a_whole_page() -> None:
    dressed = present(
        {"style_template": "<html><body><style>@import url('x');</style>value</body></html>"}
    )
    assert "@import" not in dressed.styled
    assert "<style" not in dressed.styled
    assert "<html" not in dressed.styled
    assert "value" in dressed.styled


def test_only_the_colours_a_picker_or_a_name_can_produce_are_written() -> None:
    assert sensors.style_for_colour("#0a0a0a") == "color: #0a0a0a"
    assert sensors.style_for_colour("tomato") == "color: tomato"
    assert sensors.style_for_colour("inherit") == "color: inherit"
    assert sensors.style_for_colour("red; position: fixed") == ""
    assert sensors.style_for_colour('red" onmouseover="x') == ""
    assert sensors.style_for_colour("") == ""


def test_the_three_templates_are_the_whole_public_surface() -> None:
    assert sensors.TEMPLATES == ("extract_template", "display_template", "style_template")
    assert "style" not in sensors.FORMAT_TAGS, "a <style> element reaches outside the pane"
    assert "script" in sensors.DROP_TAGS
