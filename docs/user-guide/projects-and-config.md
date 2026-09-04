# Projects & configuration

## The project folder

An MSIAnalyzer **project** is a directory containing a `.msianalyzer.yml` marker
file. It records the project name and the history of runs executed inside it.
Commands locate the project by walking up from the current directory until they
find that marker.

## The config file

A run is driven by one config file (`.yaml`/`.yml` or `.toml`). It has a
`version` key (currently **5**) and one section per pipeline stage. Only `io` is
required; every other section falls back to its defaults.

```yaml
version: 4
io:
  project_folder: .
  mzml_paths: [data/s1.mzML, data/s2.mzML]
  xml_paths:   [data/s1.xml,  data/s2.xml]
  db_paths:    []
  out_dir:     results
ms1:      {bin_width: 0.0001, min_mz: 70.0, max_mz: 900.0}
align:    {align_ppm: 5.0, mz_decimals: 4}
group_ms2: {assoc_ppm: 10.0, include_unmatched: true}
annotate: {library_path: null}   # a path (or [list, of, paths]) enables Stage B
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
| `min_matched_peaks` | `1` | store a candidate only when it shares at least this many fragments |
| `annotate_chimeric` | `true` | score chimeric scans (against their primary feature); they are always flagged. `false` skips them |
| `store_filtered_spectra` | `true` | persist the filtered empirical + library spectra on every row (for mirror plots). `false` writes them NULL |
| `batch_size` | `200` | features handed to each worker process |
| `n_workers` | `null` | parallel workers; defaults to `os.cpu_count()` |

### `h5ad` — spatial AnnData assembly

| key | default | meaning |
|---|---|---|
| `integration_ppm` | `5.0` | ppm tolerance for quantifying each feature m/z |
| `batch_size` | `1000` | spectra per processing chunk |
| `scan_handling` | `"average"` | `"average"` or `"first"` MS1 scan per pixel |
| `n_workers` | `null` | parallel workers; defaults to `os.cpu_count()` |

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
