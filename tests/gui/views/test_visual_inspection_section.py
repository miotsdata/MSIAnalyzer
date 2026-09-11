import sqlite3
from pathlib import Path

import anndata as ad
import numpy as np
import pandas as pd
import pytest
from PySide6.QtCore import Qt
from scipy.sparse import csr_matrix


def _open_visual_tab(view, root, find_visual_child, qtbot):
    nav_button = find_visual_child(root, "navButton_3")
    center = nav_button.mapToScene(nav_button.boundingRect().center()).toPoint()
    qtbot.mouseClick(view, Qt.LeftButton, pos=center)
    qtbot.wait(50)


def _scroll_controls_panel_to(root, find_visual_child, item):
    """Scrolls `controlsFlickable` so `item` (a descendant of
    `controlsPanel`) is on-screen — the panel can hold more controls than
    fit a typical window, same as any other scrollable list in this app;
    a real click needs the target actually visible, not just present in
    the (scrolled-off) content."""
    flickable = find_visual_child(root, "controlsFlickable")
    panel = find_visual_child(root, "controlsPanel")
    y_in_panel = item.mapToItem(panel, 0, 0).y()
    flickable.setProperty("contentY", max(0, y_in_panel - 20))


def test_visual_empty_state_without_features(
    analysis_view, analysis_model, find_visual_child, qtbot
):
    view = analysis_view(analysis_model)
    root = view.rootObject()
    _open_visual_tab(view, root, find_visual_child, qtbot)

    empty_label = find_visual_child(root, "visualEmptyStateLabel")
    assert empty_label is not None
    assert empty_label.property("visible") is True


def test_visual_shows_controls_and_feature_selector(
    analysis_view, visual_analysis_model, find_visual_child, qtbot
):
    view = analysis_view(visual_analysis_model)
    root = view.rootObject()
    _open_visual_tab(view, root, find_visual_child, qtbot)

    empty_label = find_visual_child(root, "visualEmptyStateLabel")
    assert empty_label.property("visible") is False

    feature_combo = find_visual_child(root, "featureCombo")
    assert feature_combo.property("count") == 1

    colormap_combo = find_visual_child(root, "colormapCombo")
    assert colormap_combo is not None

    tile_s1 = find_visual_child(root, "heatmapTile_s1")
    tile_s2 = find_visual_child(root, "heatmapTile_s2")
    assert tile_s1 is not None
    assert tile_s2 is not None


def test_visual_layer_button_click_updates_data_layer(
    analysis_view, visual_analysis_model, find_visual_child, qtbot
):
    view = analysis_view(visual_analysis_model)
    root = view.rootObject()
    _open_visual_tab(view, root, find_visual_child, qtbot)

    section = find_visual_child(root, "visualSection")
    assert section.property("dataLayer") == "TIC"

    raw_button = find_visual_child(root, "rawLayerButton")
    center = raw_button.mapToScene(raw_button.boundingRect().center()).toPoint()
    qtbot.mouseClick(view, Qt.LeftButton, pos=center)
    qtbot.wait(50)

    assert section.property("dataLayer") == "raw"


def test_visual_sample_visibility_toggle_removes_tile(
    analysis_view, visual_analysis_model, find_visual_child, qtbot
):
    view = analysis_view(visual_analysis_model)
    root = view.rootObject()
    _open_visual_tab(view, root, find_visual_child, qtbot)

    assert find_visual_child(root, "heatmapTile_s2") is not None

    checkbox = find_visual_child(root, "sampleVisibility_s2")
    _scroll_controls_panel_to(root, find_visual_child, checkbox)
    center = checkbox.mapToScene(checkbox.boundingRect().center()).toPoint()
    qtbot.mouseClick(view, Qt.LeftButton, pos=center)
    qtbot.wait(50)

    assert find_visual_child(root, "heatmapTile_s2") is None
    assert find_visual_child(root, "heatmapTile_s1") is not None


