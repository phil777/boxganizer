"""The store: SQLite persistence for boxes and the links between them.

:class:`BoxStore` is the only module that touches SQL. The CLI, web app and
text UI all call it, which keeps their behaviour identical.

One connection serves every caller, so access goes through two locked helpers
(:meth:`BoxStore._rows` and :meth:`BoxStore._execute`). That is what lets the
web app answer requests from a thread pool.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType

from boxganizer import boxtypes, config, filters
from boxganizer.errors import (
    BoxNotFound,
    ChildrenNotAllowed,
    DuplicateBoxName,
    InvalidBoxContent,
    RootCannotBeFiled,
    StoreError,
)
from boxganizer.models import DEFAULT_ICON, Box, canonical_name, new_uuid

#: 1 = boxes and links. 2 = boxes gained ``box_type`` (see ``doc/box-types.md``).
#: 3 = boxes gained ``config``: type-specific settings as JSON.
#: 4 = boxes gained ``uuid``: a permanent identifier for links and layouts.
#: 5 = dynamic folders resolve their members into ``links``; an older library's
#: cabinet is filled from its filter once, on the way in (see ``_migrate``).
#: 6 = the full-text index tables (``box_text``). They are created with every other
#: table when a database opens, but they are *filled* from the files, which is why
#: ``box index`` exists: the confine and the reading belong to the caller, not here.
SCHEMA_VERSION = 6

_SCHEMA = f"""
PRAGMA user_version = {SCHEMA_VERSION};

