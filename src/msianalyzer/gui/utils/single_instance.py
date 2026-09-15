# src/msianalyzer/gui/utils/single_instance.py
from typing import Callable

from PySide6.QtNetwork import QLocalServer, QLocalSocket

SERVER_NAME = "msianalyzer-gui-single-instance"
_ACTIVATE_MESSAGE = b"activate"


def acquire(server_name: str = SERVER_NAME) -> QLocalServer | None:
    """Claims this process as the one-and-only GUI instance, or hands off
    to whichever one already holds it.

    Returns a listening `QLocalServer` if no other instance is running —
    the caller must keep it alive (e.g. a local variable held for the
    whole `app.exec()` call) for as long as the app runs, since nothing
    else references it. Returns `None` if another instance is already
    running; an activation message has already been sent to it in that
    case (see `connect_activation`), so the caller should exit immediately
    without building any UI.
    """
    socket = QLocalSocket()
    socket.connectToServer(server_name)
    if socket.waitForConnected(200):
        socket.write(_ACTIVATE_MESSAGE)
        socket.waitForBytesWritten(200)
        socket.disconnectFromServer()
        return None

    # No live instance responded — but a previous instance that crashed
    # (rather than exiting cleanly) can leave a stale socket file behind
    # on some platforms, which would otherwise make listen() below fail
    # as if the name were still taken. removeServer() first is Qt's own
    # documented way to clear that before claiming the name.
    QLocalServer.removeServer(server_name)
    server = QLocalServer()
    server.listen(server_name)
    return server


def connect_activation(server: QLocalServer, on_activate: Callable[[], None]) -> None:
    """Calls `on_activate()` whenever a later launch pings `server`
    instead of starting its own instance — the hook for raising and
    focusing the already-running window."""

    def _handle_new_connection() -> None:
        conn = server.nextPendingConnection()
        if conn is None:
            return

        def _on_ready_read() -> None:
            conn.readAll()
            on_activate()

        conn.readyRead.connect(_on_ready_read)
        conn.disconnected.connect(conn.deleteLater)

    server.newConnection.connect(_handle_new_connection)