def _write_sample_h5ad(path, mz=150.0, values=(0.0, 10.0, 5.0, 15.0)):
    obs = pd.DataFrame(index=["a", "b", "c", "d"])
    var = pd.DataFrame({"mz": [mz]}, index=[f"mz_{mz:.4f}"])
    X = csr_matrix(np.array(values, dtype=np.float32).reshape(-1, 1))
    adata = ad.AnnData(X=X, obs=obs, var=var)
    adata.obsm["spatial"] = np.array(
        [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0)], dtype=float
    )
    adata.write_h5ad(path)


def test_visual_heatmap_tile_actually_loads_an_image(
    analysis_view, visual_analysis_model, find_visual_child, qtbot
):
    """End-to-end regression test for the id percent-encoding bug: QML's
    Image element percent-encodes "|" in `source` (not valid raw in a URL
    path) before HeatmapImageProvider.requestImage ever sees the id, which
    broke every single heatmap tile in production despite the provider's
    own unit tests passing (they call requestImage directly with an
    already-decoded id, never exercising the real Image/URL round trip)."""
    out_dir = Path(visual_analysis_model.outDir)
    _write_sample_h5ad(out_dir / "s1.h5ad")
    _write_sample_h5ad(out_dir / "s2.h5ad")

    view = analysis_view(visual_analysis_model)
    root = view.rootObject()
    _open_visual_tab(view, root, find_visual_child, qtbot)

    tile = find_visual_child(root, "heatmapImage_s1")
    assert tile is not None
    image = find_visual_child(tile, "zoomableImageContent")

    # `status` (an enum) isn't readable via .property() from Python — use
    # sourceSize instead: it's the loaded image's natural pixel size, zero
    # in both dimensions on Null/Error, non-zero only once genuinely loaded.
    qtbot.waitUntil(
        lambda: image.property("sourceSize").width() > 0, timeout=2000
    )
    assert image.property("sourceSize").width() == 2
    assert image.property("sourceSize").height() == 2


def test_visual_heatmap_tile_preserves_aspect_ratio_and_is_crisp(
    analysis_view, visual_analysis_model, find_visual_child, qtbot
):
    # Regression test for "heatmaps look out of focus / super zoomed in":
    # the tile used to stretch the image to fill the whole box
    # (fillMode: Stretch, width/height maxed independently against the
    # viewport), distorting non-square rasters and — combined with
    # smooth: true — blurring a small source raster blown up to a much
    # bigger tile. A non-square source (2 x-coords, 4 y-coords) makes a
    # regression to that old stretching behavior visible: it would render
    # at 1:1 instead of 1:2.
    out_dir = Path(visual_analysis_model.outDir)
    obs = pd.DataFrame(index=["a", "b", "c", "d", "e", "f", "g", "h"])
    var = pd.DataFrame({"mz": [150.0]}, index=["mz_150.0000"])
    X = csr_matrix(np.arange(8, dtype=np.float32).reshape(-1, 1))
    adata = ad.AnnData(X=X, obs=obs, var=var)
    adata.obsm["spatial"] = np.array(
        [(x, y) for y in range(4) for x in range(2)], dtype=float
    )
    adata.write_h5ad(out_dir / "s1.h5ad")

    view = analysis_view(visual_analysis_model)
    root = view.rootObject()
    _open_visual_tab(view, root, find_visual_child, qtbot)

    tile = find_visual_child(root, "heatmapImage_s1")
    image = find_visual_child(tile, "zoomableImageContent")
    qtbot.waitUntil(lambda: image.property("sourceSize").width() > 0, timeout=2000)

    assert image.property("sourceSize").width() == 2
    assert image.property("sourceSize").height() == 4
    # Rendered box keeps the source's 1:2 aspect ratio (width == half of
    # height), not squished/stretched to whatever shape the tile is.
    assert image.width() == pytest.approx(image.height() / 2, rel=0.01)
    assert image.property("smooth") is False


