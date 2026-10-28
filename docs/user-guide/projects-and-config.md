# Projects & configuration

## The project folder

An MSIAnalyzer **project** is a directory containing a `msianalyzer.yml` marker
file. It records the project name and the history of runs executed inside it.
Commands locate the project by walking up from the current directory until they
find that marker.

Projects created before 0.7 used a hidden `.msianalyzer.yml`. Network shares
mark dotfiles Hidden, and Windows refuses to overwrite a hidden file, so runs
failed. Such a project is migrated to `msianalyzer.yml` the first time it is
opened; older MSIAnalyzer versions can no longer open it after that.

## The config file

A run is driven by one config file (`.yaml`/`.yml` or `.toml`). It has a
`version` key (currently **23**) and one section per pipeline stage. Only `io` is
required; every other section falls back to its defaults — you never need to
write out every field, only the ones you want to change from the defaults shown
below.

```yaml
version: 23
io:
  project_folder: .
  mzml_paths: [data/s1.mzML, data/s2.mzML]
  xml_paths:   [data/s1.xml,  data/s2.xml]
  db_paths:    []
  out_dir:     results
ms1:      {bin_width: 0.0001, min_mz: 70.0, max_mz: 900.0}
align:    {align_ppm: 5.0, mz_decimals: 4}
group_ms2: {assoc_ppm: 10.0, include_unmatched: true}
purity:   {enabled: true, precursor_confirm_ppm: 25.0}
annotate: {library_path: null}   # a path (or [list, of, paths]) enables Stage B
consensus: {enabled: true, target_peaks: 10}
report:   {enabled: true, purity_cutoff: 0.8}
```

Relative paths (like `data/s1.mzML` above) are resolved against `project_folder`
— so a config file can be checked into the project and moved around without
editing paths, as long as it stays next to the project's data.
Analyses store their paths the same way, so a whole project folder can be moved,
or opened from a NAS on both Linux and Windows, and its analyses still open.
Opening a project always shows the results inside *that* folder.

