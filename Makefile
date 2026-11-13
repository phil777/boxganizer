# Boxganizer developer tasks. Run `make help` for the list.

VENV ?= .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
RUFF := $(VENV)/bin/ruff
MYPY := $(VENV)/bin/mypy
PYTEST := $(VENV)/bin/pytest

help:
	echo "install    create $(VENV) and install boxganizer[dev]"
	echo "test       pytest with coverage"
	echo "lint       ruff check"
	echo "format     ruff format"
	echo "typecheck  mypy"
	echo "check      lint + typecheck + test"
	echo "run-cli    ARGS=\"box list\""
	echo "run-web    serve on 127.0.0.1:8000"
	echo "run-tui    open the terminal UI"
	echo "shots      retake the README's screenshots (needs a browser)"
	echo "uitest     drive the real page in a browser (needs playwright)"
	echo "clean      remove build and tool caches"

install: $(VENV)/bin/python
	$(PIP) install -e ".[dev]"

$(VENV)/bin/python:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip

test:
	$(PYTEST)

lint:
	$(RUFF) check .

format:
	$(RUFF) format .
	$(RUFF) check --fix .

typecheck:
	$(MYPY)

check: lint typecheck test

run-cli:
	$(VENV)/bin/box $(ARGS)

run-web:
	$(VENV)/bin/box serve

run-tui:
	$(VENV)/bin/box tui

shots:
	$(PY) tools/shot_docs.py

# Not part of `check`: a browser download in the test pipeline is a dependency nobody asked for,
# and `make test` must stay fast and offline. This seeds its own library and reaps its server.
uitest:
	$(PY) tools/verify_web_ui.py --self

clean:
	rm -rf build dist *.egg-info .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage coverage.xml
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

.PHONY: help install test lint format typecheck check run-cli run-web run-tui shots uitest clean
