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
MS2 scan to a feature. It writes three tables — `ms2_associations`,
`ms2_window_features`, `feature_ms2_summary` — and drops nothing. Controlled by
the `group_ms2.*` settings. Full detail in [MS2 annotation](ms2-annotation.md).

## Stage 8 — Precursor ion purity (→ analysis DB)

`run_precursor_purity()` scores every MS2 scan's isolation window against its own
parent MS1 scan (and the next MS1 on the same raster line): `purity`,
`n_peaks_in_window`, `runner_up_rel_int` per scan, in `precursor_purity`. A
feature-list-free chimericity signal that stays meaningful on large runs.
Controlled by `purity.*`; set `purity.enabled: false` to skip. Full detail in
[MS2 annotation](ms2-annotation.md).

## Stage 9 — Annotate MS2 against a library (→ analysis DB)

`run_annotation()` runs only when `annotate.library_path` is set. For every
feature that carries MS2 it pulls library candidates near the feature m/z
(`annotate.candidate_ppm`), scores each scan against each candidate with a
coverage-aware reverse dot product, and writes `annotation_libraries` +
`ms2_annotations` (one row per scored candidate, ranked). Controlled by the
`annotate.*` settings. Full detail in [MS2 annotation](ms2-annotation.md).

## Stage 10 — Spatial AnnData per sample

`create_spatial_adata()` quantifies every feature m/z across each pixel's MS1
spectra (`h5ad.integration_ppm`, `h5ad.scan_handling`) and writes
`<sample>.h5ad`.

## What you end up with

```
<project_folder>/
  parsed/
    <sample>.db               raw DB (per sample, immutable, shared by all analyses)
  <out_dir>/                   one per analysis
    <sample>.h5ad             spatial matrix (per sample)
    <sample>_filtered_ms1.html  spectrum figure
    <sample>_peaks_data.csv   filtered peak list
    aligned_mzs.csv           the feature list
    analysis_<run-id>.db      everything parameter-dependent
```
