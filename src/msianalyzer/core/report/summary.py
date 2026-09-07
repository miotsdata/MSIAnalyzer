"""Build the end-of-run summary report (``summary_report.html`` + ``summary.json``).

Pure stat functions (``per_sample_counts``, ``feature_membership``,
``overlap_combos``, ``ms2_summary``, ``purity_vs_nfw``) read the analysis
database (and each sample's raw database, read-only) and return plain data;
the ``figure_*`` builders turn that data into Plotly figures;
:func:`build_summary_report` glues it together and writes the two files.
Nothing here writes to a database.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from ..analysis_db import load_features
from ..utils.logging_utils import log_call

if TYPE_CHECKING:  # avoid importing the config package at module load
    from ..config.config import ReportConfig

logger = logging.getLogger(__name__)

__all__ = [
    "SampleCounts",
    "Ms2Summary",
    "SummaryStats",
    "per_sample_counts",
    "feature_membership",
    "overlap_combos",
    "ms2_summary",
    "purity_vs_nfw",
    "figure_per_sample",
    "figure_overlap_upset",
    "figure_ms2_association",
    "figure_nfw",
    "figure_purity",
    "figure_purity_vs_nfw",
    "collect_stats",
    "build_summary_report",
]

_REPORT_HTML = "summary_report.html"
_REPORT_JSON = "summary.json"


# ---------------------------------------------------------------------------
# containers
# ---------------------------------------------------------------------------


@dataclass
class SampleCounts:
    """Per-sample scan / pixel / peak / feature counts."""

    sample_id: int
    name: str
    n_ms1: int
    n_ms2: int
    n_pixels: int
    n_detected_peaks: int
    n_features: int


@dataclass
class Ms2Summary:
    """Analysis-wide MS2 association + window + purity roll-up."""

    n_total: int
    n_associated: int
    n_unassociated: int
    n_precursor_only: int
    n_empty_window: int
    n_unique_window: int
    n_chimeric_window: int
    nfw_distribution: dict[int, int]
    n_purity_scored: int
    n_low_purity: int
    purity_cutoff: float
    purity_values: list[float] = field(default_factory=list)


@dataclass
class SummaryStats:
    """Everything the report renders."""

    samples: list[SampleCounts]
    n_features: int
    overlap_combos: list[tuple[list[str], int]]
    ms2: Ms2Summary

    def to_dict(self) -> dict:
        d = asdict(self)
        # purity_values is large and already summarised — keep the JSON small
        d["ms2"].pop("purity_values", None)
        d["ms2"]["n_purity_values"] = len(self.ms2.purity_values)
        d["overlap_combos"] = [
            {"samples": list(s), "n_features": n} for s, n in self.overlap_combos
        ]
        return d


# ---------------------------------------------------------------------------
# small raw-DB helpers
# ---------------------------------------------------------------------------


def _scalar(con: sqlite3.Connection, sql: str, default: int = 0) -> int:
    try:
        row = con.execute(sql).fetchone()
    except sqlite3.OperationalError:
        return default
    return int(row[0]) if row and row[0] is not None else default


def _detected_peak_count(con: sqlite3.Connection, sample_id: int) -> int:
    """Length of the sample's most recent ``filter_spectra`` aggregated array."""
    try:
        row = con.execute(
            "SELECT a.mz_array FROM aggregated_spectra a "
            "JOIN commands c ON a.command_id = c.id "
            "WHERE c.command_name = 'filter_spectra' AND a.sample_id = ? "
            "ORDER BY a.id DESC LIMIT 1",
            (sample_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return 0
    if row is None or row[0] is None:
        return 0
    from ..parser.mzml_parser import blob_to_array

    return int(np.asarray(blob_to_array(row[0])).size)


# ---------------------------------------------------------------------------
# pure stats
# ---------------------------------------------------------------------------


@log_call(source="analysis_db_path")
def per_sample_counts(
    analysis_db_path: Path | str,
    raw_db_paths: dict[int, str | Path] | None = None,
) -> list[SampleCounts]:
    """One :class:`SampleCounts` per row of ``samples``.

    ``raw_db_paths`` optionally overrides where each ``sample_id``'s raw
    database lives; by default ``samples.raw_db_path`` is used.
    """
    analysis_db_path = Path(analysis_db_path)
    with sqlite3.connect(analysis_db_path) as con:
        samples = con.execute(
            "SELECT sample_id, name, raw_db_path FROM samples ORDER BY sample_id"
        ).fetchall()

    try:
        features_df = load_features(analysis_db_path)
    except Exception:  # pragma: no cover - defensive
        features_df = pd.DataFrame()

    out: list[SampleCounts] = []
    for sample_id, name, raw_db_path in samples:
        path = (
            raw_db_paths.get(sample_id, raw_db_path)
            if raw_db_paths is not None
            else raw_db_path
        )
        n_ms1 = n_ms2 = n_pixels = n_peaks = 0
        if path and Path(path).exists():
            with sqlite3.connect(str(path)) as rcon:
                n_ms1 = _scalar(rcon, "SELECT COUNT(*) FROM ms1_scans")
                n_ms2 = _scalar(rcon, "SELECT COUNT(*) FROM ms2_scans")
                n_pixels = _scalar(rcon, "SELECT COUNT(*) FROM spatial_pixels")
            with sqlite3.connect(analysis_db_path) as acon:
                n_peaks = _detected_peak_count(acon, sample_id)
        n_feat = (
            int(features_df[name].notna().sum())
            if name in getattr(features_df, "columns", [])
            else 0
        )
        out.append(
            SampleCounts(
                sample_id=int(sample_id),
                name=str(name),
                n_ms1=n_ms1,
                n_ms2=n_ms2,
                n_pixels=n_pixels,
                n_detected_peaks=n_peaks,
                n_features=n_feat,
            )
        )
    return out


def feature_membership(features_df: pd.DataFrame) -> pd.DataFrame:
    """Boolean ``features x samples`` matrix: True where the sample contributed."""
    if features_df is None or features_df.empty:
        return pd.DataFrame()
    return features_df.notna()


def overlap_combos(
    membership: pd.DataFrame, top_n: int = 30
) -> list[tuple[list[str], int]]:
    """``(sorted sample names, feature count)`` per distinct membership pattern.

    Largest patterns first, truncated to ``top_n``.
    """
    if membership is None or membership.empty:
        return []
    cols = list(membership.columns)
    patterns: dict[tuple[str, ...], int] = {}
    for _, row in membership.iterrows():
        combo = tuple(c for c in cols if bool(row[c]))
        if combo:
            patterns[combo] = patterns.get(combo, 0) + 1
    ordered = sorted(patterns.items(), key=lambda kv: kv[1], reverse=True)
    return [(list(combo), n) for combo, n in ordered[: max(0, top_n)]]


@log_call(source="analysis_db_path")
def ms2_summary(
    analysis_db_path: Path | str, *, purity_cutoff: float = 0.8
) -> Ms2Summary:
    """Association / isolation-window / purity roll-up over every MS2 scan."""
    analysis_db_path = Path(analysis_db_path)
    with sqlite3.connect(analysis_db_path) as con:
        assoc = con.execute(
            "SELECT feature_id, n_features_in_window, precursor_only "
            "FROM ms2_associations"
        ).fetchall()
        purity = [
            float(r[0])
            for r in con.execute(
                "SELECT purity FROM precursor_purity WHERE purity IS NOT NULL"
            ).fetchall()
        ]

    n_total = len(assoc)
    n_assoc = sum(1 for f, _, _ in assoc if f is not None)
    n_prec_only = sum(1 for _, _, p in assoc if p)
    nfw = [int(n) if n is not None else 0 for _, n, _ in assoc]
    dist: dict[int, int] = {}
    for v in nfw:
        dist[v] = dist.get(v, 0) + 1
    n_empty = sum(1 for v in nfw if v == 0)
    n_unique = sum(1 for v in nfw if v == 1)
    n_chimeric = sum(1 for v in nfw if v > 1)
    n_low = sum(1 for v in purity if v < purity_cutoff)

    return Ms2Summary(
        n_total=n_total,
        n_associated=n_assoc,
        n_unassociated=n_total - n_assoc,
        n_precursor_only=n_prec_only,
        n_empty_window=n_empty,
        n_unique_window=n_unique,
        n_chimeric_window=n_chimeric,
        nfw_distribution=dict(sorted(dist.items())),
        n_purity_scored=len(purity),
        n_low_purity=n_low,
        purity_cutoff=float(purity_cutoff),
        purity_values=purity,
    )


@log_call(source="analysis_db_path")
def purity_vs_nfw(
    analysis_db_path: Path | str,
) -> tuple[np.ndarray, np.ndarray]:
    """Paired ``(purity, n_features_in_window)`` for scans that have both."""
    analysis_db_path = Path(analysis_db_path)
    with sqlite3.connect(analysis_db_path) as con:
        rows = con.execute(
            "SELECT p.purity, a.n_features_in_window "
            "FROM precursor_purity p "
            "JOIN ms2_associations a "
            "  ON a.sample_id = p.sample_id AND a.scan_id = p.ms2_scan_id "
            "WHERE p.purity IS NOT NULL AND a.n_features_in_window IS NOT NULL"
        ).fetchall()
    if not rows:
        return np.array([]), np.array([])
    purity = np.array([float(r[0]) for r in rows])
    nfw = np.array([int(r[1]) for r in rows])
    return purity, nfw


# ---------------------------------------------------------------------------
# figures
# ---------------------------------------------------------------------------

_EMPTY_NOTE = dict(
    xref="paper", yref="paper", x=0.5, y=0.5, showarrow=False,
    text="no data", font=dict(size=16, color="#888"),
)


def _empty(title: str) -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(**_EMPTY_NOTE)
    fig.update_layout(title=title, xaxis=dict(visible=False), yaxis=dict(visible=False))
    return fig


def figure_per_sample(counts: Sequence[SampleCounts]) -> go.Figure:
    """Grouped bar of the five per-sample counts (log y)."""
    if not counts:
        return _empty("Per-sample counts")
    names = [c.name for c in counts]
    metrics = [
        ("MS1 scans", [c.n_ms1 for c in counts]),
        ("MS2 scans", [c.n_ms2 for c in counts]),
        ("pixels", [c.n_pixels for c in counts]),
        ("detected peaks", [c.n_detected_peaks for c in counts]),
        ("features", [c.n_features for c in counts]),
    ]
    fig = go.Figure()
    for label, values in metrics:
        fig.add_bar(name=label, x=names, y=values)
    fig.update_layout(
        title="Per-sample counts",
        barmode="group",
        yaxis=dict(title="count", type="log"),
        xaxis=dict(title="sample"),
        legend=dict(orientation="h"),
    )
    return fig


def figure_overlap_upset(
    combos: Sequence[tuple[Sequence[str], int]], sample_names: Sequence[str]
) -> go.Figure:
    """Hand-rolled UpSet: intersection-size bars over a membership dot matrix."""
    if not combos or not sample_names:
        return _empty("Feature overlap across samples")

    names = list(sample_names)
    idx = list(range(len(combos)))
    sizes = [n for _, n in combos]

    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.62, 0.38],
        vertical_spacing=0.04,
    )
    fig.add_bar(x=idx, y=sizes, marker_color="#1f77b4", name="features", row=1, col=1)

    on_x, on_y, off_x, off_y = [], [], [], []
    for i, (combo, _) in enumerate(combos):
        member = set(combo)
        for j, s in enumerate(names):
            if s in member:
                on_x.append(i)
                on_y.append(j)
            else:
                off_x.append(i)
                off_y.append(j)
    fig.add_scatter(
        x=off_x, y=off_y, mode="markers",
        marker=dict(size=9, color="#dddddd"), showlegend=False, row=2, col=1,
    )
    fig.add_scatter(
        x=on_x, y=on_y, mode="markers",
        marker=dict(size=11, color="#1f77b4"), showlegend=False, row=2, col=1,
    )
    fig.update_yaxes(
        tickmode="array", tickvals=list(range(len(names))), ticktext=names,
        row=2, col=1,
    )
    fig.update_xaxes(title_text="sample combination", showticklabels=False, row=2, col=1)
    fig.update_yaxes(title_text="features", type="log", row=1, col=1)
    fig.update_layout(title="Feature overlap across samples", showlegend=False)
    return fig


