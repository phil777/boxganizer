# The web app

`box serve` puts the library in a browser: a left pane to search and pick, a main pane that shows
one box with **its content first**, and one modal to make a box. The library is the same SQLite
store the CLI and the text UI read and write, so a box made here is immediately `box show`-able
there.

This file is the reference: what each kind of box draws, and what its controls do. The
[README](../README.md) gives the tour; the decisions behind the shapes are in
[the ADRs](adr/).

## What each box shows

What a box shows depends on what it is. A view is registered per type key in
`web/static/app.js` (`registerView`), and a type with no view of its own falls back to the detail
panel — the last row of the table.

| Box                | Content view                                              |
| ------------------ | --------------------------------------------------------- |
| image              | the picture, scaled into the space left, ratio untouched   |
| text, markdown     | the file's text; wrap, crop and font size in the right-click menu; vertical scroll always, horizontal when wrap is off. A **markdown** file is rendered instead — headings, emphasis, lists that nest, tables that line up, quotes, fenced code, links — with the plain text one click away and that choice kept in the box. Raw HTML in the file stays text on the screen, and a link whose address would run something keeps its words and loses its reach |
| folder, directory, cabinet | its children, as a list or as icons; double-click opens one in place; a search box narrows the list by name or type — it starts empty every time, while the searches you have tried are remembered in the box. A **directory** box shows the top of its folder whenever it is opened, however deep the last browse went; a file in it can be dragged out — onto another box to file a new box inside it, or onto the library list to leave it unfiled — with the type inferred from the file |
| grid               | rows and columns you set (type them or step them), one child per cell, drag to move, drop on an occupied cell to swap, right-click to create a box in a cell |
| desktop            | icons anywhere on the surface: drag them with the pointer, drag a corner to size them, arrow-key them about, and let a magnetic grid of the size you chose pull them onto its lines. The resource, when it is a picture, is the wallpaper — right-click the bare surface to choose one or take it away. Double-click opens a box in place of the desktop. Shown in a smaller pane it is the whole desktop drawn smaller, not a corner of it: canvas, wallpaper and icons scale by one factor, so an icon stays on the same feature of the picture |
| tiling-viewport    | its space split into panes, each showing one box with its own path line and four buttons — settings, close, split side by side, split above and below — and a bar between any two panes you can drag; the split, the ratio and what is open where are saved on the box. Each pane folds **its own** settings row: one gear per box on screen, and the gear in the top line reaches no further than the main screen |
| windowed-viewport  | floating windows over the same space, each with its own path line, settings gear and window buttons — **minimize** (the title line and nothing else), **maximize** (the whole board) and **restore** (back to the rectangle it had). Drag the title to move a window, the corner to resize it, click anywhere in it to raise it above the rest; the settings row holds the **transparency** slider — **0 % invisible, 100 % solid**, and it starts at 0 — so a windowed box shows you the window you last touched and lets the rest of the pile recede; the focused window stays solid whatever the slider says, so the desk can show through without the box you are reading going with it. Everything comes back where you left it |
| dynamic-folder     | the folder list; above it the filter as one expression, a row per clause to add, remove, reorder or negate, what the filter asks for in words, and a button to re-apply it. It shows its members and refuses to receive one: a drop says "not here" in amber and explains itself (ADR 25) |
| mindmap            | the box in a rounded node at the centre, its children as branches growing left and right, each branch curved to its parent in its own colour. Click into it and the keys drive it: **Insert** a node under the one in hand, **Enter** beside it, **arrows** to walk the map, **Ctrl+↑↓** to shuffle a node among its siblings, **Ctrl+←→** to push it deeper into the map or pull it out towards the centre — read off the side the branch grows on, so Ctrl always means away from the middle or towards it, and a branch of the centre changes side instead of rising above it. Double-click, or **F2**, writes the node's name where it stands. A box dragged onto a node that nothing has been written into takes that node's place; onto any other node it is filed inside |
| browser, web       | the page, framed; the address bar saves back to the box |
| mqtt               | the last message on its topic, and every one after it, as the broker sends them — worded and dressed by the box's own templates, with the raw payload in the tooltip. The address is shown with its password hidden, which the box keeps for itself. In a grid cell it is the last value alone: the cell does not listen, the opened box does |
| vnc                | another machine's screen. The panel first asks what is there — the server's name, the protocol it speaks, how big its screen is — and draws nothing until you press **Show the screen**. From then the machine sends a picture when its screen changes and nothing when it does not; your pointer and keystrokes go back to it, mapped through whatever size the pane drew the screen at. **A server that wants a password is asked for one**, in a field that appears when the screen asks and never otherwise; the answer is used for that connection and never written to the library — which is a file that gets copied — though you may point the box at a file that holds it (`password_file`, outside the library, readable only by you). ADRs 15 and 17 |
| ssh                | a console on another machine: the address it goes to, the line to type for a session of your own, a field for one command at a time, and the transcript of what was asked and what answered, exit code and all. Up and down arrows walk what was run here, kept in the box. **There is no password field, and none can be stored** — the type refuses an address carrying one; keys, an agent and `~/.ssh/config` decide who you are. It is a console and not a terminal emulator, and says so on the panel: one command, one answer, no full-screen programs |
| note               | an `edit` switch (a real switch: `role=switch`, `aria-checked`, knob) and a `read-only` / `editing` cue on the sheet; the toolbar is always visible because it is how you write, not a setting | a post-it with a place in the graph: **Markdown** or **Plain text**, and — when markdown — one **`edit`** switch — a real switch, `role="switch"` with `aria-checked`, the word beside it never moving — and the note says its condition on itself with a small `read-only` or `editing` cue, because the toolbar is one thing and the sheet you are about to type into is another. Both settings are kept on the box. The toolbox writes markdown markers into the text and nothing else: `**` around a selection, a `#` or a `-` at the start of every line the selection touches. A box can be named inside a sentence, by picking it from the toolbox or dragging it in, and is written as `[📋 name](boxganizer:uuid)` — an ordinary markdown link on a scheme that reaches nothing until the page decides that clicking it opens the box. Naming a box here files it here too, so a note cannot quietly lose what it points at; deleting the sentence does not throw the box out |
| checklist          | a list to tick. Type under the list and press **Enter** to add a line; click a square to tick it and the count at the top moves. A child box dragged onto a line hangs there as an icon — at the right end of the line, centred on it — and clicking the icon opens that box; lifting it off takes the link and leaves the box filed here. Anything filed in the box but on no line is listed underneath rather than hidden. What you type is written to a file of Boxganizer's own beside the database, in plain `- [ ]` markdown, and reindexed each time it changes, so the items are found by full-text search like the words in any other file |
| anything else      | its icon, and a detail panel with description, resource, metadata and config |

