"""FastAPI application: JSON API plus a minimal HTML browser.

Create it with :func:`create_app` and hand it to uvicorn (``box serve`` does
both, through :func:`boxganizer.web.server.run`). The app owns one :class:`BoxStore`.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import mimetypes
import os
import tarfile
import zipfile
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Annotated, Any
from uuid import uuid4

from boxganizer import (
    __version__,
    boxtypes,
    desktop,
    filters,
    fslist,
    grid,
    infer,
    markdown,
    mindmap,
    mqtt,
    paths,
    resources,
    sensors,
    ssh,
    tags,
    thumbs,
    viewports,
    vnc,
)

# The endpoint below is called `search`, so the module that plans a query comes in
# under a name that cannot be mistaken for it.
from boxganizer import search as text_search
from boxganizer.errors import (
    BoxNotFound,
    ChildrenNotAllowed,
    DuplicateBoxName,
    GridError,
    HandFilingRefused,
    InvalidBoxContent,
    InvalidBoxName,
    MqttError,
    PlacementError,
    RootCannotBeDeleted,
    RootCannotBeFiled,
    UnknownBoxType,
)
from boxganizer.models import DEFAULT_ICON, Box
from boxganizer.store import BoxStore

#: Frames a second the page is sent, at most. RFB pushes an update whenever the screen
#: changes, and a machine watching a video changes constantly: without a cap this server
#: encodes PNGs and the page decodes them as fast as the pipe allows, for a picture nobody
#: sees better for it. Eight is above what a person reads as motion and far below what
#: would make the machine hum.
VNC_MAX_FPS = 8

#: How long an opened screen may go unwatched, in seconds. The page opens a session and then
#: asks for its stream; a browser killed in between leaves a socket held to somebody's
#: machine with nobody at this end, and that is reclaimed rather than kept for the lifetime
#: of the server.
VNC_SESSION_TTL = 180.0

#: How long a VNC box waits for a machine to say what it is. Shorter than the session
#: timeout on purpose: this answers a page that merely opened a box, and a machine that is
#: not there should say so before the person has moved on to something else.
VNC_PROBE_TIMEOUT = 3.0

try:
    from fastapi import Depends, FastAPI, HTTPException, Query, Request
    from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
    from fastapi.staticfiles import StaticFiles
    from fastapi.templating import Jinja2Templates
    from pydantic import BaseModel, Field, JsonValue
except ModuleNotFoundError as exc:  # pragma: no cover - only in a damaged install
    raise ModuleNotFoundError(
        "the web frontend needs fastapi, which every boxganizer install carries: repair the "
        "install (pipx install --force boxganizer)"
    ) from exc

#: Files larger than this are not handed to the browser.
MAX_RAW_BYTES = 32 * 1024 * 1024

#: Environment variable that confines served files to one subtree.
CONTENT_ROOT_ENV = "BOXGANIZER_CONTENT_ROOT"

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_STATIC_DIR = Path(__file__).parent / "static"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))


class BoxUpdate(BaseModel):
    """Any subset of a box's own fields. `uuid` and the id are never writable."""

    name: Annotated[str | None, Field(min_length=1, max_length=200)] = None
    description: Annotated[str | None, Field(max_length=4000)] = None
    icon: Annotated[str | None, Field(max_length=8)] = None
    box_type: Annotated[str | None, Field(max_length=60)] = None
    resource_uri: Annotated[str | None, Field(max_length=2000)] = None
    metadata: dict[str, Any] | None = None
    config: dict[str, Any] | None = None
    index_resource: bool | None = Field(
        default=None,
        description="Change whether this box's file is kept in the full-text index."
        " Omitted: leave the resource row as it is.",
    )


class DesktopSpot(BaseModel):
    """Where on a desktop an icon starts: canvas pixels from the top left.

    The page sends the spot the surface was right-clicked at, already divided by
    whatever scale that surface is drawn at, so a box created from a desktop shown
    inside a grid cell lands under the pointer too.
    """

    x: int = Field(ge=0, le=8192)
    y: int = Field(ge=0, le=8192)


class BoxCreate(BaseModel):
    """Request body for creating a box."""

    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    icon: str | None = None
    box_type: str = boxtypes.DEFAULT_TYPE_KEY
    resource_uri: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    config: dict[str, JsonValue] = Field(default_factory=dict)
    parent: str | None = Field(default=None, description="Name or uuid of the box to create it in.")
    cell: list[int] | None = Field(
        default=None, description="[row, column] when the parent is a grid."
    )
    place: DesktopSpot | None = Field(
        default=None, description="{x, y} when the parent arranges its icons."
    )
    index_resource: bool | None = Field(
        default=None,
        description="Keep this box's file in the full-text index. Omitted: the default"
        " for what it points at — a text or PDF, and a local file whose ending says"
        " words, yes; anything else no.",
    )


class GridPlace(BaseModel):
    """Put a child in a grid cell, swapping with whoever is there."""

    child: str = Field(description="Name or uuid of the child box.")
    cell: list[int] = Field(description="[row, column], both counted from 0.")


class ViewportNode(BaseModel):
    """Do something to one viewport: split it, close it, or say what it shows."""

    node: str = Field(default="", description="The pane's id, like v2.")
    direction: str = Field(
        default="horizontal", description="horizontal (side by side) or vertical (stacked)."
    )
    ratio: float | None = Field(
        default=None, description="Share of the space given to the first pane, 0.05 to 0.95."
    )
    child: str | None = Field(
        default=None, description="Name or uuid of the box to show in this viewport."
    )
    x: int | None = None
    y: int | None = None
    width: int | None = None
    height: int | None = None


class GridUnplace(BaseModel):
    """Take a child out of the grid; the box stays in the graph."""

    child: str


class GridResize(BaseModel):
    """A new grid size. Children outside it lose their cell, not their place."""

    rows: int = Field(ge=1, le=grid.MAX_ROWS)
    columns: int = Field(ge=1, le=grid.MAX_COLUMNS)


class DesktopMove(BaseModel):
    """Put a child's icon at a spot on the desktop surface."""

    child: str = Field(description="Name or uuid of the child box.")
    x: int = Field(ge=0, description="Pixels from the left edge of the surface.")
    y: int = Field(ge=0, description="Pixels from the top edge of the surface.")


class DesktopResize(BaseModel):
    """How much surface one icon takes."""

    child: str
    width: int = Field(ge=desktop.MIN_SIZE, le=desktop.MAX_SIZE)
    height: int = Field(ge=desktop.MIN_SIZE, le=desktop.MAX_SIZE)


class DesktopSettingsUpdate(BaseModel):
    """The magnetic grid: how coarse it is, and whether it is engaged."""

    grid: int = Field(ge=desktop.MIN_GRID_SIZE, le=desktop.MAX_GRID_SIZE)
    snap: bool


class MindmapSide(BaseModel):
    """Which side of the centre a branch grows on."""

    child: str = Field(description="Name or uuid of the node.")
    side: str | None = Field(default=None, description='"left" or "right".')
    flip: bool = Field(default=False, description="Send it to the other side instead.")


class MindmapOrder(BaseModel):
    """Slide a node among its siblings."""

    child: str
    parent: str | None = Field(
        default=None, description="The node it hangs from; the map itself when omitted."
    )
    delta: int = Field(description="-1 towards the first node of the branch, +1 towards the last.")


class MindmapHierarchy(BaseModel):
    """Move a node deeper into the map, or out towards the centre."""

    child: str
    direction: str = Field(
        description='"out" hangs it off the sibling above; "in" off the grandparent.'
    )


class MindmapNode(BaseModel):
    """A new node: a box, filed where the map wants it."""

    parent: str | None = Field(
        default=None, description="Node to hang it under; the centre by default."
    )
    after: str | None = Field(
        default=None, description="Sibling to place it after; last by default."
    )
    name: str | None = Field(default=None, description='Defaults to the next unused "new idea".')
    box_type: str | None = Field(
        default=None, description="A container by default; any type that holds boxes."
    )


class MindmapFill(BaseModel):
    """What was dragged onto a node, and which node caught it."""

    node: str
    with_box: str = Field(alias="with", description="Name or uuid of the box being dropped.")

    model_config = {"populate_by_name": True}


class SshRun(BaseModel):
    """One command to run on the far side, and how long to wait for it."""

    command: str = Field(description="The command, passed whole to the remote shell.")
    timeout: float = Field(
        default=ssh.DEFAULT_TIMEOUT,
        ge=ssh.MIN_TIMEOUT,
        le=ssh.MAX_TIMEOUT,
        description="Seconds before the command is cut off.",
    )


class ConfigUpdate(BaseModel):
    """Request body for replacing a box's config."""

    config: dict[str, JsonValue]


class FilterUpdate(BaseModel):
    """Request body for setting what a dynamic folder selects.

    `{}` asks for every box. An unrecognised clause is refused rather than stored,
    because a folder that silently selects nothing looks like an empty library.

    A string is the whole filter written as one expression (ADR 20), which is what the filter bar
    writes; the mapping form stays for what older folders hold and for `box edit`.
    """

    filter: dict[str, Any] | str


class FilterBody(BaseModel):
    """What a dynamic folder asks, and how many boxes that currently holds."""

    box: dict[str, object]
    filter: dict[str, object]
    describes: str
    usable: bool
    broken: str
    members: list[dict[str, object]]


class FilterClause(BaseModel):
    """One clause of an expression, as a row of controls.

    Sent so the page can draw a folder's filter as a list it can edit — add, remove, reorder —
    without owning a second parser. Two grammars in two languages is how a control and a stored
    filter start disagreeing about what a folder selects.
    """

    field: str
    value: str
    mode: str
    negate: bool


class FilterParse(BaseModel):
    """Request body for reading an expression without saving it anywhere."""

    expression: str


class FilterParsed(BaseModel):
    """What an expression means, whether it can be drawn as rows, and what it would select.

    `clauses` is null when the expression nests (brackets, or a mix of `and` with `or`): there is
    then no row order that would still mean the same thing after a reorder, so the text field is
    the editor and the page says so instead of drawing a list that would lie.
    """

    expression: str
    describes: str
    usable: bool
    broken: str
    join: str
    clauses: list[FilterClause] | None


class LinkCreate(BaseModel):
    """Request body for putting one box inside another.

    `move_from` turns the link into a **move**: the box is filed in this box and taken out of the
    one named. It is a parameter of the link rather than a second request the page makes because two
    requests are not one action — if the unlink failed after the link the box would be filed twice
    and the page would say it had moved, and if the link failed after the unlink the box would be
    off the shelf with nowhere new to be.
    """

    child: str
    move_from: str | None = Field(
        default=None,
        description="Name or uuid of the box to take the child out of, after filing it here.",
    )


def _store_of(request: Request) -> BoxStore:
    store: BoxStore | None = getattr(request.app.state, "store", None)
    if store is None:  # pragma: no cover - defensive
        raise HTTPException(status_code=503, detail="no store attached")
    return store


class CheckFix(BaseModel):
    """One repair, asked for by name, on one finding.

    The finding is not trusted from the wire: the server runs the same check and looks the
    finding up again, so the set of legal repairs is decided where the problem was found.
    A stale dialog offers a repair that no longer applies, and that is an error to read, not
    an action to take.
    """

    kind: str
    action: str
    box: str = ""
    path: str = ""
    value: str | None = None


class IndexRun(BaseModel):
    """Which pass the page is asking for.

    `outstanding` is the everyday one: look at the files, then read the ones that changed,
    went missing or were never read. `everything` re-reads the library, which is what a
    database copied to a new machine needs and what nothing else does.
    """

    scope: str = "outstanding"


class TagCreate(BaseModel):
    """A new tag: one name, a path, or both (`home/kitchen` under nothing)."""

    name: str
    parent: str | None = None
    colour: str | None = None


class TagEdit(BaseModel):
    """What to change about a tag. Omitted fields are left alone.

    `parent` is a name, or "" for the top of the tree — because "make this a root" has to be
    sayable, and leaving the field out cannot mean both "unchanged" and "no parent".
    """

    name: str | None = None
    parent: str | None = None
    colour: str | None = None
    clear_colour: bool = False


class BoxTagOne(BaseModel):
    """One tag, given to one box."""

    name: str


class BoxTags(BaseModel):
    """The whole tag set for one box. Empty clears it."""

    tags: list[str] = Field(default_factory=list)


class VncOpen(BaseModel):
    """The password a screen wants, if it wants one.

    In the body, never in the address: this request travels through access logs, browser
    histories and proxies, and a secret in any of those is a secret published. The field is
    read for the length of the handshake and then dropped by the client that received it.
    """

    password: str | None = None


class VncInput(BaseModel):
    """One thing the page did to a live screen.

    The session id travels with it: two tabs watching one machine are two sessions, each
    with its own pointer, and a stray click must not land on the other one's screen.
    """

    session: str
    kind: str = "pointer"
    x: int = 0
    y: int = 0
    button_mask: int = 0
    #: The browser's `key` for a key event — a character, or a name like `Enter`.
    key: str = ""
    down: bool = True


class NoteWrite(BaseModel):
    """What the page holds for a note: the text, and how it is to be read."""

    source: str = ""
    #: `markdown` or `text`. The file keeps its address either way — see the note type.
    format: str = "markdown"

    """One line of a checklist, as the page holds it."""

    text: str = ""
    checked: bool = False
    depth: int = 0
    #: `box` is a tickable item; `text` is a line of prose kept between them.
    kind: str = "box"
    #: Boxes hung on this line, in order. See :mod:`boxganizer.checklist`.
    refs: list[str] = []


