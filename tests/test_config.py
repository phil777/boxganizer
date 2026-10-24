"""Database location: explicit path, environment override, XDG default."""

from __future__ import annotations

from pathlib import Path

import pytest

from boxganizer import config
from boxganizer.store import BoxStore


def test_explicit_path_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(config.ENV_VAR, str(tmp_path / "from-env.db"))
    chosen = tmp_path / "explicit.db"
    with BoxStore(chosen) as store:
        assert store.path == chosen


def test_environment_variable_selects_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "env.db"
    monkeypatch.setenv(config.ENV_VAR, str(target))
    assert config.default_db_path() == target


def test_xdg_data_home_is_honoured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(config.ENV_VAR, raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert config.default_db_path() == tmp_path / "boxganizer" / "boxes.db"


def test_home_is_the_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(config.ENV_VAR, raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert config.default_db_path() == tmp_path / ".local" / "share" / "boxganizer" / "boxes.db"


def test_parent_directories_are_created(tmp_path: Path) -> None:
    target = tmp_path / "deep" / "deeper" / "boxes.db"
    with BoxStore(target):
        assert target.exists()
