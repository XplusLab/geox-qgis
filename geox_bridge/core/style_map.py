"""Style translation between GEOX layer styling and QGIS renderers.

GEOX side (see geox/app/style-editor.js):
  - single symbol: layer.color (hex) + layer.opacity (0..1)
  - graduated:   {mode:'graduated', column, classes, palette, method,
                  breaks[k+1], colors[k], labels[k]}
  - categorical: {mode:'categorical', column, classes, palette,
                  values[k], colors[k], labels[k]}
  - render mode: 'default' | 'heatmap' | 'cluster' (points only)
  - label:       {column, size} | null

Stored colors/breaks are authoritative in GEOX (classify() output is saved,
never recomputed on load), so both directions copy them verbatim and use
palette:'custom' on export. Fidelity rule: degrade, never fail.
"""

from qgis.PyQt.QtGui import QColor
from qgis.core import (
    QgsCategorizedSymbolRenderer,
    QgsFillSymbol,
    QgsGradientColorRamp,
    QgsGraduatedSymbolRenderer,
    QgsHeatmapRenderer,
    QgsLineSymbol,
    QgsMarkerSymbol,
    QgsPalLayerSettings,
    QgsPointClusterRenderer,
    QgsRendererCategory,
    QgsRendererRange,
    QgsSingleSymbolRenderer,
    QgsTextBufferSettings,
    QgsTextFormat,
    QgsVectorLayerSimpleLabeling,
    QgsWkbTypes,
)

from .constants import DEFAULT_CLUSTER, DEFAULT_HEATMAP, DEFAULT_LAYER_COLOR

PX_TO_MM = 0.26458  # 96 dpi CSS pixel in millimetres

# Millimeter render unit across QGIS versions (enum moved in 3.30+).
try:
    from qgis.core import Qgis as _Qgis
    MM_UNIT = _Qgis.RenderUnit.Millimeters
except (ImportError, AttributeError):
    from qgis.core import QgsUnitTypes as _QgsUnitTypes
    MM_UNIT = _QgsUnitTypes.RenderMillimeters


# ----------------------------------------------------------------- symbols
def _darken(hex_color, factor=0.65):
    c = QColor(hex_color)
    if not c.isValid():
        c = QColor(DEFAULT_LAYER_COLOR)
    return QColor(int(c.red() * factor), int(c.green() * factor), int(c.blue() * factor)).name()


def make_symbol(geom_class, hex_color):
    """A GEOX-look symbol for 'point' | 'line' | 'polygon'."""
    color = QColor(hex_color).isValid() and hex_color or DEFAULT_LAYER_COLOR
    if geom_class == "point":
        return QgsMarkerSymbol.createSimple({
            "name": "circle", "color": color,
            "outline_color": "white", "outline_width": "0.3",
            "size": "2.4", "size_unit": "MM",
        })
    if geom_class == "line":
        return QgsLineSymbol.createSimple({
            "line_color": color, "line_width": "0.5", "line_width_unit": "MM",
        })
    return QgsFillSymbol.createSimple({
        "color": color,
        "outline_color": _darken(color), "outline_width": "0.26",
    })


def _wkb_geom_class(wkb_type):
    g = QgsWkbTypes.geometryType(wkb_type)
    if g == QgsWkbTypes.PointGeometry:
        return "point"
    if g == QgsWkbTypes.LineGeometry:
        return "line"
    if g == QgsWkbTypes.PolygonGeometry:
        return "polygon"
    return "point"


