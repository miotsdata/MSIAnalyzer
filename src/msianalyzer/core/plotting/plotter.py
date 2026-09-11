"""
plotter.py
Visualization module for msianalyzer.

Produces interactive Plotly figures that work both as standalone HTML
and embedded in QML via WebEngineView.
"""

from __future__ import annotations

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
_BLUE_UNMATCHED = "#aec7e8"
_RED_MATCHED = "#d62728"
_RED_UNMATCHED = "#f7b6b6"
_CONNECTOR = "#888888"


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
        color: str = "black",
        height: int = 500,
    ) -> go.Figure:
        """Plot a single spectrum as a centroid (stick) figure.

        Draws one vertical line per peak with invisible markers carrying
        hover tooltips for m/z and intensity.

        Args:
            mz_array: Peak m/z values.
            intensity_array: Peak intensities, parallel to `mz_array`.
            color: Line and marker colour. Defaults to `"black"`.
            height: Figure height in pixels. Defaults to 500.

        Returns:
            A Plotly `Figure` containing the spectrum.
        """

        fig = go.Figure()
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
                showlegend=True,
                hoverinfo="skip",
            )
        )
        # Ghost markers for hover tooltips
        fig.add_trace(
            go.Scatter(
                x=mz_array,
                y=intensity_array,
                mode="markers",
                marker=dict(color=color, size=6),
                customdata=np.abs(intensity_array).reshape(-1, 1),
                hovertemplate=(
                    "<b>m/z</b>: %{x:.4f}<br>"
                    "<b>intensity</b>: %{customdata[0]:.3f}"
                    "<extra></extra>"
                ),
            )
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

    @log_call
    def plot_ms2_annotation(
        self,
        analysis_db_path: Path | str,
        annotation_id: int,
        title: Optional[str] = None,
        fragment_ppm_tolerance: float = 10.0,
        height: int = 500,
    ) -> go.Figure:
        """
        Mirror plot: empirical MS2 (top, blue) vs library match (bottom, red).

        Both spectra are read straight from the analysis database's
        ``ms2_annotations`` row — the noise-filtered, max-normalised arrays
        stored at annotation time (``AnnotateConfig.store_filtered_spectra``,
        on by default). No raw per-sample database or external library file
        is touched.

        Matched peaks are shown in full colour; unmatched peaks are faded.
        Dashed grey lines connect matched peak pairs across the mirror axis.

        Args:
            analysis_db_path: The analysis database.
            annotation_id: Primary key in ``ms2_annotations``.
            title: Plot title. Auto-generated from annotation metadata if
                None.
            fragment_ppm_tolerance: ppm tolerance used only to decide which
                peaks "match" for colouring/connector purposes — the stored
                `score` / `n_matched_peaks` already reflect the run's own
                annotation-time tolerance, this doesn't recompute them.
            height: Figure height in pixels.

        Returns:
            plotly.graph_objects.Figure

        Raises:
            ValueError: No such annotation, or it was stored without
                filtered spectra (`store_filtered_spectra` was off for that
                run).
        """
        ann = self._fetch_annotation(analysis_db_path, annotation_id)

        empirical_mz = ann["emp_filtered_mz"]
        empirical_int = ann["emp_filtered_intensity"]  # already normalised [0,1]
        library_mz = ann["lib_filtered_mz"]
        library_int = ann["lib_filtered_intensity"]  # already normalised [0,1]

        if len(empirical_mz) == 0 or len(library_mz) == 0:
            raise ValueError(
                f"annotation id={annotation_id} has no stored filtered "
                "spectra (store_filtered_spectra was off for that run)"
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
            color_matched=_BLUE_MATCHED,
            color_unmatched=_BLUE_UNMATCHED,
            legend_matched="Empirical (matched)",
            legend_unmatched="Empirical (unmatched)",
        )
        self._add_bars(
            fig,
            mz=library_mz,
            intensity=library_int,
            matched_mask=lib_matched_mask,
            direction=-1,
            color_matched=_RED_MATCHED,
            color_unmatched=_RED_UNMATCHED,
            legend_matched="Library (matched)",
            legend_unmatched="Library (unmatched)",
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

        auto_title = title or (
            f"{ann['compound_name']}  ({ann['compound_formula']})  ·  "
            f"score={ann['score']:.4f}  "
            f"dp={ann['dot_product_score']:.4f}  "
            f"lib_cov={ann['lib_coverage']:.2f}  "
            f"emp_cov={ann['emp_coverage']:.2f}  ·  "
            f"{ann['n_matched_peaks']}/{ann['n_lib_peaks']} lib peaks  "
            f"{ann['n_emp_peaks_filtered']}/{ann['n_emp_peaks_raw']} emp peaks"
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
        color_matched: str,
        color_unmatched: str,
        legend_matched: str,
        legend_unmatched: str,
    ) -> None:
        for mask, color, name in (
            (matched_mask, color_matched, legend_matched),
            (~matched_mask, color_unmatched, legend_unmatched),
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
                    showlegend=True,
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
        """One `ms2_annotations` row, joined with its library name and the
        scan's `precursor_mz` (from `ms2_associations`), filtered-spectrum
        blobs decoded to arrays (empty when `store_filtered_spectra` was
        off — the caller decides whether that's fatal)."""
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
                       lib.name AS library_name,
                       assoc.precursor_mz
                FROM ms2_annotations a
                LEFT JOIN annotation_libraries lib ON lib.id = a.library_id
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
            "library_name",
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
