# 24. boxganizer installs fully featured: an interface is a dependency, not an extra

Date: 2026-11-10
Status: accepted
Supersedes: ADR 0004 (the optional-extras part)

## Context

The install had four switches: `[web]`, `[tui]`, `[live]`, and `[all]` for people who gave up on
choosing. `pipx install boxganizer` — how anyone actually installs a command-line tool — produced
a boxganizer with no web app, no text UI and no MQTT, each one a sentence away: *"install the
extra"*.

That arrangement cost more than it saved:

* **The feature you want is the one you did not install.** Extras work when the optional thing is
  rare or heavy. Here they split off the interfaces a third of the README is about.
* **The advice was always slightly wrong for somebody.** `pip install "boxganizer[live]"` inside a
  pipx install puts `aiomqtt` in a Python the tool never imports. ADR 18 went as far as generating
  the repair command from `sys.prefix` — pipx got `pipx inject`, uv got `uv tool install`, a venv
  got pip — which is real code, real tests and a real bug report (`"stream dropped"` on a box that
  could never start) spent working around a decision that should not have been made.
* **Every interface is already required in practice.** `pillow` and `pypdfium2` — far heavier than
  `fastapi` — have always been plain dependencies, because a box that shows a pdf is boxganizer.
  The line between "core" and "extra" was drawn where it was for a reason no longer held.

## Decision

**Every runtime library is a normal dependency**: `fastapi`, `uvicorn[standard]`, `textual`,
`aiomqtt`, `jinja2`, `markdown-it-py`, `pillow`, `pypdfium2`, `pyyaml`, `typer`. The extras are
gone.

**`dev` stays an extra.** Test and lint tooling is not a feature of the program: a person who wants
to organise boxes does not want `pytest`, `ruff` and `mypy` in their tool environment. That is the
one choice that still answers a question a user can actually be asked.

**The availability machinery stays, with its meaning changed.** `dependency`, `is_available()` and
`unavailable_reason()` (ADR 18) do not go away — a damaged install, a distribution that strips
dependencies, or an install older than this change all reach the same user-visible state: a box that
cannot do the thing. What changes is the sentence. It no longer offers an option that does not
exist; it says the module *should have come with boxganizer* and prints the command that repairs
this install: `pipx install --force boxganizer`, `uv tool install --reinstall boxganizer`, or
`python -m pip install --force-reinstall boxganizer`, chosen from `sys.prefix` as before.

**Nothing is refused for lacking a feature that ships.** `MissingDependency` now names the
interface (`web`, `tui`) and calls the situation a broken install; `dependency_extra` is deleted,
because a field that names a thing which no longer exists will be filled in again by the next
person who meets the old docs.

## Consequences

**Costs.** An install carries what some users will never open: `textual` and `fastapi` plus their
trees, a few tens of megabytes, and their updates arrive whether or not the interface is used.
Someone who truly wants a CLI-only boxganizer no longer has a knob to turn — they have a Python
environment they built themselves.

**What it buys.** One install line, and it is the one in the README. The box that cannot listen
stops being a configuration mystery and becomes what it always was in practice: an install that
needs repairing, with the right command printed next to the complaint. And ADR 18's machinery keeps
its purpose — a box that cannot work says so and stops — without the extras that made it necessary.

**Verified.** `tests/test_packaging.py` reads `pyproject.toml` and fails if any interface library
leaves the dependency list or a feature extra reappears; the MQTT and CLI tests assert the repair
sentences per installer and that no message contains `boxganizer[...]` any more. 1099 tests, ruff
and mypy clean.
