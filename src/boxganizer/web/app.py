"""FastAPI application: JSON API plus a minimal HTML browser.

Create it with :func:`create_app` and hand it to uvicorn (``box serve`` does
both, through :func:`boxganizer.web.server.run`). The app owns one :class:`BoxStore`.
"""

from __future__ import annotations

import mimetypes
import os
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from urllib.parse import unquote, urlsplit

from boxganizer import __version__, boxtypes, grid
from boxganizer.errors import (
    BoxNotFound,
    ChildrenNotAllowed,
    DuplicateBoxName,
    GridError,
    InvalidBoxContent,
    InvalidBoxName,
    RootCannotBeFiled,
    UnknownBoxType,
)
from boxganizer.models import DEFAULT_ICON, Box
from boxganizer.store import BoxStore

try:
    from fastapi import Depends, FastAPI, HTTPException, Query, Request
    from fastapi.responses import HTMLResponse, JSONResponse, Response
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


def create_app(store: BoxStore | None = None) -> FastAPI:
    """Build the web app. Pass a store to reuse one (tests, embedding)."""
    app = FastAPI(
        title="Boxganizer",
        version=__version__,
        description="Everything is a box.",
        lifespan=_lifespan,
    )
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    confine = os.environ.get(CONTENT_ROOT_ENV)
    app.state.content_root = Path(confine).expanduser() if confine else None
    app.state.store = store if store is not None else BoxStore()
    app.state.owns_store = store is None

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

    @app.get("/api/boxes", response_model=None)
    def list_boxes(
        store: StoreDep, limit: Annotated[int, Query(ge=1, le=1000)] = 200
    ) -> list[dict[str, object]]:
        return _dump(store.list_boxes(limit=limit))

    @app.get("/api/boxes/{ref}")
    def read_box(ref: str, store: StoreDep) -> dict[str, object]:
        box = store.resolve(ref)
        return box.to_dict() | {
            "children": [_brief(b) for b in store.children(box.uuid)],
            "parents": [_brief(b) for b in store.parents(box.uuid)],
        }

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

    @app.delete("/api/boxes/{ref}/children/{child}", status_code=204)
    def remove_child(ref: str, child: str, store: StoreDep) -> None:
        """Delete the parent/child link. Both boxes stay in the library."""
        parent = store.resolve(ref)
        store.unlink(parent.uuid, child)

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
        saved = store.add(box)
        if payload.parent:
            store.link(payload.parent, saved.uuid)
            if payload.cell is not None:
                _grid_place(store, payload.parent, saved.uuid, payload.cell)
        return saved.to_dict()

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


def _local_path(resource_uri: str) -> Path:
    """A `file://` URL or a plain path, as a Path."""
    if "://" in resource_uri:
        parts = urlsplit(resource_uri)
        if parts.scheme != "file":
            raise HTTPException(
                status_code=415,
                detail={"error": "resource_not_local", "scheme": parts.scheme},
            )
        return Path(unquote(parts.path))
    return Path(resource_uri).expanduser()


def _inside(path: Path, root: Path) -> bool:
    """True when `path` is `root` or below it, symlinks resolved."""
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _brief(box: Box) -> dict[str, object]:
    """Just enough of a box to draw it in a list and select it later."""
    return {"uuid": box.uuid, "name": box.name, "icon": box.icon, "box_type": box.box_type}
