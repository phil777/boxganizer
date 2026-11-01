"""Start the web app.

The command lives in the CLI (``box serve``); this module only knows how to
hand the FastAPI app to uvicorn. Both uvicorn and FastAPI are optional, so the
imports stay inside :func:`run`.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from boxganizer.store import BoxStore


def run(
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    db: Path | None = None,
    reload: bool = False,
    confined: bool = False,
    on_start: Callable[[str, int], None] | None = None,
) -> None:
    """Serve the web app until interrupted.

    *confined* keeps the server inside its content root — the directory named by
    `$BOXGANIZER_CONTENT_ROOT`, or the default one. Left off, which is the default, the server
    reads and files any path this account can reach: confinement is something you ask for, not
    something you have to switch off (ADR 30).

    *on_start* runs once the app exists and immediately before uvicorn takes
    over, so a caller can announce the address without lying when the ``web``
    extra is missing. :class:`ModuleNotFoundError` propagates in that case and
    the caller turns it into an install hint.
    """
    import uvicorn

    from boxganizer.store import BoxStore
    from boxganizer.web.app import create_app

    store: BoxStore = BoxStore(db)
    try:
        asgi = create_app(store, confined=confined)
        if on_start is not None:
            on_start(host, port)
        uvicorn.run(asgi, host=host, port=port, reload=reload)
    finally:
        # This function created the store, so this function closes it.
        store.close()
