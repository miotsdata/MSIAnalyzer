# report

End-of-run summary: reads a finished analysis database and writes
`summary_report.html` + `summary.json`. Narrative:
[Outputs](../../user-guide/outputs.md).

::: msianalyzer.core.report.summary
    options:
      members:
        - SampleCounts
        - Ms2Summary
        - PerSampleMs2
        - PerSamplePurity
        - UnscoredPurity
        - UnscoredSummary
        - RecheckSummary
        - SummaryStats
        - per_sample_counts
        - feature_membership
        - overlap_combos
        - ms2_summary
        - per_sample_ms2
        - per_sample_purity
        - purity_unscored
        - unassociated_recheck
        - figure_per_sample
        - figure_overlap_upset
        - figure_ms2_association
        - figure_ms2_association_per_sample
        - figure_unassociated_recheck
        - figure_purity
        - figure_purity_per_sample
        - figure_purity_unscored
        - collect_stats
        - build_summary_report
