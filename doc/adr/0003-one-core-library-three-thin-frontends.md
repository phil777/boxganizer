# 3. One core library, three thin frontends

Date: 2026-10-24
Status: accepted

## Context

Boxganizer ships a CLI, a web app and a text UI. The tempting shortcut is to let
each interface talk to the database in its own way — the CLI grew one query, the
web app another, and search behaves differently depending on where you typed it.

## Decision

Put all logic in a core library (`models`, `store`, `config`, `errors`). Each
frontend is a shell: parse input, call one `BoxStore` method, render the result.
Frontends never import each other and never open a database connection of their
own. Errors leave the core as `BoxganizerError` subclasses and each frontend
translates them once, at its own edge.

## Consequences

* One behaviour, three interfaces: a core change reaches all of them at once, and
  a test against the core proves the behaviour for all three.
* The three frontend suites stay honest — they drive the real CLI, the real
  `TestClient`, and a real Textual pilot instead of mocking the store.
* Adding a feature means touching up to four files (core, CLI, web, TUI). That
  repetition is the point; it is what keeps the interfaces interchangeable.
* A frontend cannot invent a private shortcut, so a tempting one-line hack in the
  web layer becomes a deliberate core change with a test.
