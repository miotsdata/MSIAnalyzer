# GUI

The desktop app (`msianalyzer-gui`) is PySide6 + QML. Python owns state and
talks to the core pipeline; QML is view-only. This page describes the current
shape of things — see the [ADR index](../adr/index.md) for the handful of
decisions here that are load-bearing enough to be immutable records rather
than "whatever the code currently does."

## Package layout

```
msianalyzer/gui/
  main.py                 build_engine() — wires everything below into one QQmlApplicationEngine
  models/
    project.py             ProjectModel   — one open Project, exposed to QML
    analysis.py             AnalysisModel  — one selected run within a project
  utils/
    router.py               Router          — the only cross-page communication channel
    application.py          Application     — owns every bridge/model, wires signals to Router
    core_bridge.py           CoreBridge      — create/load project, run analysis (QThread via run_worker)
    analysis_bridge.py       AnalysisBridge  — read-only queries against one analysis DB, for QML
    heatmap_provider.py      HeatmapImageProvider — image://heatmap/... raster tiles for Visual Inspection
    run_worker.py            QThread wrapper around Run, for the CoreBridge
    config_schema.py         drives the New Analysis page's generated config form
    formatting.py            shared display formatters (e.g. minute-precision dates)
  qml/
    Main/Main.qml            ApplicationWindow, StackView, palette, Router Connections
    Views/*.qml               one page or reusable component per file
    resources.qrc             registers every .qml file under qrc:/...
```

`resources_rc.py` is generated (`pyside6-rcc qml/resources.qrc -o resources_rc.py`)
and must be regenerated after any change to `resources.qrc` or any `.qml` file's
existence/path — see [testing.md](../testing.md).

## Navigation: `Router` + `StackView`

There is no page-to-page QML importing or direct signaling. Every navigation
is: a page emits a `Router` signal → `Application` (or `Main.qml`'s
`Connections { target: Router }`) reacts. `Router` itself holds no state — it
is a pure signal bus, instantiated once as a QML singleton.

Two different things flow through it:
- **Pure navigation** (no Python-side reaction needed): `Main.qml`'s own
  `Connections` block pushes the target page onto `stackView` directly —
  `createProjectPageRequested`, `newAnalysisPageRequested`,
  `showErrorRequested`, etc.
- **Navigation that needs Python state first** (loading a project from disk,
  starting a run, building an `AnalysisModel` from a run id):
  the page emits a *request* signal (`projectFolderChosen`,
  `runAnalysisRequested`, `analysisSelected`); `Application._connect_signals`
  is the only place these are consumed; once Python-side work finishes,
  `Application` emits the corresponding `show*Requested` signal, which
  `Main.qml` then pushes.

Pages are plain `StackView` items — nothing is destroyed on navigating away
except by `StackView`'s own default replace behavior, so anything holding a
resource across a page's lifetime (a `WebEngineView`, an image provider
cache) needs to actually be re-created per page load if that's the intent,
not assumed torn down.

## The Analysis workspace: `AnalysisPage` + lazy `Loader` sections

`AnalysisPage.qml` is one container with four sections (Summary, MS1 Spectra,
Annotations, Visual Inspection) swapped via a `StackLayout` of `Loader`s, not
four separate `StackView` pushes — the nav rail needs to stay visible and
switching sections should not re-run the whole page's construction.

- `summaryLoader` is eager (`source` set directly) — cheap, and it's the
  default landing section.
- The other three are lazy (`active: sectionStack.currentIndex === N`).
  `StackLayout` keeps every child as a permanent sibling instead of
  destroying hidden ones, so an eagerly-loaded section would sit fully
  constructed in the tree even while another tab is active. This matters
  most for MS1 Spectra and (for `find_visual_child`-style recursive
  searches) any section holding a `WebEngineView` — see below.
- Every Loader's `onLoaded` re-binds `item.analysis` via `Qt.binding(...)`,
  not a one-time assignment: `AnalysisPage.analysis` can still be unset at
  the exact moment a Loader first finishes when the page is reached via
  `StackView.push({"analysis": ...})`.
