# 68 — "Get statistics" window in Visual Inspection

**Status:** Accepted

## Context

Comparing a feature or an obs column (`tic`, `nFeatures`, a clustering)
between samples and ROIs meant leaving the app for a notebook: seaborn
violins per `sample_ROI` for QC, crosstabs for categorical columns. The user
wanted the same plots one click away from Visual Inspection.

## Decision

- **A "Get statistics" button** in Visual Inspection opens one reusable
  window (`StatisticsWindow.qml`, lazy Loader + `openFor`, like ROI
  Design). It follows Visual Inspection's selection live: the feature on
  the current layer, or the obs column, over the samples not hidden there.
  - Continuous (feature, numeric obs column): **violins**, with quartile
    lines (median dashed) and no density beyond the data (seaborn's
    `cut=0`), as in the QC notebook.
  - Categorical obs column: **stacked bars** of pixel counts, coloured by
    the category colours (ADR 57); **Proportion** scales every bar to 1.
    One shared legend.
  - Disabled on the Local Moran's I layer: it is computed only while
    rendering, there are no stored per-pixel values.
- **Groups:** one per sample, or one per sample × ROI with **Use ROI**
  (the ROI's catalog colour). A pixel in two nested ROIs counts in both.
  **Pixels outside ROI** adds a grey `(no ROI)` group per sample.
  **Detected pixels only** drops zeros (on by default for a feature, where
  most pixels are 0; off for an obs column). NaN is always dropped.
- **Layout:**
  - **Grouped by ROI** (needs Use ROI): one panel per ROI, samples along x
    (the notebook's `Sample / Matrix / -` split). Defaults to one column,
    the panels one below the other.
  - Otherwise one panel per violin. Bars without grouping share one panel
    (one stacked bar per panel says little).
  - Rows × columns arrange the panels; rows grow when the cells are too
    few. Every panel shares the y axis.
- **Plot size** (50–400 %) re-renders the figure larger, not a pixel zoom:
  fonts keep their size, so there is more room per violin, and the panel
  scrolls, like a big `figsize` in Jupyter. 100 % fills the panel, with at
  least 220 px per row.
- **Reorder…**: drag-and-drop list of the violins (or, grouped, of the ROI
  panels and of the samples). `ListModel.move()`, not a Repeater over a JS
  array. Names the order doesn't mention go last, so a new ROI or sample
  doesn't need a reorder.
- **Right-click → Export image…**: PNG, SVG or PDF by extension, the same
  figure re-rendered at 200 dpi (`ExportWorker`, Export menu feedback).
- **Code:** `core/plotting/stats_plot.py` holds pure functions
  (split by ROI, order, panels, grid, matplotlib `Figure` with the object
  API, so it runs on a background thread).
  `HeatmapImageProvider.collectStatsGroups` reads the values through the
  `LightSample` cache and lock (ROI membership from `obs["roi_<name>"]`).
  `AnalysisBridge.requestStatsPlot` → `statsPlotReady` renders off the GUI
  thread; `exportStatsPlot` writes the file.
- Plain matplotlib, no seaborn: `violin` with full-data quartiles gives the
  same look, and seaborn is only a transitive dependency.

## Consequences

- Nothing is stored: the window reads the `.h5ad` files Visual Inspection
  already caches. No config or schema change.
- The violin's density is estimated on a random subsample of 100 000 pixels
  per group (`KDE_MAX_POINTS`); quartiles, min and max use every pixel.
- Not built: statistical tests between groups, log scale, a "(no ROI)"
  group without Use ROI (that is the plain per-sample violin).
