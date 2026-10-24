"""Exceptions raised by Boxganizer.

Every error the core raises derives from :class:`BoxganizerError`, so a
frontend can catch one type and turn it into a message, an HTTP 4xx, or a
dialog.
"""


class BoxganizerError(Exception):
    """Base class for every Boxganizer error."""


class InvalidBoxName(BoxganizerError):
    """A box name is empty, or contains a character we refuse to store."""

    def __init__(self, name: str, reason: str) -> None:
        super().__init__(f"invalid box name {name!r}: {reason}")
        self.name = name
        self.reason = reason


class DuplicateBoxName(BoxganizerError):
    """A box with the same name (case-insensitive) already exists."""

    def __init__(self, name: str) -> None:
        super().__init__(f"a box named {name!r} already exists")
        self.name = name


class BoxNotFound(BoxganizerError):
    """No box matches the requested name."""

    def __init__(self, name: str) -> None:
        super().__init__(f"no box named {name!r}")
        self.name = name


class CyclicLink(BoxganizerError):
    """Linking two boxes would make a box contain itself."""

    def __init__(self, parent: str, child: str) -> None:
        super().__init__(f"linking {child!r} inside {parent!r} would create a cycle")
        self.parent = parent
        self.child = child


class UnknownBoxType(BoxganizerError):
    """No box type is registered under that name."""

    def __init__(self, key: str, known: list[str] | None = None) -> None:
        message = f"unknown box type {key!r}"
        if known:
            message += f"; try one of: {', '.join(known[:12])}"
        super().__init__(message)
        self.key = key


class InvalidBoxContent(BoxganizerError):
    """A box does not satisfy the rules of its type."""

    def __init__(self, type_key: str, reason: str, name: str = "") -> None:
        subject = f"box {name!r}" if name else "this box"
        super().__init__(f"{subject} is not a valid {type_key!r}: {reason}")
        self.type_key = type_key
        self.reason = reason


class ChildrenNotAllowed(BoxganizerError):
    """A box of a leaf type was asked to contain another box."""

    def __init__(self, parent: str, parent_type: str, child: str) -> None:
        super().__init__(
            f"cannot put {child!r} inside {parent!r}: a {parent_type!r} box holds a "
            f"resource, not other boxes"
        )
        self.parent = parent
        self.parent_type = parent_type
        self.child = child


class EditError(BoxganizerError):
    """An edited box file cannot be turned back into a box row."""

    def __init__(self, reason: str, line: int | None = None) -> None:
        where = f" at line {line}" if line else ""
        super().__init__(f"box edit: {reason}{where}")
        self.reason = reason


class RootCannotBeFiled(BoxganizerError):
    """Something tried to put the root box inside another box."""

    def __init__(self, name: str, parent: str) -> None:
        super().__init__(f"{name!r} is the root of the graph; it cannot go inside {parent!r}")
        self.name = name
        self.parent = parent


class GridError(BoxganizerError):
    """A cell reference or a placement that the grid cannot accept."""


class StoreError(BoxganizerError):
    """The database could not be opened or written."""


class MissingDependency(BoxganizerError):
    """The command needs an optional extra that is not installed."""

    def __init__(self, extra: str) -> None:
        super().__init__(
            f"this command needs the {extra!r} extra: pip install 'boxganizer[{extra}]'"
        )
        self.extra = extra
