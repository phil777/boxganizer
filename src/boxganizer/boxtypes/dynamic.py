"""Dynamic resources: things that answer back.

Everything here changes over time — a web page, a shell, a sensor. None of them
talk to the outside world yet; the declared rules (which URI, which metadata)
are already enforced.
"""

from __future__ import annotations

from typing import ClassVar

from boxganizer import sensors
from boxganizer.boxtypes.resource import ResourceBox
from boxganizer.errors import MqttError
from boxganizer.models import Box


class DynamicResource(ResourceBox):
    """Base for resources whose content comes from a live source."""

    key: ClassVar[str] = "dynamic"
    label: ClassVar[str] = "Dynamic Resource"
    icon: ClassVar[str] = "\u26a1"  # ⚡
    summary: ClassVar[str] = "Content that is fetched, not stored."
    abstract: ClassVar[bool] = True
    allows_children: ClassVar[bool] = True
    #: A terminal or a sensor may point at nothing at all; the subtypes say what they need.
    resource_uri_required: ClassVar[bool] = False


class WebResource(DynamicResource):
    """A page or endpoint on the web."""

    key: ClassVar[str] = "web"
    label: ClassVar[str] = "Web Page"
    icon: ClassVar[str] = "\U0001f310"  # 🌐
    summary: ClassVar[str] = "An http(s) address."
    resource_uri_required: ClassVar[bool] = True
    resource_uri_schemes: ClassVar[frozenset[str] | None] = frozenset({"http", "https"})
    resource_hint: ClassVar[str] = "https://example.com/reading.json"
    planned: ClassVar[str] = "Fetch title, favicon and a text extract."


class BrowserResource(WebResource):
    """An interactive browser pane."""

    key: ClassVar[str] = "browser"
    label: ClassVar[str] = "Browser"
    icon: ClassVar[str] = "\U0001f9ed"  # 🧭
    summary: ClassVar[str] = "The page, browsable inside Boxganizer."
    planned: ClassVar[str] = "Remember the address you navigated to between sessions."


class SnapshotResource(WebResource):
    """The page as it was at one moment."""

    key: ClassVar[str] = "snapshot"
    label: ClassVar[str] = "Snapshot"
    icon: ClassVar[str] = "\U0001f4f8"  # 📸
    summary: ClassVar[str] = "A capture of a web page, taken once and kept."
    planned: ClassVar[str] = (
        "Write captured_at and the capture location itself when the shot is taken; "
        "nothing is required at creation time."
    )


class TerminalResource(DynamicResource):
    """A shell session, kept as a box so it can sit where you work."""

    key: ClassVar[str] = "terminal"
    label: ClassVar[str] = "Terminal"
    icon: ClassVar[str] = "\u2328️"  # ⌨️
    summary: ClassVar[str] = "A command to run, and its output."
    planned: ClassVar[str] = "Run the command in metadata['command'] and keep the last output."


class SSHResource(TerminalResource):
    """A shell on another machine, reached through the system's own ssh.

    The box holds an address — `ssh://user@host:2222`, or the same in metadata — and
    runs commands there with the `ssh` already installed here, so keys, agents and
    `~/.ssh/config` behave the way they do at a terminal. **It cannot hold a password**:
    the address is read by a page and lives in a file that gets copied, and a secret
    belongs to neither. A box whose address carries one is refused.
    """

    key: ClassVar[str] = "ssh"
    label: ClassVar[str] = "SSH Terminal"
    icon: ClassVar[str] = "\U0001f510"  # 🔐
    summary: ClassVar[str] = "A shell on another machine."
    resource_uri_schemes: ClassVar[frozenset[str] | None] = frozenset({"ssh"})
    resource_hint: ClassVar[str] = "ssh://user@host:2222"
    # What is left is a real terminal in the page — a full-screen session with a
    # cursor. Commands run and answer already; an editor on the far side does not yet.
    planned: ClassVar[str] = "Full-screen interactive sessions in the page (a terminal emulator)."

    @classmethod
    def check(cls, box: Box) -> None:
        """No password, in the address or anywhere else.

        Enforced here rather than where the command is built, because a box that cannot
        be stored is better than one that sits in the library holding a secret until
        something prints its address. The database is a plain file that gets copied and
        backed up and shown to a page; a password belongs in a key, an agent, or
        `~/.ssh/config`, none of which Boxganizer touches.
        """
        from boxganizer import ssh

        try:
            ssh.destination(box)
        except ssh.SshError as exc:
            cls._reject(box, str(exc))


