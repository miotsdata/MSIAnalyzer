""""In use elsewhere" marker for an analysis on a shared drive (ADR 69).

The lab share is mounted with `nobrl`, so file locks never leave the
computer that takes them: SQLite and HDF5 can't see that another computer
has the same analysis open. A small JSON file next to the analysis database,
`<db>.inuse`, says who has it. The holder rewrites it every
`HEARTBEAT_S`; a marker not refreshed for `STALE_S` (a crash, a pulled
cable) counts as free.

Only another *computer* counts as "elsewhere": processes on the same
computer share its locks, which SQLite and the single-instance lock already
handle.
"""

from __future__ import annotations

import datetime
import getpass
import json
import logging
import os
import socket
import threading
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

HEARTBEAT_S = 60.0
# ponytail: compares another computer's clock with ours; generous so a few
# minutes of clock skew between lab PCs doesn't matter.
STALE_S = 300.0


class ReadOnlyError(OSError):
    """A write into an analysis this process opened read-only. An OSError,
    so the existing "could not write, log and go on" handlers cover it."""


#: Output folders this process opened read-only (held by another
#: computer). Checked by `h5ad_io.open_for_write`; the GUI also blocks its
#: own database-writing actions (`AnalysisBridge.readOnly`).
_read_only_dirs: set[Path] = set()


def set_read_only(db_path: str | Path, read_only: bool) -> None:
    folder = Path(db_path).resolve().parent
    (_read_only_dirs.add if read_only else _read_only_dirs.discard)(folder)


def check_writable(path: str | Path) -> None:
    """Raise `ReadOnlyError` if `path` lies in a read-only output folder."""
    path = Path(path).resolve()
    if any(path.is_relative_to(d) for d in _read_only_dirs):
        raise ReadOnlyError(f"{path}: analysis is open read-only (in use on another computer)")


class InUseError(RuntimeError):
    """The analysis is in use on another computer."""

    def __init__(self, info: dict):
        self.info = info
        super().__init__(f"Analysis in use on {describe(info)}")


def marker_path(db_path: str | Path) -> Path:
    db_path = Path(db_path)
    return db_path.with_name(db_path.name + ".inuse")


def _now() -> datetime.datetime:
    return datetime.datetime.now().astimezone()


def describe(info: dict) -> str:
    """'PC-07 (mrossi), since 2026-11-06 10:42'."""
    since = info.get("since", "")[:16].replace("T", " ")
    return f"{info.get('host', '?')} ({info.get('user', '?')}), since {since}"


def read_marker(db_path: str | Path) -> dict | None:
    try:
        return json.loads(marker_path(db_path).read_text())
    except (OSError, ValueError):
        return None


def held_elsewhere(db_path: str | Path) -> dict | None:
    """The marker's contents if another computer holds the analysis now,
    else None (no marker, unreadable, this computer, or stale)."""
    info = read_marker(db_path)
    if not info or info.get("host") == socket.gethostname():
        return None
    try:
        beat = datetime.datetime.fromisoformat(info["heartbeat"])
    except (KeyError, TypeError, ValueError):
        return None
    if (_now() - beat).total_seconds() > STALE_S:
        return None
    return info


class Claim:
    """This process' hold on one analysis: writes the marker, refreshes it
    from a daemon thread, removes it on `release` (only if still ours)."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.token = uuid.uuid4().hex
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._since = _now().isoformat()

    def acquire(self) -> "Claim":
        """Raises `InUseError` if another computer holds it."""
        info = held_elsewhere(self.db_path)
        if info is not None:
            raise InUseError(info)
        self._write()
        self._thread = threading.Thread(target=self._beat, daemon=True, name="in-use-marker")
        self._thread.start()
        return self

    def _write(self) -> None:
        info = {
            "host": socket.gethostname(),
            "user": getpass.getuser(),
            "pid": os.getpid(),
            "token": self.token,
            "since": self._since,
            "heartbeat": _now().isoformat(),
        }
        try:
            marker_path(self.db_path).write_text(json.dumps(info))
        except OSError:
            logger.warning("could not write in-use marker for %s", self.db_path, exc_info=True)

    def _is_ours(self) -> bool:
        info = read_marker(self.db_path)
        return bool(info) and info.get("token") == self.token

    def _beat(self) -> None:
        while not self._stop.wait(HEARTBEAT_S):
            # Taken over (e.g. by this computer's GUI opening it during a
            # run): stop refreshing, the new holder does.
            if not self._is_ours():
                return
            self._write()

    def release(self) -> None:
        self._stop.set()
        if self._is_ours():
            try:
                marker_path(self.db_path).unlink()
            except OSError:
                logger.warning("could not remove in-use marker for %s", self.db_path, exc_info=True)
