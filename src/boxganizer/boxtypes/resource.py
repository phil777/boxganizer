"""Resource boxes: a box that points at one thing outside Boxganizer.

Files on disk, folders on disk. Scaffolds: the type checks the shape of the
resource it points at, but none of them read anything yet.
"""

from __future__ import annotations

from typing import ClassVar

from boxganizer import fslist, paths
from boxganizer.boxtypes.base import BoxType
from boxganizer.errors import InvalidBoxContent
from boxganizer.models import Box

_FILE_SCHEMES = frozenset({"file"})  # plain paths are accepted as file: too


class ResourceBox(BoxType):
    """Base for anything that wraps one external thing."""

    key: ClassVar[str] = "resource"
    label: ClassVar[str] = "Resource"
    icon: ClassVar[str] = "\U0001f517"  # 🔗
    summary: ClassVar[str] = "Points at one thing outside Boxganizer."
    abstract: ClassVar[bool] = True
    allows_children: ClassVar[bool] = False
    resource_uri_required: ClassVar[bool] = True


class FileResource(ResourceBox):
    """One file, addressed by path or ``file://`` URL."""

    key: ClassVar[str] = "file"
    label: ClassVar[str] = "File"
    icon: ClassVar[str] = "\U0001f4c3"  # 📃
    summary: ClassVar[str] = "A file on disk, of any kind."
    serves_content: ClassVar[bool] = True
    resource_uri_schemes: ClassVar[frozenset[str] | None] = _FILE_SCHEMES
    planned: ClassVar[str] = "Detect the concrete kind (text, image, pdf, archive) from the file."

    @classmethod
    def check(cls, box: Box) -> None:
        """A file box holds a file. A directory belongs to a `directory` box."""
        if box.resource_uri and paths.is_directory(box.resource_uri):
            raise InvalidBoxContent(
                cls.key,
                f"{box.resource_uri} is a directory; use the 'directory' type for that",
                box.name,
            )


class TextResource(FileResource):
    """A file of plain text."""

    key: ClassVar[str] = "text"
    label: ClassVar[str] = "Text"
    icon: ClassVar[str] = "\U0001f524"  # 🔤
    summary: ClassVar[str] = "Plain text, shown as it is."
    planned: ClassVar[str] = "Sniff encoding and refuse binary files."


class MarkdownResource(TextResource):
    """A Markdown file, rendered instead of dumped."""

    key: ClassVar[str] = "markdown"
    label: ClassVar[str] = "Markdown"
    icon: ClassVar[str] = "\U0001f4dd"  # 📝
    summary: ClassVar[str] = "A .md file, rendered to HTML for the web app."
    extensions: ClassVar[frozenset[str] | None] = frozenset({".md", ".markdown", ".mdown"})
    planned: ClassVar[str] = "Render on the web page and as styled text in the terminal."


class ImageResource(FileResource):
    """A picture."""

    key: ClassVar[str] = "image"
    label: ClassVar[str] = "Image"
    icon: ClassVar[str] = "\U0001f5bc️"  # 🖼️
    summary: ClassVar[str] = "One image file."
    thumbnail_kind: ClassVar[str | None] = "image"
    extensions: ClassVar[frozenset[str] | None] = frozenset(
        {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".heic"}
    )
    planned: ClassVar[str] = (
        "Thumbnails in every interface; in-terminal preview where the terminal allows it."
    )


class VideoResource(FileResource):
    """A video file. Not in the source document: added so the explorer can
    preview video and infer a type for it instead of calling it a `file`."""

    key: ClassVar[str] = "video"
    label: ClassVar[str] = "Video"
    icon: ClassVar[str] = "\U0001f3a5"  # 🎥
    summary: ClassVar[str] = "A video file, played in place."
    extensions: ClassVar[frozenset[str] | None] = fslist.VIDEO_EXTENSIONS
    planned: ClassVar[str] = "Poster frames as icons, and chapter metadata."


class PDFResource(FileResource):
    """A PDF document, shown as a viewer and iconed with its first page."""

    key: ClassVar[str] = "pdf"
    label: ClassVar[str] = "PDF"
    icon: ClassVar[str] = "\U0001f4d5"  # 📕
    summary: ClassVar[str] = "One PDF file."
    extensions: ClassVar[frozenset[str] | None] = frozenset({".pdf"})
    #: The icon is a miniature of the first page.
    thumbnail_kind: ClassVar[str | None] = "pdf-page"
    planned: ClassVar[str] = "Page count in the detail panel; an outside-click opens it elsewhere."


class ArchiveResource(FileResource):
    """A compressed archive.

    The source doc points at :class:`DirectoryResource`: an archive is browsed
    like a folder, but its entries are virtual, so an archive stays a leaf here.
    """

    key: ClassVar[str] = "archive"
    label: ClassVar[str] = "Archive"
    icon: ClassVar[str] = "\U0001f5dc️"  # 🗜️
    summary: ClassVar[str] = "zip, tar, 7z and friends. Contents are listed, not stored."
    extensions: ClassVar[frozenset[str] | None] = fslist.ARCHIVE_EXTENSIONS
    planned: ClassVar[str] = "List entries through a read-only directory view."


class DirectoryResource(ResourceBox):
    """A folder on disk; may also hold boxes that Boxganizer adds by hand."""

    key: ClassVar[str] = "directory"
    label: ClassVar[str] = "Directory"
    icon: ClassVar[str] = "\U0001f4c1"  # 📁
    summary: ClassVar[str] = "A directory on disk."
    lists_files: ClassVar[bool] = True
    allows_children: ClassVar[bool] = True
    resource_uri_required: ClassVar[bool] = True
    resource_uri_schemes: ClassVar[frozenset[str] | None] = _FILE_SCHEMES
    planned: ClassVar[str] = "Show the real directory contents alongside stored children."


class IndexedDirectoryResource(DirectoryResource):
    """A directory whose listing is cached in the database."""

    key: ClassVar[str] = "indexed-directory"
    label: ClassVar[str] = "Indexed Directory"
    icon: ClassVar[str] = "\U0001f5c2️"  # 🗂️
    summary: ClassVar[str] = "A directory with a cached file index, so search is fast."
    planned: ClassVar[str] = "Index file names and contents; refresh on demand and on watch events."