class VNCResource(DynamicResource):
    """Another machine's screen, drawn in the page.

    The address is `vnc://host:5900`, or `vnc://host:1` for display 1. The protocol is
    spoken here, in Python, and what crosses to the browser is a picture of the framebuffer
    with the news that it changed: a browser cannot open a socket, and a second RFB
    implementation in JavaScript would be a second protocol to keep correct. See
    :mod:`boxganizer.vnc`.

    **It cannot hold a password, and it can still use one.** A server that wants `None`
    gets a session with nothing to type. A server that wants VNC authentication is given the
    password the person types into the panel at that moment, or the one in the file named by
    ``metadata["password_file"]`` — and neither is ever written to the library, because this
    database is a plain file that gets copied, backed up and read by a page (ADR 12's line,
    drawn again for a different port, in ADR 17). What the box may store is the *path*, which
    is a fact about the filesystem and not a secret. An address like
    ``vnc://secret@host:5900`` is still refused: a password there is published the moment the
    library is copied.
    """

    key: ClassVar[str] = "vnc"
    label: ClassVar[str] = "VNC Screen"
    # 💻 rather than 🖥️, which the desktop type wears: this is somebody else's machine, and
    # a list where two boxes look identical is a list people mis-click.
    icon: ClassVar[str] = "\U0001f4bb"  # 💻
    #: Offered by the dialog, because a screen that wants a file for its password is
    #: otherwise a feature that exists only in the documentation.
    optional_metadata: ClassVar[frozenset[str]] = frozenset({"password_file"})
    summary: ClassVar[str] = "A screen on another machine, live in the page."
    resource_uri_schemes: ClassVar[frozenset[str] | None] = frozenset({"vnc"})
    resource_hint: ClassVar[str] = "vnc://workstation:5900, or vnc://workstation:1 for display 1"
    draws_remote_screen: ClassVar[bool] = True

    @classmethod
    def check(cls, box: Box) -> None:
        """An address that names a host and a port, and nothing that smells like a secret.

        Checked where the box is written, not where the socket is opened: a box that cannot
        be stored beats one that sits in the library holding something it will never use.
        """
        from boxganizer import vnc

        try:
            vnc.destination(box)
        except vnc.VncError as exc:
            cls._reject(box, str(exc))


class SensorResource(DynamicResource):
    """Something that reports a value."""

    key: ClassVar[str] = "sensor"
    label: ClassVar[str] = "Sensor"
    icon: ClassVar[str] = "\U0001f4e1"  # 📡
    summary: ClassVar[str] = "A value that arrives from outside and updates itself."
    planned: ClassVar[str] = "Alert on change; keep readings across a restart."

    #: The three templates `boxganizer.sensors` runs over a reading, in order. They live in the
    #: box's own config because what a reading *means* is the owner's business: a boiler sends
    #: JSON and a door sensor sends the word `open`, and no list of unit dropdowns this project
    #: guessed at would cover both.
    sensor_templates: ClassVar[tuple[str, ...]] = sensors.TEMPLATES

    @classmethod
    def check(cls, box: Box) -> None:
        """Templates are checked when the box is saved, not when it next reports.

        A Jinja typo found at render time is a box showing an apology where a number should be.
        Found at save time it is a sentence in the form that made it — and every way of writing a
        box (the page, `box edit`, a raw config write) arrives through this same hook.
        """
        super().check(box)
        problem = sensors.check_templates(box.config)
        if problem:
            cls._reject(box, problem)


