"""Reading a directory (or an archive) as a listing, with containment enforced.

A `directory` box browses the real filesystem inside the web UI. That means
serving arbitrary files, so every path coming from a client is resolved and
checked twice: it must stay inside the box's own resource directory, and — when
`BOXGANIZER_CONTENT_ROOT` is set — inside that root too.

Entry kinds drive the UI: a directory navigates, a file opens in whichever viewer
its inferred box type has.
"""

from __future__ import annotations

import mimetypes
import tarfile
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

from boxganizer import infer

#: Extensions shown as video, in order of how well browsers play them.
VIDEO_EXTENSIONS = frozenset({".mp4", ".webm", ".ogv", ".ogg", ".mov", ".m4v"})
ARCHIVE_EXTENSIONS = frozenset({".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar"})

MAX_ENTRIES = 2000


@dataclass
class Entry:
    """One row of a listing."""

    name: str
    sub: str
    path: str | None  # absolute, for a real file; None for a member of an archive
    kind: str  # "dir" | "file" | "image" | "text" | ...
    size: int
    box_type: str
    icon: str
    mime: str | None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class OutsideRoot(ValueError):
    """A path tried to climb out of the directory it belongs to."""


def safe_join(root: Path, sub: str | None) -> Path:
    """`root / sub`, resolved, refusing anything that escapes `root`.

    Symlinks are resolved before the check, so a link out of the tree is caught
    the same way as `../`.
    """
    base = root.resolve()
    if not sub or sub in (".", "/"):
        return base
    candidate = (base / sub).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise OutsideRoot(f"{sub!r} is outside {base}") from exc
    return candidate


def kind_of(path: Path) -> str:
    lowered = path.name.lower()
    if path.is_dir():
        return "dir"
    if lowered.endswith(tuple(VIDEO_EXTENSIONS)):
        return "video"
    if lowered.endswith(tuple(ARCHIVE_EXTENSIONS)):
        return "archive"
    mime = mimetypes.guess_type(path.name)[0] or ""
    if mime.startswith("image/"):
        return "image"
    if mime == "application/pdf":
        return "pdf"
    if mime.startswith("text/") or lowered.endswith((".md", ".json", ".yaml", ".yml", ".toml")):
        return "text"
    return "file"


def box_type_for(path: Path) -> str:
    """The box type a file or directory would be created with."""
    if path.is_dir():
        return "directory"
    best = infer.best_key_for(str(path))
    return best or "file"


def icon_for(path: Path, kind: str) -> str:
    from boxganizer import boxtypes

    if kind == "dir":
        return boxtypes.DirectoryResource.icon
    key = box_type_for(path)
    kind_cls = boxtypes.try_get(key)
    if kind_cls is not None and kind_cls is not boxtypes.FileResource:
        return kind_cls.icon
    if kind == "video":
        return "\U0001f3a5"  # 🎥
    return "\U0001f4c4" if kind == "file" else (kind_cls or boxtypes.FileResource).icon


def list_directory(root: Path, sub: str | None = None) -> list[Entry]:
    """The entries of `root / sub`, directories first, then by name."""
    target = safe_join(root, sub)
    if not target.is_dir():
        return []
    prefix = "" if not sub else sub.strip("/") + "/"
    entries: list[Entry] = []
    try:
        children = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError:
        return []
    for child in children[:MAX_ENTRIES]:
        if child.name.startswith(".") and not _show_hidden(target):
            continue
        kind = kind_of(child)
        entries.append(
            Entry(
                name=child.name,
                sub=f"{prefix}{child.name}",
                path=str(child),
                kind=kind,
                size=0 if child.is_dir() else _size(child),
                box_type=box_type_for(child),
                icon=icon_for(child, kind),
                mime=mimetypes.guess_type(child.name)[0],
            )
        )
    return entries


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _show_hidden(directory: Path) -> bool:
    """Dot files stay hidden unless the box points straight at a dot directory."""
    return directory.name.startswith(".")


def list_archive(path: Path) -> list[Entry]:
    """The members of a zip or tar archive. Other formats say so upstream."""
    lowered = path.name.lower()
    entries: list[Entry] = []
    if lowered.endswith(".zip"):
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist()[:MAX_ENTRIES]:
                entries.append(
                    Entry(
                        name=info.filename.rstrip("/").rsplit("/", 1)[-1],
                        sub=info.filename,
                        path=None,
                        kind="dir" if info.is_dir() else "file",
                        size=info.file_size,
                        box_type="directory" if info.is_dir() else "file",
                        icon="\U0001f4c1" if info.is_dir() else "\U0001f4c4",
                        mime=None,
                    )
                )
        return entries
    if tarfile.is_tarfile(path):
        with tarfile.open(path) as archive:
            for member in archive.getmembers()[:MAX_ENTRIES]:
                entries.append(
                    Entry(
                        name=Path(member.name).name,
                        sub=member.name,
                        path=None,
                        kind="dir" if member.isdir() else "file",
                        size=0 if member.isdir() else member.size,
                        box_type="directory" if member.isdir() else "file",
                        icon="\U0001f4c1" if member.isdir() else "\U0001f4c4",
                        mime=None,
                    )
                )
        return entries
    raise ValueError(f"cannot list the contents of {path.name}")


def media_type_for(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"
