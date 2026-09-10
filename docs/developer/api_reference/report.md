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
        - AssocPuritySample
        - AssociatedPuritySummary
        - UnscoredPurity
        - UnscoredSummary
        - RecheckSummary
        - AnnotationLibraryInfo
        - AnnotationSummary
        - SummaryStats
        - per_sample_counts
        - feature_membership
        - overlap_combos
        - ms2_summary
        - per_sample_ms2
        - associated_purity
        - purity_unscored
        - unassociated_recheck
        - annotation_summary
        - figure_per_sample
        - figure_overlap_upset
        - figure_ms2_association
        - figure_ms2_association_per_sample
        - figure_unassociated_recheck
        - figure_purity
        - figure_purity_per_sample
        - figure_purity_unscored
        - figure_annotation_yield
        - figure_annotation_score
        - figure_annotation_ambiguity
        - figure_annotation_agreement
        - collect_stats
        - build_summary_report
