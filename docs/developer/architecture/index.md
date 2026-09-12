# Architecture

This page covers the core pipeline. See [GUI](gui.md) for the desktop app
(PySide6/QML) that sits on top of it.

## The two-database model

```
                 ┌──────────────────────────────────────────┐
   mzML  ──────► │  <project>/parsed/<sample>.db  (RAW)     │  written once, never modified
   XML   ──────► │  metadata, commands                      │  shared by every analysis
                 │  ms1_scans, ms2_scans                    │  in the project
                 │  spatial_pixels, pixel_ms1_scans         │
                 └───────────────┬──────────────────────────┘
                                 │  read-only
                                 ▼
                 ┌──────────────────────────────────────────┐
                 │  <out_dir>/analysis_<run-id>.db          │  one per run
                 │  metadata, commands, samples             │
                 │  aggregated_spectra                      │  avg / centroids / filtered
                 │  features                                │  cross-sample master m/z
                 │  ms2_associations                        │  ┐
                 │  ms2_window_features                     │  ├─ grouper
                 │  feature_ms2_summary                     │  ┘
                 └──────────────────────────────────────────┘
```

The split is the central architectural decision
([ADR 1](../adr/0001-raw-vs-analysis-db-split.md)): a raw database is a
cacheable, shareable, parallel-safe artefact produced by parsing; everything that
depends on a parameter choice belongs to *an analysis*, of which there can be
many over the same parsed files. Raw databases therefore live at the **project**
level (`IOConfig.raw_db_paths()` → `<project_folder>/parsed/<stem>.db` by
default, overridable per sample via `io.db_paths`), never inside an analysis
`out_dir`.

## Data flow

| stage | function | reads | writes |
|---|---|---|---|
| parse | `MzmlParser.parse` | mzML | raw: `metadata`, `commands`, `ms1_scans`, `ms2_scans` |
| map pixels | `map_pixels_to_db` | raw `ms1_scans`, XML df | raw: `spatial_pixels`, `pixel_ms1_scans` |
| average MS1 | `get_average_ms1_spectra` | raw `ms1_scans`, `pixel_ms1_scans` | analysis: `aggregated_spectra` |
| centroids | `detect_ms1_centroids` | previous aggregated spectrum | analysis: `aggregated_spectra` |
| filter | `filter_intensities_mad` / threshold | previous aggregated spectrum | analysis: `aggregated_spectra` |
| align | `align_mz_across_samples` | per-sample filtered peaks | analysis: `features`; `aligned_mzs.csv` |
| group MS2 | `run_grouper` | analysis `features`, `samples`; raw `ms2_scans` | analysis: `ms2_associations`, `ms2_window_features`, `feature_ms2_summary` |
| annotate MS2 | `run_annotation` | analysis `features`, `ms2_associations`; raw `ms2_scans`; library DB | analysis: `annotation_libraries`, `ms2_annotations` |
| AnnData | `create_spatial_adata` | raw `ms1_scans`, `pixel_ms1_scans`, `spatial_pixels`; feature m/z | `<sample>.h5ad` |

## Package layout

```
msianalyzer/core/
  parser/        mzml_parser.py   xml_parser.py
  spectra/       average_spectra.py   mz_tools.py
  annotation/    group_ms2.py       (Stage A — association)
                 spectral_match.py  reverse dot product + coverage
                 annotate.py        (Stage B — library annotation)
  utils/         spectra_pixels_association.py   create_adata.py
                 logging_utils.py   errors.py
  analysis_db.py       analysis-DB schema + provenance helpers
  config/        config.py
  project/       project.py
  plotting/      plotter.py
  run/           run.py          the orchestrator
```

## Orchestration

`Run` (`core/run/run.py`) is the only component that knows the full sequence:

1. `run_core` creates `analysis_<id>.db`, writes analysis-level `metadata`,
   resolves the per-sample raw-DB paths (`config.io.raw_db_paths()`) and
   registers one `samples` row per input pointing at them.
2. A `ProcessPoolExecutor` runs `_process_one_sample` per sample — given its
   `raw_db_path` — : parse + map pixels (raw DB) then average / centroid /
   filter (analysis DB), returning the filtered peak m/z.
3. Back on the main process: align across samples → `features`; run the grouper;
   run the annotator when `config.annotate.library_path` is set; build one
   `.h5ad` per sample.

Two identifiers flow through:

- `run_id` — the **project** UUID, used for raw-DB command bookkeeping and
  cross-run caching of parse / pixel-mapping.
- `analysis_id` — this **run's** id and the analysis-DB identity.

See [provenance & caching](provenance.md).
