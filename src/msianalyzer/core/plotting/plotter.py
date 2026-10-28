"""
plotter.py
Visualization module for msianalyzer.

Plotly figures for the HTML report, plus the plain-data builders
(`spectrum_groups`, `mirror_plot_data`, the calibration helpers) the GUI's
Qt Graphs plots draw from (ADR 51) — one place for the shared logic.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Optional

import numpy as np
import plotly.graph_objects as go

from msianalyzer.core.annotation.ms2_cleaning import load_cleaner
from msianalyzer.core.parser.mzml_parser import blob_to_array
from msianalyzer.core.annotation.spectral_match import (
    _align_peaks,
    normalize_and_filter_spectrum,
)
from msianalyzer.core.utils.logging_utils import log_call

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------

_BLUE_MATCHED = "#1f77b4"
_CONNECTOR = "#888888"

# Mirror plot (Plotter.plot_ms2_annotation): fragments that match across
# empirical/library (within ppm tolerance) are black, everything else gray
# — one shared meaning for "matched" regardless of which side (or which
# raw/filtered source) a peak is on, rather than a separate color per side.
_MIRROR_MATCHED = "#000000"
_MIRROR_UNMATCHED = "#b0b0b0"
_DEFAULT_FRAGMENT_PPM_TOLERANCE = 10.0
# AnnotateConfig.noise_threshold's own default — used when a row's owning
# command can't be found or its `arguments` don't carry the key (an older
# database, or a hand-built test row).
_DEFAULT_NOISE_THRESHOLD = 0.01

# MS1 spectrum peak coloring by feature category (Plotter.plot_spectra) —
# order here is also legend order.
_MS1_CATEGORY_COLORS = {
    "no_ms2": "#999999",
    "non_annotated": "#000000",
    "annotated": _BLUE_MATCHED,
}
_MS1_CATEGORY_LABELS = {
    "no_ms2": "No MS2",
    "non_annotated": "Non-annotated",
    "annotated": "Annotated",
}


def spectrum_groups(mz_array, intensity_array, categories) -> list[dict]:
    """A spectrum's peaks split by feature category, as plain data for the
    GUI's stick plot (ADR 51): `[{"key", "name", "peaks": [[mz, intensity],
    ...]}]`, in `_MS1_CATEGORY_LABELS` order, empty categories left out.
    Same split and labels as `Plotter.plot_spectra`'s traces."""
    mz_array = np.asarray(mz_array, dtype=float)
    intensity_array = np.asarray(intensity_array, dtype=float)
    categories = np.asarray(categories)
    groups = []
    for category, label in _MS1_CATEGORY_LABELS.items():
        mask = categories == category
        if mask.any():
            peaks = np.column_stack([mz_array[mask], intensity_array[mask]]).tolist()
            groups.append({"key": category, "name": label, "peaks": peaks})
    return groups


def _connector_pairs(
    empirical_mz, empirical_int, library_mz, library_int, ppm_tolerance: float
) -> list[tuple[float, float, float]]:
    """Mirror plot's matched pairs: `(library_mz, empirical_intensity,
    library_intensity)` for every library peak with an empirical peak
    within `ppm_tolerance` (the tallest one, if several)."""
    if len(empirical_mz) == 0:
        return []
    order = np.argsort(empirical_mz)
    q_mz_sorted = np.asarray(empirical_mz)[order]
    q_int_sorted = np.asarray(empirical_int)[order]
    pairs = []
    for lib_m, lib_i in zip(library_mz, library_int):
        tol_da = lib_m * ppm_tolerance * 1e-6
        lo = np.searchsorted(q_mz_sorted, lib_m - tol_da, side="left")
        hi = np.searchsorted(q_mz_sorted, lib_m + tol_da, side="right")
        if lo == hi:
            continue
        best = int(np.argmax(q_int_sorted[lo:hi])) + lo
        pairs.append((float(lib_m), float(q_int_sorted[best]), float(lib_i)))
    return pairs


def mirror_plot_data(data: dict) -> dict:
    """One `Plotter.get_annotation_spectra` result as plain data for the
    GUI's mirror plot (ADR 51), the same matching as `build_mirror_figure`:

    `{"title", "groups": [{"key", "name", "peaks": [[mz, y], ...]}],
    "connectors": [[mz, empirical_y, -library_y], ...]}`. Empirical peaks
    point up, library peaks down (negative y); keys are `"matched"` /
    `"unmatched"`, and each name appears once in the legend.
    """
    emp_mz = np.asarray(data["empirical_mz"], dtype=float)
    emp_int = np.asarray(data["empirical_intensity"], dtype=float)
    lib_mz = np.asarray(data["library_mz"], dtype=float)
    lib_int = np.asarray(data["library_intensity"], dtype=float)
    tol = data["fragment_ppm_tolerance"]
    _, emp_matched = _align_peaks(lib_mz, lib_int, emp_mz, emp_int, tol)
    _, lib_matched = _align_peaks(emp_mz, emp_int, lib_mz, lib_int, tol)

    def peaks(mz, intensity, mask, direction):
        return np.column_stack([mz[mask], intensity[mask] * direction]).tolist()

    groups = [
        {"key": "matched", "name": "Matched", "peaks": peaks(emp_mz, emp_int, emp_matched, 1)},
        {"key": "unmatched", "name": "Unmatched", "peaks": peaks(emp_mz, emp_int, ~emp_matched, 1)},
        {"key": "matched", "name": "", "peaks": peaks(lib_mz, lib_int, lib_matched, -1)},
        {"key": "unmatched", "name": "", "peaks": peaks(lib_mz, lib_int, ~lib_matched, -1)},
    ]
    sources = (data["emp_source"], data["lib_source"])
    source_note = (
        f"  ·  empirical: {sources[0]}, library: {sources[1]}"
        if sources != ("filtered", "filtered") else ""
    )
    return {
        "title": f"{data['compound_name']}  ({data['compound_formula']}){source_note}",
        "groups": [g for g in groups if g["peaks"]],
        "connectors": [
            [m, e, -l] for m, e, l in _connector_pairs(emp_mz, emp_int, lib_mz, lib_int, tol)
        ],
    }


def calibration_line_range(x) -> tuple[float, float]:
    """Where a calibration curve's fit line starts and ends: 5% past the
    standards on both ends. A line only between the outermost standards
    reads oddly next to a fit meant to extrapolate to unknowns outside
    their range. Shared by the report's Plotly figure and the GUI's plot."""
    x_min, x_max = float(np.min(x)), float(np.max(x))
    pad = (x_max - x_min) * 0.05 if x_max > x_min else max(abs(x_max), 1.0) * 0.05
    return x_min - pad, x_max + pad


def calibration_title(fit) -> str:
    """A calibration curve's equation and R², its plot title."""
    return f"y = {fit.slope:.4g}x + {fit.intercept:.4g}   R² = {fit.r_squared:.4f}"


def _normalize_to_max(intensity: np.ndarray) -> np.ndarray:
    """Scale to [0, 1] by the array's own max — a no-op for the filtered
    spectra (already max-normalised at annotation time), and what puts a
    *raw* spectrum on the same visual scale for the mirror plot."""
    intensity = np.asarray(intensity, dtype=float)
    peak = intensity.max() if intensity.size else 0.0
    return intensity / peak if peak > 0 else intensity


def _read_raw_ms2_scan(
    raw_db_path: str | None, scan_id: int
) -> tuple[np.ndarray, np.ndarray] | tuple[None, None]:
    """The untouched (mz, intensity) arrays for one MS2 scan, straight from
    a sample's raw per-sample database — `(None, None)` when the path is
    unset, the file doesn't exist, or the scan isn't in it."""
    if not raw_db_path or not Path(raw_db_path).exists():
        return None, None
    con = sqlite3.connect(f"file:{raw_db_path}?mode=ro", uri=True)
    try:
        row = con.execute(
            "SELECT mz_array, intensity_array FROM ms2_scans WHERE scan_id = ?",
            (scan_id,),
        ).fetchone()
    finally:
        con.close()
    if row is None:
        return None, None
    return blob_to_array(row[0]), blob_to_array(row[1])


def _read_raw_library_spectrum(
    library_path: str | None, spectrum_id: int
) -> tuple[np.ndarray, np.ndarray] | tuple[None, None]:
    """The untouched (mz, intensity) arrays for one library spectrum,
    re-read from the library file itself (not the filtered/normalised copy
    `ms2_annotations` stores) — `(None, None)` when the path is unset, the
    file doesn't exist, or the spectrum id isn't in it.

    Imports `libviz`/the annotation module lazily, same reasoning as
    `annotation.annotate.load_library`: most callers of this module never
    touch a library file at all.
    """
    if not library_path or not Path(library_path).exists():
        return None, None
    from libviz.core.db.models import Spectrum

    from msianalyzer.core.annotation.annotate import load_library

    library = load_library(library_path)
    with library.session_scope() as session:
        spectrum = session.query(Spectrum).filter_by(id=spectrum_id).one_or_none()
        if spectrum is None:
            return None, None
        mz = np.frombuffer(spectrum.mz_array, dtype=np.float32).copy()
        intensity = np.frombuffer(spectrum.intensity_array, dtype=np.float32).copy()
        return mz, intensity


class Plotter:
    """Builds interactive Plotly figures from msianalyzer outputs.

    The class is stateless: every method takes the data (or the database
    path) it needs as an argument. The figures are for the HTML report;
    the GUI plots the module-level plain-data builders instead (ADR 51).
    """

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    @staticmethod
    @log_call
    def plot_spectra(
        mz_array: np.ndarray,
        intensity_array: np.ndarray,
        categories: np.ndarray | None = None,
        color: str = "black",
        height: int = 500,
    ) -> go.Figure:
        """Plot a single spectrum as a centroid (stick) figure.

        Draws one vertical line per peak with invisible markers carrying
        hover tooltips for m/z and intensity.

        Args:
            mz_array: Peak m/z values.
            intensity_array: Peak intensities, parallel to `mz_array`.
            categories: Optional, parallel to `mz_array` — each peak's
                `"no_ms2"` / `"annotated"` / `"non_annotated"` feature
                category (see `analysis_db.load_feature_categories`). When
                given, peaks are split into one trace per category
                (`_MS1_CATEGORY_COLORS`), each with a single legend entry
                (its stick color, not a separate marker entry) — the GUI's
                MS1 Spectra section uses this to distinguish features with
                no MS2 (gray), annotated (blue) and non-annotated (black).
                `None` (the default) draws every peak as one plain,
                unlabeled trace in `color`, same as before this parameter
                existed.
            color: Line/marker colour when `categories` is `None`.
                Defaults to `"black"`.
            height: Figure height in pixels. Defaults to 500.

        Returns:
            A Plotly `Figure` containing the spectrum.
        """
        mz_array = np.asarray(mz_array)
        intensity_array = np.asarray(intensity_array)

        fig = go.Figure()

        if categories is None:
            Plotter._add_spectrum_trace(fig, mz_array, intensity_array, color)
        else:
            categories = np.asarray(categories)
            for category, label in _MS1_CATEGORY_LABELS.items():
                mask = categories == category
                if not mask.any():
                    continue
                Plotter._add_spectrum_trace(
                    fig,
                    mz_array[mask],
                    intensity_array[mask],
                    _MS1_CATEGORY_COLORS[category],
                    name=label,
                    legendgroup=category,
                )

        fig.update_layout(
            height=height,
            xaxis=dict(title="m/z", showgrid=True, gridcolor="#eeeeee", zeroline=False),
            yaxis=dict(
                title="Intensity",
                showgrid=True,
                gridcolor="#eeeeee",
                zeroline=False,
            ),
            plot_bgcolor="white",
            paper_bgcolor="white",
            hovermode="x unified",
            margin=dict(t=100, b=50, l=60, r=20),
        )

        return fig

    @staticmethod
    @log_call
    def plot_calibration_curve(
        points: list[tuple[float, float]],
        fit,
        concentration_unit: str,
        height: int = 450,
    ) -> go.Figure:
        """Plot a fitted calibration curve: standards as markers, the fit
        as a line, extended slightly past the data on both ends.

        Args:
            points: `(concentration, signal)` pairs — same shape
                `core.calibration.curve_fit.fit_calibration_curve` takes.
                `signal` is the analyte/reference-channel intensity ratio
                (see `core.calibration.roi_intensity.roi_internal_standard_signal`),
                not a raw intensity.
            fit: A `core.calibration.curve_fit.CalibrationFitResult`.
            concentration_unit: e.g. `"pmol/mm2"` — the x-axis label.
            height: Figure height in pixels. Defaults to 450.

        Returns:
            A Plotly `Figure` with the standards, the fit line, and its
            R²/slope/intercept in the title.
        """
        x = np.array([p[0] for p in points], dtype=float)
        y = np.array([p[1] for p in points], dtype=float)

        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=x, y=y, mode="markers", name="Standards",
                marker=dict(color=_BLUE_MATCHED, size=9),
                hovertemplate="concentration=%{x:.4g}<br>signal=%{y:.4g}<extra></extra>",
            )
        )

        line_x = np.linspace(*calibration_line_range(x), 50)
        line_y = fit.slope * line_x + fit.intercept
        fig.add_trace(
            go.Scatter(
                x=line_x, y=line_y, mode="lines", name="Fit",
                line=dict(color=_CONNECTOR, dash="dash"),
                hoverinfo="skip",
            )
        )

        fig.update_layout(
            title=calibration_title(fit),
            xaxis=dict(
                title=f"Concentration ({concentration_unit})",
                showgrid=True, gridcolor="#eeeeee", zeroline=False,
                # Plotly's default tick auto-formatting falls back to SI
                # unit-prefix notation (e.g. "0.5µ" meaning 0.5e-6) for
                # very small/large ranges — easy to misread as a literal
                # unit rather than a magnitude prefix, since this axis
                # already has its own real unit in the title. An explicit
                # general-format tickformat (not the "s"/SI type) always
                # renders plain numbers or plain scientific notation
                # instead.
                tickformat=".3g",
            ),
            yaxis=dict(
                title="Signal (analyte / reference)",
                showgrid=True, gridcolor="#eeeeee", zeroline=False,
                tickformat=".3g",
            ),
            plot_bgcolor="white",
            paper_bgcolor="white",
            height=height,
            margin=dict(t=60, b=50, l=60, r=20),
        )
        return fig

    @staticmethod
    def _add_spectrum_trace(
        fig: go.Figure,
        mz_array: np.ndarray,
        intensity_array: np.ndarray,
        color: str,
        name: str | None = None,
        legendgroup: str | None = None,
    ) -> None:
        """One category's sticks (a single legend entry, `name`) plus its
        ghost hover markers (`showlegend=False` — a category should only
        ever contribute one legend entry, not one per trace)."""
        xs, ys = [], []
        for m, i in zip(mz_array, intensity_array):
            xs += [m, m, None]
            ys += [0, i, None]

        fig.add_trace(
            go.Scatter(
                x=xs,
                y=ys,
                mode="lines",
                line=dict(color=color, width=2),
                name=name,
                legendgroup=legendgroup,
                showlegend=True,
                hoverinfo="skip",
            )
        )
        # Ghost markers for hover tooltips — not their own legend entry.
        fig.add_trace(
            go.Scatter(
                x=mz_array,
                y=intensity_array,
                mode="markers",
                marker=dict(color=color, size=6),
                legendgroup=legendgroup,
                showlegend=False,
                customdata=np.abs(intensity_array).reshape(-1, 1),
                hovertemplate=(
                    "<b>m/z</b>: %{x:.4f}<br>"
                    "<b>intensity</b>: %{customdata[0]:.3f}"
                    "<extra></extra>"
                ),
            )
        )

    def get_annotation_spectra(
        self,
        analysis_db_path: Path | str,
        annotation_id: int,
        emp_source: str = "filtered",
        lib_source: str = "filtered",
        fragment_ppm_tolerance: float | None = None,
    ) -> dict:
        """Resolve one annotation's empirical/library (mz, intensity)
        arrays and metadata — the data both `plot_ms2_annotation`
        (interactive Plotly) and the GUI's fast raster mirror plot
        (`mirror_plot_raster.render_mirror_plot_png`) need, without either
        one building a full Plotly figure just to get it.

        See `plot_ms2_annotation` for what `emp_source`/`lib_source`/
        `fragment_ppm_tolerance` mean and what this raises.

        Returns:
            `{"empirical_mz", "empirical_intensity", "library_mz",
            "library_intensity"}` (already max-normalised — a no-op for
            the filtered spectra, which `normalize_and_filter_spectrum`
            already max-normalises as part of reconstructing them),
            `"fragment_ppm_tolerance"` (resolved), plus the annotation's
            own metadata fields (`compound_name`, `compound_formula`,
            `inchikey`, `score`, `dot_product_score`, `lib_coverage`,
            `emp_coverage`, `n_matched_peaks`, `n_lib_peaks`,
            `n_emp_peaks_raw`, `n_emp_peaks_filtered`, `scan_id`,
            `precursor_mz`, `library_name`).
        """
        if emp_source not in ("filtered", "raw"):
            raise ValueError(f"emp_source must be 'filtered' or 'raw', got {emp_source!r}")
        if lib_source not in ("filtered", "raw"):
            raise ValueError(f"lib_source must be 'filtered' or 'raw', got {lib_source!r}")

        ann = self._fetch_annotation(analysis_db_path, annotation_id)

        resolved_ppm, noise_threshold = self._resolve_annotate_args(
            analysis_db_path, ann["command_id"]
        )
        if fragment_ppm_tolerance is None:
            fragment_ppm_tolerance = resolved_ppm

        empirical_mz, empirical_int = self._resolve_side(
            ann, side="emp", source=emp_source, annotation_id=annotation_id,
            noise_threshold=noise_threshold, cleaner=load_cleaner(analysis_db_path),
        )
        library_mz, library_int = self._resolve_side(
            ann, side="lib", source=lib_source, annotation_id=annotation_id,
            noise_threshold=noise_threshold,
        )
        empirical_int = _normalize_to_max(empirical_int)
        library_int = _normalize_to_max(library_int)

        return {
            "empirical_mz": empirical_mz,
            "empirical_intensity": empirical_int,
            "library_mz": library_mz,
            "library_intensity": library_int,
            "fragment_ppm_tolerance": fragment_ppm_tolerance,
            "emp_source": emp_source,
            "lib_source": lib_source,
            **{
                key: ann[key]
                for key in (
                    "compound_name", "compound_formula", "inchikey", "score",
                    "dot_product_score", "lib_coverage", "emp_coverage",
                    "n_matched_peaks", "n_lib_peaks", "n_emp_peaks_raw",
                    "n_emp_peaks_filtered", "scan_id", "precursor_mz", "library_name",
                )
            },
        }

    def get_annotation_metadata(self, analysis_db_path: Path | str, annotation_id: int) -> dict:
        """Display metadata for one `ms2_annotations` row — compound
        identity, scores, coverage, and peak counts — split out from
        `get_annotation_spectra` because none of these fields depend on
        `emp_source`/`lib_source`. The GUI's mirror-plot detail window uses
        this for its side metadata table, so it doesn't need to resolve or
        reconstruct any spectra (a potentially slow raw re-read) just to
        show numbers that never change with the source toggle.

        Returns:
            `{"compound_name", "compound_formula", "inchikey",
            "library_name", "score", "dot_product_score", "coverage_score",
            "lib_coverage", "emp_coverage", "n_matched_peaks",
            "n_lib_peaks", "n_emp_peaks_raw", "n_emp_peaks_filtered",
            "scan_id", "precursor_mz", "fragment_ppm_tolerance"}`.

        Raises:
            ValueError: No such annotation.
        """
        ann = self._fetch_annotation(analysis_db_path, annotation_id)
        fragment_ppm_tolerance, _ = self._resolve_annotate_args(
            analysis_db_path, ann["command_id"]
        )
        return {
            key: ann[key]
            for key in (
                "compound_name", "compound_formula", "inchikey", "library_name",
                "score", "dot_product_score", "coverage_score", "lib_coverage",
                "emp_coverage", "n_matched_peaks", "n_lib_peaks", "n_emp_peaks_raw",
                "n_emp_peaks_filtered", "scan_id", "precursor_mz",
            )
        } | {"fragment_ppm_tolerance": fragment_ppm_tolerance}

    @log_call
    def plot_ms2_annotation(
        self,
        analysis_db_path: Path | str,
        annotation_id: int,
        title: Optional[str] = None,
        fragment_ppm_tolerance: float | None = None,
        emp_source: str = "filtered",
        lib_source: str = "filtered",
        height: int = 500,
    ) -> go.Figure:
        """
        Mirror plot: empirical MS2 (top) vs library match (bottom).

        By default both spectra are shown noise-filtered and
        max-normalised — reconstructed from the *untouched* arrays stored
        at annotation time (``AnnotateConfig.store_raw_spectra``, on by
        default) plus this run's own ``noise_threshold``, via
        :func:`~msianalyzer.core.annotation.spectral_match.normalize_and_filter_spectrum`
        (see ADR 0018); this is a pure, deterministic reconstruction of
        exactly what was scored, not a second persisted copy.
        ``emp_source``/``lib_source`` can each independently ask for the
        *raw* spectrum instead. Either source prefers the stored raw
        arrays and falls back to a live re-read only when they're missing:
        the empirical one from the owning sample's raw per-sample database
        (``ms2_scans``, by ``scan_id``), the library one from the library
        file itself (by ``library_spectrum_id``). Whichever pair is shown
        is re-normalised to its own max intensity for display (a no-op for
        the filtered view, already max-normalised by reconstruction) — raw
        and filtered views are visually comparable this way.

        Matched fragments (within ``fragment_ppm_tolerance`` of a peak on
        the other side) are black; unmatched ones are gray. Dashed gray
        lines connect matched peak pairs across the mirror axis.

        Args:
            analysis_db_path: The analysis database.
            annotation_id: Primary key in ``ms2_annotations``.
            title: Plot title. Auto-generated from annotation metadata if
                None.
            fragment_ppm_tolerance: ppm tolerance for deciding which peaks
                "match" for coloring/connector purposes. `None` (the
                default) looks up the run's own ``fragment_ppm`` from the
                ``annotate_ms2`` command that produced this row (so this
                always matches what core actually used to score it, not
                just an approximation of it), falling back to 10.0 if that
                can't be found (e.g. a database from before commands
                carried this argument). The stored `score` /
                `n_matched_peaks` are never recomputed either way.
            emp_source: `"filtered"` (default) or `"raw"` — which
                empirical spectrum to display.
            lib_source: `"filtered"` (default) or `"raw"` — which library
                spectrum to display.
            height: Figure height in pixels.

        Returns:
            plotly.graph_objects.Figure

        Raises:
            ValueError: No such annotation; no raw spectrum is available to
                resolve or reconstruct from (not stored on this row, and
                the raw per-sample database or library file can't be
                found, or the scan/spectrum id no longer exists in it); or
                `emp_source`/`lib_source` isn't `"filtered"`/`"raw"`.
        """
        data = self.get_annotation_spectra(
            analysis_db_path, annotation_id, emp_source, lib_source, fragment_ppm_tolerance
        )
        return self.build_mirror_figure(data, title=title, height=height)

    def build_mirror_figure(
        self, data: dict, title: Optional[str] = None, height: int = 500
    ) -> go.Figure:
        """The Plotly figure for one `get_annotation_spectra` result.

        Split out from `plot_ms2_annotation` so the (potentially slow —
        raw source) data-resolution step and this (fast, pure
        Python/Plotly, CPU-only) figure-building step can run on
        different threads: `AnalysisBridge`'s `MirrorPlotWorker` calls
        `get_annotation_spectra` on a background `QThread` (see its
        docstring for why), but calling *this* method there too
        reproducibly crashed — Plotly's own object graph isn't safe to
        build concurrently with the main thread's own Python activity
        (observed: a segfault inside `plotly`'s `_perform_plotly_relayout`
        while the main thread was mid garbage-collection). `AnalysisBridge`
        therefore calls this method back on the main thread once the
        worker's data arrives.

        Args:
            data: A `get_annotation_spectra` result.
            title: Plot title. Auto-generated from `data`'s metadata if None.
            height: Figure height in pixels.

        Returns:
            plotly.graph_objects.Figure
        """
        empirical_mz = data["empirical_mz"]
        empirical_int = data["empirical_intensity"]
        library_mz = data["library_mz"]
        library_int = data["library_intensity"]
        fragment_ppm_tolerance = data["fragment_ppm_tolerance"]
        emp_source = data["emp_source"]
        lib_source = data["lib_source"]
        ann = data

        # Match masks — each sized to its own spectrum
        _, emp_matched_mask = _align_peaks(
            library_mz,
            library_int,
            empirical_mz,
            empirical_int,
            fragment_ppm_tolerance,
        )
        _, lib_matched_mask = _align_peaks(
            empirical_mz,
            empirical_int,
            library_mz,
            library_int,
            fragment_ppm_tolerance,
        )

        fig = go.Figure()

        self._add_bars(
            fig,
            mz=empirical_mz,
            intensity=empirical_int,
            matched_mask=emp_matched_mask,
            direction=1,
            legend_matched="Matched",
            legend_unmatched="Unmatched",
            show_legend=True,
        )
        self._add_bars(
            fig,
            mz=library_mz,
            intensity=library_int,
            matched_mask=lib_matched_mask,
            direction=-1,
            legend_matched="Matched",
            legend_unmatched="Unmatched",
            show_legend=False,
        )
        self._add_connectors(
            fig,
            empirical_mz=empirical_mz,
            empirical_int=empirical_int,
            library_mz=library_mz,
            library_int=library_int,
            ppm_tolerance=fragment_ppm_tolerance,
        )

        fig.add_hline(y=0, line_width=1, line_color="black")

        # The score/coverage/peak-count breakdown and scan/precursor/library
        # identifiers used to be packed into this title plus a corner
        # annotation box — moved to a dedicated side table in the GUI's
        # mirror-plot detail window instead (ADR — see
        # gui/qml/Views/MirrorPlotDetailWindow.qml), so the title now only
        # names what's plotted; the raw/filtered source note stays here
        # since it directly describes what the two curves below actually
        # show, not identity/score metadata.
        source_note = (
            f"  ·  empirical: {emp_source}, library: {lib_source}"
            if (emp_source, lib_source) != ("filtered", "filtered")
            else ""
        )
        auto_title = title or (
            f"{ann['compound_name']}  ({ann['compound_formula']}){source_note}"
        )

        fig.update_layout(
            title=dict(text=auto_title, font=dict(size=12)),
            height=height,
            xaxis=dict(title="m/z", showgrid=True, gridcolor="#eeeeee", zeroline=False),
            yaxis=dict(
                title="Normalised intensity",
                range=[-1.15, 1.15],
                showgrid=True,
                gridcolor="#eeeeee",
                zeroline=False,
                tickvals=[-1, -0.5, 0, 0.5, 1],
                ticktext=["1.0", "0.5", "0", "0.5", "1.0"],
            ),
            plot_bgcolor="white",
            paper_bgcolor="white",
            legend=dict(
                orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1
            ),
            hovermode="x unified",
            margin=dict(t=70, b=50, l=60, r=20),
        )

        # Which mirrored half is which — bars pointing up (direction=1) are
        # always empirical, down (direction=-1) always library (see
        # _add_bars calls above); label both explicitly since the y-axis
        # itself is just a symmetric "Normalised intensity" scale with no
        # indication otherwise.
        fig.add_annotation(
            xref="paper", yref="paper", x=0.99, y=0.97,
            xanchor="right", yanchor="top",
            text="Empirical", showarrow=False,
            font=dict(size=11, color="#333333"),
            bgcolor="rgba(255,255,255,0.7)",
        )
        fig.add_annotation(
            xref="paper", yref="paper", x=0.99, y=0.03,
            xanchor="right", yanchor="bottom",
            text="Library", showarrow=False,
            font=dict(size=11, color="#333333"),
            bgcolor="rgba(255,255,255,0.7)",
        )

        return fig

    # ------------------------------------------------------------------
    # Figure building helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _add_bars(
        fig: go.Figure,
        mz: np.ndarray,
        intensity: np.ndarray,
        matched_mask: np.ndarray,
        direction: int,
        legend_matched: str,
        legend_unmatched: str,
        show_legend: bool,
    ) -> None:
        """Matched fragments black, unmatched gray — same meaning (and same
        legend entries, via a shared `legendgroup`) on both the empirical
        (`direction=1`) and library (`direction=-1`) side. `show_legend`
        is False for the second call (library) so "Matched"/"Unmatched"
        each appear once, not once per side."""
        for mask, color, name in (
            (matched_mask, _MIRROR_MATCHED, legend_matched),
            (~matched_mask, _MIRROR_UNMATCHED, legend_unmatched),
        ):
            if not mask.any():
                continue

            mz_sel = mz[mask]
            int_sel = intensity[mask] * direction

            xs, ys = [], []
            for m, i in zip(mz_sel, int_sel):
                xs += [m, m, None]
                ys += [0, i, None]

            fig.add_trace(
                go.Scatter(
                    x=xs,
                    y=ys,
                    mode="lines",
                    name=name,
                    line=dict(color=color, width=2),
                    legendgroup=name,
                    showlegend=show_legend,
                    hoverinfo="skip",
                )
            )
            # Ghost markers for hover tooltips
            fig.add_trace(
                go.Scatter(
                    x=mz_sel,
                    y=int_sel,
                    mode="markers",
                    marker=dict(color=color, size=6),
                    name=name,
                    legendgroup=name,
                    showlegend=False,
                    customdata=np.abs(int_sel).reshape(-1, 1),
                    hovertemplate=(
                        "<b>m/z</b>: %{x:.4f}<br>"
                        "<b>intensity</b>: %{customdata[0]:.3f}"
                        "<extra></extra>"
                    ),
                )
            )

    @staticmethod
    def _add_connectors(
        fig: go.Figure,
        empirical_mz: np.ndarray,
        empirical_int: np.ndarray,
        library_mz: np.ndarray,
        library_int: np.ndarray,
        ppm_tolerance: float,
    ) -> None:
        xs, ys = [], []
        for lib_m, emp_i, lib_i in _connector_pairs(
            empirical_mz, empirical_int, library_mz, library_int, ppm_tolerance
        ):
            xs += [lib_m, lib_m, None]
            ys += [emp_i, -lib_i, None]

        if xs:
            fig.add_trace(
                go.Scatter(
                    x=xs,
                    y=ys,
                    mode="lines",
                    line=dict(color=_CONNECTOR, width=1, dash="dot"),
                    name="Matched pair",
                    showlegend=True,
                    hoverinfo="skip",
                )
            )

    # ------------------------------------------------------------------
    # Database access
    # ------------------------------------------------------------------

    def _fetch_annotation(self, analysis_db_path, annotation_id: int) -> dict:
        """One `ms2_annotations` row, joined with its library's name/path,
        its owning sample's raw database path, and the scan's
        `precursor_mz` (from `ms2_associations`). The stored *untouched*
        spectra (`emp_raw_mz`/`emp_raw_intensity`, `lib_raw_mz`/
        `lib_raw_intensity`) are decoded to arrays here — empty when
        `store_raw_spectra` was off, or (for `emp_raw_*`) for rows written
        before that column existed; `_resolve_raw` falls back to a live
        re-read (the raw per-sample database for `emp`, the library file
        for `lib`) in either case. The noise-filtered view is never fetched
        or stored — `_resolve_side` reconstructs it on demand from these
        raw arrays plus the run's `noise_threshold` (see ADR 0018)."""
        con = sqlite3.connect(f"file:{analysis_db_path}?mode=ro", uri=True)
        try:
            row = con.execute(
                """
                SELECT a.sample_id, a.scan_id, a.compound_name,
                       a.compound_formula, a.inchikey, a.score,
                       a.dot_product_score, a.lib_coverage, a.emp_coverage,
                       a.coverage_score, a.n_matched_peaks, a.n_lib_peaks,
                       a.n_emp_peaks_raw, a.n_emp_peaks_filtered,
                       a.emp_raw_mz, a.emp_raw_intensity,
                       a.lib_raw_mz, a.lib_raw_intensity,
                       a.library_spectrum_id, a.command_id,
                       lib.name AS library_name, lib.path AS library_path,
                       s.raw_db_path AS sample_raw_db_path,
                       assoc.precursor_mz, assoc.polarity, f.mz AS feature_mz
                FROM ms2_annotations a
                LEFT JOIN features f ON f.feature_id = a.feature_id
                LEFT JOIN annotation_libraries lib ON lib.id = a.library_id
                LEFT JOIN samples s ON s.sample_id = a.sample_id
                LEFT JOIN ms2_associations assoc
                    ON assoc.sample_id = a.sample_id AND assoc.scan_id = a.scan_id
                WHERE a.id = ?
                """,
                (annotation_id,),
            ).fetchone()
        finally:
            con.close()

        if row is None:
            raise ValueError(f"No annotation found with id={annotation_id}")

        keys = (
            "sample_id",
            "scan_id",
            "compound_name",
            "compound_formula",
            "inchikey",
            "score",
            "dot_product_score",
            "lib_coverage",
            "emp_coverage",
            "coverage_score",
            "n_matched_peaks",
            "n_lib_peaks",
            "n_emp_peaks_raw",
            "n_emp_peaks_filtered",
            "emp_raw_mz_blob",
            "emp_raw_intensity_blob",
            "lib_raw_mz_blob",
            "lib_raw_intensity_blob",
            "library_spectrum_id",
            "command_id",
            "library_name",
            "library_path",
            "sample_raw_db_path",
            "precursor_mz",
            "polarity",
            "feature_mz",
        )
        d = dict(zip(keys, row))

        for arr_key, blob_key in (
            ("emp_raw_mz_stored", "emp_raw_mz_blob"),
            ("emp_raw_intensity_stored", "emp_raw_intensity_blob"),
            ("lib_raw_mz_stored", "lib_raw_mz_blob"),
            ("lib_raw_intensity_stored", "lib_raw_intensity_blob"),
        ):
            blob = d.pop(blob_key)
            d[arr_key] = (
                blob_to_array(blob, compressed=True)
                if blob is not None
                else np.array([], dtype=np.float32)
            )
        return d

    def _resolve_raw(self, ann: dict, side: str, annotation_id: int) -> tuple[np.ndarray, np.ndarray]:
        """The untouched (mz, intensity) arrays for one side ("emp"/"lib").

        Both sides prefer the *stored* copy persisted at annotation time
        by `annotate.persist_annotations` (`emp_raw_mz`/`emp_raw_intensity`
        for "emp", `lib_raw_mz`/`lib_raw_intensity` for "lib" — see ADR
        0016/0018) when present, and only fall back to a live re-read —
        the owning sample's raw database for "emp", the library file
        itself for "lib" — for rows written before those columns existed,
        or with `store_raw_spectra` off. The live path is the one that can
        mean slow/remote file I/O, which is why raw-spectrum viewing used
        to be a real crash risk before these stored copies existed.
        """
        stored_mz, stored_int = ann[f"{side}_raw_mz_stored"], ann[f"{side}_raw_intensity_stored"]
        if len(stored_mz) > 0:
            return stored_mz, stored_int

        if side == "emp":
            mz, intensity = _read_raw_ms2_scan(ann["sample_raw_db_path"], ann["scan_id"])
            if mz is None:
                raise ValueError(
                    f"annotation id={annotation_id}: raw empirical spectrum "
                    f"not found (not stored on this row, and the sample raw "
                    f"database {ann['sample_raw_db_path']!r}, scan "
                    f"{ann['scan_id']} couldn't be re-read live)"
                )
            return mz, intensity

        mz, intensity = _read_raw_library_spectrum(
            ann["library_path"], ann["library_spectrum_id"]
        )
        if mz is None:
            raise ValueError(
                f"annotation id={annotation_id}: raw library spectrum not "
                f"found (not stored on this row, and the library "
                f"{ann['library_path']!r}, spectrum id "
                f"{ann['library_spectrum_id']} couldn't be re-read live)"
            )
        return mz, intensity

    def _resolve_side(
        self, ann: dict, side: str, source: str, annotation_id: int, noise_threshold: float,
        cleaner=None,
    ) -> tuple[np.ndarray, np.ndarray]:
        """The (mz, intensity) arrays to plot for one side ("emp"/"lib").

        Both `source="raw"` and `source="filtered"` start from the same
        untouched spectrum (`_resolve_raw`); `"filtered"` additionally
        noise-filters and max-normalises it via
        `spectral_match.normalize_and_filter_spectrum` using this run's own
        `noise_threshold` — a pure, deterministic reconstruction of exactly
        what was scored, rather than a second persisted copy (see ADR
        0018).
        """
        mz, intensity = self._resolve_raw(ann, side, annotation_id)
        if source == "raw":
            return mz, intensity
        if side == "emp" and cleaner is not None:
            # What was scored: MS2 cleaning first (ADR 0064).
            # ponytail: charge unknown here (not in the analysis DB), so a
            # 2+ precursor's peaks above it are hidden in this view though
            # scoring kept them; store the charge if that ever matters.
            from msianalyzer.core.annotation.annotate import normalize_polarity

            mz, intensity = cleaner.clean(
                mz, intensity, normalize_polarity(ann.get("polarity")),
                ann.get("feature_mz") or ann.get("precursor_mz"),
            )
        return normalize_and_filter_spectrum(mz, intensity, noise_threshold)

    @staticmethod
    def _resolve_annotate_args(analysis_db_path, command_id: int | None) -> tuple[float, float]:
        """`(fragment_ppm, noise_threshold)` the `annotate_ms2` run that
        produced this row actually used, from that command's stored
        `arguments` JSON (one parse serves both — they're the same JSON
        blob) — each falls back to its own `AnnotateConfig` default when
        `command_id` is unset or the lookup fails (an older database, a
        hand-built test row, or a stored value that isn't valid JSON/lacks
        the key)."""
        fragment_ppm = _DEFAULT_FRAGMENT_PPM_TOLERANCE
        noise_threshold = _DEFAULT_NOISE_THRESHOLD
        if command_id is not None:
            try:
                con = sqlite3.connect(f"file:{analysis_db_path}?mode=ro", uri=True)
                try:
                    row = con.execute(
                        "SELECT arguments FROM commands WHERE id = ?", (command_id,)
                    ).fetchone()
                finally:
                    con.close()
                if row is not None:
                    args = json.loads(row[0])
                    if args.get("fragment_ppm") is not None:
                        fragment_ppm = float(args["fragment_ppm"])
                    if args.get("noise_threshold") is not None:
                        noise_threshold = float(args["noise_threshold"])
            except (sqlite3.OperationalError, ValueError, TypeError, json.JSONDecodeError):
                pass
        return fragment_ppm, noise_threshold