class ChecklistItem(BaseModel):
    """One line of a checklist, as the page holds it."""

    text: str = ""
    checked: bool = False
    depth: int = 0
    #: `box` is a tickable item; `text` is a line of prose kept between them.
    kind: str = "box"
    #: Boxes hung on this line, in order. See :mod:`boxganizer.checklist`.
    refs: list[str] = []


class ChecklistWrite(BaseModel):
    """The whole list. The page sends what it shows, and the file becomes that.

    Replacing the list rather than patching it is deliberate: the file is the list, the
    page holds the file, and a set of granular edits would be a second order of operations
    to keep in step with a text editor that changes the same file between saves.
    """

    title: str = ""
    items: list[ChecklistItem] = []


StoreDep = Annotated[BoxStore, Depends(_store_of)]


def _warm_request_models() -> None:
    """Assemble every request model's schema once, here, in the importing thread.

    This module is written under ``from __future__ import annotations``, so each model's
    fields are strings until the model is used for the first time, and building a schema
    means looking those names up in this module's namespace. Do that first use inside a
    worker thread — which is how uvicorn runs an app — and two threads can be assembling the
    same half-built schema at the same moment, with one of them losing the namespace and
    raising ``AttributeError: __pydantic_core_schema__``. It surfaces as a failure on
    whichever request happens to arrive first, which is not a thing a library gets to ship.
    """
    for value in list(globals().values()):
        if (
            isinstance(value, type)
            and issubclass(value, BaseModel)
            and value.__module__ == __name__
        ):
            value.model_rebuild()


_warm_request_models()


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    try:
        yield
    finally:
        # A live VNC session is somebody else's machine holding a connection open for us, so
        # stopping this server gives every one of them back. Left in `app.state`, a session
        # outlives the event loop that owns its socket, and what the garbage collector makes
        # of that minutes later is a warning attached to unrelated code.
        sessions: dict[str, Any] = getattr(app.state, "vnc_sessions", None) or {}
        for key in list(sessions):
            with contextlib.suppress(Exception):
                await sessions.pop(key).close()
        store: BoxStore | None = getattr(app.state, "store", None)
        if store is not None and getattr(app.state, "owns_store", False):
            store.close()


