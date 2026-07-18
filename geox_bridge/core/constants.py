"""Shared constants for the GEOX bundle format.

The magic string and version come from geox/app/io.js in the GEOX web app
(MAGIC = 'y-plus-1', FORMAT_VERSION = 1). The magic predates the GEOX
rebrand and must be preserved verbatim or GEOX will reject the bundle.
"""

PLUGIN_VERSION = "0.2.0"
BUNDLE_FORMAT = "y-plus-1"
BUNDLE_VERSION = 1
BRIDGE_PROTOCOL = 1

DEFAULT_BRIDGE_PORT = 8765
ALLOWED_ORIGINS = ("https://xplus-studio.com", "https://www.xplus-studio.com")
DEV_ORIGINS = ("http://localhost:8793", "http://127.0.0.1:8793")
LICENSE_VERIFY_URL = "https://xplus-studio.com/api/verify-license"

# Honest browser limits: GEOX runs client-side.
MAX_FEATURES_PER_LAYER = 50_000
MAX_BUNDLE_BYTES = 30 * 1024 * 1024   # export guardrail
MAX_REQUEST_BYTES = 100 * 1024 * 1024  # bridge body cap

# Coordinate precision on export (7 decimals is roughly 1 cm).
COORD_PRECISION = 7

# MapLibre zoom <-> QGIS scale (Web Mercator at the equator, 96 dpi).
SCALE_AT_ZOOM_0 = 591657527.591555

# Default layer shapes mirrored from geox/app/idb-persist.js blankProject().
DEFAULT_HEATMAP = {"radius": 30, "intensity": 1, "weightColumn": None}
DEFAULT_CLUSTER = {"radius": 50, "maxZoom": 14}
DEFAULT_LAYER_COLOR = "#e4572e"

# GEOX layer color cycle (matches the categorical fallbacks used in the app).
LAYER_COLORS = [
    "#e41a1c", "#377eb8", "#4daf4a", "#984ea3",
    "#ff7f00", "#a65628", "#f781bf", "#999999",
]