def test_visual_grid_is_fixed_to_one_column(
    analysis_view, visual_analysis_model, find_visual_child, qtbot
):
    # Rows/cols picking is disabled for now — one tile per row, fixed
    # size, the surrounding Flickable scrolls for more samples.
    view = analysis_view(visual_analysis_model)
    root = view.rootObject()
    _open_visual_tab(view, root, find_visual_child, qtbot)

    assert find_visual_child(root, "rowsSpinBox") is None
    assert find_visual_child(root, "colsSpinBox") is None

    grid = find_visual_child(root, "heatmapGrid")
    assert grid.property("columns") == 1

    flickable = find_visual_child(root, "heatmapGridFlickable")
    assert flickable is not None


@pytest.fixture
def sortable_features_model(visual_analysis_model):
    """`visual_analysis_model` (feature 1, mz=150.0, unannotated), plus
    two annotated features out of alphabetical order relative to their
    mz: feature 2 (mz=100.0, "Zebra compound") and feature 3 (mz=200.0,
    "Apple compound") — so a name-sort and an mz-sort disagree, and the
    already-existing unannotated feature must land last either way."""
    db_path = visual_analysis_model.analysisDbPath
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO features (feature_id, mz, members_json) "
            "VALUES (2, 100.0, '{\"s1\": 0, \"s2\": 0}')"
        )
        con.execute(
            "INSERT INTO features (feature_id, mz, members_json) "
            "VALUES (3, 200.0, '{\"s1\": 0, \"s2\": 0}')"
        )
        con.execute(
            "INSERT INTO annotation_libraries (id, path, name) "
            "VALUES (1, 'lib.db', 'my_library')"
        )
        for feature_id, scan_id, mz, name in [
            (2, 42, 100.0, "Zebra compound"),
            (3, 43, 200.0, "Apple compound"),
        ]:
            con.execute(
                "INSERT INTO ms2_associations "
                "(sample_id, scan_id, match_key, precursor_mz, "
                "n_features_in_window, rt, n_peaks, polarity) "
                "VALUES (1, ?, ?, ?, 1, 12.3, 5, 'positive')",
                (scan_id, f"k{scan_id}", mz),
            )
            con.execute(
                "INSERT INTO ms2_annotations "
                "(feature_id, sample_id, scan_id, library_id, "
                "library_spectrum_id, compound_name, compound_formula, inchikey, "
                "score, dot_product_score, lib_coverage, emp_coverage, "
                "coverage_score, n_matched_peaks, n_lib_peaks, n_emp_peaks_raw, "
                "n_emp_peaks_filtered, rank_ms2, rank_feature) "
                "VALUES (?, 1, ?, 1, ?, ?, 'C1H1', ?, "
                "0.9, 0.9, 0.8, 0.8, 0.8, 2, 2, 10, 3, 1, 1)",
                (feature_id, scan_id, scan_id, name, f"KEY{scan_id}"),
            )
        con.commit()
    return visual_analysis_model


def test_feature_labels_default_sorted_by_mz(
    analysis_view, sortable_features_model, find_visual_child, qtbot
):
    view = analysis_view(sortable_features_model)
    root = view.rootObject()
    _open_visual_tab(view, root, find_visual_child, qtbot)

    section = find_visual_child(root, "visualSection")
    labels = section.property("featureLabels").toVariant()
    assert labels == [
        "100.0000: Zebra compound",
        "m/z 150.0000",
        "200.0000: Apple compound",
    ]


