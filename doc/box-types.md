# Box types

Every box declares what kind of thing it is. The hierarchy below comes from the
project's source document — a heading outline of box kinds, kept in this repo as
[box-types-source.md](box-types-source.md) — and is implemented as Python classes
in `src/boxganizer/boxtypes/`.

See it live, straight from the registry:

```bash
box types                 # the whole hierarchy, grouped levels marked
box types --selectable    # only the types you can actually pick
box types --json          # every rule each type declares
```

Or over HTTP: `GET /api/types`, `GET /api/types?selectable_only=true`.

## The hierarchy

`group` = a level of structure, not a choice. `leaf` = holds one thing, never
other boxes. Rules in brackets are enforced by the store.

```
- `box` 📦 Box
  - `content` 📄 Content — group; leaf
    - `label` 🏷️ Label — leaf
    - `note` 📝 Note — children allowed; resource optional
  - `resource` 🔗 Resource — group; leaf; resource required
    - `file` 📃 File — leaf; resource required; schemes: file
      - `text` 🔤 Text — leaf; resource required; schemes: file
        - `markdown` 📝 Markdown — leaf; resource required; extensions: .md .markdown .mdown
      - `image` 🖼️ Image — leaf; resource required; extensions: .png .jpg .jpeg .gif .webp .bmp .svg .heic
      - `pdf` 📕 PDF — leaf; resource required; extensions: .pdf
      - `video` 🎥 Video — leaf; resource required; extensions: .mp4 .webm .mov .m4v .ogv .ogg *(added, see below)*
      - `archive` 🗜️ Archive — leaf; resource required; extensions: .zip .tar .gz .tgz .bz2 .xz .7z .rar
    - `directory` 📁 Directory — resource required; schemes: file
      - `indexed-directory` 🗂️ Indexed Directory — resource required; schemes: file;
        its files are read into the search index (ADR 16)
    - `dynamic` ⚡ Dynamic Resource — group
      - `web` 🌐 Web Page — resource required; schemes: http https
        - `browser` 🧭 Browser — resource required; schemes: http https
        - `snapshot` 📸 Snapshot — resource required; schemes: http https
      - `terminal` ⌨️ Terminal
        - `ssh` 🔐 SSH Terminal — schemes: ssh; the host comes from the address
      - `vnc` 💻 VNC Screen — schemes: vnc; this machine speaks RFB, the page is shown frames;
        optional metadata: `password_file`
      - `sensor` 📡 Sensor — a reading is turned into a value by three templates in `config`
        (`extract_template`, `display_template`, `style_template`); the styling output is filtered
        against an allow-list before the page sees it (ADR 21)
        - `mqtt` 📶 MQTT Sensor — schemes: mqtt mqtts; the topic is the path
        of the address (`metadata["topic"]` remains as an override)
        needs the `live` extra (`aiomqtt`): the type, the dialog and `box types` all say so
        when it is absent, and the box is then refused rather than retried
        - `http` 📥 HTTP Sensor — resource required; schemes: http https
        - `home-assistant` 🏠 Home Assistant — schemes: http https;
        needs metadata: entity_id (nowhere in a URL to be read from)
  - `folder` 📂 Folder
    - `dynamic-folder` 🗃️ Dynamic Folder — members come from `config.filter` (one expression;
        ADR 20)
      - `cabinet` 🗄️ Cabinet — the dynamic folder that filters nothing; holds every box, and itself
    - `root` 🌲 Root — ours, not the document's; singleton
  - `organizer` 🧰 Organizer — group
    - `desktop` 🖥️ Desktop
      - `blueprint` 📐 Blueprint
    - `3d-model` 🧊 3D Model
    - `grid` 🔳 Grid
    - `mindmap` 🕸️ Mindmap
    - `graph` 🧩 Graph
      - `manual-graph` ✍️ Manual Graph
      - `parental-graph` 🌳 Parental Graph
    - `dashboard` 📊 Dashboard
    - `checklist` 📋 Checklist
  - `viewport` 🎛️ Viewport — group
    - `simple-viewport` 👁️ Simple Viewport
    - `tiling-viewport` 🔷 Tiling Viewport
    - `windowed-viewport` 🪟 Windowed Viewport
```

