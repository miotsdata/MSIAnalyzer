# Projects & configuration

## The project folder

An MSIAnalyzer **project** is a directory containing a `.msianalyzer.yml` marker
file. It records the project name and the history of runs executed inside it.
Commands locate the project by walking up from the current directory until they
find that marker.

## The config file

A run is driven by one config file (`.yaml`/`.yml` or `.toml`). It has a
`version` key (currently **13**) and one section per pipeline stage. Only `io` is
required; every other section falls back to its defaults.

```yaml
version: 13
io:
  project_folder: .
  mzml_paths: [data/s1.mzML, data/s2.mzML]
  xml_paths:   [data/s1.xml,  data/s2.xml]
  db_paths:    []
  out_dir:     results
ms1:      {bin_width: 0.0001, min_mz: 70.0, max_mz: 900.0}
align:    {align_ppm: 5.0, mz_decimals: 4}
group_ms2: {assoc_ppm: 10.0, include_unmatched: true}
purity:   {enabled: true, ppm_precursor_match: 20.0}
annotate: {library_path: null}   # a path (or [list, of, paths]) enables Stage B
consensus: {enabled: true, target_peaks: 10}
report:   {enabled: true, purity_cutoff: 0.8}
```

Relative paths are resolved against `project_folder`.

## Sections

### `io` — input/output (required)

| key | meaning |
|---|---|
| `project_folder` | project root (contains `.msianalyzer.yml`) |
| `mzml_paths` | input mzML files |
| `xml_paths` | raster XML files, paired with `mzml_paths` |
| `db_paths` | parsed raw-database path per sample, paired with `mzml_paths`. Leave empty to use `<project_folder>/parsed/<stem>.db` (the recommended default — raw DBs are shared across analyses, so they belong at the project level, not in an analysis `out_dir`). |
| `out_dir` | where **this analysis'** outputs are written |

### `ms1` — averaged MS1 spectrum

| key | default | meaning |
|---|---|---|
| `chunk_size` | `2000` | scans read per DB chunk |
| `bin_width` | `0.0001` | width of each m/z bin, Da |
| `min_mz` | `70.0` | lower bound of the binned range |
| `max_mz` | `900.0` | upper bound of the binned range |

### `centroid` — peak detection

| key | default | meaning |
|---|---|---|
| `prominence_factor` | `0.1` | minimum peak prominence, as a multiple of baseline |
| `baseline_factor` | `100` | peak must exceed `baseline + baseline_factor × baseline` |
| `baseline_method` | `"local"` | `"global"` or `"local"` baseline estimation |
| `baseline_percentile` | `10` | percentile of non-zero intensities used as baseline |
| `local_window` | `501` | rolling window (bins) for local baseline |
| `smooth_sigma` | `10` | Gaussian smoothing sigma (bins) for the local baseline |
| `merge_ppm` | `5` | ppm tolerance for merging adjacent detected peaks |

### `peak` — intensity filtering

| key | default | meaning |
|---|---|---|
| `filter_mad` | `true` | use a median-absolute-deviation threshold |
| `filter_mad_log` | `true` | compute the MAD threshold in log10 space |
| `filter_mad_nmads` | `2.5` | number of MADs above the median for the cutoff |
| `peak_height_threshold` | `1000.0` | flat intensity cutoff, used when `filter_mad` is false |

### `align` — cross-sample m/z alignment

| key | default | meaning |
|---|---|---|
| `align_ppm` | `5.0` | ppm tolerance for grouping peaks from different samples into one feature |
| `sample_names` | `null` | column names for samples; defaults to the mzML stems |
| `mz_decimals` | `4` | decimal places the consensus feature m/z is rounded to |

### `group_ms2` — MS2→feature association

| key | default | meaning |
|---|---|---|
| `assoc_ppm` | `10.0` | ppm tolerance accepting a precursor→feature match. Should be **≥ `align.align_ppm`** — it must cover the feature's own width plus the extra error of a single survey-scan precursor. A warning is logged if it is tighter. |
| `include_unmatched` | `true` | keep MS2 scans that matched no feature (stored with a NULL feature) instead of dropping them |
| `default_isolation_half_width` | `0.5` | isolation half-width (Da) assumed when a scan carries no isolation offsets |
| `precursor_only_tic_frac` | `0.8` | a scan is flagged `precursor_only` when at least this fraction of its fragment TIC is within `precursor_only_mz_tol_da` of the precursor |
| `precursor_only_mz_tol_da` | `2.0` | half-width (Da) of the "on the precursor" band for the `precursor_only` test |
| `flat_fragmentation_min_peaks` | `3` | a scan needs at least this many peaks (after `flat_fragmentation_min_rel_intensity` filtering) before the `flat_fragmentation` test applies |
| `flat_fragmentation_cv_threshold` | `0.2` | a scan is flagged `flat_fragmentation` when its surviving peaks' coefficient of variation (`std(intensity)/mean(intensity)`) is `<=` this — many peaks at different m/z but near-identical height, more consistent with noise/co-isolation than real decaying fragmentation. A soft QC flag, not a filter. |
| `flat_fragmentation_min_rel_intensity` | `0.01` | peaks below this fraction of the scan's base peak are dropped before the peak count and the CV are computed |

### `purity` — precursor ion purity

Measures each MS2 scan's isolation-window purity against its parent MS1 scan (and
the next MS1 on the same raster line). Independent of the feature list — use it
instead of the grouper's `n_features_in_window` on large runs.

