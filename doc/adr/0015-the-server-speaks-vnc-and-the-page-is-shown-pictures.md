# 15. The server speaks VNC, and the page is shown pictures

Date: 2026-11-05
Status: accepted, amended by ADR 17 (authentication)

## Context

A `vnc://` box is asked to show another machine's screen in the page. RFB — the protocol
behind VNC — is a stateful TCP conversation: greet with a version, choose a security type,
receive a framebuffer description, then exchange update messages and input events
indefinitely.

A browser cannot open a TCP socket. That single fact decides everything else, and there
were three honest ways to answer it.

**Ship a JavaScript RFB client and bridge the socket.** noVNC is that client, and it is good.
It would mean vendoring a large third-party bundle into this project's static assets and
running a WebSocket proxy between it and the far machine — because noVNC in a browser still
needs a WebSocket, and VNC servers do not speak WebSocket, so the proxy is required either
way (`websockify`, or `x11vnc -ws`). The proxy is our code, the protocol client is theirs,
and the seam between them is where the debugging happens. It also means the protocol this
project depends on is implemented in a place its 850 tests cannot reach.

**Speak RFB here, and send the page pictures.** This machine holds the TCP conversation,
decodes the framebuffer, and pushes PNG frames over the server-sent events channel the MQTT
box already uses. Pointer and keystrokes come back as small POSTs. The page contains no VNC
at all.

**Refuse, and hand the address to the person's own viewer.** Correct, tiny, and not what was
asked: the item says the box *proposes a web VNC interface*.

The second option costs bandwidth and CPU that the first does not — a PNG per changed screen
versus an efficient wire encoding — and buys something that turned out to matter more: there
is one protocol implementation, it is in Python, and it is tested against a server written
independently from the same spec (`tests/test_vnc.py`'s `FakeRfbServer`, which exists because
there is no VNC server on the machine these tests run on, and pretending otherwise would have
left this whole module unexecuted).

## Decision

**`boxganizer.vnc` speaks RFB. The page receives frames and sends input.**

- The handshake replies 3.8, and the framebuffer is held here as it arrives, rectangle by
  rectangle. A changed region is applied to the copy we keep, and the whole screen is
  re-encoded as PNG — because the alternative (tracking dirty rectangles into a canvas
  update protocol of our own) is a second renderer, and the page is already a good PNG
  renderer.
- Encoding happens on a worker thread (`asyncio.to_thread`). A 1280x800 PNG is tens of
  milliseconds, and the event loop must not spend them: this server also answers every
  other request the library makes.
- **Raw is the only encoding requested**, because the standard requires every server to
  support it. Tight, Hextile and CoRRE would each be a decoder here; the cost is bytes on a
  busy screen, on a connection this program walks a few frames at a time.
- **`None` was the only security type offered** *(amended by ADR 17: a server that insists on
  VNC password authentication is now authenticated against, with a password it is given for
  that connection alone — and the part of this bullet that survives is the part that mattered:
  this database is a plain file that gets copied, backed up and read by a page, so
  `vnc://secret@host` is still refused where it is read, and the type still refuses the box,
  so a secret never reaches storage).*
- Frames are capped at eight a second. RFB pushes an update whenever the screen changes and a
  machine playing video changes constantly; without a cap, this server encodes and the page
  decodes as fast as the pipe allows. Skipping is safe because the framebuffer held here *is*
  the current screen: an intermediate frame carries nothing the next one does not. (This was
  not theoretical: the first end-to-end run delivered 4,700 frames a second and the browser
  painted none of them.)
- A session is per event stream, keyed by an id the stream hands out, because a session is a
  pointer: two tabs on one machine are two cursors, and RFB is built for that. The page sends
  position and button mask together — RFB has no press and release, only a position with
  buttons held — and collapses consecutive *moves* while a message is in flight but never
  merges away a *button change*, because a dropped release leaves a window being dragged by a
  button nobody is holding.

## Consequences

- **The web interface is a live view with input, and it is not a replacement for a viewer.**
  No clipboard, no audio, no multi-monitor switching, no remote resize, no encrypted
  `Rs`/`x509` server variants. The copyable `vnc://` address is on the panel for exactly the
  sessions this page will not serve, and it says so.
- **A screen this box can open is a screen that permits it.** In practice: a loopback port,
  an SSH tunnel, a VPN, or a machine whose owner decided the local network may look. A box
  cannot be made to authenticate, so an authenticated machine is out — by decision, and the
  refusal names the types it asked for rather than failing with a timeout.
- **Bandwidth is paid in pictures.** A full 1920x1080 update is a few megabytes on the wire
  and a PNG of maybe a megabyte to the page. That is the price of Raw plus whole-frame
  encoding, and it is why the frame cap exists.
- **The disconnect path relies on ASGI cancellation, with a two-strike poll behind it.** When
  a browser goes away the server cancels the response task and the `finally` closes the
  socket. Cancelling the *read* instead would leave the protocol position unknown and the next
  byte would be read as a message type in the middle of a rectangle. The polling watcher needs
  two consecutive `http.disconnect` readings because an ASGI test transport reports disconnect
  as its ordinary idle answer — with one reading, every session closed itself the moment it
  opened, which the tests caught and a browser would have made mysterious.
- **A machine that answers is distinguishable from a machine that is not**, without
  authenticating or holding anything open: `GET /api/boxes/{ref}/vnc` probes the version, the
  security types and the screen size, and hangs up. That is what lets the panel say "TigerVNC,
  1920x1080, wants a password" instead of a spinner.
