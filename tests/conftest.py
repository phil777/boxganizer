"""Shared fixtures: a throwaway database per test."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
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


class CommitCounter:
    """Wraps a store's connection and counts the commits that actually reach SQLite.

    The tests that use it are about *how often the process syncs to disk*, which no query
    result shows. Timing a real fsync would make the suite depend on the machine, so the
    durability boundary is counted instead: same writes, fewer commits, provably.

    It lives here rather than in `test_store.py` because a helper imported as
    `from tests.test_store import ...` only resolves when the repository root is on
    `sys.path` -- true for `python -m pytest`, false for the bare `pytest` that `make test`
    runs. That mismatch let one invocation report a green suite and the other a broken one.
    A fixture needs no import at all, and `tests/test_suite_layout.py` now holds the line.
    """

    def __init__(self, connection: object) -> None:
        self._connection = connection
        self.commits = 0
        self.rollbacks = 0

    def commit(self) -> None:
        self.commits += 1
        self._connection.commit()  # type: ignore[attr-defined]

    def rollback(self) -> None:
        self.rollbacks += 1
        self._connection.rollback()  # type: ignore[attr-defined]

    def __getattr__(self, name: str) -> object:
        return getattr(self._connection, name)


@pytest.fixture
def commit_counter() -> Iterator[Callable[[BoxStore], CommitCounter]]:
    """Install a :class:`CommitCounter` on a store, and take it back off afterwards.

    Teardown restores the real connection even if the test forgot, or failed on the way out:
    a store left holding a counter reports commit counts to whatever test borrowed it next.
    """
    installed: list[tuple[BoxStore, object]] = []

    def install(box_store: BoxStore) -> CommitCounter:
        spy = CommitCounter(box_store._conn)
        installed.append((box_store, box_store._conn))
        box_store._conn = spy  # type: ignore[assignment]
        return spy

    yield install

    for box_store, connection in reversed(installed):
        box_store._conn = connection  # type: ignore[assignment]
