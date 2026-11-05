"""
stats_plot.py
Visual Inspection's "Get statistics" figure (ADR 68): violins of one
continuous value (a feature on a layer, or a numeric `obs` column) or
stacked bars of one categorical `obs` column, per sample or per
sample × ROI.

Pure functions over plain numpy arrays: the GUI collects the per-pixel
values (`HeatmapImageProvider.collectStatsGroups`), this module splits,
orders, lays out and draws them. Drawn with matplotlib's object API
(`Figure`, no pyplot), so it is safe on a background thread, and the same
`Figure` is both the on-screen PNG and the exported PNG/SVG/PDF.

Style follows the user's QC notebook: quartile lines, no density beyond
the data (seaborn's `cut=0`), rotated x labels, one shared y axis.
"""

from __future__ import annotations

import io
import math

import numpy as np
from matplotlib import cbook
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.patches import Patch

#: The group of pixels inside no ROI, when the user asks for it.
OUTSIDE_ROI = "(no ROI)"
#: Violin colour with no ROI to borrow one from, and for OUTSIDE_ROI.
DEFAULT_COLOR = "#4c72b0"
OUTSIDE_COLOR = "#9e9e9e"
#: The violin's density is estimated on at most this many pixels per group.
# ponytail: random subsample for the KDE only (quartiles/min/max use every
# pixel); raise or replace with a binned KDE if shapes look too coarse.
KDE_MAX_POINTS = 100_000


def split_by_roi(values: np.ndarray, roi_masks: dict | None, include_outside: bool):
    """`[(roi_name | None, values), ...]` for one sample. `roi_masks` None
    means "Use ROI" is off: one group, every pixel. A pixel in two nested
    ROIs counts in both."""
    if roi_masks is None:
        return [(None, values)]
    out = [(name, values[mask]) for name, mask in roi_masks.items()]
    if include_outside:
        inside = np.zeros(len(values), bool)
        for mask in roi_masks.values():
            inside |= mask
        out.append((OUTSIDE_ROI, values[~inside]))
    return out


def make_group(sample: str, roi: str | None, values: np.ndarray, detected_only: bool,
               color: str | None = None) -> dict:
    """One violin/bar. Numeric values lose NaNs (pixels with no value),
    and zeros too when `detected_only`; categorical values are kept as-is."""
    values = np.asarray(values)
    if values.dtype.kind in "iuf":
        values = values[~np.isnan(values)] if values.dtype.kind == "f" else values
        if detected_only:
            values = values[values > 0]
    if color is None:
        color = OUTSIDE_COLOR if roi == OUTSIDE_ROI else DEFAULT_COLOR
    return {
        "sample": sample,
        "roi": roi,
        "label": sample if roi is None else f"{sample} · {roi}",
        "color": color,
        "values": values,
    }


def apply_order(items: list, order: list) -> list:
    """`items` sorted by their position in `order`; items `order` doesn't
    mention keep their original order, after the ones it does."""
    rank = {item: i for i, item in enumerate(order)}
    return sorted(items, key=lambda item: (rank.get(item, len(rank)), items.index(item)))


def _unique(seq) -> list:
    return list(dict.fromkeys(seq))


def build_panels(groups: list[dict], group_by_roi: bool, order=(), roi_order=(),
                 sample_order=(), single_panel: bool = False) -> list[dict]:
    """`[{"title", "groups"}, ...]`, in display order.

    - `group_by_roi`: one panel per ROI (`roi_order`), samples along x
      (`sample_order`).
    - `single_panel`: every group on one axis (`order`).
    - otherwise: one panel per group (`order`).
    """
    if group_by_roi:
        samples = apply_order(_unique(g["sample"] for g in groups), list(sample_order))
        rois = apply_order(_unique(g["roi"] for g in groups), list(roi_order))
        return [
            {
                "title": roi,
                "groups": sorted((g for g in groups if g["roi"] == roi),
                                 key=lambda g: samples.index(g["sample"])),
            }
            for roi in rois
        ]
    labels = apply_order([g["label"] for g in groups], list(order))
    ordered = sorted(groups, key=lambda g: labels.index(g["label"]))
    if single_panel:
        return [{"title": "", "groups": ordered}] if ordered else []
    return [{"title": g["label"], "groups": [g]} for g in ordered]


def grid_shape(n_panels: int, rows: int, cols: int) -> tuple[int, int]:
    """The user's rows × cols, with rows added when there are too few cells."""
    cols = max(1, cols)
    rows = max(1, rows, math.ceil(n_panels / cols))
    return rows, cols


