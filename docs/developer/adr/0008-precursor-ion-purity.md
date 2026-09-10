# 8 — Precursor ion purity (Stage A′)

**Status:** Accepted

## Context

The grouper ([ADR 2](0002-ms2-feature-association-design.md)) records
`n_features_in_window` — the number of aligned `features` inside a scan's
isolation window — as its chimericity flag, and `feature_ms2_summary.n_chimeric`
rolls it up. The `features` list is the union of aligned peaks over **every
sample and every pixel**. On a large acquisition a typical 1–4 Da isolation
window straddles several features even when the scan's own parent MS1 held a
single clean peak there, so the flag fires on the vast majority of scans and
stops discriminating. It measures feature-grid density, not co-isolation.

What actually contaminates a fragment spectrum is other **ions**, present in the
MS1 the instrument fragmented, at the moment and place it fragmented them.
`ms2_scans.parent_scan_id` already links each MS2 to that survey scan; profile
MS1 arrays are already stored. The physics question — precursor ion purity /
interference, cf. msPurity in metabolomics — can be answered from data on hand.

A wrinkle specific to imaging: the stage moves continuously, so an MS2's ions can
originate between two pixels. Bracketing the MS2 with the parent MS1 **and** the
next MS1 and interpolating by retention time captures that — but only when the
two MS1 scans sampled nearly the same spot, i.e. the same pixel or the next
pixel on the same raster line. Whether the fast raster axis is `x` or `y`, and
whether rows alternate direction (serpentine) or fly back, varies by
instrument/config and must not be assumed.

## Decision

New module `annotation/precursor_purity.py`, same pure-core + thin-IO split as
`group_ms2.py`; new run-wide step `precursor_purity` in `run.py`, after the
grouper and before the annotator; new table `precursor_purity` folded into
`analysis_db.create_analysis_schema` ([ADR 6](0006-schema-single-source-of-truth.md));
new config group `purity` (`PurityConfig`), schema `version` 5 → 6.

Design choices:

1. **Measure against the parent MS1, not the feature list.** Parent =
   `ms2_scans.parent_scan_id` when set, else the nearest earlier MS1 (same
   polarity). The feature list is not consulted at any point — the outputs stay
   meaningful however many samples the analysis spans.
2. **Only the isolation window is peak-picked**, with a purpose-built local
   picker (local maxima above `min_rel_intensity × window base peak`, parabolic
   refinement, `merge_ppm` merge) — not `detect_ms1_centroids`, whose baseline
   model is meant for a full averaged spectrum. A thin slice is sub-millisecond,
   so no global MS1 peak detection and no precompute.
3. **Store a measurement, not a verdict.** Each row carries `purity`,
   `purity_parent`, `n_peaks_in_window`, `runner_up_rel_int`, `precursor_found`
   and the resolved window bounds. The chimeric cutoff (e.g. `purity < 0.8`) is a
   query-time choice.
4. **Interpolate across parent + next MS1** (`use_next_ms1`, default on) only
   when the next MS1 is the **same pixel** or **`same_line`**: adjacent
   `pixel_id`s whose slow-axis index is equal, fast-axis index differs by one,
   and `t_start(next) − t_end(parent)` is within `max_interpixel_gap_sec`
   (auto-derived from the median in-line gap when null). The fast axis is
   *inferred* per sample from `spatial_pixels` (`infer_raster_geometry`);
   `|Δfast| == 1` covers serpentine. `bracket_kind` records which case fired.
5. **Compare pixel identity and indices, never raw coordinates.** A "same row"
   test on one axis would be the wrong grain (that is a row, not a pixel), and
   hard-coding the fast axis would break on transposed or serpentine rasters.
6. **Degrade to parent-only** whenever the geometry is unknowable: no
   `spatial_pixels` (mapping never run), `< 2` pixels, un-callable step pattern,
   parent MS1 in an inter-pixel dead zone, or `use_next_ms1 = false`. Then
   `purity == purity_parent` and `bracket_kind = parent_only`.
7. **Re-run replaces every row** (`DELETE FROM precursor_purity` then repopulate),
   like `features` — one purity computation per analysis. `samples` and the
   grouper tables are untouched.

## Alternatives considered

- **Fix the grouper's `n_features_in_window`** to count only features seen in
  *that sample's* filtered peak list. Cheaper (no MS1 slicing) and a real
  improvement, but still a per-sample average over all pixels, and still a
  feature-list proxy rather than a measurement of ion current. Kept as the cheap
  fallback idea, not the design.
- **Peak-detect every MS1 scan up front.** The only artefact that would justify a
  precompute, but unnecessary: only the ~1–4 Da window matters, and per-scan
  slicing is fast enough to do inline. Rejected as storage/complexity with no
  payoff below millions of MS2.
- **Use the previous *and* next MS1** (symmetric bracket, classic msPurity). The
  laser only moves forward and the MS2 always follows its parent, so the earlier
  MS1 is a different, already-passed spot — forward-only is both simpler and more
  correct here.
- **A boolean `is_chimeric` column.** Discards the runner-up intensity and the
  count, and freezes a threshold into the table. Rejected — see choice 3.

## Consequences

- `run.py` gained one run-wide step; `PurityConfig` adds a `purity` group and
  bumps config `version` to 6 (old v5 files now fail to load — expected for a
  schema change).
- The parser already captures `parent_scan_id`; no raw-schema change was needed.
- `n_features_in_window` / `feature_ms2_summary.n_chimeric` stay as they are.
  Wiring the grouper (and `ms2_annotations.is_chimeric`) to consume `purity` is
  left to a later change; for now the narrative points users at the purity table.
- `precursor_purity` is one row per MS2 scan per analysis — small next to
  `ms2_annotations`.
- Scale: on a real run (10^6 MS2 scans) the parent/next resolution must not
  touch SQLite per scan — `pixel_ms1_scans` has no `scan_id` index and the
  MS1-array cache would blow memory if it kept every parent. `SampleScanIndex`
  preloads the MS1 rt/polarity list, the scan→pixel map and pixel geometry once
  per sample (bisect for parent/next), and holds MS1 arrays in a small LRU;
  `map_pixels_to_db` also now writes `idx_pms1_scan`.
- **Follow-up (v8): peak-detection-free confirmation + m/z snap.** On real
  MALDI-imaging data the step-2 peak-picker failed to emit a peak at
  `precursor_mz` for ~56 % of scans in dense, matrix-heavy, low-m/z 2-Da
  windows — even though the precursor signal was plainly there (direct
  integration: median ~48 % of the window base peak, never zero). So the stage
  now also records `precursor_frac` (above-baseline profile area within
  `precursor_confirm_ppm` of `precursor_mz`, over the window — no peak needed),
  the boolean `precursor_confirmed`, and `precursor_mz_snapped` /
  `snap_shift_ppm` (snap to the nearest parent-MS1 local max within a tight
  `precursor_snap_ppm`). Association is **not** re-run — the aligned feature
  list is a better m/z reference than any single survey scan, and re-deriving
  the precursor from the window argmax would mis-assign the ~50 % of scans
  where the precursor is a minor co-isolate. `precursor_confirmed` /
  `precursor_frac` are carried onto `ms2_annotations` as an annotation-
  confidence gate. Config `version` 7 → 8.
