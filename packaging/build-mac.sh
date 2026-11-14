#!/bin/bash
# Builds OpenLion for macOS (Apple silicon): OpenLion.app with its own Python and osmium, the
# engine, and a disk image to install it from. The macOS counterpart of build-release.ps1.
#
# Needs: macOS on Apple silicon, the .NET 10 SDK (dotnet on PATH or in ~/.dotnet) and the Xcode
# command line tools (for iconutil and codesign). Nothing else: Python comes from the pinned
# python-build-standalone download below.
#
# Output: dist/OpenLion-<version>-mac.dmg (+ .sha256), and dist/mac/OpenLion.app.
#
#   packaging/build-mac.sh 1.6.0
set -euo pipefail

VERSION="${1:-1.6.0}"
PY_VERSION="3.12.16"
PY_BUILD="20261110"
PY_FILE="cpython-${PY_VERSION}+${PY_BUILD}-aarch64-apple-darwin-install_only.tar.gz"
PY_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PY_BUILD}/cpython-${PY_VERSION}%2B${PY_BUILD}-aarch64-apple-darwin-install_only.tar.gz"
# From the release's SHA256SUMS, pinned when this script was written.
PY_SHA256="e956e60bc9465d577ef8062059aa05cd3a2665b4b8678a2ed24e432368330b8f"
BUNDLE_ID="io.github.hyprninja.openlion"

REPO="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$REPO/dist"
CACHE="$OUT/cache"
STAGE="$OUT/mac"
APP="$STAGE/OpenLion.app"
REQUIREMENTS="$REPO/packaging/engine-requirements-mac.txt"   # versions + SHA-256 of every wheel

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
    echo "build-mac.sh builds the Apple silicon app and must run on an Apple silicon Mac." >&2
    exit 1
fi
command -v dotnet >/dev/null || export PATH="$HOME/.dotnet:$PATH"
export DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1

rm -rf "$STAGE"
mkdir -p "$CACHE" "$APP/Contents/MacOS" "$APP/Contents/Resources"

echo "== App ($VERSION)"
dotnet publish "$REPO/app/src/OpenLion.Mac/OpenLion.Mac.csproj" -c Release -r osx-arm64 --self-contained \
    -p:Version="$VERSION" -p:DebugType=none -o "$APP/Contents/MacOS" -nologo -v q

echo "== Python $PY_VERSION ($PY_BUILD, python-build-standalone)"
if [[ ! -f "$CACHE/$PY_FILE" ]]; then
    curl -fL --retry 3 -o "$CACHE/$PY_FILE.part" "$PY_URL"
    mv "$CACHE/$PY_FILE.part" "$CACHE/$PY_FILE"
fi
echo "$PY_SHA256  $CACHE/$PY_FILE" | shasum -a 256 -c - >/dev/null \
    || { echo "Python download has the wrong SHA-256: $CACHE/$PY_FILE" >&2; exit 1; }
RUNTIME="$APP/Contents/Resources/runtime"
mkdir -p "$RUNTIME"
tar -xzf "$CACHE/$PY_FILE" -C "$RUNTIME"          # → runtime/python/bin/python3
PY="$RUNTIME/python/bin/python3"

echo "== Engine packages (pinned wheels)"
PIP_CONFIG_FILE=/dev/null PYTHONNOUSERSITE=1 "$PY" -m pip install --disable-pip-version-check --no-cache-dir \
    --quiet --no-compile --only-binary=:all: --require-hashes -r "$REQUIREMENTS"
# Command-line launchers point at this build folder; the app doesn't use them.
find "$RUNTIME/python/bin" \( -type f -o -type l \) ! -name 'python3*' -delete
find "$RUNTIME" -name '__pycache__' -type d -prune -exec rm -rf {} +

echo "== Engine"
ENGINE="$APP/Contents/Resources/engine"
mkdir -p "$ENGINE/openlion" "$ENGINE/tests" "$ENGINE/docs/reference"
cp "$REPO"/openlion/*.py "$ENGINE/openlion/"
cp "$REPO"/tests/*.py "$ENGINE/tests/"
cp "$REPO/docs/reference/manifest_sha256.tsv" "$ENGINE/docs/reference/"
cp "$REPO/LICENSE" "$APP/Contents/Resources/LICENSE.txt"

echo "== Icon"
ICONSET="$OUT/cache/OpenLion.iconset"
rm -rf "$ICONSET" && mkdir -p "$ICONSET"
LOGO="$REPO/app/src/OpenLion/Assets/logo.png"
for s in 16 32 128 256 512; do
    sips -z $s $s "$LOGO" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
    sips -z $((s * 2)) $((s * 2)) "$LOGO" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/OpenLion.icns"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>OpenLion</string>
    <key>CFBundleDisplayName</key><string>OpenLion</string>
    <key>CFBundleIdentifier</key><string>$BUNDLE_ID</string>
    <key>CFBundleExecutable</key><string>OpenLion</string>
    <key>CFBundleIconFile</key><string>OpenLion</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>$VERSION</string>
    <key>CFBundleVersion</key><string>$VERSION</string>
    <key>LSMinimumSystemVersion</key><string>12.0</string>
    <key>LSApplicationCategoryType</key><string>public.app-category.utilities</string>
    <key>NSHighResolutionCapable</key><true/>
    <key>NSHumanReadableCopyright</key><string>OpenLion, MIT License. Map data © OpenStreetMap contributors (ODbL).</string>
</dict>
</plist>
PLIST

echo "== Smoke test of the bundled engine"
(cd "$TMPDIR" && PYTHONPATH="$ENGINE" PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 "$PY" -X utf8 -c \
    "import osmium, sqlite3, zlib, openlion.make_card, openlion.compat, openlion.memory as m; import importlib.metadata as md; print('engine ok, osmium', md.version('osmium'), '-', m.gb(m.total()), 'installed')")

echo "== Signing (ad hoc: runs on this Mac; not notarized)"
xattr -cr "$APP"
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP"

echo "== Disk image"
DMG="$OUT/OpenLion-$VERSION-mac.dmg"
DMGROOT="$OUT/cache/dmgroot"
rm -rf "$DMGROOT" "$DMG" && mkdir -p "$DMGROOT"
cp -R "$APP" "$DMGROOT/"
ln -s /Applications "$DMGROOT/Applications"
hdiutil create -quiet -volname "OpenLion $VERSION" -srcfolder "$DMGROOT" -fs HFS+ -format UDZO "$DMG"
rm -rf "$DMGROOT"
HASH="$(shasum -a 256 "$DMG" | cut -d' ' -f1)"
echo "$HASH  $(basename "$DMG")" > "$DMG.sha256"
echo "Built $DMG ($(du -m "$DMG" | cut -f1) MB), SHA-256 $HASH"
