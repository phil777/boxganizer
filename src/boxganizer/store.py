"""The store: SQLite persistence for boxes and the links between them.

:class:`BoxStore` is the only module that touches SQL. The CLI, web app and
text UI all call it, which keeps their behaviour identical.

One connection serves every caller, so access goes through two locked helpers
(:meth:`BoxStore._rows` and :meth:`BoxStore._execute`). That is what lets the
web app answer requests from a thread pool.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType

from boxganizer import boxtypes, config, filters, paths, resources
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
#: 7 = ``resources``, and the index keyed by it (``resource_text``). A box points at a
#: resource instead of carrying its own copy of an address, so one file watched and
#: indexed once serves every box that points at it — and "does this need reindexing?"
#: finally has somewhere to be remembered. See :mod:`boxganizer.resources` and
#: :meth:`BoxStore._split_resources_out`.
SCHEMA_VERSION = 8

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
    /*
     * What the box points at lives in `resources`, and this points there. One file,
     * one row: two boxes on one PDF means the PDF is read, hashed and watched once,
     * and a box that is deleted does not take the record of a file that is still on
     * disk with it. See `boxganizer.resources`.
     */
    resource_id  INTEGER REFERENCES resources (id),
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

"""

# v7: the resources table, and the index keyed by it rather than by the box.
_SCHEMA_RESOURCES = """
CREATE TABLE IF NOT EXISTS resources (
    id          INTEGER PRIMARY KEY,
    /* The address as it was written, and the form two addresses are compared on. */
    reference   TEXT    NOT NULL,
    ref_key     TEXT    NOT NULL UNIQUE,
    /* 'local' | 'remote' | 'generated': can this be opened here at all. */
    origin      TEXT    NOT NULL DEFAULT 'local',
    /* The opt in/out of the full-text index. A default came from the box type; the
       dialog's checkbox is what settles it. */
    index_it    INTEGER NOT NULL DEFAULT 0,
    /* What was last read into the index, and what the thing looked like then: the
       size and modification time answer "read it again?" from one stat, and the hash
       of the text keeps a file that was merely touched from being read a third time. */
    indexed_at  TEXT,
    mtime       REAL,
    bytes       INTEGER,
    digest      TEXT,
    /* Something noticed that makes the index wrong: a file edited outside Boxganizer,
       or one that has gone away. Set by `check`, cleared by a reindex. */
    stale       INTEGER NOT NULL DEFAULT 0,
    /* A folder holds other people's words rather than words of its own. Remembered
       rather than re-stat'd, because a count of what the index holds must be able to
       tell "this one was read and is empty" from "this one is a folder, and its words
       live in the rows under it" — without walking the disk to find out. */
    is_dir      INTEGER NOT NULL DEFAULT 0
);

/*
 * Which folders a file was found inside.
 *
 * A folder marked for indexing does not get read — there is nothing to read in a folder —
 * it gets walked, and the files that come out are resources in their own right, with rows
 * of their own, so the ordinary "has this one changed" mechanism works on them unchanged.
 * What a folder still has to be able to say is which files *it* brought in, because that
 * is the only way to tell a file that has been deleted from the folder from one that was
 * simply never inside it, and the first is a reason to take words out of the index.
 *
 * Links, not ownership. A file inside two indexed folders is one resource with two links,
 * and removing one folder from the library must not delete the other's index entries.
 */
CREATE TABLE IF NOT EXISTS resource_link (
    parent_id INTEGER NOT NULL REFERENCES resources (id) ON DELETE CASCADE,
    child_id  INTEGER NOT NULL REFERENCES resources (id) ON DELETE CASCADE,
    PRIMARY KEY (parent_id, child_id)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS resource_link_child_idx ON resource_link (child_id);

CREATE INDEX IF NOT EXISTS resources_stale_idx ON resources (stale, index_it);

/*
 * The full-text index, keyed by the resource rather than by the box: it is the file
 * that has the words, and several boxes may be pointing at it.
 */
CREATE VIRTUAL TABLE IF NOT EXISTS resource_text USING fts5(
    resource_id UNINDEXED,
    body,
    tokenize = 'porter unicode61'
);

CREATE VIRTUAL TABLE IF NOT EXISTS resource_text_vocab USING fts5vocab(resource_text, row);
"""

