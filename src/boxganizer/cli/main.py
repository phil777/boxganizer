"""``box`` command line interface.

Thin shell over :class:`boxganizer.store.BoxStore`: parse arguments, call the
core, print the result. No business logic lives here.

``box`` is also how the other two interfaces start: ``box serve`` runs the web
app and ``box tui`` opens the text UI. Both import their framework lazily, so
the CLI works when the optional extra is not installed.
"""

from __future__ import annotations

import asyncio
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

from boxganizer import (
    __version__,
    bootstrap,
    boxtypes,
    config,
    editing,
    infer,
    mqtt,
    paths,
    thumbs,
)

# The module, not the `box search` command: the two would be easy to confuse.
from boxganizer import search as text_search
from boxganizer.errors import BoxganizerError, EditError, MissingDependency, MqttError
from boxganizer.models import Box
from boxganizer.store import BoxStore

app = typer.Typer(
    name="box",
    help="Organise everything as boxes.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)

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


@app.command("add")
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
        # Read into the full-text index now, so a text or PDF box is findable by what
        # it says without anyone remembering to rebuild.
        unread = text_search.sync(store, saved)
        if container is not None:
            store.link(container, saved.name)
    note = f" in {container}" if container else ""
    say("added", f"{saved.icon} {saved.name} (#{saved.id}){note}")
    if unread:
        lit(f"  note: not indexed for text — {unread}")
    if proposal is not None and saved.box_type != proposal.key:
        lit(
            f"  tip: that resource {proposal.reason} — it would suit "
            f"{proposal.icon} {proposal.label} (-t {proposal.key}, or --infer)"
        )


@app.command("list")
def box_list(
    ctx: typer.Context,
    limit: Annotated[int, typer.Option("--limit", "-n", min=1)] = 50,
    as_json: Annotated[bool, typer.Option("--json", help="Machine readable output.")] = False,
) -> None:
    """List boxes. `box ls` is the same command."""
    with _open(ctx, _db_of(ctx)) as store:
        found = store.list_boxes(limit=limit)
    if as_json:
        console.print_json(json.dumps([b.to_dict() for b in found]))
        return
    if not found:
        console.print(Text.assemble("no boxes yet — try ", ("box add", "bold"), ""))
        return
    table = Table(title=f"boxes ({len(found)})")
    table.add_column("icon", justify="center")
    table.add_column("name", style="bold")
    table.add_column("description")
    for box in found:
        table.add_row(box.icon, Text(box.name), Text(box.description))
    console.print(table)


# The shorter spelling, kept out of the command list so --help shows one entry
# for this command and points at the alias instead of repeating it.
app.command("ls", hidden=True)(box_list)


@app.command("show")
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
        # A sensor keeps the way in inside its own address; the terminal shows where
        # it listens, not the password.
        shown = mqtt.redact(box.resource_uri) if _listens_mqtt(box) else box.resource_uri
        lit(f"  resource : {shown}")
    for key, value in sorted(box.metadata.items()):
        lit(f"  {key} = {value}")
    if box.config:
        lit(f"  config   : {json.dumps(box.config, sort_keys=True)}")
    lit(f"  parents  : {', '.join(p.name for p in folks) or '-'}")
    lit(f"  children : {', '.join(c.name for c in kids) or '-'}")


@app.command("value")
def box_value(
    ctx: typer.Context,
    name: str,
    wait: Annotated[
        float,
        typer.Option("--wait", help="Seconds to wait for a message to arrive."),
    ] = 5.0,
) -> None:
    """Print the next value an MQTT box hears on its topic, once, and exit.

    The web page keeps listening and updates itself; this answers one message for
    a script or a shell. A broker that sends nothing within `--wait` is a failure,
    because the caller asked for a value and got none.
    """
    with _open(ctx, _db_of(ctx)) as store:
        box = store.get(name)
    if not _listens_mqtt(box):
        _fail(f"{box.name} is a {box.box_type} box; it does not listen to an MQTT topic")
        raise typer.Exit(code=1)
    target = mqtt.parse_target(box.resource_uri, box.metadata.get("topic"))
    try:
        reading = asyncio.run(mqtt.read_once(target, wait_s=wait))
    except MqttError as exc:
        _fail(str(exc))
        raise typer.Exit(code=1) from exc
    if reading is None:
        _fail(f"nothing arrived on {target.topic} within {wait:g}s")
        raise typer.Exit(code=1)
    if reading.encoding == "utf-8":
        console.print(Text(reading.value))
    else:
        console.print(Text(f"{reading.value} (base64, {len(reading.value)} characters)"))


def _listens_mqtt(box: Box) -> bool:
    """Whether this box reads an MQTT broker — asked of its type, not its string."""
    return boxtypes.get(box.box_type).live_source == "mqtt"


@app.command("edit")
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
        unread = text_search.sync(store, updated)
    say("saved", f"{updated.icon} {updated.name} (#{updated.id})")
    if unread:
        lit(f"  note: not indexed for text — {unread}")


@app.command("search")
def box_search(
    ctx: typer.Context,
    term: str,
    limit: Annotated[int, typer.Option("--limit", "-n", min=1, max=1000)] = 100,
    content: Annotated[
        bool,
        typer.Option(
            "--content/--no-content",
            help="Also search inside the text and PDF files. On by default.",
        ),
    ] = True,
) -> None:
    """Find boxes by their own fields, and by what their files say.

    The words are widened before they are asked: the index stems, so "running" finds
    "run"; the thesaurus adds the words that mean the same; and a word that looks like
    one already indexed is asked too, which is what catches a mistyped spelling. What
    was added to your words is printed afterwards, so nothing is quietly rewritten.
    """
    with _open(ctx, _db_of(ctx)) as store:
        named = store.search(term, limit=limit)
        shown = {box.name for box in named}
        hits: list[tuple[str, str]] = []
        if content:
            planned = text_search.plan(term, lambda word: store.vocabulary(prefix=word[:1]))
            if planned.worth_asking:
                ordered = store.content_search(planned.content_match, limit=limit * 2)
                boxes = store.boxes_by_uuid([uuid for uuid, _score, _p in ordered])
                for uuid, _score, passage in ordered:
                    box = boxes.get(uuid)
                    if box is None or box.name in shown:
                        continue
                    shown.add(box.name)
                    hits.append((f"{box.icon} {box.name}", " ".join(passage.split())))
                    if len(hits) >= limit:
                        break
            widened = text_search.explain(planned) if planned.expanded else ""
        else:
            widened = ""
    for box in named:
        lit(f"{box.icon} {box.name}")
    if hits:
        if named:
            lit("")
        for label, passage in hits:
            lit(f"{label}  [in the file: {passage}]")
    if not shown:
        lit(f"nothing matches {term!r}")
    if widened and (named or hits):
        lit(f"  searched: {widened}")


@app.command("index")
def box_index(
    ctx: typer.Context,
    root: Annotated[
        Path | None,
        typer.Option(
            "--root",
            help="Directory file resources are read from. Defaults to "
            "$BOXGANIZER_CONTENT_ROOT, then your home directory.",
        ),
    ] = None,
    show_skipped: Annotated[int, typer.Option("--show-skipped", min=0, max=200)] = 10,
) -> None:
    """Read every text and PDF box again into the full-text index.

    The index lives in the database and the files do not, so three ordinary things
    empty it: a library copied to another machine, a database written before the index
    existed, and a file edited outside Boxganizer. This makes the rows true again in
    one go. Boxes created or edited afterwards are indexed on the spot.
    """
    confine = paths.content_root(root)
    with _open(ctx, _db_of(ctx)) as store:
        report = text_search.rebuild(store, root=confine)
        say("indexed", report.summary())
        lit(f"  reading inside {confine}")
        for name, note in report.skipped[:show_skipped]:
            lit(f"  {name}: {note}")
        if len(report.skipped) > show_skipped:
            lit(f"  … and {len(report.skipped) - show_skipped} more nothing-to-read")
        lit(f"  {store.indexed_boxes()} boxes now in the index")


@app.command("rm")
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


@app.command("link")
def link_box(
    ctx: typer.Context,
    child: Annotated[str, typer.Argument(help="Box to put inside.")],
    parent: Annotated[str, typer.Argument(help="Box to put it in.")],
) -> None:
    """Put a box inside another: `box link CHILD PARENT`.

    `box link Passport Kitchen` puts Passport inside Kitchen. The box named first
    is the one that moves.
    """
    with _open(ctx, _db_of(ctx)) as store:
        store.link(parent, child)
    say("linked", f"{child} → {parent}")


@app.command("unlink")
def unlink_box(
    ctx: typer.Context,
    child: Annotated[str, typer.Argument(help="Box to take out.")],
    parent: Annotated[str, typer.Argument(help="Box to take it out of.")],
) -> None:
    """Take a box out of another: `box unlink CHILD PARENT`.

    `box unlink Passport Kitchen` removes the link that holds Passport in Kitchen;
    Passport itself stays where it is.
    """
    with _open(ctx, _db_of(ctx)) as store:
        store.unlink(parent, child)
    lit(f"unlinked {child} from {parent}")


@app.command()
def tree(
    ctx: typer.Context,
    name: Annotated[
        str | None,
        typer.Argument(help="Box to start from. Omit to start from the root box."),
    ] = None,
    depth: Annotated[int, typer.Option("--depth", "-d", min=1, max=20)] = 10,
) -> None:
    """Print the containment tree below *name*, or below the root box.

    Without a name the tree starts at the root box — asked of the database by type,
    so a library with thousands of boxes still starts in the same place.

    The graph may hold cycles, so a box that turns up inside itself is marked and
    not followed again.
    """
    with _open(ctx, _db_of(ctx)) as store:
        start = store.get(name) if name else store.root_box()
        if start is None:
            _fail("no root box in this library — run `box init`")
            raise typer.Exit(code=1)
        _print_tree(store, start, level=0, max_depth=depth, path=())


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