44 types: 5 grouping levels and 39 you can put on a box.

One type is not in the source document: `root` 🌲, a `FolderBox` subtype, because
a database needs one place where the graph starts. `box init` creates it, and it
is the only type that may exist once (`singleton`): a second `root` is refused. It
holds **one** child (`is_root`): the interface shows that child's content, and
linking a box into the root takes the slot from whoever held it.

## What a type declares

| Class attribute         | Meaning                                                     |
| ----------------------- | ----------------------------------------------------------- |
| `key`                   | Public identifier, stored in `Box.box_type`. Required, unique. |
| `label`                 | Name shown to a human.                                      |
| `icon`                  | Default glyph for the interfaces.                           |
| `summary`               | One line about what the type is for.                        |
| `abstract`              | `True` on grouping levels only. Not inherited.              |
| `allows_children`       | May this box contain other boxes?                           |
| `resource_uri_required` | Must the box point at something?                            |
| `resource_uri_schemes`  | Allowed URI schemes; `"file"` also accepts plain paths.     |
| `extensions`            | Allowed filename suffixes, or `None` for any.               |
| `required_metadata`     | Metadata keys the user must supply.                         |
| `singleton`             | At most one box of this type per database.                        |
| `planned`               | What the type will do once it is built. Empty means: done.  |
| `check(box)`            | Hook for rules that need logic.                             |

## What is enforced today

1. **The type must exist** — `store.add()` and `store.update()` resolve the key
   and raise `UnknownBoxType` when it does not.
2. **The box must satisfy its type** — required resource, URI scheme, file
   extension, required metadata, then the type's own `check()`. Failures raise
   `InvalidBoxContent`.
3. **Singletons stay single** — a second box of a `singleton` type raises
   `InvalidBoxContent`; editing the one that exists is fine.
4. **Leaves stay leaves** — linking a box into one whose type has
   `allows_children = False` raises `ChildrenNotAllowed`.
5. **The type lends the box its icon** — a box created without an explicit
   `--icon` takes the icon of its type, so a picture looks like a picture. An
   icon chosen by hand always wins (`boxtypes.adopt_type_icon`).
6. **Reads are forgiving** — listing and showing a box uses `boxtypes.try_get()`,
   so a database written by a newer Boxganizer still opens; it displays
   `hologram (unknown type; this Boxganizer is older than the box)`.

No type **does** anything yet: nothing fetches, renders, indexes, subscribes or
lays out. That is the work each `planned` line describes.

## Two rules that shaped the hierarchy

**A leaf is one immutable thing, not a class with few subclasses.** `Label`,
`ImageResource` and `PDFResource` forbid children; `DirectoryResource`,
`FolderBox` and every organizer allow them. `ArchiveResource` is a leaf: the
source doc points it at `DirectoryResource`, which means an archive is *browsed*
like a folder — its entries are virtual and are not stored as contained boxes.

**A field the machine writes is never required at creation.** `SnapshotResource`
will gain `captured_at` when the capture happens, so requiring it up front would
make the type impossible to create. Required metadata is only for what the user
knows at creation time: `ssh` needs `host` and refuses an address carrying a password,
`mqtt` needs `topic`,
`home-assistant` needs `entity_id`.

## Resource analysis and proposals

`boxganizer/infer.py` reads a resource URI and answers "which type really fits?".
It proposes a type in two cases only: the suggestion is a **descendant** of the one
chosen (`file` → `pdf`), or the chosen type would **reject** the resource (`mqtt`
holding an `https://` address). Everything else stays quiet — `image` with a `.png`
needs no advice.

Proposals are never applied on their own:

