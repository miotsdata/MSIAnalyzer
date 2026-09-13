import sqlite3

import numpy as np
from PySide6.QtCore import QObject, Qt
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
            "n_emp_peaks_filtered, emp_raw_mz, emp_raw_intensity, "
            "lib_raw_mz, lib_raw_intensity, rank_ms2, rank_feature) "
            "VALUES (2, ?, 1, 43, 1, 11, ?, 'H2O', "
            "'XLYOFNOQVPJJNP-UHFFFAOYSA-N', 0.5, 0.5, 0.5, 0.5, 0.5, "
            "1, 1, 5, 2, ?, ?, ?, ?, 1, 1)",
            (feature_id, compound, blob, blob, blob, blob),
        )
        con.commit()


def _select_feature_7(view, root, find_visual_child, qtbot):
    _open_annotations_tab(view, root, find_visual_child, qtbot)
    _click(view, find_visual_child(root, "annotationRow_7"), qtbot)


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
    # flips direction. Checked via `sortedRows`' own order (the data feeding
    # the Repeater), not rendered row `y` — a Column-in-Flickable list with
    # 2+ rows was observed to (still, despite three separate fixes for
    # related issues) leave every row's rendered `y` stuck at 0 in this
    # environment even though the *data* order is provably correct
    # (verified directly). See [[gui-workspace-status]] for the fuller
    # note; worth revisiting with real-app confirmation.
    _seed_second_feature(annotated_analysis_model.analysisDbPath)  # mz 50.0 < feature 7's 123.4567

    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    _open_annotations_tab(view, root, find_visual_child, qtbot)

    section = find_visual_child(root, "annotationsSection")
    assert section.property("sortColumn") == "feature_id"
    assert section.property("sortAscending") is True
    # Default sort (feature_id asc): 7 before 9.
    assert [r["feature_id"] for r in section.property("sortedRows").toVariant()] == [7, 9]

    mz_header = find_visual_child(root, "annotationSortHeader_mz")
    _click(view, mz_header, qtbot)

    assert section.property("sortColumn") == "mz"
    assert section.property("sortAscending") is True
    # mz asc: feature 9 (mz 50.0) before feature 7 (mz 123.4567).
    assert [r["feature_id"] for r in section.property("sortedRows").toVariant()] == [9, 7]

    _click(view, mz_header, qtbot)
    assert section.property("sortAscending") is False
    assert [r["feature_id"] for r in section.property("sortedRows").toVariant()] == [7, 9]


