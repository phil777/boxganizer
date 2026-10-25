"""MQTT: the last value published on a topic.

A `mqtt` box points at a broker with an `mqtt://` or `mqtts://` address, and the
topic it listens to is the box's `topic` metadata — or the path of that address,
for a box written the shorter way::

    mqtt://user:secret@cellar.example:1883/cellar/temp

The credentials stay here. Nothing that leaves this module repeats them: the API
hands the page a *public* target (host, port, topic, whether it is secured and
whether it carries credentials) and never the password, and log lines get the same
treatment through :func:`redact`.

Reading the broker is a stream of :class:`Event` values rather than a callback, so
the web layer can forward it as it stands and a test can hand the app a generator
of its own. `aiomqtt` is imported where it is used: a Boxganizer without the
`live` extra still opens a database full of MQTT boxes and says so.
"""

from __future__ import annotations

import asyncio
import base64
import ssl
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

from boxganizer.errors import MqttError

#: The ports a broker answers on when the address does not say.
DEFAULT_PORTS = {"mqtt": 1883, "mqtts": 8883}

#: How long to wait before reconnecting, and the ceiling it grows to.
RETRY_SECONDS = 2.0
MAX_RETRY_SECONDS = 30.0

#: States an event stream moves through. `value` carries a reading; the rest do not.
STATES = ("connecting", "live", "value", "retrying", "error")


@dataclass(frozen=True)
class Target:
    """Where to connect and what to listen to."""

    scheme: str
    host: str
    port: int
    topic: str
    username: str | None = None
    password: str | None = None

    @property
    def secured(self) -> bool:
        return self.scheme == "mqtts"

    @property
    def has_credentials(self) -> bool:
        return bool(self.username or self.password)

    def as_public_dict(self) -> dict[str, object]:
        """What the interfaces may show: the address without the way in."""
        return {
            "scheme": self.scheme,
            "host": self.host,
            "port": self.port,
            "topic": self.topic,
            "secured": self.secured,
            "credentials": self.has_credentials,
        }


@dataclass(frozen=True)
class Reading:
    """One message, as it arrived."""

    topic: str
    value: str
    received_at: datetime
    retained: bool = False
    #: "utf-8" when the payload was text, "base64" when it was not.
    encoding: str = "utf-8"

    def as_dict(self) -> dict[str, object]:
        return {
            "topic": self.topic,
            "value": self.value,
            "received_at": self.received_at.isoformat(),
            "retained": self.retained,
            "encoding": self.encoding,
        }


@dataclass(frozen=True)
class Event:
    """One step of a subscription: a state, and the message if there is one."""

    state: str
    reading: Reading | None = None
    detail: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "state": self.state,
            "reading": self.reading.as_dict() if self.reading else None,
            "detail": self.detail,
        }


#: Overridable so a test does not sit through a real backoff.
_sleep = asyncio.sleep

#: Something that turns a target into events. The web app holds one, so a test can
#: hold the page still and feed it a story of its own.
EventStream = Callable[[Target], AsyncIterator[Event]]


def parse_target(resource_uri: str | None, topic_metadata: str | None = None) -> Target:
    """Read a box's address into a :class:`Target`, or say what is wrong with it.

    The topic is the box's `topic` metadata first and the path of the address
    second; both are legal, and the type insists on at least one of them.
    """
    raw = (resource_uri or "").strip()
    if not raw:
        raise MqttError("an MQTT box needs an mqtt:// or mqtts:// address")
    parts = urlsplit(raw)
    scheme = parts.scheme.lower()
    if scheme not in DEFAULT_PORTS:
        wanted = " or ".join(f"{name}://" for name in sorted(DEFAULT_PORTS))
        raise MqttError(f"{raw!r} is not an MQTT address; write it as {wanted}host[:port]/topic")
    if not parts.hostname:
        raise MqttError(f"{raw!r} names no host to connect to")

    username: str | None = None
    password: str | None = None
    # `urlsplit` stops at the first `@`, and a password is allowed to contain one
    # when it is percent-encoded; the authority is split at its last `@` instead.
    authority = parts.netloc
    if "@" in authority:
        userinfo, _, _host = authority.rpartition("@")
        login, _, secret = userinfo.partition(":")
        username = unquote(login) or None
        password = unquote(secret) or None

    # `parts.port` is a property that raises instead of answers when the address
    # says `991883` or `http`. A box is written by hand, so that is a box to fix,
    # and the answer the interfaces show for it is a `MqttError`, not a traceback.
    try:
        written = parts.port
    except ValueError as exc:
        raise MqttError(f"{raw!r} carries a port that is not a port ({exc})") from exc
    port = DEFAULT_PORTS[scheme] if written is None else written
    if not 1 <= port <= 65535:
        raise MqttError(f"port {port} is not a port")

    topic = (topic_metadata or "").strip() or unquote(parts.path).strip("/")
    if not topic:
        raise MqttError(f"{raw!r} says which broker, but not which topic")
    return Target(
        scheme=scheme,
        host=parts.hostname,
        port=port,
        topic=topic,
        username=username,
        password=password,
    )


