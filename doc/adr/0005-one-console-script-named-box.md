# 5. One console script named `box`

Date: 2026-10-24
Status: accepted
Supersedes: part of 0004 (the three-console-scripts decision)

## Context

The tool is typed hundreds of times a day, and `boxganizer` is long enough to
feel like friction. Three scripts (`boxganizer`, `boxganizer-web`,
`boxganizer-tui`) also mean three help texts and three places to look for an
option. Heavy frameworks must not become a cost for someone who only wants the
core or the CLI.

## Decision

Ship one console script, `box`, defined by `boxganizer.cli.main:main`. The web
app starts with `box serve` and the text UI with `box tui`. Those two commands
import FastAPI, uvicorn and Textual inside the function body, so `box --help`
and every data command work with the core alone; a command whose extra is
missing raises `MissingDependency`, which the entry point prints as an install
hint with exit code 1. The distribution keeps the name `boxganizer`; only the
command is short.

## Consequences

* One help system, one completion target, one muscle memory: `box serve`,
  `box tree`, `box show`.
* The commands were later flattened to one level (`box add`, not `box box add`)
  and `box link in` / `box link out` became `box link` / `box unlink`. This
  strengthens the decision rather than changing it: fewer words between `box`
  and the thing you mean.
* Framework imports happen at command time, so an import error surfaces as a
  message about a missing extra rather than a traceback at `box --help`.
* The web runner is now a plain function (`boxganizer.web.server.run`) instead
  of its own Typer app, which also makes it callable from tests and scripts.
* Nothing external can pin `boxganizer-web` yet — the project is one commit old,
  so no alias was kept. If that ever becomes necessary, add it as a thin script
  pointing at the same code path.
