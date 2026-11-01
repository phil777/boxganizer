"""Shared fixtures: a throwaway database per test."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from boxganizer.models import Box
from boxganizer.store import BoxStore


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "boxes.db"


@pytest.fixture
def store(db_path: Path) -> Iterator[BoxStore]:
    box_store = BoxStore(db_path)
    try:
        yield box_store
    finally:
        box_store.close()


@pytest.fixture
def populated(store: BoxStore) -> BoxStore:
    """House → (Kitchen → Passport, Garage) plus an unrelated Books box."""
    store.add(Box(name="House", description="The whole building"))
    store.add(Box(name="Kitchen", description="Room for cooking", icon="🍳"))
    store.add(Box(name="Garage", description="Storage and tools"))
    store.add(
        Box(
            name="Passport",
            description="Expired in 2031",
            metadata={"country": "FR", "shelf": "top"},
        )
    )
    store.add(Box(name="Books", description="Novels and manuals"))
    store.link("House", "Kitchen")
    store.link("House", "Garage")
    store.link("Kitchen", "Passport")
    return store


def wait_for_index(client: object, timeout: float = 20.0) -> None:
    """Let the background reader finish before a test asserts on what the index holds.

    Saving a box now returns before its file has been read (ADR 32), which is the point of the
    change and a race for any test that asserts a word is findable straight afterwards. Waiting on
    the queue is the honest fix: waiting on a sleep passes on a fast machine and fails on a loaded
    one, which is how a suite starts needing `--rerun 3` and stops being believed.
    """
    app = getattr(client, "app", None)
    state = getattr(getattr(app, "state", None), "index_queue", None)
    if state is None:
        return  # an app built without the queue: nothing was ever queued
    deadline = time.monotonic() + timeout
    while True:
        with state["lock"]:
            busy = bool(state["todo"]) or bool(state["live"])
        if not busy:
            return
        if time.monotonic() > deadline:
            with state["lock"]:
                raise AssertionError(
                    f"index queue never drained: {len(state['todo'])} waiting, live={state['live']}"
                )
        time.sleep(0.02)
