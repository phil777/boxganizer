"""Resources: what a box points at, kept once so it can be watched once.

A box no longer holds its address in a text column of its own. It holds
``resource_id``, a pointer to a row in the ``resources`` table, and that row carries the
reference (a path, a URL, an ``mqtt://`` topic) together with the things that are true
of the *thing pointed at* rather than of the box: where it lives, whether its contents
are worth putting in the full-text index, and what was last seen of it — the size and
modification time of a file, the hash of the text that was read out of it, and whether
that still matches.

Why this is a table and not a column:

* one file, one index. Two boxes pointing at the same PDF should not mean the PDF is
  read twice, and re-reading it once refreshes both.
* a box may be renamed, retyped or deleted while the file goes on living; the file's
  own record should not be rewritten every time, nor lost when the last box referring
  to it is deleted — the file is still on disk for the next box to point at.
* reindexing needs a place to remember what it saw. A column on the box cannot answer
  "has this changed", because it is overwritten by every edit of the box.

The reference is the identity: the same path or URL written by two boxes resolves to
one row. Comparison is on a normalised form — a local path made absolute without
symlinks followed, a URL lowercased in its scheme and host — because the same file is
routinely spelled three ways.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Where the thing lives. This is the question the indexer asks first: a file on this
#: machine can be opened, hashed and watched; anything else has to be fetched, and
#: fetching it is the business of the type that knows how.
LOCAL = "local"
REMOTE = "remote"

#: A local file Boxganizer wrote itself — a checklist or a note, kept beside the
#: database. Local, and therefore watchable, but with a life tied to its box.
GENERATED = "generated"

ORIGINS = (LOCAL, REMOTE, GENERATED)

#: Schemes that name something on another machine. Anything without a scheme, and
#: ``file:``, is a path here.
_REMOTE_SCHEMES = frozenset(
    {"http", "https", "ws", "wss", "ftp", "sftp", "ssh", "mqtt", "mqtts", "vnc", "git", "mailto"}
)

#: File endings whose text is worth reading when nobody has said otherwise. The list is
#: the *default* for a new resource row and nothing more: the checkbox in the dialog
#: overrides it, because a person with a directory of ``.log`` files knows better than
#: an extension table.
INDEXABLE_SUFFIXES = frozenset(
    {
        ".txt",
        ".md",
        ".markdown",
        ".rst",
        ".adoc",
        ".csv",
        ".tsv",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".cfg",
        ".conf",
        ".html",
        ".htm",
        ".xml",
        ".log",
        ".pdf",
        ".py",
        ".js",
        ".ts",
        ".css",
        ".sql",
        ".sh",
    }
)


def normalise(reference: str) -> str:
    """The form two references are compared on.

    A path is made absolute — without following symlinks, which would tie a resource's
    identity to wherever the link happens to point this month. A URL has its host folded
    and its trailing slash dropped, because `https://X/` and `https://x` name one page,
    and nothing else is touched: the path of a URL is case-sensitive and its query means
    something.
    """
    raw = (reference or "").strip()
    if not raw:
        return ""
    scheme, sep, rest = raw.partition("://")
    if sep and scheme.lower() in _REMOTE_SCHEMES:
        authority, _split, tail = rest.partition("/")
        userinfo, at, hostname = authority.rpartition("@")
        # The host has no case; who is logging in does, so only half of the userinfo
        # line is folded.
        host = hostname.lower().rstrip("/")  # a trailing slash on a host is not a path
        head = f"{userinfo}@{host}" if at else host
        if not tail:
            return f"{scheme.lower()}://{head}"
        # A path keeps its own case and its own trailing slash: `/a` and `/a/` are two
        # URLs as far as a web server is concerned.
        return f"{scheme.lower()}://{head}/{tail}"
    if sep and scheme.lower() == "file":
        raw = rest if rest.startswith("/") else f"/{rest}"
    expanded = os.path.expandvars(os.path.expanduser(raw))
    return os.path.normpath(os.path.abspath(expanded))


def origin_of(reference: str) -> str:
    """Local path, or something elsewhere."""
    scheme, sep, _rest = (reference or "").partition("://")
    if sep and scheme.lower() in _REMOTE_SCHEMES:
        return REMOTE
    return LOCAL


def index_by_default(reference: str, *, origin: str | None = None) -> bool:
    """Whether a brand-new resource is worth reading into the index.

    A file whose ending says words; anything else, no. A web page is a fetch and not a
    file, an MQTT topic is a value and not a document, and reading either is the type's
    business, not the indexer's.
    """
    where = origin or origin_of(reference)
    if where == REMOTE:
        return False
    # `generated` (a note, a checklist) is a local file by any sensible default, and the
    # ending decides the rest.
    path = Path(normalise(reference))
    return path.suffix.lower() in INDEXABLE_SUFFIXES


def display(reference: str) -> str:
    """What to call this resource in a line of terminal output.

    The last part of a path rather than the whole of it: a report of forty unread files,
    each prefixed with `/home/someone/documents/invoices/2019/`, is unreadable in a
    different way. A remote thing has no last part worth showing on its own, so it keeps
    its address.
    """
    if origin_of(reference) != LOCAL:
        return reference.strip()
    name = Path(normalise(reference)).name
    return name or reference.strip()


def why_indexed(reference: str, *, indexes_content: bool = False) -> tuple[bool, str]:
    """Whether to read this, and the sentence that explains the answer.

    The page shows the reason beside the checkbox, because a box that is silently left
    out of the search is indistinguishable from a search that is broken.
    """
    if indexes_content:
        return True, "this kind of box holds words, so its file is read into the index"
    where = origin_of(reference)
    if where == REMOTE:
        return False, "a remote thing is fetched by the box that points at it, not indexed"
    if not (reference or "").strip():
        return False, "there is nothing to read yet"
    if index_by_default(reference, origin=where):
        return True, "a file whose ending says words"
    suffix = Path(normalise(reference)).suffix.lower() or "has no ending"
    return False, f"a {suffix} file is not words, so it is left out unless asked for"


@dataclass(frozen=True)
class Fingerprint:
    """What a local file looked like at the moment it was last read.

    ``mtime`` and ``bytes`` answer "should I read it again" cheaply, from a stat.
    ``digest`` is the hash of the text that was actually put in the index, so a file
    that was touched without changing, or changed back to what it was, is not
    needlessly read twice more.
    """

    mtime: float | None = None
    bytes: int | None = None
    digest: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"mtime": self.mtime, "bytes": self.bytes, "digest": self.digest}

    @property
    def empty(self) -> bool:
        return self.mtime is None and self.bytes is None and self.digest is None


def stat(reference: str) -> Fingerprint:
    """The present state of a local file, or an empty fingerprint for anything else.

    Nothing here raises: a file that has gone away has no mtime, and "there is nothing
    to read" is an observation rather than an error. The caller compares what comes back
    with what the index was built from.
    """
    if origin_of(reference) != LOCAL:
        return Fingerprint()
    try:
        info = Path(normalise(reference)).stat()
    except OSError:
        return Fingerprint()
    return Fingerprint(mtime=info.st_mtime, bytes=info.st_size)


def digest(text: str) -> str:
    """A short hash of what was read, kept so an unchanged file is not re-read."""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:32]


@dataclass(frozen=True)
class Resource:
    """One row of the ``resources`` table, as the rest of the code wants it."""

    id: int
    reference: str
    origin: str = LOCAL
    index_it: bool = False
    indexed_at: str | None = None
    fingerprint: Fingerprint = Fingerprint()
    #: Something already noticed: a file edited outside Boxganizer, found by
    #: `store.check_observations`. Asking `stale()` is still better than trusting it,
    #: because the file may have been fixed or deleted since, but the flag is what a
    #: list of "3 files changed" is built from without stat-ing the disk.
    marked_stale: bool = False

    @property
    def indexed(self) -> bool:
        """Whether anything has ever been read out of this into the index."""
        return self.indexed_at is not None

    def stale(self, *, now: Fingerprint | None = None) -> bool | None:
        """Whether the index no longer says what the file says.

        ``None`` when the question cannot be asked — a remote resource is not watched
        here, and a file nobody chose to index is not stale, it is simply not indexed.
        ``True`` covers a file that has gone away: what is in the index is certainly
        not what is on disk.
        """
        # A file Boxganizer wrote is watched exactly like one it merely points at: it is
        # on disk, someone may edit it in a text editor, and the index should notice.
        if self.origin not in (LOCAL, GENERATED) or not self.index_it:
            return None
        if self.marked_stale and not self.indexed:
            return None
        seen = now or stat(self.reference)
        if seen.empty:
            return True
        return seen.mtime != self.fingerprint.mtime or seen.bytes != self.fingerprint.bytes

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "reference": self.reference,
            "origin": self.origin,
            "index_it": self.index_it,
            "indexed_at": self.indexed_at,
            "fingerprint": self.fingerprint.as_dict(),
            "stale": self.stale(),
            "marked_stale": self.marked_stale,
        }
