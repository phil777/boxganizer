"""MQTT: reading an address, keeping a value, and surviving a broker that drops."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from boxganizer import mqtt
from boxganizer.errors import MqttError
from boxganizer.models import Box
from boxganizer.store import BoxStore
from boxganizer.web.app import create_app

ADDRESS = "mqtt://cellar.example:1883/cellar/temp"


def target_of(uri: str, metadata_topic: str | None = None) -> mqtt.Target:
    return mqtt.parse_target(uri, metadata_topic)


# -- the address --------------------------------------------------------------


def test_a_plain_broker_address_is_read() -> None:
    target = target_of(ADDRESS)
    assert (target.scheme, target.host, target.port, target.topic) == (
        "mqtt",
        "cellar.example",
        1883,
        "cellar/temp",
    )
    assert target.username is None and target.password is None
    assert target.secured is False


def test_the_port_is_optional_and_the_secured_scheme_has_its_own() -> None:
    assert target_of("mqtt://broker.test/one/two").port == 1883
    secure = target_of("mqtts://broker.test/one/two")
    assert (secure.scheme, secure.port, secure.secured) == ("mqtts", 8883, True)


def test_a_user_and_password_may_be_written_in_the_address() -> None:
    target = target_of("mqtt://pbi:s3cr3t@broker.test:1883/cellar/temp")
    assert (target.username, target.password) == ("pbi", "s3cr3t")
    assert target.has_credentials is True


def test_a_password_holding_the_characters_that_split_an_address_is_encoded() -> None:
    # `p@ss:word` written as it must be on the wire: %40 is '@', %3A is ':'.
    target = target_of("mqtt://pbi:p%40ss%3Aword@broker.test/cellar/temp")
    assert (target.username, target.password) == ("pbi", "p@ss:word")


def test_the_topic_may_be_written_in_the_address_instead_of_the_metadata() -> None:
    assert target_of(ADDRESS).topic == "cellar/temp"


def test_the_metadata_topic_wins_over_the_path() -> None:
    target = target_of(ADDRESS, "kitchen/humidity")
    assert target.topic == "kitchen/humidity"


def test_a_blank_metadata_topic_falls_back_to_the_path() -> None:
    assert target_of(ADDRESS, "   ").topic == "cellar/temp"


@pytest.mark.parametrize(
    ("uri", "why"),
    [
        (None, "needs an mqtt"),
        ("", "needs an mqtt"),
        ("https://broker.test/topic", "not an MQTT address"),
        ("/srv/mqtt/broker", "not an MQTT address"),
        ("mqtt:///cellar/temp", "no host"),
        ("mqtt://broker.test", "not which topic"),
        ("mqtt://broker.test//", "not which topic"),
    ],
)
def test_an_address_that_cannot_be_connected_to_is_refused(uri: str | None, why: str) -> None:
    with pytest.raises(MqttError, match=why):
        target_of(uri)


def test_a_port_outside_the_universe_is_refused() -> None:
    with pytest.raises(MqttError, match="not a port"):
        target_of("mqtt://broker.test:991883/cellar/temp")


def test_the_public_form_of_a_target_keeps_the_way_in_out() -> None:
    public = target_of("mqtt://pbi:s3cr3t@broker.test:1883/cellar/temp").as_public_dict()
    assert public == {
        "scheme": "mqtt",
        "host": "broker.test",
        "port": 1883,
        "topic": "cellar/temp",
        "secured": False,
        "credentials": True,
    }
    assert "s3cr3t" not in repr(public)


def test_an_address_printed_for_a_human_loses_the_password() -> None:
    redacted = mqtt.redact("mqtt://pbi:s3cr3t@broker.test:1883/cellar/temp")
    assert redacted == "mqtt://pbi:•••@broker.test:1883/cellar/temp"
    assert "s3cr3t" not in redacted


def test_an_address_without_credentials_is_left_as_it_was_written() -> None:
    assert mqtt.redact(ADDRESS) == ADDRESS
    assert mqtt.redact(None) == ""


# -- a message ----------------------------------------------------------------


def test_a_text_payload_is_kept_as_text() -> None:
    moment = datetime(2026, 10, 25, 7, 40, tzinfo=UTC)
    reading = mqtt.reading_from("cellar/temp", b"12.5", received_at=moment)
    assert reading.value == "12.5"
    assert reading.encoding == "utf-8"
    assert reading.as_dict()["received_at"] == "2026-10-25T07:40:00+00:00"


def test_a_payload_that_is_not_text_is_kept_as_it_arrived() -> None:
    reading = mqtt.reading_from("camera/frame", b"\x00\x01\xff")
    assert (reading.encoding, reading.value) == ("base64", "AAH/")


def test_a_retained_message_says_so() -> None:
    assert mqtt.reading_from("t", b"1", retained=True).as_dict()["retained"] is True


def test_the_latest_reading_is_kept_per_box() -> None:
    latest = mqtt.Latest()
    first = mqtt.reading_from("a", b"1")
    second = mqtt.reading_from("b", b"2")
    latest.remember("box-1", first)
    latest.remember("box-2", second)
    assert latest.of("box-1") is first
    assert len(latest) == 2
    latest.remember("box-1", second)
    assert latest.of("box-1") is second
    latest.forget("box-1")
    assert latest.of("box-1") is None
    assert latest.of("never seen") is None


# -- the subscription ---------------------------------------------------------


class _Message:
    def __init__(self, payload: bytes, topic: str = "cellar/temp", retain: bool = False) -> None:
        self.payload = payload
        self.topic = topic
        self.retain = retain


class _AsyncIter:
    def __init__(self, items: list[Any]) -> None:
        self._items = list(items)

    def __aiter__(self) -> _AsyncIter:
        return self

    async def __anext__(self) -> Any:
        if not self._items:
            raise StopAsyncIteration
        item = self._items.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class _QuietStream:
    """A subscription that stays open and says nothing, as an idle broker does."""

    def __aiter__(self) -> _QuietStream:
        return self

    async def __anext__(self) -> Any:
        await asyncio.Event().wait()  # only a cancel, or the test ending, lets go of it
        raise AssertionError("a message cannot arrive on a stream that sends none")


class Broker:
    """What the scripted broker was asked to do.

    `connects` is one entry per accepted connection, with the arguments the client
    was built from; `waits` is every backoff the stream asked for, in order.
    """

    def __init__(self) -> None:
        self.connects: list[dict[str, Any]] = []
        self.waits: list[float] = []


def install_fake_aiomqtt(
    monkeypatch: pytest.MonkeyPatch, sessions: list[list[Any] | Exception]
) -> Broker:
    """Pretend `aiomqtt` is installed, and script what each connection does.

    One entry per connection attempt: the messages to read (an exception in the
    list is raised where it stands), or the exception the connect fails with.

    Past the end of the script the broker holds the connection open and keeps
    quiet. It used to replay its last session forever, which turned a reconnect
    loop that lost its plot into a machine out of memory instead of a failed test.
    """
    broker = Broker()

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs
            self.subscribed: str | None = None

        async def __aenter__(self) -> FakeClient:
            index = len(broker.connects)
            broker.connects.append(self.kwargs)
            if index >= len(sessions):
                self.messages: Any = _QuietStream()
                return self
            session = sessions[index]
            if isinstance(session, BaseException):
                raise session
            self.messages = _AsyncIter(list(session))
            return self

        async def __aexit__(self, *exc: object) -> bool:
            return False

        async def subscribe(self, topic: str) -> None:
            self.subscribed = topic

    module = types.ModuleType("aiomqtt")
    module.Client = FakeClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "aiomqtt", module)

    async def counted_sleep(seconds: float) -> None:
        """The backoff, recorded and not slept.

        It still hands the loop back: a wait that returns without ever suspending
        starves every timer in the process, so a deadline can never fire and the
        stream is off its end before anything can call it back.
        """
        broker.waits.append(seconds)
        await asyncio.sleep(0)

    monkeypatch.setattr(mqtt, "_sleep", counted_sleep)
    return broker


async def collect(events: Any, how_many: int) -> list[Any]:
    gathered = []
    async for event in events:
        gathered.append(event)
        if len(gathered) >= how_many:
            break
    if hasattr(events, "aclose"):
        await events.aclose()
    return gathered


def test_a_subscription_goes_connecting_then_live_then_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broker = install_fake_aiomqtt(monkeypatch, [[_Message(b"12.5"), _Message(b"13.0")]])
    target = target_of("mqtt://pbi:s3cr3t@broker.test:1883/cellar/temp")

    async def story() -> list[Any]:
        return await collect(mqtt.aiomqtt_events(target), 4)

    events = asyncio.run(story())

    assert [event.state for event in events] == ["connecting", "live", "value", "value"]
    assert events[2].reading is not None and events[2].reading.value == "12.5"
    assert events[3].reading is not None and events[3].reading.topic == "cellar/temp"
    # what the client was handed, credentials included, because it needs them
    assert broker.connects[0]["hostname"] == "broker.test"
    assert broker.connects[0]["port"] == 1883
    assert broker.connects[0]["username"] == "pbi"
    assert broker.connects[0]["password"] == "s3cr3t"


def test_a_secured_address_brings_a_tls_context(monkeypatch: pytest.MonkeyPatch) -> None:
    broker = install_fake_aiomqtt(monkeypatch, [[]])

    async def story() -> list[Any]:
        return await collect(mqtt.aiomqtt_events(target_of("mqtts://broker.test/one/two")), 2)

    events = asyncio.run(story())
    assert [event.state for event in events] == ["connecting", "live"]
    assert broker.connects[0]["tls_context"] is not None


def test_a_broker_that_drops_is_retried_rather_than_declared_dead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_aiomqtt(
        monkeypatch,
        [
            ConnectionResetError("broker went away"),
            [_Message(b"21.0"), RuntimeError("stream died")],
            [_Message(b"22.0")],
        ],
    )

    async def story() -> list[Any]:
        return await collect(mqtt.aiomqtt_events(target_of(ADDRESS)), 5)

    events = asyncio.run(story())

    assert [event.state for event in events] == [
        "connecting",
        "retrying",
        "live",
        "value",
        "retrying",
    ]
    assert "ConnectionResetError" in events[1].detail
    assert events[3].reading is not None and events[3].reading.value == "21.0"


def test_a_broker_that_hangs_up_without_an_error_is_still_waited_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clean close is a drop, not a reason to knock again at once."""
    broker = install_fake_aiomqtt(monkeypatch, [[_Message(b"12.5")], [_Message(b"13.0")]])

    async def story() -> list[Any]:
        return await collect(mqtt.aiomqtt_events(target_of(ADDRESS)), 6)

    events = asyncio.run(story())
    assert [event.state for event in events] == [
        "connecting",
        "live",
        "value",
        "retrying",
        "live",
        "value",
    ]
    assert "closed" in events[3].detail
    assert broker.waits == [mqtt.RETRY_SECONDS]  # one wait, between the two connections
    assert events[5].reading is not None and events[5].reading.value == "13.0"