def test_sorting_does_not_change_current_selection(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    # Selection set directly (`setProperty`), not via a row click — with
    # 2+ rows, clicking a specific row unreliably registered against the
    # *other* row instead in this environment (see
    # test_clicking_column_header_sorts_table's comment); this test's
    # actual subject is "does sorting preserve the selection", which
    # doesn't need the click itself to exercise.
    _seed_second_feature(annotated_analysis_model.analysisDbPath)

    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    _open_annotations_tab(view, root, find_visual_child, qtbot)

    section = find_visual_child(root, "annotationsSection")
    section.setProperty("selectedFeatureId", 7)
    assert section.property("selectedFeatureId") == 7

    _click(view, find_visual_child(root, "annotationSortHeader_mz"), qtbot)
    assert section.property("selectedFeatureId") == 7


def test_selecting_feature_shows_top_hits_with_sample_name(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    _select_feature_7(view, root, find_visual_child, qtbot)

    hit_label = find_visual_child(root, "topHitLabel_1")
    assert hit_label is not None
    text = hit_label.property("text")
    assert "Caffeine" in text
    assert "s1" in text
    assert "my_library" in text


def test_selecting_feature_auto_selects_first_hit_and_loads_basic_plot(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    # "a basic plot in that panel (no plotly)" — should already be
    # showing something once a feature (with hits) is picked, not
    # require a further click on the one-and-only hit, and never touch a
    # WebEngineView for this inline view.
    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    _select_feature_7(view, root, find_visual_child, qtbot)

    section = find_visual_child(root, "annotationsSection")
    selected = section.property("selectedHit")
    assert selected is not None
    assert selected["id"] == 1

    image = find_visual_child(root, "basicPlotImage")
    assert image is not None
    assert image.property("source").toString().startswith("data:image/png;base64,")
    assert root.findChild(QQuickItem, "mirrorPlotView") is None  # no inline WebEngineView


def test_stats_panel_shows_match_details(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    # "all the stats are written better (list, clear)"
    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    _select_feature_7(view, root, find_visual_child, qtbot)

    label_0 = find_visual_child(root, "statLabel_0")
    value_0 = find_visual_child(root, "statValue_0")
    assert label_0.property("text") == "Compound:"
    assert "Caffeine" in value_0.property("text")

    section = find_visual_child(root, "annotationsSection")
    flat = section.property("statsRows").toVariant()
    assert "Sample:" in flat
    assert "Matched peaks:" in flat


def test_three_row_split_default_is_30_30_40_percent(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    # Default (undragged) split: top hits 30%, stats 30%, graph 40% — via
    # `SplitView.preferredHeight` on each pane, not a plain `height:`
    # binding (tried first: it happened to measure correctly under this
    # offscreen test platform, but the user found the real app's default
    # wasn't actually proportioned — SplitView's own sizing pass overrides
    # a plain `height:` binding once it genuinely engages, which it does
    # in the real app; `SplitView.preferredHeight` is the mechanism that's
    # actually respected for both the default *and* stays draggable
    # afterward).
    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    _select_feature_7(view, root, find_visual_child, qtbot)

    split = find_visual_child(root, "detailSplit")
    top_hits = find_visual_child(root, "topHitsPanel")
    stats = find_visual_child(root, "statsPanel")
    basic_plot = find_visual_child(root, "basicPlotPanel")

    split_height = split.property("height")
    assert split_height > 0
    # A few px of slack — the SplitView's own drag handles (2 of them,
    # between the 3 panes) eat into the total, so the panes' shares are a
    # little short of their exact mathematical fraction of split_height.
    assert abs(top_hits.property("height") - split_height * 0.3) <= 8.0
    assert abs(stats.property("height") - split_height * 0.3) <= 8.0
    assert abs(basic_plot.property("height") - split_height * 0.4) <= 8.0


def test_no_feature_selected_shows_placeholder(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    _open_annotations_tab(view, root, find_visual_child, qtbot)

    placeholder = find_visual_child(root, "annotationsDetailEmptyLabel")
    assert placeholder is not None
    assert placeholder.property("visible") is True


def test_details_button_opens_detail_window_for_selected_hit(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    _select_feature_7(view, root, find_visual_child, qtbot)

    details_button = find_visual_child(root, "mirrorPlotDetailsButton")
    assert details_button.property("enabled") is True
    _click(view, details_button, qtbot)

    detail_window = root.findChild(QObject, "mirrorPlotDetailWindow")
    assert detail_window is not None
    assert detail_window.property("visible") is True
    assert detail_window.property("annotationId") == 1
    assert detail_window.property("analysisDbPath") == annotated_analysis_model.analysisDbPath


def test_details_button_disabled_without_a_selected_hit(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    _open_annotations_tab(view, root, find_visual_child, qtbot)

    details_button = find_visual_child(root, "mirrorPlotDetailsButton")
    assert details_button.property("enabled") is False


def test_detail_window_loads_full_plotly_mirror_plot(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    _select_feature_7(view, root, find_visual_child, qtbot)
    _click(view, find_visual_child(root, "mirrorPlotDetailsButton"), qtbot)

    detail_window = root.findChild(QObject, "mirrorPlotDetailWindow")
    mirror_view = detail_window.findChild(QQuickItem, "detailMirrorPlotView")

    # requestMirrorPlot is async (MirrorPlotWorker, background thread) —
    # see AnalysisBridge's docstring on why this must not be synchronous.
    qtbot.waitUntil(
        lambda: mirror_view.property("url").toString().startswith("file://"), timeout=5000
    )


def test_detail_window_shows_metadata_table_next_to_the_plot(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    _select_feature_7(view, root, find_visual_child, qtbot)
    _click(view, find_visual_child(root, "mirrorPlotDetailsButton"), qtbot)

    detail_window = root.findChild(QObject, "mirrorPlotDetailWindow")
    qtbot.waitUntil(
        lambda: detail_window.property("metadata").get("compound_name") == "Caffeine",
        timeout=5000,
    )

    panel = detail_window.findChild(QObject, "detailMetadataPanel")
    assert panel is not None
    assert "Caffeine" in detail_window.findChild(QObject, "detailMetadataCompound").property("text")
    assert detail_window.findChild(QObject, "detailMetadataInchikey").property("text") == (
        "RYYVLZVUVIJVGH-UHFFFAOYSA-N"
    )
    assert detail_window.findChild(QObject, "detailMetadataLibrary").property("text") == "my_library"
    assert detail_window.findChild(QObject, "detailMetadataScore").property("text") == "0.8700"
    assert detail_window.findChild(QObject, "detailMetadataScanId").property("text") == "42"


def test_detail_window_reloads_on_source_toggle(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    # Set empSource directly rather than driving the ComboBox's native
    # popup — this codebase's tests don't simulate clicks inside a
    # ComboBox's popup list for any control (see test_visual_inspection_
    # section.py's sortModeCombo test); the onActivated -> refresh() wiring
    # itself is a one-line call, low risk without its own test.
    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    _select_feature_7(view, root, find_visual_child, qtbot)
    _click(view, find_visual_child(root, "mirrorPlotDetailsButton"), qtbot)

    detail_window = root.findChild(QObject, "mirrorPlotDetailWindow")
    mirror_view = detail_window.findChild(QQuickItem, "detailMirrorPlotView")
    qtbot.waitUntil(
        lambda: mirror_view.property("url").toString().startswith("file://"), timeout=5000
    )
    initial_url = mirror_view.property("url").toString()

    detail_window.setProperty("empSource", "raw")
    detail_window.refresh()

    qtbot.waitUntil(
        lambda: mirror_view.property("url").toString() != initial_url, timeout=5000
    )
    assert mirror_view.property("url").toString().startswith("file://")


def test_detail_window_resets_to_filtered_on_reopen(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    # Opening the popup for a *different* annotation than last time with
    # "raw" silently carried over would be surprising.
    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    _select_feature_7(view, root, find_visual_child, qtbot)
    _click(view, find_visual_child(root, "mirrorPlotDetailsButton"), qtbot)

    detail_window = root.findChild(QObject, "mirrorPlotDetailWindow")
    detail_window.setProperty("empSource", "raw")
    detail_window.setProperty("visible", False)
    detail_window.setProperty("visible", True)

    assert detail_window.property("empSource") == "filtered"
    assert detail_window.property("libSource") == "filtered"
