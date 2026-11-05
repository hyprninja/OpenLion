<#
.SYNOPSIS
  Builds the OpenLion MSI: the self-contained app, an embedded Python with osmium, and the engine.

.DESCRIPTION
  Needs: .NET 10 SDK, the WiX 6 tool (dotnet tool install -g wix --version 6.0.2;
  wix extension add -g WixToolset.UI.wixext/6.0.2), and any Python 3 with pip (only used to
  download the osmium wheel for Python 3.12 / win_amd64).

  Output: dist\OpenLion-<Version>.msi. The staged files are in dist\stage.

.EXAMPLE
  pwsh packaging\build-release.ps1 -Version 1.0.0
#>
param(
    [string]$Version = "1.0.0",
    [string]$BuildPython = "python",
    [string]$Out = (Join-Path $PSScriptRoot "..\dist")
)
$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true

$PyVersion = "3.12.10"
$PyMd5 = "fe8ef205f2e9c3ba44d0cf9954e1abd3"   # from python.org's release page
# SHA-256 of the same file, pinned when 1.2.0 was built (python.org publishes only MD5/Sigstore).
$PySha256 = "4ACBED6DD1C744B0376E3B1CF57CE906F9DC9E95E68824584C8099A63025A3C3"
$Osmium = "4.3.2"
$Requirements = Join-Path $PSScriptRoot "engine-requirements.txt"   # versions + SHA-256 of every wheel

$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
New-Item -ItemType Directory -Force $Out | Out-Null
$Out = (Resolve-Path $Out).Path
$stage = Join-Path $Out "stage"
$cache = Join-Path $Out "cache"
if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory -Force $stage, $cache | Out-Null

Write-Host "== App ($Version)"
dotnet publish (Join-Path $repo "app\src\OpenLion\OpenLion.csproj") -c Release -r win-x64 --self-contained `
    -p:Version=$Version -p:DebugType=none -o $stage -nologo -v q

Write-Host "== Python $PyVersion (embeddable)"
$zip = Join-Path $cache "python-$PyVersion-embed-amd64.zip"
if (-not (Test-Path $zip)) {
    Invoke-WebRequest "https://www.python.org/ftp/python/$PyVersion/python-$PyVersion-embed-amd64.zip" -OutFile $zip
}
if ((Get-FileHash $zip -Algorithm MD5).Hash -ne $PyMd5) { throw "Python download has the wrong MD5: $zip" }
if ((Get-FileHash $zip -Algorithm SHA256).Hash -ne $PySha256) { throw "Python download has the wrong SHA-256: $zip" }
$py = Join-Path $stage "runtime\python"
Expand-Archive $zip -DestinationPath $py
# The ._pth file fixes sys.path (PYTHONPATH and the current folder are ignored): stdlib,
# site-packages, and the engine folder two levels up.
Set-Content (Join-Path $py "python312._pth") -Encoding ascii -Value @(
    "python312.zip", ".", "Lib\site-packages", "..\..\engine", "import site")

Write-Host "== osmium $Osmium"
$site = Join-Path $py "Lib\site-packages"
& $BuildPython -m pip install --disable-pip-version-check --quiet --no-compile --target $site `
    --only-binary=:all: --platform win_amd64 --python-version 3.12 --implementation cp `
    --require-hashes -r $Requirements
# pip's command-line launchers point at the build machine's Python; the app doesn't use them.
Remove-Item -Recurse -Force (Join-Path $site "bin") -ErrorAction SilentlyContinue

Write-Host "== Engine"
$engine = Join-Path $stage "engine"
New-Item -ItemType Directory -Force "$engine\openlion", "$engine\tests", "$engine\docs\reference" | Out-Null
Copy-Item (Join-Path $repo "openlion\*.py") "$engine\openlion"
Copy-Item (Join-Path $repo "tests\*.py") "$engine\tests"
Copy-Item (Join-Path $repo "docs\reference\manifest_sha256.tsv") "$engine\docs\reference"
Copy-Item (Join-Path $repo "LICENSE") (Join-Path $stage "LICENSE.txt")

Write-Host "== Smoke test of the bundled engine"
Push-Location $env:TEMP
try {
    & (Join-Path $py "python.exe") -X utf8 -c "import osmium, sqlite3, zlib, openlion.make_card, openlion.compat; import importlib.metadata as m; print('engine ok, osmium', m.version('osmium'), osmium.__file__)"
} finally { Pop-Location }

Write-Host "== MSI"
$msi = Join-Path $Out "OpenLion-$Version.msi"
Push-Location $PSScriptRoot
try {
    wix build OpenLion.wxs -arch x64 -ext WixToolset.UI.wixext -d "Version=$Version" -bindpath "stage=$stage" -o $msi
} finally { Pop-Location }
Remove-Item (Join-Path $Out "OpenLion-$Version.wixpdb") -ErrorAction SilentlyContinue

$hash = (Get-FileHash $msi -Algorithm SHA256).Hash.ToLower()
Set-Content "$msi.sha256" -Encoding ascii -Value "$hash  OpenLion-$Version.msi"
Write-Host ("Built {0} ({1:N0} MB), SHA-256 {2}" -f $msi, ((Get-Item $msi).Length / 1MB), $hash)