def test_sort_by_name_puts_annotated_alphabetically_then_unannotated_last(
    analysis_view, sortable_features_model, find_visual_child, qtbot
):
    # Set sortMode directly rather than driving the ComboBox's native
    # popup — this codebase's tests don't simulate clicks inside a
    # ComboBox's popup list for any control (see featureCombo/
    # colormapCombo elsewhere: only ever asserted on, never clicked
    # through). The onActivated -> sortMode wiring itself is a one-line
    # ternary, low risk without its own test.
    view = analysis_view(sortable_features_model)
    root = view.rootObject()
    _open_visual_tab(view, root, find_visual_child, qtbot)

    section = find_visual_child(root, "visualSection")
    section.setProperty("sortMode", "name")

    labels = section.property("featureLabels").toVariant()
    assert labels == [
        "200.0000: Apple compound",
        "100.0000: Zebra compound",
        "m/z 150.0000",
    ]

    sort_combo = find_visual_child(root, "sortModeCombo")
    assert sort_combo.property("currentIndex") == 1


def test_sort_mode_combo_reflects_default_mz_sort(
    analysis_view, sortable_features_model, find_visual_child, qtbot
):
    view = analysis_view(sortable_features_model)
    root = view.rootObject()
    _open_visual_tab(view, root, find_visual_child, qtbot)

    sort_combo = find_visual_child(root, "sortModeCombo")
    assert sort_combo.property("currentIndex") == 0


def _disable_autoscale(view, root, find_visual_child, qtbot):
    autoscale_checkbox = find_visual_child(root, "autoScaleCheckBox")
    center = autoscale_checkbox.mapToScene(
        autoscale_checkbox.boundingRect().center()
    ).toPoint()
    qtbot.mouseClick(view, Qt.LeftButton, pos=center)
    qtbot.wait(50)


def test_moving_vmin_slider_does_not_re_render_until_apply_clicked(
    analysis_view, visual_analysis_model, find_visual_child, qtbot
):
    # Dragging vmin/vmax used to re-render every visible tile on every
    # intermediate tick (each write went straight into vminToken()/
    # vmaxToken(), which every tile's `source` depends on) — draft values
    # are separate from what's actually applied until "Apply" is clicked.
    view = analysis_view(visual_analysis_model)
    root = view.rootObject()
    _open_visual_tab(view, root, find_visual_child, qtbot)
    _disable_autoscale(view, root, find_visual_child, qtbot)

    section = find_visual_child(root, "visualSection")
    original_vmin = section.property("vmin")

    section.setProperty("draftVmin", original_vmin + 500)
    qtbot.wait(50)

    assert section.property("vmin") == original_vmin
    assert section.vminToken() == "auto" or "500" not in section.vminToken()

    apply_button = find_visual_child(root, "applyColorRangeButton")
    assert apply_button.property("enabled") is True
    center = apply_button.mapToScene(apply_button.boundingRect().center()).toPoint()
    qtbot.mouseClick(view, Qt.LeftButton, pos=center)
    qtbot.wait(50)

    assert section.property("vmin") == original_vmin + 500


def test_apply_button_disabled_when_draft_matches_applied(
    analysis_view, visual_analysis_model, find_visual_child, qtbot
):
    view = analysis_view(visual_analysis_model)
    root = view.rootObject()
    _open_visual_tab(view, root, find_visual_child, qtbot)
    _disable_autoscale(view, root, find_visual_child, qtbot)

    apply_button = find_visual_child(root, "applyColorRangeButton")
    assert apply_button.property("enabled") is False

    section = find_visual_child(root, "visualSection")
    section.setProperty("draftVmax", section.property("vmax") + 10)
    qtbot.wait(50)
    assert apply_button.property("enabled") is True

    section.applyColorRange()
    qtbot.wait(50)
    assert apply_button.property("enabled") is False


def test_vmin_vmax_value_labels_show_the_draft_value(
    analysis_view, visual_analysis_model, find_visual_child, qtbot
):
    # Reported as "values are not so clear, because the text input is so
    # short" — the current value is now also shown as its own label,
    # not just squeezed into a narrow TextField.
    view = analysis_view(visual_analysis_model)
    root = view.rootObject()
    _open_visual_tab(view, root, find_visual_child, qtbot)
    _disable_autoscale(view, root, find_visual_child, qtbot)

    section = find_visual_child(root, "visualSection")
    section.setProperty("draftVmin", 1.5)
    section.setProperty("draftVmax", 7.25)
    qtbot.wait(50)

    vmin_label = find_visual_child(root, "vminValueLabel")
    vmax_label = find_visual_child(root, "vmaxValueLabel")
    assert vmin_label.property("text") == "vmin: 1.500"
    assert vmax_label.property("text") == "vmax: 7.250"


