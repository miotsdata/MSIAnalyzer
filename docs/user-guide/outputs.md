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
| `<sample>.h5ad` | `out_dir` | sample | `AnnData` — pixels × features quantification matrix with spatial coordinates |
| `<sample>_filtered_ms1.html` | `out_dir` | sample | interactive figure of the filtered MS1 peak list |
| `<sample>_peaks_data.csv` | `out_dir` | sample | the filtered MS1 peak list as `mz,intensity` |
| `debug_<run-id>.log` | `<project>/logs/` | run | full-DEBUG trace of the run (always written); plus `run -l PATH` for a user log at the `-v` level |

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
  snapped to (NULL if none), the ppm offset, the isolation-window flags, and
  `precursor_only`.
- **`ms2_window_features`** — one row per (MS2 scan, feature inside its isolation
  window), with the per-feature ppm difference; `is_primary = 1` marks the
  chosen one.
- **`feature_ms2_summary`** — one row per feature: how many MS2 scans hit it,
  across how many samples, and how many were `precursor_only` / single-peak /
  chimeric.
- **`annotation_libraries`** — one row per spectral library used to annotate
  (`path`, `name`, spectrum / compound counts).
- **`ms2_annotations`** — one row per (MS2 scan, library candidate) comparison:
  the compound, all sub-scores, `rank` within the scan, `rank_feature` across the
  feature, and (unless disabled) the filtered empirical + library spectra for
  mirror plots. Only written when `annotate.library_path` is set.
- **`aggregated_spectra`** — averaged / centroided / filtered MS1 spectra,
  attributed per sample and per `commands` row.
- **`commands`** — the provenance log: one row per step with its JSON arguments.

## Reading the AnnData

```python
import anndata as ad
adata = ad.read_h5ad("results/<sample>.h5ad")
adata.X          # pixels × features intensities
adata.var_names  # feature m/z
adata.obs        # pixel x / y coordinates
```

## Re-running

- Re-running an analysis **never rewrites** `<sample>.db`; a new analysis over
  the same samples reuses the parsed databases and skips straight to averaging.
- Re-running the grouper replaces `ms2_associations`, `ms2_window_features` and
  `feature_ms2_summary`; `features` and `samples` are untouched.
- Re-running annotation replaces that library's `ms2_annotations` rows.
- A stage whose output file or `commands` row already exists is skipped; delete
  the output to force recomputation.