* `box add -t file -r report.pdf` creates the box and prints
  `tip: that resource ends with .pdf — it would suit 📕 PDF (-t pdf, or --infer)`.
* `--infer` accepts on the command line, but **only when `-t` was not typed**: an
  explicit choice is respected even with `--infer` (detected via Click's parameter
  source, matched by name because Typer vendors its own copy of that enum).
* The web modal shows a proposal row with a **Use 📕 PDF** button.

One rule the analysis exposed: a `file` box may not point at a **directory**.
`FileResource.check` now refuses it at write time ("use the 'directory' type for
that"), and inference treats it as a rejection, so choosing `file` for a folder
path proposes `directory`. `boxganizer/paths.py` holds the one URI parser that
inference, the file endpoint and the type all share — three readers of the same
field was three chances to disagree about `file://` quoting.

## How a dynamic folder decides what it holds

A dynamic folder stores one expression. The page writes it, the store reads it, and both use the
same parser, so the controls and the text cannot disagree about what the folder selects (ADR 20).

    type:note and (tag:urgent or name:"boiler fix") and not resource:/\.pdf$/

Five fields are available:

| Field | Asks | Plain text means | Regex means |
| --- | --- | --- | --- |
| `type` | the box's type | type is this, or below it in the hierarchy | pattern matched against the type key |
| `name` | the box's name | case-insensitive substring | pattern matched against the name |
| `tag` | the tag tree | carries this tag, or any tag below it | refused: a tag is a name, not a pattern |
| `content` | the box's own words | the same query the search box runs | refused: the index is stemmed full text |
| `resource` | the resource URI | case-insensitive substring | pattern matched against the URI |

`and`, `or` and `not` combine clauses, brackets group them, and writing clauses next to each other
means `and`. A value with spaces is quoted: `name:"boiler fix"`. A regular expression is written
`/like this/`.

Three rules keep a folder honest:

* **An expression that cannot be read holds nothing, and says why.** A half-read filter would
  easily end up with no conditions, and no conditions means the whole library in the folder.
* **The filter bar shows one row per clause** — field, plain or regex, value, and whether to
  negate it — with add, remove, move up, move down, and a single switch between `and` and `or`.
  A grouped expression (`(a or b) and c`) is edited in the field: no row order means the same
  thing after a reorder, so the bar says so instead of drawing a list that would lie.
* **Membership is written by the store**, whenever the filter is edited or any box changes. A box
  the filter stops selecting is taken back out.

## Adding a type

1. Pick the module that owns the branch (`content.py`, `resource.py`,
   `dynamic.py`, `folder.py`, `organizer.py`, `viewport.py`) and add a subclass
   in the order the hierarchy reads.
2. Declare `key`, `label`, `icon`, `summary`, plus `allows_children` and the
   resource/metadata rules. Set `planned` to what it will eventually do.
3. Add it to the re-exports and `__all__` in `boxtypes/__init__.py`.
4. Add one line to `DOCUMENTED` in `tests/test_boxtypes.py` (key → parent), or to
   `ADDITIONS` if the type is not in the source document.
   That test is the contract with the source document: forget it and the suite
   fails.
5. Add the rule tests if the type declares anything unusual, and update the
   tree above.

Two mistakes the registry already catches for you: a subclass that forgets
`key`, and two types claiming the same key — both raise `TypeError` at import.

## Open questions from the source document

* One heading under `ContentBox` is empty. Content kinds are missing, not
  undecided — `label` is the only one that landed.
* Where does a `Desktop` store the position of each child: on the child link, or
  in the child's metadata? `Grid` and the viewports will ask the same question,
  so it wants one answer.
* `Blueprint` is a layout that makes `Desktop`s. Whether it holds boxes of its
  own (it allows children today) or only describes them is unresolved.
* `Model3D` is written that way in code because a Python class name cannot start
  with a digit; its key and label are still `3d-model` and "3D Model".
* `Graph` and `ViewportBox` overlap: both show other boxes rather than holding
  them. Kept separate for now, because a graph may be edited and a viewport may
  not.
* Where checklist ticks live: in the checklist's own file, as `- [ ]` / `- [x]` task
  markers. Not on the child boxes: a list of things to do is mostly made of things that
  are not boxes, and a tick that lives on a box cannot be searched by what it says. A
  child box may be hung on one item — drawn as an icon at the right of that line, written
  in the file as `[📦 Name](boxganizer:uuid)` — and a child on no item is shown as
  unattached rather than hidden.

## Types boxganizer 2 added

The document above is kept as written. Two types exist because the running
product needs them, and both are listed in `ADDITIONS` in `tests/test_boxtypes.py`
so an omission fails a test rather than passing unnoticed:

- **`root` 🌲** (`folder`) — one place for the graph to start, with exactly one
  child. See `doc/adr/0010-single-slot-root.md`.
- **`video` 🎥** (`file`) — so an `.mp4` can be inferred, previewed in the
  directory explorer, and filed as itself instead of being called a `file`.

## What each resource type shows

| Type | Icon | Content |
| --- | --- | --- |
| `image` | a miniature of the file, in a thin frame | the image, fitted to the pane |
| `pdf` | its first page, in a thin document outline | the browser's PDF viewer, opened on the file |
| `video` | emoji (no poster frame yet) | `<video controls>` on the file |
| `archive` | emoji | the member listing of a zip or tar |
| `text`, `markdown` | emoji | the text, with wrap/font/crop controls |
| `file` | emoji | a card that says so, plus a link to the file |
| `directory` | emoji | the file explorer, below |

Miniatures are not stored in the database. A box records
`config.icon_source = "thumbnail"` and `config.thumb` (path, size, mtime); the
PNG lives in `<data dir>/thumbnails/<uuid>-<size>-<mtime>.png`, so replacing the
file replaces the icon, and the stale key is simply never read again. If the
miniature cannot be served, the UI falls back to the emoji rather than showing a
broken image.

## The directory explorer

A `directory` box browses the real filesystem in place of showing child boxes:

- Directories first, then files by name; dot files hidden unless the box points
  straight at a dot folder.
- **Double-click a directory** to go deeper — still the content of the same box.
  `↑` goes up, `▣ box folder` returns to the directory the box names.
- **Double-click a file** to open it here in whichever viewer fits: image, text,
  markdown, PDF, video, or an archive listing. A file nothing can open gets a
  card with a link to it.
- **Right-click** an entry to make a box out of it, with the type already
  inferred (`markdown` for `notes.md`, `video` for `clip.mp4`, `directory` for a
  folder) and the resource filled in.

Every path from the browser is resolved and confined to the box's own resource
directory, and then to `$BOXGANIZER_CONTENT_ROOT` when that is set — symlinks
included (`boxganizer/fslist.py:safe_join`). Escaping is a `403 path_out_of_range`.

### A word on the two boxes that write files, and the one that opens a screen

`checklist` and `note` need no resource when they are made. The first time there is
something to keep, Boxganizer writes a file beside the database —
`<directory-of-the-database>/files/<uuid>.md` — and gives the box a resource row pointing at
it, whose origin is `generated`. That row is what puts the words in the full-text index, and
it is also what exempts the file from `$BOXGANIZER_CONTENT_ROOT`: the confine is a rule about
which paths other people's boxes may open, and applying it to the library's own files would
make every note unsearchable. ADR 14.

`vnc` holds no secret either, in the same way `ssh` holds none — and unlike `ssh`, it can
still use one. `None` is chosen when the server will take it; VNC authentication when it will
not, with the password taken from the field the panel shows or from the file named by
`metadata["password_file"]`. Either way the bytes are used for that handshake and dropped:
they are never written to the box, the metadata, or the address, and a test reads the database
file's bytes to prove it. A `vnc://secret@host` address is still refused by the type.
ADRs 15 and 17.
