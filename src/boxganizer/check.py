"""Checking a library: what has gone wrong with the things boxes point at, and what to do.

A box is a promise about something else — a file, a folder, a topic, a host — and promises
rot. The file moves, the folder becomes a file, the laptop's name changes, the drive that
held the photos is not mounted today. Boxganizer cannot stop any of that, but it is the one
place that knows all of it at once, so it is the place that should say so.

**A finding names an action.** This module's whole reason to exist is that a list of broken
things is a complaint, and a list of broken things with a verb attached is a repair. Each
finding carries the fixes that genuinely apply to it — a file that has moved can be
re-pointed, a file that has not been read can be read, an orphaned note-file can be deleted —
and `apply` performs exactly one of them. The CLI prints the findings and the command that
would fix each; the page offers the same set as buttons. Neither invents an action the core
does not implement, which is why the vocabulary lives here and not in either interface.

**Nothing is repaired silently.** A check writes no state except the two things that are
observations rather than decisions — the `stale` flag, refreshed by looking at the file — and
`apply` is only ever reached from an explicit request. A tool that "fixed" a library by
guessing where a moved file went would be a tool that files your documents in the wrong
places and tells you it helped.

**An unreadable file is not a broken one.** A zero-byte note, a file that holds no words, a
topic nobody has published to: these are findings with a low severity and their fix is often
to leave the thing alone. They are reported because a person wondering why the search finds
nothing needs to know, and because the alternative — the index quietly holding nothing — is
the failure that makes people stop trusting the search box.
"""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from boxganizer import authored, boxrefs, paths
from boxganizer import resources as address
from boxganizer.models import Box
from boxganizer.store import BoxStore

#: The actions a finding can offer. The words are the API: the page turns them into
#: buttons and the CLI turns them into commands, and neither has a private vocabulary.
_UUID_LIKE = re.compile(r"[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")

RELOCATE = "relocate"
DELETE_BOX = "delete-box"
REINDEX = "reindex"
UNINDEX = "unindex"
DELETE_FILE = "delete-file"


@dataclass
class Finding:
    """One thing wrong, the box it belongs to, and what can be done about it."""

    kind: str
    #: `watch` is worth knowing about; `broken` means the box cannot do its job.
    severity: str = "watch"
    box_name: str = ""
    box_uuid: str = ""
    reference: str = ""
    detail: str = ""
    fixes: list[str] = field(default_factory=list)
    #: For a finding about a file rather than a box (an orphan), where the file is.
    path: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "box": {"name": self.box_name, "uuid": self.box_uuid} if self.box_uuid else None,
            "reference": self.reference,
            "detail": self.detail,
            "fixes": list(self.fixes),
            "path": self.path,
            "command": self.command(),
        }

    def command(self) -> str:
        """The CLI line that performs the first fix on offer.

        Said out loud because a person reading a terminal is usually one step from typing
        something, and `box check` should hand them the sentence rather than make them
        reconstruct it from a description of the problem.
        """
        if not self.fixes:
            return ""
        name = self.box_name or self.path
        if self.fixes[0] == REINDEX:
            return "box index --stale"
        if self.fixes[0] == UNINDEX:
            return f"box index --drop {sh(name)}"
        if self.fixes[0] == DELETE_BOX:
            return f"box rm {sh(name)}"
        if self.fixes[0] == RELOCATE:
            return f"box edit {sh(name)} --resource <new address>"
        if self.fixes[0] == DELETE_FILE:
            return f"rm {sh(self.path)}"
        return ""


def sh(value: str) -> str:
    """Quote a name the way a shell wants it, when it needs quoting at all."""
    return shlex.quote(value)


def run(store: BoxStore, *, confine: Path | str | None = None) -> list[Finding]:
    """Look at every box, and at what the library holds that no box holds.

    Reads the disk (one `stat` per address, plus a listing of the directory Boxganizer
    writes its own files in) and the database, and changes neither. A pass over a few
    hundred boxes costs a few hundred stats, which is why it is an action someone takes
    rather than something that runs when the page opens.
    """
    # One library, one confine: what the store was opened with is what its boxes are
    # judged against, so a check agrees with the index about what could have been read.
    root = paths.content_root(Path(confine) if confine is not None else store.content_root)
    found: list[Finding] = []
    for box in store.list_boxes():
        found.extend(check_box(store, box, root=root))
    found.extend(check_orphan_files(store))
    found.extend(dangling_mentions(store))
    found.sort(key=lambda item: (0 if item.severity == "broken" else 1, item.kind, item.box_name))
    return found


