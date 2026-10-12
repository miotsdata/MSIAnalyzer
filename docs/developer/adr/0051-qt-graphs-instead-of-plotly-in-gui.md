# 51 — Qt Graphs instead of Plotly/WebEngine for the GUI's interactive plots

**Status:** Accepted, implemented on branch `pyside6.12` (PySide6 6.12.0);
merges to `dev` after the user's real-app check. See **Implementation**
at the end for what the spike settled.

## Context

[ADR 13](0013-hybrid-plotting-approach.md) put every interactive line plot
in the GUI on Plotly, rendered with `fig.to_html(...)` into a
`WebEngineView`. That reused `Plotter`'s report figures, but each plot then
needs its own Chromium renderer, and that has cost us a lot since:

- **Startup and laziness.** Each `WebEngineView` starts a Chromium renderer
  process. [ADR 14](0014-analysis-workspace-lazy-loading.md)'s lazy
  `Loader`s, `LoadingOverlay` and the "don't navigate while loading"
  input blocking exist largely because of this.
- **Plumbing.** Each plot is written to an HTML file on disk, because
  `loadHtml()` fails silently past about 2 MB and inline Plotly.js alone is
  4.6 MB (`_write_spectrum_html`, `_write_mirror_plot_html`,
  `_write_calibration_plot_html`). MS1's point click goes from JS through a
  `QWebChannel` back to `AnalysisBridge.onSpectrumPointClicked`.
  `QtWebEngineQuick.initialize()` has to run before any
  `QGuiApplication` exists.
- **Crashes and fragility.**
  - Plotly figures can't be built off the main thread ([ADR 17](0017-mirror-plot-background-worker.md)).
  - plotly 7 / plotly.js v4 silently broke the embedded JS event wiring ([ADR 25](0025-pin-plotly-below-7.md)).
  - The GLX `BadValue` WebEngine crash (2026-09-28) was a machine/driver issue, not ours, but it took down every plot.
  - Unresolved intermittent full-suite crashes cluster around WebEngineView + Loader teardown.
  - MirrorPlotDetailWindow re-renders through Chromium on every drag frame.
- **Testing and CI.** CI needs `QTWEBENGINE_CHROMIUM_FLAGS="--no-sandbox
  --disable-gpu"` plus extra apt libraries. Under the offscreen QPA a
  WebEngineView's content can't be inspected, so tests check only that a
  URL was set, not what got plotted.
- **Packaging.** QtWebEngine is the largest part of the AppImage. The
  PyInstaller spec has to use onedir because of `QtWebEngineProcess`, and
  it prunes locales and devtools paks by hand.
- **Theming.** The plots are always white (`plot_bgcolor="white"`) and
  ignore `Theme.qml` and the live light/dark switch
  ([ADR 15](0015-professional-desktop-theme-direction.md)).

Qt Graphs is native to Qt Quick and draws through the same RHI scene graph
as the rest of the UI. Qt 6.12 (LTS) adds what was missing for 2D plots:
**built-in zoom/pan**, **log axes**, and **custom axis labels**.

The data is small. Measured on a real project
(`2026-09-11_complete-lib_test`), filtered MS1 spectra are about 400–600
peaks per sample, mirror plots are tens to low hundreds of peaks, and a
calibration curve has a handful of standards. Rendering speed isn't the
concern here.

## Decision (proposed)

The GUI's interactive plots are drawn with **Qt Graphs 2D (`GraphsView`)
in QML**. `AnalysisBridge` returns **plain data** (lists or dicts of
numbers and strings) instead of `file://` URLs to generated HTML. Each
plot becomes a QML component that binds that data to series.

**Plotly stays** for everything outside the GUI: the CLI/HTML report
(`core/report`, `Plotter.plot_spectra` / `plot_ms2_annotation` /
`plot_calibration_curve` as used there). This changes only the
GUI half of ADR 13. The heatmap raster path (`HeatmapImageProvider`) is
unchanged.

### Per-plot mapping