def test_vmax_slider_ceiling_does_not_move_while_dragging(
    analysis_view, visual_analysis_model, find_visual_child, qtbot
):
    # Reported as "the vmax slider doesn't work properly" — its `to`
    # ceiling used to be draftVmax * 2, so every drag tick (which updates
    # draftVmax right away) also moved the ceiling further away,
    # effectively making the value impossible to settle on. The ceiling
    # is now anchored to the *applied* vmax, which only changes when
    # "Apply" is clicked — stable through an entire drag.
    view = analysis_view(visual_analysis_model)
    root = view.rootObject()
    _open_visual_tab(view, root, find_visual_child, qtbot)
    _disable_autoscale(view, root, find_visual_child, qtbot)

    section = find_visual_child(root, "visualSection")
    vmax_slider = find_visual_child(root, "vmaxSlider")
    ceiling_before = vmax_slider.property("to")

    section.setProperty("draftVmax", section.property("vmax") * 1.5)
    qtbot.wait(50)

    assert vmax_slider.property("to") == ceiling_before


def test_heatmap_tile_width_is_80_percent_of_flickable_width(
    analysis_view, visual_analysis_model, find_visual_child, qtbot
):
    view = analysis_view(visual_analysis_model)
    root = view.rootObject()
    _open_visual_tab(view, root, find_visual_child, qtbot)

    flickable = find_visual_child(root, "heatmapGridFlickable")
    tile = find_visual_child(root, "heatmapTile_s1")

    assert tile.width() == pytest.approx(flickable.width() * 0.8, rel=0.02)


def test_controls_panel_width_gives_slider_row_breathing_room(
    analysis_view, visual_analysis_model, find_visual_child, qtbot
):
    view = analysis_view(visual_analysis_model)
    root = view.rootObject()
    _open_visual_tab(view, root, find_visual_child, qtbot)

    flickable = find_visual_child(root, "controlsFlickable")
    assert flickable.width() == 300


def test_long_sample_name_is_truncated_with_full_name_in_tooltip(
    analysis_view, project, find_visual_child, qtbot, tmp_path
):
    import sqlite3

    from msianalyzer.gui.models.analysis import AnalysisModel
    from msianalyzer.gui.models.project import ProjectModel
    from msianalyzer.core.analysis_db import init_analysis_db

    long_name = "1_Matrice_2_DAN_25um_msms_neg_sample_acquisition"
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    db_path = out_dir / "analysis_test-run.db"
    init_analysis_db(db_path).close()
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO samples (sample_id, name, raw_db_path, polarity) "
            "VALUES (1, ?, 'a.db', 'positive')",
            (long_name,),
        )
        con.execute(
            "INSERT INTO features (feature_id, mz, members_json) "
            "VALUES (1, 150.0, '{}')"
        )
        con.commit()
    run_dict = {
        "id": "test-run",
        "start_date": "2026-01-01 12:00:00",
        "config": {
            "io": {"out_dir": str(out_dir)},
            "analysis": {"db_name": db_path.name},
        },
    }
    project_model = ProjectModel(project, str(tmp_path))
    model = AnalysisModel(project_model, "test-run", run_dict)

    view = analysis_view(model)
    root = view.rootObject()
    _open_visual_tab(view, root, find_visual_child, qtbot)

    checkbox = find_visual_child(root, "sampleVisibility_" + long_name)
    assert checkbox is not None
    assert checkbox.property("text") != long_name
    assert len(checkbox.property("text")) < len(long_name)
    assert checkbox.property("text").endswith("…")
    assert checkbox.property("fullName") == long_name


