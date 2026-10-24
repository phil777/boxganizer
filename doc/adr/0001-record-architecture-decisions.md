# 1. Record architecture decisions

Date: 2026-10-24
Status: accepted

## Context

Boxganizer is a large application in the making: a store, a CLI, a web app and a
text UI. Choices such as where data lives, how the layers are split, and which
framework owns which layer will outlive any single session — including sessions
by agents that have not read this repository's history. Reasoning held only in
chat logs is lost.

## Decision

Record each consequential decision as a numbered Markdown file in `doc/adr/`,
following the template in `doc/adr/README.md`. Write a new ADR instead of
editing an accepted one; mark the old record `superseded by NNNN`.

## Consequences

* Every future reader learns the constraints behind a rule, not just the rule.
* Small decisions stay out — an ADR per rename would bury the important ones.
* Writing an ADR costs a few minutes at the moment the decision is made, which
  is when it is cheapest.
