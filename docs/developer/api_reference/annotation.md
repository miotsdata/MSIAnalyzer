# annotation

Stage A of MS2 annotation — the grouper. Narrative:
[MS2 annotation](../../user-guide/ms2-annotation.md),
[ADR 2](../adr/0002-ms2-feature-association-design.md).

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
