"""``box`` command line interface.

Thin shell over :class:`boxganizer.store.BoxStore`: parse arguments, call the
core, print the result. No business logic lives here.

``box`` is also how the other two interfaces start: ``box serve`` runs the web
app and ``box tui`` opens the text UI. Both import their framework lazily, so
the CLI works when the optional extra is not installed.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from boxganizer import __version__, bootstrap, boxtypes, config, editing, infer, thumbs
from boxganizer.errors import BoxganizerError, EditError, MissingDependency
from boxganizer.models import Box
from boxganizer.store import BoxStore

app = typer.Typer(
    name="box",
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


def _editor_command(explicit: str | None) -> list[str]:
    """The command that will be handed the file."""
    raw = explicit or os.environ.get("EDITOR") or os.environ.get("VISUAL")
    if not raw or not raw.strip():
        raise EditError('no editor configured: set $EDITOR, or pass --editor "nano"')
    return shlex.split(raw)


def _edit_in_editor(text: str, name: str, editor: str | None) -> str:
    """Write *text* to a temporary YAML file, edit it, return the file contents."""
    command = _editor_command(editor)
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-")[:40] or "box"
    handle, filename = tempfile.mkstemp(prefix=f"box-{stem}-", suffix=".yaml")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        result = subprocess.run([*command, filename], check=False)
        if result.returncode != 0:
            raise EditError(
                f"{command[0]} exited with status {result.returncode}; the box was not touched"
            )
        with open(filename, encoding="utf-8") as stream:
            return stream.read()
    finally:
        os.unlink(filename)


def _resolve_parent(
    store: BoxStore, box: Box, *, parent: str | None, no_parent: bool
) -> str | None:
    """Which box the new box goes into: an explicit one, `root`, or none.

    Checked before the insert, so a bad `--parent` never leaves a stray box
    behind.
    """
    if no_parent:
        if parent:
            raise typer.BadParameter("--parent and --no-parent cannot be combined")
        return None
    # No implicit parent. The root box has a single slot, so filing every new box
    # there would keep evicting the previous one; say `--parent root` to mean it.
    chosen = parent
    if chosen is None:
        return None
    if store.get(chosen).key == box.key:
        raise typer.BadParameter(f"a box cannot be its own parent ({chosen!r})")
    if not store.exists(chosen):
        raise typer.BadParameter(f"no box named {chosen!r} to put it in")
    return chosen


def _parse_metadata(items: Sequence[str]) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep or not key.strip():
            raise typer.BadParameter(f"--meta expects key=value, got {item!r}")
        metadata[key.strip()] = value
    return metadata


def _fail(message: str) -> None:
    """Report an error. The message is literal: brackets in it stay visible."""
    err_console.print(Text.assemble(("error: ", "red"), message))


def say(prefix: str, body: str, *, style: str = "green") -> None:
    """Print a styled word, then *body* exactly as stored — never as markup."""
    console.print(Text.assemble((f"{prefix} ", style), body))


def lit(body: str) -> None:
    """Print *body* exactly as stored — never as markup."""
    console.print(Text(body))


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
    lit(f"database : {path}")
    lit(f"created  : {'yes' if exists else 'not yet'}")
    lit(f"boxes    : {count}")


@app.command()
def init(ctx: typer.Context, db: DbOption = None) -> None:
    """Create the database, its schema, and the two boxes every library starts with."""
    path = db or _db_of(ctx) or config.default_db_path()
    with _open(ctx, path) as store:
        created = bootstrap.bootstrap(store)
    if created:
        say("initialised", str(path))
        for name in created:
            lit(f"  created {name}")
    else:
        lit(f"already initialised: {path} (nothing added)")


@boxes_app.command("add")
def box_add(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Unique box name.")],
    description: Annotated[str, typer.Option("--description", "-d")] = "",
    icon: Annotated[str | None, typer.Option("--icon", "-i")] = None,
    box_type: Annotated[
        str, typer.Option("--type", "-t", help="Box type; see 'box types'.")
    ] = boxtypes.DEFAULT_TYPE_KEY,
    explicit_type: Annotated[bool, typer.Option("--explicit-type", hidden=True)] = False,
    resource: Annotated[str | None, typer.Option("--resource", "-r")] = None,
    meta: Annotated[
        list[str] | None, typer.Option("--meta", "-m", help="key=value, repeatable.")
    ] = None,
    parent: Annotated[
        str | None,
        typer.Option(
            "--parent",
            help="Box to put the new box in. No parent by default: the root box "
            "holds only one child, so nothing is filed there implicitly.",
        ),
    ] = None,
    no_parent: Annotated[
        bool, typer.Option("--no-parent", help="Create the box at the top level.")
    ] = False,
    infer_type: Annotated[
        bool,
        typer.Option(
            "--infer/--no-infer",
            help="Use --infer to create the specialised type the resource suggests "
            "(-t pdf is still your call).",
        ),
    ] = False,
) -> None:
    """Create a box."""
    # `-t` on the command line is a decision; the default is not. Only an
    # unqualified choice is ever changed by --infer.
    # Typer ships its own copy of click's ParameterSource, so the enum member is
    # matched by name rather than by importing one of the two classes.
    source = ctx.get_parameter_source("box_type")
    explicit_type = getattr(source, "name", "") == "COMMANDLINE"
    proposal = infer.suggest(resource, box_type)
    if proposal is not None and infer_type and not explicit_type:
        box_type = proposal.key
    box = Box(
        name=name,
        description=description,
        box_type=box_type,
        resource_uri=resource,
        metadata=_parse_metadata(meta or []),
    )
    if icon:
        box.icon = icon
    else:
        boxtypes.adopt_type_icon(box)
        thumbs.icon_from_resource(box)  # an image box is drawn as its own picture
    with _open(ctx, _db_of(ctx)) as store:
        container = _resolve_parent(store, box, parent=parent, no_parent=no_parent)
        saved = store.add(box)
        if container is not None:
            store.link(container, saved.name)
    note = f" in {container}" if container else ""
    say("added", f"{saved.icon} {saved.name} (#{saved.id}){note}")
    if proposal is not None and saved.box_type != proposal.key:
        lit(
            f"  tip: that resource {proposal.reason} — it would suit "
            f"{proposal.icon} {proposal.label} (-t {proposal.key}, or --infer)"
        )


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
        console.print(Text.assemble("no boxes yet — try ", ("box box add", "bold"), ""))
        return
    table = Table(title=f"boxes ({len(found)})")
    table.add_column("icon", justify="center")
    table.add_column("name", style="bold")
    table.add_column("description")
    for box in found:
        table.add_row(box.icon, Text(box.name), Text(box.description))
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
    console.print(Text.assemble(f"{box.icon} ", (box.name, "bold"), f"  #{box.id}"))
    lit(f"  type     : {_type_note(box.box_type)}")
    lit(f"  uuid     : {box.uuid}")
    if box.description:
        lit(f"  {box.description}")
    if box.resource_uri:
        lit(f"  resource : {box.resource_uri}")
    for key, value in sorted(box.metadata.items()):
        lit(f"  {key} = {value}")
    if box.config:
        lit(f"  config   : {json.dumps(box.config, sort_keys=True)}")
    lit(f"  parents  : {', '.join(p.name for p in folks) or '-'}")
    lit(f"  children : {', '.join(c.name for c in kids) or '-'}")


@boxes_app.command("edit")
def box_edit(
    ctx: typer.Context,
    name: str,
    editor: Annotated[
        str | None,
        typer.Option(
            "--editor",
            help="Command to edit with. Defaults to $EDITOR, then $VISUAL.",
        ),
    ] = None,
) -> None:
    """Edit a box in a text editor: the row becomes YAML, the file becomes the row.

    metadata and config are shown as YAML and written back as JSON. Links are not
    edited here — use `box link`. Quit the editor without saving to change nothing.
    """
    with _open(ctx, _db_of(ctx)) as store:
        box = store.get(name)
        start = editing.dump(box, parents=[b.name for b in store.parents(box.name)])
        written = _edit_in_editor(start, box.name, editor)
        if written == start:
            console.print("left unchanged")
            return
        updated = store.update(editing.load(written, original=box))
    say("saved", f"{updated.icon} {updated.name} (#{updated.id})")


# Reachable as `box edit` as well as `box box edit`.
app.command("edit")(box_edit)


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
        lit(f"{box.icon} {box.name}")
    if not found:
        lit(f"nothing matches {term!r}")


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
    say("deleted", removed.name, style="red")


@links_app.command("in")
def link_in(
    ctx: typer.Context,
    child: Annotated[str, typer.Argument(help="Box to put inside.")],
    parent: Annotated[str, typer.Argument(help="Box to put it in.")],
) -> None:
    """Put *child* inside *parent*."""
    with _open(ctx, _db_of(ctx)) as store:
        store.link(parent, child)
    say("linked", f"{child} → {parent}")


@links_app.command("out")
def link_out(
    ctx: typer.Context,
    child: str,
    parent: str,
) -> None:
    """Take *child* out of *parent*."""
    with _open(ctx, _db_of(ctx)) as store:
        store.unlink(parent, child)
    lit(f"unlinked {child} from {parent}")


@app.command()
def tree(
    ctx: typer.Context,
    name: Annotated[str | None, typer.Argument(help="Root box. Omit to list roots.")] = None,
    depth: Annotated[int, typer.Option("--depth", "-d", min=1, max=20)] = 10,
) -> None:
    """Print the containment tree below *name*.

    The graph may hold cycles, so a box that turns up inside itself is marked and
    not followed again.
    """
    with _open(ctx, _db_of(ctx)) as store:
        roots = [store.get(name)] if name else _find_roots(store)
        if not roots:
            console.print("no box without a parent — the graph is all cycles; each box once")
            roots = store.list_boxes(limit=200)
        started: set[str] = set()
        for root_box in roots:
            if root_box.key in started:
                continue
            started.add(root_box.key)
            _print_tree(store, root_box, level=0, max_depth=depth, path=())


def _find_roots(store: BoxStore) -> list[Box]:
    return [box for box in store.list_boxes() if not store.parents(box.name)]


def _print_tree(
    store: BoxStore, box: Box, *, level: int, max_depth: int, path: tuple[str, ...]
) -> None:
    if box.key in path:
        lit("  " * level + f"\u27f2 {box.icon} {box.name}  (cycle: already above)")
        return
    lit("  " * level + f"{box.icon} {box.name}")
    if level + 1 >= max_depth:
        return
    for kid in store.children(box.name):
        _print_tree(store, kid, level=level + 1, max_depth=max_depth, path=(*path, box.key))


@app.command()
def types(
    as_json: Annotated[bool, typer.Option("--json", help="Machine readable output.")] = False,
    selectable_only: Annotated[
        bool, typer.Option("--selectable", help="Leave out the grouping levels.")
    ] = False,
) -> None:
    """Print the box type hierarchy and what each type insists on."""
    if as_json:
        described = [
            kind.describe() for kind in boxtypes.all_types(include_abstract=not selectable_only)
        ]
        console.print_json(json.dumps(described))
        return
    for depth, kind in boxtypes.tree(include_abstract=not selectable_only):
        needs = _type_requirements(kind)
        console.print(
            Text.assemble(
                "  " * depth,
                f"{kind.icon} ",
                (kind.key, "bold"),
                "  (group)" if kind.is_grouping() else "",
                (f"  {kind.summary}", "dim"),
                (f"  [{needs}]" if needs else "", "dim"),
            )
        )


def _type_note(key: str) -> str:
    """Describe a stored type, even one this version does not know."""
    kind = boxtypes.try_get(key)
    if kind is None:
        return f"{key} (unknown type; this Boxganizer is older than the box)"
    return f"{kind.icon} {kind.label} ({kind.key})"


def _type_requirements(kind: type[boxtypes.BoxType]) -> str:
    """Short note about what a type demands, shown by `box types`."""
    parts: list[str] = []
    if kind.required_metadata:
        parts.append("metadata: " + ", ".join(sorted(kind.required_metadata)))
    if not kind.allows_children:
        parts.append("no children")
    if kind.resource_uri_required:
        parts.append("resource required")
    return ", ".join(parts)


@app.command()
def serve(
    ctx: typer.Context,
    host: Annotated[str, typer.Option("--host", help="Bind address.")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", "-p", min=1, max=65535)] = 8000,
    reload: Annotated[bool, typer.Option("--reload", help="Restart on code changes.")] = False,
    db: DbOption = None,
) -> None:
    """Start the web app: page on /, JSON API on /api, docs on /docs."""
    path = db or _db_of(ctx) or config.default_db_path()
    try:
        from boxganizer.web.server import run
    except ModuleNotFoundError as exc:  # pragma: no cover - only without the extra
        raise MissingDependency("web") from exc

    def announce(bound_host: str, bound_port: int) -> None:
        console.print(
            Text.assemble(
                "serving ",
                (str(path), "bold"),
                f" on http://{bound_host}:{bound_port} — Ctrl-C to stop",
            )
        )

    try:
        run(host=host, port=port, db=path, reload=reload, on_start=announce)
    except ModuleNotFoundError as exc:  # pragma: no cover - only without the extra
        raise MissingDependency("web") from exc


@app.command()
def tui(ctx: typer.Context, db: DbOption = None) -> None:
    """Open the text UI in this terminal."""
    try:
        from boxganizer.tui.app import BoxganizerApp
    except ModuleNotFoundError as exc:  # pragma: no cover - only without the extra
        raise MissingDependency("tui") from exc

    BoxganizerApp(db or _db_of(ctx)).run()


def main() -> None:
    """Console-script entry point: turn core errors into a message and exit 1."""
    try:
        app()
    except BoxganizerError as exc:
        _fail(str(exc))
        raise SystemExit(1) from exc
