"""Checklists: what the file says, and what the items mean.

A checklist box keeps its items in a file of its own (see :mod:`boxganizer.authored`),
written as markdown task list syntax — the `- [ ]` that GitHub, Keep, Obsidian and every
other plain-text note app already agree on:

    # Cellar, spring
    - [x] Call the plumber
    - [ ] Buy fittings [🔧 Copper elbow](boxganizer:3f1c…)
      - [ ] order the part first

Four things worth explaining, because each is a choice and not a accident.

**The file is markdown, and stays readable when Boxganizer is not running.** The heading
is the checklist's own title, the boxes are ticked with a marker any editor can set, and
nesting is indentation. Someone who opens this in a text editor sees their list, and
someone who edits it there is read back correctly, which is the whole point of keeping
notes as files rather than as rows.

**A child box is attached to an item by a link, not by a private marker.**
`[🔧 Copper elbow](boxganizer:3f1c…)` is a markdown link whose scheme is this project's:
it survives being read by anything that reads markdown, it names the box the way a person
wrote it, and the uuid is what survives renaming the box. The file therefore carries
*which item* a child hangs on — which the links table cannot say, since the links table
knows only that a box is inside another box.

**Two sources of truth are kept in step rather than merged.** The graph says a box is a
child of the checklist; the file says it belongs to item two. Attaching from the page does
both; deleting the checklist's file does not delete the child boxes, and detaching an item
takes the link out of the file and leaves the box where it is. When they disagree — a box
linked in the graph and mentioned nowhere, a link naming a box that has gone — the page
shows the orphan under "not on an item" and the dead link greyed, rather than pretending
either is nothing.

**The title is read from the file, not from the box name.** A box can be renamed by the
panel; the heading is what the list itself is called, and it is the one line of the file
that is not an item.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from boxganizer import boxrefs

#: `- [ ] something`, `- [x] something`, with any amount of leading indentation.
_ITEM = re.compile(r"^(?P<indent>[ \t]*)[-*+]\s+\[(?P<mark>[ xX])\]\s?(?P<text>.*)$")

#: A child box is written as a markdown link on this project's scheme. One parser for the
#: scheme, shared with notes — see :mod:`boxganizer.boxrefs`.
_REF = boxrefs.LINK

#: Spaces (or a tab) that count as one level of nesting when reading a file written by
#: someone else. Rendering always uses two.
_INDENT_STEP = 2

#: What a brand-new checklist file looks like. Two rules and a blank line: an empty
#: markdown file is 0 bytes, and a file that size reads like a failure rather than like
#: an empty list.
EMPTY = ""


@dataclass
class Item:
    """One line of the list.

    `box` is the usual kind: a tickable item. `text` is a line someone wrote between the
    items — a heading for a section of the list, a reminder in prose. It is kept, in
    place, because the file is the person's own and the page is an editor of it rather
    than its owner: a checklist that ate the sentence typed under it would be a checklist
    nobody could use for anything but ticks.
    """

    text: str = ""
    checked: bool = False
    depth: int = 0
    kind: str = "box"
    #: Boxes hung on this item, in the order they were written.
    refs: list[str] = field(default_factory=list)

    @property
    def tickable(self) -> bool:
        return self.kind == "box"

    def as_dict(self) -> dict[str, object]:
        return {
            "text": self.text,
            "checked": self.checked,
            "depth": self.depth,
            "kind": self.kind,
            "refs": list(self.refs),
        }


@dataclass
class Checklist:
    """A whole list: an optional title and its items."""

    title: str = ""
    items: list[Item] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {"title": self.title, "items": [item.as_dict() for item in self.items]}

    @property
    def open(self) -> int:
        return sum(1 for item in self.items if item.tickable and not item.checked)

    @property
    def done(self) -> int:
        return sum(1 for item in self.items if item.tickable and item.checked)

    def summary(self) -> str:
        if not self.open and not self.done:
            return "nothing ticked on it yet"
        if not self.open:
            return f"all {self.done} done"
        return f"{self.open} of {self.open + self.done} still open"


def parse(text: str) -> Checklist:
    """Read a checklist file. Lines that are neither a title nor an item are kept.

    Nothing is thrown away: a paragraph someone typed between the items comes back out of
    `parse` and goes back into `render` where it was, because the file is theirs and
    Boxganizer is an editor of it, not its owner.
    """
    result = Checklist()
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not result.title and stripped.startswith("# "):
            result.title = stripped[2:].strip()
            continue
        match = _ITEM.match(line)
        if match is None:
            # prose between the items: kept as its own line, unedited, where it was
            words = stripped
            result.items.append(
                Item(
                    text=words,
                    depth=_depth(line[: len(line) - len(line.lstrip())]),
                    kind="text",
                    refs=[found.group("uuid") for found in _REF.finditer(words)],
                )
            )
            continue
        body = match.group("text")
        refs = [found.group("uuid") for found in _REF.finditer(body)]
        # The link is the attachment; the words the author typed around it are the item.
        # A line that is nothing but attachments reads as an item with no text, which is
        # what it is.
        clean = _REF.sub("", body).strip()
        result.items.append(
            Item(
                text=clean,
                checked=match.group("mark").lower() == "x",
                depth=_depth(match.group("indent")),
                refs=refs,
            )
        )
    return result


def label_for(labels: dict[str, str] | None, uuid: str) -> str:
    """What to write beside a box reference.

    The box's own name where it is known, so the file reads as a list of things and not
    as a list of uuids; a marker alone where it is not, because a link to a box that has
    gone should still be visible rather than silently vanish from someone's file.
    """
    name = (labels or {}).get(uuid, "").strip()
    return f"📦 {name}" if name else "📦 missing box"


def render(checklist: Checklist, *, labels: dict[str, str] | None = None) -> str:
    """Write a checklist back out, with the title first and two spaces per level.

    `labels` maps box uuid to the name to write in its link. Left out, a reference is
    written with a marker instead of a name — the uuid is still there, so the file is
    still correct; only the words get less friendly.
    """
    lines: list[str] = []
    if checklist.title.strip():
        lines.append(f"# {checklist.title.strip()}")
        lines.append("")
    for item in checklist.items:
        indent = " " * (max(0, item.depth) * _INDENT_STEP)
        tail = "".join(f" [{label_for(labels, uuid)}](boxganizer:{uuid})" for uuid in item.refs)
        if item.kind != "box":
            lines.append(f"{indent}{item.text.strip()}{tail}".rstrip())
            continue
        mark = "x" if item.checked else " "
        lines.append(f"{indent}- [{mark}] {item.text.strip()}{tail}".rstrip())
    return ("\n".join(lines) + "\n") if lines else EMPTY


def attach(checklist: Checklist, index: int, box_uuid: str) -> Checklist:
    """Hang a box on one item. Already there is a no-op, not a duplicate."""
    if not (0 <= index < len(checklist.items)):
        raise IndexError(f"there is no item {index} on this list")
    refs = checklist.items[index].refs
    if box_uuid not in refs:
        refs.append(box_uuid)
    return checklist


def detach(checklist: Checklist, box_uuid: str) -> Checklist:
    """Take a box off whichever item carries it. It stays a child box either way."""
    for item in checklist.items:
        if box_uuid in item.refs:
            item.refs = [uuid for uuid in item.refs if uuid != box_uuid]
    return checklist


def referenced(checklist: Checklist) -> list[str]:
    """Every box attached anywhere in the list, first mention wins."""
    seen: list[str] = []
    for item in checklist.items:
        for uuid in item.refs:
            if uuid not in seen:
                seen.append(uuid)
    return seen


def _depth(indent: str) -> int:
    """How deep a line sits, however its author chose to indent it."""
    expanded = indent.replace("\t", " " * _INDENT_STEP)
    return len(expanded) // _INDENT_STEP if expanded else 0
