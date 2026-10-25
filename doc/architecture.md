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

1. **The core owns the logic.** Name rules, link rules, search, the schema:
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
| `grid.py`       | Grid placement rules: swap, displace, unplace on shrink, clamp.     |
| `bootstrap.py`  | The two boxes every library starts with (`root`, `cabinet`).       |
| `store.py`      | `BoxStore`: CRUD, search, links, schema, migrations.               |
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

One box may sit in many boxes, so the graph is not a tree: `links` is a join
table and `ON DELETE CASCADE` removes the edges of a deleted box. Cycles are
legal, including a box inside itself (see `doc/adr/0009`), so **anything that
walks the graph has to expect to come back where it started**: `BoxStore.tree`
marks a repeated box `truncated`, and `box tree` prints `⟲ … (cycle: already
above)`. The only link the store refuses is filing a box inside a type that
cannot hold boxes (`ChildrenNotAllowed`) or filing the root box
(`RootCannotBeFiled`). The root box also has a single child slot: `link` into it
takes the slot from the previous child, which is unlinked but not deleted, and
`BoxStore.root_child()` reads it.

Timestamps are stored as ISO-8601 strings in UTC. Metadata is a JSON object of
string values; unknown shapes decode to an empty object rather than raising.

## The web interface

Two panes. The left one searches and lists boxes and opens the create-box modal;
the right one shows the selected box, as its icon or as its content.

A view registers under a box type key (`registerView("grid", …)`) and is resolved
by walking the box type's ancestors, so a type inherits the nearest ancestor's
view — `cabinet` browses like a folder, `markdown` reads like text, and anything
unbuilt falls back to the icon. This mirrors the class hierarchy in `boxtypes/`;
the client learns the ancestry from `/api/types`.

Settings that belong to a box — display mode, list or icons, wrap, font size, the
grid's rows, columns and cells — are written into the box's `config` column under
the view's own namespace, so they survive a reload and follow the box. See
`doc/adr/0008`.

Endpoints the interface uses:

| Endpoint                             | What it is for                                  |
| ------------------------------------ | ----------------------------------------------- |
| `GET /api/types`                     | the hierarchy, the rules, what the modal offers  |
| `GET /api/boxes`, `/api/search`       | the side pane                                   |
| `GET /api/boxes/{ref}`                | one box, with `{uuid,name,icon,box_type}` relations |
| `GET /api/boxes/{ref}/children`       | what a container holds                          |
| `POST /api/boxes`                     | create, optionally in a parent and a grid cell  |
| `PUT /api/boxes/{ref}/config`         | save view settings                              |
| `POST /api/boxes/{ref}/grid/place`    | place a child, swapping occupants               |
| `POST /api/boxes/{ref}/grid/resize`   | new size; reports what lost a cell              |
| `POST /api/boxes/{ref}/grid/unplace`  | empty a cell, keep the child                    |
| `GET /api/boxes/{ref}/raw`            | the bytes behind a file box (see below)         |
| `GET /api/boxes/{ref}/thumbnail`      | the miniature that box was iconed from          |
| `GET /api/types/suggest`              | the better type for a resource, if there is one |
| `GET /api/boxes/{ref}/listing`        | browse a `directory` box's files (see below)    |
| `GET /api/boxes/{ref}/content`        | one file inside that directory, for previews    |
| `GET /api/boxes/{ref}/archive`        | the members of a zip or tar in that directory   |

`{ref}` is a name or a uuid everywhere.

**Reading files off the disk** — `/raw`, `/content`, `/listing`, `/archive` — is
the one place the server touches the filesystem beyond the database. Listings and
previews are confined to the box's own resource directory first, so a `directory`
box can browse freely inside itself and nowhere else: `boxganizer/fslist.py`
resolves every client path and refuses anything that climbs out, symlinks
included. Only types whose `serves_content` is true — from
`FileResource` down — are readable, only regular files, only under 32 MiB, and
only inside `$BOXGANIZER_CONTENT_ROOT` when that is set (symlinks resolved
first). Responses carry `X-Content-Type-Options: nosniff`, and SVG gets a
sandboxing CSP header because an SVG can carry script. Text is put on the page
with `textContent`, never as HTML.

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
| `UnknownBoxType`   | message, exit 1| 422               | (reads tolerate it)  |
| `InvalidBoxContent`| message, exit 1| 422               | detail pane, red     |
| `ChildrenNotAllowed`| message, exit 1| 409              | detail pane, red     |
| `EditError`        | message, exit 1| n/a               | n/a                |
| `MissingDependency`| install hint, exit 1 | n/a         | n/a                |

`box serve` and `box tui` import their framework inside the command, so a
missing extra reaches the user as `MissingDependency` instead of an import
traceback.

A frontend never translates an error into another frontend's vocabulary, and
never swallows one silently. `GridError` is the web layer's 422; `EditError`
exists only for `box edit`.

Boxes are identified two ways: `id` is the row number, `uuid` is permanent.
Anything written down by a person or by a layout — a URL, a grid cell — uses the
uuid, because names change.

## Extension points

* **New frontend** — import `BoxStore`, add a console script, no core change.
* **Box types** — done: a class registry (`boxganizer.boxtypes`) checked in
  `BoxStore.add` and `update`, with `tree()` and `describe()` feeding the CLI,
  the API and the docs. Behaviour per type is what still has to be written.
* **Full-text search** — swap the `LIKE` query in `store._SEARCH_SQL` for an
  FTS5 index; keep the `search()` signature, and the three frontends keep working.
* **Another database** — everything SQL is in `store.py`; the rest of the core is
  storage-agnostic.
