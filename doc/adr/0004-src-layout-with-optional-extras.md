# 4. src layout with optional extras

Date: 2026-10-24
Status: accepted (the optional-extras part superseded by ADR 24) — the console-script part is superseded by 0005

## Context

The package will grow, and two of its three interfaces pull heavy dependencies:
FastAPI plus uvicorn for the web app, Textual for the text UI. Someone scripting
against the core, or using only the CLI, should not have to install all of it.

## Decision

Use the `src/` layout: the importable package lives in `src/boxganizer/`, and the
repository root holds only tooling. Declare Typer as a runtime dependency, and
FastAPI/uvicorn/Jinja2 and Textual as `[web]` and `[tui]` extras, with `[all]`
and `[dev]` on top. Three console scripts — `boxganizer`, `boxganizer-web`,
`boxganizer-tui` — each import only their own subtree.

## Consequences

* `import boxganizer` cannot silently resolve to the source tree, so tests run
  against the installed package and packaging errors surface in CI, not later.
* `pip install -e .` is enough for scripts and CI use; the `all` extra is one
  obvious command for development.
* Each frontend must fail with a clear message when its extra is missing, and the
  matching test suite must `importorskip` rather than fail.
* A shared dependency used by two frontends belongs in the core dependency list,
  not duplicated in both extras.
