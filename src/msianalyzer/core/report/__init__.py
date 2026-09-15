"""End-of-run reporting.

:mod:`msianalyzer.core.report.summary` builds ``summary_report.html`` +
``summary.json`` from a finished analysis database: per-sample scan / peak
counts, a feature-overlap UpSet plot, and the MS2 association / base peak
intensity / precursor-purity distributions. It only reads — the analysis
and raw databases are never modified.
"""

from .summary import build_summary_report

__all__ = ["build_summary_report"]