def figure_ms2_association(summary: Ms2Summary) -> go.Figure:
    """Donut of associated vs. unassociated MS2 scans."""
    if summary.n_total == 0:
        return _empty("MS2 association")
    fig = go.Figure(
        go.Pie(
            labels=["associated", "unassociated"],
            values=[summary.n_associated, summary.n_unassociated],
            hole=0.55,
            marker_colors=["#1f77b4", "#d62728"],
        )
    )
    fig.update_layout(
        title="MS2 association",
        annotations=[
            dict(
                text=f"{summary.n_total} MS2<br>{summary.n_precursor_only} precursor-only",
                x=0.5, y=0.5, showarrow=False, font=dict(size=13),
            )
        ],
    )
    return fig


def figure_nfw(summary: Ms2Summary) -> go.Figure:
    """Bar histogram of ``n_features_in_window`` (0 / 1 / >1 coloured)."""
    if not summary.nfw_distribution:
        return _empty("Features per isolation window")
    keys = sorted(summary.nfw_distribution)
    values = [summary.nfw_distribution[k] for k in keys]
    colors = [
        "#999999" if k == 0 else "#1f77b4" if k == 1 else "#d62728" for k in keys
    ]
    fig = go.Figure(go.Bar(x=keys, y=values, marker_color=colors))
    fig.update_layout(
        title="Features per isolation window (grouper)",
        xaxis=dict(title="n_features_in_window", dtick=1),
        yaxis=dict(title="MS2 scans"),
    )
    return fig


