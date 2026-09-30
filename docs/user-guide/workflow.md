# The workflow

A run (`msianalyzer.core.run.run.Run`) executes the stages below over a set of
samples. Per-sample stages run in parallel worker processes; cross-sample stages
run once. Any stage whose output already exists is skipped
([provenance & caching](../developer/architecture/provenance.md)).

## Inputs

Per sample: one **mzML** file and one **raster XML** file. The run also needs a
**project folder** (a directory containing `.msianalyzer.yml`) and an **output
directory**.

## Stage 1 — Parse (→ raw DB)

`MzmlParser().parse()` streams the mzML and writes the sample's **raw database**:

- `metadata` — instrument info,
- `commands` — a `parse` provenance row,
- `ms1_scans`, `ms2_scans` — every scan, with m/z and intensity arrays stored as
  compressed `float32` blobs.

The raw database lives **once per project** — by default
`<project_folder>/parsed/<sample>.db`, or an explicit path per sample via
`io.db_paths`. It is **immutable** afterwards, parse takes no configurable
parameters, and every analysis in the project reuses it. A second analysis over
the same file finds the `parse` `commands` row already present and skips
straight to averaging.

## Stage 2 — Map pixels (→ raw DB)

`parse_raster_xml()` reads the pixel time windows; `map_pixels_to_db()` writes
`spatial_pixels` and `pixel_ms1_scans` into the raw DB by range-joining scan
retention time against each pixel window. The pixel grid is treated as an
objective fact about the acquisition, so it lives in the raw DB.

## Stage 3 — Average MS1 (→ analysis DB)

`get_average_ms1_spectra()` streams the MS1 scans that belong to a valid pixel,
bins them onto a fixed m/z grid (`ms1.bin_width`, `ms1.min_mz`, `ms1.max_mz`) and
averages. The result is stored in `aggregated_spectra` in `analysis_<id>.db`,
attributed to the sample and to a `commands` row.

## Stage 4 — Detect centroids (→ analysis DB)

`detect_ms1_centroids()` estimates a baseline (`centroid.baseline_method` and
friends), finds peaks above it, and merges peaks closer than
`centroid.merge_ppm`. Output: a centroided peak list in `aggregated_spectra`.

## Stage 5 — Filter peaks (→ analysis DB)

Either a median-absolute-deviation threshold (`peak.filter_mad`,
`peak.filter_mad_log`, `peak.filter_mad_nmads`) or a flat height cut
(`peak.peak_height_threshold`). Output: the filtered peak list, plus a
per-sample spectrum figure (`<sample>_filtered_ms1.html`) and CSV
(`<sample>_peaks_data.csv`).

## Stage 6 — Align m/z across samples (→ analysis DB)

`align_mz_across_samples()` clusters the per-sample filtered peaks within
`align.align_ppm` and rounds each consensus m/z to `align.mz_decimals`. Output:

- `features` table in the analysis DB (consensus m/z + which original peak index
  each sample contributed), and
- `aligned_mzs.csv` for inspection.

## Stage 7 — Associate MS2 with features (→ analysis DB)

`run_grouper()` reads `features` and every sample's `ms2_scans`, and snaps each
MS2 scan to a feature. It writes two tables — `ms2_associations`,
`feature_ms2_summary` — and drops nothing. Controlled by
the `group_ms2.*` settings. Full detail in [MS2 annotation](ms2-annotation.md).

## Stage 8 — Precursor ion purity (→ analysis DB)

`run_precursor_purity()` scores every MS2 scan's isolation window against its own
parent MS1 scan: `precursor_frac` (peak-detection-free, always computable) and
`precursor_confirmed` per scan, in `precursor_purity`. A feature-list-free
purity signal that stays meaningful on large runs. Samples are scored one
process per sample (`purity.n_workers`). Controlled by `purity.*`; set
`purity.enabled: false` to skip. Full detail in
[MS2 annotation](ms2-annotation.md).

## Stage 9 — Annotate MS2 against a library (→ analysis DB)

`run_annotation()` runs only when `annotate.library_path` is set. For every
feature that carries MS2 it pulls library candidates near the feature m/z
(`annotate.candidate_ppm`), scores every associated scan unconditionally
against each candidate with a coverage-aware reverse dot product, and writes
`annotation_libraries` + `ms2_annotations` (one row per scored candidate,
ranked). Each row also carries the scan's `precursor_frac`;
`annotate.min_precursor_frac` skips known-low-purity scans. Full detail in
[MS2 annotation](ms2-annotation.md).

## Stage 10 — MS2 consensus (→ analysis DB)

`run_consensus()` picks one representative MS2 scan per feature —
`consensus_score = best library score × precursor_frac term × peak term` — into
`feature_ms2_consensus`. Works library-free and purity-free. Controlled by
`consensus.*`; set `consensus.enabled: false` to skip.

## Stage 11 — Spatial AnnData per sample

`create_spatial_adata()` quantifies every feature m/z across each pixel's MS1
spectra (`h5ad.integration_ppm`, `h5ad.scan_handling`) and writes
`<sample>.h5ad`.

## Stage 12 — TIC normalization

`run_tic_normalization()` computes each pixel's TIC relative to the
dataset-wide median TIC (across every pixel, every sample) and uses it to
add `layers['raw']` (untouched) and `layers['TIC']` (normalized,
log1p-compressed) to every `<sample>.h5ad`, plus a `merged.h5ad` — all
samples concatenated with a `sample` obs column — needed to compute the
median and kept for future cross-sample analyses. Controlled by
`normalization.*`; set `normalization.enabled: false` to skip.

