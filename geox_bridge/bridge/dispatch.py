"""Main-thread marshalling for the bridge.

The QGIS API must only be touched from the main (GUI) thread, but HTTP
handlers run in worker threads. MainThreadDispatcher lives in the main
thread; handler threads call push()/pull()/layers(), which emit a queued
signal and block on a reply queue until the main-thread slot has done the
QGIS work (30 s timeout).
"""

import queue

from qgis.PyQt.QtCore import QObject, pyqtSignal
from qgis.core import QgsProject, QgsVectorLayer, QgsWkbTypes

from ..core import geoxjson
from ..core.constants import MAX_FEATURES_PER_LAYER

REPLY_TIMEOUT_S = 30


class MainThreadDispatcher(QObject):
    _request = pyqtSignal(str, object, object)  # op, payload, reply queue

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        # Queued connection: slot always runs on the thread owning this
        # QObject (the main thread), regardless of the emitting thread.
        self._request.connect(self._handle)

    # ------------------------------------------------- worker-thread API
    def push(self, bundle):
        return self._call("push", bundle)

    def pull(self, layer_ids):
        return self._call("pull", layer_ids)

    def layers(self):
        return self._call("layers", None)

    def _call(self, op, payload):
        reply = queue.Queue(maxsize=1)
        self._request.emit(op, payload, reply)
        try:
            ok, result = reply.get(timeout=REPLY_TIMEOUT_S)
        except queue.Empty:
            raise RuntimeError("QGIS did not respond within {} s".format(REPLY_TIMEOUT_S))
        if not ok:
            raise RuntimeError(result)
        return result

    # --------------------------------------------------- main-thread slot
    def _handle(self, op, payload, reply):
        try:
            if op == "push":
                result = self._do_push(payload)
            elif op == "pull":
                result = self._do_pull(payload)
            elif op == "layers":
                result = self._do_layers()
            else:
                raise ValueError("unknown op " + op)
            reply.put((True, result))
        except Exception as exc:  # marshal the failure back, never swallow
            reply.put((False, str(exc)))

    def _do_push(self, bundle):
        import datetime
        stamp = datetime.datetime.now().strftime("%H:%M")
        report = geoxjson.bundle_to_qgis(
            bundle,
            apply_view=False,
            canvas=self.iface.mapCanvas(),
            group_name="GEOX push {}".format(stamp),
        )
        return {
            "ok": True,
            "layersCreated": [name for _id, name in report.layers_created],
            "featuresSkipped": report.features_skipped,
            "warnings": report.warnings,
        }

    def _do_pull(self, layer_ids):
        project = QgsProject.instance()
        if layer_ids:
            layers = [project.mapLayer(lid) for lid in layer_ids]
            layers = [l for l in layers if l is not None]
        else:
            layers = [l for l in project.mapLayers().values()
                      if isinstance(l, QgsVectorLayer)]
        warnings = []
        oversized = [l.name() for l in layers
                     if isinstance(l, QgsVectorLayer)
                     and l.featureCount() > MAX_FEATURES_PER_LAYER]
        if oversized and len(oversized) == len(layers):
            raise RuntimeError(
                "guardrail: layer(s) {} exceed {} features".format(
                    ", ".join(oversized), MAX_FEATURES_PER_LAYER))
        bundle = geoxjson.qgis_to_bundle(
            layers, canvas=self.iface.mapCanvas(), warnings=warnings)
        return bundle

    def _do_layers(self):
        out = []
        for layer in QgsProject.instance().mapLayers().values():
            if not isinstance(layer, QgsVectorLayer):
                continue
            geom = {0: "point", 1: "line", 2: "polygon"}.get(
                int(QgsWkbTypes.geometryType(layer.wkbType())), "unknown")
            out.append({
                "qgisId": layer.id(),
                "name": layer.name(),
                "geomType": geom,
                "featureCount": layer.featureCount(),
                "crs": layer.crs().authid(),
            })
        return out
