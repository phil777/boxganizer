"""The install carries every interface (ADR 24).

These read `pyproject.toml` rather than importing anything, because the thing under test is what
a person gets from `pipx install boxganizer` — a decision made in metadata, not in code.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


@pytest.fixture(scope="module")
def project() -> dict[str, object]:
    with PYPROJECT.open("rb") as handle:
        return tomllib.load(handle)["project"]


def test_every_interface_is_a_plain_dependency(project: dict[str, object]) -> None:
    listed = " ".join(project["dependencies"])  # type: ignore[index]
    for library in ("fastapi", "uvicorn", "textual", "aiomqtt", "jinja2", "markdown-it-py"):
        assert library in listed, f"{library} is a feature someone has to ask for"


def test_no_feature_extra_is_left_to_choose(project: dict[str, object]) -> None:
    """An extra that hides a feature is how a fully-featured install ends up mute.

    Only `dev` survives: test and lint tooling is not part of organising boxes.
    """
    extras = project.get("optional-dependencies", {})  # type: ignore[union-attr]
    assert set(extras) == {"dev"}, sorted(extras)


def test_the_console_script_is_still_the_one_command(project: dict[str, object]) -> None:
    assert project["scripts"] == {"box": "boxganizer.cli.main:main"}
