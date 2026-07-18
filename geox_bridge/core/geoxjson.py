"""Bidirectional translation between GEOX .geoxjson bundles and QGIS layers.

Bundle shape (geox/app/io.js + idb-persist.js):
{
  "_format": "y-plus-1", "_version": 1, "exportedAt": ISO,
  "project": {
    "id", "name", "createdAt", "updatedAt", "userId",
    "basemap", "customBasemap",
    "viewState": {"center": [lon, lat], "zoom", "bearing", "pitch"},
    "layers": [ {id, name, color, opacity, visible, geomType,
                 data: <GeoJSON FeatureCollection>, columns, _drawable,
                 render, heatmap, cluster, style, filter, filterSpec, label,
                 desktopLink?: {app, sourceId}} ]
  }
}
All coordinates are EPSG:4326 [lon, lat]. QGIS reprojects at this edge.
"""

import datetime
import json
import time
import uuid

from qgis.PyQt.QtCore import QDate, QDateTime, QTime, Qt, QVariant
from qgis.core import (
    NULL,
    QgsCoordinateTransform,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsMemoryProviderUtils,
    QgsPointXY,
    QgsProject,
    QgsVectorLayer,
    QgsWkbTypes,
)

from . import style_map
from .constants import (
    BUNDLE_FORMAT,
    BUNDLE_VERSION,
    DEFAULT_CLUSTER,
    DEFAULT_HEATMAP,
    LAYER_COLORS,
    MAX_FEATURES_PER_LAYER,
)
from .geometry import (
    EPSG_4326,
    clean_number,
    geometry_class,
    normalize_geometry,
    scale_to_zoom,
    transform_to_4326,
    zoom_to_scale,
)


class GeoxFormatError(Exception):
    """Raised when a bundle is not a GEOX project."""


class ImportReport:
    def __init__(self):
        self.layers_created = []      # [(qgis_layer_id, name)]
        self.features_skipped = 0
        self.warnings = []

    def summary(self):
        parts = ["{} layer(s) created".format(len(self.layers_created))]
        if self.features_skipped:
            parts.append("{} feature(s) skipped".format(self.features_skipped))
        if self.warnings:
            parts.append("{} warning(s)".format(len(self.warnings)))
        return ", ".join(parts)


# ================================================================ import
def validate_bundle(bundle):
    if not isinstance(bundle, dict) or "project" not in bundle:
        raise GeoxFormatError("Not a GEOX project file (missing 'project').")
    if bundle.get("_format") != BUNDLE_FORMAT:
        raise GeoxFormatError(
            "Not a GEOX project file (format tag is {!r}).".format(bundle.get("_format")))
    version = bundle.get("_version")
    warn = None
    if isinstance(version, (int, float)) and version > BUNDLE_VERSION:
        warn = ("bundle version {} is newer than this plugin understands ({}) - "
                "importing anyway, some settings may be ignored").format(version, BUNDLE_VERSION)
    return warn


GEOM_WKB = {
    "point": QgsWkbTypes.MultiPoint,
    "line": QgsWkbTypes.MultiLineString,
    "polygon": QgsWkbTypes.MultiPolygon,
}
COLUMN_QVARIANT = {
    "number": QVariant.Double,
    "text": QVariant.String,
    "boolean": QVariant.Bool,
}


def bundle_to_qgis(bundle, apply_view=False, canvas=None, group_name=None):
    """Create QGIS layers from a bundle. Returns ImportReport."""
    report = ImportReport()
    version_warning = validate_bundle(bundle)
    if version_warning:
        report.warnings.append(version_warning)

    project = bundle["project"]
    layers = project.get("layers") or []
    root = QgsProject.instance().layerTreeRoot()
    group = root.insertGroup(0, group_name or project.get("name") or "GEOX project")

    for layer_dict in layers:
        _import_layer(layer_dict, group, report)

    if apply_view and canvas is not None:
        _apply_view_state(project.get("viewState"), canvas)

    return report


