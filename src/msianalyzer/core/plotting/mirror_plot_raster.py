"""
mirror_plot_raster.py
Fast, static (non-interactive) empirical-vs-library mirror plot as PNG bytes.

The "basic" companion to Plotter.plot_ms2_annotation's interactive Plotly
figure — the GUI's inline Annotations panel shows this by default (no
WebEngineView/Chromium renderer needed for it at all) and only builds the
full interactive one on demand, in a popup window. Renders directly via
matplotlib's Agg backend (Figure + FigureCanvasAgg), not pyplot — no
global backend state, safe to call from a background thread or under the
offscreen QPA platform used for tests.
"""

from __future__ import annotations

import io

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from msianalyzer.core.annotation.spectral_match import _align_peaks
from msianalyzer.core.plotting.plotter import _normalize_to_max

_MATCHED_COLOR = "#000000"
_UNMATCHED_COLOR = "#b0b0b0"


def render_mirror_plot_png(
    empirical_mz: np.ndarray,
    empirical_intensity: np.ndarray,
    library_mz: np.ndarray,
    library_intensity: np.ndarray,
    fragment_ppm_tolerance: float,
    figsize: tuple[float, float] = (5.0, 3.0),
    dpi: int = 110,
) -> bytes:
    """Empirical (top) vs library (bottom) stick plot, matched fragments
    black and unmatched gray — same convention and matching logic as
    `Plotter.plot_ms2_annotation`, minus the title/metadata/legend/hover
    (the GUI shows those separately, as plain text — see
    AnnotationsSection.qml's stats panel).

    Args:
        empirical_mz: Empirical spectrum m/z values.
        empirical_intensity: Empirical intensities, parallel to
            `empirical_mz` — normalised to its own max before plotting
            (a no-op if already normalised, e.g. the stored filtered
            spectra).
        library_mz: Library spectrum m/z values.
        library_intensity: Library intensities, parallel to `library_mz`
            — normalised the same way.
        fragment_ppm_tolerance: ppm tolerance for deciding which peaks
            "match" across the two spectra.
        figsize: Matplotlib figure size in inches.
        dpi: Render resolution.

    Returns:
        PNG-encoded image bytes.
    """
    empirical_mz = np.asarray(empirical_mz, dtype=float)
    empirical_intensity = _normalize_to_max(empirical_intensity)
    library_mz = np.asarray(library_mz, dtype=float)
    library_intensity = _normalize_to_max(library_intensity)

    _, emp_matched_mask = _align_peaks(
        library_mz, library_intensity, empirical_mz, empirical_intensity,
        fragment_ppm_tolerance,
    )
    _, lib_matched_mask = _align_peaks(
        empirical_mz, empirical_intensity, library_mz, library_intensity,
        fragment_ppm_tolerance,
    )

    fig = Figure(figsize=figsize, dpi=dpi)
    canvas = FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)

    for mz, intensity, matched, direction in (
        (empirical_mz, empirical_intensity, emp_matched_mask, 1),
        (library_mz, library_intensity, lib_matched_mask, -1),
    ):
        if mz.size == 0:
            continue
        colors = np.where(matched, _MATCHED_COLOR, _UNMATCHED_COLOR)
        ax.vlines(mz, 0, intensity * direction, colors=colors, linewidth=1.4)

    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_xlabel("m/z", fontsize=8)
    ax.set_ylabel("Intensity", fontsize=8)
    ax.set_ylim(-1.15, 1.15)
    ax.tick_params(labelsize=7)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout(pad=0.6)

    buf = io.BytesIO()
    canvas.print_png(buf)
    return buf.getvalue()
