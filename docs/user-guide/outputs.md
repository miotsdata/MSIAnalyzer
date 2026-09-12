# Outputs

Raw databases are written once per project, under
`<project_folder>/parsed/` by default (or wherever `io.db_paths` points).
Everything else lands in the analysis' `io.out_dir`.

## Files

| file | location | one per | what it is |
|---|---|---|---|
| `<sample>.db` | `<project>/parsed/` | sample | raw database — every scan + the pixel grid. Immutable, shared by every analysis. |
| `analysis_<run-id>.db` | `out_dir` | run | every parameter-dependent result of the run |
| `aligned_mzs.csv` | `out_dir` | run | the feature list: consensus m/z + contributing peak index per sample |
| `<sample>.h5ad` | `out_dir` | sample | `AnnData` — pixels × features quantification matrix with spatial coordinates. `.X` and `layers['raw']` are the raw matched intensities; `layers['TIC']` is the TIC-normalized, log1p-compressed version (unless `normalization.enabled` is False) |
| `merged.h5ad` | `out_dir` | run | every sample's `AnnData` concatenated, with a `sample` obs column and the same `raw`/`TIC` layers — built to compute the TIC normalization (needs every sample's pixels for the dataset-wide median) and persisted for cross-sample analyses |
| `<sample>_filtered_ms1.html` | `out_dir` | sample | interactive figure of the filtered MS1 peak list |
| `<sample>_peaks_data.csv` | `out_dir` | sample | the filtered MS1 peak list as `mz,intensity` |
| `summary_report.html` | `out_dir` | run | per-sample counts, feature-overlap UpSet plot, MS2 association (overall + per sample), a recheck of the unassociated MS2 against each sample's pre-filter MS1 peaks, the `precursor_frac` distribution of the **associated** MS2 (overall + per sample), a breakdown of *why* peak-based purity is unscored, and — when a library ran — an **MS2 annotation** section: features by best-hit confidence, best-score distribution, plausible-compounds-per-feature, cross-scan agreement, and top-compound / per-library tables |
| `summary.json` | `out_dir` | run | the same numbers, machine-readable |
| `debug_<run-id>.log` | `<project>/logs/` | run | full-DEBUG trace of the run (always written); plus `run -l PATH` for a user log at the `-v` level |

Regenerate the report from a finished database any time with `msianalyzer report
path/to/analysis_<run-id>.db` — no reprocessing.

## Reading the analysis database

```python
import sqlite3, pandas as pd
con = sqlite3.connect("results/analysis_<run-id>.db")

pd.read_sql("SELECT * FROM features ORDER BY mz", con)
pd.read_sql("SELECT command_name, datetime, arguments FROM commands", con)
```

Key tables (full schema in the
[developer database reference](../developer/architecture/databases.md)):

- **`features`** — one row per feature: `feature_id`, consensus `mz`,
  `members_json` (sample → original peak index).
- **`ms2_associations`** — one row per MS2 scan: which `feature_id` it was
  snapped to (NULL if none), the ppm offset, and `precursor_only`.
- **`feature_ms2_summary`** — one row per feature: how many MS2 scans hit it,
  across how many samples, and how many were `precursor_only` / single-peak.
- **`precursor_purity`** — one row per MS2 scan: `precursor_frac` (a
  peak-detection-free purity proxy, always computable) and `precursor_confirmed`,
  measured against the scan's own parent MS1. A purity signal independent of
  the feature list — prefer `precursor_frac < 0.5` over any feature-density
  count.
- **`feature_ms2_consensus`** — one row per MS2-bearing feature: the single
  chosen scan (`best_sample_id` / `best_scan_id`), its `consensus_score`,
  `precursor_frac`, `n_peaks` and — when a library ran — `best_compound_name` /
  `best_inchikey`.
- **`annotation_libraries`** — one row per spectral library used to annotate
  (`path`, `name`, spectrum / compound counts).
- **`ms2_annotations`** — one row per (MS2 scan, library candidate) comparison:
  the compound, all sub-scores, `rank_ms2` within the scan, `rank_feature` /
  `rank_feature_sample` over the feature's rows (`rank_feature = 1` is the
  feature's single best hit), `rank_scan_feature` / `rank_scan_feature_sample`
  ranking its scans, and (unless disabled) the untouched empirical + library
  spectra for mirror plots. Only written when `annotate.library_path` is set.
- **`feature_compound_scores`** (view) — best library score per
  `(feature, distinct compound)`, with the row it came from. Always reflects
  the current `ms2_annotations`.
- **`aggregated_spectra`** — averaged / centroided / filtered MS1 spectra,
  attributed per sample and per `commands` row.
- **`commands`** — the provenance log: one row per step with its JSON arguments.

## Reading the AnnData

```python
import anndata as ad
adata = ad.read_h5ad("results/<sample>.h5ad")
adata.X                  # pixels × features intensities (raw)
adata.layers["raw"]      # same as .X, explicitly labelled
adata.layers["TIC"]      # TIC-normalized, log1p-compressed
adata.var_names          # feature m/z
adata.obs                # pixel x / y coordinates, obs["tic"] is the per-pixel TIC used for normalization
```

## Re-running

- Re-running an analysis **never rewrites** `<sample>.db`; a new analysis over
  the same samples reuses the parsed databases and skips straight to averaging.
- Re-running the grouper replaces `ms2_associations` and
  `feature_ms2_summary`; `features` and `samples` are untouched.
- Re-running the purity stage replaces every `precursor_purity` row.
- Re-running annotation replaces that library's `ms2_annotations` rows.
- Re-running the consensus stage replaces every `feature_ms2_consensus` row.
- Re-running TIC normalization rewrites every `<sample>.h5ad`'s `raw`/`TIC`
  layers and `merged.h5ad`.
- A stage whose output file or `commands` row already exists is skipped; delete
  the output to force recomputation.
