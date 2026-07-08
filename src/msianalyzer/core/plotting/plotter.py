"""
plotter.py
Visualization module for msianalyzer.

Produces interactive Plotly figures that work both as standalone HTML
and embedded in QML via WebEngineView — no code changes needed between
the two contexts.

Classes
-------
Plotter
    Stateless helper that builds figures from database paths.
    Each method returns a ``plotly.graph_objects.Figure`` so the caller
    decides how to display it (.show(), .write_html(), or pass to QML).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

import numpy as np
import plotly.graph_objects as go

from msianalyzer.core.mzml_parser import blob_to_array
from msianalyzer.core.spectral_matching import _align_peaks


# ---------------------------------------------------------------------------
# Colour palette (consistent across all plots)
# ---------------------------------------------------------------------------

_BLUE_MATCHED   = "#1f77b4"   # empirical, matched peak
_BLUE_UNMATCHED = "#aec7e8"   # empirical, unmatched peak
_RED_MATCHED    = "#d62728"   # library, matched peak
_RED_UNMATCHED  = "#f7b6b6"   # library, unmatched peak
_CONNECTOR      = "#888888"   # dashed line linking matched peak pairs


class Plotter:
    """
    Builds Plotly figures from msianalyzer database outputs.

    Parameters
    ----------
    annotations_db_path : Path | str
        Path to the annotations SQLite database (from ms2_annotator).
    ms2_db_path : Path | str
        Path to the MS2 SQLite database (from MzmlParser).
    fragment_ppm_tolerance : float
        PPM tolerance used to decide which peaks are "matched" for
        highlighting. Should match what was used during annotation.
    """

    def __init__(
        self,
        annotations_db_path: Path | str,
        ms2_db_path: Path | str,
        fragment_ppm_tolerance: float = 10.0,
    ) -> None:
        self.annotations_db_path = Path(annotations_db_path)
        self.ms2_db_path = Path(ms2_db_path)
        self.fragment_ppm_tolerance = fragment_ppm_tolerance

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def plot_ms2_annotation(
        self,
        annotation_id: int,
        title: Optional[str] = None,
        height: int = 500,
    ) -> go.Figure:
        """
        Mirror plot comparing an empirical MS2 spectrum (top, blue)
        against its best library match (bottom, red).

        Matched peaks (within fragment_ppm_tolerance) are shown in full
        colour; unmatched peaks are shown faded. Dashed vertical lines
        connect matched peak pairs across the mirror axis.

        Parameters
        ----------
        annotation_id : int
            Primary key in the ``annotations`` table.
        title : str | None
            Plot title. Auto-generated from compound name if None.
        height : int
            Figure height in pixels.

        Returns
        -------
        plotly.graph_objects.Figure
        """
        ann  = self._fetch_annotation(annotation_id)
        scan = self._fetch_ms2_scan(ann["scan_id"])
        lib  = self._fetch_library_spectrum(ann)

        empirical_mz  = scan["mz"]
        empirical_int = scan["intensity"]
        library_mz    = lib["mz"]
        library_int   = lib["intensity"]

        # Normalise both to [0, 1] so the mirror is visually balanced
        empirical_int = _normalise(empirical_int)
        library_int   = _normalise(library_int)

        # Identify matched peaks.
        # _align_peaks(query, library) returns a mask sized to LIBRARY.
        # Swap roles so each mask is sized to its own spectrum:
        #   emp_matched_mask[i] = True if empirical peak i matches any library peak
        #   lib_matched_mask[i] = True if library  peak i matches any empirical peak
        _, emp_matched_mask = _align_peaks(
            library_mz, library_int,       # query  = library
            empirical_mz, empirical_int,   # target = empirical → mask sized to empirical
            self.fragment_ppm_tolerance,
        )
        _, lib_matched_mask = _align_peaks(
            empirical_mz, empirical_int,   # query  = empirical
            library_mz, library_int,       # target = library   → mask sized to library
            self.fragment_ppm_tolerance,
        )

        fig = go.Figure()

        # -- Empirical bars (top, positive) --
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

        # -- Library bars (bottom, negative) --
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

        # -- Connector lines for matched peak pairs --
        self._add_connectors(
            fig,
            empirical_mz=empirical_mz,
            empirical_int=empirical_int,
            library_mz=library_mz,
            library_int=library_int,
            ppm_tolerance=self.fragment_ppm_tolerance,
        )

        # -- Zero line --
        fig.add_hline(y=0, line_width=1, line_color="black")

        # -- Layout --
        auto_title = (
            title or (
                f"{ann['compound_name']}  "
                f"({ann['compound_formula']})  "
                f"·  score = {ann['score']:.4f}  "
                f"·  {ann['n_matched_peaks']}/{ann['n_library_peaks']} peaks matched"
            )
        )

        fig.update_layout(
            title=dict(text=auto_title, font=dict(size=13)),
            height=height,
            xaxis=dict(
                title="m/z",
                showgrid=True,
                gridcolor="#eeeeee",
                zeroline=False,
            ),
            yaxis=dict(
                title="Normalised intensity",
                tickformat=".1f",
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
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="right",
                x=1,
            ),
            hovermode="x unified",
            margin=dict(t=80, b=50, l=60, r=20),
        )

        # Annotation box with metadata
        fig.add_annotation(
            xref="paper", yref="paper",
            x=0.01, y=0.97,
            xanchor="left", yanchor="top",
            text=(
                f"scan {ann['scan_id']}  ·  "
                f"precursor m/z {scan['precursor_mz']:.4f}  ·  "
                f"library: {Path(ann['library_path']).stem}  ·  "
                f"InChIKey: {ann['inchikey']}"
            ),
            showarrow=False,
            font=dict(size=10, color="#555555"),
            bgcolor="rgba(255,255,255,0.7)",
        )

        return fig

    # ------------------------------------------------------------------
    # Private helpers — figure building
    # ------------------------------------------------------------------

    @staticmethod
    def _add_bars(
        fig: go.Figure,
        mz: np.ndarray,
        intensity: np.ndarray,
        matched_mask: np.ndarray,
        direction: int,           # +1 = top (empirical), -1 = bottom (library)
        color_matched: str,
        color_unmatched: str,
        legend_matched: str,
        legend_unmatched: str,
    ) -> None:
        """Add two Bar traces per spectrum: matched and unmatched peaks."""

        for mask, color, name, show in (
            (matched_mask,  color_matched,   legend_matched,   True),
            (~matched_mask, color_unmatched, legend_unmatched, True),
        ):
            if not mask.any():
                continue

            mz_sel  = mz[mask]
            int_sel = intensity[mask] * direction

            # Build one scatter trace per peak using lines (vertical bars)
            # using a single Scatter trace with None separators — much more
            # efficient than one trace per peak and supports unified hover.
            xs, ys = [], []
            for m, i in zip(mz_sel, int_sel):
                xs += [m, m, None]
                ys += [0, i, None]

            fig.add_trace(go.Scatter(
                x=xs,
                y=ys,
                mode="lines",
                name=name,
                line=dict(color=color, width=2),
                legendgroup=name,
                showlegend=show,
                hoverinfo="skip",
            ))

            # Invisible markers for clean hover tooltips
            fig.add_trace(go.Scatter(
                x=mz_sel,
                y=int_sel,
                mode="markers",
                marker=dict(color=color, size=6, symbol="circle"),
                name=name,
                legendgroup=name,
                showlegend=False,
                customdata=np.abs(int_sel).reshape(-1, 1),
                hovertemplate=(
                    "<b>m/z</b>: %{x:.4f}<br>"
                    "<b>intensity</b>: %{customdata[0]:.3f}<extra></extra>"
                ),
            ))

    @staticmethod
    def _add_connectors(
        fig: go.Figure,
        empirical_mz: np.ndarray,
        empirical_int: np.ndarray,
        library_mz: np.ndarray,
        library_int: np.ndarray,
        ppm_tolerance: float,
    ) -> None:
        """
        Draw a dashed vertical line connecting each matched peak pair
        (empirical top ↔ library bottom) at the library peak's m/z.
        """
        order = np.argsort(empirical_mz)
        q_mz_sorted  = empirical_mz[order]
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
            fig.add_trace(go.Scatter(
                x=xs,
                y=ys,
                mode="lines",
                line=dict(color=_CONNECTOR, width=1, dash="dot"),
                name="Matched pair",
                showlegend=True,
                hoverinfo="skip",
            ))

    # ------------------------------------------------------------------
    # Private helpers — database access
    # ------------------------------------------------------------------

    def _fetch_annotation(self, annotation_id: int) -> dict:
        con = sqlite3.connect(
            f"file:{self.annotations_db_path}?mode=ro", uri=True
        )
        try:
            row = con.execute(
                """
                SELECT scan_id, library_path, library_spectrum_id,
                       compound_id, compound_name, compound_formula,
                       inchikey, score, n_matched_peaks, n_library_peaks,
                       matched_fraction, rank, rank_group
                FROM annotations
                WHERE id = ?
                """,
                (annotation_id,),
            ).fetchone()
        finally:
            con.close()

        if row is None:
            raise ValueError(f"No annotation found with id={annotation_id}")

        keys = (
            "scan_id", "library_path", "library_spectrum_id",
            "compound_id", "compound_name", "compound_formula",
            "inchikey", "score", "n_matched_peaks", "n_library_peaks",
            "matched_fraction", "rank", "rank_group",
        )
        return dict(zip(keys, row))

    def _fetch_ms2_scan(self, scan_id: int) -> dict:
        con = sqlite3.connect(
            f"file:{self.ms2_db_path}?mode=ro", uri=True
        )
        try:
            row = con.execute(
                """
                SELECT mz_array, intensity_array, precursor_mz
                FROM ms2_scans
                WHERE scan_id = ?
                """,
                (scan_id,),
            ).fetchone()
        finally:
            con.close()

        if row is None:
            raise ValueError(f"No MS2 scan found with scan_id={scan_id}")

        mz_blob, int_blob, precursor_mz = row
        return {
            "mz":          blob_to_array(mz_blob),
            "intensity":   blob_to_array(int_blob),
            "precursor_mz": precursor_mz,
        }

    def _fetch_library_spectrum(self, ann: dict) -> dict:
        """
        Load the library spectrum arrays from the libviz .db file.
        Uses a direct sqlite3 query — avoids loading the full Library
        object just for one spectrum, keeping this display-only code
        dependency-light.
        """
        lib_path = Path(ann["library_path"])
        spectrum_id = ann["library_spectrum_id"]

        con = sqlite3.connect(f"file:{lib_path}?mode=ro", uri=True)
        try:
            row = con.execute(
                "SELECT mz_array, intensity_array FROM spectrum WHERE id = ?",
                (spectrum_id,),
            ).fetchone()
        finally:
            con.close()

        if row is None:
            raise ValueError(
                f"No spectrum found with id={spectrum_id} in {lib_path}"
            )

        mz_blob, int_blob = row
        return {
            "mz":        blob_to_array(mz_blob, compressed=False),
            "intensity": blob_to_array(int_blob, compressed=False),
        }


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _normalise(arr: np.ndarray) -> np.ndarray:
    """Normalise an intensity array to [0, 1]. Returns zeros if max is 0."""
    m = arr.max()
    if m == 0:
        return arr.copy()
    return arr / m
