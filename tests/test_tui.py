"""The terminal UI must mount, list, filter, drill in and create boxes."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from boxganizer.models import Box
from boxganizer.store import BoxStore

pytest.importorskip("textual")

from textual.widgets import Input, Label, ListItem, ListView

from boxganizer.tui.app import BoxganizerApp, NewBoxScreen

ALL_BOXES = ["Books", "Garage", "House", "Kitchen", "Passport"]


def _run(app: BoxganizerApp, scenario: Any) -> None:
    async def main() -> None:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await scenario(app, pilot)

    asyncio.run(main())


def test_mount_lists_every_box(db_path: Path, populated: BoxStore) -> None:
    app = BoxganizerApp(db_path)

    async def scenario(app: BoxganizerApp, pilot: Any) -> None:
        assert app.shown == ALL_BOXES
        assert len(app.query(ListItem)) == 5

    _run(app, scenario)


def test_typing_filters_the_list(db_path: Path, populated: BoxStore) -> None:
    app = BoxganizerApp(db_path)

    async def scenario(app: BoxganizerApp, pilot: Any) -> None:
        box_input = app.query_one(Input)
        box_input.focus()
        await pilot.press(*"pass")
        await pilot.pause()
        assert app.shown == ["Passport"]
        box_input.value = ""
        await pilot.pause()
        assert app.shown == ALL_BOXES

    _run(app, scenario)


def test_detail_pane_follows_the_highlighted_box(db_path: Path, populated: BoxStore) -> None:
    app = BoxganizerApp(db_path)

    async def scenario(app: BoxganizerApp, pilot: Any) -> None:
        assert "Books" in app.detail_markup
        app.query_one(ListView).index = 2
        await pilot.pause()
        assert "House" in app.detail_markup
        assert "Garage, Kitchen" in app.detail_markup

    _run(app, scenario)


def test_enter_drills_into_children(db_path: Path, populated: BoxStore) -> None:
    app = BoxganizerApp(db_path)

    async def scenario(app: BoxganizerApp, pilot: Any) -> None:
        app.query_one(ListView).index = app.shown.index("Kitchen")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert app.shown == ["Passport"]
        await pilot.press("escape")
        await pilot.pause()
        assert app.shown == ALL_BOXES

    _run(app, scenario)


def test_a_box_created_by_the_cli_shows_up_on_reload(db_path: Path) -> None:
    app = BoxganizerApp(db_path)

    async def scenario(app: BoxganizerApp, pilot: Any) -> None:
        assert app.shown == []
        with BoxStore(db_path) as other:
            other.add(Box(name="From CLI"))
        app.action_reload()
        await pilot.pause()
        assert app.shown == ["From CLI"]

    _run(app, scenario)


def test_new_box_modal_creates_a_box(db_path: Path, populated: BoxStore) -> None:
    app = BoxganizerApp(db_path)

    async def scenario(app: BoxganizerApp, pilot: Any) -> None:
        app.action_new_box()
        await pilot.pause()
        assert isinstance(app.screen, NewBoxScreen)
        await pilot.press(*"Cellar")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert not isinstance(app.screen, NewBoxScreen)
        assert "Cellar" in app.shown
        assert populated.exists("Cellar") is True

    _run(app, scenario)


def test_cancelling_the_modal_creates_nothing(db_path: Path, populated: BoxStore) -> None:
    app = BoxganizerApp(db_path)

    async def scenario(app: BoxganizerApp, pilot: Any) -> None:
        app.action_new_box()
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, NewBoxScreen)
        assert app.shown == ALL_BOXES
        assert populated.exists("Ghost") is False

    _run(app, scenario)


def test_labels_carry_the_box_icon(db_path: Path, populated: BoxStore) -> None:
    app = BoxganizerApp(db_path)

    async def scenario(app: BoxganizerApp, pilot: Any) -> None:
        texts = [str(label.render()) for label in app.query(Label)]
        assert any("\U0001f373" in text for text in texts)

    _run(app, scenario)
