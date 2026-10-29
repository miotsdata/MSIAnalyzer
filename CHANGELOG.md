# Changelog

Notable changes to MSIAnalyzer, newest first. Design details are in the
ADRs under `docs/developer/adr/`.

## 0.7.1 — 2026-10-29

### Changed
- **Precursor ppm against the feature's weighted m/z** (ADR 65 addendum).
  The ppm error uses the intensity-weighted m/z across samples instead of
  the rounded one. Annotations shows the spread across samples (±ppm), and
  candidates within that spread share a rank ("Tied with").

### Fixed
- **Faster Annotations and analysis opening, especially on network shares**
  (ADR 50 addendum).
  - The Annotations table and the Predict formula feature list build only
    the rows on screen.
  - Each sample's raw-data path is resolved once, not once per feature.
  - The database schema update runs in the background, and is skipped when
    the analysis is already up to date.
  - The Predict formula window loads its features in the background.
  - `.var` annotations are rewritten in the background after formula
    prediction or a post-run target list.
- **No concurrent `.h5ad` writes:** all in-app writes to the same file now
  take turns, and `.var` is replaced in one step, so a reader never finds it
  missing.

## 0.7.0 — 2026-10-29

### Added
- **MS2 cleaning** (ADR 64), a new optional config page.
  - **Background fragments:** fragments found in the MS2 spectra of more
    than a set % of precursors are removed (ppm tolerance and % are
    configurable). The list of shared fragments (in at least 5 % of
    precursors) is always computed, and shown as a table on the Summary
    page and in the Re-annotate dialog's MS2 cleaning tab.
  - **Fragments above the precursor:** peaks above the precursor m/z +
    ppm are removed.
  - Raw data is untouched: spectra are cleaned when they are read.
- **Precursor m/z annotation** (ADR 65). Features can be annotated by
  precursor m/z alone, as well as or instead of MS2 matching, within a ppm
  tolerance.
  - All candidates from all libraries are stored, ranked by |ppm|. The
    reference m/z is the library's stored precursor m/z if it has at least
    4 decimals, otherwise formula + adduct.
  - A feature with only precursor hits is named `formula + adduct (n
    candidates)`; MS2 hits and target-list names win over it.
  - Annotations shows MS2 or Precursor top hits, with the ppm error.
  - Exports and `.var` gain `precursor_name`, `precursor_ppm` and
    `n_precursor_candidates`; every candidate goes into
    `.uns["precursor_candidates"]`.
- **Re-annotate a finished analysis, with history** (ADR 63). **Analyses →
  Re-annotate…** re-runs only the MS2 stages with edited settings, in the
  background. Every annotation state is kept and can be restored or
  deleted.
- **Why an MS2 feature got no hit** (ADR 62): the annotation outcome is
  stored per feature, shown in Annotations, the Summary funnel, the
  Annotation export and `.var`.
- **Annotations in `.h5ad` `.var`.** Every sample `.h5ad` and
  `merged.h5ad` carry the Annotation export columns, refreshed after
  post-run changes and backfilled when an older analysis is opened.
- **Annotations page:**
  - The name search also matches the top-N MS2 and precursor candidates
    (**Search in candidates**), and says which candidate matched.
  - **Show non-annotated** lists every feature, and shows the MS2 of those
    that have one.
  - MS2 hits name the scored scan.
  - The Annotations and Visual Inspection tabs remember their state.
- **Crash trace:** a hard crash writes every thread's stack to
  `crash_<stamp>.txt` in the log folder, and Qt's messages go to the log.

### Changed
- The project file is now `msianalyzer.yml` (no leading dot); an old
  project's `.msianalyzer.yml` is migrated on first open.
- The Summary page is centred and scrolls; Annotations panels are padded
  away from the divider.
- Config version 23. Older configs still load.

### Fixed
- Windows crash opening the MS1 and mirror plots.
- Runs failing on SMB shares when rewriting the project file.

## 0.6.0 — 2026-10-27

### Added
- **Normalize to a feature after the run** (ADR 61). **Analyses → Normalize
  to feature…** searches a feature by name or m/z and normalizes the
  finished analysis to it, on a background thread, with no re-run.
  - The result is a new layer, `feature_<id>`, in every sample file and in
    `merged.h5ad`. The existing layers are kept.
  - Visual Inspection lists it under **Normalized to feature…**, next to
    Raw / TIC.
  - Normalizing to the same feature again replaces its layer. The dialog
    also lists the existing layers, and each one can be removed.

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