def figure_purity(summary: Ms2Summary) -> go.Figure:
    """Histogram of scored precursor purity with the cutoff line."""
    if not summary.purity_values:
        return _empty("Precursor ion purity")
    fig = go.Figure(
        go.Histogram(x=summary.purity_values, nbinsx=40, marker_color="#1f77b4")
    )
    fig.add_vline(
        x=summary.purity_cutoff,
        line=dict(color="#d62728", dash="dash"),
        annotation_text=f"cutoff {summary.purity_cutoff:g}",
    )
    unscored = summary.n_total - summary.n_purity_scored
    fig.update_layout(
        title=(
            f"Precursor ion purity — {summary.n_low_purity} of "
            f"{summary.n_purity_scored} scored below cutoff "
            f"({unscored} unscored)"
        ),
        xaxis=dict(title="purity", range=[0, 1]),
        yaxis=dict(title="MS2 scans"),
    )
    return fig


def figure_purity_vs_nfw(purity: np.ndarray, nfw: np.ndarray) -> go.Figure:
    """2-D density of purity against the grouper's window feature count."""
    if purity.size == 0:
        return _empty("Purity vs. window feature count")
    fig = go.Figure(
        go.Histogram2d(
            x=nfw,
            y=purity,
            colorscale="Blues",
            xbins=dict(start=-0.5, end=float(nfw.max()) + 0.5, size=1),
            ybins=dict(start=0.0, end=1.0, size=0.05),
        )
    )
    fig.update_layout(
        title="Purity vs. n_features_in_window",
        xaxis=dict(title="n_features_in_window", dtick=1),
        yaxis=dict(title="purity", range=[0, 1]),
    )
    return fig


