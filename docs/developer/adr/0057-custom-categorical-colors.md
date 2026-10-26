# 57 — Custom colors for categorical obs columns

**Status:** Accepted

## Context

A discrete `obs` column (an ROI, a cluster, polarity...) is colored by
`heatmap.category_color(index)`: the tab20 palette by the category's
position in the sorted union of categories across every sample. Users want
to pick their own colors (hex, or a color wheel), e.g. to match a figure
style or make two neighbouring clusters distinguishable.

## Decision

- **Clicking a swatch in Visual Inspection's "Categories" legend** opens
  Qt's own `ColorDialog` (`DontUseNativeDialog`, so the hue wheel and hex
  field look the same on every OS). "Reset colors" appears next to the
  legend title when any category of the column is customised.
- **Stored analysis-wide, per column and category**, as JSON in the
  analysis `metadata` table under `obs_category_colors`:
  `{obs_column: {category: "#rrggbb"}}`. Same pattern as the scale bar
  settings (ADR 53): no new table, no schema change, existing analyses get
  it without a re-run. One set per analysis, shared by imported datasets
  (ADR 49).
- **`heatmap.category_colors(categories, overrides)`** is the one place
  overrides and defaults combine. A category without an override keeps
  its positional default, so customising one category never shifts the
  others.
- **The color travels in the tile request id**: the categories field is
  `cat1=rrggbb,cat2=rrggbb` (`heatmap.parse_category_colors`). A color
  change is a new id, so the tile cache never needs invalidating. No `#`:
  in a QML `Image.source` URL it starts the fragment, which never reaches
  the provider. An entry without `=rrggbb` falls back to the default.
- All four consumers follow: GUI tiles (and therefore the ROI Design and
  coregistration windows, which reuse `tileTarget`), the legend
  (`getObsCategories`, which also returns `custom`), the H&E overlay, and
  image export (`core/export.py`, legend included).

## Alternatives

- **A dedicated `obs_category_colors` table:** proposed first; the
  metadata JSON does the same job with less code and no schema change.
- **scanpy's `uns["<col>_colors"]` in each `.h5ad`:** interoperable, but
  aligned to each file's own category order, while the GUI colors by the
  union across samples; it would also mean writing every sample's file
  plus `merged.h5ad` per color change. Could still be read as defaults for
  imported datasets if that is ever wanted.
- **Invalidating the tile cache on a color change** instead of putting the
  color in the id: more code, and easy to miss in one of the consumers.

## Consequences

- Category names containing `,` or `|` still break the tile id, as before
  (unchanged by this ADR).
- No config or pipeline change; no re-run needed.
