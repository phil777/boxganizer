# 17. A VNC password is asked for per connection, and never kept

Date: 2026-11-07
Status: accepted

## Context

ADR 15 left the VNC box offering exactly one RFB security type — `None` — and refusing a
server that wanted anything else, with what it asked for named back to the person. That was a
decision about *not storing* a secret, and it quietly decided something bigger: a screen that
asks for a password could not be looked at at all. Most screens worth looking at ask.

The request was "add authentication to vnc". Two of its constraints were already fixed by
earlier decisions and were not on the table:

* **The library is a plain SQLite file** that gets copied, backed up, e-mailed to a phone, and
  read by a page whose every field is served over HTTP. A password stored in a box column, in
  box metadata, or in an address is a published password. ADR 12 said this about SSH and ADR 15
  said it about this box; nothing since has given a reason to change the answer.
* **The person is not a daemon.** Whatever the mechanism, a human has to be able to look at
  their own workstation at 23:00 without having pre-delegated a secret to a database.

That leaves where the password comes from *now*, and for how long anything holds it.

## Decision

### 1. `None` when the server will take it, VNC authentication when it will not

The client reads the list of security types the server offers and prefers `None`. A server
that accepts an unauthenticated connection gets one, with no prompt. VNC authentication
(type 2) is chosen only when the server insists — with one exception worth naming: asking for
a password a server does not want teaches people that the field is decoration.

Types this client does not implement (RSA-AES, the AES variants, JetBrains's types) are
refused with the server's own list named, exactly as before.

### 2. The password is carried by a POST body, and held for the length of a handshake

Authentication could not live in the existing event-stream endpoint: **a URL is a publish
target**. It goes into the access log, the browser history, and every proxy between, so a
`?password=` would have been the same mistake as a password in the VNC address, one layer up.

So opening a screen is its own call, and the session id it returns is what the stream and the
input messages carry:

```
POST /api/boxes/{ref}/vnc/open     {"password": "…"}   →  {session, width, height, …}
GET  /api/boxes/{ref}/vnc/stream?session=…             →  frames
POST /api/boxes/{ref}/vnc/input    {"session": "…", …} →  ok
```

Three consequences, each enforced by code rather than intent:

* **`Screen` drops the password in a `finally`.** The exchange that needs it is sixteen bytes
  and happens once; a session lives as long as a browser tab. Holding it would cost a heap
  dump and buy nothing.
* **The page wipes the input** the moment a session exists, so the secret is not sitting in a
  DOM node that a later screenshot, an extension, or a "view source" would show.
* **A refusal names the password.** After a type-2 handshake the server's own reason travels
  with the message: *"the server refused that password (reason: bad password)"*, not
  *"the server refused the session"*, which sends a person to check the machine rather than
  what they typed.

### 3. Unattended use points at a file, and the file stays outside the library

`metadata["password_file"]` holds a **path**. At the moment of connection the bytes are read,
used, and dropped. Typing a password now outranks the one on disk.

This is the same bargain SSH strikes with its identity file: the library is allowed to know
where a thing lives, and not what it is. The box dialog offers the field because the type
declares it in `optional_metadata` — metadata a type honours but never asks for is a feature
that exists only in the documentation.

A `vnc://secret@host:5900` address is still refused, by the type, at creation time. That rule
survived this change on purpose: it is the one shape in which ordinary use of the box would
write a secret into the database.

### 4. A probe reports that a password is wanted, instead of failing

`GET /api/boxes/{ref}/vnc` returns `screen.needs_password: true`, and stops before choosing a
security type. "It wants a password" is a fact about the far machine worth putting on screen
before anyone has typed anything, and the panel that says *AuthVNC · RFB 003.008 · wants a
password* with a field already visible is a different experience from a box that says "will
not do a session without authentication".

`AuthenticationNeeded` is a separate exception for the same reason: every other failure in
this protocol ends in a sentence a person reads and stops on. This one ends in a question.

### 5. Single DES is implemented here, in Python

Type 2 is DES-ECB over a 16-byte challenge. Python's standard library has no DES. The
candidates:

| Option | Why not |
| --- | --- |
| `cryptography`'s TripleDES, with three identical key parts (which *is* single DES) | Works, and was verified against — but the symbol has been announced for removal (48.0.0), and it would be a hard dependency for sixteen bytes. "Install a maintained library" is not an answer when the library is the thing scheduled to drop the cipher. |
| `ctypes` into the system `libcrypto` | Present on many machines, absent on slim images, and a version-skewed ABI in the middle of an authentication path. |
| `pycryptodome` | A second crypto provider in a project that has one, for one 8-byte block cipher. |
| **the tables, here** | ~200 lines, no dependency, and *falsifiable against the standard*. |

`boxganizer/des.py` is that last row, and the honesty of it rests entirely on how it is
tested: `tests/test_vnc_auth.py` checks the FIPS PUB 46-3 worked example
(`133457799BBCDFF1 / 0123456789ABCDEF → 85E813540F0AB405`), a second published vector chosen
because it is where permutation mistakes surface, and — when `cryptography` happens to be
installed — **3000 random key/plaintext pairs against a third-party implementation**, skipped
rather than failed when it is not.

That cross-check is not a courtesy. The first version of this module had the initial
permutation written column-major and three S-boxes half-remembered from the specification, and
it produced eight bytes of confident nonsense in every single case, raising nothing. A cipher
is the one place in this codebase where "it runs" and "it works" are maximally far apart: a
wrong table does not crash, it fails authentication forever.

### 6. A server that hangs up mid-close cannot fail a request

`Screen.close()` swallows `CancelledError` from `wait_closed()`. The waiter belongs to the
transport, and a peer closing at the same instant hands back a cancellation about the socket —
which, allowed to escape cleanup code, turned an unrelated click into a 500. This was found by
using the panel, not by the unit tests, and the test for it exists now.

## Consequences

**What this buys.** Authenticated screens are usable: a password typed once per connection, a
file for the machine you watch every day, and the far machine's name and size on the panel
before either.

**What this costs.** An endpoint that did not exist (`/vnc/open`), a session id in the stream's
query string, and a cipher module in a knowledge-management app. The session id is the
unavoidable part: authentication must happen before the stream, and the stream must know which
authentication it belongs to.

**What is deliberately still missing, and stated rather than hidden.**

* No `None`-or-ask ordering problem: the panel asks only when the server asked.
* No password change / no challenge retry: RFB refuses the connection, it does not offer a
  second attempt, so a wrong password means pressing "Show the screen" again.
* No `tight`/`hextile`/`zlib` decoding, unchanged from ADR 15: Raw only.
* No secret in the library, and now a test that reads the database file's bytes to prove it:
  `test_a_password_used_to_look_at_a_screen_never_enters_the_library`.
* VNC passwords are eight characters. A longer one is *truncated*, by the protocol, so a box
  that accepts a 20-character password would be lying about what it sends. The field does not
  say "8 characters"; the DES module says so in the docstring of `vnc_key`, and the failure is
  the server's legible refusal.

## Verification

Driven against a Python RFB server that offers type 2 only, in the browser: the panel reports
"wants a password" with the field already visible; clicking with nothing typed answers the
server's own sentence; a wrong password answers *"the server refused that password (reason: bad
password)"*; the right one draws the screen (780 distinct colours sampled from the canvas) and
marks the line "live · authenticated"; the click and the two typed characters arrive as
`pointer mask=0 x=40 y=20` and keysyms `0x68`/`0x69`; the input field is empty afterwards; and
after a reload the panel asks again, with `metadata: {}` on the box and no trace of the secret
in the database file.
