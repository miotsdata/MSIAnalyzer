# Distribution & packaging

How MSIAnalyzer ships: a Python package (core + optional GUI extra) and a
standalone Linux AppImage. See [ADR 22](adr/0022-distribution-and-ci-strategy.md)
for the strategy and why artifact hosting is deliberately not part of it yet.

## Python package

```
pip install msianalyzer          # core + CLI only
pip install msianalyzer[gui]     # + PySide6, for the desktop app
```

Only `pyside6` is GUI-exclusive — `plotly`/`matplotlib`/`anndata`/`libviz`/
`tomli-w`/`kaleido` are used by core plotting/report code too, so they stay
in the base `dependencies` list, not the `gui` extra.

Build locally:

```
pdm build       # writes dist/msianalyzer-<version>-py3-none-any.whl and .tar.gz
```

### Packaging excludes

`[tool.pdm.build] excludes` in `pyproject.toml` keeps two things out of
what `pdm build` ships:

- **`src/msianalyzer/log/`**: `configure_logging`'s default
  `debug_log_dir` used to live inside the package source tree (it's now
  the OS per-user log folder, see [logging](logging.md)). Older checkouts
  still have that folder full of debug logs, and without the exclude
  `pdm build` would ship them.
- **`tests/core/examples/`** — real instrument data (`.raw`/`.mzML` +
  generated `.db`/`.h5ad` results, hundreds of MB). Never reached the
  wheel (pdm-backend excludes `tests/` from wheels by default), but
  bloated the **sdist** to over a gigabyte before this exclude.

Neither gap was caused by a specific change — `pdm build` had simply never
been run for real before the excludes were added. Worth periodically
building the actual distributable artifacts (not just running tests) to
catch this class of issue early; see [ADR 22](adr/0022-distribution-and-ci-strategy.md).

## Linux standalone build

```
packaging/appimage/build.sh
```

produces `dist/MSIAnalyzer-x86_64.AppImage` (a onedir PyInstaller build
wrapped as a single file). The script, in order:

1. Fetches `appimagetool` into `packaging/.tools/` if not already cached
   (not committed — a build tool, not a project asset).
2. Recompiles `resources_rc.py` from `resources.qrc` — can drift from the
   `.qml` sources otherwise (same reasoning as the test suite's
   `compile_qml_resources` fixture, see [testing](testing.md)).
3. Runs PyInstaller against `packaging/pyinstaller/msianalyzer-gui.spec`.
4. Assembles an `AppDir` and runs `appimagetool --appimage-extract-and-run`
   (sidesteps needing FUSE to mount appimagetool itself, which isn't
   guaranteed available in every environment).

See [ADR 23](adr/0023-pyinstaller-appimage-packaging.md) for why the spec
is shaped the way it is — onedir, the QML/library trimming approach, and
the exact traps that shaped it. The trimmed build is ~742MB unpacked,
~298MB as the AppImage; a real app icon is still needed (currently a
generated placeholder).

Both build scripts run `mkdocs build` first; the spec bundles `site/`
for Help > User Guide.

## Windows build

