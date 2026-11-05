"""Tests for core/plotting/stats_plot.py (ADR 68, Visual Inspection "Get statistics")."""

import numpy as np
import pytest

from msianalyzer.core.plotting.stats_plot import (
    OUTSIDE_ROI,
    apply_order,
    build_panels,
    category_counts,
    grid_shape,
    make_group,
    render_stats_figure,
    split_by_roi,
)


def test_split_by_roi_without_rois_keeps_every_pixel():
    values = np.array([1.0, 2.0, 3.0])
    assert [(r, v.tolist()) for r, v in split_by_roi(values, None, False)] == [(None, [1, 2, 3])]


def test_split_by_roi_nested_pixel_counts_in_both_rois_and_outside_is_optional():
    values = np.array([1.0, 2.0, 3.0, 4.0])
    masks = {"A": np.array([1, 1, 0, 0], bool), "B": np.array([0, 1, 1, 0], bool)}
    split = dict(split_by_roi(values, masks, False))
    assert split["A"].tolist() == [1, 2]
    assert split["B"].tolist() == [2, 3]
    assert OUTSIDE_ROI not in split
    split = dict(split_by_roi(values, masks, True))
    assert split[OUTSIDE_ROI].tolist() == [4]


def test_make_group_drops_nan_and_optionally_zeros():
    values = np.array([0.0, np.nan, 2.0, 0.0, 5.0])
    assert make_group("s1", None, values, False)["values"].tolist() == [0, 2, 0, 5]
    assert make_group("s1", None, values, True)["values"].tolist() == [2, 5]


def test_make_group_labels():
    assert make_group("s1", None, np.zeros(1), False)["label"] == "s1"
    assert make_group("s1", "Tumor", np.zeros(1), False)["label"] == "s1 · Tumor"


def test_apply_order_puts_unknown_items_last_in_original_order():
    assert apply_order(["a", "b", "c", "d"], ["c", "x", "a"]) == ["c", "a", "b", "d"]
    assert apply_order(["a", "b"], []) == ["a", "b"]


def _groups():
    return [
        make_group(s, r, np.arange(10.0) + i, False)
        for i, (s, r) in enumerate([("s1", "A"), ("s1", "B"), ("s2", "A"), ("s2", "B")])
    ]


def test_build_panels_ungrouped_one_panel_per_group_in_label_order():
    panels = build_panels(_groups(), False, order=["s2 · B", "s1 · A"])
    assert [p["title"] for p in panels] == ["s2 · B", "s1 · A", "s1 · B", "s2 · A"]
    assert all(len(p["groups"]) == 1 for p in panels)


def test_build_panels_grouped_one_panel_per_roi_samples_on_x():
    panels = build_panels(_groups(), True, roi_order=["B"], sample_order=["s2"])
    assert [p["title"] for p in panels] == ["B", "A"]
    assert [g["sample"] for g in panels[0]["groups"]] == ["s2", "s1"]


def test_grid_shape_grows_rows_when_too_few_cells():
    assert grid_shape(5, 1, 2) == (3, 2)
    assert grid_shape(2, 3, 3) == (3, 3)
    assert grid_shape(0, 1, 1) == (1, 1)


def test_category_counts_and_proportion():
    counts = category_counts(np.array(["a", "b", "a", "a"]), ["a", "b", "c"])
    assert counts.tolist() == [3, 1, 0]


def test_render_violin_figure_shares_y_and_removes_unused_axes():
    panels = build_panels(_groups(), True)
    fig = render_stats_figure(panels, "violin", rows=3, cols=1, width_in=6, height_in=6, ylabel="TIC")
    axes = fig.get_axes()
    assert len(axes) == 2  # third cell removed
    assert axes[0].get_ylim() == axes[1].get_ylim()
    assert [t.get_text() for t in axes[0].get_xticklabels()] == ["s1", "s2"]


def test_render_violin_survives_constant_and_empty_groups():
    groups = [
        make_group("s1", None, np.full(5, 3.0), False),
        make_group("s2", None, np.zeros(0), False),
        make_group("s3", None, np.random.default_rng(0).normal(size=200_000), False),
    ]
    fig = render_stats_figure(build_panels(groups, False), "violin", 1, 3, 6, 3, "x")
    assert fig.get_axes()[1].get_title() == "s2"


def test_render_bars_proportion_stacks_to_one():
    groups = [
        make_group("s1", None, np.array(["a", "a", "b"]), False),
        make_group("s2", None, np.array(["b"]), False),
    ]
    panels = build_panels(groups, False, single_panel=True)
    assert len(panels) == 1
    fig = render_stats_figure(
        panels, "bar", 1, 1, 4, 3, "Pixels",
        categories=["a", "b"], colors=["#ff0000", "#0000ff"], proportion=True,
    )
    ax = fig.get_axes()[0]
    tops = {}
    for patch in ax.patches:
        x = round(patch.get_x() + patch.get_width() / 2)
        tops[x] = max(tops.get(x, 0), patch.get_y() + patch.get_height())
    assert tops == pytest.approx({0: 1.0, 1: 1.0})
    assert fig.legends  # one shared legend
