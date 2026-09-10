"""Build the end-of-run summary report (``summary_report.html`` + ``summary.json``).

Pure stat functions read the analysis database (and each sample's raw
database, read-only) and return plain data; the ``figure_*`` builders turn
that data into Plotly figures; :func:`build_summary_report` glues it
together and writes the two files. Nothing here writes to a database.
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
    "PerSampleMs2",
    "AssocPuritySample",
    "AssociatedPuritySummary",
    "UnscoredPurity",
    "UnscoredSummary",
    "RecheckSummary",
    "SummaryStats",
    "per_sample_counts",
    "feature_membership",
    "overlap_combos",
    "ms2_summary",
    "per_sample_ms2",
    "associated_purity",
    "purity_unscored",
    "unassociated_recheck",
    "figure_per_sample",
    "figure_overlap_upset",
    "figure_ms2_association",
    "figure_ms2_association_per_sample",
    "figure_unassociated_recheck",
    "figure_purity",
    "figure_purity_per_sample",
    "figure_purity_unscored",
    "collect_stats",
    "build_summary_report",
]

_REPORT_HTML = "summary_report.html"
_REPORT_JSON = "summary.json"

_C_OK = "#1f77b4"
_C_BAD = "#d62728"
_C_WARN = "#ff7f0e"
_C_MUTED = "#9e9e9e"
_C_DARK = "#5c5c5c"
_C_FAINT = "#d9d9d9"
_C_GOOD = "#2ca02c"

# horizontal legend above the plot area (keeps it off the x-axis tick labels)
_LEGEND_TOP = dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0)
_MARGIN_TOP = dict(t=80)


def _fmt(n) -> str:
    """Integer with a thousands separator, for HTML text."""
    try:
        return f"{int(round(float(n))):,}"
    except (TypeError, ValueError):
        return str(n)


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
    """Analysis-wide MS2 association roll-up."""

    n_total: int
    n_associated: int
    n_unassociated: int
    n_precursor_only: int


@dataclass
class PerSampleMs2:
    """MS2 association counts for one sample."""

    sample_id: int
    name: str
    n_total: int
    n_associated: int
    n_unassociated: int


@dataclass
class AssocPuritySample:
    """`precursor_frac` of one sample's *associated* MS2 (fed to annotation)."""

    sample_id: int
    name: str
    n_associated: int
    n_ge_cutoff: int
    frac_values: list[float] = field(default_factory=list)


@dataclass
class AssociatedPuritySummary:
    """Precursor purity restricted to the MS2 that will actually be annotated.

    ``precursor_frac`` (peak-detection-free, always populated) is the metric;
    the population is MS2 with a non-NULL ``feature_id`` in
    ``ms2_associations``. ``frac_values_all`` keeps every scan's value for a
    faint context overlay only.
    """

    cutoff: float
    n_associated: int
    n_ge_cutoff: int
    frac_values: list[float]
    frac_values_all: list[float]
    per_sample: list[AssocPuritySample]

    @property
    def pct_ge_cutoff(self) -> float:
        return (
            100.0 * self.n_ge_cutoff / self.n_associated
            if self.n_associated
            else 0.0
        )


@dataclass
class UnscoredPurity:
    """Why precursor purity (the peak-based value) is unscored for one sample.

    Mutually exclusive, in priority order:

    Attributes:
        n_scored: peak-based purity was computed.
        n_no_parent: no MS1 scan resolved before the MS2 (rare).
        n_no_precursor_mz: scan carries no ``precursor_mz`` / isolation target.
        n_off_pixel: the parent MS1 is not among the sample's imaged pixels
            (laser flyback / off-tissue / warm-up scans).
        n_unresolved_confirmed: the precursor ion *is* present in its own
            parent MS1 (``precursor_confirmed``), the peak-picker just could
            not resolve it as a discrete peak (dense low-m/z window). Not a
            data problem — ``precursor_frac`` still measures its purity.
        n_not_confirmed: no real signal at ``precursor_mz`` in the parent MS1
            (dynamic-exclusion carry-over, wrong pixel, precursor gone).
    """

    sample_id: int
    name: str
    n_scored: int
    n_no_parent: int
    n_no_precursor_mz: int
    n_off_pixel: int
    n_unresolved_confirmed: int
    n_not_confirmed: int


