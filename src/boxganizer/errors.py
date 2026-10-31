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


class PlacementError(BoxganizerError):
    """A coordinate, a tile size, or a grid size the desktop cannot accept."""


class MqttError(BoxganizerError):
    """An MQTT address that cannot be connected to, or a broker that refused."""


class StoreError(BoxganizerError):
    """The database could not be opened or written."""


class MissingDependency(BoxganizerError):
    """The command needs a library that every boxganizer install is supposed to carry.

    It used to name an extra (`boxganizer[web]`), which was a polite way of telling someone to go
    and find a feature they had already asked for. Since ADR 24 the interfaces are plain
    dependencies, so landing here means the install is damaged or older than the change — and the
    message says so and tells them how to put it back.
    """

    def __init__(self, interface: str) -> None:
        super().__init__(
            f"this install has no {interface!r} interface: the library it needs is missing, "
            "and it should have come with boxganizer. Repair the install — "
            "pipx install --force boxganizer, or pip install --force-reinstall boxganizer"
        )
        self.interface = interface