def test_the_wait_before_going_back_grows_and_stops_growing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broker = install_fake_aiomqtt(
        monkeypatch, [ConnectionResetError("broker is down") for _ in range(9)]
    )

    async def story() -> list[Any]:
        return await collect(mqtt.aiomqtt_events(target_of(ADDRESS)), 8)

    events = asyncio.run(story())
    assert [event.state for event in events] == ["connecting", *("retrying" for _ in range(7))]
    # The wait doubles, then is held at the 30-second ceiling the module writes down.
    assert broker.waits == [2.0, 4.0, 8.0, 16.0, 30.0, 30.0]


def test_reading_once_answers_with_the_first_value_and_leaves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_aiomqtt(monkeypatch, [[_Message(b"7"), _Message(b"8")]])
    reading = asyncio.run(mqtt.read_once(target_of(ADDRESS), wait_s=2.0))
    assert reading is not None and reading.value == "7"


def test_reading_once_says_nothing_when_nothing_arrives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # One connection, no message, and the script is over: the broker holds the
    # connection open quietly and the reader gives up on its own deadline.
    broker = install_fake_aiomqtt(monkeypatch, [[]])
    reading = asyncio.run(mqtt.read_once(target_of(ADDRESS), wait_s=0.05))
    assert reading is None
    assert len(broker.connects) <= 2  # it waited its turn, it did not rattle the door


