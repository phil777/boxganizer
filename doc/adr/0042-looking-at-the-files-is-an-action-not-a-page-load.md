# 42. Looking at the files is an action, not a page load

Date: 2026-11-13
Status: accepted
Amends: the documented rationale of `GET /api/index/status` (its docstring, never an ADR)

## Context

`GET /api/index/status?check=1` stats every watched file and writes the freshness flag it
finds. The page sent it every time the panel drew — on load, and again whenever a pass
finished — because the plain read answers from what the database was last told, and a panel
saying "12 to reindex" from an hour-old flag is a polite lie.

The docstring said so, and gave the reason writing was acceptable:

> It writes, and that is allowed here for one reason: what it writes is a cache of what the
> filesystem says about a file, not anything a person typed. The stored flag is not a decision
> and loses nothing if it is refreshed twice.

Both halves of that were true and neither was sufficient. The flag is indeed not a decision.
But "loses nothing if it is refreshed twice" describes the *value*, not the *cost*: a refresh is
one write per watched file, and on this store every statement was its own transaction. Measured
against a copy of the real library:

    GET /api/index/status?check=1     54.1 s   (842 watched files)
    the same request, 12 concurrent writers   104 of 120 writes refused

So a method that promises no side effects was the slowest request the application made, was the
only thing making that cost scale with the size of the library, and was the source of the lock
traffic. It also had the practical consequence that reviewing this code meant running it against
somebody's actual library: a reviewer opening the page wrote to it, and there is no way to
*read* one's way out of that afterwards.

## Decision

**The status read is a read.** `GET /api/index/status` writes nothing, and is therefore safe to
poll as often as the panel redraws. Asserted at the connection — a commit spy counts zero commits
across four status reads, a pending read and a search — not argued.

**Looking at the files is an action.** `POST /api/index/check` stats every watched file, updates
the flags, and returns the new counts with the answer, because the person who pressed the button
is about to look at them. The panel's "Look at the files again" button calls it. A background pass
also counts as having looked: finishing one records the observation, since a pass has by
definition read what it read.

**A read that may be old says how old it is.** The response carries `observed`: when this process
last looked at the files, or `null`. The panel appends the sentence — "the files were last looked
at 3 h ago", or, after a restart, "nothing has looked at the files since this server started".

That is the honest replacement for `check=1`. The lie was never that the numbers could be stale;
it was that the panel presented them in the present tense. `observed` lives in the process rather
than the database for the same reason the index queue does: it describes what *this server* knows,
and a restart forgetting it is the truth — the files may well have changed meanwhile, which is
precisely what the panel then says.

## Consequences

- A page load issues no slow request and no write. Measured on the copy: `GET
  /api/index/status` 2.212 s → 0.0027 s, and the freshness pass, when somebody asks for it,
  54.1 s → 0.013 s after P1 and P6.
- The staleness a person sees can be as old as the last action or pass. The number is labelled
  with its age instead of being kept current by a background write.
- Anything that wants freshness on a schedule now has to say so explicitly — a timer, a pass, a
  button. This ADR does not add one; it moves the decision somewhere a decision can be seen.
- `check=1` is gone rather than deprecated: it is a local interface with no external callers, and
  a silently ignored parameter would leave the panel looking fresh while nothing looked.
