"""Files Boxganizer writes: where a note or a checklist actually lives.

A checklist box and a note box have no file to point at when they are made — the whole
point is that Boxganizer holds the words. But "holds" has to mean something in a library
whose promise is that everything is a box pointing at a thing, and the answer the items
ask for is the same answer as everything else: a file, on disk, with a resource row
pointing at it, so it is in the index and searchable by what it says.

So these files exist, in one directory beside the database:

    ~/.local/share/boxganizer/
        boxes.db
        files/
            2f6c…c19.md      ← the checklist box with that uuid
            7ba4…8e0.md      ← the note box with that uuid

Three things follow from the shape, and they are the reason it is this shape.

**The file is named after the box's uuid, not its name.** A note called *Shopping* is
renamed every other day; a file named after it would have to move, which means the
resource row would have to change, which means the index entry — written under the old
path — would go on answering searches for a file that is no longer there. The uuid does
not move, so nothing has to be updated, and the indexed words stay true.

**It is a plain file with a plain format.** A checklist is markdown task syntax, a note
is markdown or text. Anything that edits them works, `grep` works, a backup is a copy of
a directory, and a library whose database is lost still has its notes in it.

**The origin of such a resource is `generated`, not `local`.** It is a file on this
machine, but one Boxganizer wrote, and that distinction earns its keep in one place: the
content confine. `$BOXGANIZER_CONTENT_ROOT` says which files the machine is allowed to
read, and it is routinely set to one project directory — which would put the notes
directory outside it, and quietly make every note in the library unindexable and every
checklist unreadable on the page. Boxganizer's own files are therefore read on their own
terms, and a path someone else typed into a box still has to clear the confine.

The directory is created when the first such box is written, not when the library is:
most libraries will never hold a note, and an empty `files/` next to the database is
clutter that explains nothing.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path
from typing import Any

#: The directory's name, beside the database file.
DIRECTORY = "files"

#: Where an in-memory library's own files go. A database that exists only in RAM has no
#: directory beside it, and writing into the current working directory because the path
#: happened to be `:memory:` would be a surprise in the worst place: a test, or a
#: `--db :memory:` demo. So they go somewhere clearly temporary, and disappear with the
#: process, which is exactly the lifetime of the library they belong to.
MEMORY_DIRECTORY = Path(tempfile.gettempdir()) / f"boxganizer-files-{os.getpid()}"

#: What these files are written as. Markdown, because a note and a checklist are both
#: readable that way by other programs, and because the renderer is already here.
SUFFIX = ".md"

#: A note or checklist with nothing in it is still a file, and a file with a heading that
#: says what it is beats a zero-byte one when someone opens the directory by hand.
EMPTY_CHECKLIST = "---\n---\n\n"
EMPTY_NOTE = ""


def directory_for(db_path: str | Path) -> Path:
    """The directory these files live in, for a given database: beside it, named `files`.

    An in-memory database has no beside, so it gets a temporary directory instead of one
    inferred from the string `:memory:` — see :data:`MEMORY_DIRECTORY`.
    """
    where = Path(db_path).expanduser()
    if str(where) == ":memory:" or str(where.parent) == ".":
        return MEMORY_DIRECTORY
    return where.parent / DIRECTORY


def path_for(db_path: str | Path, box_uuid: str, *, title: str | None = None) -> Path:
    """Where this box's own words live.

    Named by uuid and nothing else: see the module docstring. The title is not in the
    name — it is the first line inside, where renaming the box cannot strand it.
    """
    cleaned = str(box_uuid).strip()
    if not cleaned:
        raise ValueError("a box that owns a file needs its uuid")
    return directory_for(db_path) / f"{cleaned}{SUFFIX}"


def ensure_directory(db_path: str | Path) -> Path:
    """Create the directory if it is not there, and return it.

    Owner-only permissions: the notes directory holds whatever people wrote in their
    notes, and a directory readable by the whole machine is not what anyone means by a
    personal library. Existing directories are left as their owner set them.
    """
    where = directory_for(db_path)
    where.mkdir(parents=True, exist_ok=True)
    # Owner-only: the directory holds whatever people wrote in their notes. An existing
    # directory is left as its owner set it, and a filesystem with no permission bits
    # (a mount, a test tmp) is not a reason to complain.
    with contextlib.suppress(OSError):
        where.chmod(0o700)
    return where


def ensure_file(db_path: str | Path, box_uuid: str, *, seed: str = EMPTY_NOTE) -> Path:
    """The box's file, written with `seed` if it does not exist yet.

    Existing content is never overwritten: a file that has been edited outside Boxganizer
    is the more recent truth, and opening a note must not be able to empty it.
    """
    target = path_for(db_path, box_uuid)
    ensure_directory(db_path)
    if not target.exists():
        _write(target, seed)
    return target


def read(target: Path) -> str:
    """What the file says now, or nothing when it is not there to be read."""
    try:
        return target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def write(db_path: str | Path, box_uuid: str, text: str) -> Path:
    """Replace what the box's file says, and leave the file readable.

    Written through a temporary file in the same directory, then moved into place, so a
    crash mid-write cannot leave half a note where a whole one was: either the old file
    is there or the new one is.
    """
    target = ensure_file(db_path, box_uuid)
    _write(target, text)
    return target


def _write(target: Path, text: str) -> None:
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    with contextlib.suppress(OSError):
        temporary.chmod(0o600)
    os.replace(temporary, target)


def is_authored(store: Any, box: Any) -> bool:
    """Whether this box's file is one Boxganizer wrote, rather than one it points at.

    Asked of the resource row's origin, not of the path: a person may well point an
    ordinary file box at the notes directory, and that is a file they own, with the
    confine rules that apply to files they own.
    """
    record = getattr(box, "resource", None)
    return bool(record is not None and getattr(record, "origin", "") == "generated")


def adopt(store: Any, box: Any, target: Path, *, seed: str = EMPTY_NOTE) -> Any:
    """Give a box a file of its own, and register it the way any other resource is.

    Returns the resource row, whose origin is `generated` — which is what lets the
    indexer read it from outside the content confine, and what tells `box show` this file
    belongs to the box rather than merely being where the box pointed.
    """
    from boxganizer import resources as address

    if not target.exists():
        _write(target, seed)
    return store.register_resource(str(target), index_it=True, origin=address.GENERATED)
