#!/usr/bin/env bash
# build_linux.sh — Build ZebraFET as a portable AppImage for Linux
#
# Produces:
#   dist/ZebraFET-x86_64.AppImage   (portable, no installation required)
#
# Requirements:
#   pip install pyinstaller
#   appimagetool: https://github.com/AppImage/AppImageKit/releases
#     wget -O appimagetool https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage
#     chmod +x appimagetool && sudo mv appimagetool /usr/local/bin/

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

APP_NAME="ZebraFET"
DIST_DIR="dist"
BUILD_DIR="build"
APPDIR="${BUILD_DIR}/AppDir"

# ── 1. Build with PyInstaller ────────────────────────────────────────────────
echo "==> Building ${APP_NAME} with PyInstaller..."
pyinstaller ZebraFET.spec --noconfirm --distpath "$DIST_DIR" --workpath "$BUILD_DIR"
echo "    Done: ${DIST_DIR}/${APP_NAME}/"

# ── 2. Assemble AppDir structure ─────────────────────────────────────────────
echo "==> Assembling AppDir..."
rm -rf "$APPDIR"
mkdir -p "${APPDIR}/usr/bin"

# Copy all PyInstaller output into AppDir
cp -r "${DIST_DIR}/${APP_NAME}/." "${APPDIR}/usr/bin/"

# App icon (AppImage spec requires a PNG at the root of AppDir)
cp "resources/icons/fishapp_icon.png" "${APPDIR}/${APP_NAME}.png"

# .desktop file (required by AppImage spec)
cat > "${APPDIR}/${APP_NAME}.desktop" << DESKTOP
[Desktop Entry]
Type=Application
Name=ZebraFET
GenericName=Fish Embryo Toxicity Test Assistant
Comment=Standardized zebrafish embryo toxicity test assistant (OECD TG 236)
Exec=ZebraFET
Icon=ZebraFET
Categories=Science;Education;
Keywords=toxicology;FET;OECD;zebrafish;
StartupNotify=true
DESKTOP

# AppRun wrapper (entry point used by AppImage runtime)
cat > "${APPDIR}/AppRun" << 'APPRUN'
#!/usr/bin/env bash
SELF_DIR="$(dirname "$(readlink -f "$0")")"
exec "${SELF_DIR}/usr/bin/ZebraFET" "$@"
APPRUN
chmod +x "${APPDIR}/AppRun"

echo "    AppDir assembled at: ${APPDIR}"

# ── 3. Build AppImage ────────────────────────────────────────────────────────
if command -v appimagetool &> /dev/null; then
    echo "==> Building AppImage..."
    ARCH=x86_64 appimagetool "$APPDIR" "${DIST_DIR}/${APP_NAME}-x86_64.AppImage"
    chmod +x "${DIST_DIR}/${APP_NAME}-x86_64.AppImage"
    echo "    Done: ${DIST_DIR}/${APP_NAME}-x86_64.AppImage"
else
    echo ""
    echo "WARNING: appimagetool not found. AppDir is ready at ${APPDIR}."
    echo "To build the AppImage manually, install appimagetool:"
    echo "  wget -O /usr/local/bin/appimagetool \\"
    echo "    https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage"
    echo "  chmod +x /usr/local/bin/appimagetool"
    echo "Then run:"
    echo "  ARCH=x86_64 appimagetool ${APPDIR} ${DIST_DIR}/${APP_NAME}-x86_64.AppImage"
    exit 1
fi

echo ""
echo "Build complete. Run with:"
echo "  chmod +x ${DIST_DIR}/${APP_NAME}-x86_64.AppImage"
echo "  ./${DIST_DIR}/${APP_NAME}-x86_64.AppImage"
