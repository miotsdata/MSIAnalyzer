# Core modules

Responsibility and public surface of each `msianalyzer.core` module. Signatures
are in the [API reference](../api_reference/index.md).

## `parser/mzml_parser.py`

Streams an mzML file into the **raw** database. No GUI, no pixel/spatial logic.

- `MzmlParser.parse(mzml_path, ms1_db_path) -> MzmlFile` — writes `metadata`,
  a `parse` `commands` row, and every `ms1_scans` / `ms2_scans` row.
- `create_raw_schema(con)` / `init_raw_db(path) -> Connection` — the single
  source of truth for the raw schema (WAL, foreign keys on).
- `array_to_blob` / `blob_to_array` — zlib-compressed `float32` (de)serialisation
  for the m/z and intensity arrays. Reused by every downstream reader.
- `log_command(db_path, command_name, arguments, run_id)` — append a provenance
  row (generic; the analysis DB has its own variant).

MS2 rows carry `precursor_mz`, `precursor_charge`, `precursor_intensity`,
`isolation_window_target/_lower/_upper`, `collision_energy`, `filter_string`.

## `parser/xml_parser.py`

- `parse_raster_xml(xml_path) -> (DataFrame, ...)` — AP-MALDI raster XML into
  per-pixel `[t_start, t_end]` time windows.

## `utils/spectra_pixels_association.py`

- `map_pixels_to_db(db_path, df_pixels)` — creates `spatial_pixels` and
  `pixel_ms1_scans` in the raw DB by range-joining `ms1_scans.rt` against each
  pixel window.

## `spectra/average_spectra.py`

Pure spectral maths plus analysis-DB read/write for the aggregated spectra.

- `get_average_ms1_spectra(db_path, chunk_size, bin_width, min_mz, max_mz)` —
  streams pixel-mapped MS1 scans, bins and averages.
- `detect_ms1_centroids(bin_centers, mean_intensities, **centroid_cfg)` —
  baseline estimation, peak picking, ppm merge.
- `filter_intensities_mad(mz, intensity, log, n_mads)` — MAD intensity threshold.
- `save_aggregated_spectra` / `load_aggregated_spectra` — round-trip a spectrum
  to `aggregated_spectra`, keyed by `run_id` + `command_id` + `sample_id`.

Private helpers (`_estimate_baseline`, `_merge_peaks_ppm`,
`_infer_decimal_places`, …) are pure and independently testable.

## `spectra/mz_tools.py`

- `align_mz_across_samples(mz_arrays, sample_names, align_ppm, mz_decimals)
  -> DataFrame` — clusters peaks across samples within `align_ppm`; returns a
  frame indexed by consensus m/z with one column per sample holding the original
  peak index (nullable `Int64`). Pure; persistence is done by
  `analysis_db.save_features`.

## `analysis_db.py`

Schema and provenance helpers for `analysis_<id>.db`. See the
[database reference](databases.md).

- `analysis_db_path(out_dir, run_id, override)` ,
  `init_analysis_db(path) -> Connection` , `create_analysis_schema(con)` — the
  single source of truth for the analysis schema, **including the grouper and
  annotator tables** ([ADR 6](../adr/0006-schema-single-source-of-truth.md)).
- `register_sample`, `log_command`, `is_command_already_run`, `write_metadata`.
- `attach_raw(con, raw_db_path, alias)` — `ATTACH DATABASE` for cross-DB reads.
- `save_features` / `load_features` — round-trip the aligned frame to `features`.

## `annotation/group_ms2.py`

Stage A of annotation: snap each MS2 scan to a feature. Detail in
[MS2 annotation](../../user-guide/ms2-annotation.md) and
[ADR 2](../adr/0002-ms2-feature-association-design.md).

- Pure: `associate_scan`, `group_ms2`, `summarize_features`,
  `detect_precursor_only`, `ppm_between`.
- Data classes: `WindowFeature`, `ScanAssociation`, `FeatureMs2Summary`,
  `GroupingResult`.
- IO: `persist_grouping(db_path, result, command_id)` (calls
  `create_analysis_schema`, then replace-and-insert),
  `run_grouper(analysis_db_path, *, assoc_ppm, align_ppm, include_unmatched, …)`
  — the orchestration entry point used by `run.py`.

## `annotation/precursor_purity.py`

Stage A′ of annotation: per-MS2 isolation-window purity, measured against the
scan's parent MS1 scan (and the next MS1 on the same raster line), independent of
the feature list. Detail in
[MS2 annotation](../../user-guide/ms2-annotation.md) and
[ADR 8](../adr/0008-precursor-ion-purity.md).

- Pure (arrays only): `window_bounds`, `detect_window_peaks`, `score_window`,
  `interpolate_purity`, `compute_scan_purity`, `ppm_between`.
- Raw-DB readers: `infer_raster_geometry(con)` (infers the fast raster axis +
  inter-pixel gap tolerance), `SampleScanIndex(con)` (preloads MS1 rt/polarity,
  the scan→pixel map and pixel geometry once per sample so the hot loop does no
  per-scan SQL), `resolve_parent_next(index, ms2_row, geom, …)` (parent MS1 + a
  `same_pixel` / `same_line` next MS1, or `parent_only`; also accepts a bare
  connection for one-off calls).
- Data classes: `RasterGeometry`, `WindowPurity`, `ResolvedScans`, `PurityRow`,
  `PurityResult`.
- IO: `persist_purity(db_path, result, command_id)` (replace-and-insert),
  `run_precursor_purity(analysis_db_path, config, *, command_id) -> PurityResult`
  — the orchestration entry point used by `run.py`.

## `annotation/consensus.py`

