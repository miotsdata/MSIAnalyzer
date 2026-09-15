# packaging/pyinstaller/msianalyzer-gui.spec
#
# Build: pdm run pyinstaller packaging/pyinstaller/msianalyzer-gui.spec
# (run from the repo root — paths below are relative to it)
#
# onedir, not onefile: QtWebEngine spawns its own helper process
# (QtWebEngineProcess) at a path relative to the app bundle at runtime;
# onefile's self-extracting-to-a-temp-dir model is a worse fit for that
# than a plain directory the exe already lives beside.
import sys
from pathlib import Path

block_cipher = None

REPO_ROOT = Path(SPECPATH).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src"

sys.path.insert(0, str(SRC_ROOT))

# Dev/test/doc tooling that has shown up in the built bundle despite
# nothing under src/msianalyzer importing it directly (traced as far as
# confirming that — not worth blocking the build on pinning the exact
# transitive path further). Excluding by name is robust regardless of
# how something reaches them.
excludes = [
    "jedi",
    "parso",
    "hypothesis",
    "IPython",
    "jupyter",
    "jupyter_client",
    "jupyter_core",
    "ipykernel",
    "ipywidgets",
    "notebook",
    "nbconvert",
    "nbformat",
    "pytest",
    "_pytest",
    "pytest_mock",
    "pytestqt",
    "mkdocs",
    "mkdocstrings",
    "pylint",
    "astroid",
    "PyInstaller",
]

