# 6. Box types are classes in a registry; the box keeps a string key

Date: 2026-10-24
Status: accepted

## Context

The project's source document defines a hierarchy of about forty box kinds
(`doc/box-types.md`, with the original outline in `doc/box-types-source.md`).
Storing that has three obvious shapes:

1. Make `Box` the base class, subclass it per type, and map the classes onto the
   database — the ORM route.
2. Keep types as strings with their rules in a configuration table, editable at
   runtime — no classes at all.
3. Keep the stored record as it is and hold the hierarchy in code, next to the
   behaviour each type will eventually own.

Option 1 means a schema change for every new type, and pushes class plumbing
into `store.py`, which is the one module that must stay simple. Option 2 puts
rules where nobody can test them or write logic in them. Type behaviour will be
real code — a renderer, a poller, an indexer — so it needs a place to live.

## Decision

Keep `models.Box` as the single stored record and give it one new column,
`box_type`, holding the key of a class in `boxganizer.boxtypes`. The hierarchy is
plain Python classes under a common root `BoxType`; each class registers itself
by key when it is created, and declares its rules as class attributes
(`allows_children`, `resource_uri_schemes`, `extensions`, `required_metadata`,
`check()`). The store resolves the key on write and validates; reads use a
lookup that tolerates a key this version does not know.

`abstract` marks a grouping level. It is read from the class body at registration
and never inherited, because a child of a grouping level is a real choice.

## Consequences

* Adding a type is one class plus one line in `tests/test_boxtypes.py`; no
  migration, no store change, and the interfaces pick it up from the registry.
* One table means one query path: search, list and tree stay as they were.
* Behaviour has a home before it exists — the `planned` line and the `check()`
  hook — so a future renderer attaches to a type instead of to a string compare.
* Unknown types are a one-way door: a database written by a newer version can
  still be read, but such a box cannot be saved again until the software catches
  up. Deliberate: writing through a type you do not understand is how records get
  corrupted.
* Two class-level mistakes are now import-time failures instead of data
  corruption: a subclass without its own `key`, and two classes sharing one.
* The registry is global mutable state populated at import. `import
  boxganizer.boxtypes` is the trigger, which every entry point does; a type that
  nobody imports is invisible, so the package re-exports every class.
* The class hierarchy can drift from the source document silently, so
  `DOCUMENTED` in `tests/test_boxtypes.py` transcribes the document and asserts
  every parent link. That test is the contract.
