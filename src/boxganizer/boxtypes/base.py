"""The root of the box type hierarchy.

The source document calls the root of the tree ``Box``. In code the root is
:class:`BoxType`, because :class:`boxganizer.models.Box` is the stored record;
every other class here is named exactly as the document names it.

A type is stateless. It says what a box of that kind holds and what it needs —
an icon, whether it may contain other boxes, which resource it points at, which
metadata keys are mandatory. The box itself keeps the data and stores the type
under ``Box.box_type``.

Nothing in this module does work yet beyond declaration and shape validation.
Each type records what it will do in ``planned``, and ``box types`` shows it.
"""

from __future__ import annotations

from typing import ClassVar
from urllib.parse import urlsplit

from boxganizer.boxtypes import registry
from boxganizer.errors import InvalidBoxContent
from boxganizer.models import DEFAULT_ICON, Box

#: Type recorded on boxes created before the hierarchy existed.
DEFAULT_TYPE_KEY = "box"


def _scheme_of(uri: str) -> str:
    """Lowercase scheme of *uri*; a Windows drive letter is not a scheme."""
    scheme = urlsplit(uri).scheme.lower()
    return "" if len(scheme) <= 1 else scheme


class BoxType:
    """What every box type knows about itself."""

    key: ClassVar[str] = DEFAULT_TYPE_KEY
    label: ClassVar[str] = "Box"
    icon: ClassVar[str] = DEFAULT_ICON
    summary: ClassVar[str] = "Holds other boxes and, optionally, one resource."
    #: True only on the grouping levels of the hierarchy (Content, Resource, …).
    #: It is read from the class body once, at registration, so it does not pass
    #: to subclasses: a subclass is selectable unless it declares itself too.
    abstract: ClassVar[bool] = False
    allows_children: ClassVar[bool] = True
    resource_uri_required: ClassVar[bool] = False
    #: Schemes the resource may use; ``None`` means any, "file" also allows plain paths.
    resource_uri_schemes: ClassVar[frozenset[str] | None] = None
    #: Lowercase suffixes the resource must have, or ``None`` for any.
    extensions: ClassVar[frozenset[str] | None] = None
    required_metadata: ClassVar[frozenset[str]] = frozenset()
    #: True when at most one box of this type may exist in a database.
    singleton: ClassVar[bool] = False
    #: True when the resource is a directory the interfaces may list.
    lists_files: ClassVar[bool] = False
    #: What the box's icon is made from: "image", "pdf-page", or None for the
    #: type's emoji. See `boxganizer.thumbs`.
    thumbnail_kind: ClassVar[str | None] = None
    #: True for the one box that anchors the graph: nothing contains it.
    is_root: ClassVar[bool] = False
    #: True when `resource_uri` points at a local file the interfaces may read —
    #: which is not the same as existing: a directory or a URL is not served raw.
    serves_content: ClassVar[bool] = False
    #: Which live source the box reads outside this machine — "mqtt", "ssh" — or
    #: None. A frontend asks this instead of comparing `box_type` with a string.
    live_source: ClassVar[str | None] = None
    #: True when the store keeps this box's children in step with a filter over
    #: every box, rather than taking them one link at a time. See `boxganizer.filters`.
    resolves_membership: ClassVar[bool] = False
    #: True when such a folder asks for everything and has no filter to edit: the
    #: cabinet. Whatever its config holds is ignored.
    filter_is_fixed: ClassVar[bool] = False
    #: True when the search index reads this box's file and keeps its text, so a
    #: full-text search can find a box by what is *inside* it. See `boxganizer.search`.
    indexes_content: ClassVar[bool] = False
    #: True when the type places its children at coordinates of the user's choosing,
    #: so a new child can be told where to land. See `boxganizer.desktop`.
    arranges_icons: ClassVar[bool] = False
    #: True when the type draws its children as a growing map of nodes, which is the
    #: question the mindmap endpoints ask before answering. See `boxganizer.mindmap`.
    maps_nodes: ClassVar[bool] = False
    #: True when the type keeps words of its own, in a file Boxganizer writes beside the
    #: database. Such a box needs no resource when it is made: it is given one the first
    #: time there is something to keep, and that resource is what puts the words in the
    #: search index. See `boxganizer.authored`.
    writes_own_file: ClassVar[bool] = False
    #: What this type is meant to do once it is built. Empty means: already does all it claims.
    planned: ClassVar[str] = ""

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        registry.register(cls)

    # -- rules -------------------------------------------------------------

    @classmethod
    def validate(cls, box: Box) -> None:
        """Raise :class:`InvalidBoxContent` when *box* does not fit this type."""
        missing = sorted(
            key for key in cls.required_metadata if not str(box.metadata.get(key, "")).strip()
        )
        if missing:
            cls._reject(box, f"metadata {', '.join(missing)} is required")

        uri = (box.resource_uri or "").strip()
        if cls.resource_uri_required and not uri:
            cls._reject(box, f"a {cls.label} needs a resource")

        if uri:
            cls._validate_scheme(box, uri)
            cls._validate_extension(box, uri)

        cls.check(box)

    @classmethod
    def check(cls, box: Box) -> None:
        """Hook for rules that need logic. The default accepts everything."""

    @classmethod
    def _validate_scheme(cls, box: Box, uri: str) -> None:
        allowed = cls.resource_uri_schemes
        if allowed is None:
            return
        scheme = _scheme_of(uri)
        if scheme in allowed:
            return
        if scheme == "" and "file" in allowed:
            return
        wanted = " or ".join(f"{name}:" for name in sorted(allowed))
        cls._reject(box, f"resource {uri!r} needs {wanted}, not {scheme or 'no scheme'}")

    @classmethod
    def _validate_extension(cls, box: Box, uri: str) -> None:
        if cls.extensions is None:
            return
        path = urlsplit(uri).path.lower()
        if any(path.endswith(ext) for ext in cls.extensions):
            return
        wanted = ", ".join(sorted(cls.extensions))
        cls._reject(box, f"resource {uri!r} must end with one of: {wanted}")

    @classmethod
    def _reject(cls, box: Box, reason: str) -> None:
        raise InvalidBoxContent(cls.key, reason, box.name)

    # -- description -------------------------------------------------------

    @classmethod
    def describe(cls) -> dict[str, object]:
        """Machine-readable description, used by the web API and ``box types``."""
        parent = registry.parent_of(cls)
        return {
            "key": cls.key,
            "label": cls.label,
            "icon": cls.icon,
            "summary": cls.summary,
            "abstract": registry.is_abstract(cls),
            "allows_children": cls.allows_children,
            "thumbnail_kind": cls.thumbnail_kind,
            "is_root": cls.is_root,
            "live_source": cls.live_source,
            "resolves_membership": cls.resolves_membership,
            "filter_is_fixed": cls.filter_is_fixed,
            "arranges_icons": cls.arranges_icons,
            "maps_nodes": cls.maps_nodes,
            "writes_own_file": cls.writes_own_file,
            "indexes_content": cls.indexes_content,
            "parent": parent.key if parent is not None else None,
            "ancestors": [ancestor.key for ancestor in registry.ancestors_of(cls)],
            "resource_uri_required": cls.resource_uri_required,
            "resource_uri_schemes": sorted(cls.resource_uri_schemes)
            if cls.resource_uri_schemes
            else None,
            "extensions": sorted(cls.extensions) if cls.extensions else None,
            "required_metadata": sorted(cls.required_metadata),
            "planned": cls.planned,
        }

    @classmethod
    def is_grouping(cls) -> bool:
        """True when this class is structure rather than a choice of box type."""
        return registry.is_abstract(cls)

    @classmethod
    def path(cls) -> str:
        """Hierarchy path, root first: ``resource/file/text``."""
        chain = [cls.key, *(ancestor.key for ancestor in registry.ancestors_of(cls))]
        return "/".join(reversed(chain))


registry.register(BoxType)
