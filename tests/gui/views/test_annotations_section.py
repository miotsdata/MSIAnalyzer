import sqlite3

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtQuick import QQuickItem

from msianalyzer.core.parser.mzml_parser import array_to_blob


def _open_annotations_tab(view, root, find_visual_child, qtbot):
    nav_button = find_visual_child(root, "navButton_2")
    center = nav_button.mapToScene(nav_button.boundingRect().center()).toPoint()
    qtbot.mouseClick(view, Qt.LeftButton, pos=center)
    qtbot.wait(50)


def _click(view, item, qtbot):
    center = item.mapToScene(item.boundingRect().center()).toPoint()
    qtbot.mouseClick(view, Qt.LeftButton, pos=center)
    qtbot.wait(50)


def _seed_second_feature(db_path, *, feature_id=9, mz=50.0, compound="Water"):
    """A second, lower-mz annotated feature — for the sort tests, so
    default (feature_id asc) and mz-sorted order actually differ."""
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO features (feature_id, mz, members_json) VALUES (?, ?, '{}')",
            (feature_id, mz),
        )
        blob = array_to_blob(np.array([50.0, 60.0], dtype=np.float32))
        con.execute(
            "INSERT INTO ms2_annotations "
            "(id, feature_id, sample_id, scan_id, library_id, "
            "library_spectrum_id, compound_name, compound_formula, inchikey, "
            "score, dot_product_score, lib_coverage, emp_coverage, "
            "coverage_score, n_matched_peaks, n_lib_peaks, n_emp_peaks_raw, "
            "n_emp_peaks_filtered, emp_filtered_mz, emp_filtered_intensity, "
            "lib_filtered_mz, lib_filtered_intensity, rank_ms2, rank_feature) "
            "VALUES (2, ?, 1, 43, 1, 11, ?, 'H2O', "
            "'XLYOFNOQVPJJNP-UHFFFAOYSA-N', 0.5, 0.5, 0.5, 0.5, 0.5, "
            "1, 1, 5, 2, ?, ?, ?, ?, 1, 1)",
            (feature_id, compound, blob, blob, blob, blob),
        )
        con.commit()


def test_annotations_empty_state_for_db_without_annotations(
    analysis_view, analysis_model, find_visual_child, qtbot
):
    view = analysis_view(analysis_model)
    root = view.rootObject()
    _open_annotations_tab(view, root, find_visual_child, qtbot)

    empty_label = find_visual_child(root, "annotationsEmptyStateLabel")
    assert empty_label is not None
    assert empty_label.property("visible") is True


