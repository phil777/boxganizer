# Boxganizer

Personal knowledge management where **everything is a box**.

A box holds one thing — a file, a note, a photo, a passport — and any number of
other boxes. Boxes inside boxes give you a graph you can navigate, search and
reshape. One store, three ways in:

| Interface   | Command            | Package                      |
| ----------- | ------------------ | ---------------------------- |
| CLI         | `boxganizer`       | `boxganizer.cli`             |
| Web app     | `boxganizer-web`   | `boxganizer.web`             |
| Text UI     | `boxganizer-tui`   | `boxganizer.tui`             |

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
boxganizer init                              # create the database
boxganizer box add "Kitchen" -d "Room for cooking" -m floor=0
boxganizer box add "Passport" -i 🛂 -d "Expires 2031"
boxganizer link in "Passport" "Kitchen"      # put a box inside another box
boxganizer box list
boxganizer box show Kitchen
boxganizer box search cooking
boxganizer tree
```

```bash
boxganizer-web --port 8000                   # API on /api, docs on /docs, page on /
boxganizer-tui                               # browse, filter (type), drill in (enter), new (n)
```

The database lives at `$BOXGANIZER_DB`, else
`$XDG_DATA_HOME/boxganizer/boxes.db`, else `~/.local/share/boxganizer/boxes.db`.
Point at another file anywhere with `--db path/to/boxes.db`.

## Layout

```
src/boxganizer/
├── models.py      # the Box type and its name rules
├── store.py       # SQLite persistence, the only module that speaks SQL
├── config.py      # where the database lives
├── errors.py      # one exception hierarchy for all frontends
├── cli/           # typer command line
├── web/           # FastAPI app, JSON API + HTML page
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
make run-cli / run-web / run-tui
```

## Status

Early development. The core store (boxes, links, search), the CLI, the web API
with a minimal HTML browser, and the text UI all work and are covered by tests.
Not built yet: import/export, box types with validation, full-text search,
authentication, and multi-user access.

See [doc/architecture.md](doc/architecture.md) for the design and
[doc/development.md](doc/development.md) for the workflow.

## License

GPLv2 — see [LICENSE](LICENSE).
