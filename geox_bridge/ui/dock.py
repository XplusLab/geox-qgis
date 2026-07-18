"""GEOX Bridge dock: File exchange, Live Sync (Pro), and a log."""

import datetime
import json

from qgis.PyQt.QtCore import QObject, QUrl, pyqtSignal
from qgis.PyQt.QtGui import QFont
from qgis.PyQt.QtNetwork import QNetworkRequest
from qgis.PyQt.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QCheckBox,
    QVBoxLayout,
    QWidget,
)
from qgis.core import Qgis, QgsBlockingNetworkRequest, QgsSettings
from qgis.gui import QgsDockWidget

from ..bridge.dispatch import MainThreadDispatcher
from ..bridge.handlers import BridgeState
from ..bridge.server import BridgeServer
from ..core.constants import DEFAULT_BRIDGE_PORT, LICENSE_VERIFY_URL

SETTINGS_PORT = "geox_bridge/port"
SETTINGS_KEY = "geox_bridge/license_key"
SETTINGS_KEY_OK_AT = "geox_bridge/license_ok_at"
LICENSE_TTL_DAYS = 7


class _EventBus(QObject):
    """Thread-safe funnel: handler threads emit, dock slots receive."""
    event = pyqtSignal(str)
    token = pyqtSignal(str)


