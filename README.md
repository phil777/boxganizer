# Boxganizer

Personal knowledge management where **everything is a box**.

A box holds one thing — a file, a note, a photo, a passport — and any number of
other boxes. Boxes inside boxes give you a graph you can navigate, search and
reshape. One store, three ways in:

Everything runs through one command, `box`:

| Interface   | Command        | Package          |
| ----------- | -------------- | ---------------- |
| CLI         | `box`          | `boxganizer.cli` |
| Web app     | `box serve`    | `boxganizer.web` |
| Text UI     | `box tui`      | `boxganizer.tui` |

All three read and write the **same** SQLite database through the same core
library, so anything you add in one shows up in the others.

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"          # core + CLI + web + text UI + tooling
```

Extras on their own: `pip install -e ".[web]"` or `pip install -e ".[tui]"`.

Installing `box` as a command, the way you would install any tool — `pipx install boxganizer`
or `uv tool install boxganizer` — puts it in an environment of its own, where plain `pip
install` of an extra would land in a Python the tool never imports. That is why the message a
missing extra produces is generated from the running process rather than copied out of this
file: a pipx install is told `pipx inject boxganizer aiomqtt`, and it is the right command.

## Use

```bash
box init                                     # create the database: root + cabinet
box add "Kitchen" -d "Room for cooking" -m floor=0
box add "Passport" -i 🛂 -d "Expires 2031" --parent Kitchen
box add "Loose" --no-parent                    # keep it out of the tree
box edit Kitchen                               # edit the row as YAML in $EDITOR
box add "Report" -r report.pdf --infer         # take the type the resource suggests
box link "Passport" "Kitchen"                  # put a box inside another box
box unlink "Passport" "Kitchen"                # take it out again (the box stays)
box link "Kitchen" root                        # take the root's slot from cabinet
box ls                                         # same command as `box list`
box list
box show Kitchen
box search cooking                           # names, fields, and what the files say
box search "basement NOT pump"               # quotes and and/or/not are understood
box index                                    # read every text and PDF file again
box ssh Build "df -h"                          # one command on the machine an ssh box points at
box tree                                       # starts at the root box
box tree Kitchen                               # or wherever you say
box types                                      # the box type hierarchy
```

`box init` leaves two boxes behind: **root** (type `root`), the way in, and
**cabinet** (type `cabinet`) in the root's single child slot. New boxes get **no
parent** unless you pass `--parent NAME` — the root holds exactly one box, so
filing every new box there would keep evicting the previous one. Running `init`
again adds nothing.

Every command is one word after `box`. `box link CHILD PARENT` files the box named
first inside the one named second, and `box ls` is `box list` said shorter.

`box search` looks in two places. First the box itself — name, description, resource,
metadata. Then, for boxes that hold a **text**, **markdown** or **PDF** file, inside
that file, and it answers with the words that matched:

```
$ box search basement
🔤 Pump log  [in the file: [Cellar] pump service log … The sump under the kitchen]
  searched: basement; also: basement -> cellar/vault
```

The second line is the point of the whole thing: the file never said "basement". Three
things widen a query, and all three are reported rather than done quietly —

| Widening | Where it comes from | Example |
| -------- | ------------------- | ------- |
| Stems | SQLite's porter tokenizer, at index time and again on the words you type | "paint" finds "painted" |
| Synonyms | a list in the code, plus your own `thesaurus.json` | "cellar" finds "basement" |
| Near-miss spellings | compared only against words your library already contains | "cellrr" finds "cellar" |

A name is never widened — a box called *Cellar Plans* should not turn up just because
you asked for a basement. Nothing outside the content confine is read (see
`BOXGANIZER_CONTENT_ROOT` below), files over 2 MB are left alone as are the last pages
of a PDF past page 200, and a scan with no text layer is reported as such.

`thesaurus.json` sits next to the database and is plain JSON, a list of lists or a
word-to-synonyms object, and is merged with the words that ship with Boxganizer:

```json
{"annex": ["outbuilding", "wing"], "soho": ["london office"]}
```

The index lives in the database while the files do not, so three ordinary things make
them disagree: a library copied to another machine, a database written before the index
existed, and a file edited outside Boxganizer.

```bash
box index                                    # read everything wanted again
box index --check                            # only look: which files changed since
box index --stale                            # read those alone
box index --add Ledger-csv                   # this file, whatever its type says
box index --drop Huge-log                    # and not this one
box check                                    # every box's resource: still there, still right
box check --fix-stale                        # and read again whatever has changed
```

`box check` is the one command that asks whether the library's promises still hold: is
each box's file where it said, of the kind the type expects, inside the content confine,
and up to date in the index? It also names the files Boxganizer wrote for boxes that no
longer exist (ADR 14 keeps them on purpose) and the notes that link to a box that is not
here. Every finding prints the command that repairs it, unwrapped so it can be copied, and
nothing is changed unless asked: a check that "helped" by guessing where a moved file went
is a tool that files your documents in the wrong places. The same findings, and the same
repairs as buttons, are the page's **Check the library** action.

The dialog changes with the type, because it is not asking the same question of a note, a
topic and a host. An address whose *path* means something shows an example while you type
(`mqtt://host:1883/boiler/pressure`), a type that genuinely needs a piece of metadata gets
a field for it and one that merely honours it gets a field marked *optional* — metadata a
type will use but never ask for is a feature that exists only in the documentation — and
Browse and the index tick disappear when the resource is not a file on this machine — a control that will be ignored is a control that lies about what it is
asking. A refusal is written **inside the dialog**, in its own line, because the status bar
behind an open modal is a message nobody receives.

