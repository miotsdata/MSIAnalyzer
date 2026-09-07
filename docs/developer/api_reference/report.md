# report

End-of-run summary: reads a finished analysis database and writes
`summary_report.html` + `summary.json`. Narrative:
[Outputs](../../user-guide/outputs.md).

::: msianalyzer.core.report.summary
    options:
      members:
        - SampleCounts
        - Ms2Summary
        - SummaryStats
        - per_sample_counts
        - feature_membership
        - overlap_combos
        - ms2_summary
        - purity_vs_nfw
        - figure_per_sample
        - figure_overlap_upset
        - figure_ms2_association
        - figure_nfw
        - figure_purity
        - figure_purity_vs_nfw
        - collect_stats
        - build_summary_report
