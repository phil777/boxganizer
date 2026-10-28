"""VNC: what a box does about a screen on another machine.

A box whose resource is `vnc://workstation:1` shows the screen that address serves, in
the page. Three facts shape how this module is built.

**A browser cannot open a socket.** RFB, the protocol behind VNC, is a TCP conversation.
So the conversation happens here, in Python, and what crosses to the page is a picture:
the framebuffer, encoded as PNG, pushed over the same server-sent events the MQTT box
already uses. Mouse and keystrokes go back the other way as small POSTs, and this module
turns them into RFB messages. Nothing in the page knows the protocol exists, which is the
point: the alternative is a second RFB implementation in JavaScript, out of reach of the
tests, and out of date within a year.

**The server speaks, and we listen.** An RFB server sends a FramebufferUpdate whenever
something on the screen changes and nothing when it does not, so the page redraws when
there is news rather than on a timer. One `FramebufferUpdateRequest(incremental=1)` follows
each update, which is the whole refresh policy. There is no polling loop and no frame rate
to tune: the machine's own activity sets the pace, and an idle desktop costs nothing.

**Authentication is refused, not stored.** The client offers exactly one security type —
`None` — and if the server will not accept it, the box says which types the server asked
for and stops. This is the same line ADR 12 drew for SSH: Boxganizer holds no secrets, and
a VNC password in a URL (`vnc://secret@host`) is rejected where it is read rather than
filtered later. The consequence is stated plainly in the panel: a screen this machine can
look at uninvited is a screen that is on a loopback interface, a VPN, or a machine the
person has decided is theirs to look at. Raw is the one encoding, because the standard
requires every server to support it; Tight and Hextile are welcome to exist, and are not
implemented here — the cost is bandwidth on a busy screen, on a link this program only ever
walks a few frames at a time.

What is deliberately *not* attempted: audio, clipboard, multi-monitor switching, resizing
the remote screen, and the encrypted `Rs`/`x509` variants of newer servers. If the person
needs a session that types like a real terminal — full-screen apps, cursors, cut and paste
— the copyable `vnc://` line on the panel is the way to their own viewer, and this page says
so rather than pretending.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import struct
from dataclasses import dataclass, field
from typing import Any

#: How long to wait for the other machine, in seconds. A VNC server that does not answer
#: in this time is not answering; the person gets to know now rather than after a minute.
DEFAULT_TIMEOUT = 10.0

#: A screen bigger than this is refused rather than allocated: the framebuffer is held in
#: memory here as well as there, and a 16K square would be a gigabyte for a box on a page.
MAX_DIMENSION = 4096

#: The reply this client sends to a server's greeting. 3.8 is what everything since 2007
#: speaks; 3.3 and 3.7 servers accept a 3.8 reply too, per the protocol's own rule that
#: the client answers with the higher version it supports.
VERSION_REPLY = b"RFB 003.008\n"

# Client-to-server message types.
_SET_PIXEL_FORMAT = 0
_SET_ENCODING = 1
_UPDATE_REQUEST = 2
_KEY_EVENT = 4
_POINTER_EVENT = 5

# Server-to-client message types.
_FRAMEBUFFER_UPDATE = 0
_SET_COLORMAP = 1
_BELL = 2
_SERVER_CUT_TEXT = 3

_SECURITY_NONE = 1

#: Names for the security types, so a refusal can say what was asked for.
SECURITY_NAMES = {
    1: "None",
    2: "VNC password",
    5: "RA2ne",
    6: "ARD",
    16: "UNIX diffie-hellman",
    17: "TLS",
    18: "RSA-AES256",
}

_ADDRESS = re.compile(
    r"""^
    (?:vnc://)?                       # the scheme, when it is there
    (?P<user>[^@/?]*@)?               # a user, only so it can be refused by name
    (?P<host>[A-Za-z0-9._\-]*[\-.A-Za-z0-9])
    (?::(?P<port>[0-9]{1,5}))?
    /?$
    """,
    re.VERBOSE,
)


def authority_of(address: str) -> str:
    """The part of an address before the path — where a user, a host and a port live.

    A password has to be refused where it is read, and the naive check (`"@" in raw`)
    looks in the whole address, including a path a machine is allowed to have. This is the
    part that could hold one.
    """
    without_scheme = address.split("://", 1)[1] if "://" in address else address
    return without_scheme.split("/", 1)[0].split("?", 1)[0]


class VncError(ValueError):
    """A VNC box cannot be used as asked — a bad address, or a server that says no."""


@dataclass(frozen=True)
class Destination:
    """Where a VNC box connects: a host and a port, and nothing else."""

    host: str
    port: int
    #: The display number the address was written with, when it was written that way.
    display: int | None = None

    @property
    def url(self) -> str:
        """The address in the form the person wrote it, and the form `vncviewer` takes."""
        if self.display is not None:
            return f"vnc://{self.host}:{self.display}"
        return f"vnc://{self.host}:{self.port}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "port": self.port,
            "display": self.display,
            "url": self.url,
        }


def destination(box: Any) -> Destination:
    """The address a box points at, as a host and a port.

    Two conventions meet here, and both are honoured. `vnc://host:5901` names a port;
    `vnc://host:1` names display 1, which is port 5901, because that is how the `-display`
    argument, the `Xvnc :1` idiom and macOS's screen-sharing URI all write it. The number
    decides which reading applies: 31 and below cannot be a port anyone means (it is
    privileged and unused) so it is a display; anything above is a port.

    A password in the address is refused, here, where the address is read — not quietly
    dropped on the way to the socket. See the module docstring.
    """
    raw = (getattr(box, "resource_uri", None) or "").strip()
    if not raw:
        raise VncError("the box points at nothing; a VNC box needs vnc://host[:port]")
    if "@" in authority_of(raw):
        raise VncError(
            "a VNC box keeps no password in its address — this client offers no "
            "authentication, so ask the machine for none, or use your own viewer"
        )
    match = _ADDRESS.match(raw)
    if match is None:
        raise VncError(f"'{raw}' is not a VNC address; try vnc://host:5900 or vnc://host:1")
    host = match.group("host")
    if not host:
        raise VncError("a VNC address needs a host")
    written = match.group("port")
    if written is None:
        return Destination(host=host, port=5900)
    number = int(written)
    if number > 65535:
        raise VncError(f"port {number} is not a port")
    if number <= 31:
        return Destination(host=host, port=5900 + number, display=number)
    return Destination(host=host, port=number)


@dataclass
class Probe:
    """What the other end said, up to and including the screen it serves.

    The size is here because it is the first useful thing a box can learn about a machine,
    and because the panel is better for saying "TigerVNC, 1920x1080, refuses
    authentication" than for saying "reachable".
    """

    version: str
    security_types: list[int] = field(default_factory=list)
    server_name: str = ""
    width: int = 0
    height: int = 0

    @property
    def accepts_none(self) -> bool:
        return _SECURITY_NONE in self.security_types

    def asked_for(self) -> str:
        if not self.security_types:
            return "nothing it would say"
        return ", ".join(SECURITY_NAMES.get(kind, f"type {kind}") for kind in self.security_types)

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "security_types": self.security_types,
            "security_names": [
                SECURITY_NAMES.get(kind, f"type {kind}") for kind in self.security_types
            ],
            "accepts_none": self.accepts_none,
            "server_name": self.server_name,
            "width": self.width,
            "height": self.height,
        }


async def _read(stream: asyncio.StreamReader, count: int, what: str) -> bytes:
    """Read exactly *count* bytes, or say what ran out."""
    try:
        data = await stream.readexactly(count)
    except asyncio.IncompleteReadError as exc:
        raise VncError(f"the server closed the connection while reading {what}") from exc
    return data


async def _greeting(
    stream: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> Probe:
    """Read the version, and on 3.8 and later the list of security types.

    Stops there. Nothing is chosen, nothing is authenticated: this is the box asking what
    is on the other end, which is a different thing from starting a session, and it is the
    question the panel answers when the person has not asked for a screen yet.
    """
    hello = await _read(stream, 12, "the protocol version")
    text = hello.decode("latin-1").strip()
    if not text.startswith("RFB ") and not text.startswith("000.00"):
        raise VncError(f"the machine on that port did not speak VNC (it said {text!r})")
    version = text[4:] if text.startswith("RFB ") else text
    writer.write(VERSION_REPLY)
    await writer.drain()
    probe = Probe(version=version)
    # 3.3 answers with a security type instead of a list; 3.7 and 3.8 send a list.
    if version >= "003.007":
        count = (await _read(stream, 1, "the number of security types"))[0]
        kinds = list(await _read(stream, max(count, 0), "the security types"))
        probe.security_types = kinds
        if count == 0:
            reason = (await _read(stream, 4, "a failure reason")).decode("latin-1", "replace")
            raise VncError(f"the server refuses every connection: {reason.strip()}")
        chosen = _SECURITY_NONE if _SECURITY_NONE in kinds else (kinds[0] if kinds else 0)
        writer.write(bytes([chosen]))
        await writer.drain()
        if chosen != _SECURITY_NONE:
            raise VncError(
                "the server will not do a session without authentication; it asked for "
                + probe.asked_for()
            )
        await _security_result(stream, probe)
    else:
        # 3.3 has no list and no result: the handshake is over and ServerInit is next.
        probe.security_types = [_SECURITY_NONE]
    await _server_init(stream, probe)
    return probe


async def _security_result(stream: asyncio.StreamReader, probe: Probe) -> None:
    """The 4-byte SecurityResult 3.8 sends after the security type is chosen."""
    result = struct.unpack(">I", await _read(stream, 4, "the security result"))[0]
    if result != 0:
        reason = ""
        with contextlib.suppress(VncError):
            length = struct.unpack(">I", await _read(stream, 4, "a failure length"))[0]
            raw_reason = await _read(stream, length, "the failure reason")
            reason = raw_reason.decode("latin-1", "replace")
        raise VncError(f"the server refused the session (reason: {reason.strip() or result})")


async def _server_init(stream: asyncio.StreamReader, probe: Probe) -> None:
    """Take the screen size and the name the server introduces itself with.

    width u16, height u16, pixel format 16 bytes, name length u32, name. Reading the name
    length as two bytes instead of four is the classic way a client ends up printing
    nonsense, so the layout is spelled out in the unpack rather than counted by hand.
    """
    header = await _read(stream, 24, "the screen size")
    size_and_name = header[:4] + header[20:24]
    probe.width, probe.height, name_length = struct.unpack(">HHI", size_and_name)
    probe.server_name = (await _read(stream, name_length, "the server name")).decode(
        "utf-8", "replace"
    )


async def probe(dest: Destination, *, timeout: float = DEFAULT_TIMEOUT) -> Probe:
    """Ask a machine what it is, without holding a session open."""
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(dest.host, dest.port), timeout
    )
    try:
        return await asyncio.wait_for(_greeting(reader, writer), timeout)
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()


class Screen:
    """A live VNC session: frames out, input in.

    Held open by the page's event stream, and closed when that stream ends. One screen per
    box view, not one per box: two tabs watching the same machine get two sessions, which is
    what the protocol is built for and what avoids a fight over whose mouse is whose.
    """

    def __init__(self, dest: Destination, *, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.dest = dest
        self.timeout = timeout
        self.width = 0
        self.height = 0
        self.name = ""
        self.server_version = ""
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._pixels = bytearray()
        self._closed = False

    @property
    def started(self) -> bool:
        return bool(self.width and self.height)

    async def start(self) -> Probe:
        """Connect, agree on nothing fancy, and ask for the screen."""
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self.dest.host, self.dest.port), self.timeout
        )
        self._reader, self._writer = reader, writer
        try:
            probe_result = await asyncio.wait_for(_greeting(reader, writer), self.timeout)
        except BaseException:
            await self.close()  # the same: nothing handed back, nothing left open
            raise
        self.name = probe_result.server_name
        self.server_version = probe_result.version
        self.width, self.height = probe_result.width, probe_result.height
        if not 0 < self.width <= MAX_DIMENSION or not 0 < self.height <= MAX_DIMENSION:
            await self.close()
            raise VncError(
                f"a {self.width}x{self.height} screen is more than this box will hold"
            )
        # 32 bits a pixel, 24 used, little-endian, red at bit 16. Chosen rather than
        # accepted, because it is the layout Pillow takes as `RGBX` with no shuffling: a
        # frame is then a copy, not a conversion.
        writer.write(
            struct.pack(">B3x4B3H3B3x", _SET_PIXEL_FORMAT, 32, 24, 0, 1, 255, 255, 255, 16, 8, 0)
        )
        # Raw, and nothing else. Every server must support Raw; the ones that also offer
        # Tight or Hextile would have to be decoded here too, and are not.
        # type u8, padding u8, number-of-encodings u16, then each encoding as an i32.
        writer.write(struct.pack(">BBh", _SET_ENCODING, 0, 1) + struct.pack(">i", 0))
        await writer.drain()
        try:
            await self._request(incremental=0)
        except BaseException:
            await self.close()  # a screen that did not finish starting is a socket leak
            raise
        return probe_result

    async def _request(self, *, incremental: int) -> None:
        assert self._writer is not None
        self._writer.write(
            struct.pack(">BBHHHH", _UPDATE_REQUEST, incremental, 0, 0, self.width, self.height)
        )
        await self._writer.drain()

    async def next_frame(self) -> bytes | None:
        """Wait for the screen to change, and return it as a PNG.

        Returns None when the session has ended. Nothing is sent when nothing moved: the
        server decides when there is news, which is why an idle desktop costs this page
        nothing at all.
        """
        reader = self._reader
        if reader is None or self._closed:
            return None
        if not self._pixels:
            self._pixels = bytearray(self.width * self.height * 4)
        while True:
            kind = (await _read(reader, 1, "a message type"))[0]
            if kind == _FRAMEBUFFER_UPDATE:
                # type u8, padding u8, number-of-rectangles u16
                count = struct.unpack(">BH", await _read(reader, 3, "the update"))[1]
                for _ in range(count):
                    # rectangle-header: x, y, width, height u16 each, encoding i32 = 12
                    x, y, width, height, encoding = struct.unpack(
                        ">HHHHi", await _read(reader, 12, "a rectangle")
                    )
                    if encoding != 0:
                        raise VncError(
                            f"the server sent encoding {encoding}, and this box reads only Raw"
                        )
                    blob = await _read(reader, width * height * 4, "the pixels")
                    self._paint(x, y, width, height, blob)
                await self._request(incremental=1)
                return await asyncio.to_thread(_png, self.width, self.height, self._pixels)
            if kind == _BELL:
                continue  # a beep on the other machine is not this page's business
            if kind == _SET_COLORMAP:
                _first, number = struct.unpack(">HH", await _read(reader, 4, "the colormap"))
                await _read(reader, int(number) * 6, "the colormap")
                continue
            if kind == _SERVER_CUT_TEXT:
                # padding u8 x3, length u32
                length = struct.unpack(">I", (await _read(reader, 7, "the clipboard"))[3:])[0]
                await _read(reader, length, "the clipboard")
                continue  # the remote clipboard stays where it is; see the module docstring
            raise VncError(f"unexpected message {kind} from the server")

    def _paint(self, x: int, y: int, width: int, height: int, pixels: bytes) -> None:
        """Copy a rectangle of the update into the framebuffer we hold."""
        if width <= 0 or height <= 0:
            return
        row_bytes = self.width * 4
        for line in range(height):
            top = (int(y) + line) * row_bytes + int(x) * 4
            take = min(width * 4, len(pixels) - line * width * 4)
            if take <= 0 or top + take > len(self._pixels):
                continue
            self._pixels[top : top + take] = pixels[line * width * 4 : line * width * 4 + take]

    async def pointer(self, x: int, y: int, button_mask: int) -> None:
        """Move the pointer, and press or release with it.

        RFB sends position and buttons together, always, which is why the page reports every
        move rather than only the clicks: a window dragged by its title bar needs the button
        held while the position changes.
        """
        # type u8, button-mask u8, x u16, y u16
        await self._send(
            struct.pack(">BBHH", _POINTER_EVENT, button_mask & 0xFF, int(x), int(y))
        )

    async def key(self, keysym: int, *, down: bool) -> None:
        await self._send(struct.pack(">BBxxI", _KEY_EVENT, 1 if down else 0, int(keysym)))

    async def _send(self, payload: bytes) -> None:
        if self._writer is None or self._closed:
            raise VncError("the session is closed")
        self._writer.write(payload)
        await self._writer.drain()

    async def close(self) -> None:
        self._closed = True
        if self._writer is not None:
            self._writer.close()
            with contextlib.suppress(Exception):
                await self._writer.wait_closed()
        self._writer = None
        self._reader = None


def _png(width: int, height: int, pixels: bytearray) -> bytes:
    """The framebuffer, as a PNG."""
    import io

    from PIL import Image

    image = Image.frombytes("RGBX", (width, height), bytes(pixels))
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="PNG", optimize=False, compress_level=4)
    return buffer.getvalue()


#: Keys that have no character of their own. The Latin-1 range needs no table: the keysym
#: *is* the code point there, which is why typing mostly works without saying anything.
KEYSYMS = {
    "Backspace": 0xFF08,
    "Tab": 0xFF09,
    "Clear": 0xFF0B,
    "Enter": 0xFF0D,
    "Escape": 0xFF1B,
    "PageUp": 0xFF55,
    "PageDown": 0xFF56,
    "End": 0xFF57,
    "Home": 0xFF50,
    "ArrowLeft": 0xFF51,
    "ArrowUp": 0xFF52,
    "ArrowRight": 0xFF53,
    "ArrowDown": 0xFF54,
    "Insert": 0xFF63,
    "Delete": 0xFFFF,
    "F1": 0xFFBE,
    "F2": 0xFFBF,
    "F3": 0xFFC0,
    "F4": 0xFFC1,
    "F5": 0xFFC2,
    "F6": 0xFFC3,
    "F7": 0xFFC4,
    "F8": 0xFFC5,
    "F9": 0xFFC6,
    "F10": 0xFFC7,
    "F11": 0xFFC8,
    "F12": 0xFFC9,
    "ControlLeft": 0xFFE3,
    "ControlRight": 0xFFE4,
    "ShiftLeft": 0xFFE1,
    "ShiftRight": 0xFFE2,
    "AltLeft": 0xFFE9,
    "AltRight": 0xFFEA,
    "MetaLeft": 0xFFEB,
    "MetaRight": 0xFFEC,
    "CapsLock": 0xFFE5,
    " ": 0x20,
}


def keysym(event_key: str) -> int | None:
    """The keysym for a keyboard event's `key`, or None when there is nothing to send.

    The browser's `key` is a character for text and a name for the rest, which is the same
    split this function makes: a character in the Latin-1 range maps to itself, a name is
    looked up, and anything else (IME dead keys, modifiers arriving alone) is dropped rather
    than guessed at.
    """
    if not event_key:
        return None
    if len(event_key) == 1:
        code = ord(event_key)
        if code < 32:
            return None
        if code <= 0xFF:
            return code  # Latin-1: the keysym *is* the code point, and always was
        return 0x01000000 + code  # the Unicode block, by the rule XKB settled on
    return KEYSYMS.get(event_key)