# ---------------------------------------------------------------------------
# assembly
# ---------------------------------------------------------------------------


@log_call(source="analysis_db_path")
def collect_stats(
    analysis_db_path: Path | str,
    raw_db_paths: dict[int, str | Path] | None = None,
    *,
    overlap_top_n: int = 30,
    purity_cutoff: float = 0.8,
) -> SummaryStats:
    """Compute every number the report needs (no figures, no files)."""
    analysis_db_path = Path(analysis_db_path)
    samples = per_sample_counts(analysis_db_path, raw_db_paths)
    try:
        features_df = load_features(analysis_db_path)
    except Exception:  # pragma: no cover - defensive
        features_df = pd.DataFrame()
    membership = feature_membership(features_df)
    return SummaryStats(
        samples=samples,
        n_features=0 if features_df is None else int(len(features_df)),
        overlap_combos=overlap_combos(membership, overlap_top_n),
        ms2=ms2_summary(analysis_db_path, purity_cutoff=purity_cutoff),
    )


def _table_html(counts: Sequence[SampleCounts]) -> str:
    head = (
        "<tr><th>sample</th><th>MS1</th><th>MS2</th><th>pixels</th>"
        "<th>detected peaks</th><th>features</th></tr>"
    )
    rows = "".join(
        f"<tr><td>{c.name}</td><td>{c.n_ms1}</td><td>{c.n_ms2}</td>"
        f"<td>{c.n_pixels}</td><td>{c.n_detected_peaks}</td>"
        f"<td>{c.n_features}</td></tr>"
        for c in counts
    )
    return (
        "<table style='border-collapse:collapse' border='1' cellpadding='6'>"
        f"{head}{rows}</table>"
    )