## Stage 12.5 — Spatial correlation (opt-in)

Off by default (`spatial_correlation.enabled: false`). When enabled,
`core.spatial.moran.compute_spatial_autocorr()` runs per sample, after TIC
normalization: builds a spatial neighbor graph over that sample's pixel
grid (`squidpy.gr.spatial_neighbors_grid`) and computes Moran's I for every
feature (`squidpy.gr.spatial_autocorr`) against `spatial_correlation.layer`
(`"TIC"` by default) — how much a feature's pixel values cluster spatially
rather than scattering at random, computed independently per sample (never
pooled across samples). Results are written directly into that sample's
own `.h5ad` (`adata.uns["moranI"]`, squidpy's own convention) — no new
analysis-database table. See [Concepts](concepts.md) for what the score
means and [ADR 48](../developer/adr/0048-spatial-correlation-morans-i.md)
for the full computation details.

Visual Inspection separately offers **Local Moran's I** as an on-demand
heatmap layer — a *per-pixel* version of the same score, computed the
moment it's selected. It doesn't need this pipeline stage enabled at all
(it builds its own neighbor graph on the fly), so it's available
regardless of `spatial_correlation.enabled`. See [Concepts](concepts.md).

## Stage 13 — Summary report (→ out_dir)

`build_summary_report()` writes `summary_report.html` + `summary.json`:
per-sample scan / pixel / peak / feature counts, a feature-overlap UpSet plot,
MS2 association (overall donut + a 100%-stacked bar per sample), a recheck of
the unassociated MS2 against each sample's *pre-filter* MS1 peaks (how many miss
only because peak filtering dropped the peak), the `precursor_frac` distribution
of the **associated** MS2 (overall + per sample — the spectra that feed the
library search, with all-MS2 as faint context), and a per-sample breakdown of
why the peak-based purity is unscored for the rest. When a library ran it also
gets an **MS2 annotation** section — features by best-hit confidence, the
best-score distribution, how many plausible compounds each feature has,
whether a feature's repeat scans agree on the ID, and top-compound /
per-library tables. Controlled by `report.*`; regenerate it any time with
`msianalyzer report <analysis-db>`.

## What you end up with

```
<project_folder>/
  parsed/
    <sample>.db               raw DB (per sample, immutable, shared by all analyses)
  <out_dir>/                   one per analysis
    <sample>.h5ad             spatial matrix (per sample), raw + TIC-normalized layers
    <sample>_filtered_ms1.html  spectrum figure
    <sample>_peaks_data.csv   filtered peak list
    aligned_mzs.csv           the feature list
    analysis_<run-id>.db      everything parameter-dependent
    merged.h5ad               every sample concatenated (raw + TIC layers, sample obs column)
    imported/<name>/<sample>.h5ad  an imported analyzed .h5ad, split per sample (see below)
    summary_report.html       per-sample counts, feature overlap, MS2 + purity plots
    summary.json              the same numbers, machine-readable
```

## Bringing notebook results back (imported datasets)

After a run, you may analyze `merged.h5ad` (or one sample's `.h5ad`) in
your own notebook: QC, dimensionality reduction, clustering and so on.
To look at the result inside the app, open the analysis and choose
**Analyses → Import analyzed .h5ad…**:

1. Pick the file. It's read in the background without loading its
   intensity matrix, so even multi-GB files open quickly.
2. Give the import a name. Check the **sample mapping**: rows named like
   an analysis sample are pre-filled, and anything left on *(skip)* isn't
   imported. For a merged file, the sample comes from its `sample` obs
   column. You can pick another column if you rebuilt it.
3. Click **Import**. The file is split into one copy per sample, with a
   progress bar.

A **Dataset** switcher then appears in the workspace header, listing
*Pipeline output* and your imports. It changes what **Visual Inspection**
and **Spatial Correlation** show. Summary, MS1 Spectra and Annotations
always come from the analysis itself.

What to keep in your notebook so the import works:

- **Each pixel's identity.** Keep the obs names as MSIAnalyzer wrote them
  (`<pixel>-<sample>` in `merged.h5ad`), *or* keep the `x`/`y` columns or
  `obsm["spatial"]`. Filtering pixels out is fine. Pixels that don't
  belong to their sample make the import stop with an error.
- **Each feature's m/z.** Keep `var["mz"]`, or var names of the form
  `mz_<value>`. Dropping features is fine; they just disappear from the
  feature list.

With an imported dataset active:

- New obs columns (e.g. `leiden`) appear in Visual Inspection's
  **Obs column** mode.
- The **Layer** picker lists `X` (the file's own main matrix) plus its
  layers.
- An H&E image registered for a sample applies automatically, and stays
  aligned even if QC removed whole edge rows or columns.
- **ROIs** you draw are saved in the imported copy **and written back
  into your own file** as a boolean obs column `roi_<name>`. Nothing else
  in your file is modified. If the file was moved, or is open in a
  notebook, the ROI is still saved in the app and a warning tells you it
  wasn't written back.
- **Spatial Correlation** has a **Compute Moran's I** button (global
  and/or local, on the layer you choose). An import has no pipeline run,
  so this replaces the `spatial_correlation` config step.

Removing an imported dataset (the ✕ next to the switcher) deletes its
per-sample copies, never your original file.

