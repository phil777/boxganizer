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

from boxganizer import boxtypes, config
from boxganizer.errors import (
    BoxNotFound,
    ChildrenNotAllowed,
    CyclicLink,
    DuplicateBoxName,
    InvalidBoxContent,
    StoreError,
)
from boxganizer.models import DEFAULT_ICON, Box, canonical_name

#: 1 = boxes and links. 2 = boxes gained ``box_type`` (see ``doc/box-types.md``).
#: 3 = boxes gained ``config``: type-specific settings as JSON.
SCHEMA_VERSION = 3

_SCHEMA = f"""
PRAGMA user_version = {SCHEMA_VERSION};

CREATE TABLE IF NOT EXISTS boxes (
    id           INTEGER PRIMARY KEY,
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

CREATE TABLE IF NOT EXISTS links (
    parent_id INTEGER NOT NULL REFERENCES boxes (id) ON DELETE CASCADE,
    child_id  INTEGER NOT NULL REFERENCES boxes (id) ON DELETE CASCADE,
    added_at  TEXT    NOT NULL,
    PRIMARY KEY (parent_id, child_id)
);

CREATE INDEX IF NOT EXISTS links_child_idx ON links (child_id);
"""

_INSERT_BOX = """
INSERT INTO boxes (name, key, description, icon, box_type, resource_uri, metadata,
                   config, created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_SELECT_BY_KEY = "SELECT * FROM boxes WHERE key = ?"

_SELECT_RELATED_CHILDREN = """
SELECT b.* FROM boxes b JOIN links l ON l.child_id = b.id
 WHERE l.parent_id = ? ORDER BY b.key
"""

_SELECT_RELATED_PARENTS = """
SELECT b.* FROM boxes b JOIN links l ON l.parent_id = b.id
 WHERE l.child_id = ? ORDER BY b.key
"""

_SELECT_DESCENDANTS = """
WITH RECURSIVE reachable (id) AS (
    SELECT ?
    UNION
    SELECT l.child_id FROM links l JOIN reachable r ON l.parent_id = r.id
)
SELECT 1 FROM reachable WHERE id = ? LIMIT 1
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
                box.name,
                box.key,
                box.description,
                box.icon,
                box.box_type,
                box.resource_uri,
                json.dumps(box.metadata, sort_keys=True),
                _as_json(box.config),
                box.created_at.isoformat(timespec="seconds"),
                now,
            ),
            lastrowid=True,
        )
        box.id = row_id
        box.updated_at = datetime.now(UTC)
        return box

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
                box.description,
                box.icon,
                box.box_type,
                box.resource_uri,
                json.dumps(box.metadata, sort_keys=True),
                _as_json(box.config),
                box.updated_at.isoformat(timespec="seconds"),
                box.id,
            ),
        )
        return box

    def remove(self, name: str) -> Box:
        """Delete the box named *name* and its links; return the deleted box."""
        box = self.get(name)
        assert box.id is not None
        self._execute("DELETE FROM boxes WHERE id = ?", (box.id,))
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
        """Put the box *child* inside the box *parent*.

        Refused when the parent's type holds a resource rather than boxes, or
        when the link would make a box reach itself.
        """
        parent_box = self.get(parent)
        parent_kind = boxtypes.get(parent_box.box_type)
        if not parent_kind.allows_children:
            raise ChildrenNotAllowed(parent, parent_kind.key, child)
        parent_id = parent_box.id or 0
        child_id = self._id_of(child)
        if self._is_descendant(child_id, parent_id):
            raise CyclicLink(parent, child)
        self._execute(
            "INSERT OR IGNORE INTO links (parent_id, child_id, added_at) VALUES (?, ?, ?)",
            (parent_id, child_id, _now()),
        )

    def unlink(self, parent: str, child: str) -> None:
        """Take the box *child* out of the box *parent*."""
        self._execute(
            "DELETE FROM links WHERE parent_id = ? AND child_id = ?",
            (self._id_of(parent), self._id_of(child)),
        )

    def children(self, name: str) -> list[Box]:
        """Boxes directly inside *name*."""
        return [
            self._from_row(row)
            for row in self._rows(_SELECT_RELATED_CHILDREN, (self._id_of(name),))
        ]

    def parents(self, name: str) -> list[Box]:
        """Boxes directly containing *name*."""
        return [
            self._from_row(row) for row in self._rows(_SELECT_RELATED_PARENTS, (self._id_of(name),))
        ]

    def tree(self, name: str, *, max_depth: int = 10) -> dict[str, object]:
        """Nested ``{"box", "depth", "children"}`` mapping, guarded against cycles."""
        return self._tree_node(self.get(name), depth=0, max_depth=max_depth, seen=frozenset())

    # -- internals ---------------------------------------------------------

    def _rows(self, sql: str, params: Sequence[object] = ()) -> list[sqlite3.Row]:
        with self._lock:
            try:
                return list(self._conn.execute(sql, params))
            except sqlite3.Error as exc:
                raise StoreError(f"query failed: {exc}") from exc

    def _execute(self, sql: str, params: Sequence[object] = (), *, lastrowid: bool = False) -> int:
        with self._lock:
            try:
                cursor = self._conn.execute(sql, params)
            except sqlite3.IntegrityError as exc:
                self._conn.rollback()
                raise DuplicateBoxName(str(params[0] if params else "?")) from exc
            except sqlite3.Error as exc:
                self._conn.rollback()
                raise StoreError(f"write failed: {exc}") from exc
            self._conn.commit()
            return int(cursor.lastrowid or 0)

    def _id_of(self, name: str) -> int:
        return self.get(name).id or 0

    def _is_descendant(self, start_id: int, target_id: int) -> bool:
        """True when *target_id* is *start_id* or reachable from it downwards."""
        return bool(self._rows(_SELECT_DESCENDANTS, (start_id, target_id)))

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
        ]
        for version, name, definition in additions:
            if stored_version < version and name not in columns:
                self._execute(f"ALTER TABLE boxes ADD COLUMN {name} {definition}")

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Box:
        metadata = _object_field(row["metadata"])
        config = _object_field(row["config"])
        return Box(
            id=row["id"],
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