def redact(resource_uri: str | None) -> str:
    """The address with the password gone, for anything that may be printed."""
    raw = (resource_uri or "").strip()
    if not raw or "@" not in raw:
        return raw
    parts = urlsplit(raw)
    userinfo, _, host = parts.netloc.rpartition("@")
    login, _sep, _secret = userinfo.partition(":")
    shown = f"{login}:•••@{host}"
    return urlunsplit((parts.scheme, shown, parts.path, parts.query, parts.fragment))


def reading_from(
    topic: str, payload: bytes, *, received_at: datetime | None = None, retained: bool = False
) -> Reading:
    """A reading from the bytes the broker sent, kept as text when it is text."""
    moment = received_at or datetime.now(UTC)
    try:
        return Reading(
            topic=topic, value=payload.decode("utf-8"), received_at=moment, retained=retained
        )
    except UnicodeDecodeError:
        return Reading(
            topic=topic,
            value=base64.b64encode(payload).decode("ascii"),
            received_at=moment,
            retained=retained,
            encoding="base64",
        )


class Latest:
    """The last reading the server saw for each box.

    Kept so a page that opens later has something to show before the next message
    arrives, and so the value endpoint answers without waiting for the stream.
    """

    def __init__(self) -> None:
        self._by_box: dict[str, Reading] = {}

    def remember(self, box_uuid: str, reading: Reading) -> None:
        self._by_box[box_uuid] = reading

    def of(self, box_uuid: str) -> Reading | None:
        return self._by_box.get(box_uuid)

    def forget(self, box_uuid: str) -> None:
        self._by_box.pop(box_uuid, None)

    def __len__(self) -> int:
        return len(self._by_box)


def _client_arguments(target: Target) -> dict[str, Any]:
    """Keyword arguments for `aiomqtt.Client`, TLS included when the scheme says so."""
    arguments: dict[str, Any] = {
        "hostname": target.host,
        "port": target.port,
        "username": target.username,
        "password": target.password,
        "identifier": f"boxganizer-{target.topic}",
    }
    if target.secured:
        arguments["tls_context"] = ssl.create_default_context()
    return arguments


def _payload_of(message: Any) -> bytes:
    raw = getattr(message, "payload", b"")
    if isinstance(raw, bytes):
        return raw
    if isinstance(raw, bytearray | memoryview):
        return bytes(raw)
    return str(raw).encode("utf-8")


async def aiomqtt_events(target: Target) -> AsyncIterator[Event]:
    """Connect, subscribe, and turn every message into an event.

    A broker that goes away is a Tuesday, not an error: the stream says it is
    retrying, waits with a backoff, and reconnects. A box that cannot be read at
    all is a refusal, and it is raised.
    """
    try:
        import aiomqtt
    except ModuleNotFoundError as exc:  # pragma: no cover - needs the extra absent
        raise MqttError(
            "the MQTT view needs the 'live' extra: pip install 'boxganizer[live]'"
        ) from exc

    yield Event(state="connecting")
    delay = RETRY_SECONDS
    while True:
        dropped: str = ""
        try:
            async with aiomqtt.Client(**_client_arguments(target)) as client:
                await client.subscribe(target.topic)
                yield Event(state="live")
                # A connection that lasted is worth waiting for again from the
                # first refusal, not from wherever the last backoff had grown to.
                delay = RETRY_SECONDS
                async for message in client.messages:
                    topic = str(getattr(message, "topic", None) or target.topic)
                    reading = reading_from(
                        topic,
                        _payload_of(message),
                        retained=bool(getattr(message, "retain", False)),
                    )
                    yield Event(state="value", reading=reading)
            # A broker that hangs up without an error is a drop like any other, and
            # waiting before going back is what keeps a fast-closing broker from
            # being reconnectled to as hard as the machine can manage.
            dropped = "the broker closed the subscription"
        except asyncio.CancelledError:
            raise
        except MqttError:
            # This is *our* MqttError, not `aiomqtt.MqttError` — two classes, one
            # name. Ours means a box that cannot be read at all, and is a refusal the
            # web layer turns into one `error` event. Everything the transport itself
            # raises, aiomqtt's own error included, is a broker being down, and goes
            # to the retry below.
            raise
        except Exception as exc:  # whatever the transport raises, the page is told
            dropped = f"{type(exc).__name__}: {exc}"[:300]
        yield Event(state="retrying", detail=dropped)
        await _sleep(delay)
        delay = min(delay * 2, MAX_RETRY_SECONDS)


async def read_once(
    target: Target,
    *,
    stream: EventStream | None = None,
    wait_s: float = 5.0,
) -> Reading | None:
    """The first value the broker sends, or None if none comes in `wait_s`.

    The CLI uses it: `box box value Cellar-temp` answers once and exits, where the
    web page stays open and keeps answering.
    """
    source = (stream or aiomqtt_events)(target)
    try:
        return await asyncio.wait_for(_first_value(source), wait_s)
    except TimeoutError:
        return None
    finally:
        # A generator left suspended holds its connection until the garbage
        # collector notices it, which is not an answer for a CLI that prints one
        # value and exits. An iterator that is not a generator has nothing to
        # close, so the question is asked rather than assumed.
        close = getattr(source, "aclose", None)
        if close is not None:
            await close()


async def _first_value(source: AsyncIterator[Event]) -> Reading | None:
    async for event in source:
        if event.state == "value" and event.reading is not None:
            return event.reading
    return None
