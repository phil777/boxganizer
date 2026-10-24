"""Shared fixtures: a throwaway database per test."""

from __future__ import annotations

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
