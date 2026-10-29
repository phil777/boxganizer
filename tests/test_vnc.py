"""VNC: the address, the protocol, and the picture that comes out of it.

There is no VNC server on the machine these tests run on, and pretending otherwise would
mean the protocol code is never executed. So the tests bring their own: `FakeRfbServer`
implements the server side of RFB 3.8 well enough to handshake, serve a framebuffer it
invents, and record what the client sent back. It is written from the protocol's message
layouts rather than from `vnc.py`, so a mistake in how this project read the spec does not
agree with itself.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import json
import socket
import struct
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from boxganizer import des, vnc
from boxganizer.errors import InvalidBoxContent
from boxganizer.models import Box
from boxganizer.store import BoxStore
from boxganizer.web.app import VNC_SESSION_TTL, create_app


class FakeRfbServer:
    """A VNC server small enough to read, running on localhost.

    It serves one client at a time, sends the frames it was given, then hangs up.
    """

    def __init__(
        self,
        *,
        version: str = "003.008",
        security_types: list[int] | None = None,
        width: int = 4,
        height: int = 3,
        name: str = "FakeVNC",
        frames: list[list[tuple[int, int, int, int, bytes]]] | None = None,
        close_after_frames: bool = True,
        password: str | None = None,
    ) -> None:
        self.version = version
        # A password asked for is a security type offered: type 2 on its own, or alongside
        # None when the server would take either, which is how the real ones are configured.
        self.password = password
        if security_types is not None:
            self.security_types = security_types
        else:
            self.security_types = [2] if password else [1]
        self.width = width
        self.height = height
        self.name = name
        self.frames = frames if frames is not None else []
        self.close_after_frames = close_after_frames
        self.port = 0
        self._writers: list[asyncio.StreamWriter] = []
        self.received: list[tuple[Any, ...]] = []
        self.client_version = ""
        self.chosen_security: int | None = None
        self.challenge = b""
        self.responses: list[bytes] = []
        self.authentication_ok: bool | None = None
        self.encodings: list[int] = []
        self.pixel_format: bytes = b""
        self.update_requests = 0
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> int:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        sockets = self._server.sockets or []
        self.port = int(sockets[0].getsockname()[1])
        return self.port

    async def stop(self) -> None:
        """Close the listener, then every session, then let the handlers finish.

        Closing the accepted writers by hand is what makes this deterministic: a handler
        blocked on a read wakes on EOF, runs its own `finally`, and the socket is closed by
        the code that opened it. `Server.abort_clients()` alone leaves a transport that was
        cancelled mid-close, and the file descriptor is released by the garbage collector —
        in whichever test happens to be running at the time, which is how a clean suite
        starts failing at random.
        """
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()
        for writer in list(self._writers):
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
        current = asyncio.current_task()
        for _ in range(10):  # up to half a second for handlers to unwind themselves
            leftovers = [task for task in asyncio.all_tasks() if task is not current]
            if not leftovers:
                break
            await asyncio.sleep(0.05)
        for task in [task for task in asyncio.all_tasks() if task is not current]:
            task.cancel()
        await asyncio.sleep(0.05)

    async def _read(self, reader: asyncio.StreamReader, count: int) -> bytes:
        return await reader.readexactly(count)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writers.append(writer)
        try:
            await self._session(reader, writer)
        except (asyncio.IncompleteReadError, ConnectionResetError, asyncio.CancelledError):
            pass  # the client hung up; that is a normal end for a probe
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            if writer in self._writers:
                self._writers.remove(writer)

    async def _session(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.write(f"RFB {self.version}\n".encode())
        await writer.drain()
        self.client_version = (await self._read(reader, 12)).decode("latin-1").strip()

        if self.version >= "003.007":
            writer.write(bytes([len(self.security_types), *self.security_types]))
            await writer.drain()
            self.chosen_security = (await self._read(reader, 1))[0]
            accepted = self.chosen_security in self.security_types
            if accepted and self.chosen_security == 2:
                accepted = await self._vnc_authenticate(reader, writer)
            writer.write(struct.pack(">I", 0 if accepted else 1))
            await writer.drain()
            if not accepted:
                reason = getattr(self, "explain_refusal", b"")
                if reason:
                    writer.write(struct.pack(">I", len(reason)) + reason)
                    await writer.drain()
                return
        encoded = self.name.encode()
        writer.write(
            struct.pack(">HH16sI", self.width, self.height, b"\x00" * 16, len(encoded)) + encoded
        )
        await writer.drain()

        while True:
            kind = (await self._read(reader, 1))[0]
            if kind == 0:  # SetPixelFormat: 3 padding + 16 format
                await self._read(reader, 3)
                self.pixel_format = await self._read(reader, 16)
                self.received.append(("pixel_format", self.pixel_format))
            elif kind == 1:  # SetEncoding: 1 padding + count + encodings
                padding = await self._read(reader, 1)
                count = struct.unpack(">H", await self._read(reader, 2))[0]
                blob = await self._read(reader, 4 * count)
                self.encodings = list(struct.unpack(f">{count}i", blob))
                self.received.append(("set_encoding", padding, self.encodings))
            elif kind == 2:  # FramebufferUpdateRequest
                body = await self._read(reader, 9)
                incremental, x, y, width, height = struct.unpack(">BHHHH", body)
                self.update_requests += 1
                self.received.append(("update", incremental, x, y, width, height))
                if self.frames:
                    await self._send_update(writer, self.frames.pop(0))
                    if not self.frames and self.close_after_frames:
                        await asyncio.sleep(0.05)
                        return
            elif kind == 4:  # KeyEvent: down u8, 2 padding, keysym u32
                body = await self._read(reader, 7)
                keysym = struct.unpack(">I", body[3:7])[0]
                self.received.append(("key", body[0], keysym))
            elif kind == 5:  # PointerEvent
                body = await self._read(reader, 5)
                mask = body[0]
                x, y = struct.unpack(">HH", body[1:5])
                self.received.append(("pointer", mask, x, y))
            else:
                return

    async def _vnc_authenticate(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> bool:
        """The server's half of security type 2: challenge, then check the answer.

        The expected response is computed here from the password the test supplied — with the
        same `des` module the client uses. That makes this a test of the exchange, not of the
        cipher; the cipher has its own test against a published vector and a reference
        implementation, where a wrong table cannot agree with itself.
        """
        self.challenge = bytes(range(1, 17))
        writer.write(self.challenge)
        await writer.drain()
        response = await self._read(reader, 16)
        self.responses.append(response)
        self.authentication_ok = self.password is not None and response == des.respond_to_challenge(
            self.challenge, self.password
        )
        if not self.authentication_ok:
            # 3.8 lets a server explain itself after a refusal, and the client is expected to
            # pass that sentence on rather than replacing it with "connection failed"
            self.explain_refusal = b"bad password"
        return bool(self.authentication_ok)

    async def _send_update(
        self, writer: asyncio.StreamWriter, rects: list[tuple[int, int, int, int, bytes]]
    ) -> None:
        payload = struct.pack(">BBH", 0, 0, len(rects))
        for x, y, width, height, pixels in rects:
            payload += struct.pack(">HHHHi", x, y, width, height, 0) + pixels
        writer.write(payload)
        await writer.drain()


def sse_events(text: str) -> list[tuple[str, str]]:
    """The `(event, data)` pairs of a server-sent-events body."""
    pairs: list[tuple[str, str]] = []
    current = ""
    for line in text.splitlines():
        if line.startswith("event:"):
            current = line[len("event:") :].strip()
        elif line.startswith("data:"):
            pairs.append((current, line[len("data:") :].strip()))
    return pairs


def solid(width: int, height: int, rgba: tuple[int, int, int, int]) -> bytes:
    return bytes(rgba) * (width * height)


def image_of(png: bytes) -> Image.Image:
    return Image.open(io.BytesIO(png))


class BackgroundServer:
    """A fake server on its own thread and event loop.

    The protocol tests drive everything inside one `asyncio.run`, and that is fine: the
    loop lives as long as the scene. The web tests are different — they hand the port to
    an ASGI app running in TestClient's loop, in another thread — and a server started by
    an `asyncio.run` that has already returned has a socket nobody will ever accept on.
    Connections then seem to work, hang, and time out, which is the worst possible way for
    a test of a *timeout* to fail. So this keeps a loop running for the life of the test.
    """

    def __init__(self, server: FakeRfbServer) -> None:
        self.server = server
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self.server.start())
        self.ready.set()
        self.loop.run_forever()

    def start(self) -> BackgroundServer:
        self.thread.start()
        self.ready.wait(5.0)
        return self

    @property
    def port(self) -> int:
        return self.server.port

    @property
    def received(self) -> list[tuple[Any, ...]]:
        """What the client sent, as the fake server recorded it."""
        return self.server.received

    def stop(self) -> None:
        # wait for the close to actually run: stopping the loop first leaves the
        # coroutine un-awaited, and Python is right to complain about that
        asyncio.run_coroutine_threadsafe(self.server.stop(), self.loop).result(timeout=5.0)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5.0)
        self.loop.close()


def background(**kwargs: Any) -> BackgroundServer:
    """A fake VNC server the web app can be pointed at."""
    return BackgroundServer(FakeRfbServer(**kwargs)).start()


# -- the address --------------------------------------------------------------


def destination_of(resource: str | None, **metadata: str) -> vnc.Destination:
    box = Box(name="Screen", box_type="vnc", resource_uri=resource, metadata=dict(metadata))
    return vnc.destination(box)


def test_an_address_without_a_port_is_the_one_vnc_starts_on() -> None:
    assert destination_of("vnc://workstation").port == 5900


def test_a_number_that_cannot_be_a_port_meant_a_display() -> None:
    """`vnc://host:1` is display 1, which is port 5901, and the box remembers both."""
    dest = destination_of("vnc://workstation:1")
    assert dest.port == 5901
    assert dest.display == 1
    assert dest.url == "vnc://workstation:1"  # written back the way it was written down


def test_a_number_big_enough_to_be_a_port_is_a_port() -> None:
    dest = destination_of("vnc://workstation:6001")
    assert dest.port == 6001
    assert dest.display is None
    assert dest.url == "vnc://workstation:6001"


def test_a_host_on_its_own_is_enough() -> None:
    assert destination_of("workstation").host == "workstation"


@pytest.mark.parametrize(
    "address",
    [
        "vnc://secret@workstation",
        "vnc://user:secret@workstation:5900",
        "http://workstation:5900",
        "vnc://",
        "vnc://host:99999",
        "",
    ],
)
def test_an_address_that_cannot_be_used_is_refused_where_it_is_written(address: str) -> None:
    box = Box(name="Screen", box_type="vnc", resource_uri=address)
    from boxganizer.boxtypes.dynamic import VNCResource

    with pytest.raises(Exception) as caught:
        VNCResource.check(box)
    assert "vnc" in str(caught.value).lower() or "password" in str(caught.value).lower()


def test_a_password_is_refused_by_name_not_silently_dropped() -> None:
    with pytest.raises(vnc.VncError) as caught:
        destination_of("vnc://letmein@workstation")
    assert "password" in str(caught.value)


# -- the handshake ------------------------------------------------------------


def test_probe_asks_what_the_machine_is_and_hangs_up() -> None:
    async def scene() -> vnc.Probe:
        server = FakeRfbServer(name="TigerVNC", width=800, height=600)
        await server.start()
        try:
            return await vnc.probe(vnc.Destination(host="127.0.0.1", port=server.port))
        finally:
            await server.stop()  # in this loop, while there is still a loop to do it in

    found = asyncio.run(scene())
    assert found.version == "003.008"
    assert found.server_name == "TigerVNC"
    assert (found.width, found.height) == (800, 600)
    assert found.accepts_none


def test_a_probe_reports_that_the_screen_wants_a_password() -> None:
    """A password is a fact about the server, so probing answers with it instead of failing."""

    async def scene() -> vnc.Probe:
        server = FakeRfbServer(security_types=[2])  # VNC authentication, nothing else
        await server.start()
        try:
            return await vnc.probe(vnc.Destination(host="127.0.0.1", port=server.port))
        finally:
            await server.stop()

    found = asyncio.run(scene())
    assert found.needs_password is True
    assert found.accepts_none is False
    assert found.as_dict()["needs_password"] is True


def test_a_session_without_the_password_the_server_wants_asks_for_one() -> None:
    """Not a sentence to give up on: the page has to be able to tell a field from a failure."""

    async def scene() -> str:
        server = FakeRfbServer(password="hunter2")
        await server.start()
        try:
            await vnc.Screen(vnc.Destination(host="127.0.0.1", port=server.port)).start()
        except vnc.AuthenticationNeeded as exc:
            return str(exc)
        finally:
            await server.stop()
        raise AssertionError("the server took a session with no password")

    message = asyncio.run(scene())
    assert "password" in message
    assert "no password to offer" in message


def test_the_right_password_opens_the_screen_and_the_wrong_one_does_not() -> None:
    async def scene(password: str | None) -> tuple[str, bytes, bool]:
        server = FakeRfbServer(password="hunter2")
        await server.start()
        screen = vnc.Screen(vnc.Destination(host="127.0.0.1", port=server.port), password=password)
        try:
            await screen.start()
            # what comes back on success is the answer the server was meant to receive; the
            # fact that `start` returned at all is the proof the SecurityResult was accepted
            return server.responses[0], server.challenge, bool(screen.used_password)
        except vnc.VncError as exc:
            return str(exc), server.challenge, False
        finally:
            await screen.close()
            await server.stop()

    response, challenge, used = asyncio.run(scene("hunter2"))
    assert response == des.respond_to_challenge(challenge, "hunter2")
    assert used is True

    message, _challenge, used = asyncio.run(scene("wrong"))
    assert "refused that password" in message, message
    assert "bad password" in message  # the server's own reason travels with it
    assert used is False


def test_a_password_from_a_file_is_read_at_the_moment_of_use(tmp_path: Path) -> None:
    """The path is the box's business; the bytes belong to the file, which stays outside."""
    missing = Box(name="gone", box_type="vnc", resource_uri="vnc://h:5900", metadata={})
    assert vnc.password_for(missing) is None

    gone = Box(
        name="gone",
        box_type="vnc",
        resource_uri="vnc://h:5900",
        metadata={vnc.PASSWORD_FILE: str(tmp_path / "nope")},
    )
    with pytest.raises(vnc.VncError, match="could not be read"):
        vnc.password_for(gone)

    secret = tmp_path / "vnc.pass"
    secret.write_text("hunter2\n", encoding="utf-8")  # the newline every editor and echo adds
    box = Box(
        name="f",
        box_type="vnc",
        resource_uri="vnc://h:5900",
        metadata={vnc.PASSWORD_FILE: str(secret)},
    )
    assert vnc.password_for(box) == "hunter2"
    # a password typed now outranks the one on disk, and neither is ever written back
    assert vnc.password_for(box, override="now") == "now"
    assert box.metadata == {vnc.PASSWORD_FILE: str(secret)}


