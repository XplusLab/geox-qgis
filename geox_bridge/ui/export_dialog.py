"""Layer picker dialog for exporting QGIS layers to a GEOX bundle."""

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)
from qgis.core import QgsProject, QgsVectorLayer

from ..core.constants import MAX_FEATURES_PER_LAYER


class ExportDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Export to GEOX"))
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel(self.tr("Project name")))
        self.name_edit = QLineEdit(
            QgsProject.instance().title()
            or QgsProject.instance().baseName()
            or "QGIS export")
        name_row.addWidget(self.name_edit, 1)
        layout.addLayout(name_row)

        layout.addWidget(QLabel(self.tr("Layers to export (vector only):")))
        self.list = QListWidget()
        vector_layers = [l for l in QgsProject.instance().mapLayers().values()
                         if isinstance(l, QgsVectorLayer)]
        for layer in vector_layers:
            label = "{}  -  {} features, {}".format(
                layer.name(), layer.featureCount(), layer.crs().authid())
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, layer.id())
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            over = layer.featureCount() > MAX_FEATURES_PER_LAYER
            item.setCheckState(Qt.Unchecked if over else Qt.Checked)
            if over:
                item.setForeground(Qt.red)
                item.setToolTip(self.tr(
                    "Over the {} feature browser guardrail - will be skipped."
                ).format(MAX_FEATURES_PER_LAYER))
            self.list.addItem(item)
        layout.addWidget(self.list)

        note = QLabel(self.tr(
            "Everything is reprojected to WGS84. The current map view becomes "
            "the GEOX start view."))
        note.setWordWrap(True)
        layout.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def project_name(self):
        return self.name_edit.text().strip()

    def selected_layers(self):
        project = QgsProject.instance()
        out = []
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.checkState() == Qt.Checked:
                layer = project.mapLayer(item.data(Qt.UserRole))
                if layer is not None:
                    out.append(layer)
        return out
