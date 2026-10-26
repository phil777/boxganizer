# 12. SSH boxes borrow the system client and never hold a password

Date: 2026-10-25
Status: accepted

## Context

An SSH box is a shell on another machine, filed where you work. Three decisions had to
be made together, because each one changes what the others can be: how a connection is
made, what happens to secrets, and how much of a terminal the page promises.

A Python SSH library (Paramiko, asyncssh) would give Boxganizer a connection it owns:
no external binary, keys read from wherever the code is pointed, and a session that
survives nothing. It would also mean Boxganizer becomes an SSH implementation with its
own key handling, its own host-key database, its own opinion about `~/.ssh/config`,
ProxyJump, certificates, agents, FIDO keys and whatever else a site's SSH is configured
to require — the parts that are security, and the parts people have already spent a
day configuring once, correctly, for the whole machine.

And a page that can run commands on another machine is the most dangerous surface in
the project. Whatever is decided here has to be defensible in writing.

## Decision

**Borrow the system `ssh`.** Boxganizer assembles an argv list — `ssh`, a few `-o`
settings, `-p`, `-i`, `-J`, the `user@host`, and the command as one argument — and runs
it with `subprocess`, never through a shell. Keys, agents, host keys, certificates and
`~/.ssh/config` belong to the client that is already installed and already trusted.

**Never store a password, and never ask for one.** A box whose address carries a
password (`ssh://user:secret@host`) is refused by the *type*, at creation and on every
edit, not merely ignored at run time. Commands are run with `BatchMode=yes`, so an
`ssh` that would stop and ask a question fails with `Permission denied (publickey)` on
stderr instead of hanging until the timeout — a question nobody can hear is not a
result. The page has no password field, and a test asserts that nothing in the SSH view
invites one.

**A console, not an emulator.** The page runs one command, shows one answer with its
exit code and duration, and keeps the exchange on screen in order. It does not pretend
to be a terminal: no cursor, no full-screen programs, and the panel says what it is, so
the first `vim` attempt is a disappointment about scope rather than a mystery. For a
session of their own, the page offers the line to type — without `BatchMode`, because a
person *can* answer a question and the pasted line should let them.

Every command runs under a timeout (30s by default, capped at 600s), and output is
clipped (200 KB by default) with the clipping announced. No port is opened, no daemon
started, no session left attached: a command exists while it is running.

## Consequences

- Boxganizer inherits the machine's SSH security posture instead of building a second,
  weaker one. A FIDO key, an agent, a bastion host or a `Match exec` rule works on day
  one, because it works for `ssh`.
- A box needs no secret to be useful, so the database stays a file that can be copied,
  backed up and synced. `config.ssh.history` holds commands that were run, which is a
  record of intent and not a credential — and it is the only thing the console stores.
- Refusing at the type means an unusable box never exists, but also that a user with a
  password-only host gets an error rather than a degraded path. That is the point: the
  error names the alternatives (key, agent, `~/.ssh/config`).
- `BatchMode=yes` makes the failure mode legible but rules out interactive
  authentication from the page entirely, which is the same limit as the one above,
  arrived at from the other direction.
- Because a command is one `ssh` invocation, "connect once, run ten things" is not
  offered. ControlMaster, if someone wants it, is a `~/.ssh/config` setting the client
  will honour — deliberately not Boxganizer's business.
- What is genuinely unfinished is written in the type's `planned` line: full-screen
  sessions, which need a terminal emulator in the page (xterm.js or equivalent) and a
  server-side PTY. That is a separate decision with its own risks, and it is not taken
  here by accident.