_INSERT_BOX = """
INSERT INTO boxes (uuid, name, key, description, icon, box_type, resource_id, metadata,
                   config, created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

# Reading a box means reading what it points at, so the columns come along in the same
# query: a box list that asked for one resource per box would be a thousand queries.
# Reading a box means reading what it points at, so the columns come along in the same
# query: a box list that asked for one resource per box would be a thousand queries. The
# head and the FROM are kept apart so the joins in `root_child` and the link reads below
# extend one definition rather than repeating a column list that would drift.
_BOX_COLUMNS = """SELECT b.*, r.reference AS resource_uri, r.id AS res_id,
       r.origin AS res_origin, r.index_it AS res_index_it,
       r.indexed_at AS res_indexed_at, r.mtime AS res_mtime, r.bytes AS res_bytes,
       r.digest AS res_digest, r.stale AS res_stale, r.is_dir AS res_is_dir"""

_BOX_FROM = "FROM boxes b LEFT JOIN resources r ON r.id = b.resource_id"

_SELECT_BOX = f"{_BOX_COLUMNS} {(_BOX_FROM)}"

_SELECT_BY_KEY = f"{_BOX_COLUMNS} {_BOX_FROM} WHERE b.key = ?"

_SELECT_BY_TYPE = f"{_BOX_COLUMNS} {_BOX_FROM} WHERE b.box_type = ? ORDER BY b.key"

_SELECT_CHILD_IDS = "SELECT child_id FROM links WHERE parent_id = ?"

_SELECT_ONE_LINK = "SELECT 1 FROM links WHERE parent_id = ? AND child_id = ?"

_INSERT_DOC = "INSERT INTO resource_text (resource_id, body) VALUES (?, ?)"

_DELETE_DOC = "DELETE FROM resource_text WHERE resource_id = ?"

# snippet() addresses the body as column 1: column 0 is the unindexed resource id.
_SEARCH_DOCS = """
SELECT resource_id, bm25(resource_text) AS score,
       snippet(resource_text, 1, '[', ']', ' \u2026 ', 12) AS passage
  FROM resource_text WHERE resource_text MATCH ? ORDER BY score LIMIT ?
"""

_VOCAB_BY_PREFIX = "SELECT term FROM resource_text_vocab WHERE term GLOB ? LIMIT 4000"

_COUNT_DOCS = "SELECT count(*) AS n FROM resource_text"

_SELECT_BY_REF = f"{_BOX_COLUMNS} {_BOX_FROM} WHERE b.key = ? OR b.uuid = ?"

_SELECT_BOXES_BY_RESOURCE = f"{_BOX_COLUMNS} {_BOX_FROM} WHERE b.resource_id = ? ORDER BY b.key"

# The links are joined onto the same reading, so a child arrives with its own resource
# and the panel does not ask again for each of them.
_SELECT_RELATED_CHILDREN = (
    f"{_BOX_COLUMNS} {_BOX_FROM} JOIN links l ON l.child_id = b.id"
    " WHERE l.parent_id = ? ORDER BY b.key"
)

_SELECT_RELATED_PARENTS = (
    f"{_BOX_COLUMNS} {_BOX_FROM} JOIN links l ON l.parent_id = b.id"
    " WHERE l.child_id = ? ORDER BY b.key"
)

# A box is found by its own fields or by the address it points at, which since v7 lives
# in another table: the join above is what makes that one query rather than two.
_SEARCH_SQL = (
    f"{_BOX_COLUMNS} {_BOX_FROM}"
    + r"""
 WHERE b.key LIKE ? ESCAPE '\'
    OR lower(b.description) LIKE ? ESCAPE '\'
    OR lower(coalesce(r.reference, '')) LIKE ? ESCAPE '\'
    OR lower(b.metadata) LIKE ? ESCAPE '\'
    OR lower(b.config) LIKE ? ESCAPE '\'
 ORDER BY b.key
 LIMIT ?