def test_annotations_table_shows_one_row_per_feature(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    _open_annotations_tab(view, root, find_visual_child, qtbot)

    row = find_visual_child(root, "annotationRow_7")
    assert row is not None

    compound_text = find_visual_child(root, "annotationRowCompound_7")
    mz_text = find_visual_child(root, "annotationRowMz_7")
    score_text = find_visual_child(root, "annotationRowScore_7")
    assert compound_text.property("text") == "Caffeine"
    assert mz_text.property("text") == "123.4567"
    assert score_text.property("text") == "0.870"


def test_clicking_column_header_sorts_table(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    # "User can decide to sort for any of these [feature id/number, name,
    # mz]" — clicking a header sorts by it; clicking the same header again
    # flips direction.
    _seed_second_feature(annotated_analysis_model.analysisDbPath)  # mz 50.0 < feature 7's 123.4567

    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    _open_annotations_tab(view, root, find_visual_child, qtbot)

    section = find_visual_child(root, "annotationsSection")
    assert section.property("sortColumn") == "feature_id"
    assert section.property("sortAscending") is True

    row_7 = find_visual_child(root, "annotationRow_7")
    row_9 = find_visual_child(root, "annotationRow_9")
    # Default sort (feature_id asc): 7 before 9.
    assert row_7.property("y") < row_9.property("y")

    mz_header = find_visual_child(root, "annotationSortHeader_mz")
    _click(view, mz_header, qtbot)

    assert section.property("sortColumn") == "mz"
    assert section.property("sortAscending") is True
    row_7 = find_visual_child(root, "annotationRow_7")
    row_9 = find_visual_child(root, "annotationRow_9")
    # mz asc: feature 9 (mz 50.0) before feature 7 (mz 123.4567).
    assert row_9.property("y") < row_7.property("y")

    _click(view, mz_header, qtbot)
    assert section.property("sortAscending") is False
    row_7 = find_visual_child(root, "annotationRow_7")
    row_9 = find_visual_child(root, "annotationRow_9")
    assert row_7.property("y") < row_9.property("y")


def test_sorting_does_not_change_current_selection(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    _seed_second_feature(annotated_analysis_model.analysisDbPath)

    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    _open_annotations_tab(view, root, find_visual_child, qtbot)

    _click(view, find_visual_child(root, "annotationRow_7"), qtbot)
    section = find_visual_child(root, "annotationsSection")
    assert section.property("selectedFeatureId") == 7

    _click(view, find_visual_child(root, "annotationSortHeader_mz"), qtbot)
    assert section.property("selectedFeatureId") == 7


def test_selecting_feature_shows_top_hits_with_sample_name(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    _open_annotations_tab(view, root, find_visual_child, qtbot)

    _click(view, find_visual_child(root, "annotationRow_7"), qtbot)

    hit_label = find_visual_child(root, "topHitLabel_1")
    assert hit_label is not None
    text = hit_label.property("text")
    assert "Caffeine" in text
    assert "s1" in text
    assert "my_library" in text


def test_selecting_feature_auto_selects_and_loads_first_hit(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    # "the second, the classic plot of matching spectra" — should already
    # be showing something once a feature (with hits) is picked, not
    # require a further click on the one-and-only hit.
    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    _open_annotations_tab(view, root, find_visual_child, qtbot)

    _click(view, find_visual_child(root, "annotationRow_7"), qtbot)

    section = find_visual_child(root, "annotationsSection")
    selected = section.property("selectedHit")
    assert selected is not None
    assert selected["id"] == 1

    mirror_view = find_visual_child(root, "mirrorPlotView")
    assert mirror_view.property("url").toString() != ""
    assert mirror_view.property("url").toString().startswith("file://")


def test_top_hits_panel_is_40_percent_of_detail_panel_height(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    # "Top hit should be 40% of the available height for the column"
    # — was a fixed 190px, which dominated a short column regardless of
    # how many hits there actually were (reported bug: "top hits take
    # almost all the column, even with one hit only").
    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    _open_annotations_tab(view, root, find_visual_child, qtbot)
    _click(view, find_visual_child(root, "annotationRow_7"), qtbot)

    detail_panel = find_visual_child(root, "annotationsDetailPanel")
    top_hits_panel = find_visual_child(root, "topHitsPanel")

    detail_height = detail_panel.property("height")
    top_hits_height = top_hits_panel.property("height")
    assert detail_height > 0
    assert abs(top_hits_height - detail_height * 0.4) <= 1.0


def test_mirror_plot_panel_fills_remaining_height_and_does_not_scroll(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    _open_annotations_tab(view, root, find_visual_child, qtbot)
    _click(view, find_visual_child(root, "annotationRow_7"), qtbot)

    detail_panel = find_visual_child(root, "annotationsDetailPanel")
    mirror_plot_panel = find_visual_child(root, "mirrorPlotPanel")

    # ~60% of detailPanel's height (the remainder after topHitsPanel's
    # explicit 40% and a thin divider) — not a scrolling Flickable, just
    # a plain fillHeight ColumnLayout so the WebEngineView itself resizes.
    assert mirror_plot_panel.property("height") > detail_panel.property("height") * 0.5


def test_toggling_empirical_source_reloads_mirror_plot(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    # Set `empSource` directly rather than driving the ComboBox's native
    # popup — this codebase's tests don't simulate clicks inside a
    # ComboBox's popup list for any control (see test_visual_inspection_
    # section.py's sortModeCombo test); the onActivated -> empSource
    # wiring itself is a one-line assignment, low risk without its own
    # test.
    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    _open_annotations_tab(view, root, find_visual_child, qtbot)
    _click(view, find_visual_child(root, "annotationRow_7"), qtbot)

    section = find_visual_child(root, "annotationsSection")
    mirror_view = find_visual_child(root, "mirrorPlotView")
    initial_url = mirror_view.property("url").toString()

    section.setProperty("empSource", "raw")
    qtbot.wait(50)

    assert mirror_view.property("url").toString() != initial_url
    assert mirror_view.property("url").toString().startswith("file://")

    emp_combo = find_visual_child(root, "empSourceCombo")
    assert emp_combo is not None


def test_no_feature_selected_shows_placeholder(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    _open_annotations_tab(view, root, find_visual_child, qtbot)

    placeholder = find_visual_child(root, "annotationsDetailEmptyLabel")
    assert placeholder is not None
    assert placeholder.property("visible") is True
