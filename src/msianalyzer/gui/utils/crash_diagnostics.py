"""crash_diagnostics.py

Evidence for a hard crash: an access violation inside Qt kills the process
with no Python traceback, and a windowed Windows build has no console for
Qt's own warnings either. So:

- `faulthandler.enable` writes every thread's Python stack to
  `crash_<stamp>.txt` in the log folder when the process crashes. A
  session that ends normally removes its (empty) file in `stop()`.
- Qt's messages (warnings, `QSG_INFO=1` output, ...) go to the `qt` logger,
  so they land in the debug log next to ours.
"""

from __future__ import annotations

import faulthandler
import logging
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QtMsgType, qInstallMessageHandler

_QT_LEVELS = {
    QtMsgType.QtDebugMsg: logging.DEBUG,
    QtMsgType.QtInfoMsg: logging.INFO,
    QtMsgType.QtWarningMsg: logging.WARNING,
    QtMsgType.QtCriticalMsg: logging.ERROR,
    QtMsgType.QtFatalMsg: logging.CRITICAL,
}

_qt_logger = logging.getLogger("qt")


def _qt_message(mode, context, message) -> None:
    where = f" ({context.file}:{context.line})" if context.file else ""
    _qt_logger.log(_QT_LEVELS.get(mode, logging.WARNING), "%s%s", message, where)


class CrashDiagnostics:
    """Started once in `main()` before any QML loads, stopped right before
    the process exits."""

    def __init__(self, log_dir: Path) -> None:
        self._log_dir = Path(log_dir)
        self._file = None
        self.crash_path: Path | None = None

    def start(self) -> None:
        self._log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.crash_path = self._log_dir / f"crash_{stamp}.txt"
        # faulthandler writes to the file descriptor directly; the file
        # must stay open for the whole session.
        self._file = open(self.crash_path, "w")
        faulthandler.enable(file=self._file, all_threads=True)
        qInstallMessageHandler(_qt_message)

    def stop(self) -> None:
        qInstallMessageHandler(None)
        faulthandler.disable()
        if self._file is not None:
            self._file.close()
            self._file = None
        if self.crash_path is not None and self.crash_path.exists() \
                and self.crash_path.stat().st_size == 0:
            self.crash_path.unlink()