def create_app(
    store: BoxStore | None = None,
    *,
    content_root: Path | str | None = None,
    mqtt_events: mqtt.EventStream | None = None,
) -> FastAPI:
    """Build the web app. Pass a store to reuse one (tests, embedding).

    `content_root` overrides `$BOXGANIZER_CONTENT_ROOT`, the directory every file
    read and every picker browse is confined to.

    `mqtt_events` replaces how an MQTT box is read. It is there for tests, which
    feed the page a story instead of a broker; production leaves it alone.
    """
    app = FastAPI(
        title="Boxganizer",
        version=__version__,
        description="Everything is a box.",
        lifespan=_lifespan,
    )
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    effective_root = content_root if content_root is not None else os.environ.get(CONTENT_ROOT_ENV)
    app.state.content_root = Path(effective_root).expanduser() if effective_root else None
    app.state.store = store if store is not None else BoxStore()
    app.state.owns_store = store is None
    # The store reads a file when a box is added or changed, so it has to be told the
    # same confine the page browses inside: a box made here would otherwise index
    # nothing at all, and the search would quietly never find it.
    if app.state.content_root is not None:
        app.state.store.content_root = app.state.content_root
    app.state.mqtt_events = mqtt_events or mqtt.aiomqtt_events
    app.state.mqtt_latest = mqtt.Latest()

    @app.exception_handler(BoxNotFound)
    async def _not_found(request: Request, exc: BoxNotFound) -> JSONResponse:
        return JSONResponse(
            status_code=404, content={"detail": {"error": "box_not_found", "name": exc.name}}
        )

    @app.exception_handler(DuplicateBoxName)
    async def _duplicate(request: Request, exc: DuplicateBoxName) -> JSONResponse:
        return JSONResponse(
            status_code=409, content={"detail": {"error": "duplicate_name", "name": exc.name}}
        )

    @app.exception_handler(InvalidBoxName)
    async def _invalid(request: Request, exc: InvalidBoxName) -> JSONResponse:
        return JSONResponse(
            status_code=422, content={"detail": {"error": "invalid_name", "reason": exc.reason}}
        )

    @app.exception_handler(ChildrenNotAllowed)
    async def _no_children(request: Request, exc: ChildrenNotAllowed) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"detail": {"error": "children_not_allowed", "parent_type": exc.parent_type}},
        )

    @app.exception_handler(HandFilingRefused)
    async def _no_hand_filing(request: Request, exc: HandFilingRefused) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"detail": {"error": "hand_filing_refused", "reason": exc.reason}},
        )

    @app.exception_handler(RootCannotBeFiled)
    async def _filed_root(request: Request, exc: RootCannotBeFiled) -> JSONResponse:
        return JSONResponse(
            status_code=409, content={"detail": {"error": "root_cannot_be_filed", "name": exc.name}}
        )

    @app.exception_handler(RootCannotBeDeleted)
    async def _deleted_root(request: Request, exc: RootCannotBeDeleted) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={
                "detail": {
                    "error": "root_cannot_be_deleted",
                    "name": exc.name,
                    "reason": str(exc),
                }
            },
        )

    @app.exception_handler(GridError)
    async def _bad_grid_move(request: Request, exc: GridError) -> JSONResponse:
        return JSONResponse(
            status_code=422, content={"detail": {"error": "bad_grid_move", "reason": str(exc)}}
        )

    @app.exception_handler(ssh.SshError)
    async def _bad_ssh(request: Request, exc: ssh.SshError) -> JSONResponse:
        """A box with no host, an address holding a password, or a bad option line."""
        return JSONResponse(
            status_code=422, content={"detail": {"error": "bad_ssh_box", "reason": str(exc)}}
        )

    @app.exception_handler(PlacementError)
    async def _bad_placement(request: Request, exc: PlacementError) -> JSONResponse:
        """A layout the type does not do, or a node not in the map it was asked about.

        Named for placement rather than for any one surface: the desktop, the grid and
        the mindmap all answer to it, and the reason says which.
        """
        return JSONResponse(
            status_code=422, content={"detail": {"error": "bad_placement", "reason": str(exc)}}
        )

    @app.exception_handler(tags.TagError)
    async def _bad_tag(request: Request, exc: tags.TagError) -> JSONResponse:
        return JSONResponse(
            status_code=422, content={"detail": {"error": "bad_tag", "reason": str(exc)}}
        )

    @app.exception_handler(MqttError)
    async def _bad_mqtt_target(request: Request, exc: MqttError) -> JSONResponse:
        return JSONResponse(
            status_code=422, content={"detail": {"error": "bad_mqtt_target", "reason": str(exc)}}
        )

    @app.exception_handler(UnknownBoxType)
    async def _unknown_type(request: Request, exc: UnknownBoxType) -> JSONResponse:
        return JSONResponse(
            status_code=422, content={"detail": {"error": "unknown_box_type", "key": exc.key}}
        )

    @app.exception_handler(InvalidBoxContent)
    async def _invalid_content(request: Request, exc: InvalidBoxContent) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "detail": {
                    "error": "invalid_for_type",
                    "type": exc.type_key,
                    "reason": exc.reason,
                }
            },
        )

    @app.get("/api/health")
    def health(store: StoreDep) -> dict[str, object]:
        return {"status": "ok", "version": __version__, "boxes": store.count()}

    @app.get("/api/types")
    def list_types(
        selectable_only: Annotated[bool, Query(description="Leave out grouping levels.")] = False,
    ) -> list[dict[str, object]]:
        return boxtypes.describe_all(include_abstract=not selectable_only)

    @app.get("/api/types/suggest")
    def suggest_type(
        resource_uri: Annotated[str, Query(description="The resource being pointed at.")],
        current: Annotated[str, Query(description="The type chosen so far.")] = "box",
    ) -> dict[str, object]:
        """A type that fits the resource better, or null. A proposal, never a change."""
        found = infer.suggest(resource_uri, current)
        return {"suggestion": found.as_dict() if found else None}

    @app.get("/api/boxes", response_model=None)
    def list_boxes(
        store: StoreDep, limit: Annotated[int, Query(ge=1, le=1000)] = 200
    ) -> list[dict[str, object]]:
        return _dump(store.list_boxes(limit=limit))

    @app.get("/api/boxes/{ref}")
    def read_box(ref: str, store: StoreDep) -> dict[str, object]:
        return _full(store, store.resolve(ref))

    @app.get("/api/root")
    def read_root(store: StoreDep) -> dict[str, object]:
        """The box the page starts on, found by its type.

        `/api/boxes` is a page of the library and a page is sorted by name, so it
        cannot answer "which box is the way in" for a library bigger than the page.
        This can, and it is what the landing page and the Root button ask.
        """
        box = store.root_box()
        if box is None:
            raise HTTPException(
                status_code=404,
                detail={"error": "no_root_box", "reason": "run `box init` to create one"},
            )
        return _full(store, box)

    @app.get("/api/boxes/{ref}/raw")
    def read_raw(ref: str, request: Request, store: StoreDep) -> Response:
        """The bytes behind a file box, for `<img>` and for the text view.

        Only types that say they serve content, only real regular files, and only
        inside `BOXGANIZER_CONTENT_ROOT` when that is set.
        """
        box = store.resolve(ref)
        kind = boxtypes.get(box.box_type)
        if not kind.serves_content:
            raise HTTPException(
                status_code=415,
                detail={"error": "type_not_servable", "type": kind.key},
            )
        if not box.resource_uri:
            raise HTTPException(
                status_code=404,
                detail={"error": "resource_missing", "name": box.name},
            )
        path = _local_path(box.resource_uri)
        root = getattr(request.app.state, "content_root", None)
        if root is not None and not _inside(path, root):
            raise HTTPException(
                status_code=403,
                detail={"error": "resource_out_of_range", "root": str(root)},
            )
        if not path.is_file():
            raise HTTPException(
                status_code=404,
                detail={"error": "resource_missing", "path": str(path)},
            )
        size = path.stat().st_size
        if size > MAX_RAW_BYTES:
            raise HTTPException(
                status_code=413,
                detail={"error": "resource_too_large", "size": size, "limit": MAX_RAW_BYTES},
            )
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        headers = {"X-Content-Type-Options": "nosniff", "Content-Disposition": "inline"}
        if media_type == "image/svg+xml":
            # An SVG can carry script; sandbox it so it cannot act as this origin.
            headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
        return Response(content=path.read_bytes(), media_type=media_type, headers=headers)

    @app.post("/api/boxes/{ref}/grid/place")
    def place_in_grid(ref: str, payload: GridPlace, store: StoreDep) -> dict[str, object]:
        box = store.resolve(ref)
        child = store.resolve(payload.child)
        return _grid_place(store, box.uuid, child.uuid, payload.cell)

    @app.post("/api/boxes/{ref}/grid/unplace")
    def unplace_from_grid(ref: str, payload: GridUnplace, store: StoreDep) -> dict[str, object]:
        box = store.resolve(ref)
        child = store.resolve(payload.child)
        cells = grid.cells_of(box.config)
        box.config = grid.to_config(box.config, cells=grid.unplace(cells, child.uuid))
        return store.update(box).to_dict()

    @app.post("/api/boxes/{ref}/grid/resize")
    def resize_grid(ref: str, payload: GridResize, store: StoreDep) -> dict[str, object]:
        box = store.resolve(ref)
        size = grid.GridSize(rows=payload.rows, columns=payload.columns)
        kept, lost = grid.unplace_outside(grid.cells_of(box.config), size)
        box.config = grid.to_config(box.config, size=size, cells=kept)
        saved = store.update(box)
        return saved.to_dict() | {"unplaced": lost}

    @app.post("/api/boxes/{ref}/desktop/move")
    def move_on_desktop(ref: str, payload: DesktopMove, store: StoreDep) -> dict[str, object]:
        """Place a child's icon. The magnetic grid has the last word on where it lands."""
        box = _desktop_box(store, ref)
        child = _desktop_child(store, box, payload.child)
        saved = desktop.move(box.config, child.uuid, payload.x, payload.y)
        box.config = saved
        return _with_rect(store, box, child.uuid)

    @app.post("/api/boxes/{ref}/desktop/resize")
    def resize_on_desktop(ref: str, payload: DesktopResize, store: StoreDep) -> dict[str, object]:
        """Change the size of one icon on the surface."""
        box = _desktop_box(store, ref)
        child = _desktop_child(store, box, payload.child)
        box.config = desktop.resize(box.config, child.uuid, payload.width, payload.height)
        return _with_rect(store, box, child.uuid)

    @app.post("/api/boxes/{ref}/desktop/settings")
    def write_desktop_settings(
        ref: str, payload: DesktopSettingsUpdate, store: StoreDep
    ) -> dict[str, object]:
        """Engage or disengage the magnetic grid, and set its size."""
        box = _desktop_box(store, ref)
        settings = desktop.Settings(grid=payload.grid, snap=payload.snap)
        box.config = desktop.to_config(box.config, settings=settings)
        return store.update(box).to_dict()

    @app.get("/api/boxes/{ref}/ssh")
    def read_ssh(ref: str, store: StoreDep) -> dict[str, object]:
        """Where this SSH box goes, and what it can do from here.

        The address comes back with any user name it holds, because it is not a secret
        who someone logs in as — but a box whose address *does* hold a secret is refused
        here as everywhere else, so there is nothing to redact.
        """
        box = _ssh_box(store, ref)
        target = ssh.destination(box)
        return {
            "box": box.to_dict(),
            "destination": target.as_dict(),
            "client_available": ssh.available(),
            "max_output_bytes": ssh.MAX_OUTPUT_BYTES,
        }

    @app.post("/api/boxes/{ref}/ssh/run")
    def run_ssh(ref: str, payload: SshRun, store: StoreDep) -> dict[str, object]:
        """Run one command on the far side and bring back what it printed.

        There is no session behind this: each command is one `ssh`, started and finished,
        so the page never holds a shell open on another machine while nobody is looking.
        """
        box = _ssh_box(store, ref)
        result = ssh.run(box, payload.command, timeout=payload.timeout)
        return result.as_dict()

    def _authored_box(store: BoxStore, ref: str) -> Box:
        """A box whose type keeps words of its own.

        Asked of the type rather than compared with a type key, so a note — which keeps a
        file exactly the way a checklist does — answers to the same endpoints, and a new
        type that writes files needs no new gate.
        """
        box = store.resolve(ref)
        if not boxtypes.get(box.box_type).writes_own_file:
            raise HTTPException(
                status_code=415,
                detail={"error": "keeps_no_words", "type": box.box_type},
            )
        return box

    def _words_file(store: BoxStore, box: Box, *, seed: str) -> Path:
        """Where this box's words live.

        The address the box already holds, when it has one: a person may point a note at
        minutes that exist, or a checklist at a list they keep in a repository, and those
        words belong to that file. Only a box with nothing pointed at it is given one beside
        the database — and being given it is what gives the box a resource row, which is the
        handle the index keys on. A list that exists only in the database is a list nobody
        can search.
        """
        from boxganizer import authored

        if box.resource_uri:
            return Path(box.resource_uri)
        target = authored.ensure_file(store.path, box.uuid, seed=seed)
        # The row is made first, with the origin that says Boxganizer wrote this file, and
        # then the box is pointed at it: `update` finds the row by its normalised address
        # and leaves the origin it was given alone.
        record = authored.adopt(store, box, target, seed=seed)
        box.resource_uri = str(target)
        store.update(box)
        box.resource = record
        return target

    def _screen_box(store: BoxStore, ref: str) -> Box:
        """A box whose type is a live screen.

        Asked of the type rather than compared with `"vnc"`, so anything that later
        inherits the behaviour answers to the same endpoints.
        """
        box = store.resolve(ref)
        if not boxtypes.get(box.box_type).draws_remote_screen:
            raise HTTPException(
                status_code=415,
                detail={"error": "not_a_screen", "type": box.box_type},
            )
        return box

    def _vnc_sessions(request: Request) -> dict[str, vnc.Screen]:
        """The live screens, keyed by session id.

        Per session and not per box, because a session is a pointer: two people, or two
        tabs, looking at one machine are two cursors, and RFB is built for that. They are
        dropped when their event stream ends — see the stream endpoint, which owns the
        screen's lifetime.
        """
        sessions: dict[str, vnc.Screen] | None = getattr(request.app.state, "vnc_sessions", None)
        if sessions is None:
            sessions = {}
            request.app.state.vnc_sessions = sessions
        return sessions

    async def _reclaim_vnc_sessions(sessions: dict[str, vnc.Screen]) -> None:
        """Close sessions nobody is watching.

        Opening a session holds a socket to someone's machine. A browser that is closed
        between the open and the stream never sends the close it promised, so an unwatched
        session is expired here rather than kept until the server restarts.
        """
        now = asyncio.get_running_loop().time()
        for key in list(sessions):
            screen = sessions[key]
            abandoned = screen.closed or (
                not screen.streamed and now - screen.opened_at > VNC_SESSION_TTL
            )
            if abandoned:
                sessions.pop(key)
                # awaited rather than fired and forgotten: a close that is dropped on the
                # floor is a socket that may never be closed at all
                with contextlib.suppress(Exception):
                    await screen.close()

    @app.get("/api/boxes/{ref}/vnc")
    async def vnc_status(ref: str, request: Request, store: StoreDep) -> dict[str, Any]:
        """What is on the other end of the address, without holding a session open.

        The box asks the machine which protocol it speaks, which authentication it wants,
        and how big its screen is — then hangs up. That is the whole of `probe`, and it is
        what lets the panel say "TigerVNC, 1920x1080, wants a password" instead of a
        spinner. Nothing here is a promise about the session endpoint: the machine could
        still refuse, and the session says so when it tries.
        """
        box = _screen_box(store, ref)
        try:
            dest = vnc.destination(box)
        except vnc.VncError as exc:
            return {"destination": None, "reachable": False, "error": str(exc)}
        try:
            # The file-backed password is used for the question too, so a box that is
            # configured to connect unattended reports the screen it will actually get,
            # instead of "wants a password" forever.
            found = await vnc.probe(dest, timeout=VNC_PROBE_TIMEOUT, password=vnc.password_for(box))
        except (TimeoutError, vnc.VncError, OSError) as exc:
            return {
                "destination": dest.as_dict(),
                "reachable": False,
                "error": str(exc) or type(exc).__name__,
            }
        return {"destination": dest.as_dict(), "reachable": True, "screen": found.as_dict()}

    @app.post("/api/boxes/{ref}/vnc/open")
    async def vnc_open(
        ref: str, body: VncOpen, request: Request, store: StoreDep
    ) -> dict[str, Any]:
        """Start a session and hand back its id, before any picture is asked for.

        Opening is its own call because authentication is its own event. A password cannot
        travel in the address of an event stream — a URL is written into the access log, the
        browser history and every proxy between — and it cannot be asked for inside a stream
        that has already begun. So the page asks first, gets `password_needed` back if the
        screen wants one, and comes again with the answer typed into a field.

        The password is put into the session's `Screen` for the length of the handshake and
        then dropped by the client itself. Nothing here writes it to the library.
        """
        box = _screen_box(store, ref)
        try:
            dest = vnc.destination(box)
            password = vnc.password_for(box, override=body.password)
        except vnc.VncError as exc:
            raise HTTPException(
                status_code=422, detail={"error": "bad_vnc_address", "reason": str(exc)}
            ) from exc
        sessions = _vnc_sessions(request)
        await _reclaim_vnc_sessions(sessions)
        screen = vnc.Screen(dest, password=password)
        session = uuid4().hex
        try:
            info = await screen.start()
        except vnc.AuthenticationNeeded as exc:
            return {
                "session": None,
                "password_needed": True,
                "reason": str(exc),
                "address": dest.url,
            }
        except (TimeoutError, vnc.VncError, OSError) as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "vnc_unreachable",
                    "reason": str(exc) or type(exc).__name__,
                    "address": dest.url,
                },
            ) from exc
        sessions[session] = screen
        return {
            "session": session,
            "password_needed": False,
            "name": info.server_name,
            "version": info.version,
            "width": screen.width,
            "height": screen.height,
            "address": dest.url,
            "used_password": screen.used_password,
        }

    @app.get("/api/boxes/{ref}/vnc/stream")
    async def vnc_stream(
        ref: str, request: Request, store: StoreDep, session: str = ""
    ) -> StreamingResponse:
        """A live screen, as the frames the machine sends when something changes.

        RFB pushes a FramebufferUpdate when the screen moves and nothing when it does not,
        so there is no timer in this loop and no frame rate to choose: the pace belongs to
        the other machine, and an idle desktop costs the page nothing. Each frame is the
        whole framebuffer re-encoded as PNG on a worker thread — a 1280x800 encode is tens
        of milliseconds, and the event loop must not spend them.

        The screen is closed by a watcher that notices the browser going away, which is the
        only way an unfinished read ends promptly: cancelling the read instead would leave
        the protocol position unknown, and the next byte would be interpreted as a message
        type in the middle of a rectangle.
        """
        box = _screen_box(store, ref)
        try:
            dest = vnc.destination(box)
        except vnc.VncError as exc:
            raise HTTPException(
                status_code=422,
                detail={"error": "bad_vnc_address", "reason": str(exc)},
            ) from exc
        sessions = _vnc_sessions(request)
        screen = sessions.get(session)
        if screen is None:
            # No session means the open call was never made or this screen was already
            # closed. Answering with an empty stream would leave the panel spinning.
            raise HTTPException(
                status_code=404,
                detail={"error": "no_such_session", "reason": "open the screen first"},
            )
        screen.streamed = True

        async def events() -> AsyncIterator[str]:
            async def watch() -> None:
                # Two readings, a second apart. An ASGI transport that has finished handing
                # over the request body answers `http.disconnect` as its ordinary idle state,
                # and a screen that closed itself the moment it opened is a screen nobody can
                # look at. A real disconnect is handled the other way anyway: the server
                # cancels the response task, and the `finally` below closes the screen.
                seen = 0
                while seen < 2:
                    await asyncio.sleep(1.0)
                    seen = seen + 1 if await request.is_disconnected() else 0
                await screen.close()

            watcher = asyncio.create_task(watch())
            try:
                yield (
                    "event: info\ndata: "
                    + json.dumps(
                        {
                            "session": session,
                            "name": screen.name,
                            "version": screen.server_version,
                            "width": screen.width,
                            "height": screen.height,
                            "address": dest.url,
                            "used_password": screen.used_password,
                        }
                    )
                    + "\n\n"
                )
                clock = asyncio.get_running_loop()
                interval = 1.0 / VNC_MAX_FPS
                while True:
                    began = clock.time()
                    try:
                        frame = await screen.next_frame()
                    except (vnc.VncError, OSError, asyncio.IncompleteReadError):
                        break
                    if frame is None:
                        break
                    yield "event: frame\ndata: " + base64.b64encode(frame).decode() + "\n\n"
                    # the framebuffer held here is the screen as of now, so skipping the
                    # frames that happened in between loses nothing that is still true
                    rest = interval - (clock.time() - began)
                    if rest > 0:
                        await asyncio.sleep(rest)
            except (TimeoutError, vnc.VncError, OSError) as exc:
                message = str(exc) or type(exc).__name__
                yield "event: error\ndata: " + json.dumps({"message": message}) + "\n\n"
            finally:
                watcher.cancel()
                await screen.close()
                sessions.pop(session, None)
                yield "event: end\ndata: {}\n\n"

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/boxes/{ref}/vnc/input")
    async def vnc_input(
        ref: str, body: VncInput, request: Request, store: StoreDep
    ) -> dict[str, Any]:
        """Hand the screen one pointer move, button or keystroke.

        Pointer position and button mask travel together because that is how RFB sends
        them, and because a window dragged by its title bar is a held button with a
        position that will not stop changing. A keystroke arrives as the browser's `key`,
        and `vnc.keysym` decides what that is worth to the far machine — a character in the
        Latin-1 range needs no table, a name is looked up, and anything else is dropped
        rather than guessed at.
        """
        box = _screen_box(store, ref)
        screen = _vnc_sessions(request).get(body.session)
        if screen is None:
            raise HTTPException(
                status_code=410,
                detail={
                    "error": "session_gone",
                    "reason": "that screen's session has ended; open the box again",
                },
            )
        try:
            if body.kind == "key":
                symbol = vnc.keysym(body.key)
                if symbol is None:
                    return {"ok": True, "ignored": f"no keysym for {body.key!r}"}
                await screen.key(symbol, down=body.down)
            else:
                x = min(max(0, body.x), max(screen.width - 1, 0))
                y = min(max(0, body.y), max(screen.height - 1, 0))
                await screen.pointer(x, y, body.button_mask)
        except (vnc.VncError, OSError) as exc:
            raise HTTPException(
                status_code=409,
                detail={"error": "screen_closed", "reason": str(exc)},
            ) from exc
        return {"ok": True, "box": box.name}

    @app.post("/api/boxes/{ref}/vnc/close")
    async def vnc_close(
        ref: str, body: VncInput, request: Request, store: StoreDep
    ) -> dict[str, Any]:
        """End a session early, from the page's Stop button."""
        _screen_box(store, ref)
        sessions = _vnc_sessions(request)
        screen = sessions.pop(body.session, None)
        if screen is not None:
            await screen.close()
        return {"ok": True, "closed": screen is not None}

    @app.get("/api/boxes/{ref}/note")
    def note_get(ref: str, request: Request, store: StoreDep) -> dict[str, object]:
        """The note's words, the format they are written to be read as, and — when that
        format is markdown — the rendering of them.

        The rendered HTML comes from the same function the markdown file box uses, so a
        note and a `.md` file cannot disagree about what a heading or a link is, and the
        safety rules that keep a file's own HTML as text apply here once rather than twice.
        """
        box = _authored_box(store, ref)
        from boxganizer import authored, boxrefs

        target = _words_file(store, box, seed=authored.EMPTY_NOTE)
        source = authored.read(target)
        section = box.config.get("note") if isinstance(box.config, dict) else None
        fmt = (section or {}).get("format", "markdown")
        html = markdown.render(source) if fmt == "markdown" else ""
        children = store.children(box.uuid)
        by_uuid = {child.uuid: child for child in children}
        named = boxrefs.mentions(source)
        return {
            "uuid": box.uuid,
            "name": box.name,
            "source": source,
            "format": fmt,
            "html": html,
            "file": str(target),
            # the boxes this note names inside its own text, resolved where possible
            "mentioned": [
                {
                    "uuid": uuid,
                    "name": by_uuid[uuid].name if uuid in by_uuid else None,
                    "icon": by_uuid[uuid].icon if uuid in by_uuid else None,
                    "box_type": by_uuid[uuid].box_type if uuid in by_uuid else None,
                    "gone": uuid not in by_uuid,
                }
                for uuid in named
            ],
            # and everything filed here, whether the text speaks of it or not
            "children": [
                {
                    "uuid": child.uuid,
                    "name": child.name,
                    "icon": child.icon,
                    "box_type": child.box_type,
                }
                for child in children
            ],
        }

    @app.put("/api/boxes/{ref}/note")
    def note_put(ref: str, body: NoteWrite, request: Request, store: StoreDep) -> dict[str, object]:
        """Write the note, keep the graph in step with what it names, reindex the words.

        A box named inside the text is filed here as well. That is not the text ordering
        the graph: it is the difference between a note that references a box and a note
        that can lose it. The other direction stays one-way on purpose — deleting the
        sentence does not unlink the box, because the box may hold things the note never
        mentioned, and quietly emptying it would be the page making a filing decision
        nobody asked it to make.
        """
        box = _authored_box(store, ref)
        from boxganizer import authored, boxrefs
        from boxganizer import search as text_search

        fmt = body.format if body.format in ("markdown", "text") else "markdown"
        target = _words_file(store, box, seed=authored.EMPTY_NOTE)
        try:
            if box.resource_uri and str(target) != str(authored.path_for(store.path, box.uuid)):
                authored.write_at(target, body.source)
            else:
                authored.write(store.path, box.uuid, body.source)
        except OSError as exc:
            raise HTTPException(
                status_code=422,
                detail={"error": "words_not_writable", "path": str(target), "reason": str(exc)},
            ) from exc
        if box.resource_id is not None:
            text_search.sync_resource(store, int(box.resource_id))
        # the format is a property of the box, kept beside its other view settings
        section = box.config.get("note") if isinstance(box.config, dict) else None
        box.config = {**(box.config or {}), "note": {**(section or {}), "format": fmt}}
        store.update(box)
        attached: list[str] = []
        missing: list[str] = []
        for uuid in boxrefs.mentions(body.source):
            try:
                store.resolve(uuid)
            except BoxNotFound:
                missing.append(uuid)
                continue
            store.link(box.uuid, uuid)  # parent first, as the store takes it
            attached.append(uuid)
        return {
            "ok": True,
            "format": fmt,
            "characters": len(body.source),
            "file": str(target),
            "attached": attached,
            "unknown": missing,
        }

    @app.get("/api/boxes/{ref}/checklist")
    def checklist_get(ref: str, request: Request, store: StoreDep) -> dict[str, object]:
        """The list as it stands, with each attached child resolved to the box it names."""
        box = _authored_box(store, ref)
        from boxganizer import authored
        from boxganizer import checklist as list_of_things

        target = _words_file(store, box, seed=list_of_things.EMPTY)
        list_ = list_of_things.parse(authored.read(target))
        children = store.children(box.uuid)
        by_uuid = {child.uuid: child for child in children}

        def describe(item: list_of_things.Item) -> dict[str, Any]:
            shape = item.as_dict()
            shape["attached"] = [
                {
                    "uuid": uuid,
                    "name": by_uuid[uuid].name if uuid in by_uuid else None,
                    "icon": by_uuid[uuid].icon if uuid in by_uuid else None,
                    "box_type": by_uuid[uuid].box_type if uuid in by_uuid else None,
                    "gone": uuid not in by_uuid,
                }
                for uuid in item.refs
            ]
            return shape

        referenced = set(list_of_things.referenced(list_))
        return {
            "uuid": box.uuid,
            "name": box.name,
            "title": list_.title,
            "items": [describe(item) for item in list_.items],
            # A child filed here that is on no item is shown, not hidden: nothing filed in a
            # box should be invisible because the file does not mention it.
            "unattached": [
                {
                    "uuid": child.uuid,
                    "name": child.name,
                    "icon": child.icon,
                    "box_type": child.box_type,
                }
                for child in children
                if child.uuid not in referenced
            ],
            "file": str(target),
            "summary": list_.summary(),
            "indexed": bool(box.resource_id is not None),
        }

    @app.put("/api/boxes/{ref}/checklist")
    def checklist_put(
        ref: str, body: ChecklistWrite, request: Request, store: StoreDep
    ) -> dict[str, object]:
        """Write the list, keep the graph in step with it, and reindex what it now says.

        Every box the file mentions is made a child of this box if it is not one already —
        that is what a drag from the panel means. Boxes that are children and no longer
        mentioned stay children: taking an icon off a line is not the same as throwing the
        box away, and the page shows them as unattached.
        """
        box = _authored_box(store, ref)
        from boxganizer import authored
        from boxganizer import checklist as list_of_things
        from boxganizer import search as text_search

        target = _words_file(store, box, seed=list_of_things.EMPTY)
        names = {child.uuid: child.name for child in store.children(box.uuid)}
        list_ = list_of_things.Checklist(
            title=body.title,
            items=[
                list_of_things.Item(
                    text=item.text,
                    checked=item.checked,
                    depth=max(0, item.depth),
                    kind=item.kind,
                    refs=list(dict.fromkeys(item.refs)),
                )
                for item in body.items
            ],
        )
        authored.write(store.path, box.uuid, list_of_things.render(list_, labels=names))
        # The words changed, so the index must change with them — that is the promise the file
        # is kept for.
        if box.resource_id is not None:
            text_search.sync_resource(store, int(box.resource_id))
        attached: list[str] = []
        missing: list[str] = []
        for uuid in list_of_things.referenced(list_):
            try:
                store.resolve(uuid)
            except BoxNotFound:
                missing.append(uuid)
                continue
            store.link(box.uuid, uuid)  # parent first, as the store takes it
            attached.append(uuid)
        return {
            "ok": True,
            "file": str(target),
            "items": len(list_.items),
            "summary": list_.summary(),
            "attached": attached,
            # A link naming a box that is not in the library is reported, not deleted: the
            # file is the person's, and the box may be one rename or one restore away.
            "unknown": missing,
        }

    @app.get("/api/boxes/{ref}/markdown")
    def read_markdown(
        request: Request,
        ref: str,
        store: StoreDep,
        sub: Annotated[
            str,
            Query(description="A markdown file inside a directory box, when this is a preview."),
        ] = "",
    ) -> dict[str, object]:
        """The box's file, as HTML and as the text it came from.

        Rendered on the server rather than in the page, because the parser that decides
        what is safe to show is the one in `boxganizer.markdown`, and a second
        implementation in JavaScript would be a second set of rules to fall behind. The
        source comes back with it so that switching to the raw view costs no second
        request — and so the two views can never disagree about which file they read.

        A file outside the content confine, or one that is not text, answers with the
        reason in `note` and nothing in `html`: the page says that instead of showing an
        empty box.

        `sub` is for a preview of a file *inside* a directory box. Such a file is not a
        box — nothing in the library has a uuid for it — so sending one to this endpoint as
        a `ref` could only ever answer `box_not_found`, which is the bug this parameter
        closes. Here the directory box is not the subject, it is the confine: the path is
        joined inside its root and refuses to leave, and the file that comes out is
        rendered by the same rules as any other.
        """
        box = store.resolve(ref)
        if sub:
            _root, inside = _box_file(store, request, ref, sub, need_list=True)
            found = text_search.extract_resource(
                str(inside),
                root=getattr(request.app.state, "content_root", None),
                pdf=False,
            )
            if not found.usable:
                return {"html": "", "source": "", "note": found.note}
            return {"html": markdown.render(found.text), "source": found.text, "note": ""}
        if not box.resource_uri:
            return {"html": "", "source": "", "note": "the box points at no file"}
        found = text_search.extract_resource(
            box.resource_uri,
            root=getattr(request.app.state, "content_root", None),
            pdf=False,
        )
        if not found.usable:
            return {"html": "", "source": "", "note": found.note}
        return {"html": markdown.render(found.text), "source": found.text, "note": ""}

    def _index_job(request: Request) -> dict[str, Any]:
        """The running or last indexation, as this process remembers it.

        Kept on the app rather than the database because it describes work happening now,
        in this process: a second Boxganizer reading the same files is a different job, and
        after a restart there is nothing to report but the counts.
        """
        job = getattr(request.app.state, "index_job", None)
        if job is None:
            job = {"running": False, "scope": "", "started": "", "last": None, "task": None}
            request.app.state.index_job = job
        return job

    @app.get("/api/index/status")
    def index_status(
        request: Request,
        store: StoreDep,
        check: Annotated[
            bool,
            Query(description="Look at the files first, so 'out of date' is not stale news."),
        ] = False,
    ) -> dict[str, Any]:
        """What the index holds, what it is waiting for, and whether a pass is running.

        `check=1` stats every watched file before counting. Without it the answer is what
        the database was last told, which for a file edited in a text editor an hour ago is
        a polite lie. The stat pass is one `stat` per file and reads nothing — the same
        thing `box index --check` does — so the page asks for it when the panel is drawn,
        and asks plainly while a pass is running and the numbers are moving anyway.

        It writes, and that is allowed here for one reason: what it writes is a cache of
        what the filesystem says about a file, not anything a person typed. The stored flag
        is not a decision and loses nothing if it is refreshed twice.
        """
        if check:
            store.check_observations()
        body: dict[str, Any] = dict(store.index_counts())
        if body["unreadable"]:
            # Named, because "1 could not be read" is a riddle: the useful part is which.
            body["unreadable_examples"] = store.unreadable_references(limit=3)
        job = _index_job(request)
        return {**body, "running": job["running"], "scope": job["scope"], "last": job["last"]}

    @app.get("/api/index/pending")
    def index_pending(
        store: StoreDep, limit: Annotated[int, Query(ge=1, le=1000)] = 200
    ) -> dict[str, Any]:
        """The names behind the number in the status line.

        Read from the rows, not from the filesystem: this is the companion of the standing
        line, which says what the database believes. A person who wants the truth about the
        disk clicks "Index what changed", and that pass looks and then re-draws this.
        """
        return {"items": store.needing_index(limit=limit), **store.index_counts()}

    @app.post("/api/index/run")
    async def index_run(request: Request, store: StoreDep, body: IndexRun) -> dict[str, Any]:
        """Read the files, in the background, and let the page ask how it went.

        A pass over a library is not a fast request: it opens every file the index is
        wanted for, and a page that waits on it looks frozen and gets closed. So the work
        runs on a worker thread — reading files is exactly the kind of thing the event loop
        should not do itself — and the answer here is only that it started.
        """
        job = _index_job(request)
        if job["running"]:
            raise HTTPException(
                status_code=409,
                detail={"error": "index_running", "scope": job["scope"]},
            )
        scope = body.scope if body.scope in ("outstanding", "everything") else "outstanding"
        root = getattr(request.app.state, "content_root", None)

        async def pass_() -> None:
            job["running"] = True
            job["scope"] = scope
            job["started"] = datetime.now(UTC).isoformat(timespec="seconds")
            began = monotonic()
            try:
                if scope == "outstanding":
                    # Look first, then read what that noticed: a file edited outside
                    # Boxganizer is invisible until something stats it.
                    await asyncio.to_thread(store.check_observations)
                before = store.index_counts()
                report = await asyncio.to_thread(
                    text_search.rebuild, store, root=root, only_stale=(scope == "outstanding")
                )
                after = store.index_counts()
                # What the pass changed is read back from the index rather than added up as
                # it went: a folder walk brings in files that were not on the list the pass
                # was built from, so a tally kept by the loop under-reports exactly the work
                # the folder did — "0 read" under a panel that has just filled with 70.
                job["last"] = {
                    "scope": scope,
                    "resources": report.resources,
                    "indexed": report.indexed,
                    "added": max(0, int(after["indexed"]) - int(before["indexed"])),
                    "in_index": after["indexed"],
                    "unchanged": report.unchanged,
                    "skipped": len(report.skipped),
                    "unreadable": after["unreadable"],
                    "seconds": round(monotonic() - began, 2),
                    "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
                }
            except Exception as exc:  # a pass that died must still be over
                job["last"] = {"scope": scope, "error": str(exc) or type(exc).__name__}
            finally:
                job["running"] = False

        # Held on the job: a task nothing references can be garbage-collected before it
        # finishes, which would leave the panel saying "indexing…" for a job that stopped.
        job["task"] = asyncio.create_task(pass_())
        job["task"].add_done_callback(lambda done: job.pop("task", None))
        return {"started": True, "scope": scope}

    @app.get("/api/check")
    def check_library(request: Request, store: StoreDep) -> dict[str, Any]:
        """Every box's promise about its resource, and the repairs that apply.

        Asked for by name from the menu, never on a timer: it stats every resource in the
        library, which is cheap for a person and wrong to do unbidden.
        """
        from boxganizer import check as checker

        confine = getattr(request.app.state, "content_root", None)
        found = checker.run(store, confine=confine)
        return {
            "summary": checker.summary(found),
            "confine": str(paths.content_root(Path(confine) if confine else None)),
            "findings": [item.as_dict() for item in found],
        }

    @app.post("/api/check/fix")
    def check_repair(ref_body: CheckFix, request: Request, store: StoreDep) -> dict[str, Any]:
        """Do one repair the check offered, and say what happened."""
        from boxganizer import check as checker

        confine = getattr(request.app.state, "content_root", None)
        wanted = (ref_body.kind, ref_body.box, ref_body.path)
        match = next(
            (
                item
                for item in checker.run(store, confine=confine)
                if (item.kind, item.box_uuid or item.box_name, item.path) == wanted
                or (item.kind, item.box_uuid, item.path) == wanted
            ),
            None,
        )
        if match is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "already_fixed",
                    "reason": "that is no longer a finding — check the library again",
                },
            )
        try:
            did = checker.apply(
                store, match, ref_body.action, value=ref_body.value, confine=confine
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={"error": "cannot_apply", "reason": str(exc)},
            ) from exc
        return {"ok": True, "did": did}

    @app.get("/api/index-suggestion")
    def index_suggestion(
        store: StoreDep,
        resource: Annotated[str, Query(description="The address the dialog holds.")],
        box_type: Annotated[str, Query(description="The type the dialog holds.")] = "box",
    ) -> dict[str, object]:
        """What the dialog's indexing checkbox should be set to, and why.

        Asked of the core rather than decided in the page, so the rule lives in one
        place: a checkbox whose default is guessed at in JavaScript is a rule that
        quietly disagrees with the server by the next release.
        """
        kind = boxtypes.get(box_type) if boxtypes.known(box_type) else None
        wanted, reason = resources.why_indexed(
            resource, indexes_content=bool(kind and kind.indexes_content)
        )
        existing = store.resource(resource) if resource.strip() else None
        if existing is not None:
            # A file another box already points at keeps the answer that was given for
            # it: the checkbox is about this resource, and two boxes cannot have two
            # different opinions about whether one file is in the index.
            return {
                "index_it": existing.index_it,
                "reason": (
                    "this file is already in the search index, for every box that points at it"
                    if existing.index_it
                    else "this file is kept out of the search index, for every box that"
                    " points at it"
                ),
                "shared": True,
            }
        return {"index_it": wanted, "reason": reason, "shared": False}

    @app.get("/api/boxes/{ref}/mindmap")
    def read_mindmap(ref: str, store: StoreDep) -> dict[str, object]:
        """The map: the tree below this box, each node carrying its side and children.

        One call rather than one per node, because a mindmap is drawn as a whole and
        the walk down is where cycles and the size limit have to be caught.
        """
        box = _mindmap_box(store, ref)
        return {"map": mindmap.node(store, box.uuid)}

    @app.post("/api/boxes/{ref}/mindmap/side")
    def set_mindmap_side(ref: str, payload: MindmapSide, store: StoreDep) -> dict[str, object]:
        """Put a branch on the other side of the centre, or on a named one."""
        box = _mindmap_box(store, ref)
        child = store.resolve(payload.child)
        if payload.flip and payload.side is not None:
            raise HTTPException(
                status_code=422,
                detail={"error": "side_or_flip", "reason": "give a side, or ask to flip, not both"},
            )
        box.config = mindmap.pin_sides(
            box.config, [kid.uuid for kid in mindmap.children_of(store, box, box.config)]
        )
        tree = mindmap.node(store, box.uuid)
        parents = mindmap.parent_index(tree)
        parent_uuid = parents.get(child.uuid)
        if parent_uuid is None:
            raise HTTPException(
                status_code=409,
                detail={"error": "not_a_node", "reason": "that box is not part of this map"},
            )
        branch = mindmap.line_up(mindmap.find(tree, parent_uuid) or {})
        index = branch.index(child.uuid) if child.uuid in branch else 0
        box.config = (
            mindmap.set_side(box.config, child.uuid, payload.side)
            if payload.side is not None
            else mindmap.flip_side(box.config, child.uuid, index=index)
        )
        store.update(box)
        return {"map": mindmap.node(store, box.uuid)}

    @app.post("/api/boxes/{ref}/mindmap/order")
    def move_mindmap_sibling(ref: str, payload: MindmapOrder, store: StoreDep) -> dict[str, object]:
        """Move a node up or down among the children it hangs among.

        The order is the map's, not the links': what arrived first is rarely the order
        someone wants a branch drawn in, and the links table answers containment only.
        """
        box = _mindmap_box(store, ref)
        parent = store.resolve(payload.parent) if payload.parent else box
        child = store.resolve(payload.child)
        if parent.uuid == box.uuid:
            # pinned against the order the map actually draws, not the order the rows
            # come back from the database in
            box.config = mindmap.pin_sides(
                box.config, [kid.uuid for kid in mindmap.children_of(store, box, box.config)]
            )
        if parent.uuid != box.uuid and not _is_in_map(store, box, parent.uuid):
            raise HTTPException(
                status_code=409,
                detail={"error": "not_a_node", "reason": "that parent is not part of this map"},
            )
        box.config = mindmap.move_sibling(
            box.config, parent.uuid, child.uuid, store.children(parent.uuid), payload.delta
        )
        store.update(box)
        return {"map": mindmap.node(store, box.uuid)}

    @app.post("/api/boxes/{ref}/mindmap/hierarchy")
    def move_mindmap_node(
        ref: str, payload: MindmapHierarchy, store: StoreDep
    ) -> dict[str, object]:
        """Change how deep a node sits: a detail of the thought above it, or not.

        The two directions are named for the centre rather than for the keyboard,
        because which arrow means which depends on which side the branch grows on;
        the page does that translation, and the map keeps the meaning.
        """
        box = _mindmap_box(store, ref)
        child = store.resolve(payload.child)
        if payload.direction == "in":
            tree, note = mindmap.move_towards_centre(store, box.uuid, child.uuid)
        elif payload.direction == "out":
            tree, note = mindmap.move_away_from_centre(store, box.uuid, child.uuid)
        else:
            raise HTTPException(
                status_code=422,
                detail={"error": "unknown_direction", "direction": payload.direction},
            )
        return {"map": tree, "note": note}

    @app.post("/api/boxes/{ref}/mindmap/node", status_code=201)
    def add_mindmap_node(
        request: Request, ref: str, payload: MindmapNode, store: StoreDep
    ) -> dict[str, object]:
        """A new node on the map, in the place the keys left it.

        A node is a box, filed under the node it hangs from. Nothing about this is
        private to mindmaps — the map is how those links are drawn.
        """
        box = _mindmap_box(store, ref)
        parent = store.resolve(payload.parent) if payload.parent else box
        kind = payload.box_type or mindmap.NODE_TYPE
        saved = _add_unique(store, Box(name=mindmap.NEW_NODE_NAME, box_type=kind), payload.name)
        store.link(parent.uuid, saved.uuid)
        text_search.sync(store, saved, root=getattr(request.app.state, "content_root", None))
        line = [child.uuid for child in mindmap.children_of(store, parent, box.config)]
        if saved.uuid in line:
            line.remove(saved.uuid)
        at = line.index(payload.after) + 1 if payload.after in line else len(line)
        holder = store.resolve(box.uuid)
        holder.config = mindmap.set_order(
            holder.config, parent.uuid, [*line[:at], saved.uuid, *line[at:]]
        )
        if parent.uuid == holder.uuid:
            # A branch off the centre gets its side written down at once. Left to the
            # default it would inherit its position, and the first time a sibling was
            # reordered past it the branch would jump to the other half of the map.
            holder.config = mindmap.set_side(holder.config, saved.uuid, mindmap.default_side(at))
        store.update(holder)
        return {"map": mindmap.node(store, holder.uuid), "node": saved.to_dict()}

    @app.post("/api/boxes/{ref}/mindmap/fill")
    def fill_mindmap_node(ref: str, payload: MindmapFill, store: StoreDep) -> dict[str, object]:
        """Drop a box onto a node.

        On a node Insert made and nobody wrote into, the dropped box takes its place in
        the picture and the empty node goes away — that is what the node was waiting
        for. On any other node the box is filed inside it, because a thought that has
        been written down is not thrown away to make room.
        """
        box = _mindmap_box(store, ref)
        target = store.resolve(payload.node)
        filler = store.resolve(payload.with_box)
        tree = mindmap.node(store, box.uuid)
        parents = mindmap.parent_index(tree)
        parent_uuid = parents.get(target.uuid)
        if parent_uuid is None:
            raise HTTPException(
                status_code=409,
                detail={"error": "not_a_node", "reason": "that box is not part of this map"},
            )
        under = len(store.children(target.uuid))
        if not mindmap.placeholder(target, siblings=under):
            store.link(target.uuid, filler.uuid)
            return {"map": mindmap.node(store, box.uuid), "replaced": False}

        parent = store.resolve(parent_uuid)
        # Where the placeholder stood, with the placeholder itself taken out: the box
        # that lands here takes that place in the branch, and the dropped box keeps
        # whatever place it already had among its own parents' siblings elsewhere.
        line = [child.uuid for child in mindmap.children_of(store, parent, box.config)]
        kept = [uuid for uuid in line if uuid != target.uuid]
        at = min(line.index(target.uuid) if target.uuid in line else len(kept), len(kept))
        if filler.uuid in kept:
            kept.remove(filler.uuid)
            at = min(at, len(kept))
        holder = store.resolve(box.uuid)
        holder.config = mindmap.set_order(
            holder.config, parent_uuid, [*kept[:at], filler.uuid, *kept[at:]]
        )
        store.update(holder)
        store.link(parent_uuid, filler.uuid)
        store.remove(target.name)
        return {
            "map": mindmap.node(store, holder.uuid),
            "replaced": True,
            "removed": target.name,
        }

    @app.delete("/api/boxes/{ref}/children/{child}", status_code=204)
    def remove_child(ref: str, child: str, store: StoreDep) -> None:
        """Delete the parent/child link. Both boxes stay in the library."""
        parent = store.resolve(ref)
        store.unlink(parent.uuid, child)

    @app.get("/api/fs/browse")
    def browse_filesystem(
        request: Request,
        path: Annotated[str, Query(description="Directory to list.")] = "",
        show_hidden: Annotated[bool, Query(description="Include dot files.")] = False,
    ) -> dict[str, object]:
        """A file selector for the resource field.

        Browsers will not hand out real paths from `<input type=file>`, so the
        picker walks the server's filesystem. What it may walk is the same rule
        as everywhere else: `$BOXGANIZER_CONTENT_ROOT` when set, otherwise the
        user's home directory, and never above that.
        """
        confine = _browse_root(request)
        # A relative path or a leading `~` is read against the confine, never
        # against the process working directory, and the resolved result must
        # still be inside it.
        requested = Path(path).expanduser() if path else confine
        target = (requested if requested.is_absolute() else confine / requested).resolve()
        if not _inside(target, confine):
            raise HTTPException(
                status_code=403,
                detail={"error": "path_out_of_range", "root": str(confine)},
            )
        if not target.exists():
            raise HTTPException(
                status_code=404, detail={"error": "no_such_path", "path": str(target)}
            )
        if not target.is_dir():
            raise HTTPException(
                status_code=409, detail={"error": "not_a_directory", "path": str(target)}
            )
        entries = [
            entry.as_dict() for entry in fslist.list_directory(target, include_hidden=show_hidden)
        ]
        parent = target.parent
        return {
            "path": str(target),
            "parent": str(parent) if parent != target and _inside(parent, confine) else None,
            "root": str(confine),
            "home": str(Path.home()),
            "entries": entries,
        }

    @app.get("/api/boxes/{ref}/listing")
    def read_listing(
        ref: str,
        request: Request,
        store: StoreDep,
        sub: Annotated[str, Query(description="Path inside the box's directory.")] = "",
    ) -> dict[str, object]:
        """Browse the directory a `directory` box points at."""
        root, target = _box_file(store, request, ref, sub, need_list=True)
        return {
            "root": str(root),
            "sub": sub or "",
            "parent": _parent_of(sub),
            "at": str(target),
            "entries": [entry.as_dict() for entry in fslist.list_directory(root, sub)],
        }

    @app.get("/api/boxes/{ref}/archive")
    def read_archive(
        ref: str,
        request: Request,
        store: StoreDep,
        sub: Annotated[str, Query(description="Archive inside the box's directory.")] = "",
    ) -> dict[str, object]:
        """List the members of a zip or tar archive."""
        _root, target = _box_file(store, request, ref, sub)
        try:
            entries = fslist.list_archive(target)
        except (ValueError, zipfile.BadZipFile, tarfile.TarError) as exc:
            raise HTTPException(
                status_code=422,
                detail={"error": "cannot_list_archive", "reason": str(exc)},
            ) from exc
        return {"path": str(target), "entries": [entry.as_dict() for entry in entries]}

    @app.get("/api/boxes/{ref}/content")
    def read_box_file(ref: str, request: Request, store: StoreDep, sub: str = "") -> Response:
        """One file inside a box's directory, for the explorer's previews."""
        _, target = _box_file(store, request, ref, sub)
        return _file_response(target)

    @app.get("/api/boxes/{ref}/thumbnail")
    def read_thumbnail(ref: str, store: StoreDep) -> Response:
        """The miniature this box uses as its icon, generated from its resource."""
        box = store.resolve(ref)
        data = thumbs.thumb_bytes(box)
        if data is None:
            raise HTTPException(
                status_code=404,
                detail={"error": "no_thumbnail", "name": box.name},
            )
        return Response(
            content=data,
            media_type="image/png",
            headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=60"},
        )

    @app.get("/api/boxes/{ref}/mqtt")
    def read_mqtt_value(ref: str, request: Request, store: StoreDep) -> dict[str, object]:
        """What an MQTT box listens to, and the last value this server heard.

        Answers without connecting to anything: the page calls it to learn the
        topic and show whatever is already known, then opens the stream.
        """
        box = _live_box(store, ref, "mqtt")
        target = mqtt.parse_target(box.resource_uri, box.metadata.get("topic"))
        reading = request.app.state.mqtt_latest.of(box.uuid)
        return {
            "box": _brief(box),
            # The password is in the box's own row and nowhere in this answer.
            "address": mqtt.redact(box.resource_uri),
            "target": target.as_public_dict(),
            "reading": reading.as_dict() if reading else None,
            # what the box's own templates make of that reading, or None until one arrives
            "shown": _shown(box, reading),
        }

    @app.get("/api/boxes/{ref}/mqtt/stream")
    def stream_mqtt_values(ref: str, request: Request, store: StoreDep) -> StreamingResponse:
        """Every message the broker sends, as it arrives, until the page goes away.

        One subscription per open page: when the browser closes, the generator is
        cancelled and the connection to the broker is dropped with it.
        """
        box = _live_box(store, ref, "mqtt")
        target = mqtt.parse_target(box.resource_uri, box.metadata.get("topic"))
        source = request.app.state.mqtt_events
        latest = request.app.state.mqtt_latest
        box_uuid = box.uuid

        async def feed() -> AsyncIterator[str]:
            try:
                async for event in source(target):
                    payload = event.as_dict()
                    if event.reading is not None:
                        latest.remember(box_uuid, event.reading)
                        payload["shown"] = _shown(box, event.reading)
                    yield _sse(event.state, payload)
            except MqttError as exc:
                # `refused`, not `error`, and deliberately terminal. The browser would
                # otherwise reconnect to a box that fails identically every time — a retry
                # loop against a missing package — and the reconnect's own anonymous error
                # would overwrite this sentence with something vaguer. That is exactly how a
                # person ends up reading "the stream dropped" when the truth was "install
                # aiomqtt".
                yield _sse("refused", {"state": "refused", "reading": None, "detail": str(exc)})

        return StreamingResponse(
            feed(),
            media_type="text/event-stream",
            headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
        )

    def _tag_nodes(store: BoxStore) -> list[dict[str, Any]]:
        """The whole hierarchy, with the colour and the count each chip needs.

        Computed here rather than in the page: the colour is derived from a tag's root, the page
        would have to reimplement that rule to draw one chip, and a chip that disagrees with the
        manage window about a tag's colour is a bug report waiting to happen.
        """
        hierarchy = store.hierarchy()
        counts = store.tag_counts()
        held = store.tag_counts_including_inherited()
        nodes: list[dict[str, Any]] = []
        for tag in hierarchy.tags:
            colour = tags.colour_for(hierarchy, tag.name)
            nodes.append(
                {
                    "name": tag.name,
                    "parent": tag.parent,
                    "path": list(hierarchy.path(tag.name)),
                    "depth": hierarchy.depth(tag.name),
                    "colour": colour,
                    "text": tags.text_colour_for(colour),
                    "explicit_colour": tag.colour,
                    "count": counts.get(tags.canonical(tag.name), 0),
                    "includingBelow": held.get(tags.canonical(tag.name), 0),
                }
            )
        nodes.sort(key=lambda node: (tags.canonical(node["parent"] or ""), node["name"].casefold()))
        return nodes

    @app.get("/api/tags")
    def read_tags(store: StoreDep) -> dict[str, Any]:
        """Every tag in the library, in tree order, with what each one is worth."""
        return {"tags": _tag_nodes(store)}

    @app.post("/api/tags", status_code=201)
    def create_tag(store: StoreDep, body: TagCreate) -> dict[str, Any]:
        try:
            made = (
                store.add_tag(body.name, parent=body.parent, colour=body.colour)
                if "/" not in body.name
                else store.add_tag_path(body.name)
            )
        except tags.TagError as exc:
            raise HTTPException(
                status_code=422, detail={"error": "bad_tag", "reason": str(exc)}
            ) from exc
        return {"name": made.name, "parent": made.parent, "tags": _tag_nodes(store)}

    @app.patch("/api/tags/{name}")
    def edit_tag(name: str, store: StoreDep, body: TagEdit) -> dict[str, Any]:
        """Rename, re-parent or re-colour one tag.

        All three in one call because the manage window edits a tag as a row, and three round
        trips for one row edited is three chances for two of them to land.
        """
        try:
            if body.colour is not None or body.clear_colour:
                store.set_tag_colour(
                    name, None if body.clear_colour else str(body.colour or "").strip() or None
                )
            if body.parent is not None:
                store.move_tag(name, body.parent.strip() or None)
            if body.name and tags.canonical(body.name) != tags.canonical(name):
                store.rename_tag(name, body.name)
        except tags.TagError as exc:
            raise HTTPException(
                status_code=422, detail={"error": "bad_tag", "reason": str(exc)}
            ) from exc
        return {"tags": _tag_nodes(store)}

    @app.delete("/api/tags/{name}")
    def remove_tag(name: str, store: StoreDep) -> dict[str, Any]:
        """Delete a tag. Its children take its place; its boxes are told."""
        try:
            done = store.delete_tag(name)
        except tags.TagError as exc:
            raise HTTPException(
                status_code=422, detail={"error": "bad_tag", "reason": str(exc)}
            ) from exc
        return {**done, "tags": _tag_nodes(store)}

    @app.get("/api/boxes/{ref}/tags")
    def read_box_tags(ref: str, store: StoreDep) -> dict[str, Any]:
        """A box's tags, split into what it was given and what that commits it to.

        The split is the point. A chip for an inherited tag has no cross on it, because
        removing it means moving the tag in the hierarchy, not editing the box — and a page
        that offered a control which cannot do the thing would be worse than no control.
        """
        box = store.resolve(ref)
        held = store.box_tags(box)
        hierarchy = store.hierarchy()
        implied = [
            name
            for name in hierarchy.implied(held)
            if tags.canonical(name) not in {tags.canonical(one) for one in held}
        ]
        chips = [
            {
                "name": name,
                "explicit": tags.canonical(name) in {tags.canonical(one) for one in held},
                "colour": tags.colour_for(hierarchy, name),
                "text": tags.text_colour_for(tags.colour_for(hierarchy, name)),
                "path": list(hierarchy.path(name)),
            }
            for name in hierarchy.implied(held)
        ]
        return {"explicit": list(held), "inherited": implied, "chips": chips}

    @app.put("/api/boxes/{ref}/tags")
    def write_box_tags(ref: str, store: StoreDep, body: BoxTags) -> dict[str, Any]:
        box = store.resolve(ref)
        try:
            store.set_box_tags(box, body.tags)
        except tags.TagError as exc:
            raise HTTPException(
                status_code=422, detail={"error": "bad_tag", "reason": str(exc)}
            ) from exc
        return read_box_tags(ref, store)

    @app.post("/api/boxes/{ref}/tags")
    def add_box_tag(ref: str, store: StoreDep, body: BoxTagOne) -> dict[str, Any]:
        """Give a box one tag, leaving the rest of its tags alone.

        The chip line needs this rather than the whole-set write below: a cross removes one
        name, and rebuilding the set from what the page fetched a minute ago silently drops
        anything added since — including by the same page in another click. A delta cannot be
        stale in that way, which is the only reason it exists.
        """
        box = store.resolve(ref)
        try:
            store.assign_tag(box, body.name)
        except tags.TagError as exc:
            raise HTTPException(
                status_code=422, detail={"error": "bad_tag", "reason": str(exc)}
            ) from exc
        return read_box_tags(ref, store)

    @app.delete("/api/boxes/{ref}/tags/{name}")
    def remove_box_tag(ref: str, store: StoreDep, name: str) -> dict[str, Any]:
        """Take one tag off a box. An inherited tag has no row here to take off."""
        box = store.resolve(ref)
        store.unassign_tag(box, name)
        return read_box_tags(box.uuid, store)

    _register_viewport_routes(app)

    @app.get("/api/boxes/{ref}/filter", response_model=FilterBody)
    def read_filter(ref: str, store: StoreDep) -> FilterBody:
        """What a dynamic folder asks for, in words, and who it currently holds.

        The meaning of a filter — which types count as which — belongs to the type
        hierarchy, so the page asks here instead of working it out again in
        JavaScript and drifting from the store.
        """
        box = _resolving_box(store, ref)
        asked = store.filter_of(box)
        return FilterBody(
            box=_brief(box),
            filter=asked.as_config(),
            describes=asked.describe(),
            usable=asked.usable,
            broken=asked.broken,
            members=[_brief(child) for child in store.children(box.uuid)],
        )

    @app.get("/api/sensors/palette")
    def sensor_palette() -> dict[str, object]:
        """The colours and pictures a sensor scale may use.

        Served rather than hard-coded in the page: the same list is what `check_numeric` accepts,
        and a picker offering a colour the store would refuse is a form that lies.
        """
        return {"palette": sensors.palette(), "representations": list(sensors.REPRESENTATIONS)}

    @app.post("/api/filters/parse", response_model=FilterParsed)
    def parse_filter_expression(payload: FilterParse) -> FilterParsed:
        """Read one filter expression without touching any folder.

        The filter bar needs this to keep its controls and the text in the field the same filter:
        the grammar, the wording, and the decision about what can be drawn as a list all live in
        `filters.py`, and the page must not hold a copy of any of them.
        """
        asked = filters.parse_expression_filter(payload.expression)
        rows = filters.editable_clauses(asked.root)
        join = "or" if isinstance(asked.root, filters.Alternative) else "and"
        return FilterParsed(
            expression=asked.expression
            if asked.usable
            else filters.render_expression(asked.root)
            if asked.root is not None
            else asked.expression,
            describes=asked.describe(),
            usable=asked.usable,
            broken=asked.broken,
            join=join,
            clauses=None
            if rows is None
            else [
                FilterClause(
                    field=row.field_name, value=row.value, mode=row.mode, negate=row.negate
                )
                for row in rows
            ],
        )

    @app.put("/api/boxes/{ref}/filter", response_model=FilterBody)
    def write_filter(ref: str, payload: FilterUpdate, store: StoreDep) -> FilterBody:
        """Set a dynamic folder's filter, and update the link table to match it.

        The store is what re-cuts the membership, so the same thing happens here as
        when a filter is edited through `box edit` or a raw config write.
        """
        box = _resolving_box(store, ref)
        # the string form is stored under the key that means "this is an expression", because
        # `filters.parse` deliberately refuses a bare string: a value this version used to
        # reject must not quietly gain a meaning in somebody's already-filed library
        asked = filters.parse(
            {filters.EXPRESSION_KEY: payload.filter}
            if isinstance(payload.filter, str)
            else payload.filter
        )
        if not asked.usable:
            raise HTTPException(
                status_code=422,
                detail={"error": "bad_filter", "reason": asked.broken},
            )
        box.config = {**box.config, filters.FILTER_KEY: asked.as_config()}
        # `update` is what re-cuts the membership; calling resolve again would do
        # the same work twice and make it unclear which one is the rule.
        saved = store.update(box)
        return FilterBody(
            box=_brief(saved),
            filter=asked.as_config(),
            describes=asked.describe(),
            usable=True,
            broken="",
            members=[_brief(child) for child in store.children(saved.uuid)],
        )

    @app.post("/api/boxes/{ref}/resolve")
    def resolve_dynamic_folder(ref: str, store: StoreDep) -> dict[str, object]:
        """Re-apply a dynamic folder's filter now, and say how many boxes it holds."""
        box = _resolving_box(store, ref)
        return {"box": _brief(box), "members": store.resolve_folder(box.uuid)}

    @app.get("/api/boxes/{ref}/children")
    def read_children(ref: str, store: StoreDep) -> list[dict[str, object]]:
        box = store.resolve(ref)
        return _dump(store.children(box.uuid))

    @app.put("/api/boxes/{ref}/config")
    def write_config(ref: str, payload: ConfigUpdate, store: StoreDep) -> dict[str, object]:
        """Replace `config`. This is where view settings and cell layouts live."""
        box = store.resolve(ref)
        box.config = dict(payload.config)
        return store.update(box).to_dict()

    @app.post("/api/boxes", status_code=201)
    def create_box(request: Request, payload: BoxCreate, store: StoreDep) -> dict[str, object]:
        box = Box(
            name=payload.name,
            description=payload.description,
            icon=payload.icon or DEFAULT_ICON,
            box_type=payload.box_type,
            resource_uri=payload.resource_uri,
            metadata=payload.metadata,
            config=dict(payload.config),
        )
        if payload.icon is None:
            boxtypes.adopt_type_icon(box)
            thumbs.icon_from_resource(box)
        if getattr(boxtypes.get(box.box_type), "viewport_layout", None) is not None:
            # A box that does not exist yet holds nothing, so a viewport created with a pane
            # already pointing at a box is asking for a view of the graph that is not true.
            problem = viewports.check(box.config, set(), _viewport_kind(box))
            if problem:
                raise HTTPException(
                    status_code=422, detail={"error": "bad_viewport", "reason": problem}
                )
        saved = store.add(box, index_it=payload.index_resource)
        # A text or PDF box goes into the full-text index the moment it exists, so it
        # is findable by what it says without anyone remembering to rebuild anything.
        text_search.sync(store, saved, root=getattr(request.app.state, "content_root", None))
        if payload.parent:
            store.link(payload.parent, saved.uuid)
            if payload.cell is not None:
                _grid_place(store, payload.parent, saved.uuid, payload.cell)
            elif payload.place is not None:
                _desktop_place(store, payload.parent, saved.uuid, payload.place.x, payload.place.y)
        elif payload.place is not None:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "place_without_parent",
                    "reason": "a spot on the surface needs a surface to put it on",
                },
            )
        return saved.to_dict()

    @app.put("/api/boxes/{ref}")
    def update_box(
        request: Request, ref: str, payload: BoxUpdate, store: StoreDep
    ) -> dict[str, object]:
        """Change a box's own fields, including its type.

        Omitted fields stay as they are. Every write goes through the type's
        validation, so retyping is refused when the resource, metadata, or the
        boxes inside it no longer fit.
        """
        box = store.resolve(ref)
        data = payload.model_dump(exclude_unset=True)
        for field in ("name", "description", "icon", "box_type", "resource_uri"):
            if data.get(field) is None:
                continue
            value = str(data[field]).strip()
            # An emptied description, icon or resource means "unset". A name is left
            # as typed so the store rejects it with its own message, not silently.
            if field in {"name", "box_type"}:
                setattr(box, field, value)
            elif field == "description":
                box.description = value  # a column that is never NULL
            elif field == "icon":
                # Clearing a custom icon returns the box to its type's glyph.
                box.icon = value or boxtypes.get(box.box_type).icon or DEFAULT_ICON
            else:
                setattr(box, field, value or None)
        if "metadata" in data and data["metadata"] is not None:
            box.metadata = dict(data["metadata"])
        if "config" in data and data["config"] is not None:
            box.config = dict(data["config"])
        _check_viewport_children(store, box)
        saved = store.update(box, index_it=payload.index_resource)
        # The same rule as on creation, in both directions: a box retyped away from
        # text, or pointed at another file, has its indexed text replaced or removed.
        text_search.sync(store, saved, root=getattr(request.app.state, "content_root", None))
        return saved.to_dict()

    @app.delete("/api/boxes/{ref}", status_code=204)
    def delete_box(ref: str, store: StoreDep) -> None:
        """Delete one box, by name or uuid. Nothing comes back, because there is nothing to report.

        The links from it to what it held go with the row; the boxes inside stay in the library,
        filed wherever else they were. A file it pointed at is left alone, and so is the words file
        it wrote for itself — ADR 14, on purpose. The page states all of that *before* it asks (its
        menu entry is armed by the first click), which is the moment a person can still change their
        mind; a response body after the fact is not. Deleting the root is refused instead: it is
        where everything else is filed, so there is nowhere to arrive at afterwards.
        """
        store.remove(store.resolve(ref).name)

    @app.post("/api/boxes/{ref}/children", status_code=201)
    def add_child(ref: str, payload: LinkCreate, store: StoreDep) -> dict[str, object]:
        """File a box here — and, with `move_from`, stop filing it somewhere else.

        A plain drop of one box onto another **copies the link**: the box ends up a child of both,
        which is what "put a copy of this note in that project" means and what the page's own
        wording says. Holding Shift while dropping asks for a move, and that is this parameter.
        """
        parent = store.resolve(ref)
        child_uuid = store.resolve(payload.child).uuid
        origin = store.resolve(payload.move_from) if payload.move_from else None
        if origin is not None:
            # Checked before anything is filed, so a move that does not add up changes nothing at
            # all: a box pulled out of a parent it never had, or filed in a place the page only
            # thinks it is, is worse than a drop that did nothing.
            filed = {child.uuid for child in store.children(origin.uuid)}
            if child_uuid not in filed:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "not_filed_there",
                        "reason": (
                            f"{origin.name} does not hold that box, so nothing was moved; "
                            "it is still filed where it was"
                        ),
                    },
                )
        store.link(parent.uuid, child_uuid)
        if origin is not None:
            store.unlink(origin.uuid, child_uuid)
        return {
            "children": [_brief(b) for b in store.children(parent.uuid)],
            "moved": bool(payload.move_from),
        }

    @app.get("/api/search")
    def search(
        request: Request,
        store: StoreDep,
        q: Annotated[str, Query(min_length=1)],
        response: Response,
        limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    ) -> list[dict[str, object]]:
        """Find boxes: by their own fields first, then by what their files say.

        Every answer carries `match` — how this box came up, and a passage of the file
        when the file is what matched — so "why is this in my results" has an answer on
        the screen instead of being a guess. What the words were widened to (synonyms,
        near-misses) comes back in the `X-Search-Expanded` header, because it belongs
        to the query as a whole and not to any one box.
        """
        named = store.search(q, limit=limit)
        found: list[dict[str, object]] = [
            box.to_dict() | {"match": {"how": "box", "passage": ""}} for box in named
        ]
        seen = {str(entry["uuid"]) for entry in found}

        planned = text_search.plan(q, lambda term: store.vocabulary(prefix=term[:1]))
        # Only say something when something was actually added: "searched: kitchen"
        # under a search for "kitchen" is noise, and noise trains people to ignore it.
        if planned.expanded:
            response.headers["X-Search-Expanded"] = text_search.explain(planned)
        if planned.worth_asking:
            hits = store.content_search(planned.content_match, limit=limit * 2)
            # The index answers with the *file* that matched; what a person wants is the
            # box. One file can be pointed at by several boxes, and every one of them is
            # a true answer, so each is offered with the same passage and the same
            # reason — and a box already listed by its own name is not listed twice.
            for resource_id, _score, passage in hits:
                for box, inside in store.boxes_for_text(resource_id):
                    if box.uuid in seen:
                        continue
                    seen.add(box.uuid)
                    found.append(
                        box.to_dict()
                        | {
                            "match": {
                                "how": "content",
                                "passage": passage,
                                "resource": resource_id,
                                # which file inside the folder answered, when the box is a
                                # folder and the words came from one of its files
                                "in_file": inside,
                            }
                        }
                    )
                    if len(found) >= limit:
                        break
                if len(found) >= limit:
                    break
        return found

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        """The application shell; everything inside it comes from the API."""
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={"version": __version__},
        )

    return app


