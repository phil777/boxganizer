# 7. Box rows are edited as YAML through $EDITOR

Date: 2026-10-24
Status: accepted

## Context

A box carries several fields that are awkward to reach through command options:
nested `config`, long descriptions, type changes that must be checked against the
type's rules. People asked to edit a box the way they edit a configuration file —
open it, change it, save it — and to have the change land in the database with
the same validation as any other write.

Two details make or break that. The database stores `metadata` and `config` as
JSON text, which is painful to read and impossible to comment. And an editor is
an outside program: it can fail, produce nonsense, or change fields that belong
to the database.

## Decision

`box edit NAME` serialises the whole row to a temporary YAML document, runs
`$EDITOR` (or `--editor`), then reads the file back and writes the row with
`BoxStore.update()`.

- `metadata` and `config` are rendered as YAML mappings and converted back to
  JSON on the way in.
- `id`, `created_at` and `updated_at` appear in the file and are **refused** if
  changed. Silence would be worse than an error.
- Unknown fields are refused, with a hint pointing at `box link` when someone
  tries to edit `parents` or `children`: links are not part of the row.
- Values JSON cannot store — including the bare dates YAML parses on its own —
  are refused with the offending path.
- Nothing is written unless the file parses and validates. A failing editor
  exit code aborts.
- The conversion lives in `boxganizer/editing.py` and is pure; only the CLI
  touches the filesystem and the subprocess.

## Consequences

* One editing path for every field, including shapes no flag could express, and
  it goes through the same type validation as `box add`.
* YAML is a new core dependency (`pyyaml`) for one command. Accepted: it is
  small, stable, and the alternative — teaching users JSON indentation — is worse.
* Round-tripping is now a correctness surface: a field that is not dumped is
  silently deleted on save. `tests/test_editing.py` asserts every column appears
  in the file and that an untouched file changes nothing, which is the guard
  against that.
* Editing is single-user: two people editing the same box last-write-wins, as
  elsewhere in the store.
* The web app cannot use this; it will need its own form or textarea, reusing
  `editing.load()` for validation rather than the CLI path.
