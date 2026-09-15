#!/bin/bash
# packaging/appimage/build.sh — builds the Linux standalone AppImage.
#
# Run from the repo root: packaging/appimage/build.sh
# Output: dist/MSIAnalyzer-x86_64.AppImage
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${REPO_ROOT}"

TOOLS_DIR="packaging/.tools"
APPIMAGETOOL="${TOOLS_DIR}/appimagetool-x86_64.AppImage"
APPDIR="packaging/appimage/AppDir"

mkdir -p "${TOOLS_DIR}"
if [ ! -x "${APPIMAGETOOL}" ]; then
    echo "Fetching appimagetool..."
    curl -sL -o "${APPIMAGETOOL}" \
        https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage
    chmod +x "${APPIMAGETOOL}"
fi

# Resources embedded in resources_rc.py can drift from the .qml sources
# if someone forgets to recompile after editing them (see
# tests/gui/conftest.py's compile_qml_resources fixture, which does the
# same thing for tests) — regenerate rather than trust whatever's
# currently committed.
echo "Recompiling QML resources..."
pdm run pyside6-rcc src/msianalyzer/gui/qml/resources.qrc -o src/msianalyzer/gui/resources_rc.py

echo "Running PyInstaller..."
pdm run pyinstaller packaging/pyinstaller/msianalyzer-gui.spec --noconfirm

echo "Assembling AppDir..."
rm -rf "${APPDIR}"
mkdir -p "${APPDIR}/usr/opt"
cp -r dist/msianalyzer-gui "${APPDIR}/usr/opt/msianalyzer-gui"
install -m 755 packaging/appimage/AppRun "${APPDIR}/AppRun"
install -m 644 packaging/appimage/msianalyzer.desktop "${APPDIR}/msianalyzer.desktop"
install -m 644 packaging/appimage/msianalyzer-placeholder-icon.png "${APPDIR}/msianalyzer.png"

echo "Running appimagetool..."
# --appimage-extract-and-run: appimagetool is itself an AppImage, which
# needs FUSE to mount — not guaranteed to be available (e.g. containers,
# some sandboxes). This extracts it to a temp dir and runs it directly
# instead, sidestepping FUSE entirely.
ARCH=x86_64 "${APPIMAGETOOL}" --appimage-extract-and-run "${APPDIR}" dist/MSIAnalyzer-x86_64.AppImage

echo "Built dist/MSIAnalyzer-x86_64.AppImage"