- A `LoadingOverlay` (reusable component, `BusyIndicator` over an opaque
  backdrop, with its own click/wheel-blocking `MouseArea`) covers the
  content area — and disables the nav rail / "Back to project" — until
  `AnalysisPage.currentSectionReady` is true for the active section. For
  most sections that's just `Loader.status === Loader.Ready`; for MS1 it
  additionally waits on the embedded plot's own load (`plotLoading` on
  `MS1SpectraSection.qml`) — see
  [ADR 14](../adr/0014-analysis-workspace-lazy-loading.md).

## Plotting: two different approaches for two different needs

- **Line/mirror plots** (MS1 aggregated spectra, MS2 mirror plots) use
  `core/plotting/plotter.py`'s existing Plotly figures, rendered via
  `fig.to_html(...)` into a `QtWebEngineQuick` `WebEngineView`. Reuses the
  same plotting code the CLI report already produces; buys interactive
  zoom/pan for free.
- **Spatial heatmaps** (Visual Inspection) render as raw raster images
  instead: `HeatmapImageProvider` (a `QQuickImageProvider`, registered as
  `image://heatmap/...`) reads `obsm['spatial']` + the requested feature or
  `obs` column out of the sample's `.h5ad`, maps it through a matplotlib
  colormap, and returns a `QImage`. QML requests tiles by a struct-like
  string id (`sample|mz|layer|colormap|vmin|vmax`, or `sample|obs:col|...`)
  that Qt itself caches by, so an unchanged id is instant. Chosen over a
  second `WebEngineView`-per-tile approach for speed and exact per-pixel
  control over the black-background imshow style — see
  [ADR 13](../adr/0013-hybrid-plotting-approach.md).

See `_find_visual_child` in `tests/gui/conftest.py` for a related gotcha:
recursing into a live `WebEngineView`'s child tree while searching for an
unrelated item's children is unsafe (can crash `getWrapperForQObject`) —
scope any recursive `childItems()` search to the smallest relevant subtree.

## Singleton popup windows: `Loader` + `showFor()`/`openFor()`

`MirrorPlotDetailWindow` and `RoiDesignWindow` (both top-level `Window`s,
opened from a button click) are each held by a lazy `Loader` with
`active: false` initially — the first click sets `active = true`, every
click after that reuses the same `Loader.item` instead of constructing a
new window. This matters beyond tidiness: enough accumulated top-level
`QQuickView`/window wrappers in one process is what triggers the
PySide6/Shiboken wrapper-lifecycle bug noted below and in
[testing](../testing.md) — reusing one window instead of piling up new
ones is a real mitigation, not just a size optimization.

Each window exposes a single entry point (`showFor(...)`/`openFor(...)`)
that the caller always goes through, rather than the caller setting
properties and toggling `visible` directly. This is deliberate: an earlier
version relied on `onVisibleChanged` (fired on a closed→open transition)
to reset/refresh the window's content, which worked for a fresh open but
silently did nothing on a second click while the window was already
open — `visible` doesn't actually change in that case, so the signal never
fires, and the window kept showing the *previous* click's stale content
under a title bar that (misleadingly, since it's a direct property
binding) looked updated. `showFor()`/`openFor()` refresh unconditionally
regardless of the transition, and are what `onVisibleChanged` now delegates
to for the fresh-open case too. Any future singleton popup window should
follow the same shape: one `Loader`, one `showFor`-style entry point, no
caller setting `visible = true` directly.

## Single-instance app lock

A second `msianalyzer-gui` launch doesn't open a second window — it pings
the running instance (which raises/focuses itself) and exits before
building any UI. See [ADR 24](../adr/0024-single-instance-app-lock.md).

## Theme

The GUI is being redesigned page-by-page toward a professional desktop-app
look (explicit light `palette` block in `Main.qml`, denser layouts, no
mobile-app-style card chrome) — a standing direction, not a one-off styling
pass. See [ADR 15](../adr/0015-professional-desktop-theme-direction.md).

## Testing

No real display in CI or in this development environment — `QT_QPA_PLATFORM=offscreen`
throughout. See [testing.md](../testing.md) for the full protocol
(rebuild `resources_rc.py`, run `tests/gui` repeatedly — offscreen + WebEngine
has known rare-crash and first-`QQuickView` flakiness that isn't a real bug).