def check_box(store: BoxStore, box: Box, *, root: Path) -> list[Finding]:
    """What is wrong with this box's promise about the thing it points at."""
    kind = _kind_of(box)
    reference = (box.resource_uri or "").strip()
    findings: list[Finding] = []

    def add(kind_name: str, detail: str, fixes: list[str], severity: str = "broken") -> None:
        findings.append(
            Finding(
                kind=kind_name,
                severity=severity,
                box_name=box.name,
                box_uuid=box.uuid,
                reference=reference,
                detail=detail,
                fixes=fixes,
            )
        )

    if not reference:
        if box.resource_id is None and kind and kind.resource_uri_required:
            add(
                "missing_address",
                "the box points at nothing, and this type needs something to",
                [RELOCATE],
            )
        # A box with no address is normal for a folder, an organiser, a checklist. Nothing
        # to say about a promise it never made.
        return findings

    where = address.origin_of(reference)
    if where == address.REMOTE:
        return findings  # a URL, a topic, a host: reachability is the box's own business

    target = Path(address.normalise(reference))
    if not target.exists():
        add("gone", f"nothing is at {target} any more", [RELOCATE, DELETE_BOX])
    elif target.is_dir() and not _expects_directory(kind):
        add(
            "wrong_kind",
            f"{target} is a folder, and this box is a {box.box_type}, which holds a file",
            [RELOCATE, DELETE_BOX],
        )
    elif target.is_file() and _expects_directory(kind):
        add(
            "wrong_kind",
            f"{target} is a file, and this box is a {box.box_type}, which holds a folder",
            [RELOCATE, DELETE_BOX],
        )
    elif not os_readable(target):
        add("unreadable", f"{target} is there but cannot be opened", [RELOCATE], severity="watch")

    if not paths.inside(target, root) and where == address.LOCAL:
        add(
            "out_of_range",
            f"{target} is outside {root}, so its words cannot be indexed from here",
            [RELOCATE, UNINDEX],
            severity="watch",
        )

    record = store.resource(box.resource_id) if box.resource_id is not None else None
    if record is not None and record.index_it and not record.is_dir:
        state = record.stale()
        if state is True:
            add(
                "stale",
                "the file has changed since it was read into the index"
                if record.indexed
                else "the file has never been read into the index",
                [REINDEX, UNINDEX],
                severity="watch",
            )
        elif state is False and record.indexed and not record.marked_stale:
            # It was read and nothing came out. Sometimes that is correct (an empty note)
            # and sometimes it is the file the person thought was indexed.
            holds_words = store.resource_holds_words(int(record.id))
            if not holds_words and target.is_file() and target.stat().st_size > 0:
                add(
                    "nothing_indexed",
                    "the file was read and no words came out of it",
                    [REINDEX, UNINDEX],
                    severity="watch",
                )
    return findings


def check_orphan_files(store: BoxStore) -> list[Finding]:
    """Files Boxganizer wrote that no box points at any more.

    ADR 14 says a box's own words are not deleted with the box, because the row can be
    re-created and the words cannot. That is the right rule and it leaves residue, and
    residue nobody can see is where a library starts to feel like it is rotting. This names
    each one and offers the single sensible action: delete it, deliberately.
    """
    findings: list[Finding] = []
    directory = authored.directory_for(store.path)
    if not directory.is_dir():
        return findings
    kept = {box.uuid for box in store.list_boxes()}
    for entry in sorted(directory.iterdir()):
        if not entry.is_file() or entry.suffix != authored.SUFFIX:
            continue
        # Only a name shaped like a uuid is one of ours. The directory lives beside the
        # database, and a person who keeps their notes there would otherwise be told that
        # their own files are residue to delete.
        if not _UUID_LIKE.fullmatch(entry.stem):
            continue
        if entry.stem in kept:
            continue
        findings.append(
            Finding(
                kind="orphan_file",
                severity="watch",
                reference=str(entry),
                path=str(entry),
                detail="a file Boxganizer wrote for a box that is no longer in the library",
                fixes=[DELETE_FILE],
            )
        )
    return findings


