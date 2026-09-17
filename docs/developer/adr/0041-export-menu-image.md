# 41 — Export menu: Image export

**Status:** Accepted

## Context

Third and final Export-menu action ([ADR 39](0039-export-menu-annotation.md)
covers Annotation, [ADR 40](0040-export-menu-integration.md) Integration).
Confirmed with the user during clarification: export a PNG/PDF/SVG image
per sample, for *every* sample (not just currently visible ones),
matching whatever Visual Inspection is currently showing exactly — same
feature/obs selection, colormap, vmin/vmax or autoscale, ROI overlay if
"Show ROIs" is on — with a real tick-labelled colorbar/legend baked into
each image, switching to scientific notation once vmin/vmax's magnitude
exceeds 10³.

## Decision

- `core/plotting/heatmap.py`: new `render_heatmap_figure(adata, *, mode,
  mz, obs_column, categories, layer, colormap, vmin, vmax, rois, title)
  -> matplotlib.figure.Figure`. Reuses `render_feature_heatmap`/
  `render_obs_heatmap`/`render_obs_categories_heatmap` internally to
  build the *exact same* RGBA raster the live GUI tile shows, then
  `imshow`s that array directly rather than re-normalizing independently
  — a colorbar built alongside it is guaranteed to describe the same
  pixels the user actually saw. ROI polygons (from
  `core.plotting.roi.load_sample_rois`) draw straight from their
  `[col, row]` vertices with no coordinate transform:
  `imshow`'s default pixel-center placement already matches
  `pixel_grid_indices`' grid-index space 1:1, and the QML side's own
  `RoiOverlay.qml` places `row` increasing downward the same way
  `imshow(origin="upper")` does — no flip needed either side. Categorical
  `obs` gets a `Patch` legend (`category_color`-consistent) instead of a
  colorbar. Scientific notation
  (`matplotlib.ticker.ScalarFormatter(useMathText=True)`,
  `set_powerlimits((0, 0))`) kicks in once
  `max(abs(vmin), abs(vmax)) > 1000` — confirmed with the user.
- `core/export.py`: `export_visual_inspection_images(db_path,
  dest_folder, image_format, mode, mz, obs_column, layer, colormap,
  vmin, vmax, show_rois)` — every sample's `.h5ad` is read once up
  front (not per-render), a categorical `obs` column's category union is
  computed across every loaded sample before rendering any of them (same
  cross-sample color-consistency rule `HeatmapControlsPanel.
  obsCategoryLegend` already follows for the live GUI), then one
  `render_heatmap_figure` + `fig.savefig(dest_path, bbox_inches="tight")`
  per sample. `vmin`/`vmax` `None` means autoscale-per-sample — the exact
  same convention `HeatmapImageProvider.requestImage` already uses for
  live tiles, not a new one.
- `AnalysisBridge.exportVisualInspectionImages(...)` takes `vmin`/`vmax`
  as the literal `vminToken()`/`vmaxToken()` strings
  (`"auto"` or numeric) `HeatmapControlsPanel` already produces —
  reusing that convention verbatim rather than inventing a second way to
  spell "autoscale."
- **The real gap this ADR had to resolve**: Export's menu bar lives in
  `Main.qml`, always present; Visual Inspection's live control values
  only exist as an actual QML instance while `AnalysisPage.qml`'s
  `visualLoader` (a lazily-`active` `Loader`, see
  [ADR 14](0014-analysis-workspace-lazy-loading.md)) is the current tab.
  "Reuse the live control values" only means something once there's a
  live instance to reuse. Resolved by making `Export > Image…` itself
  conditional on that — `AnalysisPage.qml` exposes
  `visualInspectionActive` (is that tab both selected and the Loader
  Ready) and `visualInspectionSection` (`visualLoader.item` itself) as
  forwarding properties; `VisualInspectionSection.qml` forwards its own
  child `HeatmapControlsPanel` instance the same way
  (`property var controlsPanel: controls`, the exact pattern its own
  `featuresLoading` forward already established). `Main.qml` reads both
  through `stackView.currentItem` (dynamic QML property lookup — no
  static type needed). The menu item is disabled outside that tab, and
  the dialog snapshots every value once, in `onAboutToShow`, into a
  plain JS object — not re-read at `FolderDialog.onAccepted` time, so
  the export's inputs stay pinned to what the user was actually looking
  at when they opened the dialog, unaffected by the modal folder picker
  in between.
- `Main.qml`: `Export > Image…` opens a small custom `Dialog` (PNG/PDF/SVG
  plain buttons, same non-`RadioButton` convention as Integration export)
  that hands off to a `FolderDialog`, reusing ADR 40's exact shape.

## Alternatives considered

- **Re-deriving the raster from scratch for the export** (its own
  `imshow(grid, cmap=..., vmin=..., vmax=...)` on a raw numeric grid,
  independent of `render_feature_heatmap`) instead of colorizing an
  already-built RGBA array. Rejected — two independent color
  computations for "the same thing the user is looking at" is exactly
  the kind of drift this feature explicitly promises not to have; a
  second codepath could disagree with the live tile after either one is
  edited later without the other.
- **Gating Image export only on `currentAnalysis`**, same as Annotation/
  Integration, and just reading whatever `HeatmapControlsPanel` state
  happens to still be around (defaulting to some fixed fallback if the
  tab was never opened). Rejected — silently exporting a colormap/vmin
  the user never actually chose (or never saw) contradicts "matching
  the exact live Visual Inspection display settings," which the user
  confirmed explicitly; disabling the menu item is the honest signal
  that there's nothing live to export yet.

## Consequences

- All three Export actions now share one `ExportWorker`/`exportFinished`/
  `exportFailed` pair (ADR 39) and, for the two folder-based ones, the
  same "custom `Dialog` with plain-Button choices, then a `FolderDialog`"
  shape (ADR 40) — Image export added zero new plumbing beyond its own
  `AnalysisBridge` method and QML dialog.
- `AnalysisPage.qml`/`VisualInspectionSection.qml` now forward two
  properties (`visualInspectionActive`/`visualInspectionSection`,
  `controlsPanel`) purely for `Main.qml`'s benefit — a small, deliberate
  crack in "each page only knows about itself," justified by the
  alternative (Main.qml reaching three files deep via raw `objectName`
  string lookups, a testing-only convention this ADR did not want to
  promote to production runtime code).