## How the page is put together

A left pane to search and pick — **Root** above it, the search field below, which
finds boxes by their own fields and by the words inside their text and PDF files,
showing the passage that answered and what your words were widened to — and a main
pane to show one box, **its content first**. **New box** opens a modal where you
choose the type (the list is searchable, because there are 39 of them) and configure
it. ☰ beside the name at the top of that panel is what belongs to the library rather than
to one box: **About**, **Check the library**, and the two index passes. Along the bottom,
in front of the box count, the index reports itself all the time — `75 indexed`, or `2 out
of date`, or `reading 12 of 80…` — with the detail on hover, including which files hold nothing to
read. **Saving a box does not wait for its file to be read**: the rows are written, the answer comes
back, and the reading happens behind that line with a bar that counts **files**, so you can see how
much of the folder you just pointed at has been gone through. Taking a box *out* of the index is
immediate — a stale document staying findable is a wrong answer, not a slow one. A file the index
refuses is named there rather than left as a stalled bar (ADR 32). A drop that no view claimed files the box into the box you are looking at, so the
whole pane takes it. The outline that says *drop here* is the page's to take away, not the
element's that drew it: a view that claims a drop stops the event, so the pane behind it is never
told, and one drag cancelled with Escape never fires a drop at all. Every ending clears every zone
(ADR 27), which is why a red outline no longer follows you into the next three boxes.

**Deleting a box is on its right-click menu** and asks twice: the item rewrites itself into
*Really delete — what was filed inside stays in the library*, and only the second click runs
(ADR 28). What survives is stated rather than guessed at. The links from the box go with it; every
box that was filed inside stays in the library, still filed wherever else it was; a file the box
pointed at is untouched; and the words a note or checklist wrote for itself stay in their file
beside the database, named in the sentence you get back (ADR 14). The **root** is never offered for
deletion, and the server refuses it in the same words if asked by another route: deleting it would
leave nowhere to file anything. `box rm` asks the same question and answers with the same facts.