def dangling_mentions(store: BoxStore) -> list[Finding]:
    """Notes and checklists that name a box the library no longer holds.

    A `[📋 name](boxganizer:uuid)` link is inert until the page turns it into a way to open
    something, and a uuid that names nothing stays inert forever: the sentence reads fine and
    the icon shows as missing. Reported, not repaired — the words around it are the person's,
    and only they know whether the box is coming back.
    """
    findings: list[Finding] = []
    boxes = store.list_boxes()
    living = {box.uuid for box in boxes}
    for box in boxes:
        if not _writes_words(box):
            continue
        # A note or checklist usually names its own file through a resource row, and that
        # row is made the first time the page writes it. A box created and then filled by
        # hand — or by a restore that brought the file back without the row — has the file
        # and no pointer, so look where the words would be either way.
        if box.resource_uri:
            target = Path(address.normalise(box.resource_uri))
        else:
            target = authored.path_for(store.path, box.uuid)
        if not target.is_file():
            continue
        text = authored.read(target)
        if not text:
            continue
        missing = [uuid for uuid in boxrefs.mentions(text) if uuid not in living]
        if missing:
            findings.append(
                Finding(
                    kind="dangling_reference",
                    severity="watch",
                    box_name=box.name,
                    box_uuid=box.uuid,
                    reference=str(target),
                    detail=f"{len(missing)} link"
                    f"{'' if len(missing) == 1 else 's'} to a box that is not in the library",
                    fixes=[],
                )
            )
    return findings


def apply(
    store: BoxStore,
    finding: Finding,
    action: str,
    *,
    value: str | None = None,
    confine: Path | str | None = None,
) -> str:
    """Do one offered fix, and say what happened in the words a person would use.

    Refuses anything the finding did not offer, because the page and the CLI are both
    untrusted callers by the time a button is clicked or a flag is typed: the set of legal
    repairs is decided where the problem was found, not at the end of the wire.
    """
    if action not in finding.fixes:
        raise ValueError(f"'{action}' is not something that can be done about {finding.kind}")

    if action == DELETE_FILE:
        target = Path(finding.path)
        if not target.is_file():
            return f"{target} is already gone"
        target.unlink()
        return f"deleted {target}"

    box = store.resolve(finding.box_uuid or finding.box_name)

    if action == RELOCATE:
        if not value or not value.strip():
            raise ValueError("a new address is needed to re-point the box")
        box.resource_uri = value.strip()
        store.update(box)
        return f"{box.name} now points at {value.strip()}"

    if action == DELETE_BOX:
        store.remove(box.name)
        return f"deleted the box {box.name}"

    if action == UNINDEX:
        if box.resource_id is None:
            return f"{box.name} points at nothing, so there is nothing to take out of the index"
        record = store.set_index_flag(int(box.resource_id), False)
        return (
            f"{box.name}'s file is no longer read for the search index"
            if record is not None
            else f"{box.name} points at a resource that is not here any more"
        )

    if action == REINDEX:
        from boxganizer import search as text_search

        if box.resource_id is None:
            return f"{box.name} points at nothing to read"
        note = text_search.sync_resource(
            store, int(box.resource_id), root=confine if confine is not None else store.content_root
        )
        return (
            f"{box.name}'s file was read into the index"
            if not note
            else f"{box.name}'s file still cannot be read: {note}"
        )

    raise ValueError(f"no repair named '{action}'")


def summary(findings: Iterable[Finding]) -> str:
    """One line, by severity, in the order a person acts on them."""
    broken = sum(1 for item in findings if item.severity == "broken")
    watch = sum(1 for item in findings if item.severity == "watch")
    if not broken and not watch:
        return "nothing to report — every box's resource is where it said it would be"
    parts = []
    if broken:
        parts.append(f"{broken} broken")
    if watch:
        parts.append(f"{watch} to keep an eye on")
    return ", ".join(parts)


# -- small judgements the checks keep needing ---------------------------------


def _kind_of(box: Box) -> Any:
    from boxganizer import boxtypes

    return boxtypes.get(box.box_type) if boxtypes.known(box.box_type) else None


def _expects_directory(kind: Any) -> bool:
    return bool(kind is not None and getattr(kind, "lists_files", False))


def _writes_words(box: Box) -> bool:
    kind = _kind_of(box)
    return bool(kind is not None and getattr(kind, "writes_own_file", False))


def os_readable(target: Path) -> bool:
    """Whether this process could open the file if it tried.

    A separate question from existing: a file on a mount that is there but belongs to
    somebody else passes one check and fails the other, and a check that conflates them
    reports "gone" when the truth is "you cannot read it from here".
    """
    try:
        return os.access(target, os.R_OK)
    except OSError:
        return False
