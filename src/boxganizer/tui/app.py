"""Terminal UI: browse, filter and create boxes without leaving the shell.

Run it with ``boxganizer-tui``. It reads and writes the same database as the
CLI and the web app, through :class:`boxganizer.store.BoxStore`.

Keys: type to filter, up/down to move, enter to drill into a box, ``n`` to
create a box, ``r`` to reload, escape to show every box, ``q`` to quit.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, ClassVar

import typer

try:
    from textual.app import App, ComposeResult
    from textual.binding import Binding, BindingType
    from textual.containers import Horizontal, Vertical
    from textual.screen import Screen
    from textual.widgets import (
        Button,
        Footer,
        Header,
        Input,
        Label,
        ListItem,
        ListView,
        Static,
    )
except ModuleNotFoundError as exc:  # pragma: no cover - only when the extra is absent
    raise ModuleNotFoundError(
        "the text UI needs the 'tui' extra: pip install 'boxganizer[tui]'"
    ) from exc

from boxganizer.errors import BoxganizerError
from boxganizer.models import Box
from boxganizer.store import BoxStore

FILTER_ID = "filter"
LIST_ID = "box-list"
DETAIL_ID = "detail"
_DETAIL_EMPTY = "No box to show. Press n to create one."


class NewBoxScreen(Screen[str | None]):
    """Modal that asks for the name of the box to create."""

    CSS = """
    NewBoxScreen { align: center middle; }
    #new-box { width: 64; height: auto; padding: 1 2; border: round $primary; }
    #new-box-actions { height: auto; }
    """

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="new-box"):
            yield Label("New box name")
            yield Input(placeholder="Kitchen", id="new-box-name")
            with Horizontal(id="new-box-actions"):
                yield Button("Create", variant="primary", id="create")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#new-box-name", Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "create":
            self.dismiss(self.query_one(Input).value.strip() or None)
        else:
            self.dismiss(None)


class BoxganizerApp(App[None]):
    """One-screen box browser."""

    CSS = """
    Input { margin: 0 1; }
    ListView { border: round $primary 40%; margin: 0 1; }
    #detail { height: auto; min-height: 4; margin: 0 1; padding: 0 1; }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("q", "quit", "Quit"),
        Binding("n", "new_box", "New box"),
        Binding("r", "reload", "Reload"),
        Binding("escape", "show_all", "All boxes"),
    ]

    def __init__(self, db: Path | None = None) -> None:
        super().__init__()
        self._db = db
        self.store = BoxStore(db) if db is not None else BoxStore()
        self._shown: list[str] = []
        self._detail_markup = _DETAIL_EMPTY

    # -- layout ------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal():
            yield Input(placeholder="filter boxes…", id=FILTER_ID)
        yield ListView(id=LIST_ID)
        yield Static(_DETAIL_EMPTY, id=DETAIL_ID)
        yield Footer()

    def on_mount(self) -> None:
        self.title = "Boxganizer"
        self.sub_title = str(self.store.path)
        self._refresh()

    # -- actions -----------------------------------------------------------

    def action_reload(self) -> None:
        self._refresh()

    def action_show_all(self) -> None:
        self.query_one(Input).value = ""
        self._refresh()

    def action_new_box(self) -> None:
        self.push_screen(NewBoxScreen(), self._add_box)

    # -- events ------------------------------------------------------------

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == FILTER_ID:
            self._refresh(term=event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == FILTER_ID:
            self._refresh(term=event.value)

    @property
    def shown(self) -> list[str]:
        """Names of the boxes currently listed, in display order."""
        return list(self._shown)

    @property
    def detail_markup(self) -> str:
        """Last text written to the detail pane."""
        return self._detail_markup

    def _name_at(self, index: int | None) -> str | None:
        if index is None or not 0 <= index < len(self._shown):
            return None
        return self._shown[index]

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        name = self._name_at(event.list_view.index)
        if name is not None:
            self._show(name)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Enter drills into the selected box: the list becomes its children."""
        name = self._name_at(event.index)
        if name is None:
            return
        children = self.store.children(name)
        self._fill(children)
        self._show(name)
        if not children:
            self.notify(f"{name} is empty", timeout=2)

    # -- helpers -----------------------------------------------------------

    def _refresh(self, *, term: str | None = None, select: str | None = None) -> None:
        needle = term if term is not None else self.query_one(Input).value
        if needle.strip():
            boxes = self.store.search(needle, limit=500)
        else:
            boxes = self.store.list_boxes(limit=500)
        self._fill(boxes, select=select)

    def _fill(self, boxes: list[Box], *, select: str | None = None) -> None:
        listing = self.query_one(ListView)
        listing.remove_children()
        self._shown = [box.name for box in boxes]
        listing.extend(ListItem(Label(f"{box.icon}  {box.name}")) for box in boxes)
        if not boxes:
            listing.index = None
            self._detail(_DETAIL_EMPTY)
            return
        position = 0
        if select is not None:
            position = next((i for i, name in enumerate(self._shown) if name == select), 0)
        listing.index = position
        self._show(self._shown[position])

    def _show(self, name: str) -> None:
        try:
            box = self.store.get(name)
        except BoxganizerError as exc:  # pragma: no cover - box vanished mid-session
            self._show_error(str(exc))
            return
        parents = ", ".join(p.name for p in self.store.parents(name)) or "—"
        children = ", ".join(c.name for c in self.store.children(name)) or "—"
        lines = [
            f"[b]{box.icon} {box.name}[/]  #{box.id}",
            box.description or "[dim]no description[/]",
        ]
        if box.resource_uri:
            lines.append(f"resource : {box.resource_uri}")
        lines.append(f"parents  : {parents}")
        lines.append(f"children : {children}")
        for key, value in sorted(box.metadata.items()):
            lines.append(f"{key} = {value}")
        self._detail("\n".join(lines))

    def _detail(self, markup: str) -> None:
        self._detail_markup = markup
        self.query_one(f"#{DETAIL_ID}", Static).update(markup)

    def _show_error(self, message: str) -> None:
        self._detail(f"[red]{message}[/]")
        self.notify(message, severity="error")

    def _add_box(self, name: str | None) -> None:
        if not name:
            return
        try:
            box = self.store.add(Box(name=name))
        except BoxganizerError as exc:  # reported in the detail pane, no crash
            self._show_error(str(exc))
            return
        self._refresh(select=box.name)
        self.notify(f"created {box.name}")

    def on_unmount(self) -> None:
        self.store.close()


cli = typer.Typer(help="Open the Boxganizer text UI.", add_completion=False)


@cli.command()
def main(
    db: Annotated[Path | None, typer.Option("--db", help="Database file.")] = None,
) -> None:
    """Launch the terminal interface."""
    BoxganizerApp(db).run()


if __name__ == "__main__":  # pragma: no cover
    main()
