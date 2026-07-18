"""Round-trip tests for the bundle translation. Require QGIS's Python."""

import json
import os

import pytest

qgis_core = pytest.importorskip("qgis.core", reason="run under QGIS's bundled Python")

from qgis.core import QgsProject  # noqa: E402

from geox_bridge.core import geoxjson  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "sample-project.geoxjson")


@pytest.fixture(autouse=True)
def _qgis(qgis_app):
    """All tests in this module need the shared QgsApplication."""


@pytest.fixture()
def bundle():
    with open(FIXTURE, "r", encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(autouse=True)
def clean_project():
    yield
    QgsProject.instance().clear()


def test_validate_bundle_rejects_foreign(bundle):
    with pytest.raises(geoxjson.GeoxFormatError):
        geoxjson.validate_bundle({"foo": 1})
    with pytest.raises(geoxjson.GeoxFormatError):
        geoxjson.validate_bundle({"_format": "something-else", "project": {}})
    assert geoxjson.validate_bundle(bundle) is None
    newer = dict(bundle, _version=99)
    assert "newer" in geoxjson.validate_bundle(newer)


def test_import_creates_layers_and_splits_mixed(bundle):
    report = geoxjson.bundle_to_qgis(bundle)
    names = [n for _i, n in report.layers_created]
    # 6 plain layers + mixed split into 2 = 8
    assert len(names) == 8
    assert "Districts" in names and "Shops" in names and "Streets" in names
    assert "Mixed sketch (points)" in names and "Mixed sketch (lines)" in names
    assert "Contours" in names and "Buildings 3D" in names
    # the null-geometry shop was skipped
    assert report.features_skipped == 1
    assert any("mixes geometry types" in w for w in report.warnings)


def test_import_features_and_fields(bundle):
    report = geoxjson.bundle_to_qgis(bundle)
    project = QgsProject.instance()
    districts = next(project.mapLayer(i) for i, n in report.layers_created
                     if n == "Districts")
    assert districts.featureCount() == 3
    field_names = [f.name() for f in districts.fields()]
    assert field_names == ["name", "population"]
    values = sorted(f["population"] for f in districts.getFeatures())
    assert values == [1200, 3400, 5100]


def test_import_styles(bundle):
    from qgis.core import (QgsCategorizedSymbolRenderer,
                           QgsGraduatedSymbolRenderer, QgsHeatmapRenderer)
    report = geoxjson.bundle_to_qgis(bundle)
    project = QgsProject.instance()
    by_name = {n: project.mapLayer(i) for i, n in report.layers_created}
    grad = by_name["Districts"].renderer()
    assert isinstance(grad, QgsGraduatedSymbolRenderer)
    assert len(grad.ranges()) == 3
    assert grad.classAttribute() == "population"
    assert grad.ranges()[0].lowerValue() == pytest.approx(1200)
    cat = by_name["Shops"].renderer()
    assert isinstance(cat, QgsCategorizedSymbolRenderer)
    heat = by_name["Incidents heat"].renderer()
    assert isinstance(heat, QgsHeatmapRenderer)
    assert by_name["Districts"].labelsEnabled()


def test_round_trip_geometry_and_attributes(bundle):
    report = geoxjson.bundle_to_qgis(bundle)
    project = QgsProject.instance()
    layers = [project.mapLayer(i) for i, _n in report.layers_created]
    out = geoxjson.qgis_to_bundle(layers, project_name="round trip")
    assert out["_format"] == "y-plus-1" and out["_version"] == 1

    src_districts = bundle["project"]["layers"][0]
    dst_districts = next(l for l in out["project"]["layers"] if l["name"] == "Districts")
    assert dst_districts["desktopLink"]["app"] == "qgis"
    assert dst_districts["desktopLink"]["sourceId"]
    assert dst_districts["geomType"] == "polygon"
    assert len(dst_districts["data"]["features"]) == 3
    # attribute fidelity
    src_pops = sorted(f["properties"]["population"]
                      for f in src_districts["data"]["features"])
    dst_pops = sorted(f["properties"]["population"]
                      for f in dst_districts["data"]["features"])
    assert src_pops == dst_pops
    # geometry fidelity within 1e-7 (first ring, first vertex)
    src_pt = src_districts["data"]["features"][0]["geometry"]["coordinates"][0][0]
    coords = dst_districts["data"]["features"][0]["geometry"]["coordinates"]
    # exported polygons may be Multi; unwrap
    ring = coords[0][0] if isinstance(coords[0][0][0], list) else coords[0]
    assert ring[0][0] == pytest.approx(src_pt[0], abs=1e-7)
    assert ring[0][1] == pytest.approx(src_pt[1], abs=1e-7)
    # style round trip
    assert dst_districts["style"]["mode"] == "graduated"
    assert dst_districts["style"]["column"] == "population"
    assert dst_districts["style"]["breaks"] == pytest.approx([1200, 2500, 3800, 5100])
    assert dst_districts["style"]["colors"] == ["#fee8c8", "#fdbb84", "#e34a33"]
    assert dst_districts["label"] == {"column": "name", "size": 12}

    dst_shops = next(l for l in out["project"]["layers"] if l["name"] == "Shops")
    assert dst_shops["style"]["mode"] == "categorical"
    assert dst_shops["style"]["values"] == ["cafe", "market"]

    dst_heat = next(l for l in out["project"]["layers"] if l["name"] == "Incidents heat")
    assert dst_heat["render"] == "heatmap"
    assert dst_heat["heatmap"]["weightColumn"] == "weight"


def test_export_reprojects_utm(qgis_app):
    """A UTM 36N layer must come out as lon/lat near Cairo."""
    from qgis.core import (QgsCoordinateReferenceSystem, QgsFeature, QgsField,
                           QgsFields, QgsGeometry, QgsMemoryProviderUtils,
                           QgsPointXY, QgsWkbTypes)
    from qgis.PyQt.QtCore import QVariant
    fields = QgsFields()
    fields.append(QgsField("id", QVariant.Int))
    vl = QgsMemoryProviderUtils.createMemoryLayer(
        "utm", fields, QgsWkbTypes.Point,
        QgsCoordinateReferenceSystem("EPSG:32636"))
    f = QgsFeature(fields)
    # 330000 E, 3325000 N is around Cairo in UTM zone 36N
    f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(330000, 3325000)))
    f.setAttribute(0, 1)
    vl.dataProvider().addFeatures([f])
    out = geoxjson.qgis_to_bundle([vl], project_name="utm test")
    coords = out["project"]["layers"][0]["data"]["features"][0]["geometry"]["coordinates"]
    lon, lat = (coords[0] if isinstance(coords[0], list) else coords)[:2] \
        if isinstance(coords[0], list) else coords
    assert 29.0 < lon < 33.0
    assert 29.0 < lat < 31.5


def test_export_skips_oversized_layer(bundle):
    report = geoxjson.bundle_to_qgis(bundle)
    project = QgsProject.instance()
    layers = [project.mapLayer(i) for i, _n in report.layers_created]
    warnings = []
    out = geoxjson.qgis_to_bundle(layers, max_features=2, warnings=warnings)
    # Districts (3 features) and Shops (3 features) skipped by the guardrail
    names = [l["name"] for l in out["project"]["layers"]]
    assert "Districts" not in names
    assert any("guardrail" in w for w in warnings)
