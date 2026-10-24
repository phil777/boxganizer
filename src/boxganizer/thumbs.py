"""Miniatures used as a box's icon.

An image box looks like its image, a PDF box like its first page. The miniature
is not stored in the database — it is derived from a file that may be replaced at
any time — so it lives in a cache directory under the user's data dir, keyed by
the box uuid and stamped with the source file's size and mtime so a changed file
produces a new key and the stale one is left for the cleaner.

Nothing here is required for a box to exist: if the source is missing, unreadable
or not something Pillow can open, the caller keeps the type's emoji icon.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

from boxganizer import config
from boxganizer.models import Box

#: Longest edge of a stored miniature, in pixels.
THUMB_EDGE = 160

#: Keys written into `box.config` about the icon.
ICON_SOURCE_KEY = "icon_source"
THUMB_KEY = "thumb"


@dataclass(frozen=True)
class Source:
    """What the miniature was made from, enough to tell if it is still current."""

    path: str
    size: int
    mtime: int

    def as_dict(self) -> dict[str, object]:
        return {"path": self.path, "size": self.size, "mtime": self.mtime}

    @staticmethod
    def of(path: Path) -> Source:
        stat = path.stat()
        return Source(path=str(path), size=stat.st_size, mtime=int(stat.st_mtime))


def cache_dir() -> Path:
    """Where miniatures live: `<data dir>/thumbnails`."""
    path = config.data_dir() / "thumbnails"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _key(box_uuid: str, source: Source) -> str:
    return f"{box_uuid}-{source.size}-{source.mtime}.png"


def path_for(box: Box, source: Source) -> Path:
    """The cache path for this box and this version of the source file."""
    return cache_dir() / _key(box.uuid, source)


def current_source(box: Box) -> Source | None:
    """The source recorded in the box, if the file still matches it."""
    recorded = (box.config or {}).get(THUMB_KEY)
    if not isinstance(recorded, dict) or not recorded.get("path"):
        return None
    try:
        fresh = Source.of(Path(str(recorded["path"])))
    except OSError:
        return None
    if fresh.size != recorded.get("size") or fresh.mtime != recorded.get("mtime"):
        return None
    return fresh


def make_miniature(source_path: Path, *, kind: str = "image", edge: int = THUMB_EDGE) -> bytes:
    """A PNG miniature that fits a square of `edge` pixels.

    `kind` is what the source file is: `"image"` scales the picture,
    `"pdf-page"` renders its first page. Anything else raises.
    """
    if kind == "pdf-page":
        return _pdf_first_page(source_path, edge=edge)
    if kind != "image":
        raise ValueError(f"no miniature can be made from a {kind!r} resource")
    try:
        from PIL import Image
    except ModuleNotFoundError as exc:  # pragma: no cover - Pillow is a dependency
        raise ValueError("Pillow is not installed") from exc
    try:
        with Image.open(source_path) as opened:
            opened.load()
            image: Image.Image = opened
            if image.mode not in ("RGB", "RGBA"):
                image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
            image.thumbnail((edge, edge))
            buffer = io.BytesIO()
            image.save(buffer, format="PNG", optimize=True)
            return buffer.getvalue()
    except Exception as exc:  # unreadable, truncated, unsupported: not our problem
        raise ValueError(f"cannot make a miniature of {source_path}: {exc}") from exc


def _pdf_first_page(source_path: Path, *, edge: int) -> bytes:  # pragma: no cover - see PDF job
    """The first page of a PDF, as a miniature."""
    try:
        import pypdfium2 as pdfium
    except ModuleNotFoundError as exc:
        raise ValueError("pypdfium2 is not installed") from exc
    pdf = pdfium.PdfDocument(str(source_path))
    try:
        page = pdf[0]
        bitmap = page.render(scale=1.0)
        pil = bitmap.to_pil()
        pil.thumbnail((edge, edge))
        buffer = io.BytesIO()
        pil.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()
    finally:
        pdf.close()


def resource_path(box: Box) -> Path | None:
    """The local file a box points at, if it points at one at all."""
    uri = (box.resource_uri or "").strip()
    if not uri:
        return None
    from boxganizer import paths

    scheme, path = paths.split_resource(uri)
    if scheme not in ("", "file") or not path:
        return None
    return Path(path).expanduser()


def ensure_thumb(box: Box, resource: Path) -> dict[str, object] | None:
    """Generate and remember a miniature for *box*, or None if it cannot be made.

    The returned dict is what belongs in `config.thumb`; the caller writes the
    box, because this module does not touch the database.
    """
    try:
        source = Source.of(resource)
    except OSError:
        return None
    target = path_for(box, source)
    if not target.exists():
        try:
            data = make_miniature(resource, kind=_kind_of(box))
        except ValueError:
            return None
        # Write aside, then move: a reader never sees a half-written file.
        temp = target.with_suffix(".tmp")
        temp.write_bytes(data)
        temp.replace(target)
    return source.as_dict()


def _kind_of(box: Box) -> str:
    from boxganizer import boxtypes

    return boxtypes.get(box.box_type).thumbnail_kind or "image"


def icon_from_resource(box: Box) -> Box:
    """Give *box* an icon made from its own resource, when its type asks for one.

    Silent when there is nothing to make it from: the box keeps the type's emoji.
    """
    from boxganizer import boxtypes

    kind = boxtypes.get(box.box_type).thumbnail_kind
    if not kind:
        return box
    resource = resource_path(box)
    if resource is None:
        return box
    return apply_icon(box, resource)


def apply_icon(box: Box, resource: Path) -> Box:
    """Point the box's icon at a miniature of *resource*, if one can be made."""
    thumb = ensure_thumb(box, resource)
    if thumb is None:
        return box
    box.config = {**(box.config or {}), THUMB_KEY: thumb, ICON_SOURCE_KEY: "thumbnail"}
    return box


def thumb_bytes(box: Box) -> bytes | None:
    """The stored miniature for *box*, regenerating it if the source changed."""
    source = current_source(box)
    if source is None:
        return None
    path = Path(source.path)
    target = path_for(box, source)
    if not target.exists():
        if not path.is_file():
            return None
        try:
            target.write_bytes(make_miniature(path, kind=_kind_of(box)))
        except ValueError:
            return None
    try:
        return target.read_bytes()
    except OSError:  # pragma: no cover - unreadable cache
        return None