def test_a_port_that_is_not_vnc_says_it_was_not_vnc() -> None:
    async def scene() -> str:
        async def not_vnc(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            await writer.drain()
            await asyncio.sleep(0.05)
            writer.close()

        server = await asyncio.start_server(not_vnc, "127.0.0.1", 0)
        port = int((server.sockets or [None])[0].getsockname()[1])  # type: ignore[index]
        try:
            await vnc.probe(vnc.Destination(host="127.0.0.1", port=port))
        except vnc.VncError as exc:
            return str(exc)
        finally:
            server.close()
            await server.wait_closed()
        return ""

    assert "did not speak VNC" in asyncio.run(scene())


# -- the picture --------------------------------------------------------------


def test_a_framebuffer_becomes_a_picture_of_the_right_size() -> None:
    """The pixels the server sent are the pixels on the page, in the right places."""

    async def scene() -> tuple[bytes, bytes]:
        red = solid(4, 3, (200, 10, 10, 255))
        server = FakeRfbServer(frames=[[(0, 0, 4, 3, red)]])
        await server.start()
        try:
            screen = vnc.Screen(vnc.Destination(host="127.0.0.1", port=server.port))
            await screen.start()
            frame = await screen.next_frame()
            await screen.close()
            return frame or b"", server.pixel_format
        finally:
            await server.stop()

    frame, pixel_format = asyncio.run(scene())
    image = image_of(frame)
    assert image.size == (4, 3)
    assert image.getpixel((0, 0))[:3] == (200, 10, 10)
    assert image.getpixel((3, 2))[:3] == (200, 10, 10)
    # the client asked for 32 bits, 24 used, little-endian, true colour — the layout
    # Pillow takes as RGBX with no shuffling
    assert pixel_format[0] == 32 and pixel_format[1] == 24 and pixel_format[3] == 1


def test_a_rectangle_that_only_covers_part_of_the_screen_leaves_the_rest_alone() -> None:
    """Incremental updates are the whole reason there is no timer: paint what moved."""

    async def scene() -> tuple[bytes, bytes]:
        blue = solid(4, 3, (10, 20, 200, 255))
        patch = solid(2, 1, (20, 200, 20, 255))
        server = FakeRfbServer(
            frames=[
                [(0, 0, 4, 3, blue)],
                [(1, 1, 2, 1, patch)],  # one row, in the middle of the second row
            ],
            close_after_frames=False,
        )
        await server.start()
        try:
            screen = vnc.Screen(vnc.Destination(host="127.0.0.1", port=server.port))
            await screen.start()
            first = await screen.next_frame()
            second = await screen.next_frame()
            await screen.close()
            return first or b"", second or b""
        finally:
            await server.stop()

    first, second = asyncio.run(scene())
    before = image_of(first)
    after = image_of(second)
    assert after.getpixel((1, 1))[:3] == (20, 200, 20)
    assert after.getpixel((2, 1))[:3] == (20, 200, 20)
    assert after.getpixel((0, 0))[:3] == before.getpixel((0, 0))[:3] == (10, 20, 200)
    assert after.getpixel((3, 2))[:3] == (10, 20, 200)


def test_the_client_asks_for_raw_and_for_news_rather_than_for_frames() -> None:
    """Raw because every server must have it; incremental because the server pushes."""

    async def scene() -> FakeRfbServer:
        server = FakeRfbServer(frames=[[(0, 0, 4, 3, solid(4, 3, (0, 0, 0, 255)))]] * 2)
        await server.start()
        try:
            screen = vnc.Screen(vnc.Destination(host="127.0.0.1", port=server.port))
            await screen.start()
            await screen.next_frame()
            await screen.next_frame()
            await screen.close()
            return server
        finally:
            await asyncio.sleep(0.05)  # let the handler see the hang-up and finish
            await server.stop()

    server = asyncio.run(scene())
    assert server.encodings == [0]
    assert server.client_version == "RFB 003.008"
    assert server.chosen_security == 1
    kinds = [entry[0] for entry in server.received]
    assert kinds.count("update") >= 2
    first_request = next(entry for entry in server.received if entry[0] == "update")
    assert first_request[1] == 0  # the whole screen first
    later = [entry for entry in server.received if entry[0] == "update"][1:]
    assert all(entry[1] == 1 for entry in later)  # and only news after that


def test_a_screen_too_big_to_hold_is_refused_before_it_is_allocated() -> None:
    async def scene() -> str:
        server = FakeRfbServer(width=9000, height=9000)
        await server.start()
        try:
            screen = vnc.Screen(vnc.Destination(host="127.0.0.1", port=server.port))
            await screen.start()
        except vnc.VncError as exc:
            return str(exc)
        finally:
            await server.stop()
        return ""

    assert "more than this box will hold" in asyncio.run(scene())


# -- input --------------------------------------------------------------------


def test_the_pointer_and_the_buttons_travel_together() -> None:
    """RFB has no press and release: it has a position and a mask, over and over."""

    async def scene() -> list[tuple[Any, ...]]:
        # the server stays up: this test is about what the client sends afterwards
        server = FakeRfbServer(
            frames=[[(0, 0, 4, 3, solid(4, 3, (0, 0, 0, 255)))]], close_after_frames=False
        )
        await server.start()
        try:
            screen = vnc.Screen(vnc.Destination(host="127.0.0.1", port=server.port))
            await screen.start()
            await screen.next_frame()
            await screen.pointer(12, 30, 1)
            await screen.pointer(12, 30, 0)
            await asyncio.sleep(0.05)
            await screen.close()
            await asyncio.sleep(0.1)  # the handler closes its side when it sees the EOF
            return list(server.received)
        finally:
            await server.stop()

    pointers = [entry for entry in asyncio.run(scene()) if entry[0] == "pointer"]
    assert pointers == [("pointer", 1, 12, 30), ("pointer", 0, 12, 30)]


def test_a_key_is_a_keysym_and_whether_it_is_being_held() -> None:
    async def scene() -> list[tuple[Any, ...]]:
        server = FakeRfbServer(
            frames=[[(0, 0, 4, 3, solid(4, 3, (0, 0, 0, 255)))]], close_after_frames=False
        )
        await server.start()
        try:
            screen = vnc.Screen(vnc.Destination(host="127.0.0.1", port=server.port))
            await screen.start()
            await screen.next_frame()
            await screen.key(vnc.keysym("a") or 0, down=True)
            await screen.key(vnc.keysym("Enter") or 0, down=False)
            await asyncio.sleep(0.05)
            await screen.close()
            return [entry for entry in server.received if entry[0] == "key"]
        finally:
            await server.stop()

    assert asyncio.run(scene()) == [("key", 1, 0x61), ("key", 0, 0xFF0D)]


def test_latin_letters_need_no_table_and_the_rest_have_one() -> None:
    assert vnc.keysym("k") == 0x6B
    assert vnc.keysym("é") == 0xE9  # Latin-1: the keysym is the code point
    assert vnc.keysym("ArrowUp") == 0xFF52
    assert vnc.keysym("F12") == 0xFFC9
    assert vnc.keysym("Ā") == 0x01000100  # past Latin-1, everything is prefixed
    assert vnc.keysym("🎯") == 0x0101F3AF


def test_a_key_with_no_meaning_is_dropped_rather_than_guessed_at() -> None:
    assert vnc.keysym("Dead") is None
    assert vnc.keysym("") is None
    assert vnc.keysym("\x07") is None  # a bell is not a keystroke


# -- through the page ---------------------------------------------------------


def test_the_page_asks_the_machine_what_it_is(tmp_path: Any) -> None:
    server = background(name="x11vnc", width=1024, height=768)
    store = BoxStore(tmp_path / "v.db", content_root=tmp_path)
    try:
        box = store.add(
            Box(
                name="Workstation",
                box_type="vnc",
                resource_uri=f"vnc://127.0.0.1:{server.port}",
            )
        )
        with TestClient(create_app(store, content_root=tmp_path)) as client:
            body = client.get(f"/api/boxes/{box.uuid}/vnc").json()
            assert body["reachable"] is True
            assert body["screen"]["server_name"] == "x11vnc"
            assert body["screen"]["width"] == 1024
    finally:
        store.close()
        server.stop()


def closed_port() -> int:
    """A port nothing is listening on, taken from the operating system and given back.

    Hard-coding a "surely closed" port is a flake waiting to happen — on some machines
    something is on 1, and on some the refusal takes a different shape. Asking for a free
    one and closing it is the same trick the fake server uses to avoid collisions.
    """
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = int(probe.getsockname()[1])
    probe.close()
    return port


def test_a_machine_that_is_not_there_answers_with_a_reason_not_an_error(
    tmp_path: Any,
) -> None:
    store = BoxStore(tmp_path / "v2.db", content_root=tmp_path)
    try:
        box = store.add(
            Box(name="Gone", box_type="vnc", resource_uri=f"vnc://127.0.0.1:{closed_port()}")
        )
        with TestClient(create_app(store, content_root=tmp_path)) as client:
            body = client.get(f"/api/boxes/{box.uuid}/vnc").json()
            assert body["reachable"] is False
            assert body["error"]
    finally:
        store.close()


def test_the_stream_carries_a_frame_and_the_session_that_owns_it(tmp_path: Any) -> None:
    red = solid(4, 3, (210, 20, 20, 255))
    server = background(frames=[[(0, 0, 4, 3, red)]])
    store = BoxStore(tmp_path / "v3.db", content_root=tmp_path)
    try:
        box = store.add(
            Box(
                name="Workstation",
                box_type="vnc",
                resource_uri=f"vnc://127.0.0.1:{server.port}",
            )
        )
        with TestClient(create_app(store, content_root=tmp_path)) as client:
            # A picture is asked for only after a session exists, because that is the order
            # that lets a password be typed between the two calls.
            assert client.get(f"/api/boxes/{box.uuid}/vnc/stream").status_code == 404
            opened = client.post(f"/api/boxes/{box.uuid}/vnc/open", json={}).json()
            assert opened["password_needed"] is False
            assert (opened["width"], opened["height"]) == (4, 3)
            # the fake server hangs up after its frame, so the stream ends by itself
            text = client.get(f"/api/boxes/{box.uuid}/vnc/stream?session={opened['session']}").text
            events = sse_events(text)
            assert [kind for kind, _ in events] == ["info", "frame", "end"]
            info = json.loads(events[0][1])
            assert (info["width"], info["height"]) == (4, 3)
            assert info["session"] == opened["session"]
            picture = image_of(base64.b64decode(events[1][1]))
            assert picture.getpixel((0, 0))[:3] == (210, 20, 20)
    finally:
        store.close()
        server.stop()


def test_input_reaches_a_live_session_and_a_gone_one_is_refused(tmp_path: Any) -> None:
    """A click and a keystroke, into a session the page is watching.

    The stream is read on its own thread and left open, because that is what the page does,
    and because an asyncio socket belongs to the loop that opened it: starting a screen from
    the test and then writing to it from the app's loop fails in ways that have nothing to
    do with the endpoint. Same client, same loop, two threads — the session the assertion
    aims at is the one the stream created.
    """
    red = solid(4, 3, (0, 0, 0, 255))
    server = background(frames=[[(0, 0, 4, 3, red)]], close_after_frames=False)
    store = BoxStore(tmp_path / "v4.db", content_root=tmp_path)
    try:
        box = store.add(
            Box(
                name="Workstation",
                box_type="vnc",
                resource_uri=f"vnc://127.0.0.1:{server.port}",
            )
        )
        ready = threading.Event()
        lines: list[str] = []
        found: dict[str, str] = {}

        with TestClient(create_app(store, content_root=tmp_path)) as client:
            # Build the request model before the second thread starts. Pydantic assembles a
            # model's schema on first use, and two threads reaching the same model class for
            # the first time at once has been seen to lose that race — a test-only hazard
            # here, but one that fails the suite at random.
            assert (
                client.post(
                    f"/api/boxes/{box.uuid}/vnc/input",
                    json={"session": "warm-up", "kind": "pointer", "x": 0, "y": 0},
                ).status_code
                == 410
            )

            opened = client.post(f"/api/boxes/{box.uuid}/vnc/open", json={}).json()
            assert opened["session"]
            session_id = opened["session"]

            def watch() -> None:
                with client.stream(
                    "GET", f"/api/boxes/{box.uuid}/vnc/stream?session={session_id}"
                ) as response:
                    for line in response.iter_lines():
                        lines.append(line)
                        if line.startswith("data:") and "session" in line:
                            found["session"] = json.loads(line[len("data:") :].strip())["session"]
                            ready.set()

            reader = threading.Thread(target=watch, daemon=True)
            reader.start()
            assert ready.wait(5.0), "the stream never announced a session: " + repr(lines[:6])
            session = found["session"]

            answer = client.post(
                f"/api/boxes/{box.uuid}/vnc/input",
                json={"session": session, "kind": "pointer", "x": 2, "y": 1, "button_mask": 1},
            )
            assert answer.status_code == 200
            typed = client.post(
                f"/api/boxes/{box.uuid}/vnc/input",
                json={"session": session, "kind": "key", "key": "Enter", "down": True},
            )
            assert typed.status_code == 200
            # a coordinate past the edge is brought back rather than sent as nonsense
            client.post(
                f"/api/boxes/{box.uuid}/vnc/input",
                json={"session": session, "kind": "pointer", "x": 9999, "y": -4, "button_mask": 0},
            )
            ignored = client.post(
                f"/api/boxes/{box.uuid}/vnc/input",
                json={"session": session, "kind": "key", "key": "Dead"},
            )
            assert ignored.json().get("ignored")

            missing = client.post(
                f"/api/boxes/{box.uuid}/vnc/input",
                json={"session": "no-such-session", "kind": "key", "key": "a"},
            )
            assert missing.status_code == 410
            assert missing.json()["detail"]["error"] == "session_gone"

            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline and not any(
                entry[0] == "key" for entry in server.received
            ):
                time.sleep(0.05)

            # Stop the session, which ends the stream, which lets the thread finish: leaving
            # a reader attached to a client that is about to close is how a test comes to
            # fail in someone else's teardown.
            closed = client.post(f"/api/boxes/{box.uuid}/vnc/close", json={"session": session})
            assert closed.json()["closed"] is True
            reader.join(timeout=5.0)
            assert not reader.is_alive(), "the event stream never ended after the session closed"

        pointers = [entry for entry in server.received if entry[0] == "pointer"]
        assert pointers[0] == ("pointer", 1, 2, 1)
        # clamped to the screen the server actually serves: 4 wide, 3 high
        assert ("pointer", 0, 3, 0) in pointers
        assert ("key", 1, 0xFF0D) in server.received
    finally:
        server.stop()
        store.close()


def test_a_stream_that_ends_leaves_no_session_behind(tmp_path: Any) -> None:
    """Whatever the page does with the id, a finished stream gives the socket back."""
    red = solid(4, 3, (5, 5, 5, 255))
    server = background(frames=[[(0, 0, 4, 3, red)]])
    store = BoxStore(tmp_path / "v7.db", content_root=tmp_path)
    try:
        box = store.add(
            Box(
                name="Workstation",
                box_type="vnc",
                resource_uri=f"vnc://127.0.0.1:{server.port}",
            )
        )
        app = create_app(store, content_root=tmp_path)
        with TestClient(app) as client:
            session = client.post(f"/api/boxes/{box.uuid}/vnc/open", json={}).json()["session"]
            assert (
                client.get(f"/api/boxes/{box.uuid}/vnc/stream?session={session}").text.count(
                    "event: end"
                )
                == 1
            )
            assert getattr(app.state, "vnc_sessions", {}) == {}
    finally:
        store.close()
        server.stop()


def test_an_opened_but_never_watched_screen_is_reclaimed(tmp_path: Any) -> None:
    """A browser closed between opening and streaming must not hold a socket forever."""
    # a frame per session: a screen is not "started" until the server answers the first
    # update request, and the second open needs its own answer. `close_after_frames=False`
    # keeps both sessions alive so the reclaim is the only thing that can end the first.
    grey = [(0, 0, 4, 3, solid(4, 3, (5, 5, 5, 255)))]
    server = background(frames=[grey, grey], close_after_frames=False)
    store = BoxStore(tmp_path / "v8.db", content_root=tmp_path)
    try:
        box = store.add(
            Box(name="Workstation", box_type="vnc", resource_uri=f"vnc://127.0.0.1:{server.port}")
        )
        app = create_app(store, content_root=tmp_path)
        with TestClient(app) as client:
            session = client.post(f"/api/boxes/{box.uuid}/vnc/open", json={}).json()["session"]
            held = app.state.vnc_sessions
            assert session in held
            # pretend the page went away the instant it had the id
            held[session].opened_at -= VNC_SESSION_TTL + 1
            again = client.post(f"/api/boxes/{box.uuid}/vnc/open", json={}).json()
            assert again["session"] != session
            assert session not in app.state.vnc_sessions
            assert again["session"] in app.state.vnc_sessions
            # hand the second one back too: a session left open here leaves the fake
            # server's handler parked on a read, and teardown waits for nothing
            client.post(f"/api/boxes/{box.uuid}/vnc/close", json={"session": again["session"]})
    finally:
        store.close()
        server.stop()


def test_a_box_that_is_not_a_screen_is_refused(tmp_path: Any) -> None:
    store = BoxStore(tmp_path / "v5.db", content_root=tmp_path)
    try:
        folder = store.add(Box(name="Cellar", box_type="folder"))
        with TestClient(create_app(store, content_root=tmp_path)) as client:
            answer = client.get(f"/api/boxes/{folder.uuid}/vnc")
            assert answer.status_code == 415
            assert answer.json()["detail"]["error"] == "not_a_screen"
    finally:
        store.close()


def test_the_type_refuses_a_password_before_the_box_is_stored(tmp_path: Any) -> None:
    store = BoxStore(tmp_path / "v6.db", content_root=tmp_path)
    try:
        with pytest.raises(Exception) as caught:
            store.add(Box(name="Bad", box_type="vnc", resource_uri="vnc://secret@host"))
        assert "password" in str(caught.value)
    finally:
        store.close()


def test_the_open_endpoint_is_where_a_password_is_asked_for(tmp_path: Any) -> None:
    """The shape the page needs: a field, not a stack trace, when the screen wants one."""
    server = background(password="hunter2", frames=[[(0, 0, 4, 3, solid(4, 3, (1, 2, 3, 255)))]])
    store = BoxStore(tmp_path / "v9.db", content_root=tmp_path)
    try:
        box = store.add(
            Box(name="Workstation", box_type="vnc", resource_uri=f"vnc://127.0.0.1:{server.port}")
        )
        with TestClient(create_app(store, content_root=tmp_path)) as client:
            asked = client.post(f"/api/boxes/{box.uuid}/vnc/open", json={}).json()
            assert asked["password_needed"] is True
            assert asked["session"] is None
            assert "password" in asked["reason"]

            probe = client.get(f"/api/boxes/{box.uuid}/vnc").json()
            assert probe["reachable"] is True
            assert probe["screen"]["needs_password"] is True

            opened = client.post(
                f"/api/boxes/{box.uuid}/vnc/open", json={"password": "hunter2"}
            ).json()
            assert opened["password_needed"] is False
            assert opened["used_password"] is True
            assert opened["session"]
            assert server.server.authentication_ok is True
            client.post(f"/api/boxes/{box.uuid}/vnc/close", json={"session": opened["session"]})
    finally:
        store.close()
        server.stop()


def test_a_password_used_to_look_at_a_screen_never_enters_the_library(tmp_path: Any) -> None:
    """The one rule authentication must not break: this database gets copied.

    So the check is not "we did not write a field called password". It is that the bytes of
    the secret are nowhere in the file on disk, and that the box used looks exactly as it did
    before anyone authenticated with it.
    """
    server = background(password="hunter2", frames=[[(0, 0, 4, 3, solid(4, 3, (9, 9, 9, 255)))]])
    database = tmp_path / "secret-check.db"
    store = BoxStore(database, content_root=tmp_path)
    try:
        box = store.add(
            Box(name="Workstation", box_type="vnc", resource_uri=f"vnc://127.0.0.1:{server.port}")
        )
        with TestClient(create_app(store, content_root=tmp_path)) as client:
            client.post(f"/api/boxes/{box.uuid}/vnc/open", json={}).json()
            opened = client.post(
                f"/api/boxes/{box.uuid}/vnc/open", json={"password": "hunter2"}
            ).json()
            assert store.get("Workstation").metadata == {}
            # handed back on the way out: a screen left open here is a socket whose loop is
            # about to be closed underneath it
            client.post(f"/api/boxes/{box.uuid}/vnc/close", json={"session": opened["session"]})
        store.close()
        assert b"hunter2" not in database.read_bytes()
    finally:
        with contextlib.suppress(Exception):
            store.close()
        server.stop()


def test_a_password_in_the_address_is_still_refused_by_the_type(tmp_path: Any) -> None:
    """Authentication arrived; the old trap did not go away with it.

    `vnc://secret@host` is the one shape where a secret would be written into the library by
    ordinary use of the box, so the type itself says no — at creation, not at connect time.
    """
    from boxganizer.boxtypes.dynamic import VNCResource

    box = Box(name="sneaky", box_type="vnc", resource_uri="vnc://hunter2@host:5900")
    with pytest.raises(InvalidBoxContent, match="password"):
        VNCResource.check(box)


def test_closing_a_screen_that_the_other_side_is_already_hanging_up_on(tmp_path: Any) -> None:
    """A refusal in the middle of a hangup must not become somebody else's failed request.

    `wait_closed()` waits on the transport's own close waiter, and a peer that is closing at
    the same instant can hand back a CancelledError about the socket. Closing a screen is
    cleanup code; cleanup code that raises takes down the request that only meant to be tidy.
    """

    async def scene() -> tuple[bool, bool]:
        server = FakeRfbServer(password="hunter2")
        await server.start()
        screen = vnc.Screen(vnc.Destination(host="127.0.0.1", port=server.port), password="hunter2")
        await screen.start()
        # the far end goes away first, so the local close races the peer's FIN
        for writer in list(server._writers):
            writer.transport.abort()
        first = await screen.close() is None
        second = await screen.close() is None  # twice, because two paths both clean up
        await server.stop()
        return bool(first), bool(second)

    closed_once, closed_twice = asyncio.run(scene())
    assert closed_once and closed_twice