def _import_layer(layer_dict, group, report):
    name = layer_dict.get("name") or layer_dict.get("id") or "layer"
    data = layer_dict.get("data") or {}
    features = data.get("features") or []

    # Partition by geometry class (GEOX allows mixed layers; QGIS does not).
    buckets = {"point": [], "line": [], "polygon": []}
    for feat in features:
        cls = geometry_class(feat.get("geometry"))
        if cls is None:
            report.features_skipped += 1
            continue
        buckets[cls].append(feat)

    present = [c for c in ("point", "line", "polygon") if buckets[c]]
    if not present:
        if features:
            report.warnings.append("layer '{}': no importable geometry".format(name))
        else:
            # Empty layer: still create a placeholder matching declared type.
            declared = layer_dict.get("geomType")
            if declared in GEOM_WKB:
                present = [declared]
    if len(present) > 1:
        report.warnings.append("layer '{}' mixes geometry types - split into {} layers"
                               .format(name, len(present)))

    fields = _fields_from_layer(layer_dict, features)

    for cls in present:
        sub_name = name if len(present) == 1 else "{} ({}s)".format(name, cls)
        vlayer = QgsMemoryProviderUtils.createMemoryLayer(
            sub_name, fields, GEOM_WKB[cls], EPSG_4326)
        if not vlayer or not vlayer.isValid():
            report.warnings.append("layer '{}': could not create memory layer".format(sub_name))
            continue

        qgis_feats = []
        for feat in buckets[cls]:
            qf = _build_feature(feat, vlayer.fields(), report)
            if qf is not None:
                qgis_feats.append(qf)
        if qgis_feats:
            vlayer.dataProvider().addFeatures(qgis_feats)
        vlayer.updateExtents()

        style_map.apply_style(vlayer, layer_dict, report.warnings)

        QgsProject.instance().addMapLayer(vlayer, False)
        node = group.addLayer(vlayer)
        if node is not None and layer_dict.get("visible") is False:
            node.setItemVisibilityChecked(False)
        report.layers_created.append((vlayer.id(), sub_name))

    if layer_dict.get("filter") or layer_dict.get("filterSpec"):
        report.warnings.append(
            "layer '{}': GEOX filter not translated - all features imported".format(name))


def _fields_from_layer(layer_dict, features):
    fields = QgsFields()
    columns = layer_dict.get("columns")
    if columns:
        for col in columns:
            fields.append(QgsField(col.get("name") or "field",
                                   COLUMN_QVARIANT.get(col.get("type"), QVariant.String)))
        return fields
    # No columns cache: infer by scanning properties.
    seen = {}
    for feat in features:
        for key, value in (feat.get("properties") or {}).items():
            if key in seen and seen[key] != QVariant.String:
                if not isinstance(value, bool) and isinstance(value, (int, float)):
                    continue
                seen[key] = QVariant.String
            elif key not in seen:
                if isinstance(value, bool):
                    seen[key] = QVariant.Bool
                elif isinstance(value, (int, float)):
                    seen[key] = QVariant.Double
                else:
                    seen[key] = QVariant.String
    for key, qtype in seen.items():
        fields.append(QgsField(key, qtype))
    return fields


def _build_feature(feat, fields, report):
    geom_json = feat.get("geometry")
    try:
        from osgeo import ogr
        ogr_geom = ogr.CreateGeometryFromJson(json.dumps(geom_json))
        wkt = ogr_geom.ExportToWkt() if ogr_geom is not None else None
    except Exception:
        wkt = None
    geom = QgsGeometry.fromWkt(wkt) if wkt else QgsGeometry()
    if geom.isNull() or geom.isEmpty():
        report.features_skipped += 1
        return None
    geom.convertToMultiType()

    qf = QgsFeature(fields)
    qf.setGeometry(geom)
    props = feat.get("properties") or {}
    for i in range(fields.count()):
        name = fields.at(i).name()
        if name in props and props[name] is not None:
            value = props[name]
            if fields.at(i).type() == QVariant.Double and isinstance(value, str):
                try:
                    value = float(value)
                except ValueError:
                    value = None
            if value is not None:
                qf.setAttribute(i, value)
    return qf


def _apply_view_state(view_state, canvas):
    if not isinstance(view_state, dict):
        return
    center = view_state.get("center")
    if isinstance(center, (list, tuple)) and len(center) == 2:
        dest = QgsProject.instance().crs()
        pt = QgsPointXY(float(center[0]), float(center[1]))
        if dest.isValid() and dest != EPSG_4326:
            xform = QgsCoordinateTransform(EPSG_4326, dest,
                                           QgsProject.instance().transformContext())
            try:
                pt = xform.transform(pt)
            except Exception:
                return
        canvas.setCenter(pt)
    zoom = view_state.get("zoom")
    if isinstance(zoom, (int, float)):
        canvas.zoomScale(zoom_to_scale(zoom))
    canvas.refresh()


# ================================================================ export
QVARIANT_NUMBER = {QVariant.Int, QVariant.UInt, QVariant.LongLong,
                   QVariant.ULongLong, QVariant.Double}
QVARIANT_DATE = {QVariant.Date, QVariant.DateTime, QVariant.Time}


class ExportError(Exception):
    pass


