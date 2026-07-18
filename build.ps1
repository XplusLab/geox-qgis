# Build dist/geox_bridge.zip for QGIS "Install from ZIP" / plugins.qgis.org
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$stage = Join-Path $env:TEMP ("geox-bridge-build-" + (Get-Date -Format 'HHmmss'))
$dist = Join-Path $root 'dist'
New-Item -ItemType Directory -Force $dist | Out-Null
robocopy (Join-Path $root 'geox_bridge') (Join-Path $stage 'geox_bridge') /E /XD test __pycache__ /XF *.pyc | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy failed with $LASTEXITCODE" }
Copy-Item (Join-Path $root 'LICENSE') (Join-Path $stage 'geox_bridge\LICENSE')
Copy-Item (Join-Path $root 'README.md') (Join-Path $stage 'geox_bridge\README.md')
$zip = Join-Path $dist 'geox_bridge.zip'
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path (Join-Path $stage 'geox_bridge') -DestinationPath $zip
Remove-Item -Recurse -Force $stage
Write-Host ("Built {0} ({1:N0} KB)" -f $zip, ((Get-Item $zip).Length / 1KB))
exit 0
