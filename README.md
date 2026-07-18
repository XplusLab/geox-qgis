# GEOX Bridge for QGIS

Sync and transfer projects between [GEOX](https://xplus-studio.com/geox) — the browser GIS
for urban analysis — and QGIS Desktop (3.28+).

## What it does

| Feature | Tier |
|---|---|
| Import `.geoxjson` project bundles as styled QGIS layers | Free |
| Export QGIS vector layers to `.geoxjson` (graduated / categorized / heatmap / cluster styling preserved) | Free |
| Live bridge: push and pull directly between an open GEOX tab and QGIS over localhost | GEOX Pro |

All GEOX data is WGS84 GeoJSON. The plugin reprojects at the QGIS edge — any source CRS
exports correctly; imported layers arrive as EPSG:4326 and reproject on the fly.

## Install

1. Download `geox_bridge.zip` from Releases (or build it, below).
2. QGIS: `Plugins > Manage and Install Plugins > Install from ZIP`.
3. A GEOX panel appears under `Plugins > GEOX Bridge` and as a toolbar icon.

## Live sync

1. In the GEOX panel, enter your GEOX Pro license key and press **Start bridge**.
   A 6-digit pairing code appears.
2. In GEOX (browser), open **Export > QGIS** and enter the code.
3. Push the current GEOX project into QGIS, or pull QGIS layers into GEOX — no files.

The bridge listens on `127.0.0.1` only, requires the pairing code, and only accepts
requests from `xplus-studio.com` origins. It is off by default and forgets its pairing
when QGIS closes.

## Build

```powershell
./build.ps1     # -> dist/geox_bridge.zip
```

Dev loop: link the package into your QGIS profile and use the Plugin Reloader plugin:

```powershell
New-Item -ItemType Junction -Path "$env:APPDATA\QGIS\QGIS3\profiles\default\python\plugins\geox_bridge" -Target "D:\X+1\geox-qgis\geox_bridge"
```

## Tests

Run under QGIS's bundled Python so `qgis.core` is importable:

```powershell
& "$env:OSGEO4W_ROOT\bin\python-qgis-ltr.bat" -m pytest geox_bridge/test
```

`test_bridge.py` (handlers, pairing, CORS/PNA) also runs under plain Python 3 —
the bridge handlers deliberately have no QGIS imports.

## Format notes

The `.geoxjson` bundle keeps the legacy magic `_format: "y-plus-1"` (pre-rebrand) —
GEOX validates it verbatim. Layer styling payloads are copied verbatim in both
directions; colors and class breaks are authoritative, palette names are not.

## License

GPL-2.0-or-later (required for QGIS plugins). See LICENSE.