A box is also **about** things, and that is what tags are for. Type a word into the tag field
under the top line — or a path like `home/kitchen/boiler`, which creates the branch — and the box
gets a coloured chip. A tag inherits the ones above it, so a box tagged `boiler` answers to
`kitchen` and `home` too; those chips are drawn dashed and without a cross, because removing one
means moving the tag in ☰ **Manage tags…**, not editing the box. Colours are derived from a tag's
place in the hierarchy — one hue per branch, lighter as it goes down — so the tree is visible
without reading it. ADR 19.

```bash
box tag add home/kitchen/boiler        # a path creates the parents it names
box tag give Rates boiler
box ls --tag home                      # the branch too, not only the word
box tag ls                             # the tree, with what each tag holds
```

Right-clicking anywhere in a box's pane brings up that box's menu — including the empty part of
a folder, the strip below a short list, or a background. A view that means something particular
by the right-click keeps its own; highlighted text keeps the browser's menu, because that click
was for copy.

Every box's own controls — a folder's search, a desktop's grid size, a note's format bar, a
sensor's address — live in a row that is **hidden until you ask**. A ⚙ at the right end of the
top line opens it, and the box's content gives up the room: the drawer pushes the pane down
rather than lying over it. A box with no controls shows no gear, because a control with nothing
to control is decoration. The choice is remembered in the browser, not in the library: it is
about how you like to work, and a library should not change shape because you opened a folder
once.

A note or checklist you save is re-read into the search index **by the save itself** — no index
pass, no button: a word you just typed answers a search, and a word you deleted stops answering.

What is indexed is decided per **resource**, not per box, and the dialog says so as you
fill it in — a tick box, with the reason beside it:

> ☑ Index content
> *a file whose ending says words*

The default comes from the core (a text or PDF box, or a local file whose ending says
words: yes; a picture, a web page, an MQTT topic: no), and the tick is yours to
overrule. `box show NAME` then tells you where the index stands — in it since when,
`CHANGED since it was indexed`, or `left out of the search` — which is the difference
between a box that is not in the results and a search that is not working.

Two boxes pointing at one file share one resource row, so the file is read, hashed and
watched once, and `box search` offers both boxes with the same passage. The address is
normalised before it is compared, so `/a/../a.txt`, `./a.txt` written from `/a` and
`file:///a/a.txt` are one thing. Deleting a box lets its resource go; the row and its
indexed text are dropped only when nothing points at the file any more — and the file
itself is nobody's to delete.

`box edit` writes the whole row to a temporary YAML file, opens `$EDITOR` (or
`--editor "nano"`), then reads it back and saves. `metadata` and `config` are
stored as JSON and shown as YAML; `id` and the timestamps are shown but refused
if changed; links are edited with `box link`, not here. Quitting without saving
changes nothing.

Every box declares a type — `box add -t markdown`, `-t mqtt`, `-t mindmap` —
out of a hierarchy of 41 kinds documented in [doc/box-types.md](doc/box-types.md).

```bash
box serve --port 8000                        # UI on /, API on /api, docs on /docs
box tui                                      # browse, filter (type), drill in (enter), new (n)
```

### The web app

A left pane to search and pick — **Root** above it, the search field below, which
finds boxes by their own fields and by the words inside their text and PDF files,
showing the passage that answered and what your words were widened to — and a main
pane to show one box, **its content first**. **New box** opens a modal where you
choose the type (the list is searchable, because there are 44 of them) and configure
it. ☰ beside the name at the top of that panel is what belongs to the library rather than
to one box: **About**, **Check the library**, and the two index passes. Along the bottom,
in front of the box count, the index reports itself all the time — `75 indexed`, or `2 out
of date`, or `indexing…` — with the detail on hover, including which files hold nothing to
read. A drop that no view claimed files the box into the box you are looking at, so the
whole pane takes it. What a box shows depends on what it is:

