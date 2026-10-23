# 53 — Heatmap scale bar and per-sample pixel size

**Status:** Accepted

## Context

Heatmaps had no physical scale. A reader couldn't tell a 10 µm raster from
a 100 µm one, and exported images needed a scale bar added by hand. The
input is mzML, which, unlike imzML, records no pixel size. The spatial
coordinates in `obsm["spatial"]` are raster positions, not µm. So the pixel
size has to come from the user. It can differ between samples of one
analysis (some acquisitions use a different raster step).

## Decision

- **The user enters the pixel size per sample.** A "Set to all" shortcut
  fills every sample at once. Pixels are assumed square (one edge length),
  the same assumption `core/calibration/units.py` makes.
- **One bar length and one color for every sample**, so tiles stay
  comparable. A sample with no pixel size gets no bar; nothing is guessed.
- **Stored as one JSON value** under the key `scale_bar` in the analysis
  DB's existing `metadata` table (`analysis_db.load_scale_bar` /
  `save_scale_bar`), shaped like
  `{"enabled", "bar_um", "color", "pixel_um": {sample: µm}}`. These are
  display settings, not derived data, so there is no new table, no config
  key, no version bump and no re-run. Keys are sample names, so an imported
  dataset (ADR 49) picks them up automatically.
- **Drawn in QML, not in the rendered tile.** `ZoomableImage` gets
  `scaleBarUm` / `scaleBarPixelUm` / `scaleBarColor`. The bar is parented to
  the Flickable viewport (bottom-right, like its BusyIndicator), so it stays
  put while panning. Its length is `bar_um / pixel_um * effectiveScale`, so
  it follows zoom without re-rendering, and the heatmap cache is unaffected.
  It's used in Visual Inspection's tiles, ROI Design's grid surface and
  Coregistration's MSI pane. It's never shown on H&E surfaces, whose pixels
  aren't MSI pixels.
- **Export > Image has its own "Include scale bar" checkbox**, independent of
  the on-screen switch. It's enabled once any sample has a pixel size and
  is checked by default when the bar is on. `render_heatmap_figure` draws it
  as a matplotlib `Rectangle` plus a label (`scale_bar_spec` converts
  µm to image pixels), with no new dependency.
- **The pixel dimensions (`W × H px`) are shown next to the sample name.**
  They're read from the loaded tile's `sourceSize`: tiles render one image
  pixel per spatial pixel, so no backend call is needed.

## Consequences

- If non-square pixels are ever needed, `pixel_um` values can become
  `{x, y}` pairs; every reader goes through `load_scale_bar` /
  `scale_bar_spec` / `Utils/ScaleBar.js`.
- The size label only appears once a tile has loaded (it shows nothing
  before that).
