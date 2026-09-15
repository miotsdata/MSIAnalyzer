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

import PySide6

block_cipher = None

REPO_ROOT = Path(SPECPATH).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src"
PYSIDE6_DIR = Path(PySide6.__file__).parent

sys.path.insert(0, str(SRC_ROOT))

# QML modules loaded via `engine.addImportPath(PySide6/Qt/qml)` at
# runtime (QtQuick.Controls, QtQuick.Dialogs, ...) — PyInstaller's static
# analysis can't see these (QML imports, not Python imports), so the
# whole PySide6 QML plugin tree ships alongside the app. Broad/unfiltered
# for now, to get a correct build first — trimming to only the modules
# actually used is a later size pass (see the packaging/PyInstaller
# discussion), not a correctness concern.
datas = [
    (str(PYSIDE6_DIR / "Qt" / "qml"), "PySide6/Qt/qml"),
]

a = Analysis(
    [str(SRC_ROOT / "msianalyzer" / "gui" / "main.py")],
    pathex=[str(SRC_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    cipher=block_cipher,
)

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
