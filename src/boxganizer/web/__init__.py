"""Web frontend (FastAPI). Optional extra: ``pip install 'boxganizer[web]'``."""

from boxganizer.web.app import create_app

__all__ = ["create_app"]