def test_global_scale_toggle_was_removed(
    analysis_view, visual_analysis_model, find_visual_child, qtbot
):
    view = analysis_view(visual_analysis_model)
    root = view.rootObject()
    _open_visual_tab(view, root, find_visual_child, qtbot)

    assert find_visual_child(root, "globalScaleCheckBox") is None


def test_vmin_vmax_controls_are_visibly_dimmed_when_autoscale_is_on(
    analysis_view, visual_analysis_model, find_visual_child, qtbot
):
    view = analysis_view(visual_analysis_model)
    root = view.rootObject()
    _open_visual_tab(view, root, find_visual_child, qtbot)

    vmin_label = find_visual_child(root, "vminValueLabel")
    vmin_group = vmin_label.parent()
    assert vmin_group.property("enabled") is False
    assert vmin_group.property("opacity") == pytest.approx(0.4)

    _disable_autoscale(view, root, find_visual_child, qtbot)
    assert vmin_group.property("enabled") is True
    assert vmin_group.property("opacity") == pytest.approx(1.0)


def test_raw_tic_buttons_show_distinct_selected_color(
    analysis_view, visual_analysis_model, find_visual_child, qtbot
):
    view = analysis_view(visual_analysis_model)
    root = view.rootObject()
    _open_visual_tab(view, root, find_visual_child, qtbot)

    section = find_visual_child(root, "visualSection")
    assert section.property("dataLayer") == "TIC"

    raw_button = find_visual_child(root, "rawLayerButton")
    tic_button = find_visual_child(root, "ticLayerButton")
    assert raw_button.property("highlighted") is False
    assert tic_button.property("highlighted") is True

    center = raw_button.mapToScene(raw_button.boundingRect().center()).toPoint()
    qtbot.mouseClick(view, Qt.LeftButton, pos=center)
    qtbot.wait(50)

    assert raw_button.property("highlighted") is True
    assert tic_button.property("highlighted") is False


def test_switching_layer_resets_to_autoscale(
    analysis_view, visual_analysis_model, find_visual_child, qtbot
):
    # Reported: vmin/vmax "remain with the value of tic" after switching
    # to raw — a manually-set TIC-scale range is meaningless for raw
    # data, so the layer switch now falls back to autoscale instead of
    # silently reusing a stale range.
    view = analysis_view(visual_analysis_model)
    root = view.rootObject()
    _open_visual_tab(view, root, find_visual_child, qtbot)
    _disable_autoscale(view, root, find_visual_child, qtbot)

    section = find_visual_child(root, "visualSection")
    assert section.property("autoScale") is False

    raw_button = find_visual_child(root, "rawLayerButton")
    center = raw_button.mapToScene(raw_button.boundingRect().center()).toPoint()
    qtbot.mouseClick(view, Qt.LeftButton, pos=center)
    qtbot.wait(50)

    assert section.property("dataLayer") == "raw"
    assert section.property("autoScale") is True


def test_typing_a_large_vmax_expands_slider_ceiling_immediately(
    analysis_view, visual_analysis_model, find_visual_child, qtbot
):
    # Reported as needing many Apply-doubling round trips ("16... 32...
    # 64...") to reach a value far from the current one — typing directly
    # now expands the slider's range in one step instead.
    view = analysis_view(visual_analysis_model)
    root = view.rootObject()
    _open_visual_tab(view, root, find_visual_child, qtbot)
    _disable_autoscale(view, root, find_visual_child, qtbot)

    vmax_field = find_visual_child(root, "vmaxField")
    vmax_slider = find_visual_child(root, "vmaxSlider")
    ceiling_before = vmax_slider.property("to")

    vmax_field.setProperty("text", "5000")
    vmax_field.editingFinished.emit()
    qtbot.wait(50)

    section = find_visual_child(root, "visualSection")
    assert section.property("draftVmax") == pytest.approx(5000)
    assert vmax_slider.property("to") > ceiling_before
    assert vmax_slider.property("to") >= 5000
