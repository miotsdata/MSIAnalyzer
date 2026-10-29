# The workflow

A run (`msianalyzer.core.run.run.Run`) executes the stages below over a set of
samples. Per-sample stages run in parallel worker processes; cross-sample stages
run once. Any stage whose output already exists is skipped
([provenance & caching](../developer/architecture/provenance.md)).

## Inputs

Per sample: one **mzML** file and one **raster XML** file. The run also needs a
**project folder** (a directory containing `msianalyzer.yml`) and an **output
directory**.

The mzML must hold **centroided** MS1 spectra. A run whose mzML declares
profile MS1 (`MS:1000128`) stops before any work, listing those files.
ThermoRawFileParser writes centroids by default; with msconvert, add
`--filter "peakPicking vendor msLevel=1-"`. A raw DB parsed earlier from a
profile mzML is not detected once the mzML has been replaced, so delete it
from `parsed/` after re-converting.

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

## Stage 6.5 — Spatial AnnData per sample

`create_spatial_adata()` quantifies every feature m/z (plus injected target-list
features and the normalization reference m/z) across each pixel's MS1 spectra
(`h5ad.integration_ppm`, `h5ad.scan_handling`) and writes `<sample>.h5ad`. It
runs before the MS2 stages because isotope detection uses these matrices
(ADR 55); nothing from the MS2 stages is stored in the `.h5ad`.

## Stage 6.6 — Detect isotopes (→ analysis DB, `.h5ad`)

`run_detect_isotopes()` finds ¹³C isotope features (M+1, M+2, …) and takes
them out of `features` before the MS2 stages use the feature list, so they are
never associated with MS2 or annotated. A feature is an isotope of a more
intense one when it sits at `mono + k × 1.00336 / z` (within `isotopes.ppm`),
is less intense, and its ion image (its `.h5ad` column) co-localizes with the
monoisotopic one (`isotopes.min_spatial_corr`), in most of the samples where
both are detected. Their columns are then removed from every `.h5ad`.
Target-list features and the normalization reference m/z are never removed.
Output: the `feature_isotopes` table, which keeps each isotope linked to its
monoisotopic feature. See ADR 54 and 55.

## Stage 7 — Associate MS2 with features (→ analysis DB)

`run_grouper()` reads `features` and every sample's `ms2_scans`, and snaps each
MS2 scan to a feature. It writes two tables — `ms2_associations`,
`feature_ms2_summary` — and drops nothing. Controlled by
the `group_ms2.*` settings. Full detail in [MS2 annotation](ms2-annotation.md).

**MS2 cleaning** (the `ms2_cleaning.*` settings, "MS2 cleaning" tab) runs
first, on the spectra as they're read; the raw databases are never changed.

- **Background fragments.** Every fragment is counted by how many
  *precursors* have it: a feature with MS2, or for scans matching no
  feature, their precursor m/z. A precursor counts once, however many of its
  scans have the fragment, so an ion fragmented in hundreds of pixels can't
  make its own fragments look like background. Fragments within
  `background_ppm` are the same fragment. With `background_enabled`, those
  found in at least `background_min_percent` % of the precursors (per
  polarity) are removed from every spectrum. The list of shared fragments
  (at least 5 % of precursors, or removed) is always computed and shown on
  the Summary page, so you can look before turning removal on. Beware of
  real fragments shared by a whole compound class, e.g. m/z 184.07 of
  phosphocholine lipids.
- **Fragments above the precursor.** With `above_precursor_enabled`, peaks
  above the precursor m/z + `above_precursor_ppm` are removed: a singly
  charged ion can't fragment into anything heavier. The precursor m/z is the
  matched feature's, else the scan's own. Scans with a precursor charge of 2
  or more are left alone.

Grouping (flat-fragmentation, fragmentation factor, peak counts),
annotation and the "filtered" mirror plot all use the cleaned spectra; the
"raw" mirror plot shows the spectrum as acquired. Both settings can be tuned
afterwards with **Re-annotate**. See ADR 64.

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
[MS2 annotation](ms2-annotation.md). `annotate.ms2_enabled: false` skips
this stage while keeping the libraries for the next one.

## Stage 9.5 — Annotate by precursor m/z (opt-in, → analysis DB)

With `annotate.precursor_enabled`, every feature — with or without MS2 — is
matched by its m/z alone against the precursors of every library spectrum,
within `annotate.precursor_ppm`. Every candidate is kept in
`precursor_annotations`, one per compound and adduct, ranked by absolute ppm
error. The reference m/z is the library's stored precursor m/z when it has at
least 4 decimals (public libraries often round it), else the m/z computed
from the compound formula and the spectrum's adduct.

A mass alone can't tell isomers apart: they share a formula and so tie on
ppm. So a feature with no target-list or MS2 match is named by its best
precursor hit as `formula + adduct (n candidates)`, e.g.
`C42H82NO8P + [M+H]+ (12 candidates)`, ahead of a predicted formula. In
Annotations, a feature with both kinds of hits gets an **MS2 / Precursor**
switch above its top hits; precursor hits list their ppm error. The export
and `.var` get `precursor_name`, `precursor_ppm` and
`n_precursor_candidates`, and every candidate is in
`.uns["precursor_candidates"]` (see [Outputs](outputs.md)). See ADR 65.