Stage A″ of annotation: pick one representative MS2 scan per feature by folding
the best library score, precursor `purity` and fragment-peak count into one
`consensus_score`. Detail in
[MS2 annotation](../../user-guide/ms2-annotation.md) and
[ADR 9](../adr/0009-consume-purity-and-consensus.md).

- Pure: `peak_term`, `purity_term`, `consensus_score`, `pick_feature`,
  `build_consensus`.
- Data classes: `ScanStat`, `ConsensusRow`, `ConsensusResult`.
- IO: `persist_consensus(db_path, result, command_id)` (replace-and-insert),
  `run_consensus(analysis_db_path, config, *, command_id) -> ConsensusResult` —
  reads `ms2_associations` + `precursor_purity` + `ms2_annotations` (rank 1),
  used by `run.py`. Works with annotation and/or purity absent.

## `report/summary.py`

End-of-run reporting: reads a finished analysis database (and each sample's raw
database, read-only) and writes `summary_report.html` + `summary.json`. Detail
in [Outputs](../../user-guide/outputs.md).

- Pure stats: `per_sample_counts`, `feature_membership`, `overlap_combos`,
  `ms2_summary`, `per_sample_ms2`, `per_sample_purity`, `purity_unscored`
  (classifies every `precursor_purity` row: scored / faint precursor not in MS1
  / parent MS1 off-pixel [flyback] / no precursor m/z / no parent),
  `unassociated_recheck` (re-tests unassociated MS2 against the sample's
  *pre-filter* centroids — `detect_ms1_centroids` output — with the grouper's
  own `assoc_ppm`), `collect_stats` → `SummaryStats`.
- Figures (Plotly, no new dependency; horizontal legends sit above the plot,
  counts use a thousands separator): `figure_per_sample`, `figure_overlap_upset`
  (hand-rolled UpSet), `figure_ms2_association` (overall donut) +
  `figure_ms2_association_per_sample` (100%-stacked bar),
  `figure_unassociated_recheck`, `figure_purity` (overall histogram) +
  `figure_purity_per_sample` (violin per sample) + `figure_purity_unscored`
  (scored-vs-reason stacked bar).
- Entry point: `build_summary_report(analysis_db_path, raw_db_paths, out_dir,
  *, config)` — called by `run.py` and by the `msianalyzer report` CLI.

## `annotation/spectral_match.py`

- `reverse_dot_product(...) -> MatchResult` — MSDial-style coverage-aware reverse
  dot product with ppm-tolerant peak alignment. `MatchResult` carries the
  noise-filtered, normalised spectra of **both** sides (`filtered_mz` /
  `lib_filtered_mz` …) so the annotator can persist them for mirror plots. Also
  used by `plotting/plotter.py` (`_align_peaks`).

## `annotation/annotate.py`

Stage B of annotation: score each associated MS2 scan against spectral libraries.
Detail in [MS2 annotation](../../user-guide/ms2-annotation.md) and
[ADR 7](../adr/0007-library-annotation-design.md).

- Pure: `normalize_polarity`, `normalize_library_paths`,
  `score_scan_against_candidates`, `rank_scan_rows`, `assign_rank_feature`,
  `annotate_feature`.
- Data classes: `Candidate`, `LibraryInfo`, `AnnotationRow`, `AnnotationResult`
  (`AnnotationResult.libraries` is a list).
- IO: `load_library(path)`, `persist_annotations(db_path, rows, library_ids, …)`,
  `run_annotation(analysis_db_path, config, *, command_id) -> AnnotationResult` —
  the orchestration entry point used by `run.py`. `config.library_path` is one
  path or a list; each library gets an `annotation_libraries` row, a worker
  loads them all once (`ProcessPoolExecutor` initializer) and per feature pools
  the candidates from every library before ranking.

## `utils/create_adata.py`

- `create_spatial_adata(db_path, target_mz_set, project_id, integration_ppm,
  batch_size, scan_handling, n_workers) -> AnnData` — quantifies target m/z
  across each pixel's MS1 spectra. Reads only the raw DB; returns an `AnnData`
  (persisted by the caller as a sidecar `.h5ad`).

## `utils/logging_utils.py`

- `configure_logging(*, level, log_file, debug_log_dir, run_id)` — console + an
  optional user file (both at `level`) + an always-DEBUG file
  `debug_<run_id>.log`. `level` accepts a name or a number (`resolve_log_level`).
- `@log_call(*, level=DEBUG, source=None)` — decorator emitting `start` / `end
  (N ms)` records (and `fail` + traceback on exception) on the wrapped
  function's own module logger. `source="db_path"` copies that argument onto
  `record.source_file`.
- `worker_logging()` — context manager: runs a `QueueListener` over the parent's
  root handlers and yields `(log_queue, initializer)` for a
  `ProcessPoolExecutor`, so worker logs reach the parent's handlers.
- See [Logging](../logging.md).

## `utils/db.py`

- `safe_execute` / `safe_executemany(con, sql, params/rows, *, table, logger)` —
  run the write; on a `sqlite3.Error` log ERROR with the offending values (for a
  batch, the first bad row, isolated via a `SAVEPOINT`) and re-raise unchanged.
  Used by every DB write site.

## `config/config.py`

- `Config` + one dataclass per stage; `GROUPS` / `GROUP_TITLES` drive
  (de)serialisation, `__str__` and CLI wiring. `version = 5`.
- `create_config_file(...)` — write a defaulted config with the given paths.

## `project/project.py`

- `Project` — the `.msianalyzer.yml` model and run history.
- `get_project_folder(start_path)` — walk up to the project marker;
  `NotInProjectFolderError` when there is none.

## `run/run.py`

- `Run` — loads the config, registers the run in the `Project`, and executes the
  full pipeline (`run_core`). `SampleResult` is the per-sample worker return.
