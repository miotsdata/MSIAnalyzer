# 23 — PyInstaller/AppImage packaging for the Linux standalone build

**Status:** Accepted

## Context

Following [ADR 22](0022-distribution-and-ci-strategy.md)'s decision to
ship a standalone Linux build, the naive approach (`pyinstaller
gui/main.py`, default settings) produces a working but enormous bundle:
926MB unpacked, 358MB as an AppImage. Most of that is genuinely
unavoidable — a full Qt6 + QtWebEngine (bundled Chromium) + numpy/scipy
stack — but a meaningful fraction is trimmable, and getting there exposed
two non-obvious PyInstaller/PySide6 traps worth recording so the next
version bump doesn't have to rediscover them.

## Decision

**onedir, not onefile.** `QtWebEngineQuick` spawns its own helper process
(`QtWebEngineProcess`) at a path relative to the app bundle at runtime.
onefile's self-extracting-to-a-temp-directory model is a worse fit for that
than a plain directory the exe already lives beside — untested, but not
worth the risk for a save that's cosmetic at best (users see one file
either way, since the AppImage wraps the onedir output regardless).

**Wrapped in an AppImage, not distributed as a raw PyInstaller folder.**
`packaging/appimage/build.sh` assembles an `AppDir` that keeps the
PyInstaller exe and its `_internal/` sibling directory intact (not
reshaped into a conventional `usr/bin`+`usr/lib` layout — the exe looks up
`_internal/` relative to its own location, so keeping that pair intact
avoids fighting PyInstaller's own path resolution) and runs `appimagetool`
with `--appimage-extract-and-run` (appimagetool is itself an AppImage
needing FUSE to mount, which isn't guaranteed available in every build
environment — this flag sidesteps FUSE entirely rather than requiring
`libfuse2` as a build-host dependency).

**Trimmed from 926MB to 742MB raw (358MB → 298MB AppImage)**, in three
independent categories, all invisible to PyInstaller's own static
analysis:

1. **Dev/test/doc tooling** (`jedi`, `hypothesis`, ...) ended up in the
   bundle despite nothing under `src/msianalyzer` importing it directly —
   traced only as far as confirming that, not the exact transitive import
   path (not worth blocking on). Fixed with an explicit `excludes` list by
   package name in `Analysis(...)`, which is robust regardless of
   mechanism.
2. **The unfiltered PySide6 QML plugin tree.** The app's own `.qml` files
   import six modules (`Qt`, `QtCore`, `QtQml`, `QtQuick`, `QtWebEngine`,
   `QtWebChannel` — verified by grepping every `import` statement, not
   assumed); the full PySide6 install ships ~24 top-level QML module
   trees, most unused (Qt3D, Charts, DataVisualization, Multimedia,
   Positioning, RemoteObjects, Sensors, VirtualKeyboard, ...). Each
   unused module's compiled QML plugin also cascades into its own backing
   `Qt/lib/*.so`, so trimming the QML tree is most of the size win here,
   not just the QML directory's own weight.
   **The real trap**: passing a filtered/pre-staged QML directory via the
   spec's own `datas=` parameter has **zero effect**.
   `PyInstaller.utils.hooks.qt`'s `collect_qtqml_files()` — invoked by
   PySide6's own `hook-PySide6.QtQml.py`, which fires because the app
   genuinely uses `QtQml`/`QtQuick` — does its own independent
   `rglob('**/qmldir')` scan of the *real* PySide6 install, completely
   bypassing whatever the spec passes to `datas`. Filtering
   `a.datas`/`a.binaries` **after** `Analysis()` returns is the only point
   that actually works.
3. **Dead weight in Qt's own data files**: nothing in `src/msianalyzer`
   calls `QTranslator`/`installTranslator`, so the 53MB of Qt's own
   translation `.qm` files (158 of them) were never loaded — dropped
   entirely. `qtwebengine_locales/*.pak` is a *separate*, Chromium-internal
   mechanism the browser engine loads regardless of the app's own
   `QTranslator` use — kept, trimmed to `en-US` only. Chromium's DevTools
   inspector resources (`qtwebengine_devtools_resources.pak`, 12MB) are
   dropped — this app never opens DevTools on its `WebEngineView`s.

**Qt/lib exclusions are computed, not guessed.** A first attempt filtered
`Qt/lib/*.so` by substring-matching unused module names — dangerous:
excluding "positioning" (because the unused *QML* `PositioningQuick`
wrapper looked safe to drop) also removed `libQt6Positioning.so.6`, which
`libQt6WebEngineCore.so.6` needs **directly** (its geolocation API), and
crashed the app on startup with a missing-library `ImportError`. Fixed by
computing the real ELF `NEEDED` transitive closure (`readelf -d`) from
every library the app's actual roots (Core/Gui/Widgets/Quick/Qml family,
`WebEngine[Quick|Core]`, Network, the kept `QuickControls2`/
`QuickDialogs2`/`Labs*` variants, ...) depend on, and excluding only what's
verifiably unreachable from that closure. The exact verified set is
hardcoded in the spec with a comment on how to regenerate it if a future
PySide6 upgrade breaks it again.

## Alternatives considered

- **Nuitka instead of PyInstaller.** Not evaluated in depth — PyInstaller
  has more mature, actively-maintained PySide6 hooks (the `hook-PySide6.*`
  family this ADR's traps live in), which mattered more than Nuitka's
  reputed smaller output once the trimming above closed most of the gap
  anyway.
- **Hand-curated substring exclusion list for `Qt/lib`** (the first
  attempt). Rejected after it crashed the app — a naive per-module-name
  guess can't distinguish "this Qt library backs an unused QML wrapper"
  from "this Qt library is a direct link-time dependency of something
  genuinely used." Only a real dependency-closure computation can.
- **Leave the bundle unfiltered** (926MB). Rejected as unnecessary bloat
  once the trimming technique was worked out — the size difference is
  real disk space and download time for every future build, for no
  functional gain.

## Consequences

- The Qt/lib exclusion set (`UNREACHABLE_QT_LIBS` in the spec) is tied to
  the exact PySide6 version in the lockfile. A PySide6 upgrade needs the
  closure recomputed (script in this ADR's commit history / the
  distribution-ci-packaging session memory) — not just re-running the
  build and hoping it still works; a missing library shows as a startup
  crash, not a silent degradation.
- The app icon is a generated placeholder (blue square, "MSI" text) — no
  real icon existed when this was built.
- `packaging/pyinstaller/msianalyzer-gui.spec` is Linux-only — the ELF
  (`readelf`, `.so`) specifics don't carry over to a Windows build, which
  will need its own dependency-trimming pass against Windows DLLs (or may
  reasonably ship untrimmed first, given no Windows build exists yet to
  compare against).
- See [distribution & packaging](../distribution.md) for the practical
  how-to-build instructions this ADR doesn't repeat.
