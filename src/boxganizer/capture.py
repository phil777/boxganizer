"""Capturing a resource: copy its bytes beside the database, and name them by what they contain.

A box normally *points* at a file. That is honest — the file is the thing, and Boxganizer is a card
index to it — but it means the box dies when the file moves, the disk is reformatted, or the
laptop the
path names is gone. **Capture** is the other choice: take a copy into the library's own files and
keep it forever, whatever happens out there. It is offered per box and off by default, because a
library that silently swallows copies of everything on the disk is a backup tool wearing a
librarian's clothes.

Where the copies live
---------------------

::

    ~/.local/share/boxganizer/
        boxes.db
        files/            <- Boxganizer's own directory (authored.py: notes and checklists)
            3f/9a/
                3f9a4c...b7.pdf
            01/d2/
                01d2e8...19.md

The same directory as the boxes' own words (``authored.directory_for``), because both are "files
Boxganizer keeps", and a reader backing up one obviously needs to back up the other. Inside it the
copies are **bucketed by the first four hex digits of their hash** — two levels of two — because a
single directory holding every captured file turns ``ls``, backup tools and the filesystem's own
lookup into the slow thing, and 65536 buckets means a bucket only gets crowded in a library with
hundreds of thousands of files.

The name is the hash
--------------------

The file is named ``<sha256 of its bytes><original extension>``. Two things fall out of that:

* **Deduplication is the filename.** Capturing the same bytes twice lands on the same path, so the
  second
  capture writes nothing and both boxes point at one file. No bookkeeping table, no reference
  counting to
  work out whether a copy already exists.
* **The file certifies itself.** Any time later, `verify` answers whether a file still contains what
  its name claims. That is what lets deletion move a captured file to a trash folder (it *is* the
  library's copy, provably) and what lets a report say "this one was changed by hand" instead of
  quietly trusting the name.

The extension is kept on purpose. Type inference, MIME guesses and "open this in an editor" all read
it, and dropping it would make a captured PDF indistinguishable from a captured text file.

"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from boxganizer import authored, paths

#: How many hex digits name each level of the bucket. Four gives 65536 top-level buckets, so a
#: directory in a real library stays small enough for a human to read in a terminal.
BUCKET_LEVEL = 2

#: A digest is hex and 64 long; nothing else is mistaken for a captured file.
HEX64 = re.compile(r"^[0-9a-f]{64}$")

#: Read in chunks: a captured video is not held in memory to be hashed.
CHUNK = 1024 * 1024


class CaptureError(ValueError):
    """The bytes could not be captured. The message is written to be shown, not logged."""


@dataclass(frozen=True)
class Capture:
    """What capturing did: where the bytes are now, and whether that required writing them."""

    path: Path
    digest: str
    #: The file was already there, byte for byte. Nothing was written; this box points at it too.
    reused: bool
    size: int

    @property
    def reference(self) -> str:
        """The address to store on the box — the captured file, in place of the original."""
        return str(self.path)


def files_dir_for(db_path: str | Path) -> Path:
    """Where this library keeps its own files. The one rule, shared with notes and checklists.

    Captured resources and a box's own words live under one directory: "back up my library" is one
    instruction, and a moved database takes both families with it without a config entry.
    """
    return authored.directory_for(db_path)


def digest_of(source: Path) -> str:
    """The sha256 of a file's bytes, read in chunks so a large file costs a large buffer nowhere."""
    digest = hashlib.sha256()
    try:
        with source.open("rb") as handle:
            for block in iter(lambda: handle.read(CHUNK), b""):
                digest.update(block)
    except OSError as exc:
        raise CaptureError(f"cannot read {source} to capture it: {exc}") from exc
    return digest.hexdigest()


def bucket_of(digest: str) -> tuple[str, str]:
    """The two directory names a digest lives under."""
    return digest[:BUCKET_LEVEL], digest[BUCKET_LEVEL : BUCKET_LEVEL * 2]