def test_a_stream_handed_in_by_a_test_is_used_as_it_stands() -> None:
    async def scripted(_target: mqtt.Target) -> Any:
        yield mqtt.Event(state="live")
        yield mqtt.Event(state="value", reading=mqtt.reading_from("t", b"from the test"))

    reading = asyncio.run(mqtt.read_once(target_of(ADDRESS), stream=scripted, wait_s=1.0))
    assert reading is not None and reading.value == "from the test"


def test_the_states_a_stream_may_report_are_written_down() -> None:
    """`retrying` and `refused` may not be collapsed into one "error".

    They are different advice. One means the broker will be back and the page should wait;
    the other means the box cannot work no matter how long anything waits, and the page must
    stop, say why, and say what to run.
    """
    assert mqtt.STATES == ("connecting", "live", "value", "retrying", "refused")
    assert "error" not in mqtt.STATES


# -- an install that cannot speak MQTT at all ------------------------------------------


def hide_aiomqtt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `import aiomqtt` and `find_spec("aiomqtt")` both say it is not installed.

    The extra is installed in a developer's venv, so the only honest way to exercise the
    missing-package path is to hide the module from the import machinery rather than to mock
    the code that asks — the reported bug lived precisely in what the real machinery raises.

    Both halves are replaced rather than the import list being edited in place: a finder
    inserted into `sys.meta_path` before recording it would be *in* the value pytest restores,
    and every later test in the process would find no `aiomqtt` either. That is a leak with the
    worst possible shape — it fails someone else's test, in random order.
    """

    def is_hidden(name: str) -> bool:
        return name == mqtt.DEPENDENCY or name.startswith(f"{mqtt.DEPENDENCY}.")

    real_find_spec = importlib.util.find_spec

    def find_spec(name: str, *args: object, **kwargs: object) -> object:
        if is_hidden(name):
            return None
        return real_find_spec(name, *args, **kwargs)  # type: ignore[arg-type]

    class Hidden:
        def find_spec(self, name: str, path: object = None, target: object = None) -> None:
            if is_hidden(name):
                raise ModuleNotFoundError(f"No module named {name!r}", name=name)
            return None

    monkeypatch.setattr(importlib.util, "find_spec", find_spec)
    monkeypatch.setattr(sys, "meta_path", [Hidden(), *sys.meta_path])
    monkeypatch.delitem(sys.modules, mqtt.DEPENDENCY, raising=False)


def test_an_install_without_the_extra_is_refused_and_tells_you_what_to_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The report this fixes: a box that never connected, described as a dropped stream."""
    hide_aiomqtt(monkeypatch)
    assert mqtt.has_dependency() is False

    async def story() -> list[Any]:
        return await collect(mqtt.aiomqtt_events(target_of(ADDRESS)), 1)

    with pytest.raises(mqtt.MqttError) as caught:
        asyncio.run(story())
    message = str(caught.value)
    assert "aiomqtt" in message
    assert "install" in message.lower()
    # a refusal, not a state: nothing was yielded before it, and nothing will be after
    assert "pip" in message or "uv" in message


