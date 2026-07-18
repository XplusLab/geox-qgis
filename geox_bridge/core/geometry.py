"""Geometry normalization between QGIS and GEOX (GeoJSON, WGS84).

GEOX stores plain GeoJSON with no Z/M, no curves, and no CRS field
(everything is implicitly EPSG:4326 lon/lat). These helpers make QGIS
geometries safe for that world and classify GeoJSON geometries on the
way back in.
"""

import json
import math

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsProject,
    QgsWkbTypes,
)

from .constants import COORD_PRECISION

EPSG_4326 = QgsCoordinateReferenceSystem("EPSG:4326")

# GeoJSON geometry type -> GEOX geometry class
GEOJSON_CLASS = {
    "Point": "point", "MultiPoint": "point",
    "LineString": "line", "MultiLineString": "line",
    "Polygon": "polygon", "MultiPolygon": "polygon",
}


def geometry_class(geojson_geom):
    """'point' | 'line' | 'polygon' | None for a GeoJSON geometry dict."""
    if not geojson_geom or not isinstance(geojson_geom, dict):
        return None
    t = geojson_geom.get("type")
    if t == "GeometryCollection":
        return None  # not produced by GEOX; skip rather than guess
    return GEOJSON_CLASS.get(t)


def transform_to_4326(crs):
    """A QgsCoordinateTransform from crs to EPSG:4326, or None if already 4326."""
    if crs == EPSG_4326 or crs.authid() == "EPSG:4326":
        return None
    return QgsCoordinateTransform(crs, EPSG_4326, QgsProject.instance().transformContext())


def normalize_geometry(geom, xform):
    """Return a GeoJSON dict for a QgsGeometry, normalized for GEOX.

    Reprojects (when xform given), strips Z/M, segmentizes curves.
    Returns None for null/empty geometry or on failure.
    """
    if geom is None or geom.isNull() or geom.isEmpty():
        return None
    g = type(geom)(geom)  # copy; transforms mutate in place
    if xform is not None:
        if g.transform(xform) != 0:
            return None
    if QgsWkbTypes.isCurvedType(g.wkbType()):
        g = g.convertToStraightSegment() or g
    abstract = g.get()
    if abstract is None:
        return None
    if abstract.is3D():
        abstract.dropZValue()
    if abstract.isMeasure():
        abstract.dropMValue()
    txt = g.asJson(COORD_PRECISION)
    if not txt:
        return None
    try:
        gj = json.loads(txt)
    except ValueError:
        return None
    if gj.get("type") == "GeometryCollection":
        return None
    return gj


def clean_number(value):
    """JSON-safe number: NaN/Infinity become None (invalid in strict JSON)."""
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def zoom_to_scale(zoom):
    from .constants import SCALE_AT_ZOOM_0
    return SCALE_AT_ZOOM_0 / (2 ** max(0.0, min(24.0, float(zoom))))


def scale_to_zoom(scale):
    from .constants import SCALE_AT_ZOOM_0
    if not scale or scale <= 0:
        return 12.0
    z = math.log2(SCALE_AT_ZOOM_0 / scale)
    return max(0.0, min(22.0, z))
