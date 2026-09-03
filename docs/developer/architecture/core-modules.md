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
  single source of truth for the analysis schema, **including the grouper
  tables** ([ADR 6](../adr/0006-schema-single-source-of-truth.md)).
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

## `spectral_matching.py`

- `reverse_dot_product(...) -> MatchResult` — MSDial-style coverage-aware reverse
  dot product with ppm-tolerant peak alignment. Unused by the current pipeline;
  the basis for Stage B (library annotation).

## `utils/create_adata.py`

- `create_spatial_adata(db_path, target_mz_set, project_id, integration_ppm,
  batch_size, scan_handling, n_workers) -> AnnData` — quantifies target m/z
  across each pixel's MS1 spectra. Reads only the raw DB; returns an `AnnData`
  (persisted by the caller as a sidecar `.h5ad`).

## `config/config.py`

- `Config` + one dataclass per stage; `GROUPS` / `GROUP_TITLES` drive
  (de)serialisation, `__str__` and CLI wiring. `version = 4`.
- `create_config_file(...)` — write a defaulted config with the given paths.

## `project/project.py`

- `Project` — the `.msianalyzer.yml` model and run history.
- `get_project_folder(start_path)` — walk up to the project marker;
  `NotInProjectFolderError` when there is none.

## `run/run.py`

- `Run` — loads the config, registers the run in the `Project`, and executes the
  full pipeline (`run_core`). `SampleResult` is the per-sample worker return.
