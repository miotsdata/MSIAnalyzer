# 13 — Hybrid plotting: Plotly/WebEngineView for line plots, raster for heatmaps

**Status:** Accepted

## Context

The Analysis workspace GUI needs two visually different kinds of plot: line
plots (MS1 aggregated spectra, MS2 mirror plots) that benefit from
interactive zoom/pan and hover tooltips, and spatial heatmaps (Visual
Inspection) that need to render one colored pixel per (x, y) coordinate,
often re-rendered on every vmin/vmax/colormap/feature change.

`core/plotting/plotter.py` already builds Plotly figures for both the CLI's
static HTML report and the existing `plot_ms2_annotation` mirror plot — that
code is worth reusing directly rather than rebuilding equivalent plots with
a different library. But Plotly is a poor fit for the heatmap case: a
`WebEngineView` per tile is heavyweight (Visual Inspection shows a grid of
tiles, one per sample, at once), and exact per-pixel control (e.g. the
black-background imshow look, precise `vmin`/`vmax` clipping) is easier with
a raw raster than through a general-purpose charting library's own encoding.

## Decision

Two different rendering paths, chosen per plot kind rather than picking one
library for everything:

- **Line/mirror plots** (MS1 spectra, MS2 mirror plots): reuse
  `Plotter.plot_spectra` / `Plotter.plot_ms2_annotation` as-is, render via
  `fig.to_html(...)` into a `QtWebEngineQuick` `WebEngineView`. Interaction
  (click-to-select a feature, zoom) comes for free from Plotly's own JS.
- **Spatial heatmaps** (Visual Inspection): render as raw numpy → matplotlib
  colormap → `QImage`, served through a `QQuickImageProvider`
  (`HeatmapImageProvider`, registered as `image://heatmap/...`). QML
  requests a tile by a struct-like id string
  (`sample|mz|layer|colormap|vmin|vmax`, or `sample|obs:col|...` for `obs`
  columns) that Qt itself caches by — an unchanged id re-renders nothing.

## Consequences

- Two rendering code paths to maintain instead of one, but each is a good
  fit for its own plot kind rather than one library stretched to cover both.
- `WebEngineView` needs `QtWebEngineQuick.initialize()` before any
  `QGuiApplication` exists (see `gui/main.py`, `tests/gui/conftest.py`), and
  interacts with `Loader`-based lazy construction in ways that needed care —
  see [ADR 14](0014-analysis-workspace-lazy-loading.md).
- `HeatmapImageProvider` owns a small LRU cache (by `.h5ad` path) so
  switching features/samples within the same session doesn't re-read from
  disk each time; this cache is per-provider-instance, not persisted.
