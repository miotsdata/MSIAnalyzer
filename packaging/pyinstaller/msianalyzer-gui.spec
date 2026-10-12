# packaging/pyinstaller/msianalyzer-gui.spec
#
# Build: pdm run pyinstaller packaging/pyinstaller/msianalyzer-gui.spec
# (run from the repo root — paths below are relative to it)
#
# onedir, not onefile: QtWebEngine spawns its own helper process
# (QtWebEngineProcess) at a path relative to the app bundle at runtime;
# onefile's self-extracting-to-a-temp-dir model is a worse fit for that
# than a plain directory the exe already lives beside.
import re
import sys
from pathlib import Path

from PyInstaller.depend import bindepend

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
    # Help > User Guide (router.openUserGuide). The build scripts run
    # `mkdocs build` first; a bare spec run without it just ships no guide.
    datas=[(str(REPO_ROOT / "site"), "site")] if (REPO_ROOT / "site").is_dir() else [],
    # Modules loaded through core.utils.lazy_import.lazy_module("..."): a
    # name in a string is invisible to PyInstaller's import scan.
    hiddenimports=["anndata"],
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
# Qt shared libraries left orphaned once the QML plugins above are gone
# (Qt6Charts, Qt6Quick3D, the FFmpeg libs behind Multimedia, ...). These
# are computed, not listed: starting from every binary we keep, follow
# real link-time imports (PyInstaller's own reader: ldd on Linux, the PE
# import table on Windows) and drop only the Qt/FFmpeg libs nothing
# reaches. A hand-written exclusion list once dropped libQt6Positioning,
# which WebEngineCore links directly, and the app crashed at startup.
# Only Qt6*/FFmpeg names are ever pruned: Qt loads a few other DLLs at
# runtime without linking them (opengl32sw.dll, d3dcompiler_47.dll on
# Windows), and an import walk can't see those.
PRUNABLE_LIB = re.compile(
    r"^(lib)?(qt6[\w-]*|avcodec|avformat|avutil|swresample|swscale)([.-][\w.-]*)?\.(so[\d.]*|dll)$"
)


def _unreachable_qt_libs(binaries) -> set[str]:
    candidates = {
        Path(dest).name.lower(): src
        for dest, src, _ in binaries
        if PRUNABLE_LIB.match(Path(dest).name.lower())
    }
    todo = [src for dest, src, _ in binaries if Path(dest).name.lower() not in candidates]
    seen_src, reached = set(), set()
    while todo:
        src = todo.pop()
        if src in seen_src:
            continue
        seen_src.add(src)
        for name, _path in bindepend.get_imports(src):
            key = Path(name).name.lower()
            if key in candidates and key not in reached:
                reached.add(key)
                todo.append(candidates[key])
    return set(candidates) - reached


def _is_unwanted(path: str) -> bool:
    lowered = path.replace("\\", "/").lower()
    # Linux wheels nest Qt's tree under PySide6/Qt/, Windows wheels don't.
    lowered = lowered.replace("pyside6/qt/", "pyside6/")

    if "pyside6/translations/qtwebengine_locales/" in lowered:
        return "en-us" not in lowered
    if "pyside6/translations/" in lowered:
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

    if "pyside6/qml/" in lowered:
        module = lowered.split("pyside6/qml/", 1)[1].split("/", 1)[0]
        if module not in KEEP_TOP_LEVEL_QML_DIRS:
            return True
        if any(frag in lowered for frag in DROP_QTQUICK_PATH_FRAGMENTS):
            return True

    return False


a.datas = [d for d in a.datas if not (_is_unwanted(d[0]) or _is_unwanted(d[1]))]
a.binaries = [b for b in a.binaries if not (_is_unwanted(b[0]) or _is_unwanted(b[1]))]
_unreachable = _unreachable_qt_libs(a.binaries)
print(f"msianalyzer-gui.spec: pruning {len(_unreachable)} unreachable Qt libs: {sorted(_unreachable)}")
a.binaries = [b for b in a.binaries if Path(b[0]).name.lower() not in _unreachable]

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
    # PyInstaller converts the .png to the .ico Windows needs (via Pillow);
    # ignored on Linux, where the AppImage carries its own icon.
    icon=str(REPO_ROOT / "packaging" / "appimage" / "msianalyzer-placeholder-icon.png"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="msianalyzer-gui",
)