def _dump(boxes: Sequence[Box]) -> list[dict[str, object]]:
    return [box.to_dict() for box in boxes]


def _desktop_box(store: BoxStore, ref: str) -> Box:
    """A box whose type places its children at chosen coordinates.

    Asked of the type rather than compared with the string `"desktop"`, so a
    blueprint — which places icons the same way — answers to the same endpoints.
    """
    box = store.resolve(ref)
    if not boxtypes.get(box.box_type).arranges_icons:
        raise HTTPException(
            status_code=415,
            detail={"error": "not_a_desktop", "type": box.box_type},
        )
    return box


def _desktop_place(store: BoxStore, parent_ref: str, child_uuid: str, x: int, y: int) -> Box:
    """Land a new child's icon on the spot the surface was right-clicked at.

    The magnetic grid still gets the last word, because it is the desktop's rule
    and a right-click is no exception to it: an icon dropped on a snapped desktop
    arrives on a line, which is what the user asked the desktop to do.
    """
    parent = _desktop_box(store, parent_ref)
    parent.config = desktop.move(parent.config, child_uuid, x, y)
    return store.update(parent)


def _grid_place(
    store: BoxStore, parent_ref: str, child_uuid: str, cell_value: list[int]
) -> dict[str, object]:
    """One placement: file the child if needed, then put it in its cell."""
    parent = store.resolve(parent_ref)
    size = grid.size_of(parent.config)
    cells = grid.cells_of(parent.config)
    children = {child.uuid for child in store.children(parent.uuid)}
    if child_uuid not in children:
        store.link(parent.uuid, child_uuid)
        children.add(child_uuid)
    placed = grid.place(
        cells=cells,
        size=size,
        child_uuid=child_uuid,
        cell=grid.parse_cell(cell_value),
        known_children=children,
    )
    parent.config = grid.to_config(parent.config, size=size, cells=placed)
    return store.update(parent).to_dict()


