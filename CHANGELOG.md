# Changelog

Notable changes to MSIAnalyzer, newest first. Design details are in the
ADRs under `docs/developer/adr/`.

## Unreleased

### Added
- **Normalize to a feature after the run** (ADR 61). **Analyses → Normalize
  to feature…** searches a feature by name or m/z and normalizes the
  finished analysis to it, on a background thread, with no re-run.
  - The result is a new layer, `feature_<id>`, in every sample file and in
    `merged.h5ad`. The existing layers are kept.
  - Visual Inspection lists it under **Normalized to feature…**, next to
    Raw / TIC.
  - Normalizing to the same feature again replaces its layer.

## 0.5.0 — 2026-10-26

### Added
- **Target list after the run** (ADR 60). **Analyses → Match target list…**
  matches a target list against a finished analysis, with no re-run.
  - It takes the same inputs as the config's `target_list` (files,
    polarity, adducts, ppm tolerance), pre-filled from the analysis.
  - A review table shows each match with the feature's current name. You
    choose per row whether the target name labels the feature; at most one
    target name per feature.
  - **Apply** saves the chosen names, and they show up everywhere.
    **Remove post-run matches** undoes them; the run's own matches are
    kept.
  - Compounds no feature matched are listed as "not detected".
- **Targets given by m/z.** Target-list files accept an `mz` (or `m/z`)
  column, next to or instead of `formula`, in the pipeline and after the
  run.
  - A row with an m/z is searched at that m/z only, with no adducts. **The
    m/z wins over the formula** when a row has both.
  - `inchikey` is now optional.
- **Moran's I after the run** (ADR 49). Spatial Correlation's **Compute
  Moran's I** also works on the pipeline output, not only on imported
  datasets. It computes global and/or local Moran's I, one sample at a
  time, on the normalized layer or raw `X`, and asks before overwriting
  existing scores.
- **Show only some categories** (ADR 57). Categorical obs columns have a
  checkbox per legend entry plus All/None. A hidden category is painted
  black; this is temporary and resets on a column or dataset change.
  Image export offers **"Only the categories shown"**.

### Fixed
- A categorical obs column picked right after the default numeric `tic`
  showed black tiles and no legend until another column was picked.

## 0.4.1 — 2026-10-26

### Fixed
- **Projects open from anywhere** (ADR 59). A project folder now works
  after being moved, or when opened from a NAS on both Linux and Windows.
  - Paths are stored relative to the project root: output folders,
    configs, parsed raw DBs and so H&E images. An input file is stored
    relative when it's inside the project, absolute otherwise.
  - Paths stored by older versions are found again inside the project you
    opened. The project you opened always wins over an old location that
    still exists.
- An analysis whose output folder can't be found shows "Output folder not
  found" instead of empty tabs, and no longer crashes on the MS1 tab.

## 0.4.0 — 2026-10-26

### Added
- **Custom category colors** (ADR 57). Click a swatch in Visual
  Inspection's category legend to pick a color (hex or color wheel). The
  color is saved per analysis and used in image exports; "Reset colors"
  restores the defaults.
- **Select, move, copy and paste ROIs** in ROI Design (ADR 58).
  - Click an ROI to select it (the smallest one, when ROIs are nested).
    Drag it or nudge it with the arrow keys (Shift = 10 px). Ctrl+Z undoes
    moves, one step at a time.
  - Ctrl+C / Ctrl+V copies an ROI, to the same or another sample.

## 0.3.1 — 2026-10-26

### Fixed
- The m/z search matches by prefix: typing `179` finds every feature whose
  m/z starts with 179, without needing the decimals.

## Earlier versions

See the `v0.3.0` and older tags in the git history.
