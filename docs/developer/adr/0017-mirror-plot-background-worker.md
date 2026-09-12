# 17 — Background thread for mirror-plot data resolution, not figure-building

**Status:** Accepted

## Context

`AnalysisBridge`'s methods are documented (see [GUI architecture](../architecture/gui.md))
as staying synchronous — "local SQLite reads are fast enough, no worker
needed, unlike `RunWorker`." That held until the Annotations section grew a
raw-source toggle for its mirror plot: `emp_source`/`lib_source == "raw"`
means real file I/O against a raw per-sample database or a spectral
library file, neither guaranteed local or fast (see
[ADR 16](0016-store-raw-library-spectrum.md) for why the library case in
particular could be a slow/remote mount). Running that synchronously
inside a `@Slot` — the GUI thread, for anything called from QML — blocked
Qt's entire event loop for as long as the I/O took. Reported by a user as
the app going fully unresponsive ("python is not responding") when
switching to a raw library spectrum, not a slow-but-working wait.

## Decision

`AnalysisBridge.requestMirrorPlot` is fire-and-forget: it starts a
`MirrorPlotWorker` (`QThread`) that calls `Plotter.get_annotation_spectra`
— data resolution only — and emits either `succeeded(data)` or
`failed(message)`. `AnalysisBridge` relays the result via its own
`mirrorPlotReady(url)` signal once the corresponding HTML file is written;
QML calls `requestMirrorPlot` and listens for that signal instead of using
a return value, showing a `LoadingOverlay` meanwhile.

Building the actual Plotly figure (`Plotter.build_mirror_figure`, split
out from `plot_ms2_annotation` for exactly this reason) stays on the
**main** thread. Building it on the worker thread too was the first
attempt and reproducibly crashed — a segfault inside Plotly's own
`_perform_plotly_relayout`, observed to coincide with the main thread
being mid garbage-collection. Plotly's object graph isn't safe to build
concurrently with unrelated main-thread activity; the worker thread
therefore only ever touches plain data (dataframes/arrays/dicts), never a
`go.Figure`.

## Consequences

- Any *other* future GUI method that turns out to need backgrounding
  should follow the same split: keep pure I/O/data-resolution on the
  worker, keep anything constructing complex third-party object graphs
  (Plotly here; plausibly true of similar libraries) on the main thread.
  Don't assume "move the whole method to a QThread" is safe by default.
- `AnalysisBridge`'s "stays synchronous" default (see the GUI architecture
  page) is no longer unconditional — it's the default for methods that
  only ever read the local analysis database; anything that can reach a
  raw per-sample database or a library file under a "raw" toggle needs
  the same worker treatment as `requestMirrorPlot`.
- The inline Annotations panel's own quick view
  (`AnalysisBridge.getBasicMirrorPlotImage`) deliberately stays
  synchronous instead: it never offers a raw option at all (always the
  stored filtered spectra, rendered as a static image, no
  `WebEngineView`), so there's no slow-I/O path to protect against in the
  first place. Raw sources and the full interactive Plotly view only
  exist in a separate, lazily-constructed detail window (same
  `active`-gated `Loader` pattern as [ADR 14](0014-analysis-workspace-lazy-loading.md)'s
  section construction) — that's the one place `requestMirrorPlot` is
  reachable at all.