a = Analysis(
    [str(SRC_ROOT / "msianalyzer" / "gui" / "main.py")],
    pathex=[str(SRC_ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    cipher=block_cipher,
)


# --- Trim data/binary files that PySide6's own PyInstaller hooks add
# unconditionally, bypassing whatever this spec's `datas`/`binaries`
# says (confirmed by reading PyInstaller.utils.hooks.qt's
# collect_qtqml_files(): it does its own `rglob('**/qmldir')` over the
# real PySide6 install, independent of this file). Filtering the
# resulting a.datas/a.binaries after Analysis() is the only point that
# actually has an effect.
#
# QML modules: whitelist only the modules this app's own .qml files
# import (`grep -rhoE "^import [A-Za-z0-9_.]+" src/msianalyzer/gui/qml`)
# plus the couple of always-needed core ones (Qt, QtCore, QtQml). The
# unfiltered tree also pulls in every *unused* module's own compiled qml
# plugin .so, which is what actually drags in most of the corresponding
# Qt/lib/libQt6<Module>*.so too — trimming the qml/ tree is most of the
# size win here, not just the qml/ directory's own weight.
KEEP_TOP_LEVEL_QML_DIRS = {"qt", "qtcore", "qtqml", "qtquick", "qtwebengine", "qtwebchannel"}
# Within QtQuick: unused Controls styles (only Fusion is selected, via
# QQuickStyle.setStyle in gui/main.py — Basic and impl/ stay, since other
# styles build on those internally) and unused whole features (PDF
# viewer, on-screen keyboard, Qt Design Studio preview metadata).
DROP_QTQUICK_PATH_FRAGMENTS = [
    "qtquick/pdf",
    "qtquick/virtualkeyboard",
    "controls/material",
    "controls/universal",
    "controls/imagine",
    "controls/fluentwinui3",
    "controls/designer",
]
# Qt/lib/*.so residuals: once their only referrer (an excluded QML
# plugin) is gone they're just orphaned dead weight, but they don't live
# under a qml/<module> path themselves, so the filter above can't catch
# them. A naive per-module substring guess here is dangerous — e.g.
# "positioning" would wrongly drop libQt6Positioning.so.6, which
# WebEngineCore actually needs directly (geolocation API) even though
# the *QML* PositioningQuick wrapper is genuinely unused (confirmed: the
# app crashed on startup with exactly that missing-library error until
# this list was corrected). This exact set was computed by walking the
# real ELF NEEDED closure from every library this app's roots (Core/Gui/
# Widgets/Quick/Qml family, WebEngine[Quick|Core], Network, the kept
# QuickControls2/QuickDialogs2/Labs* variants, ...) actually declare a
# direct or transitive dependency on, via `readelf -d` — not a guess.
# Regenerate rather than hand-edit if PySide6 is upgraded and this
# starts missing a library again.
UNREACHABLE_QT_LIBS = {
    "libqt63danimation.so.6", "libqt63dcore.so.6", "libqt63dextras.so.6",
    "libqt63dinput.so.6", "libqt63dlogic.so.6", "libqt63dquick.so.6",
    "libqt63dquickanimation.so.6", "libqt63dquickextras.so.6",
    "libqt63dquickinput.so.6", "libqt63dquicklogic.so.6",
    "libqt63dquickrender.so.6", "libqt63dquickscene2d.so.6",
    "libqt63dquickscene3d.so.6", "libqt63drender.so.6",
    "libqt6bluetooth.so.6", "libqt6canvaspainter.so.6",
    "libqt6charts.so.6", "libqt6chartsqml.so.6",
    "libqt6datavisualization.so.6", "libqt6datavisualizationqml.so.6",
    "libqt6designer.so.6", "libqt6designercomponents.so.6",
    "libqt6ffmpegstub-crypto.so.3", "libqt6ffmpegstub-ssl.so.3",
    "libqt6ffmpegstub-va-drm.so.2", "libqt6ffmpegstub-va-x11.so.2",
    "libqt6ffmpegstub-va.so.2", "libqt6graphs.so.6",
    "libqt6graphswidgets.so.6", "libqt6help.so.6", "libqt6httpserver.so.6",
    "libqt6location.so.6", "libqt6lottie.so.6",
    "libqt6lottievectorimagegenerator.so.6",
    "libqt6lottievectorimagehelpers.so.6", "libqt6multimedia.so.6",
    "libqt6multimediaquick.so.6", "libqt6multimediawidgets.so.6",
    "libqt6networkauth.so.6", "libqt6nfc.so.6", "libqt6pdf.so.6",
    "libqt6pdfquick.so.6", "libqt6pdfwidgets.so.6",
    "libqt6positioningquick.so.6", "libqt6qmlcompiler.so.6",
    "libqt6quick3d.so.6", "libqt6quick3dassetimport.so.6",
    "libqt6quick3dassetutils.so.6", "libqt6quick3deffects.so.6",
    "libqt6quick3dglslparser.so.6", "libqt6quick3dhelpers.so.6",
    "libqt6quick3dhelpersimpl.so.6", "libqt6quick3diblbaker.so.6",
    "libqt6quick3dparticleeffects.so.6", "libqt6quick3dparticles.so.6",
    "libqt6quick3druntimerender.so.6", "libqt6quick3dspatialaudio.so.6",
    "libqt6quick3dutils.so.6", "libqt6quick3dxr.so.6",
    "libqt6quickcontrols2fluentwinui3styleimpl.so.6",
    "libqt6quickcontrols2imagine.so.6",
    "libqt6quickcontrols2imaginestyleimpl.so.6",
    "libqt6quickcontrols2material.so.6",
    "libqt6quickcontrols2materialstyleimpl.so.6",
    "libqt6quickcontrols2universal.so.6",
    "libqt6quickcontrols2universalstyleimpl.so.6", "libqt6quicktest.so.6",
    "libqt6quickwidgets.so.6", "libqt6remoteobjects.so.6",
    "libqt6remoteobjectsqml.so.6", "libqt6scxml.so.6",
    "libqt6scxmlqml.so.6", "libqt6sensors.so.6", "libqt6sensorsquick.so.6",
    "libqt6serialbus.so.6", "libqt6serialport.so.6",
    "libqt6spatialaudio.so.6", "libqt6statemachine.so.6",
    "libqt6statemachineqml.so.6", "libqt6svgwidgets.so.6",
    "libqt6test.so.6", "libqt6texttospeech.so.6", "libqt6uitools.so.6",
    "libqt6virtualkeyboard.so.6", "libqt6virtualkeyboardqml.so.6",
    "libqt6virtualkeyboardsettings.so.6", "libqt6waylandcompositor.so.6",
    "libqt6waylandeglcompositorhwintegration.so.6",
    "libqt6webenginewidgets.so.6", "libqt6websockets.so.6",
    "libqt6webview.so.6", "libqt6webviewquick.so.6",
    "libqt6wlshellintegration.so.6", "libqt6xml.so.6",
    "libavcodec.so", "libavcodec.so.61", "libavcodec.so.61.19.101",
    "libavformat.so", "libavformat.so.61", "libavformat.so.61.7.103",
    "libavutil.so", "libavutil.so.59", "libavutil.so.59.39.100",
    "libswresample.so", "libswresample.so.5", "libswresample.so.5.3.100",
    "libswscale.so", "libswscale.so.8", "libswscale.so.8.3.100",
}


def _is_unwanted(path: str) -> bool:
    lowered = path.replace("\\", "/").lower()

    if "qt/translations/qtwebengine_locales/" in lowered:
        return "en-us" not in lowered
    if "qt/translations/" in lowered:
        # Nothing in this app calls QTranslator/installTranslator
        # (`grep -rn QTranslator src/msianalyzer` is empty) — none of
        # Qt's own 158 .qm files are ever loaded. QtWebEngine's locale
        # .pak files (handled above) are a separate mechanism Chromium
        # loads internally regardless of the app's own QTranslator use.
        return True
    if "qtwebengine_devtools_resources.pak" in lowered:
        # Backs the Chromium DevTools inspector, never opened on this
        # app's WebEngineViews.
        return True

    if "/qt/qml/" in lowered:
        module = lowered.split("/qt/qml/", 1)[1].split("/", 1)[0]
        if module not in KEEP_TOP_LEVEL_QML_DIRS:
            return True
        if any(frag in lowered for frag in DROP_QTQUICK_PATH_FRAGMENTS):
            return True
        return False

    if "/qt/lib/" in lowered:
        basename = lowered.rsplit("/", 1)[-1]
        if basename in UNREACHABLE_QT_LIBS:
            return True

    return False


a.datas = [d for d in a.datas if not (_is_unwanted(d[0]) or _is_unwanted(d[1]))]
a.binaries = [b for b in a.binaries if not (_is_unwanted(b[0]) or _is_unwanted(b[1]))]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="msianalyzer-gui",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="msianalyzer-gui",
)