def _viewport_box(store: BoxStore, ref: str) -> Box:
    """A box whose type lends out its space. Asked of the type, like every layout endpoint."""
    box = store.resolve(ref)
    kind = boxtypes.get(box.box_type)
    if getattr(kind, "viewport_layout", None) is None:
        raise HTTPException(
            status_code=415,
            detail={"error": "not_a_viewport", "type": kind.key},
        )
    return box


def _check_viewport_children(store: BoxStore, box: Box) -> None:
    """Refuse a layout that shows a box this viewport does not hold.

    The type's own check only sees the box, so it can judge the shape and nothing else. This is the
    half that needs the graph: a pane pointing at somebody else's child would put a box on screen
    that `box tree` says lives elsewhere, and the page and the library would disagree about where a
    box is filed — which is the one disagreement a user can never resolve from the outside.
    """
    if getattr(boxtypes.get(box.box_type), "viewport_layout", None) is None:
        return
    children = {child.uuid for child in store.children(box.uuid)}
    problem = viewports.check(box.config, children, _viewport_kind(box))
    if problem:
        raise HTTPException(status_code=422, detail={"error": "bad_viewport", "reason": problem})


def _viewport_kind(box: Box) -> str | None:
    """Which layout this box's type draws — the type decides, not the config."""
    kind = boxtypes.get(box.box_type)
    layout_kind: str | None = getattr(kind, "viewport_layout", None)
    return layout_kind


