"""``boxganizer`` command line interface.

Thin shell over :class:`boxganizer.store.BoxStore`: parse arguments, call the
core, print the result. No business logic lives here.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from boxganizer import __version__, config
from boxganizer.errors import BoxganizerError
from boxganizer.models import Box
from boxganizer.store import BoxStore

app = typer.Typer(
    name="boxganizer",
    help="Organise everything as boxes.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)
boxes_app = typer.Typer(help="Create, inspect and delete boxes.", no_args_is_help=True)
links_app = typer.Typer(help="Put boxes inside other boxes.", no_args_is_help=True)
app.add_typer(boxes_app, name="box")
app.add_typer(links_app, name="link")

console = Console()
err_console = Console(stderr=True)

DbOption = Annotated[
    Path | None,
    typer.Option("--db", help="Database file. Defaults to $BOXGANIZER_DB or the user data dir."),
]


def _db_of(ctx: typer.Context) -> Path | None:
    obj = ctx.obj if isinstance(ctx.obj, dict) else {}
    db_path: Path | None = obj.get("db")
    return db_path


def _open(ctx: typer.Context, db: Path | None) -> BoxStore:
    store = BoxStore(db)
    ctx.call_on_close(store.close)
    return store


def _parse_metadata(items: Sequence[str]) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep or not key.strip():
            raise typer.BadParameter(f"--meta expects key=value, got {item!r}")
        metadata[key.strip()] = value
    return metadata


def _fail(message: str) -> None:
    err_console.print(f"[red]error:[/red] {message}")


@app.callback()
def root(
    ctx: typer.Context,
    db: DbOption = None,
) -> None:
    """Global options, kept on the root command so sub-commands stay small."""
    ctx.obj = {"db": db}


@app.command()
def version() -> None:
    """Print the Boxganizer version."""
    console.print(f"boxganizer {__version__}")


@app.command()
def status(ctx: typer.Context, db: DbOption = None) -> None:
    """Show where the database lives and how many boxes it holds."""
    path = db or _db_of(ctx) or config.default_db_path()
    exists = path.exists()
    with _open(ctx, path) as store:
        count = store.count()
    console.print(f"database : {path}")
    console.print(f"created  : {'yes' if exists else 'not yet'}")
    console.print(f"boxes    : {count}")


@app.command()
def init(ctx: typer.Context, db: DbOption = None) -> None:
    """Create the database file and its schema."""
    path = db or _db_of(ctx) or config.default_db_path()
    with _open(ctx, path):
        pass
    console.print(f"[green]initialised[/green] {path}")


@boxes_app.command("add")
def box_add(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Unique box name.")],
    description: Annotated[str, typer.Option("--description", "-d")] = "",
    icon: Annotated[str | None, typer.Option("--icon", "-i")] = None,
    resource: Annotated[str | None, typer.Option("--resource", "-r")] = None,
    meta: Annotated[
        list[str] | None, typer.Option("--meta", "-m", help="key=value, repeatable.")
    ] = None,
) -> None:
    """Create a box."""
    box = Box(
        name=name,
        description=description,
        resource_uri=resource,
        metadata=_parse_metadata(meta or []),
    )
    if icon:
        box.icon = icon
    with _open(ctx, _db_of(ctx)) as store:
        saved = store.add(box)
    console.print(f"[green]added[/green] {saved.icon} {saved.name} (#{saved.id})")


@boxes_app.command("list")
def box_list(
    ctx: typer.Context,
    limit: Annotated[int, typer.Option("--limit", "-n", min=1)] = 50,
    as_json: Annotated[bool, typer.Option("--json", help="Machine readable output.")] = False,
) -> None:
    """List boxes."""
    with _open(ctx, _db_of(ctx)) as store:
        found = store.list_boxes(limit=limit)
    if as_json:
        console.print_json(json.dumps([b.to_dict() for b in found]))
        return
    if not found:
        console.print("no boxes yet — try [bold]boxganizer box add[/] ")
        return
    table = Table(title=f"boxes ({len(found)})")
    table.add_column("icon", justify="center")
    table.add_column("name", style="bold")
    table.add_column("description")
    for box in found:
        table.add_row(box.icon, box.name, box.description)
    console.print(table)


@boxes_app.command("show")
def box_show(
    ctx: typer.Context,
    name: str,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show one box, its parents and its children."""
    with _open(ctx, _db_of(ctx)) as store:
        box = store.get(name)
        kids = store.children(name)
        folks = store.parents(name)
    if as_json:
        payload = box.to_dict() | {
            "children": [b.name for b in kids],
            "parents": [b.name for b in folks],
        }
        console.print_json(json.dumps(payload))
        return
    console.print(f"{box.icon} [bold]{box.name}[/]  #{box.id}")
    if box.description:
        console.print(f"  {box.description}")
    if box.resource_uri:
        console.print(f"  resource : {box.resource_uri}")
    for key, value in sorted(box.metadata.items()):
        console.print(f"  {key} = {value}")
    console.print(f"  parents  : {', '.join(p.name for p in folks) or '-'}")
    console.print(f"  children : {', '.join(c.name for c in kids) or '-'}")