def suffix_of(reference: str) -> str:
    """The original extension, kept so the file still says what kind of thing it is.

    Stripped of anything path-shaped: an extension is `.pdf`, not `../../etc/passwd`. A name with no
    dot keeps nothing, which is correct — the bytes have no declared type to remember.
    """
    name = Path(paths.local_path(reference)).name
    dot = name.rfind(".")
    if dot <= 0:
        return ""
    tail = name[dot:]
    return tail if re.fullmatch(r"\.[A-Za-z0-9]{1,12}", tail) else ""


def target_for(files_dir: Path, digest: str, suffix: str = "") -> Path:
    """The path the bytes belong at for this digest — whether or not they are there yet."""
    first, second = bucket_of(digest)
    return files_dir / first / second / f"{digest}{suffix}"


def chmod_owner_only(where: Path) -> None:
    """Restrict a directory to its owner, where the filesystem understands the idea."""
    os.chmod(where, 0o700)


def ensure_files_dir(files_dir: Path) -> Path:
    """Create the capture directory (and a bucket) with owner-only permissions.

    Same reasoning as `authored.ensure_directory`: this holds whatever people kept, and a directory
    readable by the whole machine is not what anyone means by a personal library. An existing
    directory
    is left as its owner set it.
    """
    files_dir.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        chmod_owner_only(files_dir)  # a filesystem with no permission bits is left as it is
    return files_dir


def is_captured(path: str | Path, *, files_dir: Path) -> bool:
    """Whether a path has the shape of a captured file inside this library.

    Deliberately shape-only, and deliberately not a database lookup: a captured file is identified
    by
    living under ``files/<2 hex>/<2 hex>/<64 hex><ext>``: cheap to ask, and it survives a box having
    been deleted around it, because the question trash answers is about the *file*. Use `verify` for
    the stronger claim that the bytes still match the name.
    """
    candidate = Path(path)
    try:
        relative = candidate.resolve().relative_to(files_dir.resolve())
    except (ValueError, OSError):
        return False
    parts = relative.parts
    if len(parts) != BUCKET_LEVEL + 1:
        return False
    levels, filename = parts[:-1], parts[-1]
    if any(len(part) != BUCKET_LEVEL or not re.fullmatch(r"[0-9a-f]{2}", part) for part in levels):
        return False
    return bool(HEX64.match(filename.split(".")[0]))


def verify(path: Path) -> bool:
    """Whether a captured file still contains the bytes its name promises.

    A name that no longer matches its content means someone replaced the file by hand. Nothing
    should
    overwrite that or move it away on the strength of the name alone, so the name is checked
    before it is
    trusted.
    """
    if not path.is_file():
        return False
    return digest_of(path) == path.name.split(".")[0]


def capture(reference: str, *, files_dir: Path) -> Capture:
    """Copy what *reference* points at into the library, and report where it landed.

    Identical bytes land on the same path, so capturing twice writes once: the second call finds
    the file
    and reports ``reused=True``. The copy goes to a temporary file in the destination bucket and is
    then moved into place: a half-written file named by a hash it cannot match is worse than
    no copy
    at all — it would be a file that lies about what it is.

    Nothing here consults the confine. ADR 30 gates *writes to the library* and the browsing of
    folders;
    capture is a person naming one file and asking for a copy of it, which is a request to read.
    """

    where = _local_file(reference)
    digest = digest_of(where)
    destination = target_for(files_dir, digest, suffix_of(reference))

    if destination.exists():
        if not verify(destination):
            # These are not the bytes this name promises: somebody edited the store's own copy.
            # Overwriting would destroy their work on the strength of a coincidence, and quietly
            # renaming ours would hide that the library has been tampered with.
            raise CaptureError(
                f"{destination} is named for a hash it no longer holds — it was changed by hand. "
                "Nothing was copied over it; repair or move that file, then capture again."
            )
        size = destination.stat().st_size
        return Capture(path=destination, digest=digest, reused=True, size=size)

    ensure_files_dir(files_dir)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        chmod_owner_only(destination.parent)

    # `mkstemp`, not a predictable name: two boxes capturing the same bytes at the same moment are
    # two writers, and they must not each open the other's temp file.
    handle, temp_name = tempfile.mkstemp(dir=destination.parent, prefix=".capture-")
    temp = Path(temp_name)
    try:
        # mkstemp hands back a descriptor; fdopen owns it from here and closes it with the file.
        try:
            sink = os.fdopen(handle, "wb")
        except OSError:
            os.close(handle)
            raise
        with sink, where.open("rb") as reader:
            shutil.copyfileobj(reader, sink, CHUNK)
        os.replace(temp, destination)
    except (OSError, ValueError) as exc:
        temp.unlink(missing_ok=True)
        raise CaptureError(f"could not capture {where} into {destination}: {exc}") from exc
    with contextlib.suppress(OSError):
        os.chmod(destination, 0o600)
    return Capture(path=destination, digest=digest, reused=False, size=destination.stat().st_size)


