# 24 — Single-instance app lock

**Status:** Accepted

## Context

Nothing prevented launching `msianalyzer-gui` more than once. A second
launch built an entirely separate `QGuiApplication`/`QQmlApplicationEngine`
— confusing (two windows, no indication they're the same app or which one
is "current") and potentially unsafe (two processes with no coordination
touching the same project's databases). The fix needed to work identically
today (source/`.venv` launches) and once a standalone build exists — see
[ADR 22](0022-distribution-and-ci-strategy.md) — including the still-future
Windows build, so a Linux-only mechanism (e.g. a PID file + `flock`) wasn't
a real option.

## Decision

`gui/utils/single_instance.py`: `QLocalServer`/`QLocalSocket`-based.
`acquire()` tries connecting to a well-known local server name; if that
succeeds, another instance is already running — an activation message is
sent to it and `acquire()` returns `None`, which `main()` treats as "exit
immediately, before building any UI." If the connection fails, this process
claims the name (after `QLocalServer.removeServer()`, clearing any stale
socket file a crashed prior instance could have left behind — Qt's own
documented pattern for this) and becomes the server; `main()` keeps the
returned `QLocalServer` alive for the process's lifetime and wires
`connect_activation()` to raise and focus the existing window whenever a
later launch pings it.

Chosen specifically because `QLocalServer`/`QLocalSocket` is a Qt
abstraction over the right OS primitive per platform (named pipes on
Windows, Unix domain sockets on Linux/macOS) — the same code carries over
to the Windows build unchanged, with no platform branching in this app's
own code.

## Alternatives considered

- **`QSharedMemory`** (the classic Qt single-instance recipe). Rejected —
  detects "another instance exists" but has no built-in messaging channel
  back to it, so raising/focusing the existing window would need a second
  mechanism (e.g. `QLocalServer` anyway) layered on top. `QLocalServer`
  alone already does both jobs.
- **A PID file + platform lock** (`flock` on Unix, a named mutex on
  Windows). Rejected — genuinely platform-specific code for something Qt
  already abstracts, and offers no path to "focus the existing window"
  without also building a separate IPC channel.
- **No-op / rely on user discipline.** Rejected outright — the confusing
  UX (unrelated windows, no indication of which is current) and the risk
  of two processes touching the same project's databases concurrently are
  real enough to fix directly rather than document as a known limitation.

## Consequences

- A second launch now exits with code 0 before any UI is built — verified
  with real subprocess launches (offscreen platform, so it didn't require
  a display): the second process exits immediately, the first stays
  running and receives the activation ping.
- The lock is per-machine, not per-project — launching the app twice
  always converges to one window, regardless of which project (if any) the
  second launch's argv would have pointed at. Argument forwarding to the
  running instance (e.g. "open this project") is not built — the
  activation message today carries no payload beyond "wake up."
- No CLI equivalent — `msianalyzer` (the CLI entry point) is unaffected;
  this only guards the GUI, where concurrent windows are the actual
  problem. Multiple concurrent `msianalyzer run` invocations were never in
  scope here.
