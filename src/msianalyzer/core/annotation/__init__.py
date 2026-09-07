"""MS2 annotation pipeline.

Stage A — :mod:`msianalyzer.core.annotation.group_ms2`: associate every MS2
scan with a master feature from ``align_mz_across_samples`` (the "grouper").
Stage A' — :mod:`msianalyzer.core.annotation.precursor_purity`: score each
MS2 scan's isolation-window purity against its own parent MS1 scan (and the
next MS1 scan on the same raster line), independent of the feature list.
Stage B — :mod:`msianalyzer.core.annotation.annotate`: score each associated
scan against reference spectral libraries with a coverage-aware reverse dot
product (:mod:`msianalyzer.core.annotation.spectral_match`).

Both stages write only to the per-analysis database; the raw per-sample
databases produced by the parser are never modified.
"""