class GeoxDockWidget(QgsDockWidget):
    def __init__(self, iface, parent=None):
        super().__init__("GEOX Bridge", parent or iface.mainWindow())
        self.setObjectName("GeoxBridgeDock")
        self.iface = iface
        self.server = None
        self.state = None
        self.bus = _EventBus()
        self.bus.event.connect(self._on_event)
        self.bus.token.connect(self._on_token)
        self._build_ui()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        body = QWidget()
        root = QVBoxLayout(body)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        # File section ---------------------------------------------------
        file_box = QGroupBox(self.tr("File exchange (free)"))
        fl = QVBoxLayout(file_box)
        hint = QLabel(self.tr(
            "Move projects by file: download .geoxjson from GEOX and import it "
            "here, or export QGIS layers to a .geoxjson you load in GEOX."))
        hint.setWordWrap(True)
        fl.addWidget(hint)
        row = QHBoxLayout()
        btn_imp = QPushButton(self.tr("Import .geoxjson..."))
        btn_exp = QPushButton(self.tr("Export to GEOX..."))
        btn_imp.clicked.connect(self._proxy_import)
        btn_exp.clicked.connect(self._proxy_export)
        row.addWidget(btn_imp)
        row.addWidget(btn_exp)
        fl.addLayout(row)
        root.addWidget(file_box)

        # Live sync section ----------------------------------------------
        sync_box = QGroupBox(self.tr("Live sync with GEOX (Pro)"))
        sl = QVBoxLayout(sync_box)

        lic_row = QHBoxLayout()
        self.license_edit = QLineEdit()
        self.license_edit.setPlaceholderText(self.tr("GEOX Pro license key"))
        self.license_edit.setEchoMode(QLineEdit.Password)
        self.license_edit.setText(QgsSettings().value(SETTINGS_KEY, "", str))
        lic_row.addWidget(self.license_edit, 1)
        sl.addLayout(lic_row)

        port_row = QHBoxLayout()
        port_row.addWidget(QLabel(self.tr("Port")))
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1024, 65535)
        self.port_spin.setValue(int(QgsSettings().value(SETTINGS_PORT, DEFAULT_BRIDGE_PORT)))
        port_row.addWidget(self.port_spin)
        self.dev_check = QCheckBox(self.tr("Allow localhost GEOX (dev)"))
        port_row.addWidget(self.dev_check)
        port_row.addStretch(1)
        sl.addLayout(port_row)

        btn_row = QHBoxLayout()
        self.start_btn = QPushButton(self.tr("Start bridge"))
        self.start_btn.clicked.connect(self._toggle_bridge)
        btn_row.addWidget(self.start_btn)
        self.token_label = QLabel("")
        self.token_label.setTextInteractionFlags(self.token_label.textInteractionFlags()
                                                 | 0x1)  # selectable
        font = QFont("Consolas")
        font.setPointSize(12)
        font.setBold(True)
        self.token_label.setFont(font)
        btn_row.addWidget(self.token_label)
        self.regen_btn = QPushButton(self.tr("Regenerate"))
        self.regen_btn.setEnabled(False)
        self.regen_btn.clicked.connect(self._regenerate_token)
        btn_row.addWidget(self.regen_btn)
        btn_row.addStretch(1)
        sl.addLayout(btn_row)

        self.status_label = QLabel(self.tr("Bridge is off."))
        self.status_label.setWordWrap(True)
        sl.addWidget(self.status_label)
        root.addWidget(sync_box)

        # Log --------------------------------------------------------------
        log_box = QGroupBox(self.tr("Log"))
        ll = QVBoxLayout(log_box)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(500)
        ll.addWidget(self.log)
        root.addWidget(log_box, 1)

        self.setWidget(body)

    # -------------------------------------------------- file action proxies
    def _proxy_import(self):
        from ..plugin import GeoxBridgePlugin  # noqa: avoid circulars at import time
        # The plugin instance owns the actions; find it via the action set is
        # overkill - just re-implement the call through iface's plugin registry.
        import qgis.utils
        plugin = qgis.utils.plugins.get("geox_bridge")
        if plugin:
            plugin.import_geoxjson()

    def _proxy_export(self):
        import qgis.utils
        plugin = qgis.utils.plugins.get("geox_bridge")
        if plugin:
            plugin.export_geoxjson()

    # --------------------------------------------------------- bridge logic
    def _toggle_bridge(self):
        if self.server is not None:
            self._stop_bridge()
            return
        key = self.license_edit.text().strip()
        if not self._license_ok(key):
            return
        QgsSettings().setValue(SETTINGS_KEY, key)
        QgsSettings().setValue(SETTINGS_PORT, self.port_spin.value())

        dispatcher = MainThreadDispatcher(self.iface, parent=self)
        self.state = BridgeState(dispatcher,
                                 qgis_version=Qgis.QGIS_VERSION,
                                 dev_mode=self.dev_check.isChecked())
        self.state.on_event = self.bus.event.emit
        self.state.on_token_regenerated = self.bus.token.emit

        self.server = BridgeServer(self.state, self.port_spin.value(), parent=self)
        self.server.started_ok.connect(self._on_started)
        self.server.failed.connect(self._on_failed)
        self.server.stopped.connect(self._on_stopped)
        self.server.start()
        self.start_btn.setEnabled(False)

    def _stop_bridge(self):
        if self.server is not None:
            self.server.stop()
            self.server = None
        self.state = None
        self.start_btn.setText(self.tr("Start bridge"))
        self.start_btn.setEnabled(True)
        self.regen_btn.setEnabled(False)
        self.token_label.setText("")
        self.status_label.setText(self.tr("Bridge is off."))
        self._log_line("bridge stopped")

    def shutdown(self):
        if self.server is not None:
            self._stop_bridge()

    def _on_started(self, port):
        self.start_btn.setText(self.tr("Stop bridge"))
        self.start_btn.setEnabled(True)
        self.regen_btn.setEnabled(True)
        self.token_label.setText(self.state.token)
        self.status_label.setText(self.tr(
            "Listening on 127.0.0.1:{} - not paired. In GEOX open "
            "Export > QGIS, then enter the 6-digit code shown here.").format(port))
        self._log_line("bridge listening on 127.0.0.1:{}".format(port))

    def _on_failed(self, message):
        self.server = None
        self.state = None
        self.start_btn.setEnabled(True)
        self.status_label.setText(message)
        self._log_line("bridge failed: " + message)

    def _on_stopped(self):
        pass

    def _regenerate_token(self):
        if self.state is not None:
            self.state.regenerate_token()

    def _on_token(self, token):
        self.token_label.setText(token)

    def _on_event(self, message):
        self._log_line(message)
        if self.state is not None and self.state.session and "paired" in message:
            self.status_label.setText(self.tr("Paired with GEOX."))

    # -------------------------------------------------------------- license
    def _license_ok(self, key):
        if not key:
            self.status_label.setText(self.tr(
                "Live sync needs a GEOX Pro license key. File exchange stays free."))
            return False
        settings = QgsSettings()
        cached_at = settings.value(SETTINGS_KEY_OK_AT + ":" + key[:8], "", str)
        if cached_at:
            try:
                age = datetime.datetime.now() - datetime.datetime.fromisoformat(cached_at)
                if age.days < LICENSE_TTL_DAYS:
                    return True
            except ValueError:
                pass
        self.status_label.setText(self.tr("Verifying license..."))
        req = QgsBlockingNetworkRequest()
        request = QNetworkRequest(QUrl(LICENSE_VERIFY_URL))
        request.setHeader(QNetworkRequest.ContentTypeHeader, "application/json")
        err = req.post(request, json.dumps({"license_key": key}).encode("utf-8"))
        ok = False
        if err == QgsBlockingNetworkRequest.NoError:
            try:
                payload = json.loads(bytes(req.reply().content()).decode("utf-8"))
                ok = bool(payload.get("success"))
            except (ValueError, AttributeError):
                ok = False
        if ok:
            settings.setValue(SETTINGS_KEY_OK_AT + ":" + key[:8],
                              datetime.datetime.now().isoformat())
            self.status_label.setText(self.tr("License verified."))
            return True
        self.status_label.setText(self.tr(
            "License could not be verified. Check the key (it is emailed by "
            "Gumroad when you subscribe to GEOX Pro) and your connection."))
        return False

    # ------------------------------------------------------------------ log
    def _log_line(self, message):
        stamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.log.appendPlainText("[{}] {}".format(stamp, message))

    def log_lines(self, lines):
        for line in lines:
            self._log_line(line)
