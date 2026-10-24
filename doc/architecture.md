# Architecture

## Shape

```
                     ┌─────────────┐  ┌─────────────┐  ┌─────────────┐
   user ────────────►│     box     │  │  box serve  │  │   box tui   │
                     │    (cli)    │  │    (web)    │  │    (tui)    │
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
| `boxtypes/`     | The box type hierarchy and its registry; rules per type.           |
| `editing.py`    | A box row as YAML and back — pure text, no editor, no database.    |
| `bootstrap.py`  | The two boxes every library starts with (`root`, `cabinet`).       |
| `store.py`      | `BoxStore`: CRUD, search, links, cycle guard, schema, migrations.  |
| `config.py`     | Resolves the database path (argument → env → XDG → home).          |
| `errors.py`     | `BoxganizerError` and its subclasses.                             |
| `cli/main.py`   | Typer app `box`: `init`, `status`, `version`, `types`, `box add/list/show/search/rm`, `link`, `tree`, `serve`, `tui`. |
| `web/app.py`    | FastAPI factory `create_app()`: JSON API + HTML page.              |
| `web/server.py` | `run()`: builds the app and hands it to uvicorn.                    |
| `tui/app.py`    | Textual `BoxganizerApp` and the `NewBoxScreen` modal.              |

## Identity and names

A box is identified by its name, compared case-insensitively. `models.canonical_name`
strips and case-folds; the store keeps that value in a `key` column with a unique
index, so `Kitchen` and `kitchen` cannot both exist, and no locale-dependent SQL
`lower()` is involved.

## Data model

```
boxes (id, name, key UNIQUE, description, icon, box_type, resource_uri,
       metadata JSON, config JSON, created_at, updated_at)
links (parent_id → boxes.id, child_id → boxes.id, added_at)   PRIMARY KEY (parent_id, child_id)
```

Two JSON columns: `metadata` is a flat map of strings the user labels freely;
`config` holds arbitrary nested settings belonging to the box type.

`box_type` is the key of a class in `boxganizer.boxtypes`; see
`doc/box-types.md`. Schema version 2 added that column and version 3 added
`config`. Migrations are a list of `(version, column, definition)` triples
applied when an older file is opened, so upgrading is one code path and one test
per version.

One box may sit in many boxes, so the graph is a DAG, not a tree: `links` is a
join table, `ON DELETE CASCADE` removes the edges of a deleted box, and
`BoxStore.link` refuses an edge that would make a box reach itself
(`CyclicLink`), checked with a recursive CTE before the insert.

Timestamps are stored as ISO-8601 strings in UTC. Metadata is a JSON object of
string values; unknown shapes decode to an empty object rather than raising.

## Editing a box in place

`box edit` is the one place a whole row is rewritten by hand. It is split so the
risky half can be tested: `boxganizer.editing` turns a `Box` into a YAML document
and back with no editor and no database, while the CLI owns the temporary file
and `$EDITOR`. `metadata` and `config` are JSON in SQLite and YAML in the file —
`load()` converts them back and refuses anything JSON cannot hold, including the
bare dates YAML likes to invent. `id`, `created_at` and `updated_at` are shown
and refused if edited; links stay out of the file, because `box link` owns them.
A failing editor, unparsable text or a changed identity leaves the row untouched.

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
| `UnknownBoxType`   | message, exit 1| 422               | (reads tolerate it)  |
| `InvalidBoxContent`| message, exit 1| 422               | detail pane, red     |
| `ChildrenNotAllowed`| message, exit 1| 409              | detail pane, red     |
| `EditError`        | message, exit 1| n/a               | n/a                |
| `MissingDependency`| install hint, exit 1 | n/a         | n/a                |

`box serve` and `box tui` import their framework inside the command, so a
missing extra reaches the user as `MissingDependency` instead of an import
traceback.

A frontend never translates an error into another frontend's vocabulary, and
never swallows one silently.

## Extension points

* **New frontend** — import `BoxStore`, add a console script, no core change.
* **Box types** — done: a class registry (`boxganizer.boxtypes`) checked in
  `BoxStore.add` and `update`, with `tree()` and `describe()` feeding the CLI,
  the API and the docs. Behaviour per type is what still has to be written.
* **Full-text search** — swap the `LIKE` query in `store._SEARCH_SQL` for an
  FTS5 index; keep the `search()` signature, and the three frontends keep working.
* **Another database** — everything SQL is in `store.py`; the rest of the core is
  storage-agnostic.