def _viewport_body(store: BoxStore, box: Box) -> dict[str, object]:
    """The whole layout, plus each pane's box, so the page draws a viewport in one request.

    The briefs travel with the tree because a pane that has to fetch its own box is a pane that
    paints late, and a viewport is exactly the place where several boxes appear at once.
    """
    layout = viewports.layout_of(box.config, _viewport_kind(box))
    # The whole box, not a brief. A pane renders a *view*, and a view reads its settings from
    # `config` — a grid's columns and placements, a desktop's icon positions, a checklist's file.
    # Sending `{uuid, name, icon, box_type}` made every box in a viewport open with default
    # settings and its children unplaced, which is the difference between a window on a box and a
    # picture of one. It also carries `parents`, which is what the pane's path line walks.
    briefs = {child.uuid: _full(store, child) for child in store.children(box.uuid)}
    panes: list[dict[str, object]] = []
    if layout.kind == "windowed":
        for window in layout.windows:
            panes.append(
                {
                    "node": window.node,
                    "child": window.child,
                    "box": briefs.get(window.child),
                    "x": window.x,
                    "y": window.y,
                    "width": window.width,
                    "height": window.height,
                    # The state travels because the button depends on it: a maximized window asks to
                    # be restored and a minimized one asks to be put back, and a page guessing from
                    # the rectangle would fight the CSS that draws them.
                    "state": window.state,
                }
            )
    else:
        tree = layout.tree
        panes = [
            {"node": leaf.node, "child": leaf.child, "box": briefs.get(leaf.child)}
            for leaf in viewports.leaves(tree)
        ]
    return {
        "box": _brief(box),
        "kind": layout.kind,
        "tree": layout.tree.as_dict() if layout.tree is not None else None,
        "windows": [window.as_dict() for window in layout.windows],
        "order": list(layout.order),
        "panes": panes,
        "problems": list(layout.problems),
        # The page disables the button rather than explaining afterwards why nothing happened.
        "closable": len(layout.panes()) > 1,
        # How solid the windows are: 0 invisible, 1 solid — the number the **transparency** slider
        # shows as a percentage. Read from its own config section rather than the layout one,
        # which is rewritten wholesale on every layout save (see `viewports.opacity_of`).
        "opacity": viewports.opacity_of(box.config),
    }