def _local_file(reference: str) -> Path:
    """The local file a resource address names, or a refusal explaining why it is not one."""

    raw = (reference or "").strip()
    if not raw:
        raise CaptureError("capture needs a file to copy; this box points at nothing")
    scheme, _ = paths.split_resource(raw)
    if scheme and scheme.lower() != "file":
        raise CaptureError(
            f"{raw} is not a local file ({scheme}:), and capture copies bytes it can read. "
            "Capture is offered for files on this machine."
        )
    try:
        target = paths.local_path(raw)
    except (ValueError, OSError) as exc:  # an address this machine cannot resolve
        raise CaptureError(f"cannot make a local path out of {raw}: {exc}") from exc
    if target.is_dir():
        raise CaptureError(
            f"{target} is a folder. Capture copies one file; index the folder instead, "
            "or capture the files inside it."
        )
    if not target.exists():
        raise CaptureError(f"there is nothing at {target} to capture")
    return target


# -- retiring a captured copy -------------------------------------------------
#
# A captured file is the library's own, which raises a question the pointer boxes never
# ask: what happens when the last box pointing at it is deleted? Deleting it would be the
# one way Boxganizer destroys content nobody told it to destroy — a person deletes a box
# on Tuesday and the copy they made on Monday is gone. Leaving it forever turns `files/`
# into a graveyard with no way out. So it moves to a **trash folder beside `files/`**, at
# the same bucket path it had, where it is still a file someone can find and move back —
# and `box trash empty` is the single act that really removes it.

TRASH_DIRECTORY = "trash"


@dataclass(frozen=True)
class Retirement:
    """What retiring a captured file actually did, in words a front end can repeat."""

    #: The file now sitting in the trash, when one was moved.
    moved: Path | None
    #: Why nothing moved: not the library's copy, or a name that no longer matches its bytes.
    reason: str
    #: Bytes involved, for the report.
    size: int

    @property
    def happened(self) -> bool:
        return self.moved is not None


def trash_dir_for(db_path: str | Path, *, files_dir: Path | None = None) -> Path:
    """`trash/` beside the library's `files/` — a sibling, so neither lives inside the other."""
    own = files_dir or files_dir_for(db_path)
    return own.parent / TRASH_DIRECTORY


