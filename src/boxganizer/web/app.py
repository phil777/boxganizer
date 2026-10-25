"""FastAPI application: JSON API plus a minimal HTML browser.

Create it with :func:`create_app` and hand it to uvicorn (``box serve`` does
both, through :func:`boxganizer.web.server.run`). The app owns one :class:`BoxStore`.
"""

from __future__ import annotations

import json
import mimetypes
import os
import tarfile
import zipfile
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from boxganizer import __version__, boxtypes, desktop, fslist, grid, infer, mqtt, paths, thumbs
from boxganizer.errors import (
    BoxNotFound,
    ChildrenNotAllowed,
    DuplicateBoxName,
    GridError,
    InvalidBoxContent,
    InvalidBoxName,
    MqttError,
    PlacementError,
    RootCannotBeFiled,
    UnknownBoxType,
)
from boxganizer.models import DEFAULT_ICON, Box
from boxganizer.store import BoxStore

try:
    from fastapi import Depends, FastAPI, HTTPException, Query, Request
    from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
    from fastapi.staticfiles import StaticFiles
    from fastapi.templating import Jinja2Templates
    from pydantic import BaseModel, Field, JsonValue
except ModuleNotFoundError as exc:  # pragma: no cover - only when the extra is absent
    raise ModuleNotFoundError(
        "the web frontend needs the 'web' extra: pip install 'boxganizer[web]'"
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


class GridPlace(BaseModel):
    """Put a child in a grid cell, swapping with whoever is there."""

    child: str = Field(description="Name or uuid of the child box.")
    cell: list[int] = Field(description="[row, column], both counted from 0.")


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


class ConfigUpdate(BaseModel):
    """Request body for replacing a box's config."""

    config: dict[str, JsonValue]


class LinkCreate(BaseModel):
    """Request body for putting one box inside another."""

    child: str


def _store_of(request: Request) -> BoxStore:
    store: BoxStore | None = getattr(request.app.state, "store", None)
    if store is None:  # pragma: no cover - defensive
        raise HTTPException(status_code=503, detail="no store attached")
    return store


StoreDep = Annotated[BoxStore, Depends(_store_of)]


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    try:
        yield
    finally:
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

    @app.exception_handler(RootCannotBeFiled)
    async def _filed_root(request: Request, exc: RootCannotBeFiled) -> JSONResponse:
        return JSONResponse(
            status_code=409, content={"detail": {"error": "root_cannot_be_filed", "name": exc.name}}
        )

    @app.exception_handler(GridError)
    async def _bad_grid_move(request: Request, exc: GridError) -> JSONResponse:
        return JSONResponse(
            status_code=422, content={"detail": {"error": "bad_grid_move", "reason": str(exc)}}
        )

    @app.exception_handler(PlacementError)
    async def _bad_desktop_move(request: Request, exc: PlacementError) -> JSONResponse:
        return JSONResponse(
            status_code=422, content={"detail": {"error": "bad_desktop_move", "reason": str(exc)}}
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
        box = store.resolve(ref)
        child = _desktop_child(store, box, payload.child)
        saved = desktop.move(box.config, child.uuid, payload.x, payload.y)
        box.config = saved
        return _with_rect(store, box, child.uuid)

    @app.post("/api/boxes/{ref}/desktop/resize")
    def resize_on_desktop(ref: str, payload: DesktopResize, store: StoreDep) -> dict[str, object]:
        """Change the size of one icon on the surface."""
        box = store.resolve(ref)
        child = _desktop_child(store, box, payload.child)
        box.config = desktop.resize(box.config, child.uuid, payload.width, payload.height)
        return _with_rect(store, box, child.uuid)

    @app.post("/api/boxes/{ref}/desktop/settings")
    def write_desktop_settings(
        ref: str, payload: DesktopSettingsUpdate, store: StoreDep
    ) -> dict[str, object]:
        """Engage or disengage the magnetic grid, and set its size."""
        box = store.resolve(ref)
        settings = desktop.Settings(grid=payload.grid, snap=payload.snap)
        box.config = desktop.to_config(box.config, settings=settings)
        return store.update(box).to_dict()

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
                    if event.reading is not None:
                        latest.remember(box_uuid, event.reading)
                    yield _sse(event.state, event.as_dict())
            except MqttError as exc:
                yield _sse("error", {"state": "error", "reading": None, "detail": str(exc)})

        return StreamingResponse(
            feed(),
            media_type="text/event-stream",
            headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
        )

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
    def create_box(payload: BoxCreate, store: StoreDep) -> dict[str, object]:
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
        saved = store.add(box)
        if payload.parent:
            store.link(payload.parent, saved.uuid)
            if payload.cell is not None:
                _grid_place(store, payload.parent, saved.uuid, payload.cell)
        return saved.to_dict()

    @app.put("/api/boxes/{ref}")
    def update_box(ref: str, payload: BoxUpdate, store: StoreDep) -> dict[str, object]:
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
        return store.update(box).to_dict()

    @app.delete("/api/boxes/{ref}", status_code=204)
    def delete_box(ref: str, store: StoreDep) -> None:
        store.remove(store.resolve(ref).name)

    @app.post("/api/boxes/{ref}/children", status_code=201)
    def add_child(ref: str, payload: LinkCreate, store: StoreDep) -> dict[str, object]:
        parent = store.resolve(ref)
        store.link(parent.uuid, payload.child)
        return {"children": [_brief(b) for b in store.children(parent.uuid)]}

    @app.get("/api/search")
    def search(
        store: StoreDep,
        q: Annotated[str, Query(min_length=1)],
        limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    ) -> list[dict[str, object]]:
        return _dump(store.search(q, limit=limit))

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