def _window_state(store: BoxStore, ref: str, node: str, action: str) -> dict[str, object]:
    """Maximize, minimize or restore one window, and answer with the layout that resulted."""
    box = _viewport_box(store, ref)
    layout = viewports.layout_of(box.config, _viewport_kind(box))
    try:
        if action == "maximize":
            changed = viewports.maximize_window(layout, node)
        elif action == "minimize":
            changed = viewports.minimize_window(layout, node)
        else:
            changed = viewports.restore_window(layout, node)
    except viewports.ViewportError as exc:
        raise HTTPException(
            status_code=422, detail={"error": "bad_viewport", "reason": str(exc)}
        ) from exc
    return _save_viewport(store, box, changed)


def _save_viewport(store: BoxStore, box: Box, layout: viewports.Layout) -> dict[str, object]:
    """Write a layout after checking it against the boxes the viewport actually holds."""
    children = {child.uuid for child in store.children(box.uuid)}
    problem = viewports.check(layout.as_config(box.config), children, _viewport_kind(box))
    if problem:
        raise HTTPException(status_code=422, detail={"error": "bad_viewport", "reason": problem})
    box.config = layout.as_config(box.config)
    store.update(box)
    return _viewport_body(store, store.resolve(box.uuid))


def _viewport_child(store: BoxStore, box: Box, ref: str | None) -> str:
    """The uuid of a box to show, filed under the viewport if it is not filed anywhere yet.

    Opening a box in a viewport *is* filing it there: the link is what `box tree` and the side pane
    read, so a pane that showed an unfiled box would be a view of the library that the library
    disagrees with.
    """
    if not ref:
        return ""
    return store.resolve(ref).uuid