def retire(reference: str, *, files_dir: Path, trash_dir: Path) -> Retirement:
    """Move a captured copy out of `files/` and into the trash, keeping its bucket path.

    Call it only once the last box pointing at the file has gone. Two things are left exactly
    where they are, and said why:

    * a file that is **not** the library's copy — the ordinary case of a box pointing at
      somebody's document. Deleting the card does not touch the document; that is the whole
      design of the thing.
    * a captured file whose **name no longer matches its bytes**, because somebody edited it
      by hand. That file may now be the only copy of something, and moving it on the strength
      of a name that lies is how a library loses a document twice.

    A symlink is never followed and never moved: the trash must not become a route out of the
    library's own directory.
    """
    raw = (reference or "").strip()
    if not raw:
        return Retirement(moved=None, reason="nothing to retire", size=0)
    source = Path(raw)
    if source.is_symlink():
        return Retirement(moved=None, reason="a symlink was left where it is", size=0)
    if not is_captured(source, files_dir=files_dir):
        return Retirement(moved=None, reason="not a copy the library keeps", size=0)
    if not source.exists():
        return Retirement(moved=None, reason="the copy is already gone", size=0)
    if not verify(source):
        return Retirement(
            moved=None,
            size=_size(source),
            reason=(
                "was edited by hand, so it stays where it is — its name no longer says "
                "what it holds"
            ),
        )

    relative = source.resolve().relative_to(files_dir.resolve())
    destination = trash_dir / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        chmod_owner_only(destination.parent)

    if destination.exists():
        # Same path, same name, so the same digest: these two files hold identical bytes. The one
        # being retired is redundant and removing it loses nothing that is not provably elsewhere
        # — but it is said out loud, because "a file disappeared" deserves a sentence.
        source.unlink(missing_ok=True)
        return Retirement(
            moved=destination,
            reason="already in the trash — the identical copy went away",
            size=_size(destination),
        )
    try:
        os.replace(source, destination)
    except OSError as exc:
        return Retirement(
            moved=None, reason=f"could not be moved to the trash: {exc}", size=_size(source)
        )
    return Retirement(moved=destination, reason="moved to the trash", size=_size(destination))


def listing(trash_dir: Path) -> tuple[int, int]:
    """How many files the trash holds, and how many bytes. ``(0, 0)`` when there is no trash."""
    if not trash_dir.exists():
        return (0, 0)
    files = [p for p in trash_dir.rglob("*") if p.is_file() and not p.is_symlink()]
    return (len(files), sum(_size(p) for p in files))


def empty(trash_dir: Path) -> tuple[int, int]:
    """Really delete what is in the trash, and report how much went.

    The only caller that should reach here is one that has been confirmed: this is where captured
    content stops existing. Symlinks are unlinked and never followed, so a planted link cannot
    make this reach outside the trash; directories come away bottom-up, and nothing above the
    trash itself is touched.
    """
    if not trash_dir.exists():
        return (0, 0)
    count = 0
    total = 0
    for path in sorted(trash_dir.rglob("*"), reverse=True):
        try:
            if path.is_symlink():
                path.unlink()
                continue
            if path.is_file():
                total += _size(path)
                path.unlink()
                count += 1
            elif path.is_dir():
                path.rmdir()
        except OSError:
            continue  # already gone, or still open; the next pass will see it
    return (count, total)


def _size(path: Path) -> int:
    try:
        return int(path.stat().st_size)
    except OSError:
        return 0


def retire_after_delete(
    reference: str | None, *, db_path: str | Path, last_reference: bool
) -> Retirement | None:
    """Retire a captured copy on the delete path, or report why nothing moved.

    Both front ends call this, so a box deleted from the page and a box deleted from the terminal
    treat the library's files identically. It answers ``None`` when the question does not arise: the
    box pointed at nothing, or at something the library does not own (an ordinary document, or a
    note's own words file, which stays exactly where ADR 14 left it).

    ``last_reference`` comes from the store: the same file can be held by several boxes, because a
    captured copy is named for its content, and content can be pointed at twice. Only the last box
    out puts the file in the trash; the others were never the only thing keeping it.
    """
    raw = (reference or "").strip()
    if not raw:
        return None
    files_dir = files_dir_for(db_path)
    if not is_captured(raw, files_dir=files_dir):
        return None
    trash_dir = trash_dir_for(db_path, files_dir=files_dir)
    if not last_reference:
        return Retirement(
            moved=None, reason="another box still points at it", size=_size(Path(raw))
        )
    return retire(raw, files_dir=files_dir, trash_dir=trash_dir)
