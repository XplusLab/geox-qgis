"""GEOX Bridge - plugin shell: menu/toolbar actions and the dock."""

import json
import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QFileDialog, QMessageBox
from qgis.core import Qgis, QgsProject

from .core import geoxjson
from .ui.dock import GeoxDockWidget
from .ui.export_dialog import ExportDialog

ICON_DIR = os.path.join(os.path.dirname(__file__), "ui", "icons")


def _icon(name):
    return QIcon(os.path.join(ICON_DIR, name))


class GeoxBridgePlugin:
    def __init__(self, iface):
        self.iface = iface
        self.dock = None
        self.actions = []
        self.menu = "GEOX Bridge"

    # ------------------------------------------------------------ QGIS API
    def initGui(self):
        self.dock = GeoxDockWidget(self.iface)
        self.iface.addDockWidget(Qt.RightDockWidgetArea, self.dock)
        self.dock.hide()

        act_panel = QAction(_icon("geox.svg"), self.tr("Show GEOX panel"),
                            self.iface.mainWindow())
        act_panel.setCheckable(True)
        act_panel.toggled.connect(
            lambda on: self.dock.setVisible(on))
        self.dock.visibilityChanged.connect(act_panel.setChecked)

        act_import = QAction(_icon("import.svg"), self.tr("Import .geoxjson..."),
                             self.iface.mainWindow())
        act_import.triggered.connect(self.import_geoxjson)

        act_export = QAction(_icon("export.svg"), self.tr("Export to GEOX..."),
                             self.iface.mainWindow())
        act_export.triggered.connect(self.export_geoxjson)

        for act in (act_panel, act_import, act_export):
            self.iface.addPluginToMenu(self.menu, act)
            self.actions.append(act)
        self.iface.addToolBarIcon(act_panel)

    def unload(self):
        if self.dock is not None:
            self.dock.shutdown()
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None
        for act in self.actions:
            self.iface.removePluginMenu(self.menu, act)
            self.iface.removeToolBarIcon(act)
        self.actions = []

    def tr(self, text):
        from qgis.PyQt.QtCore import QCoreApplication
        return QCoreApplication.translate("GeoxBridgePlugin", text)

    # ------------------------------------------------------------- actions
    def import_geoxjson(self):
        path, _ = QFileDialog.getOpenFileName(
            self.iface.mainWindow(), self.tr("Import GEOX project"),
            "", "GEOX project (*.geoxjson *.y1json *.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                bundle = json.load(fh)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self.iface.mainWindow(), self.tr("GEOX Bridge"),
                                 self.tr("Could not read the file: {}").format(exc))
            return
        try:
            report = geoxjson.bundle_to_qgis(
                bundle, apply_view=True, canvas=self.iface.mapCanvas(),
                group_name=None)
        except geoxjson.GeoxFormatError as exc:
            QMessageBox.critical(self.iface.mainWindow(), self.tr("GEOX Bridge"), str(exc))
            return
        level = Qgis.Warning if report.warnings else Qgis.Success
        self.iface.messageBar().pushMessage(
            "GEOX Bridge", report.summary(), level=level, duration=6)
        if self.dock is not None:
            self.dock.log_lines(
                ["import {}: {}".format(os.path.basename(path), report.summary())]
                + ["  warning: " + w for w in report.warnings])

    def export_geoxjson(self):
        dlg = ExportDialog(self.iface.mainWindow())
        if not dlg.exec_():
            return
        layers = dlg.selected_layers()
        if not layers:
            QMessageBox.information(self.iface.mainWindow(), self.tr("GEOX Bridge"),
                                    self.tr("No layers selected."))
            return
        warnings = []
        try:
            bundle = geoxjson.qgis_to_bundle(
                layers, project_name=dlg.project_name(),
                canvas=self.iface.mapCanvas(), warnings=warnings)
        except geoxjson.ExportError as exc:
            QMessageBox.critical(self.iface.mainWindow(), self.tr("GEOX Bridge"), str(exc))
            return
        default_name = (dlg.project_name() or "qgis-export").replace(" ", "-") + ".geoxjson"
        path, _ = QFileDialog.getSaveFileName(
            self.iface.mainWindow(), self.tr("Save GEOX project"),
            default_name, "GEOX project (*.geoxjson)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(bundle, fh, ensure_ascii=False)
        except OSError as exc:
            QMessageBox.critical(self.iface.mainWindow(), self.tr("GEOX Bridge"),
                                 self.tr("Could not write the file: {}").format(exc))
            return
        msg = self.tr("Exported {} layer(s) to {}").format(
            len(bundle["project"]["layers"]), os.path.basename(path))
        level = Qgis.Warning if warnings else Qgis.Success
        self.iface.messageBar().pushMessage("GEOX Bridge", msg, level=level, duration=6)
        if self.dock is not None:
            self.dock.log_lines([msg] + ["  warning: " + w for w in warnings])
