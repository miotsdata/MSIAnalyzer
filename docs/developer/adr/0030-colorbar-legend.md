# 30 — Colorbar legend for the vmin/vmax color scale

**Status:** Accepted

## Context

Visual Inspection's vmin/vmax controls (`HeatmapControlsPanel.qml`) showed
the numeric bounds as text (`vminValueLabel`/`vmaxValueLabel`) but nothing
showing what the colormap between those bounds actually looks like —
reported as missing: "the legend of the colorbar is lacking."

The tiles themselves already render through one code path
(`core/plotting/heatmap.py`'s `_colorize_grid`, `matplotlib.colormaps[colormap]`)
shared by `render_feature_heatmap`/`render_obs_heatmap`, served to QML via
`HeatmapImageProvider`'s `image://heatmap/...` image provider (registered
once in `gui/main.py`). The legend needed to reuse that exact resolution —
a hand-rolled QML gradient (e.g. `Gradient` stops approximating each
colormap) would drift from what the tiles actually show the moment a new
colormap is added to the picker, or if matplotlib's own colormap data
changes across a version bump.

## Decision

Extend the existing image-provider pattern rather than build a parallel one:

- `core/plotting/heatmap.py`: `render_colorbar(colormap, width=256,
  height=16)` — a flat `(height, width, 4)` `uint8` RGBA gradient strip,
  built from the same `matplotlib.colormaps[colormap]` call the tiles use,
  just applied to `np.linspace(0, 1, width)` instead of real per-pixel data.
- `HeatmapImageProvider.requestImage`: a third request-id shape,
  `colorbar|<colormap>` (vs. `sampleName|mz|...` / `sampleName|obs:...`) —
  dispatched before `_load_adata` even runs, since there's no sample behind
  it at all.
- `HeatmapControlsPanel.qml`: `Image { source:
  "image://heatmap/colorbar|" + controlsFlickable.colormap }` between two
  literal `"min"`/`"max"` labels (not the numeric bounds — those are
  already shown above), gated on the same `showsColorScale` condition the
  vmin/vmax controls already use.

## Alternatives considered

- **A QML-only gradient** (`Gradient`/`GradientStop` per colormap,
  hand-picked to approximate viridis/magma/etc.). Rejected — six colormaps
  to hand-tune, each one an independent thing to keep in sync with
  matplotlib's actual data by hand; the image-provider route makes that
  sync automatic instead.
- **A separate image provider** just for the colorbar. Rejected — no
  reason to duplicate `HeatmapImageProvider`'s registration/lifecycle for
  one more `image://` scheme when the existing one already dispatches on
  the request id's shape.

## Consequences

- Adding a colormap to the picker (`colormapCombo`'s model list) makes the
  legend correct for it automatically — nothing colorbar-specific to
  update.
- The legend has no sample data dependency, so it renders identically
  regardless of which samples are visible/hidden — it's purely a function
  of the selected colormap.