| Box                | Content view                                              |
| ------------------ | --------------------------------------------------------- |
| image              | the picture, scaled into the space left, ratio untouched   |
| text, markdown     | the file's text; wrap, crop and font size in the right-click menu; vertical scroll always, horizontal when wrap is off. A **markdown** file is rendered instead — headings, emphasis, lists that nest, tables that line up, quotes, fenced code, links — with the plain text one click away and that choice kept in the box. Raw HTML in the file stays text on the screen, and a link whose address would run something keeps its words and loses its reach |
| folder, directory, cabinet | its children, as a list or as icons; double-click opens one in place; a search box narrows the list by name or type — it starts empty every time, while the searches you have tried are remembered in the box. A **directory** box shows the top of its folder whenever it is opened, however deep the last browse went; a file in it can be dragged out — onto another box to file a new box inside it, or onto the library list to leave it unfiled — with the type inferred from the file |
| grid               | rows and columns you set (type them or step them), one child per cell, drag to move, drop on an occupied cell to swap, right-click to create a box in a cell |
| desktop            | icons anywhere on the surface: drag them with the pointer, drag a corner to size them, arrow-key them about, and let a magnetic grid of the size you chose pull them onto its lines. The resource, when it is a picture, is the wallpaper — right-click the bare surface to choose one or take it away. Double-click opens a box in place of the desktop. Shown in a smaller pane it is the whole desktop drawn smaller, not a corner of it: canvas, wallpaper and icons scale by one factor, so an icon stays on the same feature of the picture |
| dynamic-folder     | the folder list; above it the filter as one expression, a row per clause to add, remove, reorder or negate, what the filter asks for in words, and a button to re-apply it |
| mindmap            | the box in a rounded node at the centre, its children as branches growing left and right, each branch curved to its parent in its own colour. Click into it and the keys drive it: **Insert** a node under the one in hand, **Enter** beside it, **arrows** to walk the map, **Ctrl+↑↓** to shuffle a node among its siblings, **Ctrl+←→** to push it deeper into the map or pull it out towards the centre — read off the side the branch grows on, so Ctrl always means away from the middle or towards it, and a branch of the centre changes side instead of rising above it. Double-click, or **F2**, writes the node's name where it stands. A box dragged onto a node that nothing has been written into takes that node's place; onto any other node it is filed inside |
| browser, web       | the page, framed; the address bar saves back to the box |
| mqtt               | the last message on its topic, and every one after it, as the broker sends them — worded and dressed by the box's own templates, with the raw payload in the tooltip. The address is shown with its password hidden, which the box keeps for itself. In a grid cell it is the last value alone: the cell does not listen, the opened box does |
| vnc                | another machine's screen. The panel first asks what is there — the server's name, the protocol it speaks, how big its screen is — and draws nothing until you press **Show the screen**. From then the machine sends a picture when its screen changes and nothing when it does not; your pointer and keystrokes go back to it, mapped through whatever size the pane drew the screen at. **A server that wants a password is asked for one**, in a field that appears when the screen asks and never otherwise; the answer is used for that connection and never written to the library — which is a file that gets copied — though you may point the box at a file that holds it (`password_file`, outside the library, readable only by you). ADRs 15 and 17 |
| ssh                | a console on another machine: the address it goes to, the line to type for a session of your own, a field for one command at a time, and the transcript of what was asked and what answered, exit code and all. Up and down arrows walk what was run here, kept in the box. **There is no password field, and none can be stored** — the type refuses an address carrying one; keys, an agent and `~/.ssh/config` decide who you are. It is a console and not a terminal emulator, and says so on the panel: one command, one answer, no full-screen programs |
| note               | a post-it with a place in the graph: **Markdown** or **Plain text**, and — when markdown — one **Write / Read** button (its label is what the click gets you). The note itself says which it is, with a small `read-only` or `editing` cue on the sheet, because that control lives in the settings drawer and the drawer spends its life closed. Both kept in the box. The toolbox writes markdown markers into the text and nothing else: `**` around a selection, a `#` or a `-` at the start of every line the selection touches. A box can be named inside a sentence, by picking it from the toolbox or dragging it in, and is written as `[📋 name](boxganizer:uuid)` — an ordinary markdown link on a scheme that reaches nothing until the page decides that clicking it opens the box. Naming a box here files it here too, so a note cannot quietly lose what it points at; deleting the sentence does not throw the box out |
| checklist          | a list to tick. Type under the list and press **Enter** to add a line; click a square to tick it and the count at the top moves. A child box dragged onto a line hangs there as an icon — at the right end of the line, centred on it — and clicking the icon opens that box; lifting it off takes the link and leaves the box filed here. Anything filed in the box but on no line is listed underneath rather than hidden. What you type is written to a file of Boxganizer's own beside the database, in plain `- [ ]` markdown, and reindexed each time it changes, so the items are found by full-text search like the words in any other file |
| anything else      | its icon, and a detail panel with description, resource, metadata and config |

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