Built on Windows itself (PyInstaller can't cross-compile), from the same
spec, by `packaging/windows/build.ps1`. Output:

- `dist\msianalyzer-gui\`: the runnable app (`msianalyzer-gui.exe` + `_internal\`)
- `dist\MSIAnalyzer-setup-<version>.exe`: per-user installer (Inno Setup;
  installs to `%LOCALAPPDATA%\Programs\MSIAnalyzer`, no admin rights)

See [ADR 52](adr/0052-windows-build-and-fast-startup.md) for the
startup-time work and the Windows-specific runtime fixes.

### One-time setup of the build machine (Windows 10/11 x64, e.g. a VM)

1. **Git**: <https://git-scm.com/download/win>. Clone the repo.
2. **PDM** (PowerShell):
   `powershell -ExecutionPolicy ByPass -c "irm https://pdm-project.org/install-pdm.py | py -"`.
   If `py` doesn't exist yet, install Python 3.13 x64 from python.org
   first (tick "Add to PATH"). PDM can also fetch its own interpreter
   with `pdm python install 3.13`.
3. **Inno Setup 6**: <https://jrsoftware.org/isdl.php>, default options.
4. In the repo: `pdm install -G:all -d`.

No Visual Studio or MSVC build tools are needed. Every dependency ships a
prebuilt Windows wheel, and the PySide6 wheel carries the MSVC runtime.

### Build

```
powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
```

The PyInstaller step prints `pruning N unreachable Qt libs: [...]`; on
Linux N is ~100. If the built app fails to start with a missing-DLL
error, that list is the first place to look.

### Smoke test before handing it over

Running `dist\msianalyzer-gui\msianalyzer-gui.exe` directly is enough; no
need to install. Check:

- it opens, and a second launch only focuses the first window
  (single-instance lock);
- **a Run with `n_workers` > 1** completes, and no extra windows appear
  (`freeze_support`, ADR 52);
- MS1 and mirror plots render (QtWebEngine);
- Visual Inspection heatmaps, an export, Help > User Guide;
- `%LOCALAPPDATA%\MSIAnalyzer\Logs\` has the debug log.

In a VM without GPU acceleration, if the WebEngine plots stay blank, set
`QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu` for that session. It's a VM
issue; real hardware doesn't need it.

### Expected on a fresh machine

- **SmartScreen** says "Windows protected your PC" (the exe is
  unsigned): click *More info → Run anyway*. Only a code-signing
  certificate removes this.
- **First launch is slow** (Defender scans every bundled file, and Qt
  compiles and caches the QML); later launches are fast.

## CI

`.github/workflows/ci.yml` runs on every push/PR to `dev`/`main`: install
(`pdm install -G:all -d`, pinned to exact Python `3.13.0` — see
[requires-python](#requires-python)), advisory lint, then the full test
suite headless.

### The headless Qt/WebEngine recipe

Validated against a fresh `ubuntu:24.04` container running as a non-root
user (matching GitHub's actual `ubuntu-latest` runner conditions, not just
a local dev machine that already has a display). Two environment
variables are required together:

```
QT_QPA_PLATFORM=offscreen
QTWEBENGINE_CHROMIUM_FLAGS="--no-sandbox --disable-gpu"
```

Without `--no-sandbox`: Chromium's own sandbox needs privileges a
container/CI runner doesn't grant by default, and the whole process exits
with code 1 **silently — no traceback, no error message** the moment a
`WebEngineView`-backed test runs. This is easy to misdiagnose as a crash;
it's a permissions issue.

A broad list of Qt/Chromium runtime shared libraries also has to be
`apt-get install`ed explicitly (see the workflow file for the full list) —
worked out empirically, one missing-library error at a time, against a
minimal base image. `build-essential`/`pkg-config` are in that list too,
for `find-mfs`'s C extension (a dev dependency); GitHub's real
`ubuntu-latest` image ships a fuller toolchain by default, so this may be
redundant there, but it's harmless insurance either way.

One test, `test_create_project_folder_no_permissions_emits_invalidCreateProjectPath`,
only passes running as **non-root** — root bypasses the filesystem
permission restriction the test relies on to simulate a failure. Not a
bug; just means CI (and local reproduction of a CI-only failure) needs a
non-root user, which GitHub Actions already provides by default.

### The retry line

```
pdm run pytest -q || pdm run pytest -q
```

absorbs a known, pre-existing, already-documented flake: a PySide6/
Shiboken wrapper-lifecycle bug (see `tests/gui/conftest.py`'s
`find_visual_child` docstring and [GUI](architecture/gui.md)) that
surfaces as a stale-QML-binding `AttributeError` when enough
`QQuickView`/top-level-window wrappers accumulate in one test-session
process. It's accumulation-triggered, not deterministic — it hits a
*different* test each run and always passes clean in isolation — so a
single retry of the whole suite is the right level of tolerance, not a
per-test fix.

### Linting

No `[tool.pylint]` config exists yet. Default pylint flags ~360
pre-existing findings on this codebase, dominated by
`missing-module-docstring`/`missing-function-docstring` — which directly
conflicts with this project's own convention of writing no comments/
docstrings unless the *why* is non-obvious (see the top-level style
guide). CI's lint step is `pdm run pylint src/msianalyzer || true` —
advisory, not a merge gate — until someone writes a config that actually
reflects this project's conventions (disabling the docstring checks,
allow-listing PySide6's compiled C-extension modules for
`no-name-in-module`, ...).

### requires-python

`requires-python = ">=3.13,<3.14"` — was a strict `==3.13` (a PEP 440
exact-match specifier accepting only 3.13.0 itself, rejecting every later
3.13.x patch, including for `pip install`) since the project's very first
commit, never revisited. Relaxed once noticed; CI still pins the exact
patch PDM installs to `3.13.0` for reproducibility, independent of what
the constraint itself now allows.

## Single-instance lock

The GUI refuses to open a second window — a later launch pings the
running instance (which raises/focuses itself) and exits instead. See
[ADR 24](adr/0024-single-instance-app-lock.md).
