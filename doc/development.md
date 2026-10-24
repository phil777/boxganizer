# Development guide

## First run

```bash
python -m venv .venv
source .venv/bin/activate
make install          # pip install -e ".[dev]"
make check            # lint + typecheck + tests
```

Python 3.11 or newer. The project is packaged with setuptools and the `src/`
layout, so run the tools from the repository root and import `boxganizer` —
never `src.boxganizer`.

## Commands

| Task                  | Command                                                  |
| --------------------- | -------------------------------------------------------- |
| Format code           | `make format`                                            |
| Lint                  | `make lint`                                              |
| Type check            | `make typecheck`                                         |
| Run tests             | `make test`                                              |
| One test file         | `.venv/bin/pytest tests/test_store.py -q`                |
| One test              | `.venv/bin/pytest tests/test_store.py::test_add_then_get -q` |
| Everything at once    | `make check`                                             |
| CLI smoke             | `make run-cli ARGS="box list"` or `box --help`                           |
| Web app               | `box serve` — `make run-web` (open http://127.0.0.1:8000) |
| Text UI               | `box tui` — `make run-tui`                                 |

## Testing rules

* Every behaviour in the core gets a test in `tests/test_store.py` or
  `tests/test_models.py` — the core is where logic lives, so that is where
  coverage matters most.
* Frontend tests drive the real thing: `typer.testing.CliRunner` for the CLI,
  `fastapi.testclient.TestClient` for the web app, `App.run_test()` with a pilot
  for the text UI. No mocking of `BoxStore`.
* Tests never touch the developer's database. `conftest.py` gives each test a
  `tmp_path` database; keep it that way.
* Web and text UI tests call `pytest.importorskip`, so a missing optional extra
  skips the suite instead of failing it.
* `filterwarnings = ["error"]` is set in `pyproject.toml`: a new deprecation
  warning breaks the build on purpose.

## Style

* Line length 100, `ruff format` is the formatter; `ruff check` and `mypy
  strict` must both pass before you commit.
* Annotate everything. `BoxStore` methods return `Box` or plain data, never
  `sqlite3.Row`.
* Public functions carry a one-line docstring saying what they do, not how.
* Module docstrings explain the *why* of a file when the name is not enough.
* Prefer a named constant over a magic string repeated in two places — except
  SQL, which stays inline and readable.

## Adding a box type

Types are classes in `src/boxganizer/boxtypes/`, one module per branch of the
hierarchy. `doc/box-types.md` explains the attributes and the rules they carry.
The short version:

1. Subclass the right parent, declare `key`, `label`, `icon`, `summary` and the
   rules the type needs.
2. Re-export it from `boxtypes/__init__.py`.
3. Add it to `DOCUMENTED` in `tests/test_boxtypes.py` — that table is the
   contract with the source document, and the suite fails without it.

The store validates on write and the interfaces list types from the registry, so
a new type is usable the moment it imports. Never branch on `box_type` in a
frontend: ask the type.

## Adding a feature, end to end

1. Write the behaviour in the core, with a failing test first.
2. Expose it in the CLI (`src/boxganizer/cli/main.py`).
3. Expose it in the web API (`src/boxganizer/web/app.py`) and, when it is worth
   clicking, in the HTML page (`web/templates/index.html`).
4. Expose it in the text UI (`src/boxganizer/tui/app.py`), usually one binding.
5. Add the test for each frontend, run `make check`, update the docs.

A feature that exists in one frontend only is fine — say so in the README
status table, not in a code comment.

## Data and local files

The default database is `~/.local/share/boxganizer/boxes.db`. For experiments,
use a scratch file:

```bash
export BOXGANIZER_DB=$PWD/data/scratch.db   # data/ is git-ignored
box init
```

## Committing

Conventional Commits subjects (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`,
`chore:`), imperative and short. The body explains why, not what — the diff
already shows what. CI runs lint, type check and tests on every push and pull
request (`.github/workflows/ci.yml`); keep it green on `main`.
