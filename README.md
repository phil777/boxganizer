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
box link in "Passport" "Kitchen"               # put a box inside another box
box box list
box box show Kitchen
box box search cooking
box tree
box types                                      # the box type hierarchy
```

`box init` leaves two boxes behind: **root** (type `root`, the top of the graph)
and **cabinet** (type `cabinet`) filed inside it. New boxes go into `root` unless
`--parent` says otherwise or `--no-parent` opts out. Running `init` again adds
nothing.

`box edit` writes the whole row to a temporary YAML file, opens `$EDITOR` (or
`--editor "nano"`), then reads it back and saves. `metadata` and `config` are
stored as JSON and shown as YAML; `id` and the timestamps are shown but refused
if changed; links are edited with `box link`, not here. Quitting without saving
changes nothing.

Every box declares a type — `box add -t markdown`, `-t mqtt`, `-t mindmap` —
out of a hierarchy of 40 kinds documented in [doc/box-types.md](doc/box-types.md).

```bash
box serve --port 8000                        # API on /api, docs on /docs, page on /
box tui                                      # browse, filter (type), drill in (enter), new (n)
```

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

The 40 box types are **declared, not built**: they enforce their rules, but none
of them fetches, renders, indexes or lays anything out yet — that is what the
`planned` line of each type describes. Also not built: import/export, full-text
search, authentication, and multi-user access.

See [doc/architecture.md](doc/architecture.md) for the design and
[doc/development.md](doc/development.md) for the workflow.

## License

GPLv2 — see [LICENSE](LICENSE).
