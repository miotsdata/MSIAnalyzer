"""
freeze_watchdog.py
Detects and diagnoses GUI-thread freezes, the ones the desktop reports as
"<app> is not responding" (ADR 50).

A `QTimer` on the GUI thread ticks every `interval_ms`. On every tick it
re-arms `faulthandler.dump_traceback_later(threshold_s)`. While the GUI
thread keeps processing events, the dump is always pushed into the
future and never fires. When the GUI thread stops (a long synchronous
call, or waiting on the GIL), faulthandler's own watchdog fires after
`threshold_s` and writes every Python thread's stack to `dump_path`.
That watchdog is a C thread, so it works even when the GIL is held
elsewhere. The stacks show exactly what the GUI thread was stuck in,
and which other thread was busy at the time.

On the first tick after a stall, the stall's real duration is appended
under the dump and logged as a warning. Shorter stalls, still noticeable
(`warn_s`), are only logged.

The dump file is one per session, next to the GUI's debug log. It is
removed on `stop()` if nothing was ever written to it.
"""

from __future__ import annotations

import faulthandler
import logging
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QTimer

logger = logging.getLogger(__name__)


class FreezeWatchdog(QObject):
    def __init__(
        self,
        dump_dir: Path | str,
        *,
        threshold_s: float = 3.0,
        interval_ms: int = 500,
        warn_s: float = 1.0,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._dump_dir = Path(dump_dir)
        self._threshold_s = threshold_s
        self._warn_s = warn_s
        self._interval_s = interval_ms / 1000
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._tick)
        self._file = None
        self._last_tick = 0.0
        self._dumped_bytes = 0
        self.dump_path: Path | None = None

    def start(self) -> None:
        self._dump_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.dump_path = self._dump_dir / f"freeze_{stamp}.txt"
        # Unbuffered text isn't allowed, so it's line-buffered. faulthandler
        # writes straight to the file descriptor, so buffering only
        # affects our own annotation lines.
        self._file = open(self.dump_path, "a", buffering=1)
        self._last_tick = time.monotonic()
        self._arm()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        faulthandler.cancel_dump_traceback_later()
        if self._file is not None:
            self._file.close()
            self._file = None
        if self.dump_path is not None and self.dump_path.exists() \
                and self.dump_path.stat().st_size == 0:
            self.dump_path.unlink()

    def _arm(self) -> None:
        # Re-arming replaces any pending dump, so it only fires if the next
        # re-arm doesn't come in time.
        faulthandler.dump_traceback_later(
            self._threshold_s, repeat=False, file=self._file, exit=False
        )

    def _tick(self) -> None:
        now = time.monotonic()
        stall = now - self._last_tick - self._interval_s
        self._last_tick = now
        self._arm()

        size = self.dump_path.stat().st_size if self.dump_path.exists() else 0
        dumped = size > self._dumped_bytes
        if dumped:
            self._file.write(
                f"=== GUI thread stalled ~{stall:.1f} s "
                f"(ended {datetime.now():%Y-%m-%d %H:%M:%S}) ===\n\n"
            )
            self._file.flush()
            self._dumped_bytes = self.dump_path.stat().st_size
            logger.warning(
                "GUI thread stalled for ~%.1f s; all thread stacks at the "
                "moment of the freeze are in %s", stall, self.dump_path,
            )
        elif stall >= self._warn_s:
            logger.warning("GUI thread stalled for ~%.1f s", stall)
