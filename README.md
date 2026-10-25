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

## Use

```bash
box init                                     # create the database: root + cabinet
box box add "Kitchen" -d "Room for cooking" -m floor=0
box box add "Passport" -i 🛂 -d "Expires 2031" --parent Kitchen
box box add "Loose" --no-parent                # keep it out of the tree
box box edit Kitchen                           # edit the row as YAML in $EDITOR
box box add "Report" -r report.pdf --infer     # take the type the resource suggests
box link in "Passport" "Kitchen"               # put a box inside another box
box link in "Kitchen" root                     # take the root's slot from cabinet
box box list
box box show Kitchen
box box search cooking
box tree
box types                                      # the box type hierarchy
```

`box init` leaves two boxes behind: **root** (type `root`), the way in, and
**cabinet** (type `cabinet`) in the root's single child slot. New boxes get **no
parent** unless you pass `--parent NAME` — the root holds exactly one box, so
filing every new box there would keep evicting the previous one. Running `init`
again adds nothing.

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

A left pane to search and pick (with a **Root** button above it), a main pane to
show one box — **its content first**, its icon as the alternative. **New box** opens a modal where you choose the type and configure
it. What a box shows depends on what it is:

| Box                | Content view                                              |
| ------------------ | --------------------------------------------------------- |
| image              | the picture, scaled into the space left, ratio untouched   |
| text, markdown     | the file's text; wrap, crop and font size in the right-click menu; vertical scroll always, horizontal when wrap is off |
| folder, directory, cabinet | its children, as a list or as icons; double-click opens one in place |
| grid               | rows and columns you set (type them or step them), one child per cell, drag to move, drop on an occupied cell to swap, right-click to create a box in a cell |
| desktop            | icons anywhere on the surface: drag them with the pointer, drag a corner to size them, arrow-key them about, and let a magnetic grid of the size you chose pull them onto its lines. The resource, when it is a picture, is the wallpaper. Double-click opens a box in place of the desktop |
| browser, web       | the page, framed; the address bar saves back to the box |
| mqtt               | the last message on its topic, and every one after it, as the broker sends them. The address is shown with its password hidden, which the box keeps for itself. In a grid cell it is the last value alone: the cell does not listen, the opened box does |
| anything else      | its icon, and a detail panel with description, resource, metadata and config |

Display mode, list-or-icons, text appearance, the grid layout and the desktop
surface are saved in the box's own `config` column, so they come back tomorrow in
another browser. A grid remembers cells by box **uuid**, and a desktop its icon
positions, so renaming a box does not scramble either; shrinking a grid leaves
children linked and shows them as "Not placed", and so does a desktop whose icons
nobody has placed yet.

An MQTT box is the one kind that talks to something outside the machine: while
its page is open the server subscribes to `mqtt://` or `mqtts://host[:port]/topic`
and pushes every message to the page. That needs the `live` extra —
`pip install 'boxganizer[live]'` — and without it the box says so instead of
failing silently. `box box value NAME` reads one message and exits, for scripts.

`box serve` and `box tui` import their own framework, so `box --help` works
even when only the core is installed; asking for one without its extra prints
the `pip install` line for it.

The database lives at `$BOXGANIZER_DB`, else
`$XDG_DATA_HOME/boxganizer/boxes.db`, else `~/.local/share/boxganizer/boxes.db`.
Point at another file anywhere with `--db path/to/boxes.db`.

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

In the web app these kinds render as themselves: image, text, markdown, pdf,
video, archive, file, folder, cabinet, directory, grid, desktop, root, web,
browser and mqtt. The rest still show their icon and their detail panel.

Most box types are **declared, not built**: they enforce their rules, but do not
fetch, index or talk to anything outside the machine — that is what the `planned`
line of each type describes. MQTT is the first that does, and only while a page
shows the box: a subscription nobody is watching is still `planned`, as is keeping
a value across a restart. Also not built: import/export, full-text search,
authentication, and multi-user access.

See [doc/architecture.md](doc/architecture.md) for the design and
[doc/development.md](doc/development.md) for the workflow.

## License

GPLv2 — see [LICENSE](LICENSE).
