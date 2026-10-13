"""One assistant per user session. A second launch just brings the first one to the front."""

from __future__ import annotations

import getpass
import hashlib

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from claude_assistant import APP_ID


def server_name() -> str:
    user = hashlib.sha1(getpass.getuser().encode("utf-8")).hexdigest()[:10]
    return f"{APP_ID}-{user}"


class SingleInstance(QObject):
    activated = Signal()

    def __init__(self, name: str | None = None) -> None:
        super().__init__()
        self.name = name or server_name()
        self._server: QLocalServer | None = None

    def notify_existing(self, timeout_ms: int = 500) -> bool:
        """True if another instance is running (and was asked to show itself)."""
        socket = QLocalSocket()
        socket.connectToServer(self.name)
        if not socket.waitForConnected(timeout_ms):
            return False
        socket.write(b"show")
        socket.flush()
        socket.waitForBytesWritten(timeout_ms)
        socket.disconnectFromServer()
        return True

    def listen(self) -> bool:
        server = QLocalServer(self)
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not server.listen(self.name):
            # A crashed instance can leave a stale socket file behind (Unix); remove and retry.
            QLocalServer.removeServer(self.name)
            if not server.listen(self.name):
                return False
        server.newConnection.connect(self._on_connection)
        self._server = server
        return True

    def _on_connection(self) -> None:
        assert self._server is not None
        while self._server.hasPendingConnections():
            conn = self._server.nextPendingConnection()
            conn.readyRead.connect(lambda c=conn: self._read(c))

    def _read(self, conn: QLocalSocket) -> None:
        if bytes(conn.readAll().data()).strip() == b"show":
            self.activated.emit()
        conn.disconnectFromServer()

    def close(self) -> None:
        if self._server is not None:
            self._server.close()
            self._server = None