What each kind of box draws is set out in [What each box shows](#what-each-box-shows) — the four rows worth knowing before you open anything: a **note** is a sheet you can switch between editing and reading, a **folder** is a list or a set of icons, a **grid** is rows and columns you size, and a **viewport** lends its space to other boxes. Anything with no view of its own shows its icon and its detail panel.

A box created **from** another box — "New box here" on a folder, "New box in X" in
a right-click menu, a cell of a grid — has that box already chosen as its parent,
and after creating it the view stays on the parent, because that is where the user
was looking. The parent is fetched by uuid if the left panel does not show it, so
this holds in a library bigger than one page and while a search has the panel
narrowed.

Display mode, list-or-icons, text appearance, the grid layout, the desktop surface
and the searches a folder has remembered are saved in the box's own `config`
column, so they come back tomorrow in another browser. A grid remembers cells by box **uuid**, and a desktop its icon
positions, so renaming a box does not scramble either; shrinking a grid leaves
children linked and shows them as "Not placed", and so does a desktop whose icons
nobody has placed yet.

Every sensor box — `sensor`, `mqtt`, `http`, `home-assistant` — turns what arrived into a value
using three templates the owner writes in the box's own settings drawer: *the value in what
arrived* (`{{ data.pressure }}`), *how the value is worded* (`{{ extracted }} {{ data.unit }}`), and
*how the words are dressed* (`<b class="{{ "sensor-bad" if number > 1.3 else "sensor-good" }}">`).
They are Jinja, run by the server in a sandbox with no loader, and the styling one is rebuilt
against an allow-list of formatting tags, page classes and CSS properties before it reaches the
page: `<script>`, `<img onerror>`, `href="javascript:"`, `onclick` and `position: fixed` do not
survive it. A template that fails says so beside the reading, which stays on screen. ADR 21.

When the value is a number, the box can also be given a **scale**: `min`, `max`, the range split
into colour intervals that must **fully cover it** — a stop short of `max` is refused, with the
reading that would fall through named — and one of four pictures: a gauge, a horizontal bar, a
vertical bar or a progression bar. Colours come from 16 presets served by the API, or from the
colour picker. A value outside the range is clamped to the end of the bar **and says so** (`above
10`), because a sensor pinned at the top of its scale otherwise looks exactly like a sensor at the
top of its range. For a value that stays text, the alternative to a styling template is a list of
regular expressions, each with a text and a background colour, first match wins, plus a default.
The server decides where the value sits; the page only paints it, so an opened box, a grid cell and
`box value` cannot disagree. ADR 22.

**View ports** are the way to look at two boxes at once. A viewport box is never replaced by what
you open in it: a *tiling* viewport splits its space into panes (split, drag the bar, close a pane
and its sibling takes the room back — the last pane refuses), a *windowed* one floats windows you
move, resize, raise and close — and a window can be minimized to its title, maximized over the whole
board, and restored to the rectangle it had, which is kept rather than recomputed. A window fades to
whatever the **transparency** slider says — **0 % is invisible, 100 % is solid**, and it starts at 0 —
while the focused one stays solid either way, so the desk can show through the pile without the box you
are reading going with it. The stored number is the same one an existing box already carried, so an old
library opens looking the way you left it. Every pane and window brings its own gear: a box's settings row folds
and unfolds inside the space it occupies, so changing a grid's columns does not unfold the checklist
beside it, and the gear in the page's top line folds only the box on the main screen. What a viewport
shows, it holds: those boxes are its children, so the tree view and the side list agree with the page.
Opening a box **inside a pane stays inside that pane**, however deep it is embedded: a folder in a grid
cell in a pane answers to that pane's path line, and the settings its own view writes land on the
folder — not on the grid, and not on the viewport around it. **Where you walked inside a pane is
remembered** through every operation on the viewport — a split, closing a neighbour, dragging the bar,
a drop, a reload — so a pane three folders deep stays three folders deep instead of snapping back to
the box the layout names (ADR 29). The walk lives in the browser tab and the layout lives in the
library, and they are not the same fact: what a space was *told* to show is shared, how you got there
is yours. So a second tab on the same library opens each pane at the box the layout names, and a pane
you were told to show something else in — a drop, another tab — starts a new walk from there. Dragging a box onto another box **copies the link**
(it is filed in both); **holding Shift moves it** (filed here, taken off where it came from, in one
request so it is never nowhere). ADR 23.

An MQTT box is the one kind that talks to something outside the machine: while
its page is open the server subscribes to `mqtt://` or `mqtts://host[:port]/topic`
and pushes every message to the page. `aiomqtt` ships with the install; if it is not there, the
box type says *"not in this install"* while you are still choosing it, the dialog names the module
and prints the command that repairs this particular install, and the opened box shows **"cannot
listen"** with that sentence instead of a message about a dropped stream — a box that cannot
work is refused, not retried forever. `box value NAME` reads one message and exits, for scripts,
and reports the same way. ADR 18.

`box serve` and `box tui` import their own framework when you ask for them, so `box --help`
and every filing command work even in an install whose web or text UI library has gone missing;
asking for one that cannot start prints the line that repairs it.

The database lives at `$BOXGANIZER_DB`, else
`$XDG_DATA_HOME/boxganizer/boxes.db`, else `~/.local/share/boxganizer/boxes.db`.
Point at another file anywhere with `--db path/to/boxes.db`.

Boxes whose words Boxganizer writes itself — a checklist — keep them next to that file, in
`files/`, one file per box named after the box's uuid. They are markdown, they are indexed
through a resource row like any other file, and they are not deleted when the box is: the
row can be re-created, the words cannot.

A **folder** marked for indexing is not read — there is nothing to read in a folder — it is
walked, and every text file inside it becomes a resource of its own: its own modification
time, its own entry in the index, its own answer to "has this changed". The folder keeps a
link to each one it brought in, so a file deleted from the folder stops answering searches,
and a file the library also holds directly is never discarded by the folder. Hidden entries,
symlinks and files nothing can read words out of are left alone, and a hit inside a folder
answers with the folder and names the file that spoke. ADR 16.
