"""Boxganizer: personal knowledge management where everything is a box.

The package is organised in two layers:

* the **core** library (`models`, `store`, `config`) holds all the logic,
* the **frontends** (`cli`, `web`, `tui`) are thin shells over that core.

A frontend must never talk to the database directly; it goes through
:class:`boxganizer.store.BoxStore`.
"""

from boxganizer.models import Box
from boxganizer.store import BoxStore

__version__ = "0.1.0"

__all__ = ["Box", "BoxStore", "__version__"]
