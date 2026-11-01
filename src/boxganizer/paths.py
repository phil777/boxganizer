"""What a resource URI means on disk.

`Box.resource_uri` is either a plain path (`/srv/data`, `file:///tmp/a%20b.pdf`) or
an address with a scheme (`https://…`, `mqtt://…`). Three things need to read it —
type inference, the file-serving endpoint and the file type's own validation — so
the parsing lives here once rather than three times.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from urllib.parse import unquote, urlsplit

#: The directory every file resource is read from, when it is set at all.
CONTENT_ROOT_ENV = "BOXGANIZER_CONTENT_ROOT"


def split_resource(resource_uri: str) -> tuple[str, str]:
    """`("scheme", "path")`, both lowercase where it matters.

    A bare path has an empty scheme. A single letter is a Windows drive, not a
    scheme.
    """
    cleaned = (resource_uri or "").strip()
    if "://" in cleaned:
        parts = urlsplit(cleaned)
        return parts.scheme.lower(), unquote(parts.path)
    return "", cleaned


def local_path(resource_uri: str) -> Path:
    """The path part of a file resource, as a :class:`~pathlib.Path`."""
    _, path = split_resource(resource_uri)
    return Path(path).expanduser()


def confine(explicit: Path | str | None = None) -> Path | None:
    """The directory reads are confined to, **or None when nothing is confined**.

    First match wins: the root a caller was configured with, then ``$BOXGANIZER_CONTENT_ROOT``,
    then nothing. Naming a directory *is* the opt-in — `box serve --confined`, `box index --root`,
    the environment variable — because a personal library on a real machine keeps its photos on
    another mount and its code one directory up, and quietly refusing those is a bug the reader
    discovers by their notes not being searchable (ADR 30).

    Where a *directory* is wanted rather than a boundary — the place the file selector opens on —
    use :func:`content_root`.
    """
    if explicit is not None and str(explicit).strip():
        return Path(str(explicit)).expanduser()
    from_env = os.environ.get(CONTENT_ROOT_ENV, "").strip()
    if from_env:
        return Path(from_env).expanduser()
    return None


def content_root(explicit: Path | str | None = None) -> Path:
    """Where the file selector opens, and what `--confined` confines to by default.

    First match wins: the root a caller was configured with, then
    ``$BOXGANIZER_CONTENT_ROOT``, then the user's home directory. **This is a starting place, not
    a boundary** — see :func:`confine` for the boundary, which is None unless one was named.
    """
    if explicit is not None and str(explicit).strip():
        return Path(str(explicit)).expanduser()
    from_env = os.environ.get(CONTENT_ROOT_ENV, "").strip()
    if from_env:
        return Path(from_env).expanduser()
    return Path.home()


def inside(path: Path, root: Path) -> bool:
    """True when *path* is *root* or below it, symlinks resolved."""
    try:
        path.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return False
    return True


def is_directory(resource_uri: str, *, is_dir: Callable[[Path], bool] = Path.is_dir) -> bool:
    """True when the resource names a directory rather than a file.

    ``is_dir`` is injectable so inference can be tested against paths that do not
    exist on the machine running the test.
    """
    scheme, path = split_resource(resource_uri)
    if scheme not in ("", "file") or not path:
        return False
    try:
        return bool(is_dir(Path(path).expanduser()))
    except OSError:  # unreadable path: not a directory we can claim
        return False
