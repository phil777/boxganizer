# Architecture

## Shape

```
                     ┌─────────────┐  ┌─────────────┐  ┌─────────────┐
   user ────────────►│  boxganizer │  │ boxganizer- │  │ boxganizer- │
                     │     cli     │  │     web     │  │     tui     │
                     └──────┬──────┘  └──────┬──────┘  └──────┬──────┘
                            │                │                │
                            └────────┬───────┴────────────────┘
                                     ▼
                            ┌──────────────────┐
                            │  core library    │
                            │  models / store  │
                            │  config / errors │
                            └────────┬─────────┘
                                     ▼
                            ┌──────────────────┐
                            │  SQLite file     │
                            └──────────────────┘
```

Three rules hold this together:

1. **The core owns the logic.** Name rules, cycle detection, search, the schema:
   all of it lives in `boxganizer.models` and `boxganizer.store`.
2. **A frontend only calls `BoxStore`.** It parses input, calls one method, and
   renders the result. If a frontend needs an `if` that decides something about
   boxes, that decision belongs in the core.
3. **Frontends do not import each other.** The web app never imports the CLI and
   vice versa. This keeps each optional extra truly optional.

## Modules

| Module          | Responsibility                                                     |
| --------------- | ------------------------------------------------------------------ |
| `models.py`     | `Box` dataclass; name validation; `key` identity; JSON view.       |
| `store.py`      | `BoxStore`: CRUD, search, links, cycle guard, schema, migrations.  |
| `config.py`     | Resolves the database path (argument → env → XDG → home).          |
| `errors.py`     | `BoxganizerError` and its subclasses.                             |
| `cli/main.py`   | Typer app: `init`, `status`, `box add/list/show/search/rm`, `link`, `tree`. |
| `web/app.py`    | FastAPI factory `create_app()`: JSON API + HTML page.              |
| `web/server.py` | uvicorn entry point.                                               |
| `tui/app.py`    | Textual `BoxganizerApp` and the `NewBoxScreen` modal.              |

## Identity and names

A box is identified by its name, compared case-insensitively. `models.canonical_name`
strips and case-folds; the store keeps that value in a `key` column with a unique
index, so `Kitchen` and `kitchen` cannot both exist, and no locale-dependent SQL
`lower()` is involved.

## Data model

```
boxes (id, name, key UNIQUE, description, icon, resource_uri, metadata JSON,
       created_at, updated_at)
links (parent_id → boxes.id, child_id → boxes.id, added_at)   PRIMARY KEY (parent_id, child_id)
```

One box may sit in many boxes, so the graph is a DAG, not a tree: `links` is a
join table, `ON DELETE CASCADE` removes the edges of a deleted box, and
`BoxStore.link` refuses an edge that would make a box reach itself
(`CyclicLink`), checked with a recursive CTE before the insert.

Timestamps are stored as ISO-8601 strings in UTC. Metadata is a JSON object of
string values; unknown shapes decode to an empty object rather than raising.

## Concurrency

`BoxStore` keeps one SQLite connection with `check_same_thread=False` and guards
every read and write with a `threading.RLock`. That is what lets FastAPI serve
requests from its thread pool. Writes commit per call; there is no long-running
transaction, so two processes writing at once surface as `StoreError` rather than
silently interleaving. Cross-process concurrency is a known limit (see ADR-0002).

## Error flow

The core raises `BoxganizerError` subclasses. Each frontend maps them once:

| Error              | CLI            | Web               | Text UI              |
| ------------------ | -------------- | ----------------- | -------------------- |
| `BoxNotFound`      | message, exit 1| 404               | detail pane, red     |
| `DuplicateBoxName` | message, exit 1| 409               | detail pane, red     |
| `InvalidBoxName`   | message, exit 1| 422               | detail pane, red     |
| `CyclicLink`       | message, exit 1| 409               | detail pane, red     |

A frontend never translates an error into another frontend's vocabulary, and
never swallows one silently.

## Extension points

* **New frontend** — import `BoxStore`, add a console script, no core change.
* **Box types and validation** — a registry next to `models.py`, checked in
  `BoxStore.add`; the frontends pick the available types up from one function.
* **Full-text search** — swap the `LIKE` query in `store._SEARCH_SQL` for an
  FTS5 index; keep the `search()` signature, and the three frontends keep working.
* **Another database** — everything SQL is in `store.py`; the rest of the core is
  storage-agnostic.