The **GUI's New Analysis wizard** builds one of these config files for you
interactively, one tab per section below (each field there shows this same
description as inline help). A field marked "not in the GUI" further down is
still a real, working config option — it just isn't exposed as a control in the
wizard, so the only way to set it is to hand-edit or script a config file
directly, as shown in [Programmatic use](#programmatic-use) below.

**Load from previous analysis.** The wizard's "Load from previous
analysis" dropdown (above the tabs) prefills every tab — including the
sample/db-only file lists and output folder — from an earlier run in the
same project, so re-running with a couple of settings changed doesn't
mean refilling the form from scratch. Picking "Start blank" resets
everything back to the defaults shown below. A field a past run's saved
config no longer has (removed since) is simply skipped, and a field that
run predates just keeps its default — nothing here validates the loaded
config the way starting a real run does, since you're about to review
every tab anyway.

## Sections

Sections run in this order during a pipeline execution, which is also the
order of the GUI's tabs: `ms1` → `centroid` → `peak` → `align` →
`target_list` → `h5ad` → `isotopes` → `ms2_cleaning` → `group_ms2` → `purity` → `annotate` →
`consensus` → `normalization` → `spatial_correlation` → `report`.
`io` isn't a "stage" as such — it's the input/output paths every other stage
reads from or writes into — and `analysis` only names the analysis database.

### `io` — input/output (required)

The only section you *must* fill in — it says what data to process and where to
put the results.

| key | meaning |
|---|---|
| `project_folder` | project root (contains `msianalyzer.yml`) |
| `mzml_paths` | input mzML files to process, e.g. `[data/s1.mzML, data/s2.mzML]` |
| `xml_paths` | raster/imaging XML files, one per entry in `mzml_paths`, in the same order — needed to place each scan at its pixel position |
| `db_paths` | parsed raw-database path per sample, paired with `mzml_paths`. Leave empty to use `<project_folder>/parsed/<stem>.db` (the recommended default — raw DBs are shared across analyses, so they belong at the project level, not in an analysis `out_dir`). Only set this if you deliberately want the parsed database somewhere else. |
| `out_dir` | where **this analysis'** outputs (analysis database, `.h5ad` files, summary report) are written, e.g. `results/run_1` |

**Already-parsed samples.** A sample can skip mzML/XML parsing entirely if
you already have its parsed, pixel-mapped raw database (e.g. from a
previous run): put `null` at that index in both `mzml_paths` and
`xml_paths`, and the real `.db` path at the same index in `db_paths`.
mzML-sourced and already-parsed samples can be freely mixed in the same
run:

```yaml
io:
  mzml_paths: [data/s1.mzML, null]
  xml_paths:  [data/s1.xml,  null]
  db_paths:   [null,         parsed/s2.db]
```

The GUI's New Analysis wizard exposes this via the **"Add db files..."**
button on the input/output tab, as a separate list from the mzML/XML
sample table.

### `ms1` — averaged MS1 spectrum

Bins every pixel's raw MS1 scan onto a shared m/z axis and averages them into
one representative spectrum per sample — the input to peak detection below.

| key | default | meaning |
|---|---|---|
| `chunk_size` | `2000` | scans read per DB chunk (memory/speed only — never changes the result) |
| `bin_width` | `0.0001` | width of each m/z bin, in Da. Smaller keeps more mass resolution but is slower and can under-average jittery replicate peaks; coarser is faster but can blur two close-mass peaks together. |
| `min_mz` | `70.0` | lower bound of the binned range — anything below this is discarded |
| `max_mz` | `900.0` | upper bound of the binned range. Narrowing `min_mz`/`max_mz` to the mass range you actually care about (e.g. `100`–`500` for small metabolites) saves memory and time with no loss of relevant peaks. |

### `centroid` — peak detection

Finds discrete peaks in the averaged (still-continuous) spectrum from `ms1`, by
estimating a noise baseline and reporting local maxima that rise far enough
above it.

| key | default | meaning |
|---|---|---|
| `prominence_factor` | `0.1` | how much a peak must stand out from its immediate neighbors (not just the baseline), as a multiple of the baseline — e.g. `0.1` requires rising 10% of the local baseline above the surrounding valleys. Raise to ignore small shoulders on a bigger peak. |
| `baseline_factor` | `100` | a peak must exceed `baseline + baseline_factor × baseline` to count at all — e.g. `100` requires reaching 101× the estimated noise level. The main "how far above the noise floor" knob. |
| `baseline_method` | `"local"` | `"local"` estimates a separate baseline per region (better when noise varies across the mass range); `"global"` uses one value for the whole spectrum (simpler, faster) |
| `baseline_percentile` | `10` | percentile of non-zero intensities used as the noise floor, e.g. `10` = the value below which the lowest 10% of intensities fall |
| `local_window` | `501` | rolling window size, in bins, for local baseline estimation (only used when `baseline_method` is `"local"`) |
| `smooth_sigma` | `10` | Gaussian smoothing sigma, in bins, applied to the local baseline to soften jumps between windows |
| `merge_ppm` | `5` | ppm tolerance for merging two adjacent detected peaks that are really one real peak split by noise |

### `peak` — intensity filtering

Sets the final intensity cutoff on peaks left over from `centroid` — either
adaptive per sample (`filter_mad`, recommended) or one fixed number for every
sample.

| key | default | meaning |
|---|---|---|
| `filter_mad` | `true` | when `true`, the cutoff is computed per-sample from the median + median-absolute-deviation (MAD) of peak intensities, adapting to each sample's own noise level. When `false`, every sample instead uses the single fixed `peak_height_threshold` — only appropriate when every sample has comparable intensity scale. |
| `filter_mad_log` | `true` | compute the median/MAD in log10 intensity space rather than raw — recommended for MS data, whose intensities span orders of magnitude. Only used when `filter_mad` is true. |
| `filter_mad_nmads` | `2.5` | how many MADs above the median sets the cutoff, e.g. `2.5` keeps peaks at or above `median + 2.5 × MAD`. Raise (e.g. `3.5`–`4`) for a noisy dataset; lower (e.g. `1.5`) to keep more borderline peaks. Only used when `filter_mad` is true. |
| `peak_height_threshold` | `1000.0` | flat, absolute intensity cutoff, used only when `filter_mad` is `false`. The right value here depends entirely on your instrument/acquisition, so check a sample's own intensity scale first. |

### `align` — cross-sample m/z alignment

The "same" molecule lands at a slightly different m/z in each independently
centroided sample (drift, calibration). This stage groups peaks across samples
within `align_ppm` of each other into one "feature" with one consensus m/z —
everything downstream (MS2 association, annotation, per-feature tables) is
keyed on these features, not on any one sample's raw peaks.

| key | default | meaning |
|---|---|---|
| `align_ppm` | `5.0` | how close two peaks from different samples must be (ppm) to be treated as the same feature. Example: at `5.0` ppm, m/z 400 in one sample and 400.002 in another (5 ppm apart) are just barely grouped; 400.003 is not. Too tight splits one real compound into several features; too loose merges distinct close-mass compounds. Usually a bit looser than `centroid.merge_ppm`, since it also has to absorb run-to-run calibration drift. |
| `sample_names` | `null` | *not exposed in the GUI* — optional per-sample column names for the aligned feature table (e.g. `["control_1", "treated_1"]` instead of the mzML file names). A GUI-started run always uses the mzML file names; set this only by hand-editing or scripting a config file. |
| `mz_decimals` | `4` | decimal places a feature's consensus m/z is rounded to for display, e.g. `4` shows `400.1234`. Cosmetic only — doesn't change which peaks get grouped (that's `align_ppm`). |

### `isotopes` — isotope detection

Finds ¹³C isotope features (M+1, M+2, …) and takes them out of the feature list
before MS2 association, so they are never annotated, kept in the `.h5ad` or
scored. They are kept, linked to their monoisotopic feature, in the analysis
database's `feature_isotopes` table. The ion images it compares are the `.h5ad`
matrices, built just before; isotope columns are then removed from every
`.h5ad` file.

| key | default | meaning |
|---|---|---|
| `enabled` | `true` | run isotope detection; when off, isotopes stay ordinary features |
| `max_isotopes` | `3` | isotope peaks looked for after the monoisotopic one (M+1 … M+n). The chain stops at the first missing isotope. |
| `ppm` | `5.0` | tolerance of each isotope against `mono + k × 1.0033548 / charge`. Example: mono 300.1000, M+1 expected at 301.1034 ± 0.0015. |
| `max_charge` | `1` | highest charge tried; MALDI ions of small molecules are almost always singly charged |
| `min_spatial_corr` | `0.5` | minimum Pearson correlation between the isotope's and the monoisotopic ion image, over the pixels where the monoisotopic ion is present. Lower links more (including unrelated ions in the same region); higher misses weak, noisy isotopes. |

### `ms2_cleaning` — MS2 cleaning

Removes background fragments and fragments above the precursor from every
MS2 spectrum as it is read; raw data is never changed (ADR 64). The list of
shared fragments is always computed and shown on the Summary page.

| key | default | meaning |
|---|---|---|
| `background_enabled` | `false` | remove fragments found in the spectra of many different precursors |
| `background_ppm` | `10.0` | fragments within this many ppm are the same fragment |
| `background_min_percent` | `50.0` | a fragment in at least this % of the polarity's precursors is removed. A precursor is a feature with MS2 (or an unmatched scan's precursor m/z) and counts once, however many of its scans have the fragment. |
| `above_precursor_enabled` | `false` | remove peaks above the precursor m/z (the matched feature's, else the scan's); skipped for precursor charge ≥ 2 |
| `above_precursor_ppm` | `10.0` | tolerance above the precursor m/z before a peak is removed |

### `group_ms2` — MS2→feature association

Attaches each MS2 (fragmentation) scan to the feature it belongs to, by
snapping its precursor m/z to the nearest aligned feature. Nothing is
discarded here — an unmatched or unreliable-looking scan is flagged, not
dropped.

| key | default | meaning |
|---|---|---|
| `assoc_ppm` | `10.0` | ppm tolerance accepting a precursor→feature match. Should be **≥ `align.align_ppm`** — it must cover the feature's own width plus the extra imprecision of a single survey-scan precursor. A warning is logged if it is tighter. |
| `include_unmatched` | `true` | keep MS2 scans that matched no feature (stored with a NULL feature) instead of dropping them — useful for auditing why a scan didn't associate |
| `default_isolation_half_width` | `0.5` | isolation half-width (Da) assumed when a scan carries no isolation offsets, e.g. `0.5` assumes a ±0.5 Da window |
| `precursor_only_tic_frac` | `0.8` | flags a scan `precursor_only` (fragmentation likely failed) when at least this fraction of its fragment TIC is within `precursor_only_mz_tol_da` of the precursor — e.g. `0.8` flags a scan where 80%+ of the signal is still "on the precursor." A QC flag, not a filter. |
| `precursor_only_mz_tol_da` | `2.0` | half-width (Da) of the "on the precursor" band for the `precursor_only` test above |
| `flat_fragmentation_min_peaks` | `3` | a scan needs at least this many peaks (after `flat_fragmentation_min_rel_intensity` filtering) before the `flat_fragmentation` test even applies — below it, there aren't enough peaks to trust the coefficient of variation |
| `flat_fragmentation_cv_threshold` | `0.2` | a scan is flagged `flat_fragmentation` when its surviving peaks' coefficient of variation (`std(intensity)/mean(intensity)`) is `<=` this — many peaks at near-identical height (*low* CV) looks more like noise/co-isolation than real decaying CID/HCD fragmentation (which has one or a few dominant fragments, i.e. *high* CV). A soft QC flag, not a filter. |
| `flat_fragmentation_min_rel_intensity` | `0.01` | peaks below this fraction of the scan's own base peak are dropped before the peak count and the CV above are computed, e.g. `0.01` drops anything under 1% of the tallest peak |

### `purity` — precursor ion purity

Measures how "clean" each MS2 scan's precursor selection was — how much of the
isolation-window ion current in its parent MS1 scan actually belonged to the
intended precursor, versus another co-isolated compound at similar mass.
Independent of the feature list, and needs no peak detection — a
profile-area integration that stays computable even in dense, matrix-heavy,
low-mass windows where a discrete peak often can't be resolved. See
[ADR 19](../developer/adr/0019-retire-feature-density-chimeric-flag-and-peak-based-purity.md).

| key | default | meaning |
|---|---|---|
| `enabled` | `true` | run the stage; `false` skips it entirely (every `precursor_frac`/`precursor_confirmed` field then stays NULL) |
| `default_half_window_da` | `0.5` | isolation half-width (Da) assumed when a scan carries no isolation offsets |
| `precursor_confirm_ppm` | `25.0` | half-width (ppm) of a band placed exactly on `precursor_mz`, used to compute `precursor_frac = I(band) / I(isolation window)` — the fraction of the window's ion current sitting on the precursor. This is the metric. |
| `precursor_confirm_min_frac` | `0.01` | `precursor_frac >= this` sets the `precursor_confirmed` flag — a deliberately lenient sanity check that *some* signal sits where expected, not a purity threshold (use `annotate.min_precursor_frac`/`consensus.min_precursor_frac` for that) |
| `precursor_snap_ppm` | `15.0` | snap `precursor_mz` to the nearest parent-MS1 local max within this many ppm (`0` disables); stored as `precursor_mz_snapped` — association is **not** re-run with the snapped value |
| `n_workers` | `null` | samples scored in parallel, one process per sample; `null`/`0` uses `os.cpu_count()`, `1` forces serial. Capped at the sample count; lower it if memory is tight (each worker holds one sample's scan index in memory). |

### `annotate` — MS2 spectral-library annotation

Puts a name to each MS2 spectrum by comparing it against reference spectra
from one or more spectral libraries, first narrowing candidates by precursor
m/z (`candidate_ppm`) then scoring each with a coverage-aware reverse dot
product (similar to the scoring used by tools like MS-DIAL). Leave
`library_path` empty (`null`) and the whole stage is skipped — every other
setting below is then unused.

| key | default | meaning |
|---|---|---|
| `library_path` | `null` | path to a libviz library database, or a list of them, e.g. `"lib.db"` or `["lib_a.db", "lib_b.db"]` (candidates from every library are pooled per scan before ranking). Empty (`null` / `[]`) ⇒ no annotation. |
| `noise_threshold` | `0.01` | after both spectra are max-normalised to 1, drop peaks below this fraction of the base peak (empirical **and** library) — e.g. `0.01` drops anything under 1% of the tallest peak. Raise for a stricter, high-confidence-peaks-only comparison; lower to keep more small peaks. |
| `candidate_ppm` | `10.0` | a library spectrum is a candidate when its precursor m/z is within this of the feature m/z — wider considers more candidates (slower, catches larger calibration offsets), narrower is faster and stricter |
| `fragment_ppm` | `10.0` | ppm tolerance for treating a scan peak and a library peak as "the same peak" while scoring |
| `mz_power` | `2.0` | MSDial-style m/z weighting exponent in the dot product — leave at default unless replicating a specific published scoring scheme |
| `int_power` | `0.5` | MSDial-style intensity weighting exponent — `0.5` (square root) softens the influence of one very tall peak |
| `score_weight_dot` | `1.0` | exponent on `dot_product_score` when combining it with `lib_coverage` / `emp_coverage` into `score` |
| `score_weight_lib_coverage` | `0.5` | exponent on `lib_coverage` — the fraction of the *library candidate's* peaks matched in the scan |
| `score_weight_emp_coverage` | `0.5` | exponent on `emp_coverage` — the fraction of the *scan's own* peaks matched in the candidate. Defaults reproduce `dot_product_score × sqrt(lib_coverage × emp_coverage)`. Lower this (even to `0`) when a spectrum's own real, library-absent background/matrix peaks are suppressing an otherwise good match — high dot product, high library coverage, low empirical coverage. `dot_product_score`/`lib_coverage`/`emp_coverage`/`coverage_score` are always stored unweighted, so a config change only shows up in `score` after a re-run. |
| `min_matched_peaks` | `1` | store a candidate only when it shares at least this many fragments with the scan |
| `representative_score_tolerance` | `0.05` | when a feature has candidates within this many `score` points of the top one, the one that matched the most fragment peaks (`n_matched_peaks`) becomes the feature's representative compound — shown everywhere it's labelled by one compound (GUI Annotations table, Visual Inspection's feature list, the report's "Top features") — instead of automatically whichever scored a hair higher. A trivial single-peak match shouldn't outrank a richer multi-peak one just because it landed on a cleaner scan; `score` itself is never touched, only which row is picked as representative. Set to `0` to disable (plain highest-score-wins). See [ADR 21](../developer/adr/0021-representative-compound-peak-count-tolerance.md). |
| `min_precursor_frac` | `null` | skip scans whose precursor purity (Stage A′) is known and below this; `null` scores every scan. Every associated scan is scored unconditionally regardless of how many features share its isolation window — see [ADR 19](../developer/adr/0019-retire-feature-density-chimeric-flag-and-peak-based-purity.md). Rows still carry `precursor_frac` regardless of this setting. |
| `store_raw_spectra` | `true` | persist the untouched empirical + library spectra on every row, so mirror plots never need to re-open the raw per-sample database or the library file (either can be a slow/remote mount). The noise-filtered view shown in a mirror plot is reconstructed on demand from these plus `noise_threshold`, not stored a second time. `false` writes the raw columns NULL and keeps the table smaller. |
| `batch_size` | `200` | features handed to each worker process at a time (memory/speed only) |
| `n_workers` | `null` | parallel worker processes; `null` uses `os.cpu_count()` — lower on a shared machine or under memory pressure |

### `consensus` — per-feature MS2 pick

A feature usually has several MS2 scans behind it, of varying quality. This
stage folds each scan's best library score (when `annotate` ran), its
precursor purity (`precursor_frac`), and its fragment-peak count into one
`consensus_score = best_score × precursor_frac_term × peak_term`, and picks the
highest-scoring scan to represent that feature. Runs regardless of whether
annotation or purity ran (missing pieces fall back to neutral values).

**This is a one-way, read-only stage** — it runs *after* `annotate` and only
*reads* the already-final `ms2_annotations.score`/`rank_ms2`; it writes its own
result into a separate table, `feature_ms2_consensus`, and never changes
`annotate`'s `score`, `rank_ms2` or `rank_feature`. The two answer different
questions: `annotate`'s score/rank say "how good is *this* (scan, library
candidate) match"; `consensus_score` says "of this feature's several scans,
which one is the best all-around representative to show/export."

| key | default | meaning |
|---|---|---|
| `enabled` | `true` | run the stage; `false` skips it (`feature_ms2_consensus` is left empty) |
| `target_peaks` | `10` | the peak-richness term is `peak_term = min(1, n_peaks / target_peaks)` — the fragment count at which a scan earns full credit (`1.0`); more peaks don't earn extra credit past that, fewer scale down proportionally. Worked example at the default `10`: a scan with 10+ peaks scores `1.0`, 5 peaks scores `0.5`, 2 peaks scores `0.2`. Raise it to weigh peak richness more heavily in the pick; lower it if your data is naturally low-peak-count. |
| `neutral_precursor_frac` | `0.5` | precursor-purity value substituted for a scan the purity stage couldn't score (e.g. purity disabled) — a neutral value that neither rewards nor penalizes missing purity data |
| `min_precursor_frac` | `null` | scans with a *known* `precursor_frac` below this are excluded from the pick (they still count toward `n_ms2`); `null` considers every scan |

### `report` — end-of-run summary

Writes a human-readable `summary_report.html` (open in any browser) +
machine-readable `summary.json` (also what the GUI's Analysis Summary section
reads) after every other stage — per-sample scan/peak counts, a feature-overlap
"UpSet" plot, and MS2 association/purity distributions.

| key | default | meaning |
|---|---|---|
| `enabled` | `true` | write the report; `false` skips it (e.g. for a quick exploratory run) |
| `overlap_top_n` | `30` | max sample-combination bars in the feature-overlap UpSet plot, largest first — a run with `n` samples has up to `2^n - 1` possible combinations, so this keeps the plot readable |
| `purity_cutoff` | `0.8` | `precursor_frac` value below which a scan counts as "low purity" in the report, and where the reference line is drawn on the precursor-purity histogram — a reporting threshold only, independent of `annotate.min_precursor_frac`/`consensus.min_precursor_frac` |

### `h5ad` — spatial AnnData assembly

Quantifies every aligned feature at every pixel of every sample and assembles
the result into an `AnnData` object (`scanpy`/`squidpy`'s standard format, also
used by this project's Visual Inspection view) — one `.h5ad` file per sample.

| key | default | meaning |
|---|---|---|
| `integration_ppm` | `5.0` | window (ppm around each feature's m/z) summed over when quantifying that feature at each pixel — wider tolerates more per-pixel drift at the risk of picking up a neighboring peak, narrower is more mass-specific but can miss drifted signal |
| `batch_size` | `1000` | spectra per processing chunk (memory/speed only) |
| `scan_handling` | `"average"` | `"average"` uses each pixel's averaged MS1 spectrum (smooths noise, the robust default); `"first"` uses only the first raw scan recorded at that pixel (faster, closer to a single instantaneous reading, but noisier) |
| `n_workers` | `null` | parallel CPU workers; `null` uses `os.cpu_count()` |

### `normalization` — TIC normalization

Makes feature intensities comparable across pixels and samples by correcting
for how much total ion signal a given pixel happened to produce (tissue
thickness, ionization efficiency) rather than purely how much of a compound is
there. Compares each pixel's total ion current (`obs['tic']`) to the
dataset-wide median TIC, scales intensities by that ratio, then log1p-
compresses the result.

| key | default | meaning |
|---|---|---|
| `enabled` | `true` | add `layers['raw']` (untouched) / `layers['TIC']` (normalized) to every `<sample>.h5ad` and write the cross-sample `merged.h5ad`. `false` skips this entirely — only raw intensities remain, saving processing time/disk space if TIC normalization doesn't apply to your data. |

### `spatial_correlation` — per-feature spatial autocorrelation (opt-in, off by default)

Two independent, opt-in sub-passes — see [Concepts](concepts.md) for what
each score actually means.

**Global** (`enabled`): computes Moran's I (via `squidpy`) for every
feature, independently per sample — how much a feature's pixel
intensities cluster spatially rather than scattering at random, a proxy
for "is this a real, spatially organized signal or noise." Runs after
`normalization`, once every per-sample `.h5ad` exists; results are
written directly into each sample's own `.h5ad` (`adata.uns["moranI"]`,
squidpy's own convention), not into the analysis database. Browsable in
the GUI's **Spatial Correlation** tab.

**Local** (`local_enabled`): a separate, hand-rolled pass computing
*local* (per-pixel) Moran's I for every feature, written in full to
`adata.layers["localMoranI"]` (not a pre-thresholded summary) — the
GUI's **Spatial Correlation** tab turns this into a "% of pixels above a
threshold" table on the fly, and that threshold is freely adjustable
right there, live, with no re-run: `local_threshold` below only sets what
the table shows when it first opens. Independent of `enabled`: either,
both, or neither can be on. Visual Inspection's **Local Moran's I**
heatmap layer computes the same per-pixel score live, on demand, and
never needs this pass (or `enabled`) to have run at all — this pass only
exists to pre-compute the per-pixel *data* the summary table reads.

See [ADR 48](../developer/adr/0048-spatial-correlation-morans-i.md).

| key | default | meaning |
|---|---|---|
| `enabled` | `false` | run the global pass. Off by default — it's an optional, fairly heavy extra pass (spatial neighbor graph + per-feature autocorrelation for every sample) that most runs don't need. |
| `layer` | `"TIC"` | which `.h5ad` layer's intensities to score (both passes) — TIC-normalized by default for comparability across samples, falling back to raw `.X` when the named layer isn't present (e.g. `normalization.enabled: false`). |
| `n_jobs` | `null` | parallel workers squidpy uses to score features *within* one sample for the global pass (samples are still processed one at a time). `null` lets squidpy/joblib pick its own worker count. |
| `local_enabled` | `false` | run the local per-pixel pass. Off by default, independently of `enabled` — scoring every pixel for every feature is heavier than the global per-feature score. |
| `local_threshold` | `1.0` | **not a pipeline parameter** — only the value the GUI's local-summary table starts at when it first opens; adjustable live in the GUI afterward with no re-run needed. **Not bounded to -1–1** like the global score — see the in-app field help (or [Concepts](concepts.md)) for what values mean. |
| `local_n_workers` | `null` | worker processes scoring features in parallel for the local pass — it has no built-in parallelism of its own the way `n_jobs` gives the global pass, so every feature's score is split across worker processes instead. `null` uses `os.cpu_count()`; `1` forces a fully serial run. Independent of `n_jobs`, which only affects the global pass. |

### `analysis` — analysis database and run log

Names the per-run SQLite database that collects this analysis' results
(features, MS2 associations, purity, annotations, consensus picks) — as
opposed to the raw per-sample databases, which are shared and immutable —
and sets the run's log file. In the GUI both are on the **output** tab,
next to the output folder.

| key | default | meaning |
|---|---|---|
| `db_name` | `null` | file name of the analysis DB inside `out_dir`, e.g. `"my_analysis.db"`; when null it is `analysis_<run-id>.db` — usually fine to leave as-is |
| `log_level` | `"info"` | level of the run log `run_<date>_<time>.log` written in `out_dir`, a new file per run: `off` (no file), `debug` (every function call, for troubleshooting), `info` (each step and its main numbers), `warning`, `error` |

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
cfg.align.sample_names = ["control_1", "control_2", "treated_1"]  # GUI-hidden field
cfg.to_yaml("copy.yaml")
```

The set of sections is defined once, in `config.GROUPS`; `__str__`, (de)serialisation
and the CLI all iterate it. This is also how a field like `align.sample_names` — not
offered as a control in the GUI's New Analysis wizard — stays fully usable: set it
here, or by hand-editing a YAML/TOML config file, and everything downstream (the
CLI, `Config.from_dict`) picks it up exactly like any other field.
