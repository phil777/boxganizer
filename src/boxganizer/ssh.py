"""SSH: the box that reaches another machine through the system's own client.

An SSH box holds an address — `ssh://user@host:2222`, or the same things in its
metadata — and runs commands on that machine with the `ssh` already on this one. Keys,
agents, host checking and `~/.ssh/config` are left to that client and to its own
configuration: Boxganizer assembles the command line, asks for it to be run
non-interactively, and reports what came back.

**A password is never stored, and never asked for.** A box that carries one in its
address is refused before anything is run, because the database is a plain file that
gets copied, backed up and shown to a page. Authentication is by key, by agent, or by
whatever `~/.ssh/config` already does — the same ways the command line works.

Nothing here opens a port or starts a daemon: a command runs when something asks for
it, from the page or from `box ssh`, and the process is gone when it answers.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlsplit

from boxganizer.errors import BoxganizerError

#: How long a command may take before it is cut off. A page must not hang on a machine
#: that is not answering, and the person waiting can always ask again.
DEFAULT_TIMEOUT = 30.0
MIN_TIMEOUT = 0.5
MAX_TIMEOUT = 600.0

#: Output is kept, but not without limit: a `yes` on the far side should not fill the
#: database or the browser with everything it ever printed.
MAX_OUTPUT_BYTES = 200_000

#: What the last part of an address has to look like to be a host at all. A host is a
#: name, an IPv4 address, or an IPv6 one in brackets — never a shell argument, and
#: never something with a slash in it, because a slash turns this into a path.
_HOST_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-_:[]")


class SshError(BoxganizerError):
    """An SSH box that cannot be asked for anything: no host, or a bad one."""


@dataclass(frozen=True)
class Destination:
    """Where to connect, and with what."""

    host: str
    user: str | None = None
    port: int = 22
    identity_file: str | None = None
    jump_host: str | None = None
    #: Extra `-o Key=Value` lines from the box's metadata.
    options: tuple[str, ...] = ()

    @property
    def target(self) -> str:
        """The `user@host` the client is given."""
        return f"{self.user}@{self.host}" if self.user else self.host

    def argv(self, command: str | None = None) -> list[str]:
        """The command line, as a list of arguments.

        Never a string through a shell: what is in a box's metadata should not be able
        to become an argument the local shell interprets. Everything is one argv entry
        each, and the remote command is passed whole to the far side, which is how
        `ssh host ls -la` already works.
        """
        argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
        for option in self.options:
            argv += ["-o", option]
        if self.port != 22:
            argv += ["-p", str(self.port)]
        if self.identity_file:
            argv += ["-i", self.identity_file]
        if self.jump_host:
            argv += ["-J", self.jump_host]
        argv.append(self.target)
        if command is not None:
            argv.append(command)
        return argv

    def command_line(self) -> str:
        """What to type in a real terminal, for the session this box cannot give.

        Deliberately not the argv used for a command: that carries `BatchMode=yes`,
        which is right when nothing is there to answer a question and wrong when a
        person is. A pasted line should ask for a passphrase if one is needed, so the
        batch options are left off and the address, port, key and jump are kept.

        It holds no secret either way: a password is refused by the type, and a key
        file is only a path.
        """
        argv = ["ssh"]
        if self.port != 22:
            argv += ["-p", str(self.port)]
        if self.identity_file:
            argv += ["-i", self.identity_file]
        if self.jump_host:
            argv += ["-J", self.jump_host]
        argv.append(self.target)
        return shell_join(argv)

    def as_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "user": self.user,
            "port": self.port,
            "identity_file": self.identity_file,
            "jump_host": self.jump_host,
            "options": list(self.options),
            "command_line": self.command_line(),
        }


def shell_join(argv: list[str]) -> str:
    """A command line for a human, with each argument quoted if it needs it."""
    parts: list[str] = []
    for argument in argv:
        if argument and all(char.isalnum() or char in "-_./:=@" for char in argument):
            parts.append(argument)
        else:
            parts.append("'" + argument.replace("'", "'\\''") + "'")
    return " ".join(parts)


def _clean_host(value: str) -> str:
    host = value.strip().strip("/")
    if not host:
        raise SshError("an SSH box needs a host")
    if any(char not in _HOST_CHARS for char in host):
        raise SshError(f"{host!r} is not a host name")
    if host.startswith("-"):
        # A host that begins with a dash would become an option to ssh rather than the
        # thing it connects to. There is no host named that, and there is no reason.
        raise SshError(f"{host!r} is not a host name")
    return host


def _clean_port(value: object, *, default: int = 22) -> int:
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    try:
        port = int(str(value).strip())
    except ValueError as exc:
        raise SshError(f"port {value!r} is not a number") from exc
    if not 1 <= port <= 65535:
        raise SshError(f"port {port} is outside 1 to 65535")
    return port


def destination(box: Any) -> Destination:
    """Where a box points, read from its address and its metadata.

    The address wins where both give something, because it is the one field the type
    shows and the one a person edits in the page. The metadata names it reaches for
    are `host`, `user`, `port`, `identity_file`, `jump_host` and `options`.
    """
    metadata: dict[str, Any] = dict(getattr(box, "metadata", {}) or {})
    resource = (getattr(box, "resource_uri", None) or "").strip()
    # A resource that already says `ssh://` is used as it stands; one that does not is
    # read as a bare address. Getting this the other way round turns the full form into
    # a nonsense host, and a person would blame the box.

    # The metadata a box may carry, read here once. There is no `password` among them.
    host = str(metadata.get("host", "")).strip()
    user = str(metadata.get("user", "")).strip() or None
    port_value: object = metadata.get("port")

    if resource:
        parts = urlsplit(resource if "://" in resource else f"ssh://{resource}")
        if parts.scheme and parts.scheme != "ssh":
            raise SshError(f"a {parts.scheme}:// address is not an SSH one")
        if unquote(parts.password or ""):
            # The rule of the whole module, enforced at the one place a password could
            # arrive: this box will not hold one, and saying so here is the only way to
            # be sure nothing downstream ever prints it.
            raise SshError(
                "an SSH box keeps no password — use a key, an agent, or ~/.ssh/config"
            )
        if parts.hostname:
            host = parts.hostname
        if parts.username:
            user = unquote(parts.username)
        if parts.port:
            port_value = parts.port

    if not host:
        raise SshError("an SSH box needs a host in its address, like ssh://user@build.local")

    options = metadata.get("options") or []
    if isinstance(options, str):
        options = [item for item in options.splitlines() if item.strip()]
    if not isinstance(options, list):
        raise SshError("metadata['options'] is a list of ssh -o settings")
    cleaned = [str(option).strip() for option in options if str(option).strip()]
    for option in cleaned:
        if option.startswith("-"):
            raise SshError(f"{option!r} is not an ssh option line; it belongs in -o Key=Value")

    identity = str(metadata.get("identity_file", "")).strip() or None
    jump = str(metadata.get("jump_host", "")).strip() or None
    return Destination(
        host=_clean_host(host),
        user=user or os.environ.get("USER") or None,
        port=_clean_port(port_value),
        identity_file=identity,
        jump_host=_clean_host(jump) if jump else None,
        options=tuple(cleaned),
    )


@dataclass(frozen=True)
class Result:
    """One command, answered."""

    command: str
    stdout: str
    stderr: str
    exit_code: int | None
    seconds: float
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def as_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "seconds": round(self.seconds, 3),
            "timed_out": self.timed_out,
            "ok": self.ok,
        }


def available() -> bool:
    """Whether this machine has an `ssh` to borrow."""
    return shutil.which("ssh") is not None


def _clip(text: str) -> str:
    raw = text.encode("utf-8", errors="replace")
    if len(raw) <= MAX_OUTPUT_BYTES:
        return text
    kept = raw[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
    return kept + f"\n… cut off at {MAX_OUTPUT_BYTES:,} bytes."


def run(
    box: Any,
    command: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> Result:
    """Run one command on the far side and bring the answer back.

    `BatchMode=yes` is in the argv for a reason: an `ssh` that decides to ask for a
    password or to confirm a host key would otherwise sit there until the timeout, and
    the answer to a question nobody heard is not a result. The failure arrives as
    `Permission denied (publickey)` or a host-key complaint on stderr, which is what
    the page shows.
    """
    wanted = str(command).strip()
    if not wanted:
        raise SshError("there is no command to run")
    target = destination(box)
    if not available():
        raise SshError("this machine has no ssh command; install OpenSSH")
    limit = max(MIN_TIMEOUT, min(float(timeout), MAX_TIMEOUT))

    started = time.monotonic()
    try:
        completed = subprocess.run(
            target.argv(wanted),
            capture_output=True,
            text=True,
            timeout=limit,
            check=False,
            env={**os.environ, "LC_ALL": "C.UTF-8"},
        )
    except subprocess.TimeoutExpired as exc:
        raw = (
            exc.stdout
            if isinstance(exc.stdout, str)
            else (exc.stdout or b"").decode(errors="replace")
        )
        return Result(
            command=wanted,
            stdout=_clip(raw),
            stderr=f"no answer in {limit:g}s",
            exit_code=None,
            seconds=time.monotonic() - started,
            timed_out=True,
        )
    except OSError as exc:
        raise SshError(f"ssh could not be started: {exc.strerror or exc}") from exc

    return Result(
        command=wanted,
        stdout=_clip(completed.stdout),
        stderr=_clip(completed.stderr),
        exit_code=completed.returncode,
        seconds=time.monotonic() - started,
    )
