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


class StoreError(BoxganizerError):
    """The database could not be opened or written."""
