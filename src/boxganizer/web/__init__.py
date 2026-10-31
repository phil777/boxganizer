"""Web frontend (FastAPI). Ships with the install — see ADR 24."""

from boxganizer.web.app import create_app

__all__ = ["create_app"]