def category_counts(values: np.ndarray, categories: list[str]) -> np.ndarray:
    values = np.asarray(values).astype(str)
    return np.array([np.count_nonzero(values == c) for c in categories])


def _violin_stats(values: np.ndarray) -> dict:
    rng = np.random.default_rng(0)
    kde_values = values
    if len(values) > KDE_MAX_POINTS:
        kde_values = rng.choice(values, KDE_MAX_POINTS, replace=False)
        kde_values = np.append(kde_values, [values.min(), values.max()])  # cut=0 range
    stats = cbook.violin_stats(kde_values, _kde)[0]
    q1, median, q3 = np.quantile(values, [0.25, 0.5, 0.75])
    stats.update(median=median, quantiles=[q1, q3], min=values.min(), max=values.max())
    return stats


def _kde(values, coords):
    from scipy.stats import gaussian_kde  # lazy: GUI startup guard

    return gaussian_kde(values)(coords)


def _draw_violins(ax, groups: list[dict]) -> None:
    for pos, group in enumerate(groups):
        values = group["values"]
        if len(values) == 0:
            continue
        if np.ptp(values) == 0:  # one value: no density to estimate
            ax.hlines(values[0], pos - 0.4, pos + 0.4, color=group["color"], lw=2)
            continue
        parts = ax.violin(
            [_violin_stats(values)], positions=[pos], widths=0.8,
            showmeans=False, showextrema=False, showmedians=True,
        )
        for body in parts["bodies"]:
            body.set_facecolor(group["color"])
            body.set_edgecolor("#333333")
            body.set_alpha(0.85)
        parts["cmedians"].set(color="#222222", linestyle="--", linewidth=1.2)
        parts["cquantiles"].set(color="#222222", linestyle=":", linewidth=1)


def _draw_bars(ax, groups: list[dict], categories, colors, proportion: bool) -> None:
    bottoms = np.zeros(len(groups))
    counts = np.array([category_counts(g["values"], categories) for g in groups], float)
    if proportion:
        totals = counts.sum(axis=1, keepdims=True)
        counts = np.divide(counts, totals, out=np.zeros_like(counts), where=totals > 0)
    for i, (category, color) in enumerate(zip(categories, colors)):
        ax.bar(range(len(groups)), counts[:, i], bottom=bottoms, color=color,
               edgecolor="white", linewidth=0.5, width=0.8, label=category)
        bottoms += counts[:, i]


def render_stats_figure(panels: list[dict], kind: str, rows: int, cols: int,
                        width_in: float, height_in: float, ylabel: str,
                        categories=(), colors=(), proportion: bool = False,
                        dpi: float = 100, share_y: bool = True) -> Figure:
    """The whole grid. `kind` is `"violin"` or `"bar"`. Unused cells are
    removed; panels share the y axis unless `share_y` is False."""
    rows, cols = grid_shape(len(panels), rows, cols)
    fig = Figure(figsize=(width_in, height_in), dpi=dpi, layout="constrained")
    axes = fig.subplots(rows, cols, sharey=share_y, squeeze=False).flatten()
    for i, (ax, panel) in enumerate(zip(axes, panels)):
        groups = panel["groups"]
        if kind == "bar":
            _draw_bars(ax, groups, list(categories), list(colors), proportion)
        else:
            _draw_violins(ax, groups)
        ax.set_title(panel["title"])
        ax.set_xlim(-0.6, len(groups) - 0.4)
        ax.set_xticks(range(len(groups)))
        # One group per panel: its name is already the title.
        labels = [] if len(groups) == 1 and panel["title"] else [
            g["sample"] if panel["title"] == g["roi"] else g["label"] for g in groups
        ]
        ax.set_xticklabels(labels, rotation=90)
        if len(groups) == 1 and panel["title"]:
            ax.set_xticks([])
        if i % cols == 0:
            ax.set_ylabel(ylabel)
        ax.tick_params(axis="y", labelleft=True)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
    for ax in axes[len(panels):]:
        fig.delaxes(ax)
    if kind == "bar" and len(categories):
        handles = [Patch(color=c, label=str(k)) for k, c in zip(categories, colors)]
        fig.legend(handles=handles, loc="outside right upper", frameon=False)
    return fig


def figure_png(fig: Figure) -> bytes:
    buffer = io.BytesIO()
    FigureCanvasAgg(fig).print_png(buffer)
    return buffer.getvalue()
