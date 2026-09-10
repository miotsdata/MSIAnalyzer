# annotation

MS2 annotation — Stage A (the grouper), Stage A′ (precursor ion purity),
Stage B (spectral-library matching) and Stage A″ (per-feature consensus).
Narrative: [MS2 annotation](../../user-guide/ms2-annotation.md),
[ADR 2](../adr/0002-ms2-feature-association-design.md),
[ADR 8](../adr/0008-precursor-ion-purity.md),
[ADR 7](../adr/0007-library-annotation-design.md),
[ADR 9](../adr/0009-consume-purity-and-consensus.md).

## Stage A — `group_ms2`

::: msianalyzer.core.annotation.group_ms2
    options:
      members:
        - ScanAssociation
        - WindowFeature
        - FeatureMs2Summary
        - GroupingResult
        - associate_scan
        - group_ms2
        - summarize_features
        - detect_precursor_only
        - ppm_between
        - persist_grouping
        - run_grouper

## Stage A′ — `precursor_purity`

::: msianalyzer.core.annotation.precursor_purity
    options:
      members:
        - RasterGeometry
        - WindowPurity
        - ResolvedScans
        - SampleScanIndex
        - PurityRow
        - PurityResult
        - window_bounds
        - detect_window_peaks
        - integrate_precursor_fraction
        - snap_precursor_mz
        - score_window
        - interpolate_purity
        - infer_raster_geometry
        - resolve_parent_next
        - compute_scan_purity
        - persist_purity
        - run_precursor_purity

## Stage A″ — `consensus`

::: msianalyzer.core.annotation.consensus
    options:
      members:
        - ScanStat
        - ConsensusRow
        - ConsensusResult
        - peak_term
        - purity_term
        - consensus_score
        - pick_feature
        - build_consensus
        - persist_consensus
        - run_consensus

## Stage B — `spectral_match`

::: msianalyzer.core.annotation.spectral_match
    options:
      members:
        - MatchResult
        - reverse_dot_product

## Stage B — `annotate`

::: msianalyzer.core.annotation.annotate
    options:
      members:
        - Candidate
        - LibraryInfo
        - AnnotationRow
        - AnnotationResult
        - normalize_polarity
        - normalize_library_paths
        - score_scan_against_candidates
        - rank_scan_rows
        - assign_rank_feature
        - annotate_feature
        - load_library
        - persist_annotations
        - run_annotation