# --------------------------------------------------- GEOX -> QGIS (import)
def apply_style(vlayer, layer_dict, warnings):
    """Apply GEOX layer styling to a QGIS vector layer in place."""
    geom_class = _wkb_geom_class(vlayer.wkbType())
    color = layer_dict.get("color") or DEFAULT_LAYER_COLOR
    style = layer_dict.get("style") or None
    render = layer_dict.get("render") or "default"

    renderer = None
    if render == "heatmap" and geom_class == "point":
        renderer = _heatmap_renderer(layer_dict, color)
    elif render == "cluster" and geom_class == "point":
        renderer = _cluster_renderer(layer_dict, geom_class, color)
    elif isinstance(style, dict) and style.get("mode") == "graduated":
        renderer = _graduated_renderer(style, geom_class, warnings)
    elif isinstance(style, dict) and style.get("mode") == "categorical":
        renderer = _categorized_renderer(style, geom_class, warnings)

    if renderer is None:
        renderer = QgsSingleSymbolRenderer(make_symbol(geom_class, color))

    vlayer.setRenderer(renderer)

    opacity = layer_dict.get("opacity")
    if isinstance(opacity, (int, float)):
        vlayer.setOpacity(max(0.0, min(1.0, float(opacity))))

    _apply_label(vlayer, layer_dict.get("label"))
    vlayer.triggerRepaint()


def _graduated_renderer(style, geom_class, warnings):
    column = style.get("column")
    breaks = style.get("breaks") or []
    colors = style.get("colors") or []
    labels = style.get("labels") or []
    if not column or len(breaks) < 2 or len(colors) != len(breaks) - 1:
        warnings.append("graduated style payload malformed - fell back to single symbol")
        return None
    ranges = []
    for i, col in enumerate(colors):
        label = labels[i] if i < len(labels) else "{} - {}".format(breaks[i], breaks[i + 1])
        ranges.append(QgsRendererRange(
            float(breaks[i]), float(breaks[i + 1]),
            make_symbol(geom_class, col), str(label),
        ))
    return QgsGraduatedSymbolRenderer(column, ranges)


def _categorized_renderer(style, geom_class, warnings):
    column = style.get("column")
    values = style.get("values") or []
    colors = style.get("colors") or []
    labels = style.get("labels") or []
    if not column or not values or len(colors) < len(values):
        warnings.append("categorical style payload malformed - fell back to single symbol")
        return None
    cats = []
    for i, val in enumerate(values):
        label = labels[i] if i < len(labels) else str(val)
        cats.append(QgsRendererCategory(val, make_symbol(geom_class, colors[i]), str(label)))
    # GEOX paints unlisted values gray; mirror with a default category.
    cats.append(QgsRendererCategory(None, make_symbol(geom_class, "#9aa5b4"), "other"))
    return QgsCategorizedSymbolRenderer(column, cats)


def _heatmap_renderer(layer_dict, color):
    hm = dict(DEFAULT_HEATMAP)
    hm.update(layer_dict.get("heatmap") or {})
    r = QgsHeatmapRenderer()
    start = QColor(color)
    start.setAlpha(0)
    r.setColorRamp(QgsGradientColorRamp(start, QColor(color)))
    r.setRadius(float(hm.get("radius") or 30) * PX_TO_MM)
    r.setRadiusUnit(MM_UNIT)
    if hm.get("weightColumn"):
        r.setWeightExpression('"{}"'.format(hm["weightColumn"]))
    return r


def _cluster_renderer(layer_dict, geom_class, color):
    cl = dict(DEFAULT_CLUSTER)
    cl.update(layer_dict.get("cluster") or {})
    r = QgsPointClusterRenderer()
    r.setEmbeddedRenderer(QgsSingleSymbolRenderer(make_symbol(geom_class, color)))
    r.setTolerance(float(cl.get("radius") or 50) * PX_TO_MM)
    r.setToleranceUnit(MM_UNIT)
    return r


def _apply_label(vlayer, label_spec):
    if not isinstance(label_spec, dict) or not label_spec.get("column"):
        return
    settings = QgsPalLayerSettings()
    settings.fieldName = label_spec["column"]
    fmt = QgsTextFormat()
    fmt.setSize(float(label_spec.get("size") or 12))
    buf = QgsTextBufferSettings()
    buf.setEnabled(True)
    buf.setSize(1.0)
    buf.setColor(QColor("white"))
    fmt.setBuffer(buf)
    settings.setFormat(fmt)
    vlayer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    vlayer.setLabelsEnabled(True)


