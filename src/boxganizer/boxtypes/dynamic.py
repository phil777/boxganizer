"""Dynamic resources: things that answer back.

Everything here changes over time — a web page, a shell, a sensor. None of them
talk to the outside world yet; the declared rules (which URI, which metadata)
are already enforced.
"""

from __future__ import annotations

from typing import ClassVar

from boxganizer.boxtypes.resource import ResourceBox
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
    required_metadata: ClassVar[frozenset[str]] = frozenset({"host"})
    resource_uri_schemes: ClassVar[frozenset[str] | None] = frozenset({"ssh"})
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


class SensorResource(DynamicResource):
    """Something that reports a value."""

    key: ClassVar[str] = "sensor"
    label: ClassVar[str] = "Sensor"
    icon: ClassVar[str] = "\U0001f4e1"  # 📡
    summary: ClassVar[str] = "A value that arrives from outside and updates itself."
    planned: ClassVar[str] = "Keep the last reading and its timestamp; alert on change."


class MQTTResource(SensorResource):
    """A value published on an MQTT topic."""

    key: ClassVar[str] = "mqtt"
    label: ClassVar[str] = "MQTT Sensor"
    icon: ClassVar[str] = "\U0001f4f6"  # 📶
    summary: ClassVar[str] = "Subscribes to one topic."
    live_source: ClassVar[str] = "mqtt"
    required_metadata: ClassVar[frozenset[str]] = frozenset({"topic"})
    resource_uri_schemes: ClassVar[frozenset[str] | None] = frozenset({"mqtt", "mqtts"})
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
