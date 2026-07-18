"""The bridge HTTP server, hosted inside QGIS on a QThread.

Binds strictly to 127.0.0.1. Off by default; started/stopped from the dock.
Session state (pairing) dies with the server - GEOX re-pairs next time.
"""

import socket
from http.server import ThreadingHTTPServer

from qgis.PyQt.QtCore import QThread, pyqtSignal

from .handlers import make_handler


class BridgeServer(QThread):
    started_ok = pyqtSignal(int)      # actual port
    failed = pyqtSignal(str)
    stopped = pyqtSignal()

    def __init__(self, state, port, parent=None):
        super().__init__(parent)
        self.state = state
        self.port = port
        self._httpd = None

    def run(self):
        handler = make_handler(self.state)
        try:
            self._httpd = ThreadingHTTPServer(("127.0.0.1", self.port), handler)
        except (OSError, socket.error) as exc:
            self.failed.emit("Could not bind 127.0.0.1:{} - {}".format(self.port, exc))
            return
        self._httpd.daemon_threads = True
        actual_port = self._httpd.server_address[1]
        self.started_ok.emit(actual_port)
        try:
            self._httpd.serve_forever(poll_interval=0.3)
        finally:
            try:
                self._httpd.server_close()
            except Exception:
                pass
            self._httpd = None
            self.stopped.emit()

    def stop(self):
        if self._httpd is not None:
            self._httpd.shutdown()
        self.wait(3000)