**Which m/z is matched.** A feature's listed m/z is the plain mean of its
samples' centroids, rounded to `align.mz_decimals`, and stays its name
everywhere (`.var`, exports). Precursor matching instead uses the
*intensity-weighted* mean of those centroids, unrounded (a stronger peak has
a more precise centroid), and records how far the samples disagree, as a
standard deviation in ppm. Annotations shows it next to each ppm error
(`-1.80 ppm (±0.50 ppm)`). Candidates whose |ppm| differ by less than that
spread can't be told apart by mass, so they share a rank (`1. A`, `1. B`,
`3. C`); the details panel says how many a hit is tied with. Centroids are
already on the 0.0001 Da bin grid, so at low m/z (~0.7 ppm at m/z 70) that
grid, not the averaging, limits precision. Older analyses get this on their
next Re-annotate.

The Annotations name search also looks through each feature's candidates,
MS2 and precursor, up to the **Top-N** set in the top-hits panel: a feature
shown because of a candidate says which one (`matched: Theophylline
(precursor #3)`), and selecting it opens that list on that candidate.
Untick **Search in candidates** to search only the names in the table.

## Stage 10 — MS2 consensus (→ analysis DB)

`run_consensus()` picks one representative MS2 scan per feature —
`consensus_score = best library score × precursor_frac term × peak term` — into
`feature_ms2_consensus`. Works library-free and purity-free. Controlled by
`consensus.*`; set `consensus.enabled: false` to skip.

## Stage 11 — TIC normalization

`run_tic_normalization()` computes each pixel's TIC relative to the
dataset-wide median TIC (across every pixel, every sample) and uses it to
add `layers['raw']` (untouched) and `layers['TIC']` (normalized,
log1p-compressed) to every `<sample>.h5ad`, plus a `merged.h5ad` — all
samples concatenated with a `sample` obs column — needed to compute the
median and kept for future cross-sample analyses. Controlled by
`normalization.*`; set `normalization.enabled: false` to skip.

## Stage 11.5 — Spatial correlation (opt-in)

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

Forgot to enable it? You don't need to re-run. The Spatial Correlation tab's
**Compute Moran's I** button scores the analysis' own samples afterwards
(global and/or local, on the normalized layer or raw `X`), still one sample
at a time. If scores already exist, it asks before overwriting them.

## After the run — target list without re-running

Forgot some compounds, or have a new list? **Analyses → Match target list…**
matches a target list against a finished analysis. It takes the same inputs
as the config's `target_list` section (files, polarity, adducts,
tolerance), pre-filled from the analysis' own settings.

A target-list file (CSV/TXT, for both this and the config) has a `name`
column, then `formula` and/or `mz` (or `m/z`); `inchikey` is optional. Each
row needs a formula or an m/z:

- **formula**: searched across the chosen adducts.
- **mz**: searched at that ion m/z only, with no adducts. **When a row has
  both, the m/z wins**: only the m/z is searched, and the formula is kept for
  display. In the table its adduct shows as `m/z`.

```
name,formula,mz
Glucose,C6H12O6,
Unknown 1,,327.0718
Citrate,C6H8O7,191.0197
```

**Find matches** shows a table, one row per compound and adduct that lands
on an existing feature, with the feature's current name. Tick **Use** for
the rows whose target name should label the feature. Rows are pre-ticked
only where the feature has no name yet, and a feature takes at most one
target name. Compounds no feature matched are listed greyed out as "not
detected". Adding them as new features needs the raw data, so that only
happens in a pipeline run.

**Apply** saves the ticked rows. The names then appear everywhere
(Annotations, Visual Inspection, MS1, Spatial Correlation, exports), with
the same priority as run-time target-list matches. **Remove post-run
matches** undoes every name saved this way and keeps the run's own matches.

## After the run — normalize to a feature

**Analyses → Normalize to feature…** normalizes a finished untargeted
analysis to one feature (an internal standard, say) without re-running it.
Search the feature by name or m/z, pick it, and click **Normalize**. It runs
in the background with a progress bar.

The math is the same as the config's `normalization.mode: reference_mz`.
Each pixel is divided by the feature's intensity relative to that feature's
median over every pixel of every sample, then `log1p`. Pixels where the
feature has no signal are 0. The dialog reports how many there are, so
pick a feature present across the tissue.

The result is a new layer, `feature_<id>`, in every sample `.h5ad` and in
`merged.h5ad`. Nothing else changes. In Visual Inspection, pick it from
**Normalized to feature…** under the Raw / TIC buttons. Normalizing to the
same feature again replaces its layer.

The dialog lists the layers already added. Click ✕ next to one, then
**Remove**, to delete it from every file. The files don't get smaller:
HDF5 keeps the freed space.

## After the run — re-annotate

Want another library, a different tolerance or a different flat-spectrum
threshold? **Analyses → Re-annotate…** re-runs only the MS2 part of a
finished untargeted analysis: MS2 cleaning and grouping, precursor purity,
library annotation (MS2 and precursor m/z) and consensus. The features, images and normalization stay as
they are, so it takes minutes, not a full run.

The dialog has one tab per settings group (the same fields as in New
Analysis), pre-filled with the settings now in effect. **Re-annotate** runs
in the background. The Annotations tab, Visual Inspection names, the
Summary page, the exports, `.var` and `summary_report.html` all follow the
new annotation, and the analysis' saved config is updated to the new
settings.

Nothing is lost. The **History** tab lists every annotation of the
analysis: the run's own, each re-annotation and each restore, with its date,
how many features got a library match and its main settings.

- **Restore** makes an earlier annotation current again, as a new entry.
- **Load settings** copies an entry's settings into the tabs, to tweak and
  re-annotate.
- **Delete** forgets an entry you no longer need. The current one can't be
  deleted.

Each entry keeps a copy of the MS2 tables in the analysis folder, under
`annotation_history/`. Delete entries you won't need again to free the
space. If a re-annotation fails, the previous annotation is put back.

## Stage 12 — Summary report (→ out_dir)

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