def test_the_install_command_matches_the_install_that_is_running(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`pip install` inside a pipx environment installs it somewhere nothing will look.

    The server knows which environment it is running in, so the advice is read from there:
    pipx gets a forced pipx install, uv gets a forced uv tool install, a plain venv gets pip.
    What none of them gets is an extra to add (ADR 24): `aiomqtt` is a dependency, so a missing
    module means the install is broken or stale, and `pipx inject` would be advice to go buy
    something the person already owns.
    """
    monkeypatch.setattr(sys, "prefix", "/home/pbi/.local/share/pipx/venvs/boxganizer")
    assert mqtt.install_hint() == "pipx install --force boxganizer"
    monkeypatch.setattr(sys, "prefix", "/home/pbi/.local/share/uv/tools/boxganizer")
    assert mqtt.install_hint() == "uv tool install --reinstall boxganizer"
    monkeypatch.setattr(sys, "prefix", str(tmp_path / ".venv"))
    assert mqtt.install_hint() == "python -m pip install --force-reinstall boxganizer"
    assert "boxganizer[" not in str(mqtt.missing_dependency())


def test_a_missing_module_is_a_refusal_in_the_stream_and_not_a_retry(tmp_path: Path) -> None:
    """The page has to be able to tell "wait" from "install something", and it cannot infer it."""

    async def refusing(_target: mqtt.Target) -> AsyncIterator[mqtt.Event]:
        raise mqtt.missing_dependency()
        yield  # an async generator that never produces anything (the import fails first)

    store = BoxStore(tmp_path / "noextra.db", content_root=tmp_path)
    try:
        box = store.add(Box(name="Boiler", box_type="mqtt", resource_uri=ADDRESS))
        app = create_app(store, content_root=tmp_path, mqtt_events=refusing)
        with TestClient(app) as client:
            body = client.get(f"/api/boxes/{box.uuid}/mqtt/stream").text
        assert "event: refused" in body
        assert "event: retrying" not in body
        assert "aiomqtt" in body
    finally:
        store.close()
