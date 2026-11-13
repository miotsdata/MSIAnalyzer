# 71 — Linear scale and percentile color range

**Status:** Accepted

## Context

The normalized layers are stored as `log1p(raw / (ref / median))`. The
colour bar is linear, so what is shown is a log scale of intensity. For an
on/off feature this squeezes the "on" regions together, and with autoscale
much of the colormap goes to the step from 0 ("not detected") to the
faintest detected pixel. A log colour bar would compress a second time.

## Decision

- **Linear display, not a layer.** `expm1` undoes the `log1p` exactly, so
  a linear view is computed while reading values, nothing is written. The
  layer token gets a display suffix, `"TIC@linear"`
  (`heatmap.LINEAR_SUFFIX`): `heatmap._layer_column` and
  `LightSample.ensure_layer` strip it (`split_display_layer`) and read the
  stored layer, and `_layer_column` applies `expm1`. Every consumer that
  takes a layer name — tiles, the autoscale range, image export — follows
  without its own plumbing.
- **Offered only for log1p layers** (`HeatmapControlsPanel.isLogLayer`):
  the run's normalized layer (`TIC` / `mz_<value>`), `feature_<id>`
  (ADR 61) and their filled copies (ADR 70), except `raw_filled_r*`. Not
  for raw, Local Moran's I, or imported datasets (their layers' scale is
  unknown). Switching the scale turns Autoscale back on unless a
  percentile range is active (a percentile range still means something on
  the other scale; an absolute one doesn't).
- **Percentile range.** With Autoscale off, a **Percentiles** checkbox
  makes vmin/vmax percentiles (default 1 and 99). They're computed by the
  same background request as the autoscale range
  (`requestFeatureValueRange` now also returns `pvmin`/`pvmax`), so they
  are recomputed whenever the feature, layer, scale or visible samples
  change. Computed over the **detected (non-zero) pixels, pooled over the
  visible samples**: pooling keeps one shared scale, so samples stay
  comparable, and leaving zeros out keeps the low percentiles of an on/off
  feature from all being 0. The tiles receive the absolute values, so the
  render path is unchanged. Feature mode only (not Local Moran's I or obs
  columns).
- Scale, percentile settings and interpolation are part of Visual
  Inspection's remembered tab state.

## Consequences

- Statistics (ADR 68) still use the stored values; the Linear choice is
  display and export only.
- A percentile range over few detected pixels is coarse; with none it
  falls back to the plain min/max.