"""
)


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

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        content_root: str | Path | None = None,
    ) -> None:
        """Open (and if needed create or upgrade) the database at *path*.

        `content_root` is the directory the index is allowed to read inside, and it is
        kept here rather than passed to each call because the box that is created does
        not know who created it: the page has one confine, `box index --root` names
        another, and the default is the one from the environment. Nothing else about the
        store changes — it is still storage — but a box added through it can be read into
        the index in the same breath, and that needs to know where reading is allowed.
        """
        self.path = Path(path) if path is not None else config.default_db_path()
        self.content_root = paths.content_root(content_root)
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
            self._conn.executescript(_SCHEMA_RESOURCES)
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

    def add(self, box: Box, *, index_it: bool | None = None) -> Box:
        """Insert *box* and return the stored copy with ``id`` filled in.

        The type named by ``box.box_type`` must exist and must accept the box:
        see :mod:`boxganizer.boxtypes`.

        ``index_it`` is the answer of the dialog's "keep this in the full-text index"
        checkbox. Left out, the default falls out of what the box points at and what it
        is — see :meth:`_ensure_resource`.
        """
        kind = boxtypes.get(box.box_type)
        self._check_singleton(box, kind)
        kind.validate(box)
        box.resource_id = self._ensure_resource(
            box.resource_uri, index_it=index_it, indexes_content=kind.indexes_content
        )
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
                box.resource_id,
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
        self._read_in(box)
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

    def update(self, box: Box, *, index_it: bool | None = None) -> Box:
        """Write the mutable fields of *box* back to the database.

        The type is re-checked, so a box cannot be retyped into something its
        resource or metadata does not support. ``index_it`` is the dialog's checkbox;
        left out, what the resource row already said stands.
        """
        if box.id is None:
            raise StoreError(f"cannot update {box.name!r}: box has no id")
        kind = boxtypes.get(box.box_type)
        self._check_singleton(box, kind)
        kind.validate(box)
        self._check_still_holds_children(box, kind)
        before = self._rows("SELECT resource_id FROM boxes WHERE id = ?", (box.id,))
        previous = int(before[0]["resource_id"]) if before and before[0]["resource_id"] else None
        box.resource_id = self._ensure_resource(
            box.resource_uri, index_it=index_it, indexes_content=kind.indexes_content
        )
        box.touch()
        self._execute(
            """
            UPDATE boxes
               SET name = ?, key = ?, description = ?, icon = ?, box_type = ?,
                   resource_id = ?, metadata = ?, config = ?, updated_at = ?
             WHERE id = ?
            """,
            (
                box.name,
                box.key,
                box.description or "",
                box.icon or DEFAULT_ICON,
                box.box_type,
                box.resource_id,
                json.dumps(box.metadata, sort_keys=True),
                _as_json(box.config),
                box.updated_at.isoformat(timespec="seconds"),
                box.id,
            ),
            duplicate_name=box.name,
        )
        # A box that moved to another file lets the old one go — and a box that moved
        # *off* a file lets that one go too, which is the same case with nothing in its
        # place. Either way the file itself is nobody's to delete; only the row that
        # remembered it is, and only when nothing points at it now.
        if previous is not None and previous != box.resource_id:
            self._release_resource(previous)
        self._read_in(box)
        self._sync_membership(box)
        return box

    def remove(self, name: str) -> Box:
        """Delete the box named *name* and its links; return the deleted box."""
        box = self.get(name)
        assert box.id is not None
        self._execute("DELETE FROM boxes WHERE id = ?", (box.id,))
        # Links go with the row by the foreign keys. The resource does not: it may be
        # what another box points at, and deleting the box was no word about the file.
        # When this was the last box on it, the row and its indexed text go together —
        # a search that keeps offering the file of a box that no longer exists is not a
        # stale cache but a broken promise.
        if box.resource_id is not None:
            self._release_resource(int(box.resource_id))
        return box

    def list_boxes(self, *, limit: int | None = None, offset: int = 0) -> list[Box]:
        """Return every box, ordered by name."""
        sql = _SELECT_BOX + " ORDER BY b.key"
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

    # -- resources ---------------------------------------------------------
    #
    # What a box points at is a row of its own, so that one file means one entry in the
    # full-text index and one set of observations about whether it has changed. The box
    # still carries `resource_uri`, filled from that row on every read: nothing outside
    # this class needs to know the address lives elsewhere.

    def _ensure_resource(
        self,
        reference: str | None,
        *,
        index_it: bool | None = None,
        indexes_content: bool = False,
        origin: str | None = None,
    ) -> int | None:
        """The row for this address, made if nothing has pointed at it before.

        Comparison is on the normalised form, so `/a/../a.txt`, `a.txt` written from
        `/a`, and `file:///a/a.txt` all land on one row. An address nobody has used
        gets its default from what it is: a box whose type reads its file (a text, a
        PDF) is indexed, as is a local file whose ending says words; a web page, an MQTT
        topic, a picture are not. `index_it` — the dialog's checkbox — overrules both.

        An existing row is left alone unless someone named a wish: re-pointing a second
        box at a file is not a reason to change what is known about it.
        """
        raw = (reference or "").strip()
        if not raw:
            return None
        key = resources.normalise(raw)
        found = self._rows("SELECT id FROM resources WHERE ref_key = ?", (key,))
        if found:
            existing = int(found[0]["id"])
            if index_it is not None:
                self._execute(
                    "UPDATE resources SET index_it = ?, stale = 0 WHERE id = ?",
                    (int(bool(index_it)), existing),
                )
            # A row made before this was noticed — a folder box pointed at a directory that
            # has since been created — is corrected here rather than lying forever.
            self._execute("UPDATE resources SET is_dir = ? WHERE id = ? AND is_dir = 0",
                          (int(resources.is_directory(raw)), existing))
            return existing
        where = origin or resources.origin_of(raw)
        if index_it is None:
            index_it = bool(indexes_content) or resources.index_by_default(raw, origin=where)
        return self._execute(
            "INSERT INTO resources (reference, ref_key, origin, index_it, is_dir)"
            " VALUES (?, ?, ?, ?, ?)",
            (raw, key, where, int(bool(index_it)), int(resources.is_directory(raw))),
            lastrowid=True,
        )

    def register_resource(
        self,
        reference: str,
        *,
        origin: str | None = None,
        index_it: bool | None = None,
    ) -> resources.Resource | None:
        """Adopt an address as a resource row, and return it.

        The door other modules use when a box is given a file of its own. It is the same
        `_ensure_resource` every other write goes through — one address, one row — named
        so that a caller outside this class is not reaching into a private method to do
        something the schema's whole point depends on.
        """
        resource_id = self._ensure_resource(reference, origin=origin, index_it=index_it)
        return None if resource_id is None else self.resource(int(resource_id))

    def _release_resource(self, resource_id: int) -> None:
        """Let go of a resource one box stopped pointing at.

        Only the last reference is the last use: the file goes on living on disk for the
        next box, and its indexed text goes on being useful, until nothing points at it.
        """
        left = self._rows("SELECT count(*) AS n FROM boxes WHERE resource_id = ?", (resource_id,))
        if left and int(left[0]["n"]) > 0:
            return
        self._execute(_DELETE_DOC, (resource_id,))
        self._execute("DELETE FROM resources WHERE id = ?", (resource_id,))

    def resource(self, ref: int | str | os.PathLike[str] | Box) -> resources.Resource | None:
        """One resource, by its id, by its address, or by a box pointing at it.

        A path is accepted as a `Path` or as a string because both are how addresses
        arrive from the rest of the code; either way it is the normalised form that finds
        the row, which is the same form two boxes were deduped on.
        """
        if isinstance(ref, Box):
            if ref.resource_id is None:
                return None
            key: object = ref.resource_id
            sql = "SELECT * FROM resources WHERE id = ?"
        elif isinstance(ref, int):
            key, sql = ref, "SELECT * FROM resources WHERE id = ?"
        else:
            key, sql = resources.normalise(str(ref)), "SELECT * FROM resources WHERE ref_key = ?"
        rows = self._rows(sql, (key,))
        return self._resource_from_row(rows[0]) if rows else None

    @staticmethod
    def _resource_from_joined(row: sqlite3.Row) -> resources.Resource | None:
        """The resource of a box, from the columns the box query already joined in.

        Those columns are aliased (`res_id`, `res_origin`, …) because a box row and a
        resource row have `id` in common, so this reads the aliases rather than reusing
        :meth:`_resource_from_row`, which reads a `resources` row whole.
        """
        keys = row.keys()
        if "res_id" not in keys or row["res_id"] is None:
            return None
        return resources.Resource(
            id=int(row["res_id"]),
            reference=str(row["resource_uri"] or ""),
            origin=str(row["res_origin"]),
            index_it=bool(row["res_index_it"]),
            is_dir=bool(row["res_is_dir"]) if "res_is_dir" in keys else False,
            indexed_at=str(row["res_indexed_at"]) if row["res_indexed_at"] else None,
            fingerprint=resources.Fingerprint(
                mtime=float(row["res_mtime"]) if row["res_mtime"] is not None else None,
                bytes=int(row["res_bytes"]) if row["res_bytes"] is not None else None,
                digest=str(row["res_digest"]) if row["res_digest"] else None,
            ),
            marked_stale=bool(row["res_stale"]),
        )

    @staticmethod
    def _resource_from_row(row: sqlite3.Row) -> resources.Resource:
        return resources.Resource(
            id=int(row["id"]),
            reference=str(row["reference"]),
            origin=str(row["origin"]),
            index_it=bool(row["index_it"]),
            indexed_at=str(row["indexed_at"]) if row["indexed_at"] else None,
            fingerprint=resources.Fingerprint(
                mtime=float(row["mtime"]) if row["mtime"] is not None else None,
                bytes=int(row["bytes"]) if row["bytes"] is not None else None,
                digest=str(row["digest"]) if row["digest"] else None,
            ),
            marked_stale=bool(row["stale"]),
            # whole `resources` rows always carry it: the column ships with the table and is
            # added to older databases by the v8 step
            is_dir=bool(row["is_dir"]),
        )

    # -- folders and the files they bring in ---------------------------------------

    def link_resource(self, parent_id: int, child_id: int) -> bool:
        """Record that this file was found inside that folder. Returns whether it is new.

        A link is not ownership: the same file under two indexed folders has two links and
        one row, one mtime and one copy of its words.
        """
        if parent_id == child_id:
            return False
        before = self._rows(
            "SELECT 1 FROM resource_link WHERE parent_id = ? AND child_id = ?",
            (parent_id, child_id),
        )
        if before:
            return False
        self._execute(
            "INSERT INTO resource_link (parent_id, child_id) VALUES (?, ?)", (parent_id, child_id)
        )
        return True

    def linked_resources(self, parent_id: int) -> list[resources.Resource]:
        """The files a folder walk brought in, in address order."""
        return [
            self._resource_from_row(row)
            for row in self._rows(
                "SELECT r.* FROM resources r JOIN resource_link l ON l.child_id = r.id"
                " WHERE l.parent_id = ? ORDER BY r.ref_key",
                (parent_id,),
            )
        ]

    def unlink_resource(self, parent_id: int, child_id: int) -> None:
        self._execute(
            "DELETE FROM resource_link WHERE parent_id = ? AND child_id = ?",
            (parent_id, child_id),
        )

    def release_links(self, parent_id: int, *, keep_pointed_at: bool = True) -> int:
        """Let go of every file a folder brought in, and return how many went away.

        A file another box points at is kept — the folder was not the only reason it was
        here — and so is a file some other indexed folder still claims. What is left over
        after a folder stops being indexed is nothing but a copy of the past.
        """
        gone = 0
        for child in self.linked_resources(parent_id):
            if keep_pointed_at and self._rows(
                "SELECT 1 FROM boxes WHERE resource_id = ? LIMIT 1", (child.id,)
            ):
                self.unlink_resource(parent_id, child.id)
                continue
            self.unlink_resource(parent_id, child.id)
            if self.drop_resource_if_alone(child.id):
                gone += 1
        return gone

    def drop_resource_if_alone(self, resource_id: int) -> bool:
        """Delete a resource row, and its words, if nothing needs them any more.

        'Nothing needs them' is two questions, and both have to be no: no box points at it,
        and no folder still claims to contain it. A file taken out of one of two indexed
        folders keeps its index because the other folder is still holding it up; a file
        deleted from the only folder that knew it leaves nothing behind, which is what makes
        a folder's index follow the folder rather than accumulating ghosts.
        """
        claimed = self._rows(
            """
            SELECT 1 FROM boxes WHERE resource_id = ?
            UNION ALL
            SELECT 1 FROM resource_link WHERE child_id = ?
            LIMIT 1
            """,
            (resource_id, resource_id),
        )
        if claimed:
            return False
        self._execute(_DELETE_DOC, (resource_id,))
        self._execute("DELETE FROM resource_link WHERE parent_id = ?", (resource_id,))
        self._execute("DELETE FROM resources WHERE id = ?", (resource_id,))
        return True

    def resource_holds_words(self, resource_id: int) -> bool:
        """Whether the index is holding any text for this resource right now."""
        return bool(
            self._rows("SELECT 1 FROM resource_text WHERE resource_id = ? LIMIT 1", (resource_id,))
        )

    def unreadable_references(self, *, limit: int = 3) -> list[str]:
        """Addresses marked for indexing that hold no words, and are not folders.

        A count of these is worth showing; the count alone is a riddle. The empty-file case
        is in here too, which is why the sentence about them says "nothing to read" rather
        than "failed": a 0-byte note is not an error, it is a note with nothing in it.
        """
        return [
            str(row["reference"])
            for row in self._rows(
                """
                SELECT reference FROM resources r
                 WHERE index_it = 1 AND is_dir = 0 AND indexed_at IS NOT NULL
                   AND NOT EXISTS (SELECT 1 FROM resource_text t WHERE t.resource_id = r.id)
                 ORDER BY reference
                 LIMIT ?
                """,
                (limit,),
            )
        ]

    def index_counts(self) -> dict[str, int]:
        """How much of the index is there, how much is wanted, how much is out of date.

        One query and no `stat`: this is asked by the page every time it draws the side
        panel, so it reads the flags already stored rather than looking at the files. The
        numbers are therefore as fresh as the last pass — which is why the panel says
        "out of date" from what the database was told, and the action that indexes updates
        them by looking properly.
        """
        rows = self._rows(
            """
            SELECT
                SUM(index_it)                                   AS wanted,
                SUM(is_dir)                                     AS folders,
                SUM(CASE WHEN index_it = 1 AND indexed_at IS NOT NULL
                          AND EXISTS (SELECT 1 FROM resource_text t
                                       WHERE t.resource_id = resources.id)
                         THEN 1 ELSE 0 END)                     AS with_words,
                SUM(CASE WHEN index_it = 1 AND indexed_at IS NOT NULL
                          AND is_dir = 0
                          AND NOT EXISTS (SELECT 1 FROM resource_text t
                                       WHERE t.resource_id = resources.id)
                         THEN 1 ELSE 0 END)                     AS unreadable,
                SUM(CASE WHEN index_it = 1 AND indexed_at IS NULL
                         THEN 1 ELSE 0 END)                     AS untouched,
                SUM(CASE WHEN index_it = 1 AND stale = 1
                         THEN 1 ELSE 0 END)                     AS stale
              FROM resources
            """
        )
        if not rows:
            return {
                "wanted": 0,
                "indexed": 0,
                "outstanding": 0,
                "stale": 0,
                "folders": 0,
                "unreadable": 0,
            }
        counts = rows[0]

        def number(key: str) -> int:
            # `SUM` over no rows is NULL, and over rows that are all zero it is 0: both
            # mean "none", and neither is a reason to hand back None to a page.
            value = counts[key]
            return int(value) if value is not None else 0

        wanted = number("wanted")
        # "indexed" means the index holds words for it. A folder that was walked, and a
        # file that could not be read, both have an `indexed_at` and neither has any text:
        # counting either as indexed is how a panel ends up saying everything is fine about
        # a library where two thirds of the files were never opened.
        with_words = number("with_words")
        unreadable = number("unreadable")
        return {
            "wanted": wanted,
            "indexed": with_words,
            # wanted, and the index does not hold what it is for: new, changed, unreadable
            "outstanding": max(wanted - with_words - number("folders"), 0),
            "stale": number("stale"),
            "folders": number("folders"),
            "unreadable": unreadable,
            "untouched": number("untouched"),
        }

    def indexed_resources(self) -> list[resources.Resource]:
        """Every resource the index is being kept up to date for."""
        return [
            self._resource_from_row(row)
            for row in self._rows("SELECT * FROM resources WHERE index_it = 1 ORDER BY reference")
        ]

    def set_index_flag(
        self, ref: int | str | os.PathLike[str] | Box, index_it: bool
    ) -> resources.Resource | None:
        """Opt a resource in or out of the full-text index, and act on it.

        A `Box` is accepted because that is what the interfaces hold; a box is not an
        address, and passing one where an address is meant would look up a resource by
        the box's uuid and find nothing — which is a silent no, not an error.


        Opting out takes its text out of the index at once, so a search cannot go on
        finding a file somebody asked not to read; opting in is answered by the caller
        reading it in, which is what `boxganizer.search` does.
        """
        found = self.resource(ref)
        if found is None:
            return None
        self._execute(
            "UPDATE resources SET index_it = ?, stale = 0 WHERE id = ?",
            (int(bool(index_it)), found.id),
        )
        if not index_it:
            self._execute(_DELETE_DOC, (found.id,))
            self._execute(
                "UPDATE resources SET indexed_at = NULL, mtime = NULL, bytes = NULL,"
                " digest = NULL WHERE id = ?",
                (found.id,),
            )
            if found.is_dir:
                # A folder that is no longer indexed holds nothing the index should keep:
                # the files it brought in were only ever there because it was. Done here
                # rather than on the next pass, because the next pass looks at resources
                # that want indexing, and this one has just stopped wanting it — a folder
                # whose children linger until something happens to notice is a slow leak of
                # words nobody asked to search.
                self.release_links(found.id, keep_pointed_at=True)
        return self.resource(found.id)

    def record_index(
        self,
        resource_id: int,
        *,
        body: str | None,
        seen: resources.Fingerprint | None = None,
    ) -> None:
        """What the index now holds for one resource, and what the file looked like.

        The observations are written by whoever read the file, because only they know
        whether the read succeeded: `body=None` says there is nothing to hold, which is
        recorded too so a later run does not mistake an unreadable file for an unread one.
        """
        self._execute(_DELETE_DOC, (resource_id,))
        if body and body.strip():
            self._execute(_INSERT_DOC, (resource_id, body))
        watch = seen or resources.Fingerprint()
        self._execute(
            "UPDATE resources SET indexed_at = ?, mtime = ?, bytes = ?, digest = ?,"
            " stale = 0 WHERE id = ?",
            (
                _now(),
                watch.mtime,
                watch.bytes,
                watch.digest,
                resource_id,
            ),
        )

    def check_observations(self) -> list[resources.Resource]:
        """Look at every indexed file and say which indexes have gone stale.

        One `stat` per resource, no reading: this is what `box index --check` runs, and
        what the page can ask before it offers "3 files changed since they were indexed".
        A file that has gone away counts as stale, because whatever is in the index is
        certainly not what is on disk.
        """
        stale: list[resources.Resource] = []
        for item in self.indexed_resources():
            seen = resources.stat(item.reference)
            changed = item.stale(now=seen) is True
            self._execute("UPDATE resources SET stale = ? WHERE id = ?", (int(changed), item.id))
            if changed:
                stale.append(
                    resources.Resource(
                        id=item.id,
                        reference=item.reference,
                        origin=item.origin,
                        index_it=item.index_it,
                        indexed_at=item.indexed_at,
                        fingerprint=item.fingerprint,
                        marked_stale=True,
                    )
                )
        return stale

    # -- the full-text index ------------------------------------------------
    #
    # The index is keyed by resource, not by box: the file has the words, and several
    # boxes may point at one file. Which boxes to show for a hit is answered by
    # `boxes_by_resources`, so the mapping back is the store's job too.

    def content_search(self, match: str, *, limit: int = 100) -> list[tuple[int, float, str]]:
        """Resources whose indexed text answers *match*, best first.

        Returns ``(resource_id, score, passage)``. A malformed expression — a stray
        quote in what someone typed — is not an error to show them: it answers with
        nothing, and the name search still runs.
        """
        try:
            rows = self._rows(_SEARCH_DOCS, (match, limit))
        except StoreError:
            return []
        return [(int(row["resource_id"]), float(row["score"]), str(row["passage"])) for row in rows]

    def boxes_for_text(self, resource_id: int) -> list[tuple[Box, str | None]]:
        """The boxes to answer with when this resource's words matched, and where they came from.

        Usually one box: the one that points at the file. A file inside an indexed folder is
        different — nothing in the library points at it, because what the person filed is
        the folder — so the folder answers, and says which file spoke. That pair is the
        difference between a hit you can open and a number in a passage you have to go and
        find by hand.

        The second half of each pair is the path *inside* the folder, or None when the box
        points at the file itself. Nested folders share names across subtrees, so a bare
        name would send someone to the wrong file.
        """
        answers: list[tuple[Box, str | None]] = [
            (box, None) for box in self.boxes_by_resources([resource_id]).get(resource_id, [])
        ]
        rows = self._rows("SELECT parent_id FROM resource_link WHERE child_id = ?", (resource_id,))
        if not rows:
            return answers
        child = self.resource(resource_id)
        for row in rows:
            parent_id = int(row["parent_id"])
            parent = self.resource(parent_id)
            for box in self.boxes_by_resources([parent_id]).get(parent_id, []):
                inside = child.reference if child is not None else ""
                if parent is not None and inside:
                    head = parent.reference.rstrip("/") + "/"
                    if inside.startswith(head):
                        inside = inside[len(head) :]
                answers.append((box, inside or None))
        return answers

    def boxes_by_resources(self, resource_ids: Sequence[int]) -> dict[int, list[Box]]:
        """The boxes pointing at each of these resources, in the order asked for.

        Several boxes may share one file, and a search that found the file found all of
        them; the passage is the same and so is the reason, which the page says once.
        """
        found: dict[int, list[Box]] = {}
        for resource_id in resource_ids:
            if resource_id in found:
                continue
            rows = self._rows(_SELECT_BOXES_BY_RESOURCE, (resource_id,))
            if rows:
                found[resource_id] = [self._from_row(row) for row in rows]
        return found

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

    def indexed_count(self) -> int:
        """How many resources have text in the index."""
        rows = self._rows(_COUNT_DOCS)
        return int(rows[0]["n"]) if rows else 0

    def _read_in(self, box: Box) -> None:
        """Index what this box points at, when that is what it is for.

        The reading itself belongs to :mod:`boxganizer.search`, which is imported here
        late and quietly: the store holds rows and does not open files, but every door
        into the database goes through `add` and `update`, and an index that has to be
        remembered is an index that goes stale. `search` imports no store of its own, so
        there is no cycle to trip over.
        """
        if box.resource_id is None:
            return
        from boxganizer import search as _search

        _search.sync_resource(self, box.resource_id, root=self.content_root)

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
            f"{_BOX_COLUMNS} {_BOX_FROM}"
            " JOIN links slot ON slot.child_id = b.id"
            " JOIN boxes way_in ON way_in.id = slot.parent_id"
            " WHERE way_in.box_type = 'root'"
            " ORDER BY slot.added_at DESC, b.key LIMIT 1"
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
        # The resources table comes before the folders are re-cut: a filter can ask for
        # an address, and reading every box with its address still in a column that is
        # about to disappear would resolve the membership of each folder against
        # nothing. This is why the v7 step sits above the v5 one rather than under it.
        if stored_version < 8:
            # The table itself arrives with the schema scripts, which run before this.
            columns = {str(row["name"]) for row in self._rows("PRAGMA table_info(resources)")}
            if "is_dir" not in columns:
                self._execute("ALTER TABLE resources ADD COLUMN is_dir INTEGER NOT NULL DEFAULT 0")
            # Rows written before the column existed are not all files, and a folder left
            # believing it is a file is counted as a read that failed forever. One `stat`
            # per row, once, on the way up: cheaper than a panel that lies afterwards.
            local = self._rows("SELECT id, reference FROM resources WHERE origin = 'local'")
            for row in local:
                if resources.is_directory(str(row["reference"])):
                    self._execute("UPDATE resources SET is_dir = 1 WHERE id = ?", (row["id"],))
        if stored_version < 7:
            self._split_resources_out()
        if stored_version < 5:
            self._resolve_dynamic_folders()

    def _split_resources_out(self) -> None:
        """Move every box's address into `resources`, and the index onto it, once.

        A library written before v7 has the address in a column of the box, and one box
        after another can hold the same path in four spellings. So each distinct address
        becomes a row — its default for indexing taken from the type of a box pointing at
        it — and the box is left pointing at the row. Then the full-text entries, which
        were keyed by box uuid, are re-keyed onto the resource the box now names; the
        words are carried across rather than dropped, because an index that empties on
        upgrade is not a migration but a loss, and refilling it means reading every file
        again.

        `boxes.resource_uri` is dropped at the end, so there is no second copy of an
        address left to disagree with the first.
        """
        columns = {str(row["name"]) for row in self._rows("PRAGMA table_info(boxes)")}
        if "resource_id" not in columns:
            self._execute(
                "ALTER TABLE boxes ADD COLUMN resource_id INTEGER REFERENCES resources (id)"
            )
        if "resource_uri" in columns:
            rows = self._rows(
                "SELECT id, box_type, resource_uri FROM boxes"
                " WHERE resource_uri IS NOT NULL AND resource_uri <> ''"
            )
            for row in rows:
                box_type = str(row["box_type"] or "box")
                kind = boxtypes.get(box_type) if boxtypes.known(box_type) else None
                resource_id = self._ensure_resource(
                    str(row["resource_uri"]),
                    indexes_content=bool(kind and kind.indexes_content),
                )
                self._execute(
                    "UPDATE boxes SET resource_id = ? WHERE id = ?",
                    (resource_id, int(row["id"])),
                )
        self._carry_the_index_across()
        if "resource_uri" in columns:
            self._execute("ALTER TABLE boxes DROP COLUMN resource_uri")

    def _carry_the_index_across(self) -> None:
        """Re-key `box_text` onto resources, then take the old table out.

        Two boxes that shared a file shared nothing before v7 and share one row now: the
        duplicate text is dropped on the way, since the same words indexed twice only
        make a search answer twice.
        """
        tables = {
            str(row["name"])
            for row in self._rows("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        if "box_text" not in tables:
            return
        carried: set[int] = set()
        for row in self._rows("SELECT box_id, body FROM box_text"):
            where = self._rows(
                "SELECT resource_id FROM boxes WHERE uuid = ? AND resource_id IS NOT NULL",
                (str(row["box_id"]),),
            )
            if not where or where[0]["resource_id"] is None:
                continue  # the box is gone, or never pointed at a thing worth indexing
            resource_id = int(where[0]["resource_id"])
            if resource_id in carried:
                continue
            carried.add(resource_id)
            if str(row["body"] or "").strip():
                self._execute(_INSERT_DOC, (resource_id, row["body"]))
        # A shadow table is dropped with its index; the vocab table is named first only
        # because SQLite leaves it behind if the index it reads is taken away.
        if "box_text_vocab" in tables:
            self._execute("DROP TABLE IF EXISTS box_text_vocab")
        self._execute("DROP TABLE IF EXISTS box_text")

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
            resource_id=int(row["resource_id"]) if row["resource_id"] is not None else None,
            resource=BoxStore._resource_from_joined(row),
            metadata={str(k): str(v) for k, v in metadata.items()},
            config=dict(config),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )


def seed(store: BoxStore, boxes: Iterable[Box]) -> list[Box]:
    """Add many boxes at once; return the stored copies."""
    return [store.add(box) for box in boxes]