def qgis_to_bundle(layers, project_name=None, canvas=None,
                   max_features=MAX_FEATURES_PER_LAYER, warnings=None):
    """Serialize QGIS vector layers into a GEOX bundle dict.

    Non-vector layers are skipped with a warning. Raises ExportError only
    when nothing at all can be exported.
    """
    warnings = warnings if warnings is not None else []
    now_ms = int(time.time() * 1000)
    out_layers = []
    seq = 0

    for layer in layers:
        if not isinstance(layer, QgsVectorLayer):
            warnings.append("'{}' skipped - GEOX supports vector layers only".format(layer.name()))
            continue
        if max_features and layer.featureCount() > max_features:
            warnings.append("'{}' skipped - {} features exceeds the {} browser guardrail"
                            .format(layer.name(), layer.featureCount(), max_features))
            continue
        seq += 1
        out_layers.append(_export_layer(layer, seq, warnings))

    if not out_layers:
        raise ExportError("No exportable vector layers." +
                          (" " + "; ".join(warnings) if warnings else ""))

    name = project_name or QgsProject.instance().title() \
        or QgsProject.instance().baseName() or "QGIS export"

    bundle = {
        "_format": BUNDLE_FORMAT,
        "_version": BUNDLE_VERSION,
        "exportedAt": datetime.datetime.now(datetime.timezone.utc)
                              .isoformat().replace("+00:00", "Z"),
        "project": {
            "id": str(uuid.uuid4()),
            "name": name,
            "createdAt": now_ms,
            "updatedAt": now_ms,
            "userId": None,
            "basemap": "positron",
            "customBasemap": None,
            "viewState": _view_state_from_canvas(canvas),
            "layers": out_layers,
        },
    }
    return bundle


def _export_layer(vlayer, seq, warnings):
    xform = transform_to_4326(vlayer.crs())
    fields = vlayer.fields()
    columns = []
    for i in range(fields.count()):
        f = fields.at(i)
        if f.type() in QVARIANT_NUMBER:
            ctype = "number"
        elif f.type() == QVariant.Bool:
            ctype = "boolean"
        else:
            ctype = "text"
        columns.append({"name": f.name(), "type": ctype})

    features = []
    skipped = 0
    for feat in vlayer.getFeatures():
        gj = normalize_geometry(feat.geometry(), xform)
        if gj is None:
            skipped += 1
            continue
        props = {}
        for i in range(fields.count()):
            value = feat.attribute(i)
            if value is None or value == NULL:
                continue
            props[fields.at(i).name()] = _export_value(value)
        features.append({"type": "Feature", "geometry": gj, "properties": props})
    if skipped:
        warnings.append("layer '{}': {} feature(s) without usable geometry skipped"
                        .format(vlayer.name(), skipped))

    color, opacity, style, render, heatmap, cluster, label = \
        style_map.extract_style(vlayer, warnings)

    geom_class = {0: "point", 1: "line", 2: "polygon"}.get(
        int(QgsWkbTypes.geometryType(vlayer.wkbType())), "empty")
    if not features:
        geom_class = geom_class if geom_class != "empty" else "empty"

    root = QgsProject.instance().layerTreeRoot()
    node = root.findLayer(vlayer.id())
    visible = node.isVisible() if node is not None else True

    if color == style_map.DEFAULT_LAYER_COLOR:
        color = LAYER_COLORS[(seq - 1) % len(LAYER_COLORS)]

    return {
        "id": "layer-{}".format(seq),
        "name": vlayer.name(),
        "color": color,
        "opacity": round(max(0.0, min(1.0, opacity)), 3),
        "visible": bool(visible),
        "geomType": geom_class,
        "data": {"type": "FeatureCollection", "features": features},
        "columns": columns,
        "_drawable": True,
        "render": render,
        "heatmap": heatmap or dict(DEFAULT_HEATMAP),
        "cluster": cluster or dict(DEFAULT_CLUSTER),
        "style": style,
        "filter": None,
        "filterSpec": None,
        "label": label,
        "desktopLink": {"app": "qgis", "sourceId": vlayer.id()},
    }


def _export_value(value):
    if isinstance(value, QDateTime):
        return value.toString(Qt.ISODate)
    if isinstance(value, (QDate, QTime)):
        return value.toString(Qt.ISODate)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return clean_number(value)
    if isinstance(value, (str,)):
        return value
    return str(value)


def _view_state_from_canvas(canvas):
    if canvas is None:
        return {"center": [0.0, 20.0], "zoom": 2, "bearing": 0, "pitch": 0}
    dest = canvas.mapSettings().destinationCrs()
    center = canvas.center()
    pt = QgsPointXY(center)
    if dest.isValid() and dest != EPSG_4326:
        xform = QgsCoordinateTransform(dest, EPSG_4326,
                                       QgsProject.instance().transformContext())
        try:
            pt = xform.transform(pt)
        except Exception:
            pt = QgsPointXY(0.0, 20.0)
    return {
        "center": [round(pt.x(), 6), round(pt.y(), 6)],
        "zoom": round(scale_to_zoom(canvas.scale()), 2),
        "bearing": float(canvas.rotation() or 0),
        "pitch": 0,
    }