| Plot | Today | Proposed |
|---|---|---|
| **Calibration curve** (`CalibrationSection.qml`, `getCalibrationPlotUrl`) | Plotly markers + dashed fit line, `.3g` ticks, equation/R² title | `ScatterSeries` (standards) + `LineSeries` (fit, extended 5% past the data on both ends, as now). `ValueAxis.labelFormat: "%.3g"`. Equation/R² as a QML `Label` above the graph. New slot `getCalibrationPlotData(db, display_area_unit)` reuses the existing unit-conversion code as is. |
| **MS1 spectrum** (`MS1SpectraSection.qml`, `getSpectrumUrl`) | Stick plot, three category traces (no MS2 / annotated / non-annotated) with legend, hover m/z + intensity, click → `spectrumPointClicked(mz)` via WebChannel | One stick series per category, colors from `Theme.qml`. Click → nearest peak → `analysisBridge.onSpectrumPointClicked(mz)` called **directly from QML**, so no WebChannel. Hover tooltip from the series' hover signal. Zoom/pan built in (6.12). New slot `getSpectrumData(db, run_id, sample_id)`. |
| **Mirror plot, detail window** (`MirrorPlotDetailWindow.qml`, `requestMirrorPlot` → `mirrorPlotReady(url)`) | Up/down sticks, matched/unmatched colors, connector lines, y fixed to ±1.15 with mirrored tick labels, "Empirical"/"Library" labels | Same stick approach, four series (emp/lib × matched/unmatched) + connector series. Fixed y-axis range with a label formatter for the mirrored `1.0 … 0 … 1.0`. `MirrorPlotWorker` **stays** (raw I/O still needs to run off the GUI thread). The `_align_peaks` matching moves to the worker, which now returns ready-to-plot arrays. `mirrorPlotReady` carries a dict, not a URL. ADR 17's "build the figure on the main thread" rule no longer applies, since no `go.Figure` exists in the GUI. |
| **Mirror plot, inline quick view** (`getBasicMirrorPlotImage`) | Static image, no WebEngine | **Unchanged** in this ADR. Could later reuse the same QML mirror component; that's a separate decision. |

### How sticks get drawn: the open question for the spike

Qt Graphs has no "stick" series, and as far as I know `LineSeries` has no
NaN/None gaps to imitate Plotly's `[m, m, None]` trick. Candidates, in
order of preference. The spike chooses one, and every stick plot then uses
the same one:

1. **`BarSeries` on a value axis**, if 6.12 allows thin bars at
   arbitrary x positions. I need to check this, since bars have
   historically been category-based.