@log_call(source="analysis_db_path")
def build_summary_report(
    analysis_db_path: Path | str,
    raw_db_paths: dict[int, str | Path] | None = None,
    out_dir: Path | str | None = None,
    *,
    config: "ReportConfig | None" = None,
) -> Path:
    """Write ``summary_report.html`` + ``summary.json`` and return the HTML path.

    Args:
        analysis_db_path: A finished analysis database.
        raw_db_paths: Optional ``{sample_id: raw_db_path}`` override; by
            default ``samples.raw_db_path`` is trusted.
        out_dir: Where to write the two files (default: the analysis DB's
            directory).
        config: Optional object exposing ``overlap_top_n`` / ``purity_cutoff``
            (a :class:`~msianalyzer.core.config.config.ReportConfig`).
    """
    analysis_db_path = Path(analysis_db_path)
    out_dir = Path(out_dir) if out_dir is not None else analysis_db_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    top_n = int(getattr(config, "overlap_top_n", 30))
    cutoff = float(getattr(config, "purity_cutoff", 0.8))

    stats = collect_stats(
        analysis_db_path, raw_db_paths, overlap_top_n=top_n, purity_cutoff=cutoff
    )
    sample_names = [c.name for c in stats.samples]
    pv, nfw = purity_vs_nfw(analysis_db_path)

    figures = [
        figure_per_sample(stats.samples),
        figure_overlap_upset(stats.overlap_combos, sample_names),
        figure_ms2_association(stats.ms2),
        figure_nfw(stats.ms2),
        figure_purity(stats.ms2),
        figure_purity_vs_nfw(pv, nfw),
    ]

    json_path = out_dir / _REPORT_JSON
    json_path.write_text(json.dumps(stats.to_dict(), indent=2))

    blocks = []
    for i, fig in enumerate(figures):
        blocks.append(
            fig.to_html(
                full_html=False,
                include_plotlyjs="cdn" if i == 0 else False,
            )
        )
    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>MSIAnalyzer summary report</title>"
        "<style>body{font-family:system-ui,sans-serif;margin:24px;max-width:1100px}"
        "table{font-size:14px}h1{font-size:20px}h2{font-size:16px;margin-top:32px}"
        "</style></head><body>"
        "<h1>MSIAnalyzer summary report</h1>"
        f"<p>{len(stats.samples)} sample(s) &middot; {stats.n_features} features "
        f"&middot; {stats.ms2.n_total} MS2 scans "
        f"({stats.ms2.n_associated} associated, "
        f"{stats.ms2.n_chimeric_window} chimeric by window, "
        f"{stats.ms2.n_low_purity} below purity {cutoff:g})</p>"
        "<h2>Per-sample counts</h2>"
        f"{_table_html(stats.samples)}"
        + "".join(f"<div>{b}</div>" for b in blocks)
        + "</body></html>"
    )
    html_path = out_dir / _REPORT_HTML
    html_path.write_text(html)
    logger.info(
        "summary report: wrote %s and %s", html_path.name, json_path.name,
        extra={"source_file": str(analysis_db_path)},
    )
    return html_path
