"""Box types: the hierarchy the project grew from.

Import this package and every type is registered:

    >>> from boxganizer import boxtypes
    >>> boxtypes.get("markdown").label
    'Markdown'
    >>> boxtypes.selectable_keys()[:3]
    ['label', 'file', 'text']

The classes are named after the headings of the source document; the document's
root ``Box`` is :class:`boxganizer.boxtypes.base.BoxType` here, because
:class:`boxganizer.models.Box` is the stored record. See ``doc/box-types.md``.
"""

from __future__ import annotations

from boxganizer.boxtypes import registry
from boxganizer.boxtypes.base import DEFAULT_TYPE_KEY, BoxType
from boxganizer.boxtypes.content import ContentBox, Label
from boxganizer.boxtypes.dynamic import (
    BrowserResource,
    DynamicResource,
    HomeAssistantResource,
    HTTPResource,
    MQTTResource,
    SensorResource,
    SnapshotResource,
    SSHResource,
    TerminalResource,
    WebResource,
)
from boxganizer.boxtypes.folder import Cabinet, DynamicFolder, FolderBox
from boxganizer.boxtypes.organizer import (
    Blueprint,
    CheckList,
    Dashboard,
    Desktop,
    Graph,
    Grid,
    ManualGraph,
    Mindmap,
    Model3D,
    OrganizerBox,
    ParentalGraph,
)
from boxganizer.boxtypes.resource import (
    ArchiveResource,
    DirectoryResource,
    FileResource,
    ImageResource,
    IndexedDirectoryResource,
    MarkdownResource,
    PDFResource,
    ResourceBox,
    TextResource,
    VideoResource,
)
from boxganizer.boxtypes.root import ROOT_BOX_NAME, RootBox
from boxganizer.boxtypes.viewport import (
    SimpleViewport,
    TilingViewport,
    ViewportBox,
    WindowedViewport,
)
from boxganizer.models import DEFAULT_ICON, Box

__all__ = [
    "DEFAULT_TYPE_KEY",
    "ROOT_BOX_NAME",
    "ArchiveResource",
    "Blueprint",
    "BoxType",
    "BrowserResource",
    "Cabinet",
    "CheckList",
    "ContentBox",
    "Dashboard",
    "Desktop",
    "DirectoryResource",
    "DynamicFolder",
    "DynamicResource",
    "FileResource",
    "FolderBox",
    "Graph",
    "Grid",
    "HTTPResource",
    "HomeAssistantResource",
    "ImageResource",
    "IndexedDirectoryResource",
    "Label",
    "MQTTResource",
    "ManualGraph",
    "MarkdownResource",
    "Mindmap",
    "Model3D",
    "OrganizerBox",
    "PDFResource",
    "ParentalGraph",
    "ResourceBox",
    "RootBox",
    "SSHResource",
    "SensorResource",
    "SimpleViewport",
    "SnapshotResource",
    "TerminalResource",
    "TextResource",
    "TilingViewport",
    "VideoResource",
    "ViewportBox",
    "WebResource",
    "WindowedViewport",
    "adopt_type_icon",
    "all_types",
    "ancestors_of",
    "children_of",
    "describe_all",
    "get",
    "known",
    "parent_of",
    "selectable_keys",
    "tree",
    "try_get",
]

get = registry.get
known = registry.known
try_get = registry.try_get
all_types = registry.all_types
parent_of = registry.parent_of
children_of = registry.children_of
ancestors_of = registry.ancestors_of
tree = registry.tree


def describe_all(*, include_abstract: bool = True) -> list[dict[str, object]]:
    """Every type as a plain dict, declaration order."""
    return [cls.describe() for cls in all_types(include_abstract=include_abstract)]


def adopt_type_icon(box: Box) -> Box:
    """Give *box* the icon of its type, unless the user chose one.

    Forty types carry forty icons precisely so that a picture looks like a
    picture without anyone setting it. An explicit ``--icon`` always wins.
    """
    kind = get(box.box_type)
    if box.icon == DEFAULT_ICON and kind.icon != DEFAULT_ICON:
        box.icon = kind.icon
    return box


def selectable_keys() -> list[str]:
    """Keys a user may actually pick: concrete types, declaration order."""
    return [cls.key for cls in all_types(include_abstract=False)]
