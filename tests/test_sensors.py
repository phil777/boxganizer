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


SCALE = {
    "numeric": {
        "min": 0,
        "max": 10,
        "representation": "gauge",
        "intervals": [
            {"upto": 3, "colour": "green"},
            {"upto": 7, "colour": "orange"},
            {"upto": 10, "colour": "#d0021b"},
        ],
    }
}


def scale_box(**over: object) -> Box:
    config = {k: dict(v) if isinstance(v, dict) else v for k, v in SCALE.items()}
    config.update(over)
    return Box(
        name="Boiler pressure", box_type="mqtt", resource_uri="mqtt://b:1883/t", config=config
    )


def scale(numeric: float | None):  # type: ignore[no-untyped-def]
    return sensors.numeric(scale_box(), numeric)


def test_a_value_between_two_stops_cannot_fall_through_to_no_colour() -> None:
    """The intervals are required to cover the range, and this is what that buys.

    Every point of [min, max] is asked about, at a resolution finer than the stops: a scale with a
    hole in it looks fine until the one reading that lands in the hole arrives, and by then the
    person watching has learned to trust the colour.
    """
    steps = 201
    for index in range(steps + 1):
        number = 10.0 * index / steps
        found = scale(number)
        assert found is not None, number
        assert found["colour"], f"{number} arrived with no colour"
        assert found["interval"] in (0, 1, 2)
    assert scale(2.99)["colour"] == "green"
    assert scale(3.0)["colour"] == "green"
    assert scale(3.01)["colour"] == "orange"
    assert scale(9.99)["colour"] == "#d0021b"


def test_a_reading_outside_the_range_clamps_and_says_so() -> None:
    low, high = scale(-4), scale(14)
    assert low["position"] == 0.0 and low["clamped"] == "low" and low["clamp_text"] == "below 0"
    assert (
        high["position"] == 1.0 and high["clamped"] == "high" and high["clamp_text"] == "above 10"
    )
    # and inside the range it claims nothing it did not do
    assert scale(5)["clamped"] == "" and scale(5)["clamp_text"] == ""


def test_a_scale_with_a_hole_is_refused_and_the_hole_is_named() -> None:
    gap = {
        "numeric": {
            "min": 0,
            "max": 10,
            "representation": "hbar",
            "intervals": [{"upto": 4, "colour": "green"}, {"upto": 6, "colour": "red"}],
        }
    }
    problem = sensors.check_numeric(gap)
    assert "stop at 6" in problem and "runs to 10" in problem
    assert "8" in problem, "the message names a reading that would fall through"

    bottom = {
        "numeric": {
            "min": 0,
            "max": 10,
            "representation": "hbar",
            "intervals": [{"upto": 0, "colour": "green"}, {"upto": 20, "colour": "red"}],
        }
    }
    assert "bottom of the scale" in sensors.check_numeric(bottom)

    backwards = {
        "numeric": {
            "min": 0,
            "max": 10,
            "representation": "hbar",
            "intervals": [{"upto": 8, "colour": "green"}, {"upto": 4, "colour": "red"}],
        }
    }
    assert "must end above" in sensors.check_numeric(backwards)

    for bad in (
        {
            "numeric": {
                "min": 0,
                "max": 0,
                "representation": "hbar",
                "intervals": [{"upto": 1, "colour": "red"}],
            }
        },
        {
            "numeric": {
                "min": 0,
                "max": 10,
                "representation": "spiral",
                "intervals": [{"upto": 12, "colour": "red"}],
            }
        },
        {"numeric": {"min": 0, "max": 10, "representation": "hbar", "intervals": []}},
        {
            "numeric": {
                "min": 0,
                "max": 10,
                "representation": "hbar",
                "intervals": [{"upto": 12, "colour": "red; x"}],
            }
        },
        {"numeric": "not a scale"},
    ):
        assert sensors.check_numeric(bad), bad
    assert sensors.check_numeric(SCALE) == ""
    assert sensors.check_numeric({}) == "", "a sensor with no scale is not a broken sensor"


def test_the_sixteen_palette_colours_are_the_ones_the_picker_offers() -> None:
    presets = sensors.palette()
    assert len(presets) == 16
    assert {entry["name"] for entry in presets} >= {"green", "red", "yellow", "grey"}
    for entry in presets:
        assert sensors._is_colour(entry["colour"]), entry
        assert sensors.style_for_colour(entry["colour"]).startswith("color: #")


def test_the_number_on_the_scale_travels_with_the_reading() -> None:
    box = scale_box(extract_template="{{ data.pressure }}")
    reading = sensors.present(box, '{"pressure": 8.4}')
    assert reading.numeric is not None
    assert reading.numeric["value"] == 8.4
    assert reading.numeric["colour"] == "#d0021b"
    assert reading.numeric["representation"] == "gauge"
    assert reading.numeric["position"] == 0.84
    assert reading.dressed_by == "none"

    not_a_number = sensors.present(box, "the door is open")
    assert not_a_number.numeric is None, "a scale is a question about numbers, not about words"


def test_a_textual_value_may_be_coloured_by_a_list_of_patterns_instead_of_a_template() -> None:
    """First rule that matches wins; the default is what is left, and neither is a colour guess."""
    textual = Box(
        name="Door",
        box_type="mqtt",
        resource_uri="mqtt://b:1883/t",
        config={
            "text_colours": {
                "rules": [
                    {"pattern": "^open", "colour": "#d0021b", "background": "#ffffff"},
                    {"pattern": "closed", "colour": "green"},
                ],
                "default": {"colour": "grey"},
            }
        },
    )
    opened = sensors.present(textual, "open")
    assert opened.dressed_by == "colours"
    assert opened.styled == '<span style="color: #d0021b; background-color: #ffffff">open</span>'
    assert sensors.present(textual, "the door is closed").styled.startswith(
        '<span style="color: green"'
    )
    assert sensors.present(textual, "anything else").styled.startswith('<span style="color: grey"')

    both = Box(
        name="Door",
        box_type="mqtt",
        resource_uri="mqtt://b:1883/t",
        config={**textual.config, "style_template": "<b>{{ display }}</b>"},
    )
    dressed = sensors.present(both, "open")
    assert dressed.dressed_by == "template"
    assert dressed.styled == "<b>open</b>", (
        "the more explicit instruction wins, and says which it was"
    )


def test_a_colour_rule_with_a_pattern_that_cannot_compile_is_refused_at_the_box() -> None:
    assert "not a regular expression" in sensors.check_text_colours(
        {"text_colours": {"rules": [{"pattern": "([", "colour": "red"}]}}
    )
    assert "no valid colour" in sensors.check_text_colours(
        {"text_colours": {"rules": [{"pattern": "x", "colour": "red; position: fixed"}]}}
    )
    assert (
        sensors.check_text_colours({"text_colours": {"rules": [{"pattern": "x", "colour": "red"}]}})
        == ""
    )
    assert sensors.check_text_colours({}) == ""


def test_check_config_refuses_the_first_thing_that_is_wrong() -> None:
    assert sensors.check_config({"extract_template": "{{ data.x }}"}) == ""
    assert "extract_template" in sensors.check_config({"extract_template": "{{ data.x", **SCALE})
    assert "numeric" in sensors.check_config(
        {"numeric": {"min": 0, "max": 10, "representation": "hbar"}}
    )