@boxes_app.command("search")
def box_search(
    ctx: typer.Context,
    term: str,
    limit: Annotated[int, typer.Option("--limit", "-n", min=1)] = 100,
) -> None:
    """Search names, descriptions, resources and metadata."""
    with _open(ctx, _db_of(ctx)) as store:
        found = store.search(term, limit=limit)
    for box in found:
        console.print(f"{box.icon} {box.name}")
    if not found:
        console.print(f"nothing matches {term!r}")


@boxes_app.command("rm")
def box_remove(
    ctx: typer.Context,
    name: str,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask.")] = False,
) -> None:
    """Delete a box. Links to and from it disappear too."""
    with _open(ctx, _db_of(ctx)) as store:
        if not yes and not typer.confirm(f"delete box {name!r}?"):
            raise typer.Abort
        removed = store.remove(name)
    console.print(f"[red]deleted[/red] {removed.name}")


@links_app.command("in")
def link_in(
    ctx: typer.Context,
    child: Annotated[str, typer.Argument(help="Box to put inside.")],
    parent: Annotated[str, typer.Argument(help="Box to put it in.")],
) -> None:
    """Put *child* inside *parent*."""
    with _open(ctx, _db_of(ctx)) as store:
        store.link(parent, child)
    console.print(f"[green]linked[/green] {child} → {parent}")


@links_app.command("out")
def link_out(
    ctx: typer.Context,
    child: str,
    parent: str,
) -> None:
    """Take *child* out of *parent*."""
    with _open(ctx, _db_of(ctx)) as store:
        store.unlink(parent, child)
    console.print(f"unlinked {child} from {parent}")


@app.command()
def tree(
    ctx: typer.Context,
    name: Annotated[str | None, typer.Argument(help="Root box. Omit to list roots.")] = None,
    depth: Annotated[int, typer.Option("--depth", "-d", min=1, max=20)] = 10,
) -> None:
    """Print the containment tree below *name*."""
    with _open(ctx, _db_of(ctx)) as store:
        roots = [store.get(name)] if name else _find_roots(store)
        for root_box in roots:
            _print_tree(store, root_box, level=0, max_depth=depth)
        if not roots:
            console.print("every box has a parent; pass a box name to start there")


def _find_roots(store: BoxStore) -> list[Box]:
    return [box for box in store.list_boxes() if not store.parents(box.name)]


def _print_tree(store: BoxStore, box: Box, *, level: int, max_depth: int) -> None:
    console.print("  " * level + f"{box.icon} {box.name}")
    if level + 1 >= max_depth:
        return
    for kid in store.children(box.name):
        _print_tree(store, kid, level=level + 1, max_depth=max_depth)


def main() -> None:  # pragma: no cover - console-script wrapper
    try:
        app()
    except BoxganizerError as exc:
        _fail(str(exc))
        raise typer.Exit(code=1) from exc