2. **A custom-delegate series** (`QCustomSeries` exists in PySide6
   6.11's `QtGraphs`): one item per peak, with a QML delegate drawing a
   vertical line from 0 to the peak's intensity.
3. **A `ScatterSeries` for the peak tops (hover/click hit targets) plus a
   `Shape` overlay** that draws the sticks using the graph's
   value→pixel mapping. This always works but means owning coordinate
   mapping during zoom/pan.
4. **One `LineSeries` per peak.** That's about 600 series for MS1, which
   is probably too heavy, and only kept as a last resort.

### Rollout (each step a commit on `pyside6.12`, with the user's real-app check before the next)

1. Bump only: `pyside6>=6.12.x,<6.13`, recompile and **commit**
   `resources_rc.py`, full test suite ×3, AppImage rebuild, real-app smoke
   test. **This is where we decide 6.12.0 vs waiting for 6.12.1**, before
   any plot changes, so a Qt regression can't be confused with a
   migration bug.
2. Spike: calibration curve (the simplest plot) plus a throwaway MS1 stick
   prototype to settle the stick question above. **Go/no-go point.**
3. MS1 spectrum.
4. Mirror-plot detail window.
5. Cleanup: remove the HTML writers, WebChannel script,
   `QtWebEngineQuick.initialize()`, CI Chromium flags, WebEngine parts of
   the PyInstaller spec, and the `QtWebEngine` QML imports. Then check
   whether `LoadingOverlay` and the lazy-section-loading guards are still
   needed (they are probably still worth keeping for data loading).
6. Merge to `dev` only after the user confirms the whole branch in the
   real app.

## Alternatives considered

- **Keep Plotly + WebEngine and only bump Qt.** This is what we fall back
  to if the spike fails. It keeps every cost listed in Context.
- **Matplotlib → `QImage` (like the heatmaps / basic mirror image).**
  There's no WebEngine, but zoom, pan, hover and click would all need
  re-rendering and hand-rolled hit-testing. Fine for static views, poor for
  the interactive ones.
- **pyqtgraph.** It's fast and has native stick/bar plots, but it's
  QWidget-based and doesn't fit a QML app without a QQuickWidget/embedding
  bridge.
- **Custom `QQuickPaintedItem` spectrum plot.** We'd own axes, ticks,
  zoom, pan and hover ourselves. That's more code to maintain than Qt
  Graphs, and it's only worth it if all four stick options fail.

## Consequences (if accepted)

- **WebEngine leaves the GUI entirely**, if nothing else uses it. As of
  this ADR, nothing does. That means:
  - no Chromium renderer processes
  - no `QtWebEngineQuick.initialize()` ordering constraint
  - no CI Chromium flags
  - a much smaller AppImage
  - PyInstaller onefile becomes an option again
- **The plots become testable.** Tests can assert on series point counts,
  colors, axis ranges and click → `spectrumPointClicked`, instead of only
  checking a URL was set. Whether `GraphsView` renders under
  `QT_QPA_PLATFORM=offscreen` needs checking in step 2. `TableView` is
  known to instantiate zero delegates there, so don't assume it works.
- **Plots follow `Theme.qml`** and the live light/dark switch through
  `GraphsTheme`.
- **Two plotting stacks for the same figures:** Plotly in reports, Qt
  Graphs in the GUI. Shared logic (peak categorization, `_align_peaks`
  matching, calibration unit conversion) must stay in Python and feed
  both. Each side only lays out the plot.
- **Hard dependency on Qt ≥ 6.12** for zoom/pan and log axes. That's
  acceptable because 6.12 is an LTS release.
- **Losing Plotly's free extras:** the hover "x unified" crosshair,
  double-click-to-reset, and the modebar (PNG download, autoscale). Any of
  these the user relies on must be rebuilt explicitly. Decide which before
  step 3.
- ADR 17's rule about where the figure is built stops applying to the GUI.
  ADR 25's plotly pin still applies to reports.

## Implementation (2026-10-12)

- **Sticks: none of the four candidates above.** Each group is one
  `LineSeries` that zigzags `(m, 0) → (m, y) → (m, 0)` from peak to peak;
  the return trips run along y = 0, under a zero line drawn on top (the
  mirror plot's center line anyway). One series per category, offscreen-
  renderable, so tests click real peaks. `StickPlot.qml` serves MS1 and
  the mirror plot; `CalibrationPlot.qml` the calibration curve.
- **Hover/click** pick the peak nearest the pointer in x within 8 px (the
  closer tip wins between the mirror plot's halves), from the axes'
  `visualMin`/`visualMax` and the graph's `plotArea` — not series hit-
  testing, which needs pixel-exact hits on 1 px sticks.
- **Plotly extras, decided:** kept hover tooltip (m/z + intensity) and
  double-click reset; wheel zoom and drag pan are `GraphsView`'s own. y is
  never zoomed: it refits to the tallest peak in the visible m/z window
  (or stays at ±1.15 for the mirror plot). Dropped: the modebar (PNG
  export exists in the Export menu) and the "x unified" crosshair line.
- **Data:** `getSpectrumData`, `getCalibrationPlotData`, and
  `mirrorPlotReady(dict)`, built by `plotter.py`'s `spectrum_groups`,
  `mirror_plot_data`, `calibration_line_range`/`calibration_title`, which
  the report's Plotly figures share. `MirrorPlotWorker` now builds the
  plot data on its thread too (ADR 17's main-thread rule is gone with
  Plotly).
- **Colors** come from `Theme.qml` (`plotStrongColor`, `plotWeakColor`,
  `plotAccentColor`), so the plots follow the light/dark switch.
- **Dependencies:** the `gui` extra is `pyside6-essentials` +
  `pyside6-addons` (Qt Graphs is in Addons), not the `pyside6`
  meta-package, which since 6.12 also pulls `pyside6-webengine` (~280 MB)
  and `pyside6-pdf`. Removing the meta-package from an existing
  environment deletes the shared `PySide6/__init__.py`: one
  `pdm sync -G:all -d --reinstall` restores it.
- **Gone:** the HTML writers, the WebChannel click path
  (`onSpectrumPointClicked`/`spectrumPointClicked`),
  `QtWebEngineQuick.initialize()`, the Chromium CI flags and the
  WebEngine parts of the PyInstaller spec (which now keeps the `QtGraphs`
  QML module). `LoadingOverlay` and the lazy section loaders stay: they
  also cover the sections' async data loads.
- Step 1 also surfaced a test-suite race (a test's bridge deleting its
  still-running worker thread on return), fixed in `tests/gui/conftest.py`
  on `dev` too.

