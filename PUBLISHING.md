# Publishing GEOX Bridge for QGIS

Primary channel: the **official QGIS plugin repository** (plugins.qgis.org) — users
then install it from inside QGIS via *Plugins → Manage and Install Plugins*. GitHub
Releases is a good interim channel while the first upload is reviewed.

Publishing to plugins.qgis.org needs an **OSGeo account** (free) — it can't be
automated without your credentials.

## 0. Prerequisites

- An OSGeo web account: sign up at <https://www.osgeo.org/community/getting-started-osgeo/osgeo_userid/>
  (the same login works on plugins.qgis.org).
- A clean build: run `./build.ps1` — it produces `dist/geox_bridge.zip`.
- The zip must contain exactly one top-level folder (`geox_bridge/`) with
  `metadata.txt` inside. `build.ps1` already does this; verify with any zip viewer.

## 1. Pre-flight checks

- [ ] `python -m pytest geox_bridge/test` (or `python-qgis-ltr.bat -m pytest …`) green.
- [ ] `metadata.txt` is complete and correct:
  - `name`, `version` (0.2.0), `qgisMinimumVersion` (3.28), `description`, `about`,
    `author`, `email`, `tracker`, `repository`, `tags`, `category=Web`, `icon`,
    `experimental=True`, `license` implied by the GPL `LICENSE` file.
  - **First upload should keep `experimental=True`** so it lands in the experimental
    section; flip to `False` for a stable release once field-tested.
- [ ] `LICENSE` present (GPL-2.0-or-later — required by the repository).
- [ ] Loaded once in a real QGIS: `Install from ZIP`, run `GeoxBridgeSelfTest`-equivalent
      manual matrix (import a `.geoxjson`, export layers, start the bridge and pair).
- [ ] The repo URLs in `metadata.txt` resolve (create the GitHub repo first, §4).

## 2. Upload to plugins.qgis.org

1. Sign in at <https://plugins.qgis.org> with your OSGeo account.
2. **Plugins → Upload a plugin** (or `/plugins/add/`).
3. Choose `dist/geox_bridge.zip`. The site reads `metadata.txt` automatically.
4. Confirm the parsed name, version, description, and that it's flagged experimental.
5. Submit. The first version of a new plugin is **held for a short manual approval**
   by the QGIS plugin team; later versions of the same plugin publish immediately.
6. Once approved it appears in QGIS's plugin manager (tick *Show experimental plugins*
   in Settings until you ship a stable version).

Version bumps: edit `version=` in `metadata.txt`, rebuild, upload the new zip on the
plugin's page. Never reuse a version number.

## 3. Approval tips (avoid a bounce)

- The plugin must not embed third-party code with incompatible licenses. GEOX Bridge is
  pure-Python + PyQt/QGIS APIs only — fine.
- `about` must be substantive (it is).
- No network calls on load; the bridge server is off until the user starts it, and the
  license check only runs when the user enters a Pro key — good.
- Keep the zip lean (no `test/`, `__pycache__`, `dist/`); `build.ps1` excludes them.

## 4. GitHub Releases (immediate, no review)

Good for early testers and to satisfy the `repository`/`tracker` URLs in `metadata.txt`:

```powershell
gh repo create XplusLab/geox-qgis --public --source . --remote origin   # first time
git add -A && git commit -m "GEOX Bridge for QGIS 0.2.0"
git push -u origin main
gh release create v0.2.0 dist\geox_bridge.zip `
  --title "GEOX Bridge for QGIS 0.2.0" --notes-file RELEASE_NOTES.md
```

Users install via *Plugins → Install from ZIP* pointed at the downloaded file.

## 5. After publishing

- Link the plugin from the GEOX site (the "Sync with QGIS / Rhino" panel already points
  at `https://github.com/XplusLab/geox-qgis`).
- Watch the tracker for issues; the plugin auto-updates through the QGIS manager once a
  new version is uploaded.
