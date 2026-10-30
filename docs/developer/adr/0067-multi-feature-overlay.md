# 67 — Multi-feature overlay

**Status:** Accepted

## Context

Visual Inspection shows one feature at a time with a colormap. To see
whether features colocalize, users compared tiles side by side, or built
composites in a notebook. Feature correlation (ADR 66) says which pairs
correlate, but not where.

## Decision

- **A new tab, Overlay**, after Feature Correlation: up to 6 channels, one
  feature each, drawn in one image per sample, like a multi-channel
  immunofluorescence composite.
- **Per channel:** a color (the classic fluorescence set, magenta and green
  first because that pair stays distinct with red-green color blindness;
  any color through Qt's color dialog), a visibility toggle, and its own
  contrast: Min and Max on one range slider, and gamma.
- **Range slider:** it spans the feature's values in the current layer
  over every sample (`AnalysisBridge.requestOverlayRange`, a background
  read; unlike `requestFeatureValueRange` it is never superseded, since
  several channels ask at once). **Auto** (default) leaves Min/Max empty:
  automatic contrast for each sample on its own. Moving a handle sets one
  Min/Max for every sample, so samples compare; it applies on release,
  since each change re-renders every tile. Changing the layer resets
  every channel to Auto: bounds from one layer mean nothing on another.
- **Gamma:** brightness = (scaled value)^gamma. Below 1 lifts faint
  pixels, above 1 leaves only the bright ones.
- **Blending** (`core.plotting.feature_overlay.composite`): each channel's
  values are scaled to 0-1 by its contrast, raised to its gamma and
  multiplied by its color, then:
  - **Additive** (default): summed and clipped. Overlaps mix (red + green
    = yellow, magenta + green = white), and the order of the channels
    doesn't matter. This answers the "pixels stack on top of each other"
    worry: normal transparency blending depends on drawing order, adding
    light doesn't.
  - **Max**: each color component takes its brightest channel; crisper
    when many channels overlap.
- **Automatic contrast** is per sample: Min is the smallest value, Max the
  99.5th percentile of the values above it. Taking the percentile over
  the detected pixels only keeps a sparse feature (mostly 0) visible; the
  percentile keeps a few hotspot pixels from darkening the rest.
- **Rendering** goes through the existing heatmap image provider, as a new
  target: `<sample>|overlay|<layer>|<mode>|<mz:rrggbb:vmin:vmax:gamma,...>|ds<id>`
  (no `#`: it would start the URL fragment). So it reuses the provider's
  sample cache, background render thread, render cache and dataset
  switching (ADR 49, 50), and the id changes with every setting, so a
  change re-renders. Numpy builds the image directly; no matplotlib.
- `feature_overlay` only parses and composites; `heatmap.render_feature_overlay`
  builds the per-channel grids. That keeps the import one way
  (`heatmap` -> `feature_overlay`), as the architecture test requires.
- **Layer:** the normalized layer or `raw` for the pipeline output; `X` or
  the file's own layers for an imported dataset.
- **From Feature Correlation:** right-click a row, **Add to overlay**: the
  selected feature and that row's feature become two channels, and the
  tab opens.
- Channels, blend mode and layer are kept across tab switches.

## Consequences

- Tiles are the same hand-stacked column as Visual Inspection, with the
  same zoom and scale bar, and the same read-only ROI outlines under a
  **Show ROIs** checkbox.
- **Export > Image…** works from this tab too: the same dialog (PNG, PDF
  or SVG, scale bar, ROIs, defaulting to the tab's "Show ROIs") writes
  `<sample>_overlay.<ext>` per sample (`core.export.export_overlay_images`,
  `heatmap.render_overlay_figure`): the tile's raster, ROIs and scale bar
  drawn by the helper `render_heatmap_figure` also uses, and a legend
  with one color swatch per visible channel.
- QML gotcha met again: the section first declared `property string
  layer`, which shadows `Item.layer`. Qt then warns from the QML loader
  thread and the test harness deadlocks at `setSource` (the same
  happened with a `background` property during the MS2 cleaning work).
  Named `dataLayer`.
