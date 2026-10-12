# 52 — Windows build, and a fast-starting GUI

**Status:** Accepted

## Context

The app needs a Windows 10 `.exe`. ADR 22 deferred this to a local Windows
VM, and ADR 23 built the Linux AppImage from a PyInstaller spec whose
trimming was Linux-only.

Opening the app also had to be fast. Importing `msianalyzer.gui.main`
alone took **3.4 s and 4,357 modules** on Linux, before any window
appeared. Windows is typically 2–3× slower at this (slower file access,
plus Defender scanning each file). Most of the cost came from libraries
the GUI doesn't need when it opens:

| Import chain | Cost |
|---|---|
| `core_bridge → run → spatial.moran → squidpy` (scanpy, spatialdata, dask, numba, ...) | 1.4 s |
| `anndata` (13 modules, at import time) | 0.55 s |
| `scipy.stats` / `scipy.signal` / `scipy.optimize` | ~0.6 s |

Python 3.15's `lazy import` (PEP 810) was considered and rejected for
now. The project is pinned to 3.13, and moving up means waiting for
3.15 wheels of PySide6, numba, lightgbm and PyInstaller. A global
lazy-imports switch is also unsafe here, because some imports work
through side effects (`resources_rc` registers QML resources when
imported).

## Decision

**Startup: defer the heavy imports.** This cuts startup from 3.4 s to
**~1.0 s** (1,438 modules). The floor (Qt + numpy + pandas) is ~0.4 s.

- `squidpy` and the scipy submodules: import inside the functions that
  use them (1–3 call sites each). PyInstaller still sees these imports.
- `anndata`: it has ~77 call sites across 12 modules, so the existing
  `import anndata as ad` lines became `ad = lazy_module("anndata")` instead
  (`core/utils/lazy_import.py`, stdlib `importlib.util.LazyLoader`). The
  real import runs on the first attribute access. This needs unevaluated
  annotations (`from __future__ import annotations`, added where missing)
  and a `hiddenimports` entry in the spec.
- `export.py`'s `import matplotlib.pyplot` was dead: `plt.close()` on a
  standalone `Figure` that pyplot never tracked. Both the import and the
  call were deleted.
- A subprocess test (`tests/gui/test_main.py`) fails if any of these is
  imported at GUI startup again.
- Not done: `pyteomics` (0.2 s, via `config → target_list`). Its
  module-level adduct constants are deliberately derived via pyteomics
  ("auditable"), and replacing them would undo that choice.

**Windows-safe runtime.**

- `multiprocessing.freeze_support()` is the first call in `gui.main.main()`.
  Windows starts each `ProcessPoolExecutor` worker by launching the
  `.exe` again; without this call, every Run would open more GUIs.
  Verified by running the whole test suite with the start method set to
  `spawn` (how Windows works).
- `sys.stdout`/`sys.stderr` are redirected to `os.devnull` when `None`
  (a no-console Windows exe). Otherwise any library that prints crashes.
- The debug log and freeze dumps moved from `src/msianalyzer/log` (inside
  the package, unwritable once installed) to `platformdirs.user_log_dir`
  (`%LOCALAPPDATA%\MSIAnalyzer\Logs`, `~/.local/state/MSIAnalyzer/log`).
- The QML import path comes from `QLibraryInfo` instead of a hardcoded
  `PySide6/Qt/qml`, which doesn't exist on Windows wheels.
- Help > User Guide reads `site/` from `sys._MEIPASS` when frozen. Both
  build scripts run `mkdocs build`, and the spec bundles the result.

**One spec for both platforms.** The trimming is now layout-agnostic: the
Linux wheel nests Qt under `PySide6/Qt/`, the Windows wheel doesn't. The
hand-computed `UNREACHABLE_QT_LIBS` list (Linux `.so` names, ADR 23) is
replaced by a closure computed at build time. Starting from every kept
binary, it follows real link-time imports (`PyInstaller.depend.bindepend`:
ldd on Linux, PE import tables on Windows). It drops only `Qt6*`/FFmpeg
libraries that nothing reaches. DLLs that Qt loads dynamically without
linking (`opengl32sw.dll`, `d3dcompiler_47.dll`) don't match that pattern,
so they're never pruned.

**Packaging: onedir + Inno Setup**, not onefile. Onefile unpacks the whole
~700 MB bundle to a temp folder on every launch, which defeats the
fast-open goal. `packaging/windows/build.ps1` runs mkdocs, rcc,
PyInstaller and then ISCC on `msianalyzer.iss`. The result is a per-user
installer (`%LOCALAPPDATA%\Programs\MSIAnalyzer`, no admin rights, no UAC
prompt).

**Builds run in GitHub Actions and ship as Release assets**
(`.github/workflows/windows-build.yml`), not as Actions artifacts. On
the Free plan, artifacts share 500 MB of storage, and the installer
alone is about that big; release assets have no total quota, only
< 2 GiB per file. A manual run replaces one `windows-dev` pre-release;
a `v*` tag makes a real release. The workflow smoke-tests the frozen
app first, checking that `Main.qml` loads (the watchdog's
`freeze_*.txt` appears only after that). A Windows VM is then only
needed to test the GUI, not to build.

## Consequences

- Any heavy library added later should be imported lazily. The startup
  test catches the known heavy ones only.
- The first launch on Windows stays slower: Defender scans every bundled
  file, and Qt compiles and caches the QML.
- The exe is unsigned, so SmartScreen warns on first run. That's
  acceptable for personal use; a code-signing certificate fixes it later.
- Builds must happen on Windows (no cross-compiling); see
  `docs/developer/distribution.md`.