class MQTTResource(SensorResource):
    """A value published on an MQTT topic."""

    key: ClassVar[str] = "mqtt"
    #: The live view needs a client library, which ships as an extra rather than a
    #: dependency: a library organizer should not drag a MQTT client into every install.
    dependency: ClassVar[str | None] = "aiomqtt"
    dependency_extra: ClassVar[str | None] = "live"

    @classmethod
    def is_available(cls) -> bool:
        # Asked of the module that would import it, so "is aiomqtt here" has one answer in
        # this codebase rather than two ways of asking it.
        from boxganizer import mqtt

        return mqtt.has_dependency()

    @classmethod
    def unavailable_reason(cls) -> str:
        """The sentence with the command in it, because there is a command that works.

        "the aiomqtt module is missing" is a diagnosis. What a person holding a terminal
        needs is the one line for the kind of install they have — pipx, uv, or a plain venv —
        and only the running process knows which that is.
        """
        if cls.is_available():
            return ""
        from boxganizer import mqtt

        return str(mqtt.missing_dependency())

    label: ClassVar[str] = "MQTT Sensor"
    icon: ClassVar[str] = "\U0001f4f6"  # 📶
    summary: ClassVar[str] = "Subscribes to one topic."
    live_source: ClassVar[str] = "mqtt"
    #: A sensor with nothing to read is not a sensor waiting to be filled in.
    resource_uri_required: ClassVar[bool] = True
    resource_uri_schemes: ClassVar[frozenset[str] | None] = frozenset({"mqtt", "mqtts"})
    resource_hint: ClassVar[str] = "mqtt://host:1883/boiler/pressure"

    @classmethod
    def check(cls, box: Box) -> None:
        """An address that names a broker *and* a topic.

        This type used to ask for the topic again as `metadata["topic"]`, which the page
        never sends: creating an MQTT box answered 422 and nobody could tell you why. The
        address says it — the path is the topic — and metadata stays what it always was,
        an override for a topic that will not fit in a path.
        """
        # The sensor's own rules first: a template that cannot run is refused whatever the
        # address says. Overriding `check` is not a licence to drop the parent's check, and this
        # type's did exactly that — the box was saved with a broken template and apologised on
        # screen instead, which is the failure this hook exists to prevent.
        super().check(box)

        from boxganizer import mqtt

        try:
            target = mqtt.parse_target(box.resource_uri, box.metadata.get("topic"))
        except MqttError as exc:
            cls._reject(box, str(exc))
            return
        if not target.topic:
            cls._reject(
                box,
                "the address names a broker but no topic; use mqtt://host:1883/boiler/pressure",
            )
        if not target.host:
            cls._reject(
                box, "the address names a topic but no broker; use mqtt://host:1883/boiler/pressure"
            )

    # The page subscribes while it shows the box and the server remembers the last
    # message. What is left is to listen when nobody is looking, and to keep the
    # value across a restart.
    planned: ClassVar[str] = (
        "Hold the subscription when no page is open; keep readings across a restart."
    )


class HTTPResource(SensorResource):
    """A value read from an HTTP endpoint."""

    key: ClassVar[str] = "http"
    label: ClassVar[str] = "HTTP Sensor"
    icon: ClassVar[str] = "\U0001f4e5"  # 📥
    summary: ClassVar[str] = "Polls an http(s) endpoint for a value."
    resource_uri_required: ClassVar[bool] = True
    resource_uri_schemes: ClassVar[frozenset[str] | None] = frozenset({"http", "https"})
    planned: ClassVar[str] = "Poll on a schedule in metadata['interval_s'] and extract the value."


class HomeAssistantResource(SensorResource):
    """An entity from a Home Assistant instance."""

    key: ClassVar[str] = "home-assistant"
    label: ClassVar[str] = "Home Assistant"
    icon: ClassVar[str] = "\U0001f3e0"  # 🏠
    summary: ClassVar[str] = "One Home Assistant entity."
    required_metadata: ClassVar[frozenset[str]] = frozenset({"entity_id"})
    resource_uri_schemes: ClassVar[frozenset[str] | None] = frozenset({"http", "https"})
    planned: ClassVar[str] = (
        "Read the entity state; the token stays in the environment, never in the box."
    )