@dataclass
class UnscoredSummary:
    per_sample: list[UnscoredPurity]
    n_scored: int
    n_no_parent: int
    n_no_precursor_mz: int
    n_off_pixel: int
    n_unresolved_confirmed: int
    n_not_confirmed: int

    @property
    def n_unscored(self) -> int:
        return (
            self.n_no_parent
            + self.n_no_precursor_mz
            + self.n_off_pixel
            + self.n_unresolved_confirmed
            + self.n_not_confirmed
        )


@dataclass
class RecheckSummary:
    """Would the unassociated MS2 associate against the *pre-filter* MS1 peaks?

    The grouper matches an MS2 precursor to the aligned ``features`` list.
    Some MS2 miss only because peak filtering (``filter_spectra``) dropped
    the peak in that sample. This re-tests every unassociated scan against
    the sample's ``detect_ms1_centroids`` output (centroided, *before* the
    MAD/threshold filter), using the same ``assoc_ppm`` and isolation
    window the grouper used.
    """

    assoc_ppm: float
    n_unassociated: int
    n_would_associate: int
    n_still_unassociated: int
    n_no_precursor_mz: int
    per_sample: list[dict] = field(default_factory=list)


@dataclass
class SummaryStats:
    """Everything the report renders."""

    samples: list[SampleCounts]
    n_features: int
    overlap_combos: list[tuple[list[str], int]]
    ms2: Ms2Summary
    per_sample_ms2: list[PerSampleMs2]
    associated_purity: AssociatedPuritySummary
    unscored: UnscoredSummary
    recheck: RecheckSummary

    def to_dict(self) -> dict:
        d = asdict(self)
        ap = d["associated_purity"]
        # raw value lists are large and already summarised
        ap["n_frac_values"] = len(self.associated_purity.frac_values)
        ap.pop("frac_values", None)
        ap.pop("frac_values_all", None)
        for ps in ap["per_sample"]:
            ps.pop("frac_values", None)
        ap["pct_ge_cutoff"] = round(self.associated_purity.pct_ge_cutoff, 2)
        d["unscored"]["n_unscored"] = self.unscored.n_unscored
        d["overlap_combos"] = [
            {"samples": list(s), "n_features": n} for s, n in self.overlap_combos
        ]
        return d


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _scalar(con: sqlite3.Connection, sql: str, default: int = 0) -> int:
    try:
        row = con.execute(sql).fetchone()
    except sqlite3.OperationalError:
        return default
    return int(row[0]) if row and row[0] is not None else default


def _aggregated_mz(
    con: sqlite3.Connection, command_name: str, sample_id: int
) -> np.ndarray:
    """The m/z array of a sample's most recent ``command_name`` aggregate."""
    try:
        row = con.execute(
            "SELECT a.mz_array FROM aggregated_spectra a "
            "JOIN commands c ON a.command_id = c.id "
            "WHERE c.command_name = ? AND a.sample_id = ? "
            "ORDER BY a.id DESC LIMIT 1",
            (command_name, sample_id),
        ).fetchone()
    except sqlite3.OperationalError:
        return np.array([])
    if row is None or row[0] is None:
        return np.array([])
    from ..parser.mzml_parser import blob_to_array

    return np.asarray(blob_to_array(row[0]), dtype=float)


