"""FastAPI application: JSON API plus a minimal HTML browser.

Create it with :func:`create_app` and hand it to uvicorn (``box serve`` does
both, through :func:`boxganizer.web.server.run`). The app owns one :class:`BoxStore`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from boxganizer import __version__, boxtypes
from boxganizer.errors import (
    BoxNotFound,
    ChildrenNotAllowed,
    CyclicLink,
    DuplicateBoxName,
    InvalidBoxContent,
    InvalidBoxName,
    UnknownBoxType,
)
from boxganizer.models import DEFAULT_ICON, Box
from boxganizer.store import BoxStore

try:
    from fastapi import Depends, FastAPI, HTTPException, Query, Request
    from fastapi.responses import HTMLResponse, JSONResponse
    from fastapi.templating import Jinja2Templates
    from pydantic import BaseModel, Field, JsonValue
except ModuleNotFoundError as exc:  # pragma: no cover - only when the extra is absent
    raise ModuleNotFoundError(
        "the web frontend needs the 'web' extra: pip install 'boxganizer[web]'"
    ) from exc

_TEMPLATE_DIR = Path(__file__).parent / "templates"
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

    @app.exception_handler(CyclicLink)
    async def _cycle(request: Request, exc: CyclicLink) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": {"error": "cyclic_link"}})

    @app.exception_handler(ChildrenNotAllowed)
    async def _no_children(request: Request, exc: ChildrenNotAllowed) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"detail": {"error": "children_not_allowed", "parent_type": exc.parent_type}},
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
        return store.add(box).to_dict()

    @app.get("/api/boxes/{name}")
    def read_box(name: str, store: StoreDep) -> dict[str, object]:
        box = store.get(name)
        return box.to_dict() | {
            "children": [b.name for b in store.children(name)],
            "parents": [b.name for b in store.parents(name)],
        }

    @app.delete("/api/boxes/{name}", status_code=204)
    def delete_box(name: str, store: StoreDep) -> None:
        store.remove(name)

    @app.post("/api/boxes/{parent}/children", status_code=201)
    def add_child(parent: str, payload: LinkCreate, store: StoreDep) -> dict[str, list[str]]:
        store.link(parent, payload.child)
        return {"children": [b.name for b in store.children(parent)]}

    @app.get("/api/search")
    def search(
        store: StoreDep,
        q: Annotated[str, Query(min_length=1)],
        limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    ) -> list[dict[str, object]]:
        return _dump(store.search(q, limit=limit))

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, store: StoreDep, q: str | None = None) -> HTMLResponse:
        boxes = store.search(q, limit=500) if q else store.list_boxes(limit=500)
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={"boxes": boxes, "query": q or "", "version": __version__},
        )

    return app


def _dump(boxes: Sequence[Box]) -> list[dict[str, object]]:
    return [box.to_dict() for box in boxes]
