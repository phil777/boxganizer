"""Where Boxganizer keeps its data, and how a caller picks another place.

Precedence, first match wins:

1. an explicit ``path`` argument,
2. ``$BOXGANIZER_DB``,
3. ``$XDG_DATA_HOME/boxganizer/boxes.db``,
4. ``~/.local/share/boxganizer/boxes.db``.
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_VAR = "BOXGANIZER_DB"
APP_DIR = "boxganizer"
DB_FILENAME = "boxes.db"


def data_dir() -> Path:
    """Directory holding the default database."""
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base).expanduser() if base else Path.home() / ".local" / "share"
    return root / APP_DIR


def default_db_path() -> Path:
    """Path of the database used when nothing else is configured."""
    override = os.environ.get(ENV_VAR)
    if override:
        return Path(override).expanduser()
    return data_dir() / DB_FILENAME


def ensure_parent(path: Path) -> Path:
    """Create the directory of *path* if needed and return *path*."""
    parent = path.expanduser().parent
    parent.mkdir(parents=True, exist_ok=True)
    return path.expanduser()