def _register_viewport_routes(app: FastAPI) -> None:
    """The viewport routes, kept together: they all read a layout, change it, and answer with it.

    The page sends *intent* — split this pane, move that divider, close that one — and the server
    answers with the whole new layout. It does not send a tree back, because then two pages open on
    the same box would each be deciding what the other's click means, and the model would live in
    two languages with the same name.
    """

    @app.get("/api/boxes/{ref}/viewports")
    def read_viewports(ref: str, store: StoreDep) -> dict[str, object]:
        return _viewport_body(store, _viewport_box(store, ref))

    @app.post("/api/boxes/{ref}/viewports/split")
    def split_viewport(ref: str, payload: ViewportNode, store: StoreDep) -> dict[str, object]:
        """Split one pane — or cut one window in two, which is a different act.

        The verb is the same because the *button* is the same, and a person choosing "side by
        side" should not have to know which model their viewport uses. What a split does differs: in
        tiling the new pane takes half the space the divider already governs; in a windowed box the
        two halves sit exactly where the one window was, because a window has no neighbour whose
        room it could take.
        """
        box = _viewport_box(store, ref)
        layout = viewports.layout_of(box.config, _viewport_kind(box))
        try:
            if layout.kind == "windowed":
                layout, _fresh = viewports.split_window(layout, payload.node, payload.direction)
            else:
                layout, _fresh = viewports.split(layout, payload.node, payload.direction)
        except viewports.ViewportError as exc:
            raise HTTPException(
                status_code=422, detail={"error": "bad_viewport", "reason": str(exc)}
            ) from exc
        return _save_viewport(store, box, layout)

    @app.post("/api/boxes/{ref}/viewports/maximize")
    def maximize_viewport_window(
        ref: str, payload: ViewportNode, store: StoreDep
    ) -> dict[str, object]:
        return _window_state(store, ref, payload.node, "maximize")

    @app.post("/api/boxes/{ref}/viewports/minimize")
    def minimize_viewport_window(
        ref: str, payload: ViewportNode, store: StoreDep
    ) -> dict[str, object]:
        return _window_state(store, ref, payload.node, "minimize")

    @app.post("/api/boxes/{ref}/viewports/restore")
    def restore_viewport_window(
        ref: str, payload: ViewportNode, store: StoreDep
    ) -> dict[str, object]:
        return _window_state(store, ref, payload.node, "restore")

    @app.post("/api/boxes/{ref}/viewports/close")
    def close_viewport(ref: str, payload: ViewportNode, store: StoreDep) -> dict[str, object]:
        """Close a pane: its sibling takes the space, and its box is unfiled, not deleted."""
        box = _viewport_box(store, ref)
        try:
            layout, closed = viewports.close(
                viewports.layout_of(box.config, _viewport_kind(box)), payload.node
            )
        except viewports.ViewportError as exc:
            raise HTTPException(
                status_code=422, detail={"error": "bad_viewport", "reason": str(exc)}
            ) from exc
        _save_viewport(store, box, layout)
        if closed:
            # Unfiled, not deleted: the box goes back to being a box, and whoever filed it in the
            # viewport can open it again somewhere else.
            store.unlink(box.uuid, closed)
        return _viewport_body(store, store.resolve(box.uuid))

    @app.post("/api/boxes/{ref}/viewports/ratio")
    def set_viewport_ratio(ref: str, payload: ViewportNode, store: StoreDep) -> dict[str, object]:
        box = _viewport_box(store, ref)
        try:
            layout = viewports.set_ratio(
                viewports.layout_of(box.config, _viewport_kind(box)), payload.node, payload.ratio
            )
        except viewports.ViewportError as exc:
            raise HTTPException(
                status_code=422, detail={"error": "bad_viewport", "reason": str(exc)}
            ) from exc
        return _save_viewport(store, box, layout)

    @app.post("/api/boxes/{ref}/viewports/open")
    def open_in_viewport(ref: str, payload: ViewportNode, store: StoreDep) -> dict[str, object]:
        """Show a box in one pane, filing it under the viewport box on the way."""
        box = _viewport_box(store, ref)
        child = _viewport_child(store, box, payload.child)
        layout = viewports.layout_of(box.config, _viewport_kind(box))
        if child and child not in {item.uuid for item in store.children(box.uuid)}:
            store.link(box.uuid, child)
        try:
            layout = viewports.assign(layout, payload.node, child)
        except viewports.ViewportError as exc:
            raise HTTPException(
                status_code=422, detail={"error": "bad_viewport", "reason": str(exc)}
            ) from exc
        return _save_viewport(store, box, layout)

    @app.post("/api/boxes/{ref}/viewports/window")
    def add_window(ref: str, payload: ViewportNode, store: StoreDep) -> dict[str, object]:
        """Open a box in a window — an empty one if there is one, a new one otherwise."""
        box = _viewport_box(store, ref)
        child = _viewport_child(store, box, payload.child)
        layout = viewports.layout_of(box.config, _viewport_kind(box))
        if child and child not in {item.uuid for item in store.children(box.uuid)}:
            store.link(box.uuid, child)
        try:
            layout, _fresh = viewports.show_in_window(layout, child)
        except viewports.ViewportError as exc:
            raise HTTPException(
                status_code=422, detail={"error": "bad_viewport", "reason": str(exc)}
            ) from exc
        return _save_viewport(store, box, layout)

    @app.post("/api/boxes/{ref}/viewports/move")
    def move_window(ref: str, payload: ViewportNode, store: StoreDep) -> dict[str, object]:
        box = _viewport_box(store, ref)
        try:
            layout = viewports.move_window(
                viewports.layout_of(box.config, _viewport_kind(box)),
                payload.node,
                payload.x or 0,
                payload.y or 0,
            )
        except viewports.ViewportError as exc:
            raise HTTPException(
                status_code=422, detail={"error": "bad_viewport", "reason": str(exc)}
            ) from exc
        return _save_viewport(store, box, layout)

    @app.post("/api/boxes/{ref}/viewports/resize")
    def resize_window(ref: str, payload: ViewportNode, store: StoreDep) -> dict[str, object]:
        box = _viewport_box(store, ref)
        try:
            layout = viewports.resize_window(
                viewports.layout_of(box.config, _viewport_kind(box)),
                payload.node,
                payload.width or 0,
                payload.height or 0,
            )
        except viewports.ViewportError as exc:
            raise HTTPException(
                status_code=422, detail={"error": "bad_viewport", "reason": str(exc)}
            ) from exc
        return _save_viewport(store, box, layout)

    @app.post("/api/boxes/{ref}/viewports/raise")
    def raise_window(ref: str, payload: ViewportNode, store: StoreDep) -> dict[str, object]:
        box = _viewport_box(store, ref)
        try:
            layout = viewports.raise_window(
                viewports.layout_of(box.config, _viewport_kind(box)), payload.node
            )
        except viewports.ViewportError as exc:
            raise HTTPException(
                status_code=422, detail={"error": "bad_viewport", "reason": str(exc)}
            ) from exc
        return _save_viewport(store, box, layout)


def _mindmap_box(store: BoxStore, ref: str) -> Box:
    """A box whose type draws its children as a map of nodes.

    Asked of the type, as every other layout endpoint does, so a kind of map added
    later answers to these routes without anything here changing.
    """
    box = store.resolve(ref)
    kind = boxtypes.get(box.box_type)
    if not kind.maps_nodes:
        raise HTTPException(
            status_code=415,
            detail={"error": "not_a_mindmap", "type": kind.key},
        )
    return box


def _add_unique(store: BoxStore, box: Box, name: str | None) -> Box:
    """Add a box, under a given name or under the next free one of its own.

    A name that is taken is refused to the caller in the ordinary way when they asked
    for it; when nobody asked, the numbering just moves on, because three presses of
    one key should make three nodes rather than two nodes and an error message.
    """
    if name and name.strip():
        box.name = name.strip()
        return store.add(box)
    box.name = mindmap.NEW_NODE_NAME
    refused: set[str] = set()
    for _attempt in range(50):
        try:
            return store.add(box)
        except DuplicateBoxName:
            # Each name the store says is taken has to be kept in mind, or the next
            # attempt lands on the same one and the loop walks in circles.
            refused.add(box.name)
            box.name = mindmap.next_name(refused)
    raise DuplicateBoxName(box.name)  # pragma: no cover - 50 ideas taken is a different problem


def _ssh_box(store: BoxStore, ref: str) -> Box:
    """A box whose type reaches another machine.

    Asked of the type, so any kind of terminal box added under `terminal` answers the
    SSH endpoints too — and asked before the address is read, so a box that is not one
    is refused with its own type named rather than with a parse error.
    """
    box = store.resolve(ref)
    kind = boxtypes.get(box.box_type)
    # The whole line is asked, not just the key: a kind of terminal below `ssh` is
    # still a box that reaches another machine, and inherits these routes.
    if "ssh" not in kind.path().split("/"):
        raise HTTPException(
            status_code=415,
            detail={"error": "not_an_ssh_box", "type": kind.key},
        )
    return box


def _is_in_map(store: BoxStore, box: Box, uuid: str) -> bool:
    """Whether a box is drawn somewhere in this mindmap, the centre itself included."""
    if uuid == box.uuid:
        return True
    return mindmap.find(mindmap.node(store, box.uuid), uuid) is not None


def _shown(box: Box, reading: mqtt.Reading | None) -> dict[str, object] | None:
    """The reading as the box's own templates word it.

    Rendered here rather than in the page for the same reason markdown is: the rules about what
    a template may say — and what may reach the screen from it — are one implementation, in the
    language that owns the allow-list. `sensors.present` never raises; a template that fails
    travels with the sentence that says so, beside the value it could not dress.
    """
    from boxganizer import sensors

    if reading is None:
        return None
    return sensors.present(
        box,
        reading.value,
        extra={
            "topic": reading.topic,
            "at": reading.received_at.isoformat(timespec="seconds"),
            "retained": reading.retained,
        },
    ).as_dict()


def _resolving_box(store: BoxStore, ref: str) -> Box:
    """A box whose type takes its members from a filter — the cabinet included.

    Asked of the type, never of the `box_type` string, so a future kind of dynamic
    folder answers the same endpoints without a change here.
    """
    box = store.resolve(ref)
    kind = boxtypes.get(box.box_type)
    if not kind.resolves_membership:
        raise HTTPException(
            status_code=415,
            detail={"error": "not_a_dynamic_folder", "type": kind.key},
        )
    return box


def _live_box(store: BoxStore, ref: str, source: str) -> Box:
    """A box whose own type says it reads that kind of live source.

    The type is asked, never the `box_type` string: a subtype of an MQTT box gets
    the MQTT endpoints for free, which is how the hierarchy is meant to work.
    """
    box = store.resolve(ref)
    kind = boxtypes.get(box.box_type)
    if kind.live_source != source:
        raise HTTPException(
            status_code=415,
            detail={"error": "not_a_live_box", "type": kind.key, "needs": source},
        )
    return box


def _sse(event: str, payload: dict[str, object]) -> str:
    """One server-sent event."""
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


def _desktop_child(store: BoxStore, parent: Box, child_ref: str) -> Box:
    """The child of a desktop, filing it first when the drop came from the side pane."""
    child = store.resolve(child_ref)
    if child.uuid not in {existing.uuid for existing in store.children(parent.uuid)}:
        store.link(parent.uuid, child.uuid)
    return child


def _with_rect(store: BoxStore, box: Box, child_uuid: str) -> dict[str, object]:
    """Save a desktop whose one child just moved, and say where it ended up.

    The answer carries the rectangle the core settled on — after the magnetic
    grid — so the page can leave the icon where the database says it is.
    """
    saved = store.update(box)
    rect = desktop.rect_for(saved.config, child_uuid)
    return saved.to_dict() | {"rect": rect.as_dict() if rect else None}


def _browse_root(request: Request) -> Path:
    """How far the file selector may wander."""
    confine = getattr(request.app.state, "content_root", None)
    return Path(confine) if confine is not None else Path.home()


def _box_file(
    store: BoxStore,
    request: Request,
    ref: str,
    sub: str,
    *,
    need_list: bool = False,
) -> tuple[Path, Path]:
    """Resolve `ref` + `sub` to a real file inside the box's own directory.

    The sub-path can never leave the box's resource directory, and the whole
    directory must still be inside `$BOXGANIZER_CONTENT_ROOT` when that is set.
    """
    box = store.resolve(ref)
    if need_list and not boxtypes.get(box.box_type).lists_files:
        raise HTTPException(
            status_code=415,
            detail={"error": "not_a_directory_box", "type": box.box_type},
        )
    if not box.resource_uri:
        raise HTTPException(status_code=404, detail={"error": "resource_missing", "name": box.name})
    root = _local_path(box.resource_uri)
    confine = getattr(request.app.state, "content_root", None)
    if confine is not None and not _inside(root, confine):
        raise HTTPException(
            status_code=403, detail={"error": "resource_out_of_range", "root": str(confine)}
        )
    try:
        target = fslist.safe_join(root, sub)
    except fslist.OutsideRoot as exc:
        raise HTTPException(status_code=403, detail={"error": "path_out_of_range"}) from exc
    if not target.exists():
        raise HTTPException(status_code=404, detail={"error": "no_such_path", "path": str(target)})
    return root, target


def _parent_of(sub: str) -> str | None:
    cleaned = (sub or "").strip("/")
    if not cleaned:
        return None
    parent = cleaned.rsplit("/", 1)[0] if "/" in cleaned else ""
    return parent


def _file_response(path: Path) -> Response:
    if path.is_dir():
        raise HTTPException(status_code=409, detail={"error": "is_a_directory"})
    size = path.stat().st_size
    if size > MAX_RAW_BYTES:
        raise HTTPException(status_code=413, detail={"error": "resource_too_large", "size": size})
    media_type = fslist.media_type_for(path)
    headers = {"X-Content-Type-Options": "nosniff", "Content-Disposition": "inline"}
    if media_type == "image/svg+xml":
        headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return Response(content=path.read_bytes(), media_type=media_type, headers=headers)


def _local_path(resource_uri: str) -> Path:
    """A `file://` URL or a plain path, as a Path."""
    scheme, _ = paths.split_resource(resource_uri)
    if scheme not in ("", "file"):
        raise HTTPException(
            status_code=415,
            detail={"error": "resource_not_local", "scheme": scheme},
        )
    return paths.local_path(resource_uri)


def _inside(path: Path, root: Path) -> bool:
    """True when `path` is `root` or below it, symlinks resolved."""
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _full(store: BoxStore, box: Box) -> dict[str, object]:
    """One box as the page wants it: the row, and the boxes on either side of it."""
    return box.to_dict() | {
        "children": [_brief(b) for b in store.children(box.uuid)],
        "parents": [_brief(b) for b in store.parents(box.uuid)],
    }


def _brief(box: Box) -> dict[str, object]:
    """Just enough of a box to draw it in a list and select it later."""
    return {"uuid": box.uuid, "name": box.name, "icon": box.icon, "box_type": box.box_type}
