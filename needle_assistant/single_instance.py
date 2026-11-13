from __future__ import annotations

import logging

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

log = logging.getLogger("needle.instance")

SERVER_NAME = "NeedleDesktop.SingleInstance.v1"


class SingleInstance(QObject):
    show_requested = pyqtSignal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.server: QLocalServer | None = None
        self._connections: list[QLocalSocket] = []

    def acquire(self) -> bool:
        socket = QLocalSocket()
        socket.connectToServer(SERVER_NAME)
        if socket.waitForConnected(350):
            try:
                socket.write(b"show\n")
                socket.flush()
                socket.waitForBytesWritten(350)
            except Exception:
                pass
            socket.disconnectFromServer()
            log.info("Another Needle Desktop instance is already running.")
            return False
        try:
            socket.abort()
        except Exception:
            pass

        QLocalServer.removeServer(SERVER_NAME)
        server = QLocalServer(self)
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not server.listen(SERVER_NAME):
            log.warning("Could not create the single-instance server: %s", server.errorString())
            return True
        server.newConnection.connect(self._new_connection)
        self.server = server
        return True

    def _new_connection(self) -> None:
        if self.server is None:
            return
        connection = self.server.nextPendingConnection()
        if connection is None:
            return
        self._connections.append(connection)
        connection.readyRead.connect(lambda: self._read(connection))
        connection.disconnected.connect(lambda: self._cleanup(connection))
        if connection.bytesAvailable():
            self._read(connection)

    def _read(self, connection: QLocalSocket) -> None:
        try:
            data = bytes(connection.readAll())
            if b"show" in data.lower():
                self.show_requested.emit()
        except Exception:
            pass

    def _cleanup(self, connection: QLocalSocket) -> None:
        try:
            connection.deleteLater()
        except Exception:
            pass
        if connection in self._connections:
            self._connections.remove(connection)

    def close(self) -> None:
        for connection in list(self._connections):
            try:
                connection.disconnectFromServer()
            except Exception:
                pass
        self._connections.clear()
        if self.server is not None:
            try:
                self.server.close()
            except Exception:
                pass
            self.server = None