def _group_ms2_args(con: sqlite3.Connection) -> dict:
    try:
        row = con.execute(
            "SELECT arguments FROM commands WHERE command_name = 'group_ms2' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    except sqlite3.OperationalError:
        return {}
    if not row or not row[0]:
        return {}
    try:
        return json.loads(row[0])
    except (ValueError, TypeError):
        return {}


def _pixel_scan_ids(raw_db_path: str | Path | None) -> set[int] | None:
    """The set of MS1 ``scan_id`` mapped to a pixel, or ``None`` if unknown."""
    if not raw_db_path or not Path(raw_db_path).exists():
        return None
    try:
        with sqlite3.connect(str(raw_db_path)) as con:
            rows = con.execute(
                "SELECT scan_id FROM pixel_ms1_scans"
            ).fetchall()
    except sqlite3.OperationalError:
        return None
    return {int(r[0]) for r in rows}


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
                n_peaks = int(
                    _aggregated_mz(acon, "filter_spectra", int(sample_id)).size
                )
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
def ms2_summary(analysis_db_path: Path | str) -> Ms2Summary:
    """MS2 association roll-up over every scan."""
    analysis_db_path = Path(analysis_db_path)
    with sqlite3.connect(analysis_db_path) as con:
        assoc = con.execute(
            "SELECT feature_id, precursor_only FROM ms2_associations"
        ).fetchall()

    n_total = len(assoc)
    n_assoc = sum(1 for f, _ in assoc if f is not None)
    return Ms2Summary(
        n_total=n_total,
        n_associated=n_assoc,
        n_unassociated=n_total - n_assoc,
        n_precursor_only=sum(1 for _, p in assoc if p),
    )


@log_call(source="analysis_db_path")
def associated_purity(
    analysis_db_path: Path | str, *, cutoff: float = 0.8
) -> AssociatedPuritySummary:
    """`precursor_frac` distribution for the MS2 that will be annotated.

    Population: scans with a non-NULL ``feature_id`` in ``ms2_associations``
    (an unassociated MS2 fragmented an ion you cannot image, so its purity is
    not actionable). ``frac_values_all`` keeps every scan's value for a faint
    context overlay only.
    """
    analysis_db_path = Path(analysis_db_path)
    with sqlite3.connect(analysis_db_path) as con:
        names = dict(con.execute("SELECT sample_id, name FROM samples").fetchall())
        all_frac = [
            float(r[0])
            for r in con.execute(
                "SELECT precursor_frac FROM precursor_purity "
                "WHERE precursor_frac IS NOT NULL"
            ).fetchall()
        ]
        assoc_rows = con.execute(
            "SELECT p.sample_id, p.precursor_frac "
            "FROM precursor_purity p "
            "JOIN ms2_associations a "
            "  ON a.sample_id = p.sample_id AND a.scan_id = p.ms2_scan_id "
            "WHERE p.precursor_frac IS NOT NULL AND a.feature_id IS NOT NULL"
        ).fetchall()

    by_sample: dict[int, list[float]] = {}
    for sid, frac in assoc_rows:
        by_sample.setdefault(int(sid), []).append(float(frac))
    assoc_frac = [v for vs in by_sample.values() for v in vs]

    per_sample: list[AssocPuritySample] = []
    for sid in (sorted(names) if names else sorted(by_sample)):
        vals = by_sample.get(sid, [])
        per_sample.append(
            AssocPuritySample(
                sample_id=int(sid),
                name=str(names.get(sid, sid)),
                n_associated=len(vals),
                n_ge_cutoff=sum(1 for v in vals if v >= cutoff),
                frac_values=vals,
            )
        )

    return AssociatedPuritySummary(
        cutoff=float(cutoff),
        n_associated=len(assoc_frac),
        n_ge_cutoff=sum(1 for v in assoc_frac if v >= cutoff),
        frac_values=assoc_frac,
        frac_values_all=all_frac,
        per_sample=per_sample,
    )


@log_call(source="analysis_db_path")
def per_sample_ms2(analysis_db_path: Path | str) -> list[PerSampleMs2]:
    """MS2 association counts broken down per sample."""
    analysis_db_path = Path(analysis_db_path)
    with sqlite3.connect(analysis_db_path) as con:
        names = dict(
            con.execute("SELECT sample_id, name FROM samples").fetchall()
        )
        rows = con.execute(
            "SELECT sample_id, "
            "  SUM(CASE WHEN feature_id IS NOT NULL THEN 1 ELSE 0 END), "
            "  COUNT(*) "
            "FROM ms2_associations GROUP BY sample_id ORDER BY sample_id"
        ).fetchall()

    out: list[PerSampleMs2] = []
    for sample_id, n_assoc, n_total in rows:
        n_assoc = int(n_assoc or 0)
        n_total = int(n_total or 0)
        out.append(
            PerSampleMs2(
                sample_id=int(sample_id) if sample_id is not None else -1,
                name=str(names.get(sample_id, sample_id)),
                n_total=n_total,
                n_associated=n_assoc,
                n_unassociated=n_total - n_assoc,
            )
        )
    return out


@log_call(source="analysis_db_path")
def purity_unscored(
    analysis_db_path: Path | str,
    raw_db_paths: dict[int, str | Path] | None = None,
) -> UnscoredSummary:
    """Classify every ``precursor_purity`` row: scored, or why not.

    ``raw_db_paths`` (or ``samples.raw_db_path``) is needed to tell an
    off-pixel parent MS1 (laser flyback / off-tissue) apart from an in-ROI
    scan; without it those collapse into the peak-based buckets.
    """
    analysis_db_path = Path(analysis_db_path)
    _keys = ("scored", "no_parent", "no_precursor_mz", "off_pixel",
             "unresolved_confirmed", "not_confirmed")
    with sqlite3.connect(analysis_db_path) as con:
        samples = con.execute(
            "SELECT sample_id, name, raw_db_path FROM samples ORDER BY sample_id"
        ).fetchall()

        per_sample: list[UnscoredPurity] = []
        tot = {k: 0 for k in _keys}
        for sample_id, name, raw_db_path in samples:
            path = (
                raw_db_paths.get(sample_id, raw_db_path)
                if raw_db_paths is not None
                else raw_db_path
            )
            pixel_ids = _pixel_scan_ids(path)
            rows = con.execute(
                "SELECT parent_ms1_scan_id, window_lo_mz, purity, precursor_confirmed "
                "FROM precursor_purity WHERE sample_id = ?",
                (sample_id,),
            ).fetchall()

            s = {k: 0 for k in _keys}
            for parent_id, window_lo, purity, confirmed in rows:
                if purity is not None:
                    s["scored"] += 1
                elif parent_id is None:
                    s["no_parent"] += 1
                elif window_lo is None:
                    s["no_precursor_mz"] += 1
                elif pixel_ids is not None and int(parent_id) not in pixel_ids:
                    s["off_pixel"] += 1
                elif confirmed:
                    s["unresolved_confirmed"] += 1
                else:
                    s["not_confirmed"] += 1
            for k in tot:
                tot[k] += s[k]
            per_sample.append(
                UnscoredPurity(
                    sample_id=int(sample_id),
                    name=str(name),
                    n_scored=s["scored"],
                    n_no_parent=s["no_parent"],
                    n_no_precursor_mz=s["no_precursor_mz"],
                    n_off_pixel=s["off_pixel"],
                    n_unresolved_confirmed=s["unresolved_confirmed"],
                    n_not_confirmed=s["not_confirmed"],
                )
            )

    return UnscoredSummary(
        per_sample=per_sample,
        n_scored=tot["scored"],
        n_no_parent=tot["no_parent"],
        n_no_precursor_mz=tot["no_precursor_mz"],
        n_off_pixel=tot["off_pixel"],
        n_unresolved_confirmed=tot["unresolved_confirmed"],
        n_not_confirmed=tot["not_confirmed"],
    )


def _recheck_one(
    prefilter_mz: np.ndarray,
    rows: Sequence[tuple],
    *,
    assoc_ppm: float,
    default_half: float,
) -> tuple[int, int, int]:
    """``(n_would_associate, n_still_unassociated, n_no_precursor_mz)``.

    ``rows`` are ``(precursor_mz, target, lower, upper)`` for the sample's
    currently-unassociated MS2 scans.
    """
    if not rows:
        return 0, 0, 0
    prec = np.array([r[0] if r[0] is not None else np.nan for r in rows], dtype=float)
    tgt = np.array([r[1] if r[1] is not None else np.nan for r in rows], dtype=float)
    low = np.array([r[2] if r[2] is not None else np.nan for r in rows], dtype=float)
    upp = np.array([r[3] if r[3] is not None else np.nan for r in rows], dtype=float)

    match_val = np.where(np.isnan(prec), tgt, prec)
    center = np.where(np.isnan(tgt), match_val, tgt)
    valid = ~np.isnan(match_val)
    n_no_prec = int((~valid).sum())

    if prefilter_mz.size == 0 or not valid.any():
        return 0, int(valid.sum()), n_no_prec

    lo_off = np.where(np.isnan(low) | (low <= 0), default_half, low)
    up_off = np.where(np.isnan(upp) | (upp <= 0), default_half, upp)
    mv = np.where(valid, match_val, 0.0)
    cv = np.where(valid, center, 0.0)
    lo = np.maximum(mv * (1.0 - assoc_ppm / 1e6), cv - lo_off)
    hi = np.minimum(mv * (1.0 + assoc_ppm / 1e6), cv + up_off)

    grid = np.sort(prefilter_mz)
    li = np.searchsorted(grid, lo, side="left")
    ji = np.searchsorted(grid, hi, side="right")
    would = (ji > li) & valid & (hi >= lo)
    return int(would.sum()), int((valid & ~would).sum()), n_no_prec


@log_call(source="analysis_db_path")
def unassociated_recheck(analysis_db_path: Path | str) -> RecheckSummary:
    """Re-test unassociated MS2 against each sample's pre-filter MS1 peaks."""
    analysis_db_path = Path(analysis_db_path)
    with sqlite3.connect(analysis_db_path) as con:
        args = _group_ms2_args(con)
        assoc_ppm = float(args.get("assoc_ppm", 10.0))
        default_half = float(args.get("default_isolation_half_width", 0.5))
        samples = con.execute(
            "SELECT sample_id, name FROM samples ORDER BY sample_id"
        ).fetchall()

        per_sample: list[dict] = []
        tot_un = tot_would = tot_still = tot_noprec = 0
        for sample_id, name in samples:
            prefilter_mz = _aggregated_mz(
                con, "detect_ms1_centroids", int(sample_id)
            )
            rows = con.execute(
                "SELECT precursor_mz, isolation_window_target, "
                "       isolation_window_lower, isolation_window_upper "
                "FROM ms2_associations "
                "WHERE feature_id IS NULL AND sample_id = ?",
                (sample_id,),
            ).fetchall()
            would, still, noprec = _recheck_one(
                prefilter_mz, rows, assoc_ppm=assoc_ppm, default_half=default_half
            )
            per_sample.append(
                {
                    "name": str(name),
                    "n_unassociated": len(rows),
                    "n_would_associate": would,
                    "n_still_unassociated": still,
                    "n_no_precursor_mz": noprec,
                }
            )
            tot_un += len(rows)
            tot_would += would
            tot_still += still
            tot_noprec += noprec

    return RecheckSummary(
        assoc_ppm=assoc_ppm,
        n_unassociated=tot_un,
        n_would_associate=tot_would,
        n_still_unassociated=tot_still,
        n_no_precursor_mz=tot_noprec,
        per_sample=per_sample,
    )


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
    fig.update_traces(
        hovertemplate="%{fullData.name}<br>%{x}: %{y:,}<extra></extra>"
    )
    fig.update_layout(
        title="Per-sample counts",
        barmode="group",
        yaxis=dict(title="count", type="log", tickformat=","),
        xaxis=dict(title="sample"),
        legend=_LEGEND_TOP,
        margin=_MARGIN_TOP,
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
        rows=2, cols=1, shared_xaxes=True,
        row_heights=[0.62, 0.38], vertical_spacing=0.04,
    )
    fig.add_bar(
        x=idx, y=sizes, marker_color=_C_OK, name="features",
        hovertemplate="%{y:,} features<extra></extra>", row=1, col=1,
    )

    on_x, on_y, off_x, off_y = [], [], [], []
    for i, (combo, _) in enumerate(combos):
        member = set(combo)
        for j, s in enumerate(names):
            (on_x if s in member else off_x).append(i)
            (on_y if s in member else off_y).append(j)
    fig.add_scatter(
        x=off_x, y=off_y, mode="markers",
        marker=dict(size=9, color=_C_FAINT), showlegend=False, row=2, col=1,
    )
    fig.add_scatter(
        x=on_x, y=on_y, mode="markers",
        marker=dict(size=11, color=_C_OK), showlegend=False, row=2, col=1,
    )
    fig.update_yaxes(
        tickmode="array", tickvals=list(range(len(names))), ticktext=names,
        row=2, col=1,
    )
    fig.update_xaxes(title_text="sample combination", showticklabels=False, row=2, col=1)
    fig.update_yaxes(title_text="features", type="log", tickformat=",", row=1, col=1)
    fig.update_layout(title="Feature overlap across samples", showlegend=False)
    return fig


def figure_ms2_association(summary: Ms2Summary) -> go.Figure:
    """Donut of associated vs. unassociated MS2 scans (analysis-wide)."""
    if summary.n_total == 0:
        return _empty("MS2 association")
    fig = go.Figure(
        go.Pie(
            labels=["associated", "unassociated"],
            values=[summary.n_associated, summary.n_unassociated],
            hole=0.55,
            marker_colors=[_C_OK, _C_BAD],
            texttemplate="%{label}<br>%{value:,}<br>%{percent}",
            hovertemplate="%{label}: %{value:,} (%{percent})<extra></extra>",
        )
    )
    fig.update_layout(
        title="MS2 association (overall)",
        legend=_LEGEND_TOP,
        margin=_MARGIN_TOP,
        annotations=[
            dict(
                text=(
                    f"{_fmt(summary.n_total)} MS2<br>"
                    f"{_fmt(summary.n_precursor_only)} precursor-only"
                ),
                x=0.5, y=0.5, showarrow=False, font=dict(size=13),
            )
        ],
    )
    return fig


def figure_ms2_association_per_sample(
    per_sample: Sequence[PerSampleMs2],
) -> go.Figure:
    """100%-stacked bar of associated / unassociated MS2 per sample."""
    if not per_sample:
        return _empty("MS2 association by sample")
    names = [p.name for p in per_sample]
    denom = [max(p.n_total, 1) for p in per_sample]
    pct_a = [100.0 * p.n_associated / d for p, d in zip(per_sample, denom)]
    pct_u = [100.0 * p.n_unassociated / d for p, d in zip(per_sample, denom)]
    fig = go.Figure()
    fig.add_bar(
        name="associated", x=names, y=pct_a, marker_color=_C_OK,
        customdata=[p.n_associated for p in per_sample],
        hovertemplate="%{x}<br>associated %{y:.1f}%% (%{customdata:,})<extra></extra>",
    )
    fig.add_bar(
        name="unassociated", x=names, y=pct_u, marker_color=_C_BAD,
        customdata=[p.n_unassociated for p in per_sample],
        hovertemplate="%{x}<br>unassociated %{y:.1f}%% (%{customdata:,})<extra></extra>",
    )
    fig.update_layout(
        title="MS2 association by sample",
        barmode="stack",
        yaxis=dict(title="% of MS2 scans", range=[0, 100]),
        xaxis=dict(title="sample"),
        legend=_LEGEND_TOP,
        margin=_MARGIN_TOP,
    )
    return fig


def figure_unassociated_recheck(recheck: RecheckSummary) -> go.Figure:
    """Stacked bar: of the unassociated MS2, how many the pre-filter peaks recover."""
    if recheck.n_unassociated == 0:
        return _empty("Unassociated MS2 — pre-filter recheck")
    fig = go.Figure()
    segs = [
        ("would associate on pre-filter peaks", recheck.n_would_associate, _C_GOOD),
        ("still unassociated", recheck.n_still_unassociated, _C_BAD),
    ]
    if recheck.n_no_precursor_mz:
        segs.append(("no precursor m/z", recheck.n_no_precursor_mz, _C_MUTED))
    for name, val, color in segs:
        fig.add_bar(
            name=name, x=[val], y=["unassociated MS2"], orientation="h",
            marker_color=color,
            hovertemplate=f"{name}: %{{x:,}}<extra></extra>",
        )
    fig.update_layout(
        title=(
            f"Unassociated MS2 rechecked against pre-filter MS1 peaks "
            f"(assoc_ppm {recheck.assoc_ppm:g})"
        ),
        barmode="stack",
        xaxis=dict(title="MS2 scans", tickformat=","),
        legend=_LEGEND_TOP,
        margin=_MARGIN_TOP,
    )
    return fig


def figure_purity(assoc: AssociatedPuritySummary) -> go.Figure:
    """`precursor_frac` histogram for **associated** MS2, all-MS2 as faint context."""
    if not assoc.frac_values:
        return _empty("Precursor purity of associated MS2")
    fig = go.Figure()
    if assoc.frac_values_all:
        fig.add_histogram(
            x=assoc.frac_values_all, name="all MS2", nbinsx=40,
            histnorm="percent", marker_color=_C_MUTED, opacity=0.35,
            hovertemplate="precursor_frac %{x}<br>%{y:.1f}%% of all MS2<extra></extra>",
        )
    fig.add_histogram(
        x=assoc.frac_values, name="associated MS2", nbinsx=40,
        histnorm="percent", marker_color=_C_OK,
        hovertemplate=(
            "precursor_frac %{x}<br>%{y:.1f}%% of associated MS2<extra></extra>"
        ),
    )
    fig.add_vline(
        x=assoc.cutoff, line=dict(color=_C_BAD, dash="dash"),
        annotation_text=f"cutoff {assoc.cutoff:g}",
    )
    fig.update_layout(
        title=(
            f"Precursor purity (precursor_frac) — {_fmt(assoc.n_ge_cutoff)} of "
            f"{_fmt(assoc.n_associated)} associated MS2 at ≥ {assoc.cutoff:g} "
            f"({assoc.pct_ge_cutoff:.0f}%)"
        ),
        barmode="overlay",
        xaxis=dict(title="precursor_frac", range=[0, 1]),
        yaxis=dict(title="% of MS2"),
        legend=_LEGEND_TOP,
        margin=_MARGIN_TOP,
    )
    return fig


def figure_purity_per_sample(assoc: AssociatedPuritySummary) -> go.Figure:
    """Violin of `precursor_frac` for associated MS2, per sample."""
    ps = assoc.per_sample
    if not any(p.frac_values for p in ps):
        return _empty("Precursor purity of associated MS2, by sample")
    fig = go.Figure()
    for p in ps:
        fig.add_violin(
            y=p.frac_values or [None],
            name=f"{p.name}<br>(n={_fmt(p.n_associated)})",
            box_visible=True,
            meanline_visible=True,
            points=False,
            spanmode="hard",
            line_color=_C_OK,
            fillcolor=_C_OK,
            opacity=0.65,
        )
    fig.add_hline(
        y=assoc.cutoff, line=dict(color=_C_BAD, dash="dash"),
        annotation_text=f"cutoff {assoc.cutoff:g}",
    )
    fig.update_layout(
        title="Precursor purity (precursor_frac) of associated MS2, by sample",
        yaxis=dict(title="precursor_frac", range=[0, 1]),
        showlegend=False,
    )
    return fig


_UNSCORED_SEGMENTS = [
    ("purity scored (peak resolved)", "n_scored", _C_OK),
    ("precursor confirmed in MS1, peak not resolved", "n_unresolved_confirmed", _C_GOOD),
    ("precursor not confirmed in MS1", "n_not_confirmed", _C_WARN),
    ("parent MS1 off-pixel (flyback)", "n_off_pixel", _C_MUTED),
    ("no MS1 before the scan", "n_no_parent", _C_DARK),
    ("no precursor m/z", "n_no_precursor_mz", _C_FAINT),
]


def figure_purity_unscored(unscored: UnscoredSummary) -> go.Figure:
    """Stacked bar per sample: scored vs. each reason purity is unscored."""
    ps = unscored.per_sample
    if not ps:
        return _empty("Precursor purity — scored vs. unscored")
    names = [p.name for p in ps]
    fig = go.Figure()
    for label, attr, color in _UNSCORED_SEGMENTS:
        vals = [getattr(p, attr) for p in ps]
        if not any(vals):
            continue
        fig.add_bar(
            name=label, x=names, y=vals, marker_color=color,
            hovertemplate=f"%{{x}}<br>{label}: %{{y:,}}<extra></extra>",
        )
    fig.update_layout(
        title="Precursor purity — scored vs. why unscored",
        barmode="stack",
        xaxis=dict(title="sample"),
        yaxis=dict(title="MS2 scans", tickformat=","),
        legend=_LEGEND_TOP,
        margin=_MARGIN_TOP,
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
        ms2=ms2_summary(analysis_db_path),
        per_sample_ms2=per_sample_ms2(analysis_db_path),
        associated_purity=associated_purity(analysis_db_path, cutoff=purity_cutoff),
        unscored=purity_unscored(analysis_db_path, raw_db_paths),
        recheck=unassociated_recheck(analysis_db_path),
    )


def _table_html(counts: Sequence[SampleCounts]) -> str:
    head = (
        "<tr><th>sample</th><th>MS1</th><th>MS2</th><th>pixels</th>"
        "<th>detected peaks</th><th>features</th></tr>"
    )
    rows = "".join(
        f"<tr><td>{c.name}</td><td>{_fmt(c.n_ms1)}</td><td>{_fmt(c.n_ms2)}</td>"
        f"<td>{_fmt(c.n_pixels)}</td><td>{_fmt(c.n_detected_peaks)}</td>"
        f"<td>{_fmt(c.n_features)}</td></tr>"
        for c in counts
    )
    return (
        "<table style='border-collapse:collapse' border='1' cellpadding='6'>"
        f"{head}{rows}</table>"
    )


def _assoc_purity_sentence(a: AssociatedPuritySummary) -> str:
    if a.n_associated == 0:
        return "<p>No MS2 scan associated to a feature.</p>"
    return (
        f"<p>Of {_fmt(a.n_associated)} MS2 associated to a feature (the spectra "
        f"that feed the library search), <b>{a.pct_ge_cutoff:.0f}%</b> have "
        f"<code>precursor_frac</code> &ge; {a.cutoff:g} — the precursor dominates "
        f"its isolation window in that pixel's own MS1.</p>"
    )


def _recheck_sentence(r: RecheckSummary) -> str:
    if r.n_unassociated == 0:
        return "<p>Every MS2 scan associated to a feature.</p>"
    pct = 100.0 * r.n_would_associate / r.n_unassociated
    extra = (
        f" {_fmt(r.n_no_precursor_mz)} carry no precursor m/z and could not be "
        f"rechecked."
        if r.n_no_precursor_mz
        else ""
    )
    return (
        f"<p>Of {_fmt(r.n_unassociated)} unassociated MS2 scans, "
        f"<b>{_fmt(r.n_would_associate)} ({pct:.1f}%)</b> would have matched a "
        f"peak in their sample's pre-filter centroid list (within "
        f"{r.assoc_ppm:g} ppm and the isolation window) — i.e. peak filtering, "
        f"not fragmentation, is why they are unassociated. "
        f"{_fmt(r.n_still_unassociated)} still match nothing.{extra}</p>"
    )


def _unscored_sentence(u: UnscoredSummary) -> str:
    if u.n_unscored == 0:
        return "<p>Peak-based precursor purity was scored for every MS2 scan.</p>"
    parts = []
    if u.n_unresolved_confirmed:
        parts.append(
            f"{_fmt(u.n_unresolved_confirmed)} where the precursor <b>is</b> "
            f"present in its own parent MS1 but the peak-picker could not "
            f"resolve it (dense low-m/z window) — <code>precursor_frac</code> "
            f"still measures its purity"
        )
    if u.n_not_confirmed:
        parts.append(
            f"{_fmt(u.n_not_confirmed)} with no real signal at the recorded "
            f"precursor m/z in the parent MS1 (dynamic-exclusion carry-over, "
            f"wrong pixel, or the precursor was gone)"
        )
    if u.n_off_pixel:
        parts.append(
            f"{_fmt(u.n_off_pixel)} because the parent MS1 falls outside the "
            f"sample's imaged pixels (laser flyback / off-tissue)"
        )
    if u.n_no_parent:
        parts.append(f"{_fmt(u.n_no_parent)} with no MS1 scan before them")
    if u.n_no_precursor_mz:
        parts.append(f"{_fmt(u.n_no_precursor_mz)} carrying no precursor m/z")
    tail = ""
    if u.n_unresolved_confirmed:
        tail += (
            " The first group is a peak-picking limitation, not a data problem "
            "— filter on <code>precursor_confirmed</code> / <code>precursor_frac</code>."
        )
    if u.n_off_pixel:
        tail += " The off-pixel scans can be treated as out-of-ROI acquisitions."
    return (
        f"<p>Peak-based precursor purity is unscored for {_fmt(u.n_unscored)} "
        f"MS2 scans: " + "; ".join(parts) + "." + tail + "</p>"
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

    figures = [
        figure_per_sample(stats.samples),
        figure_overlap_upset(stats.overlap_combos, sample_names),
        figure_ms2_association(stats.ms2),
        figure_ms2_association_per_sample(stats.per_sample_ms2),
        figure_unassociated_recheck(stats.recheck),
        figure_purity(stats.associated_purity),
        figure_purity_per_sample(stats.associated_purity),
        figure_purity_unscored(stats.unscored),
    ]

    json_path = out_dir / _REPORT_JSON
    json_path.write_text(json.dumps(stats.to_dict(), indent=2))

    blocks = [
        fig.to_html(
            full_html=False, include_plotlyjs="cdn" if i == 0 else False
        )
        for i, fig in enumerate(figures)
    ]
    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>MSIAnalyzer summary report</title>"
        "<style>body{font-family:system-ui,sans-serif;margin:24px;max-width:1100px}"
        "table{font-size:14px}h1{font-size:20px}h2{font-size:16px;margin-top:32px}"
        "</style></head><body>"
        "<h1>MSIAnalyzer summary report</h1>"
        f"<p>{_fmt(len(stats.samples))} sample(s) &middot; "
        f"{_fmt(stats.n_features)} features &middot; "
        f"{_fmt(stats.ms2.n_total)} MS2 scans "
        f"({_fmt(stats.ms2.n_associated)} associated to a feature)</p>"
        f"{_assoc_purity_sentence(stats.associated_purity)}"
        f"{_recheck_sentence(stats.recheck)}"
        f"{_unscored_sentence(stats.unscored)}"
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
