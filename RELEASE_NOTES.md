# GEOX Bridge for QGIS 0.2.0

This experimental release connects QGIS 3.28+ with GEOX projects and live browser sessions.

## Included

- Import and export `.geoxjson` project bundles.
- Preserve categorized, graduated, heatmap, and cluster styling.
- Reproject QGIS layers to and from GEOX WGS84 data.
- Pair with GEOX over a localhost-only, token-protected bridge.
- Update linked QGIS layers in place during repeated syncs.

## Install

1. Download `geox_bridge.zip` from the GitHub release.
2. In QGIS, open **Plugins → Manage and Install Plugins → Install from ZIP**.
3. Select the downloaded ZIP and enable **GEOX Bridge**.
4. Open **Plugins → GEOX Bridge** to import, export, or start live sync.

The first official QGIS repository release remains marked experimental while it is field-tested.
