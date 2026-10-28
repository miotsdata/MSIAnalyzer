# 62 — Why a feature got no library hit (MS2 annotation outcome)

**Status:** Accepted

## Context

A feature with MS2 but no library hit could mean three different things,
and nothing told them apart: no library spectrum near its m/z, candidates
that shared no fragment with its spectrum, or every scan dropped before
scoring. A candidate below `min_matched_peaks` is not stored at all (not
stored with score 0), so the hit table can't answer it afterwards either.

## Decision

- **One outcome per MS2-bearing feature**, decided in
  `annotate._annotate_feature_batch` (inside the existing process pool) and
  stored in a new table, `feature_annotation_outcome`, replaced on every
  annotation run:
  - `annotated` — at least one candidate stored;
  - `no_shared_fragments` — candidates within `candidate_ppm`, none sharing
    `min_matched_peaks` fragments;
  - `no_candidates` — no library spectrum within `candidate_ppm` for the
    scans' polarity;
  - `all_scans_skipped` — every scan `flat_fragmentation`, below
    `min_purity_score`, or unreadable.
  With scans of both polarities the best one wins, in that order.
- **Counts behind it:** `n_scans`, `n_skipped_flat`, `n_skipped_purity`,
  `n_skipped_unreadable`, `n_candidates`, `best_n_matched_peaks`. With the
  default `min_matched_peaks = 1`, an unmatched feature's best is 0 by
  definition; above 1, its candidates are re-scored with no minimum just
  to report how close the best one came (extra cost only in that case).
- **No MS2 = no row.** Annotation only visits features in
  `ms2_associations`, so readers report `no_ms2` for a feature without a
  row — but only when the table has rows at all. Otherwise (no library, or
  an analysis annotated before this ADR) the outcome is unknown (`NULL`),
  never a misleading `no_ms2`.
- **Not separated:** "a candidate exists, but only for the other
  polarity" counts as `no_candidates`. Rare, and it would need a second
  library lookup.
- **Shown in:** `ms2_annotation_outcome` in the Annotation export and so in
  `.var` (same columns); the Annotations table's non-annotated labels and
  details panel; a "Where MS2 annotation lost features" row of tiles on
  the Summary page.

## Consequences

- No config change, so no config version bump. The schema refresh on
  opening an analysis adds the empty table; outcomes only appear in a new
  run (which reuses the parsed raw databases, not the later steps). Older
  analyses keep today's labels.
- `_annotate_feature_batch` returns `(rows, outcomes)`;
  `persist_annotations` takes `outcomes=` and leaves the table alone when
  it isn't passed.