# --------------------------------------------------- QGIS -> GEOX (export)
def extract_style(vlayer, warnings):
    """Return (color_hex, opacity, style_payload_or_None, render_mode, heatmap, cluster, label)."""
    renderer = vlayer.renderer()
    opacity = float(vlayer.opacity())
    color = DEFAULT_LAYER_COLOR
    style = None
    render = "default"
    heatmap = dict(DEFAULT_HEATMAP)
    cluster = dict(DEFAULT_CLUSTER)

    if isinstance(renderer, QgsSingleSymbolRenderer):
        color = _symbol_hex(renderer.symbol(), color)
    elif isinstance(renderer, QgsGraduatedSymbolRenderer):
        column = renderer.classAttribute()
        if _is_plain_field(vlayer, column):
            ranges = renderer.ranges()
            breaks = [r.lowerValue() for r in ranges] + ([ranges[-1].upperValue()] if ranges else [])
            colors = [_symbol_hex(r.symbol(), color) for r in ranges]
            labels = [r.label() for r in ranges]
            style = {
                "mode": "graduated", "column": column, "classes": len(ranges),
                "palette": "custom", "method": "equal",
                "breaks": breaks, "colors": colors, "labels": labels,
            }
            color = colors[len(colors) // 2] if colors else color
        else:
            warnings.append(
                "layer '{}': graduated renderer uses an expression - exported as single symbol".format(vlayer.name()))
    elif isinstance(renderer, QgsCategorizedSymbolRenderer):
        column = renderer.classAttribute()
        if _is_plain_field(vlayer, column):
            cats = [c for c in renderer.categories()
                    if c.value() is not None and str(c.value()) != ""]
            dropped = len(renderer.categories()) - len(cats)
            if dropped:
                warnings.append(
                    "layer '{}': default category dropped on export".format(vlayer.name()))
            values = [c.value() for c in cats]
            colors = [_symbol_hex(c.symbol(), color) for c in cats]
            labels = [c.label() for c in cats]
            style = {
                "mode": "categorical", "column": column, "classes": len(values),
                "palette": "custom", "values": values, "colors": colors, "labels": labels,
            }
            color = colors[0] if colors else color
        else:
            warnings.append(
                "layer '{}': categorized renderer uses an expression - exported as single symbol".format(vlayer.name()))
    elif isinstance(renderer, QgsHeatmapRenderer):
        render = "heatmap"
        heatmap["radius"] = round(renderer.radius() / PX_TO_MM) if renderer.radius() else 30
        expr = (renderer.weightExpression() or "").strip().strip('"')
        if expr and _is_plain_field(vlayer, expr):
            heatmap["weightColumn"] = expr
        ramp = renderer.colorRamp()
        if ramp is not None:
            color = ramp.color(1.0).name()
    elif isinstance(renderer, QgsPointClusterRenderer):
        render = "cluster"
        cluster["radius"] = round(renderer.tolerance() / PX_TO_MM) if renderer.tolerance() else 50
        embedded = renderer.embeddedRenderer()
        if isinstance(embedded, QgsSingleSymbolRenderer):
            color = _symbol_hex(embedded.symbol(), color)
    elif renderer is not None:
        warnings.append(
            "layer '{}': renderer type '{}' not supported by GEOX - exported as single symbol".format(
                vlayer.name(), renderer.type()))
        sym = renderer.symbols(None)[0] if hasattr(renderer, "symbols") else None
        try:
            syms = renderer.symbols(_dummy_render_context())
            if syms:
                color = _symbol_hex(syms[0], color)
        except Exception:
            pass

    label = _extract_label(vlayer)
    return color, opacity, style, render, heatmap, cluster, label


def _extract_label(vlayer):
    if not vlayer.labelsEnabled() or vlayer.labeling() is None:
        return None
    try:
        settings = vlayer.labeling().settings()
        field = settings.fieldName
        if field and _is_plain_field(vlayer, field):
            return {"column": field, "size": settings.format().size() or 12}
    except Exception:
        pass
    return None


def _symbol_hex(symbol, fallback):
    try:
        c = symbol.color()
        if c.isValid():
            return c.name()
    except Exception:
        pass
    return fallback


def _is_plain_field(vlayer, name):
    if not name:
        return False
    return vlayer.fields().indexOf(name) >= 0


def _dummy_render_context():
    from qgis.core import QgsRenderContext
    return QgsRenderContext()
