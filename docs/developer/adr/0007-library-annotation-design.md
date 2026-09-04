# 7 — Library annotation design (Stage B)

**Status:** Accepted

## Context

Stage A ([ADR 2](0002-ms2-feature-association-design.md)) snaps every MS2 scan to
a master `feature`. Stage B must identify the metabolite: compare each scan's
fragment spectrum to reference spectral libraries and store ranked candidates.

Old code (`core/ms2_annotator.py`, `core/spectral_matching.py`) had a usable
scorer and a parallel worker, but wrote to its own standalone database, ran its
own precursor grouping, and imported the pre-split parser path. The scoring maths
was worth keeping; the data model was not.

Open questions from ADR 2 that this record closes: what m/z anchors the library
query, what to store, and what to do with chimeric scans.

## Decision

New package modules, mirroring `group_ms2.py`'s pure-core + thin-IO split:

- **`annotation/spectral_match.py`** — the ported scorer. `reverse_dot_product`
  max-normalises both spectra, drops peaks below `noise_threshold` on *both*
  sides, aligns fragments within `fragment_ppm`, and returns
  `score = dot_product_score × coverage_score`. `MatchResult` additionally
  carries the noise-filtered spectra of both sides.
- **`annotation/annotate.py`** — `run_annotation(analysis_db_path, config)`.

Design choices:

1. **Batch unit = one feature.** Library candidates are gathered *once* per
   feature (precursor m/z within `candidate_ppm` of the feature m/z) and reused
   for all of that feature's scans. Scans are already snapped to the feature
   within `assoc_ppm`, so the feature m/z is the right, stable anchor — and it
   makes the per-feature query the natural parallel unit
   (`ProcessPoolExecutor`, `batch_size` features per worker, library loaded once
   per worker via the pool initializer).
2. **Store every candidate** sharing ≥ `min_matched_peaks` fragments, each with
   a `rank` within its scan. `rank_feature` then orders a feature's scans by
   their best hit. "All comparison results", not just the top-N.
3. **Reuse the reverse-dot-product + coverage score** rather than a plain
   cosine — the coverage term is what penalises a mixed/partial spectrum.
4. **Chimeric scans** (`n_features_in_window > 1`) are scored against their
   **primary** feature only and every result row is flagged `is_chimeric = 1`
   (with `n_features_in_window` carried through). `annotate_chimeric = false`
   skips them. Attributing one spectrum to several features is left to a
   downstream join against `ms2_window_features`.
5. **Persist both filtered spectra** (`emp_filtered_*`, `lib_filtered_*`) on
   every row — zlib float32 blobs, same codec as raw scans — so a mirror plot is
   a single-row read. `store_filtered_spectra = false` writes them NULL.
6. **Empty `library_path` disables the stage** — `run_annotation` returns an
   empty result and writes nothing; `run.py` does not even log a command.
7. **`library_path` may be a list.** `normalize_library_paths` coerces
   `None` / str / list to a de-duplicated list; each library gets its own
   `annotation_libraries` row, workers load them all once, and per scan the
   candidates from every library are pooled before ranking (so `rank` is the
   best hit across all libraries). Each `ms2_annotations` row carries its own
   `library_id`; a re-run clears rows for every configured `library_id`.

Two tables, folded into `analysis_db.create_analysis_schema`
([ADR 6](0006-schema-single-source-of-truth.md)): `annotation_libraries` (one
row per library used) and `ms2_annotations` (one row per scored candidate).

## Alternatives considered

- **Query the library per scan around its own `precursor_mz`** — marginally more
  precise, but multiplies library queries and breaks the per-feature batch.
- **Top-N per scan only** — smaller table, but the user explicitly wants the full
  ranked list, and the long tail is cheap to filter in SQL.
- **A separate `ms2_annotation_spectra` table** for the filtered empirical
  spectrum (one row per scan, deduplicated) — normalises away the repeated
  empirical blob, but needs an extra join for the common "plot this hit" case.
  Rejected for query simplicity; `store_filtered_spectra = false` is the escape
  hatch when size matters.

## Consequences

- `ms2_annotations` can grow large on big libraries (every ≥1-peak candidate ×
  every scan, ×4 spectrum blobs). `min_matched_peaks`, `candidate_ppm` and
  `store_filtered_spectra` are the levers.
- `libviz` is imported lazily (`load_library`), so the module and its pure-helper
  tests do not need it.
- Config schema `version` 4 → 5; `run.py` gained one run-wide `annotate_ms2`
  step after the grouper.
- The scorer now lives under `annotation/`; `plotting/plotter.py` imports
  `_align_peaks` from the new path.
