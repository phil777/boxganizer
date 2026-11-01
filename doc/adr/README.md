# Architecture decision records

An ADR records a decision that is expensive to reverse, and why it was taken.
One file per decision, numbered in order, never edited after acceptance — a
change gets a new ADR that supersedes the old one.

## Template

```markdown
# N. Title

Date: YYYY-MM-DD
Status: proposed | accepted | superseded by NNNN

## Context

What forces the decision. Constraints, not opinions.

## Decision

What we do, in one or two sentences, active voice.

## Consequences

What becomes easy, what becomes harder, what we now have to watch.
```

## Index

| Number | Title                                       | Status   |
| ------ | ------------------------------------------- | -------- |
| 0001   | Record architecture decisions               | accepted |
| 0002   | Store boxes in a single SQLite file         | accepted |
| 0003   | One core library, three thin frontends      | accepted |
| 0004   | src layout with optional extras             | accepted, partly superseded by 0005 |
| 0005   | One console script named `box`                | accepted |
| 0006   | Box types are classes in a registry           | accepted |
| 0007   | Box rows are edited as YAML via `$EDITOR`       | accepted |
| 0008   | Layout and view settings live in the config   | accepted |
| 0009   | The graph may contain cycles                    | accepted |
| 0010   | Index box file contents in the database, with FTS5 | accepted |
| 0011   | A mindmap node is a box, and an unwritten node may be replaced | accepted |
| 0012   | SSH boxes borrow the system client and never hold a password | accepted |
| 0013   | A resource is a row, and the index is keyed on it | accepted |
| 0014   | A box's own words are a file beside the database | accepted |
| 0015   | The server speaks VNC, and the page is shown pictures | accepted, amended by ADR 17 (authentication) |
| 0016   | A folder's index is the files inside it       | accepted |
| 0017   | A VNC password is asked for per connection, and never kept | accepted |
| 0018   | A box that cannot work says so, and stops     | accepted (the install advice in it was rewritten by ADR 2… |
| 0019   | A tag's name is its identity, and its parents are never stored | accepted |
| 0020   | A dynamic folder's filter is one expression, and the builder writes that expression | accepted |
| 0021   | A sensor's meaning is written by its owner, in Jinja, and rendered by the server | accepted |
| 0022   | A numeric sensor's scale belongs to its owner, is checked to cover itself, and clamps out loud | accepted |
| 0023   | A viewport lends its space: the layout is a tree the server owns, and a drop copies unless Shift says otherwise | accepted |
| 0024   | boxganizer installs fully featured: an interface is a dependency, not an extra | accepted |
| 0025   | A folder that filters itself refuses the hand that files into it, and a tag move re-cuts it | accepted |
| 0026   | The tag window is a tree, and the rules stay on the server | accepted |
| 0027   | A drop zone belongs to the page, not to the element that painted it | accepted |
| 0028   | A delete is asked inside the menu, and the question says what survives | accepted |
| 0029   | A pane's walk belongs to the tab; the pane's box belongs to the library | accepted |