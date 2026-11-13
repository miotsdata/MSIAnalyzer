# 70 — Fill blanks and interpolation

**Status:** Accepted

## Context

Heatmaps look blocky, and inside a region where a feature is present some
pixels are empty: the pixel has a spectrum, but the feature wasn't picked
in it, so its value is 0. Users asked for two things: fill those holes, and
a smoother, higher-resolution picture.

## Decision

### Fill blanks: a new layer

- **Rule** (`core.utils.fill_blanks.fill_blanks`): a 0 entry (pixel x
  feature) is replaced by the mean of the *non-zero* neighbors within a
  square of half-size `radius`, when at least `ceil(min_fraction * n)` of
  its `n` neighbors (positions with a spectrum) are non-zero. The guard
  keeps region borders in place: a 0 just outside a border has mostly 0
  neighbors. Positions with no spectrum are never filled and never counted.
- **Computation:** a sparse 0/1 neighbor matrix `A` over the sample's
  pixel grid (`heatmap.pixel_grid_indices`); per block of 256 features,
  `A @ detected` gives the non-zero neighbor counts and `A @ layer` their
  sums. About 10 s for 100k pixels x 2,000 features at 20 % density;
  samples run in a process pool.
- **Output:** `layers["<source>_filled_r<radius>"]`, in every sample file
  and `merged.h5ad` (element-level writes, ADR 50; `merged` stacked by
  `tic_normalization._write_merged_layer`, now without the scale column
  for this case). The source layer is untouched. The name carries source
  and radius; `min_fraction` is in the `commands` row only, and filling
  the same source and radius again replaces the layer.
- **Run stage** `fill_blanks` (config group `fill_blanks`, config v25,
  off by default), right after `normalization` so later stages could use
  it. Untargeted only. With normalization off it fills `raw`. Like
  `spatial_correlation.layer`, `layer` follows `mz_<value>` for a
  reference-m/z normalization while left at `"TIC"`.
- **After the run:** **Analyses → Fill blanks…** (`FillBlanksDialog.qml`,
  `AnalysisBridge.fillBlanks`) on the shared `DatasetTaskWorker`, same
  shape as ADR 61: progress, replace warning, ✕ + confirm to remove
  (`remove_filled_layer`, which refuses any name that isn't a filled
  layer). Pipeline output only.
- **Visual Inspection:** a **Filled** button beside Raw / TIC / Local
  Moran's I / Norm. to m/z, with a combo of the filled layers while one
  is active (the ADR 61 pattern). `feature_<id>_filled_r<n>` counts as
  filled, not as a feature layer.

### Interpolation: display only

- Not a layer: interpolated sub-pixels were never measured, and as data
  they would multiply the pixel count, distort statistics and nFeatures,
  and break ROIs defined on the real grid.
- **Rendering** (`heatmap.upsample_grid`): `scipy.ndimage.zoom(order=1,
  grid_mode=True)` on the value grid before colouring, so the colormap is
  applied to interpolated values, not blended colours. `NaN` (no
  spectrum) is handled by interpolating values and coverage separately;
  a sub-pixel stays `NaN` when less than half its weight is real, so the
  sample edge doesn't darken or move. Categorical `obs` columns are
  repeated (`repeat_grid`), never blended. Feature, numeric/categorical
  obs and local Moran's I tiles all support it.
- **Request id:** a trailing `|i<N>` part (N in 2-4), stripped before
  the positional parts are read. Only Visual Inspection's tiles add it
  (`HeatmapControlsPanel.interpolatedTileSource`); ROI Design and the H&E
  overlay use `tileSource`/`tileTarget` and stay on real pixels.
- **Display:** `ZoomableImage.pixelSubdivision` sizes everything from the
  real grid (`gridSize`), so zoom, ROI overlays, the scale bar and the
  "N x M px" label are unchanged. It follows the factor of the image
  actually loaded (`loadedSubdivision`), so the old tile isn't drawn at
  the new size while the new one renders.
- **Export:** the Image export snapshots the factor. `render_heatmap_figure`
  draws the finer raster over the real grid's extent, so ROI polygons and
  the scale bar keep their real-pixel coordinates.

## Consequences

- Config v25. The GUI stamps the current version on every run (a past
  run's settings still load into New Analysis); a YAML config file used
  from the command line needs `version: 25`, as for every earlier bump.
- A filled layer costs about as much disk as its source layer; HDF5 keeps
  the space after removal (as in ADR 61).
- Not covered: the Overlay tab (and its export) isn't interpolated;
  filled layers aren't offered as Moran's I / feature-correlation source
  layers in the GUI (the config `layer` fields accept the name); imported
  datasets.