| key | default | meaning |
|---|---|---|
| `enabled` | `true` | run the stage; `false` skips it entirely |
| `ppm_precursor_match` | `20.0` | ppm tolerance for deciding which in-window MS1 peak is the precursor |
| `default_half_window_da` | `0.5` | isolation half-width (Da) assumed when a scan carries no isolation offsets |
| `min_rel_intensity` | `0.01` | drop in-window MS1 peaks below this fraction of the window's base peak |
| `merge_ppm` | `5.0` | ppm tolerance for merging split profile peaks in the window slice |
| `use_next_ms1` | `true` | interpolate purity across the parent MS1 and the next MS1 when it is the same pixel or an adjacent pixel on the same raster line |
| `max_interpixel_gap_sec` | `null` | max parent→next-pixel time gap for interpolation; `null` derives it per sample from the median in-line pixel gap |
| `precursor_confirm_ppm` | `25.0` | half-width (ppm) of the band around `precursor_mz` for the peak-detection-free `precursor_frac` |
| `precursor_confirm_min_frac` | `0.01` | `precursor_frac >= this` sets `precursor_confirmed` |
| `precursor_snap_ppm` | `15.0` | snap `precursor_mz` to the nearest parent-MS1 local max within this many ppm (`0` disables); stored as `precursor_mz_snapped` — association is **not** re-run |

### `annotate` — MS2 spectral-library annotation

Leave `library_path` empty (`null`) and the whole stage is skipped.

| key | default | meaning |
|---|---|---|
| `library_path` | `null` | path to a libviz library database, or a list of them (candidates are pooled per scan). Empty (`null` / `[]`) ⇒ no annotation. |
| `noise_threshold` | `0.01` | after both spectra are normalised to 1, drop peaks below this fraction (empirical **and** library) |
| `candidate_ppm` | `10.0` | a library spectrum is a candidate when its precursor m/z is within this of the feature m/z |
| `fragment_ppm` | `10.0` | ppm tolerance for aligning individual fragment peaks while scoring |
| `mz_power` | `2.0` | MSDial-style m/z weighting exponent in the dot product |
| `int_power` | `0.5` | MSDial-style intensity weighting exponent |
| `score_weight_dot` | `1.0` | exponent on `dot_product_score` when combining it with `lib_coverage` / `emp_coverage` into `score` |
| `score_weight_lib_coverage` | `0.5` | exponent on `lib_coverage` |
| `score_weight_emp_coverage` | `0.5` | exponent on `emp_coverage`. Defaults reproduce `dot_product_score * sqrt(lib_coverage * emp_coverage)`. Lower this (even to `0`) when a spectrum's own real, library-absent background/matrix peaks are suppressing an otherwise good match — high dot product, high library coverage, low empirical coverage. |
| `min_matched_peaks` | `1` | store a candidate only when it shares at least this many fragments |
| `min_purity` | `null` | skip scans whose precursor purity (Stage A′) is known and below this; `null` scores every scan. Rows still carry `purity` / `runner_up_rel_int`. |
| `annotate_chimeric` | `true` | score chimeric scans (against their primary feature); they are always flagged. `false` skips them |
| `store_raw_spectra` | `true` | persist the untouched empirical + library spectra on every row (for mirror plots — the noise-filtered view is reconstructed from these on demand). `false` writes them NULL |
| `batch_size` | `200` | features handed to each worker process |
| `n_workers` | `null` | parallel workers; defaults to `os.cpu_count()` |

### `consensus` — per-feature MS2 pick

`consensus_score = best library score × purity term × peak term`; the
highest-scoring scan per feature wins. Runs library-free and purity-free.

| key | default | meaning |
|---|---|---|
| `enabled` | `true` | run the stage; `false` skips it |
| `target_peaks` | `10` | fragment count at which the peak-richness term saturates to 1 |
| `neutral_purity` | `0.5` | purity assumed for a scan the purity stage could not score |
| `min_purity` | `null` | drop scans with a known purity below this from the pick (they still count in `n_ms2`) |

### `report` — end-of-run summary

| key | default | meaning |
|---|---|---|
| `enabled` | `true` | write `summary_report.html` + `summary.json` after every other stage |
| `overlap_top_n` | `30` | max sample-combination bars in the feature-overlap UpSet plot |
| `purity_cutoff` | `0.8` | reference line on the purity plots + the "low purity" count |

### `h5ad` — spatial AnnData assembly

| key | default | meaning |
|---|---|---|
| `integration_ppm` | `5.0` | ppm tolerance for quantifying each feature m/z |
| `batch_size` | `1000` | spectra per processing chunk |
| `scan_handling` | `"average"` | `"average"` or `"first"` MS1 scan per pixel |
| `n_workers` | `null` | parallel workers; defaults to `os.cpu_count()` |

### `normalization` — TIC normalization

| key | default | meaning |
|---|---|---|
| `enabled` | `true` | add `layers['raw']` / `layers['TIC']` to every `<sample>.h5ad` and write `merged.h5ad` |

### `analysis` — per-analysis database

| key | default | meaning |
|---|---|---|
| `db_name` | `null` | file name of the analysis DB inside `out_dir`; when null it is `analysis_<run-id>.db` |

## Running

```
msianalyzer run -c run.yaml [-o OUT_DIR] [-l run.log] [-v debug]
```

- `-o` overrides `io.out_dir`.
- `-l PATH` writes this run's log to `PATH` (no user log file by default).
- `-v {debug,info,warning,error,critical}` (default `info`) sets the level of the
  console **and** the `-l` file.

Every run also writes a full-DEBUG log to `<project_folder>/logs/debug_<run-id>.log`
regardless of `-v`.

## Programmatic use

```python
from msianalyzer.core.config import Config

cfg = Config.from_yaml("run.yaml")
cfg.group_ms2.assoc_ppm          # 10.0
cfg.to_yaml("copy.yaml")
```

The set of sections is defined once, in `config.GROUPS`; `__str__`, (de)serialisation
and the CLI all iterate it.
