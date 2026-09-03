"""MS2 annotation pipeline.

Stage A — :mod:`msianalyzer.core.annotation.group_ms2`: associate every MS2
scan with a master feature from ``align_mz_across_samples`` (the "grouper").
Stage B — annotation against spectral libraries — will live alongside it.

Both stages write only to the per-analysis database; the raw per-sample
databases produced by the parser are never modified.
"""