An MQTT box is the one kind that talks to something outside the machine: while
its page is open the server subscribes to `mqtt://` or `mqtts://host[:port]/topic`
and pushes every message to the page. That needs the `live` extra. Without it, the box type
says *"not in this install"* while you are still choosing it, the dialog names the module and
prints the command that fixes this particular install, and the opened box shows **"cannot
listen"** with that sentence instead of a message about a dropped stream — a box that cannot
work is refused, not retried forever. `box value NAME` reads one message and exits, for scripts,
and reports the same way. ADR 18.

`box serve` and `box tui` import their own framework, so `box --help` works
even when only the core is installed; asking for one without its extra prints
the `pip install` line for it.

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

## Layout

```
src/boxganizer/
├── models.py      # the Box type and its name rules
├── store.py       # SQLite persistence, the only module that speaks SQL
├── boxtypes/      # the type hierarchy and its rules (see doc/box-types.md)
├── editing.py     # a box row as YAML and back, for `box edit`
├── bootstrap.py   # the two boxes every library starts with
├── config.py      # where the database lives
├── errors.py      # one exception hierarchy for all frontends
├── cli/           # the `box` command line, including serve and tui
├── web/           # FastAPI app, JSON API + HTML page, uvicorn runner
└── tui/           # Textual terminal browser
tests/             # pytest suites, one per layer
doc/               # architecture, development guide, ADRs
```

Frontends never touch the database directly and never import each other. All
logic lives in the core, which is why the three interfaces behave identically.

## Develop

```bash
make install      # create .venv and install the project with dev extras
make test         # pytest with coverage
make lint         # ruff check
make format       # ruff format
make typecheck    # mypy
make check        # lint + typecheck + test
make run-cli          # box <args>, e.g. make run-cli ARGS="box list"
make run-web          # box serve
make run-tui          # box tui
```

## Status

Early development. The core store (boxes, links, search), the box type registry
with its validation, the CLI, the web API with a minimal HTML browser, and the
text UI all work and are covered by tests.

The page opens on the **root box**, not on whatever the list happens to start with:
the root is asked of the server (`GET /api/root`), which finds it by its type
however it is named. The left panel is one page of the library sorted by name, so
digging for the root there lands on the first alphabetical box once the library
outgrows the page. A library with no root box says so and points at `box init`.

The root box is never an icon of what is filed in it: it shows that box the way
opening it would, toolbars and all, and one box at a time — dropping another takes
the slot, and the box it displaces stays in the library. Dropping something on a
view that already caught the drop (an icon onto a desktop, a box into a folder) is
not the root's to answer.

In the web app these kinds render as themselves: image, text, markdown, pdf,
video, archive, file, folder, dynamic-folder, cabinet, directory, grid, desktop,
mindmap, ssh, root, web, browser and mqtt. The rest still show their icon and their
detail panel.

A **dynamic folder** holds whatever a filter over all the boxes selects, and the
filter lives in the box's own `config.filter` as one expression — `type`, `name`, `tag`,
`content` and `resource`, each plain text or a regular expression, combined with `and`, `or`
and `not`: `type:note and tag:home and not name:"weekly log"`. The filter bar edits that same
expression, one row per clause; `box edit` and a raw config write edit the text directly. Membership is written into the links
table rather than computed on read, so `box show`, `box tree` and every box's `in`
list see a dynamic container exactly as they see one filled by hand. The store
keeps it true: a box created joins the folders that want it, a box renamed or
retyped leaves the ones it no longer fits, and editing the filter re-cuts the whole
membership — through the page, `box edit` or a raw config write, since all three go
through the same door. A filter that cannot be read selects **nothing** and says
why; reading it as "empty" would mean the whole library.

The **cabinet** is the dynamic folder that filters nothing: it holds every box, and
a cabinet is a box, so it holds itself. The root is the one exception — nothing
files the root, which is what keeps the way in open.

Most box types are **declared, not built**: they enforce their rules, but do not
fetch, index or talk to anything outside the machine — that is what the `planned`
line of each type describes. MQTT is the first that does, and only while a page
shows the box: a subscription nobody is watching is still `planned`, as is keeping
a value across a restart. Also not built: import/export, authentication, and
multi-user access.

See [doc/architecture.md](doc/architecture.md) for the design and
[doc/development.md](doc/development.md) for the workflow.

## License

GPLv2 — see [LICENSE](LICENSE).
