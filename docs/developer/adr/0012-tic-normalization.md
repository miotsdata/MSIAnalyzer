# 12 — TIC normalization + merged AnnData

**Status:** Accepted

## Context

The upcoming Visual Inspection GUI section needs to compare a feature's
spatial intensity pattern across samples, and raw intensities aren't
directly comparable across samples — pixel-to-pixel and sample-to-sample
ionization efficiency varies. Some normalization by total ion current (TIC)
is needed, and the GUI should never have to compute it on demand: it's a
cross-sample quantity (the normalization reference needs every sample's
pixels), too expensive to redo on every page load, and worth getting right
once, in the pipeline, not per-view.

Each per-sample `.h5ad` (`create_spatial_adata`) already carries
`obs['tic']` — the mean raw per-scan total ion current, parsed from the
mzML `MS:1000285` field — but nothing consumes it for normalization, and
`.X` (the matched/aligned feature-intensity matrix) has no raw/normalized
distinction; there's exactly one matrix.

## Decision

New pipeline stage, `normalize_tic` (`core.utils.tic_normalization`), run
once per analysis after `assemble_adata`, gated by a new `NormalizationConfig`
(`enabled: bool = True`) — `Config` version bumped 12 → 13.

Per pixel *i*, feature *j*, computed once across every sample in the
analysis:
```
median_tic = median(obs['tic'] over every pixel, every sample)
norm_factor[i] = obs['tic'][i] / median_tic
X_tic[i, j] = log1p(X_raw[i, j] / norm_factor[i])
```
Pixels (or the whole dataset) with a zero/non-positive TIC normalize to `0`
rather than dividing by zero — treated as "no usable signal", not an error.

Every per-sample `.h5ad` gets two new layers: `layers['raw']` (an untouched
copy of `.X` — `.X` itself is left alone) and `layers['TIC']` (the
normalized matrix above). All samples are also concatenated (`sample` obs
column added from the per-sample keys) into a `merged.h5ad`, written to
`io.out_dir` — the median needs every sample loaded anyway, and a persisted
cross-sample object is wanted for later, not-yet-built analyses. The GUI's
primary read path stays the per-sample `.h5ad` files, not `merged.h5ad`.

Same caching convention as every other stage: `analysis_db`
`log_command`/`is_command_already_run("normalize_tic", ...)` — a resumed run
skips recomputation (and re-write) when it already ran.

## Consequences

- Every per-sample `.h5ad` roughly doubles in size (one extra full float32
  matrix layer; `merged.h5ad` is a further full copy again). Acceptable for
  now; revisit (e.g. don't persist `merged.h5ad`, or drop `layers['raw']`
  since it's recoverable from the original `.X`) if storage becomes a
  problem.
- `Config` version bump to 13, no migration — matches ADR 10's precedent;
  existing config files need re-export/re-run.
- The stage reads every per-sample `.h5ad` fully into memory at once (to
  build `merged`), rather than the smaller memory footprint of
  `assemble_adata`'s per-sample loop. Fine at today's typical sample counts;
  worth revisiting (e.g. compute the global median from a cheap `obs`-only
  first pass, normalize each sample independently without holding the full
  merge in memory) if that changes.
