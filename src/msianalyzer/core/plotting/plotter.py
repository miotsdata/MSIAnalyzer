"""
plotter.py
Visualization module for msianalyzer.

Produces interactive Plotly figures that work both as standalone HTML
and embedded in QML via WebEngineView.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Optional

import numpy as np
import plotly.graph_objects as go

from msianalyzer.core.parser.mzml_parser import blob_to_array
from msianalyzer.core.annotation.spectral_match import _align_peaks
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
    path) it needs as an argument. Figures render both as standalone HTML
    and embedded in QML via ``WebEngineView``.
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

        By default both spectra are read straight from the analysis
        database's ``ms2_annotations`` row — the noise-filtered,
        max-normalised arrays stored at annotation time
        (``AnnotateConfig.store_filtered_spectra``, on by default).
        ``emp_source``/``lib_source`` can each independently ask for the
        *raw* spectrum instead: the empirical one from the owning sample's
        raw per-sample database (``ms2_scans``, by ``scan_id``), the
        library one re-read from the library file itself (by
        ``library_spectrum_id``). Whichever pair is shown is re-normalised
        to its own max intensity for display (filtered spectra are already
        max-normalised, so this is a no-op for them) — raw and filtered
        views are visually comparable this way.

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
            ValueError: No such annotation; the requested source has no
                data available for it (filtered spectra were never stored,
                the raw per-sample database or library file can't be
                found, or the scan/spectrum id no longer exists in it); or
                `emp_source`/`lib_source` isn't `"filtered"`/`"raw"`.
        """
        if emp_source not in ("filtered", "raw"):
            raise ValueError(f"emp_source must be 'filtered' or 'raw', got {emp_source!r}")
        if lib_source not in ("filtered", "raw"):
            raise ValueError(f"lib_source must be 'filtered' or 'raw', got {lib_source!r}")

        ann = self._fetch_annotation(analysis_db_path, annotation_id)

        empirical_mz, empirical_int = self._resolve_side(
            ann, side="emp", source=emp_source, annotation_id=annotation_id
        )
        library_mz, library_int = self._resolve_side(
            ann, side="lib", source=lib_source, annotation_id=annotation_id
        )
        empirical_int = _normalize_to_max(empirical_int)
        library_int = _normalize_to_max(library_int)

        if fragment_ppm_tolerance is None:
            fragment_ppm_tolerance = self._resolve_fragment_ppm_tolerance(
                analysis_db_path, ann["command_id"]
            )

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

        source_note = (
            f"  ·  empirical: {emp_source}, library: {lib_source}"
            if (emp_source, lib_source) != ("filtered", "filtered")
            else ""
        )
        auto_title = title or (
            f"{ann['compound_name']}  ({ann['compound_formula']})  ·  "
            f"score={ann['score']:.4f}  "
            f"dp={ann['dot_product_score']:.4f}  "
            f"lib_cov={ann['lib_coverage']:.2f}  "
            f"emp_cov={ann['emp_coverage']:.2f}  ·  "
            f"{ann['n_matched_peaks']}/{ann['n_lib_peaks']} lib peaks  "
            f"{ann['n_emp_peaks_filtered']}/{ann['n_emp_peaks_raw']} emp peaks"
            f"{source_note}"
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
            margin=dict(t=100, b=50, l=60, r=20),
        )

        # Metadata box
        precursor_text = (
            f"precursor m/z {ann['precursor_mz']:.4f}  ·  "
            if ann["precursor_mz"] is not None
            else ""
        )
        fig.add_annotation(
            xref="paper",
            yref="paper",
            x=0.01,
            y=0.97,
            xanchor="left",
            yanchor="top",
            text=(
                f"scan {ann['scan_id']}  ·  "
                f"{precursor_text}"
                f"library: {ann['library_name'] or '?'}  ·  "
                f"InChIKey: {ann['inchikey']}"
            ),
            showarrow=False,
            font=dict(size=10, color="#555555"),
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
        if len(empirical_mz) == 0:
            return

        order = np.argsort(empirical_mz)
        q_mz_sorted = empirical_mz[order]
        q_int_sorted = empirical_int[order]

        xs, ys = [], []
        for lib_m, lib_i in zip(library_mz, library_int):
            tol_da = lib_m * ppm_tolerance * 1e-6
            lo = np.searchsorted(q_mz_sorted, lib_m - tol_da, side="left")
            hi = np.searchsorted(q_mz_sorted, lib_m + tol_da, side="right")
            if lo == hi:
                continue
            best = int(np.argmax(q_int_sorted[lo:hi])) + lo
            emp_i = float(q_int_sorted[best])
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
        `precursor_mz` (from `ms2_associations`). Filtered-spectrum blobs
        are decoded to arrays (empty when `store_filtered_spectra` was off
        — the caller decides whether that's fatal); raw spectra are *not*
        read here — see `_resolve_side`, which only reads them when
        actually requested."""
        con = sqlite3.connect(f"file:{analysis_db_path}?mode=ro", uri=True)
        try:
            row = con.execute(
                """
                SELECT a.sample_id, a.scan_id, a.compound_name,
                       a.compound_formula, a.inchikey, a.score,
                       a.dot_product_score, a.lib_coverage, a.emp_coverage,
                       a.coverage_score, a.n_matched_peaks, a.n_lib_peaks,
                       a.n_emp_peaks_raw, a.n_emp_peaks_filtered,
                       a.emp_filtered_mz, a.emp_filtered_intensity,
                       a.lib_filtered_mz, a.lib_filtered_intensity,
                       a.library_spectrum_id, a.command_id,
                       lib.name AS library_name, lib.path AS library_path,
                       s.raw_db_path AS sample_raw_db_path,
                       assoc.precursor_mz
                FROM ms2_annotations a
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
            "emp_filtered_mz_blob",
            "emp_filtered_intensity_blob",
            "lib_filtered_mz_blob",
            "lib_filtered_intensity_blob",
            "library_spectrum_id",
            "command_id",
            "library_name",
            "library_path",
            "sample_raw_db_path",
            "precursor_mz",
        )
        d = dict(zip(keys, row))

        for arr_key, blob_key in (
            ("emp_filtered_mz", "emp_filtered_mz_blob"),
            ("emp_filtered_intensity", "emp_filtered_intensity_blob"),
            ("lib_filtered_mz", "lib_filtered_mz_blob"),
            ("lib_filtered_intensity", "lib_filtered_intensity_blob"),
        ):
            blob = d.pop(blob_key)
            d[arr_key] = (
                blob_to_array(blob, compressed=True)
                if blob is not None
                else np.array([], dtype=np.float32)
            )
        return d

    def _resolve_side(
        self, ann: dict, side: str, source: str, annotation_id: int
    ) -> tuple[np.ndarray, np.ndarray]:
        """The (mz, intensity) arrays to plot for one side ("emp"/"lib").

        `source="filtered"` returns the arrays already decoded onto `ann`
        by `_fetch_annotation`. `source="raw"` re-reads from the owning
        sample's raw database (empirical) or the library file
        (library) — never touched by `_fetch_annotation` itself, only on
        actual demand, since most calls only ever want the (already
        in-hand) filtered arrays.
        """
        if source == "filtered":
            mz, intensity = ann[f"{side}_filtered_mz"], ann[f"{side}_filtered_intensity"]
            if len(mz) == 0:
                label = "empirical" if side == "emp" else "library"
                raise ValueError(
                    f"annotation id={annotation_id} has no stored filtered "
                    f"{label} spectrum (store_filtered_spectra was off for "
                    "that run)"
                )
            return mz, intensity

        if side == "emp":
            mz, intensity = _read_raw_ms2_scan(ann["sample_raw_db_path"], ann["scan_id"])
            if mz is None:
                raise ValueError(
                    f"annotation id={annotation_id}: raw empirical spectrum "
                    f"not found (sample raw database "
                    f"{ann['sample_raw_db_path']!r}, scan {ann['scan_id']})"
                )
            return mz, intensity

        mz, intensity = _read_raw_library_spectrum(
            ann["library_path"], ann["library_spectrum_id"]
        )
        if mz is None:
            raise ValueError(
                f"annotation id={annotation_id}: raw library spectrum not "
                f"found (library {ann['library_path']!r}, spectrum id "
                f"{ann['library_spectrum_id']})"
            )
        return mz, intensity

    @staticmethod
    def _resolve_fragment_ppm_tolerance(analysis_db_path, command_id: int | None) -> float:
        """The `fragment_ppm` the `annotate_ms2` run that produced this row
        actually used, from that command's stored `arguments` JSON — falls
        back to the config default when `command_id` is unset or the
        lookup fails (an older database, a hand-built test row, or a
        stored value that isn't valid JSON/lacks the key)."""
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
                    value = json.loads(row[0]).get("fragment_ppm")
                    if value is not None:
                        return float(value)
            except (sqlite3.OperationalError, ValueError, TypeError, json.JSONDecodeError):
                pass
        return _DEFAULT_FRAGMENT_PPM_TOLERANCE
