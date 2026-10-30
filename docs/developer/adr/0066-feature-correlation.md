# 66 — Feature correlation

**Status:** Accepted

## Context

Moran's I (ADR 48) tells whether one feature is spatially organized. It
says nothing about which features share a pattern: which metabolites
colocalize, which avoid each other. Users did this in notebooks.

## Decision

- **What:** for each sample separately, the correlation between every pair
  of features across that sample's pixels. Samples are never pooled, the
  same rule as Moran's I. `feature_correlation.method` is `pearson`
  (default) or `spearman`, on `feature_correlation.layer` (default `TIC`,
  already log1p).
- **How:** `core.spatial.feature_correlation.correlation_matrix` adds up
  `block.T @ block` and the column sums over densified pixel blocks
  (~20M values each), then turns them into correlations. Numpy's matrix
  product is multithreaded, so that is the parallelism; no process pool.
  About 0.2 s (Pearson) and 1 s (Spearman) for 30,000 pixels x 1,000
  features.
- **Spearman stays sparse:** all of a column's zeros share one rank,
  `(z + 1) / 2`. Shifting the whole column by that rank leaves every
  correlation unchanged and puts the zeros back at 0. Dense input, or
  input with negative values, is ranked dense.
- **No variance, no score:** a feature that is constant in a sample (e.g.
  never detected) gets a NaN row and column, not a number made of rounding
  error.
- **Storage:** `adata.varp["featureCorrelation"]` (float32, `n_var x
  n_var`), AnnData's own slot for var x var matrices, plus
  `uns["featureCorrelation"] = {method, layer}`. Written element by element
  with h5py (ADR 50), so the rest of the file is untouched. The GUI reads
  one row per sample (`read_correlation_row`), never the whole matrix.
- **Config:** a new group `feature_correlation` (`enabled`, `layer`,
  `method`), config version 24. It gets its own New Analysis tab, generated
  like the others, and is hidden for targeted analyses. The stage runs
  right after `spatial_correlation`.
- **After the run:** the Feature Correlation tab's **Compute feature
  correlation** bar runs it on the active dataset (the pipeline output or
  an imported one, ADR 49), sample by sample, and asks before overwriting
  (`imported_dataset.compute_dataset_feature_correlation`).
- **GUI:** its own tab, **Feature Correlation**, after Spatial Correlation
  (not a sub-tab of it). Left: the feature list with name and m/z search.
  Right: **Most correlated** and **Most anti-correlated**, N rows each
  (default 25): name, m/z, mean r over the samples that have a score, then
  r per sample. Right-click a row for **Show spatial distribution** (the
  ADR 32 handoff to Visual Inspection). The selection, N and search are
  kept across tab switches. Rows are a `ListView`, not a `TableView`, so
  they render under the test harness too.

## Consequences

- The matrix is `n_var**2` floats per sample: 4 MB at 1,000 features, 400
  MB at 10,000. If analyses that large become common, store only the top N
  per feature instead.
- **Zeros dominate the score.** A pixel where a feature isn't detected
  holds 0, far below its detected values (~8-10 in the `TIC` layer), so r
  mostly measures whether two features are detected in the same pixels.
  That is what colocalization means, but every feature spread over the
  whole tissue then correlates with every other one. Restricting the score
  to an ROI (as deferred for Moran's I) would fix that; not built.
- A sample with no stored matrix simply has no column in the tables; the
  mean is over the samples that have a score.
