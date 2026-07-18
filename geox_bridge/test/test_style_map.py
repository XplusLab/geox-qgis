"""Focused style translation tests. Require QGIS's Python."""

import pytest

qgis_core = pytest.importorskip("qgis.core", reason="run under QGIS's bundled Python")

from qgis.PyQt.QtCore import QVariant  # noqa: E402
from qgis.core import (  # noqa: E402
    QgsCategorizedSymbolRenderer,
    QgsCoordinateReferenceSystem,
    QgsField,
    QgsFields,
    QgsGraduatedSymbolRenderer,
    QgsMemoryProviderUtils,
    QgsRendererCategory,
    QgsRendererRange,
    QgsSingleSymbolRenderer,
    QgsWkbTypes,
)

from geox_bridge.core import style_map  # noqa: E402


@pytest.fixture(autouse=True)
def _qgis(qgis_app):
    """All tests in this module need the shared QgsApplication."""


def _point_layer(with_field="v"):
    fields = QgsFields()
    fields.append(QgsField(with_field, QVariant.Double))
    return QgsMemoryProviderUtils.createMemoryLayer(
        "t", fields, QgsWkbTypes.Point, QgsCoordinateReferenceSystem("EPSG:4326"))


def test_make_symbol_color():
    sym = style_map.make_symbol("point", "#123456")
    assert sym.color().name() == "#123456"
    sym = style_map.make_symbol("polygon", "not-a-color")
    assert sym.color().isValid()


def test_apply_graduated_and_extract_back():
    vl = _point_layer()
    warnings = []
    style_map.apply_style(vl, {
        "color": "#e41a1c", "opacity": 0.7,
        "style": {"mode": "graduated", "column": "v", "classes": 2,
                  "palette": "OrRd", "method": "equal",
                  "breaks": [0, 5, 10], "colors": ["#aaaaaa", "#bbbbbb"],
                  "labels": ["low", "high"]},
        "render": "default",
    }, warnings)
    assert isinstance(vl.renderer(), QgsGraduatedSymbolRenderer)
    assert warnings == []
    color, opacity, style, render, _hm, _cl, _lbl = style_map.extract_style(vl, warnings)
    assert style["mode"] == "graduated"
    assert style["breaks"] == pytest.approx([0, 5, 10])
    assert style["colors"] == ["#aaaaaa", "#bbbbbb"]
    assert style["labels"] == ["low", "high"]
    assert opacity == pytest.approx(0.7)


def test_malformed_style_falls_back(caplog=None):
    vl = _point_layer()
    warnings = []
    style_map.apply_style(vl, {
        "color": "#00ff00",
        "style": {"mode": "graduated", "column": "v", "breaks": [1], "colors": []},
    }, warnings)
    assert isinstance(vl.renderer(), QgsSingleSymbolRenderer)
    assert any("malformed" in w for w in warnings)
    assert vl.renderer().symbol().color().name() == "#00ff00"


def test_categorical_default_category_dropped_on_export():
    vl = _point_layer("kind")
    cats = [
        QgsRendererCategory("a", style_map.make_symbol("point", "#111111"), "a"),
        QgsRendererCategory("b", style_map.make_symbol("point", "#222222"), "b"),
        QgsRendererCategory(None, style_map.make_symbol("point", "#333333"), "other"),
    ]
    vl.setRenderer(QgsCategorizedSymbolRenderer("kind", cats))
    warnings = []
    _c, _o, style, _r, _hm, _cl, _lbl = style_map.extract_style(vl, warnings)
    assert style["mode"] == "categorical"
    assert style["values"] == ["a", "b"]
    assert any("default category" in w for w in warnings)


def test_expression_renderer_degrades():
    vl = _point_layer()
    ranges = [QgsRendererRange(0, 1, style_map.make_symbol("point", "#111111"), "x")]
    vl.setRenderer(QgsGraduatedSymbolRenderer('"v" * 2', ranges))
    warnings = []
    _c, _o, style, _r, _hm, _cl, _lbl = style_map.extract_style(vl, warnings)
    assert style is None
    assert any("expression" in w for w in warnings)
