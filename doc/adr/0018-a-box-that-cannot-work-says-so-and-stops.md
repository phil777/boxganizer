# 18. A box that cannot work says so, and stops

Date: 2026-11-08
Status: accepted

## Context

The report was two sentences long and the second one was the whole bug:

> I created a MQTT box and it always displays "the stream dropped; going back to the broker".
> If I strace the process, it does not even attempt to connect to the mqtt server.

The cause was not in the MQTT code. `aiomqtt` ships as the `live` extra, and the box was made
against a pipx install, which does not have extras. `import aiomqtt` failed, the stream raised
one honest sentence — *"the MQTT view needs the 'live' extra"* — and then ended.

What the person saw was different, because of how `EventSource` behaves. When a stream ends,
the browser fires an `error` event that carries **nothing**: no status, no reason, no body. The
page answered that anonymous event with its own canned line, *"the stream dropped; going back
to the broker"*, overwriting the specific sentence that had arrived a moment earlier — and then
reconnected, forever, to a box that fails identically every time.

Three separate faults were stacked to produce one misleading message:

| Fault | Where | Effect |
| --- | --- | --- |
| A refusal and a drop shared one event name | `mqtt.STATES`, `feed()` | The page could not tell waiting from installing |
| The browser's bodyless `error` got a generic sentence | `mqttView` | The real reason was overwritten within a second |
| Nothing said the feature was absent until it failed | type list, dialog, `box types` | The person learned it after building the box |

## Decision

### 1. `retrying` and `refused` are different states, and the difference is the advice

```python
STATES = ("connecting", "live", "value", "retrying", "refused")
```

A broker that went away is a Tuesday: back off, go back, say so. A box that cannot be read at
all — no module, an address that names nothing, credentials the broker rejects — will fail the
same way at three in the morning, so retrying is not patience but noise. `MqttError` from our
own code is a refusal; anything the transport raises is a drop. The web layer emits
`event: refused` and ends; the page closes the stream and stops.

The old `error` state is gone rather than kept as an alias. A label map for a state that never
arrives is a place for a bug to live.

### 2. The page never lets a vague message overwrite a specific one

```js
let lastWords = "";   // the last thing the server actually said
let refused = false;  // nothing to add to a refusal, and nothing to retry
```

A bodyless `error` now reuses `lastWords` when there were any, and the generic "the stream
dropped" line survives only for what it now actually means: a connection that was working or
was trying, with nothing newer said about it.

This is the general shape of the fix, and it applies to anything that uses `EventSource`
(the VNC stream too): **an event with no information must not be allowed to win an argument
with an event that had some.**

### 3. A type declares the module its live behaviour needs

```python
class MQTTResource(DynamicResource):
    dependency = "aiomqtt"
    dependency_extra = "live"
```

`describe()` carries `available` and `unavailable_reason`, so the admission appears in three
places, all before a box is made: the type dropdown says *"📶 MQTT Sensor — not in this
install"*, the dialog shows the full reason in its own warning band, and `box types` notes it
beside the type.

Note what is *not* proposed: refusing to create the box. The box is legitimate — the graph, the
index, and another machine that does have the module are all real; a library written on a
workstation is routinely read on a laptop without the extras. What was unacceptable was
silence, not absence.

### 4. The install command is read off the running process, not off the documentation

`pip install "boxganizer[live]"` is a small lie to the largest group of people who will read
it: `box` is a console script inside an isolated environment, and installing into the wrong
Python puts the module where the running program will never look. So the advice is generated
from `sys.prefix`:

```
…/.local/share/pipx/venvs/boxganizer  →  pipx inject boxganizer aiomqtt
…/.local/share/uv/tools/boxganizer    →  uv tool install 'boxganizer[live]' --force
any other prefix                      →  python -m pip install 'boxganizer[live]'
```

`unavailable_reason()` is a classmethod for exactly this: the base wording names the module,
and the MQTT type overrides it with the sentence that includes the one command that works for
the install answering the question. Only the running process knows which install it is.

## Consequences

**What this buys.** One stream request instead of an endless retry loop. The reason on screen
instead of a placeholder. The reason *before* the box is built, in the list, the dialog and the
CLI. And a command that ends with the problem being fixed rather than with a search.

**What this costs.** Two states where there was one, and a per-type `find_spec` when a
descriptor is built — cached at import of the type module in effect, since it answers a
question about the machine rather than the box.

**Where the boundary is drawn.** `dependency` is declared only when a *box cannot do the thing
it says it does*. `pypdfium2` is also optional and also sometimes absent, but a PDF box without
it still shows the file and still holds its place in the graph; only the extracted text and the
thumbnail are poorer, and `search.py` already says "PDF text needs pypdfium2" where that is the
answer being asked for. A type is not marked unavailable for a degraded decoration.

**Still true, and deliberately:** the extras stay extras. A knowledge organizer should not drag
an MQTT client into every install because one box type speaks to a broker.

## Verification

Run against the real interpreter the report was made from — the pipx install, which genuinely
has no `aiomqtt` — driving the page in a browser:

- the dropdown option reads *"📶 MQTT Sensor — not in this install"*;
- the create dialog shows *"this install cannot listen to MQTT: the 'aiomqtt' module is
  missing. Install it, then start the server again — pipx inject boxganizer aiomqtt"*;
- the opened box shows the chip **"cannot listen"** with that sentence, unchanged;
- exactly **one** request reaches `/mqtt/stream` in eight seconds — the retry loop is gone;
- the server's access log agrees: one line, not a sawtooth.

951 → 962 tests, ruff and mypy clean.