CREATE TABLE IF NOT EXISTS boxes (
    id           INTEGER PRIMARY KEY,
    uuid         TEXT,
    name         TEXT    NOT NULL,
    key          TEXT    NOT NULL,
    description  TEXT    NOT NULL DEFAULT '',
    icon         TEXT    NOT NULL DEFAULT '{DEFAULT_ICON}',
    box_type     TEXT    NOT NULL DEFAULT 'box',
    resource_uri TEXT,
    metadata     TEXT    NOT NULL DEFAULT '{{}}',
    config       TEXT    NOT NULL DEFAULT '{{}}',
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS boxes_key_idx ON boxes (key);
-- boxes_uuid_idx is created after the migration, once the column is certain to exist.


CREATE TABLE IF NOT EXISTS links (
    parent_id INTEGER NOT NULL REFERENCES boxes (id) ON DELETE CASCADE,
    child_id  INTEGER NOT NULL REFERENCES boxes (id) ON DELETE CASCADE,
    added_at  TEXT    NOT NULL,
    PRIMARY KEY (parent_id, child_id)
);

CREATE INDEX IF NOT EXISTS links_child_idx ON links (child_id);

/*
 * The full-text index. `box_id` is the box's uuid, so a rename cannot orphan an
 * entry, and deleting the box deletes its text (the row is dropped by the same
 * calls that maintain the links, plus `remove`).
 *
 * Porter stemming is done by SQLite, so "running" finds "run" without any code of
 * ours; the words a query was widened to are what `boxganizer.search` decides.
 * Only boxes that point at a text or PDF file are ever written here.
 */
CREATE VIRTUAL TABLE IF NOT EXISTS box_text USING fts5(
    box_id UNINDEXED,
    body,
    tokenize = 'porter unicode61'
);

/*
 * The words the index has actually seen. FTS5 does not expose them by itself:
 * `fts5vocab` is a second virtual table that reads the first one, and it is what
 * makes fuzzy matching answer with "kitchen" rather than with every word in English.
 */
CREATE VIRTUAL TABLE IF NOT EXISTS box_text_vocab USING fts5vocab(box_text, row);
"""

_INSERT_BOX = """
INSERT INTO boxes (uuid, name, key, description, icon, box_type, resource_uri, metadata,
                   config, created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_SELECT_BY_KEY = "SELECT * FROM boxes WHERE key = ?"

_SELECT_BY_TYPE = "SELECT * FROM boxes WHERE box_type = ? ORDER BY key"

_SELECT_CHILD_IDS = "SELECT child_id FROM links WHERE parent_id = ?"

_SELECT_ONE_LINK = "SELECT 1 FROM links WHERE parent_id = ? AND child_id = ?"

_INSERT_DOC = "INSERT INTO box_text (box_id, body) VALUES (?, ?)"

_DELETE_DOC = "DELETE FROM box_text WHERE box_id = ?"

# snippet() addresses the body as column 1: column 0 is the unindexed box id.
_SEARCH_DOCS = """
SELECT box_id, bm25(box_text) AS score,
       snippet(box_text, 1, '[', ']', ' … ', 12) AS passage
  FROM box_text WHERE box_text MATCH ? ORDER BY score LIMIT ?
"""

_VOCAB_BY_PREFIX = "SELECT term FROM box_text_vocab WHERE term GLOB ? LIMIT 4000"

_COUNT_DOCS = "SELECT count(*) AS n FROM box_text"

_SELECT_BY_REF = "SELECT * FROM boxes WHERE key = ? OR uuid = ?"

_SELECT_RELATED_CHILDREN = """
SELECT b.* FROM boxes b JOIN links l ON l.child_id = b.id
 WHERE l.parent_id = ? ORDER BY b.key
"""

_SELECT_RELATED_PARENTS = """
SELECT b.* FROM boxes b JOIN links l ON l.parent_id = b.id
 WHERE l.child_id = ? ORDER BY b.key
"""

_SEARCH_SQL = r"""
SELECT * FROM boxes
 WHERE key LIKE ? ESCAPE '\'
    OR lower(description) LIKE ? ESCAPE '\'
    OR lower(coalesce(resource_uri, '')) LIKE ? ESCAPE '\'
    OR lower(metadata) LIKE ? ESCAPE '\'
    OR lower(config) LIKE ? ESCAPE '\'
 ORDER BY key
 LIMIT ?
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _object_field(raw: str | None) -> dict[str, object]:
    """One JSON column decoded, tolerating null and broken text."""
    try:
        parsed = json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _as_json(value: dict[str, object]) -> str:
    """Serialise a JSON field, refusing what JSON cannot hold."""
    try:
        return json.dumps(value, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise StoreError(f"this box holds a value JSON cannot store: {exc}") from exc


def _like_escape(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class BoxStore:
    """Read/write access to a Boxganizer database.

    Open it as a context manager to guarantee the file handle is released::

        with BoxStore() as store:
            store.add(Box(name="Kitchen"))
    """

    def __init__(self, path: str | Path | None = None) -> None:
        """Open (and if needed create or upgrade) the database at *path*."""
        self.path = Path(path) if path is not None else config.default_db_path()
        if str(self.path) != ":memory:":
            config.ensure_parent(self.path)
        self._lock = threading.RLock()
        try:
            self._conn = sqlite3.connect(self.path, check_same_thread=False)
        except sqlite3.Error as exc:  # pragma: no cover - depends on OS permissions
            raise StoreError(f"cannot open database at {self.path}: {exc}") from exc
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            before = self._conn.execute("PRAGMA user_version").fetchone()
            stored_version = int(before[0]) if before is not None else 0
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
        self._migrate(stored_version)
        with self._lock:
            self._conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS boxes_uuid_idx ON boxes (uuid)")
            self._conn.commit()

    # -- lifecycle ---------------------------------------------------------

    def __enter__(self) -> BoxStore:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- boxes -------------------------------------------------------------

    def add(self, box: Box) -> Box:
        """Insert *box* and return the stored copy with ``id`` filled in.

        The type named by ``box.box_type`` must exist and must accept the box:
        see :mod:`boxganizer.boxtypes`.
        """
        kind = boxtypes.get(box.box_type)
        self._check_singleton(box, kind)
        kind.validate(box)
        now = _now()
        row_id = self._execute(
            _INSERT_BOX,
            (
                box.uuid,
                box.name,
                box.key,
                box.description or "",
                box.icon,
                box.box_type,
                box.resource_uri,
                json.dumps(box.metadata, sort_keys=True),
                _as_json(box.config),
                box.created_at.isoformat(timespec="seconds"),
                now,
            ),
            lastrowid=True,
            duplicate_name=box.name,
        )
        box.id = row_id
        box.updated_at = datetime.now(UTC)
        self._sync_membership(box)
        return box

    # -- dynamic folders ----------------------------------------------------
    #
    # A dynamic folder holds whatever its filter selects out of all the boxes, and
    # that membership is written into the links table rather than computed on read:
    # `box show`, the tree, and every box's own `in` list then see a dynamic
    # container exactly as they see one filled by hand. Three moments keep the two
    # in step — a box is created, a box changes, or a folder's filter changes — and
    # all three arrive here from `add` and `update`.

    def resolving_folders(self) -> list[Box]:
        """Every box whose type takes its members from a filter rather than by hand."""
        found = []
        for box in self.list_boxes():
            kind = boxtypes.get(box.box_type) if boxtypes.known(box.box_type) else None
            if kind is not None and kind.resolves_membership:
                found.append(box)
        return found

    def filter_of(self, folder: Box) -> filters.Filter:
        """The filter a folder lives by. A fixed one, like the cabinet, asks for all.

        Ignoring a cabinet's stored filter is the point: it is defined as everything,
        so a stray `config` entry must not quietly narrow the whole library down.
        """
        kind = boxtypes.get(folder.box_type) if boxtypes.known(folder.box_type) else None
        if kind is not None and kind.filter_is_fixed:
            return filters.Filter()
        return filters.parse_of(folder)

    def resolve_folder(self, ref: str) -> int | None:
        """Write one folder's membership from its filter, and return the member count.

        None when the box is not a dynamic folder: its links were put there on
        purpose and nothing here touches them. The root box is never a member of
        anything, which the store already refuses elsewhere, so it is skipped rather
        than raising on the way.
        """
        folder = self.resolve(ref)
        kind = boxtypes.get(folder.box_type) if boxtypes.known(folder.box_type) else None
        if kind is None or not kind.resolves_membership or folder.id is None:
            return None
        wanted = self.filter_of(folder)
        now = _now()
        held = {int(row["child_id"]) for row in self._rows(_SELECT_CHILD_IDS, (folder.id,))}
        keep: set[int] = set()
        for box in self.list_boxes():
            if box.id is None or not wanted.matches(box):
                continue
            if boxtypes.known(box.box_type) and boxtypes.get(box.box_type).is_root:
                continue
            keep.add(box.id)
        for stale in sorted(held - keep):
            self._execute(
                "DELETE FROM links WHERE parent_id = ? AND child_id = ?", (folder.id, stale)
            )
        for fresh in sorted(keep - held):
            self._execute(
                "INSERT OR IGNORE INTO links (parent_id, child_id, added_at) VALUES (?, ?, ?)",
                (folder.id, fresh, now),
            )
        return len(keep)

    def _sync_membership(self, box: Box) -> None:
        """Reconcile one box with every dynamic folder, in both directions.

        First the box's place in the folders that resolve themselves: a filter that
        no longer selects it takes it back, so a rename moves a box out of the folder
        it has stopped matching instead of leaving it filed there for good. Then, if
        the box is itself such a folder, its own members are recomputed — which is
        how editing a filter in `box edit` updates the link table.
        """
        if box.id is None:
            return
        kind = boxtypes.get(box.box_type) if boxtypes.known(box.box_type) else None
        filed_here: set[int] = set()
        root_box = kind is not None and kind.is_root
        for folder in self.resolving_folders():
            if folder.id is None or folder.id == box.id:
                continue
            filed_here.add(folder.id)
            wants = self.filter_of(folder).matches(box)
            held = self._rows(_SELECT_ONE_LINK, (folder.id, box.id))
            if wants and not held and not root_box:
                self._execute(
                    "INSERT OR IGNORE INTO links (parent_id, child_id, added_at) VALUES (?, ?, ?)",
                    (folder.id, box.id, _now()),
                )
            elif held and (not wants or root_box):
                self._execute(
                    "DELETE FROM links WHERE parent_id = ? AND child_id = ?", (folder.id, box.id)
                )
        if kind is not None and kind.resolves_membership:
            self.resolve_folder(box.uuid)

    def _check_still_holds_children(self, box: Box, kind: type[boxtypes.BoxType]) -> None:
        """Refuse a retype that would leave a leaf box holding children.

        Box A may sit inside three parents; retyping A is fine. Retyping a
        *parent* that holds boxes into a resource type is not — the children
        would be stranded inside something that cannot contain them.
        """
        if kind.allows_children:
            return
        held = self.children(box.uuid)
        if held:
            raise ChildrenNotAllowed(box.name, kind.key, held[0].name)

    def _check_singleton(self, box: Box, kind: type[boxtypes.BoxType]) -> None:
        """Refuse a second box of a type that only allows one."""
        if not kind.singleton:
            return
        rows = self._rows("SELECT id, name FROM boxes WHERE box_type = ?", (kind.key,))
        for row in rows:
            if int(row["id"]) != box.id:
                raise InvalidBoxContent(
                    kind.key,
                    f"only one {kind.label.lower()} box is allowed, and {str(row['name'])!r} is it",
                    box.name,
                )

    def get(self, name: str) -> Box:
        """Return the box named *name*, or raise :class:`BoxNotFound`."""
        rows = self._rows(_SELECT_BY_KEY, (canonical_name(name),))
        if not rows:
            raise BoxNotFound(name)
        return self._from_row(rows[0])

    def resolve(self, ref: str) -> Box:
        """Find a box by name, or by its uuid.

        Anything that faces a URL or a saved layout uses this: a uuid survives a
        rename, a name does not.
        """
        cleaned = ref.strip()
        rows = self._rows(_SELECT_BY_REF, (canonical_name(cleaned), cleaned.lower()))
        if not rows:
            raise BoxNotFound(ref)
        return self._from_row(rows[0])

    def exists(self, name: str) -> bool:
        """True when a box with that name (any case) is stored."""
        return bool(self._rows(_SELECT_BY_KEY, (canonical_name(name),)))

    def update(self, box: Box) -> Box:
        """Write the mutable fields of *box* back to the database.

        The type is re-checked, so a box cannot be retyped into something its
        resource or metadata does not support.
        """
        if box.id is None:
            raise StoreError(f"cannot update {box.name!r}: box has no id")
        kind = boxtypes.get(box.box_type)
        self._check_singleton(box, kind)
        kind.validate(box)
        self._check_still_holds_children(box, kind)
        box.touch()
        self._execute(
            """
            UPDATE boxes
               SET name = ?, key = ?, description = ?, icon = ?, box_type = ?,
                   resource_uri = ?, metadata = ?, config = ?, updated_at = ?
             WHERE id = ?
            """,
            (
                box.name,
                box.key,
                box.description or "",
                box.icon or DEFAULT_ICON,
                box.box_type,
                box.resource_uri,
                json.dumps(box.metadata, sort_keys=True),
                _as_json(box.config),
                box.updated_at.isoformat(timespec="seconds"),
                box.id,
            ),
            duplicate_name=box.name,
        )
        self._sync_membership(box)
        return box

    def remove(self, name: str) -> Box:
        """Delete the box named *name* and its links; return the deleted box."""
        box = self.get(name)
        assert box.id is not None
        self._execute("DELETE FROM boxes WHERE id = ?", (box.id,))
        # Links go with the row by the foreign keys; the full-text entry cannot, so
        # it is taken out here. A search that keeps offering a deleted box's file is
        # a broken promise, not a stale cache.
        self._execute(_DELETE_DOC, (box.uuid,))
        return box

    def list_boxes(self, *, limit: int | None = None, offset: int = 0) -> list[Box]:
        """Return every box, ordered by name."""
        sql = "SELECT * FROM boxes ORDER BY key"
        params: list[object] = []
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
            if offset:
                sql += " OFFSET ?"
                params.append(offset)
        return [self._from_row(row) for row in self._rows(sql, params)]

    def search(self, term: str, *, limit: int = 100) -> list[Box]:
        """Return boxes whose name, description, resource or metadata contain *term*."""
        cleaned = term.strip()
        if not cleaned:
            return self.list_boxes(limit=limit)
        needle = f"%{_like_escape(cleaned.casefold())}%"
        rows = self._rows(_SEARCH_SQL, (needle, needle, needle, needle, needle, limit))
        return [self._from_row(row) for row in rows]

    def count(self) -> int:
        """Number of stored boxes."""
        rows = self._rows("SELECT COUNT(*) AS n FROM boxes")
        return int(rows[0]["n"]) if rows else 0

    # -- links -------------------------------------------------------------

    def link(self, parent: str, child: str) -> None:
        """Put the box *child* inside the box *parent*. Either may be a uuid.

        A box may sit in several parents, and the graph may contain cycles —
        including a box inside itself. Two type rules still apply: a leaf type
        holds a resource rather than boxes (`ChildrenNotAllowed`), and the root
        box cannot itself be filed (`RootCannotBeFiled`).

        The root box has exactly one child slot: linking a box into the root
        takes the slot from the previous child, which is unlinked but not
        deleted. Use `root_child()` to read it.
        """
        parent_box = self.resolve(parent)
        parent_kind = boxtypes.get(parent_box.box_type)
        if not parent_kind.allows_children:
            raise ChildrenNotAllowed(parent_box.name, parent_kind.key, child)
        child_box = self.resolve(child)
        if boxtypes.get(child_box.box_type).is_root:
            raise RootCannotBeFiled(child_box.name, parent_box.name)
        parent_id = parent_box.id or 0
        child_id = child_box.id or 0
        if parent_kind.is_root:
            # The root box has one slot: filing something in it takes the slot
            # from whatever held it before. The displaced box is not deleted, and
            # keeps any other parent it has.
            self._execute(
                "DELETE FROM links WHERE parent_id = ? AND child_id != ?",
                (parent_id, child_id),
            )
        self._execute(
            "INSERT OR IGNORE INTO links (parent_id, child_id, added_at) VALUES (?, ?, ?)",
            (parent_id, child_id, _now()),
        )

    # -- the full-text index ------------------------------------------------
    #
    # What is stored here is the text of a box's file, nothing else: which boxes are
    # worth reading, how big a file may be and where the confine is, all belong to
    # `boxganizer.search`. The store keeps the rows and answers from them.

    def index_text(self, box_uuid: str, text: str) -> None:
        """Replace what is indexed for one box. Empty text removes it entirely."""
        self._execute(_DELETE_DOC, (box_uuid,))
        if text.strip():
            self._execute(_INSERT_DOC, (box_uuid, text))

    def drop_text(self, box_uuid: str) -> None:
        """Stop indexing a box, without touching the box."""
        self._execute(_DELETE_DOC, (box_uuid,))

    def content_search(self, match: str, *, limit: int = 100) -> list[tuple[str, float, str]]:
        """Boxes whose indexed text answers *match*, best first.

        Returns ``(uuid, score, passage)``. A malformed expression — a stray quote in
        what someone typed — is not an error to show them: it answers with nothing,
        and the name search still runs.
        """
        try:
            rows = self._rows(_SEARCH_DOCS, (match, limit))
        except StoreError:
            return []
        return [(str(row["box_id"]), float(row["score"]), str(row["passage"])) for row in rows]

    def vocabulary(self, *, prefix: str = "") -> tuple[str, ...]:
        """Distinct indexed words, optionally those starting with *prefix*.

        Fuzzy matching compares a typed word against these rather than against a
        dictionary: a near-miss is only useful if the library actually contains the
        word it is close to.
        """
        head = (prefix or "").strip().lower()
        try:
            rows = self._rows(_VOCAB_BY_PREFIX, (f"{head}*",))
        except StoreError:
            # No vocabulary to compare against means no fuzzy widening. The rest of
            # the search is untouched, which is the right way for an index that
            # cannot answer to fail: less clever, not broken.
            return ()
        return tuple(str(row["term"]) for row in rows)

    def boxes_by_uuid(self, uuids: Sequence[str]) -> dict[str, Box]:
        """Several boxes at once, keyed by uuid, in the order asked for.

        The full-text search finds uuids in the index and then needs the boxes behind
        them; asking one at a time would be one query per hit.
        """
        found: dict[str, Box] = {}
        for uuid in uuids:
            if uuid in found:
                continue
            rows = self._rows(_SELECT_BY_REF, ("", uuid))
            if rows:
                found[uuid] = self._from_row(rows[0])
        return found

    def indexed_boxes(self) -> int:
        """How many boxes have text in the index."""
        rows = self._rows(_COUNT_DOCS)
        return int(rows[0]["n"]) if rows else 0

    def root_box(self) -> Box | None:
        """The box the library starts from, or None when it holds none.

        Asked of the database rather than picked out of a page of `list_boxes()`:
        a page is not the library, and the root sorts wherever its name sorts, so
        a library big enough to spill over a page can hide the one box that ought
        to be the way in. `root` is a singleton type, so there is at most one; a
        type that inherits it counts, because being the start is the type's property.
        """
        for kind in boxtypes.registry.all_types():
            if not kind.is_root:
                continue
            rows = self._rows(_SELECT_BY_TYPE, (kind.key,))
            if rows:
                return self._from_row(rows[0])
        return None

    def root_child(self) -> Box | None:
        """The single box filed under the root, or None."""
        rows = self._rows(
            """
            SELECT b.* FROM boxes b
              JOIN links l ON l.child_id = b.id
              JOIN boxes r ON r.id = l.parent_id
             WHERE r.box_type = 'root'
             ORDER BY l.added_at DESC, b.key
             LIMIT 1
            """
        )
        return self._from_row(rows[0]) if rows else None

    def unlink(self, parent: str, child: str) -> None:
        """Take the box *child* out of the box *parent*. Either may be a uuid."""
        self._execute(
            "DELETE FROM links WHERE parent_id = ? AND child_id = ?",
            (self._id_of(parent), self._id_of(child)),
        )

    def children(self, ref: str) -> list[Box]:
        """Boxes directly inside the box named or identified by *ref*."""
        return [
            self._from_row(row) for row in self._rows(_SELECT_RELATED_CHILDREN, (self._id_of(ref),))
        ]

    def parents(self, ref: str) -> list[Box]:
        """Boxes directly containing the box named or identified by *ref*."""
        return [
            self._from_row(row) for row in self._rows(_SELECT_RELATED_PARENTS, (self._id_of(ref),))
        ]

    def tree(self, name: str, *, max_depth: int = 10) -> dict[str, object]:
        """Nested ``{"box", "depth", "children"}`` mapping.

        Cycles are legal in the graph, so a box already on the path down from
        *name* is marked ``truncated`` instead of being followed again.
        """
        return self._tree_node(self.get(name), depth=0, max_depth=max_depth, seen=frozenset())

    # -- internals ---------------------------------------------------------

    def _rows(self, sql: str, params: Sequence[object] = ()) -> list[sqlite3.Row]:
        with self._lock:
            try:
                return list(self._conn.execute(sql, params))
            except sqlite3.Error as exc:
                raise StoreError(f"query failed: {exc}") from exc

    def _execute(
        self,
        sql: str,
        params: Sequence[object] = (),
        *,
        lastrowid: bool = False,
        duplicate_name: str | None = None,
    ) -> int:
        with self._lock:
            try:
                cursor = self._conn.execute(sql, params)
            except sqlite3.IntegrityError as exc:
                self._conn.rollback()
                # Only the unique-name index is a duplicate box. Anything else —
                # a NOT NULL column, a broken foreign key — says what actually
                # happened instead of blaming the name.
                if duplicate_name is not None and "boxes.key" in str(exc):
                    raise DuplicateBoxName(duplicate_name) from exc
                raise StoreError(f"write refused: {exc}") from exc
            except sqlite3.Error as exc:
                self._conn.rollback()
                raise StoreError(f"write failed: {exc}") from exc
            self._conn.commit()
            return int(cursor.lastrowid or 0)

    def _id_of(self, ref: str) -> int:
        """Row id of a box named or identified by *ref*."""
        return self.resolve(ref).id or 0

    def _tree_node(
        self, box: Box, *, depth: int, max_depth: int, seen: frozenset[int]
    ) -> dict[str, object]:
        node: dict[str, object] = {"box": box, "depth": depth, "children": []}
        if box.id in seen or depth >= max_depth:
            node["truncated"] = True
            return node
        seen_here = seen | ({box.id} if box.id is not None else set())
        node["children"] = [
            self._tree_node(kid, depth=depth + 1, max_depth=max_depth, seen=seen_here)
            for kid in self.children(box.name)
        ]
        return node

    def _migrate(self, stored_version: int) -> None:
        """Bring a database written by an older Boxganizer up to date.

        ``stored_version`` is 0 for a brand-new file, which needs nothing: the
        current schema was just created there.
        """
        if stored_version in (0, SCHEMA_VERSION) or stored_version > SCHEMA_VERSION:
            return
        columns = {str(row["name"]) for row in self._rows("PRAGMA table_info(boxes)")}
        additions: list[tuple[int, str, str]] = [
            (2, "box_type", "TEXT NOT NULL DEFAULT 'box'"),
            (3, "config", "TEXT NOT NULL DEFAULT '{}'"),
            (4, "uuid", "TEXT"),
        ]
        for version, name, definition in additions:
            if stored_version < version and name not in columns:
                self._execute(f"ALTER TABLE boxes ADD COLUMN {name} {definition}")
        if stored_version < 4:
            self._backfill_uuids()
        if stored_version < 5:
            self._resolve_dynamic_folders()

    def _resolve_dynamic_folders(self) -> None:
        """Fill every dynamic folder from its filter, once, when a library is upgraded.

        Membership is written into the links table rather than computed on read, so a
        database written before a cabinet meant "everything" holds a cabinet holding
        nothing: true under the old definition, wrong under this one, and nothing
        later would correct it until someone happened to edit each folder. Re-cutting
        on the way in is what makes `box show` tell the truth the first time it is
        asked.
        """
        for folder in self.resolving_folders():
            self.resolve_folder(folder.uuid)

    def _backfill_uuids(self) -> None:
        """Give every row that lacks one a permanent identifier."""
        rows = self._rows("SELECT id FROM boxes WHERE uuid IS NULL OR uuid = ''")
        for row in rows:
            self._execute("UPDATE boxes SET uuid = ? WHERE id = ?", (new_uuid(), int(row["id"])))

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Box:
        metadata = _object_field(row["metadata"])
        config = _object_field(row["config"])
        return Box(
            id=row["id"],
            uuid=str(row["uuid"]) if row["uuid"] else new_uuid(),
            name=row["name"],
            description=row["description"],
            icon=row["icon"],
            box_type=str(row["box_type"]),
            resource_uri=row["resource_uri"],
            metadata={str(k): str(v) for k, v in metadata.items()},
            config=dict(config),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )


def seed(store: BoxStore, boxes: Iterable[Box]) -> list[Box]:
    """Add many boxes at once; return the stored copies."""
    return [store.add(box) for box in boxes]
