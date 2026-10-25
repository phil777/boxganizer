"""MQTT: reading an address, keeping a value, and surviving a broker that drops."""

from __future__ import annotations

import asyncio
import sys
import types
from datetime import UTC, datetime
from typing import Any

import pytest

from boxganizer import mqtt
from boxganizer.errors import MqttError

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
    assert mqtt.STATES == ("connecting", "live", "value", "retrying", "error")
