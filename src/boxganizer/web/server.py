"""Run the web app: ``boxganizer-web --port 8000``."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(help="Serve the Boxganizer web UI.", add_completion=False)


@app.command()
def serve(
    host: Annotated[str, typer.Option("--host")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", "-p", min=1, max=65535)] = 8000,
    db: Annotated[Path | None, typer.Option("--db", help="Database file.")] = None,
    reload: Annotated[bool, typer.Option("--reload", help="Restart on code changes.")] = False,
) -> None:
    """Start uvicorn."""
    try:
        import uvicorn
    except ModuleNotFoundError as exc:  # pragma: no cover - only when the extra is absent
        typer.echo(
            "the web frontend needs the 'web' extra: pip install 'boxganizer[web]'", err=True
        )
        raise typer.Exit(code=1) from exc

    from boxganizer.store import BoxStore
    from boxganizer.web.app import create_app

    store = BoxStore(db) if db is not None else BoxStore()
    uvicorn.run(create_app(store), host=host, port=port, reload=reload)


def main() -> None:
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
