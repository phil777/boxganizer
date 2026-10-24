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
