"""What a resource URI means on disk.

`Box.resource_uri` is either a plain path (`/srv/data`, `file:///tmp/a%20b.pdf`) or
an address with a scheme (`https://…`, `mqtt://…`). Three things need to read it —
type inference, the file-serving endpoint and the file type's own validation — so
the parsing lives here once rather than three times.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from urllib.parse import unquote, urlsplit


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
