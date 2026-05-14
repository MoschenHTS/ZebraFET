#!/usr/bin/env bash
# build_macos.sh — Build ZebraFET for macOS
#
# Produces two distribution artifacts in dist/:
#   - ZebraFET.app          (raw app bundle, via PyInstaller)
#   - ZebraFET-macOS.pkg    (macOS installer wizard, via pkgbuild + productbuild)
#   - ZebraFET-macOS.dmg    (optional DMG, requires: brew install create-dmg)
#
# Requirements:
#   pip install pyinstaller
#   Xcode Command Line Tools (xcode-select --install)
#   Optional: brew install create-dmg

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

APP_NAME="ZebraFET"
VERSION="2.1.1"  # Keep in sync with src/core/constants.py APP_VERSION
BUNDLE_ID="com.zebralab.zebrafet"
DIST_DIR="dist"
BUILD_DIR="build"
PKG_ROOT="build/pkg_root"
PKG_SCRIPTS="build/pkg_scripts"

# ── 1. Build .app bundle with PyInstaller ────────────────────────────────────
echo "==> Building ${APP_NAME}.app with PyInstaller..."
pyinstaller ZebraFET.spec --noconfirm --distpath "$DIST_DIR" --workpath "$BUILD_DIR"
echo "    Done: ${DIST_DIR}/${APP_NAME}.app"

# ── 2. Build macOS .pkg installer (proper wizard) ───────────────────────────
echo "==> Building macOS .pkg installer..."

# Create payload directory (pkgbuild expects the .app under /Applications)
rm -rf "$PKG_ROOT"
mkdir -p "${PKG_ROOT}/Applications"
cp -R "${DIST_DIR}/${APP_NAME}.app" "${PKG_ROOT}/Applications/"

# Create optional postinstall script (opens the app after install)
mkdir -p "$PKG_SCRIPTS"
cat > "${PKG_SCRIPTS}/postinstall" << 'POSTINSTALL'
#!/usr/bin/env bash
# Open ZebraFET after installation completes
open "/Applications/ZebraFET.app" &
exit 0
POSTINSTALL
chmod +x "${PKG_SCRIPTS}/postinstall"

# Build component package
pkgbuild \
    --root "$PKG_ROOT" \
    --scripts "$PKG_SCRIPTS" \
    --identifier "$BUNDLE_ID" \
    --version "$VERSION" \
    --install-location "/" \
    "${BUILD_DIR}/${APP_NAME}-component.pkg"

# Create welcome HTML for the installer wizard
RESOURCES_DIR="${BUILD_DIR}/pkg_resources"
mkdir -p "$RESOURCES_DIR"

cat > "${RESOURCES_DIR}/welcome.html" << 'HTML'
<!DOCTYPE html>
<html>
<body style="font-family: sans-serif; font-size: 13px;">
<h2>ZebraFET — Fish Embryo Toxicity Test Assistant</h2>
<p>Welcome to the ZebraFET installer.</p>
<p>ZebraFET helps you record, manage, and analyse Fish Embryo Acute Toxicity (FET)
tests following the OECD Test Guideline 236 protocol.</p>
<p>This installer will place ZebraFET.app in your <b>/Applications</b> folder.</p>
</body>
</html>
HTML

# Distribution XML (controls wizard pages shown by macOS Installer)
cat > "${BUILD_DIR}/Distribution.xml" << XML
<?xml version="1.0" encoding="utf-8"?>
<installer-gui-script minSpecVersion="2">
    <title>ZebraFET ${VERSION}</title>
    <welcome file="welcome.html" mime-type="text/html"/>
    <license file="GPL-3.0.txt" mime-type="text/plain"/>
    <options customize="never" require-scripts="false" rootVolumeOnly="true"/>
    <choices-outline>
        <line choice="default"/>
    </choices-outline>
    <choice id="default">
        <pkg-ref id="${BUNDLE_ID}"/>
    </choice>
    <pkg-ref id="${BUNDLE_ID}" version="${VERSION}" onConclusion="none">
        ${APP_NAME}-component.pkg
    </pkg-ref>
</installer-gui-script>
XML

# Copy license file into pkg resources
cp "resources/docs/Licenses/GPL-3.0.txt" "$RESOURCES_DIR/"

# Build final .pkg with wizard
productbuild \
    --distribution "${BUILD_DIR}/Distribution.xml" \
    --resources "$RESOURCES_DIR" \
    --package-path "$BUILD_DIR" \
    "${DIST_DIR}/${APP_NAME}-macOS.pkg"

echo "    Done: ${DIST_DIR}/${APP_NAME}-macOS.pkg"

# ── 3. Optional: Build .dmg (simpler drag-to-Applications) ──────────────────
if command -v create-dmg &> /dev/null; then
    echo "==> Building .dmg with create-dmg..."
    rm -f "${DIST_DIR}/${APP_NAME}-macOS.dmg"
    create-dmg \
        --volname "${APP_NAME} Installer" \
        --volicon "resources/icons/fishapp_icon.icns" \
        --window-pos 200 120 \
        --window-size 600 300 \
        --icon-size 100 \
        --icon "${APP_NAME}.app" 175 130 \
        --hide-extension "${APP_NAME}.app" \
        --app-drop-link 425 130 \
        "${DIST_DIR}/${APP_NAME}-macOS.dmg" \
        "${DIST_DIR}/${APP_NAME}.app"
    echo "    Done: ${DIST_DIR}/${APP_NAME}-macOS.dmg"
else
    echo "    (create-dmg not found — skipping .dmg. Install with: brew install create-dmg)"
fi

echo ""
echo "Build complete. Artifacts in ${DIST_DIR}/:"
ls -lh "${DIST_DIR}/" | grep -E "(\.app|\.pkg|\.dmg)"
