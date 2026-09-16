import sqlite3
from unittest.mock import MagicMock

from PySide6.QtCore import QObject, Qt
from PySide6.QtQuick import QQuickItem


def _click(window_or_view, item, qtbot):
    qtbot.mouseClick(
        window_or_view, Qt.LeftButton,
        pos=item.mapToScene(item.boundingRect().center()).toPoint(),
    )
    qtbot.wait(50)


def _open_annotations_tab(view, root, find_visual_child, qtbot):
    nav_button = find_visual_child(root, "navButton_2")
    _click(view, nav_button, qtbot)


def _open_predict_formula_window(view, root, find_visual_child, qtbot):
    _open_annotations_tab(view, root, find_visual_child, qtbot)
    button = find_visual_child(root, "openPredictFormulaButton")
    _click(view, button, qtbot)
    window = root.findChild(QObject, "predictFormulaWindow")
    assert window is not None
    qtbot.waitUntil(lambda: window.property("visible") is True, timeout=2000)
    return window


def _seed_unannotated_feature(db_path, feature_id=2, mz=50.0):
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO features (feature_id, mz, members_json) VALUES (?, ?, '{}')",
            (feature_id, mz),
        )
        con.commit()


# Every feature row / feature checkbox / adduct checkbox below is created by
# a Repeater — QObject.findChild doesn't reliably reach those (their QObject
# parent is the QQmlDelegateModel machinery, not the visual parent), so
# these all go through find_visual_child(window.contentItem(), ...) instead,
# same as test_roi_design_window.py's identical pattern for RoiDesignWindow.


def test_predict_formula_button_exists_and_enabled(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    _open_annotations_tab(view, root, find_visual_child, qtbot)

    button = find_visual_child(root, "openPredictFormulaButton")
    assert button is not None
    assert button.property("enabled") is True


def test_clicking_button_opens_window_with_features_listed(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    _seed_unannotated_feature(annotated_analysis_model.analysisDbPath)
    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()

    window = _open_predict_formula_window(view, root, find_visual_child, qtbot)

    assert find_visual_child(window.contentItem(), "predictFeatureRow_7") is not None
    assert find_visual_child(window.contentItem(), "predictFeatureRow_2") is not None


def test_default_polarity_positive_and_adducts_shown(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    window = _open_predict_formula_window(view, root, find_visual_child, qtbot)

    polarity_combo = window.findChild(QQuickItem, "predictPolarityCombo")
    assert polarity_combo.property("currentText") == "positive"
    assert find_visual_child(window.contentItem(), "predictAdduct_[M+H]+") is not None
    assert find_visual_child(window.contentItem(), "predictAdduct_[M-H]-") is None


def test_switching_polarity_swaps_adducts_and_clears_selection(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    window = _open_predict_formula_window(view, root, find_visual_child, qtbot)

    positive_checkbox = find_visual_child(window.contentItem(), "predictAdduct_[M+H]+")
    _click(window, positive_checkbox, qtbot)
    assert positive_checkbox.property("checked") is True

    polarity_combo = window.findChild(QQuickItem, "predictPolarityCombo")
    polarity_combo.setProperty("currentIndex", 1)
    qtbot.wait(50)

    assert polarity_combo.property("currentText") == "negative"
    assert find_visual_child(window.contentItem(), "predictAdduct_[M+H]+") is None
    negative_checkbox = find_visual_child(window.contentItem(), "predictAdduct_[M-H]-")
    assert negative_checkbox is not None
    assert negative_checkbox.property("checked") is False


def test_select_all_unannotated_selects_only_unannotated(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    _seed_unannotated_feature(annotated_analysis_model.analysisDbPath)
    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    window = _open_predict_formula_window(view, root, find_visual_child, qtbot)

    select_button = window.findChild(QQuickItem, "predictSelectAllUnannotatedButton")
    _click(window, select_button, qtbot)

    selected = window.property("selectedFeatureIds").toVariant()
    assert selected == [2]


def test_run_button_disabled_until_features_and_adducts_selected(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    window = _open_predict_formula_window(view, root, find_visual_child, qtbot)

    run_button = window.findChild(QQuickItem, "predictRunButton")
    assert run_button.property("enabled") is False

    feature_check = find_visual_child(window.contentItem(), "predictFeatureCheck_7")
    _click(window, feature_check, qtbot)
    assert run_button.property("enabled") is False  # no adduct selected yet

    adduct_check = find_visual_child(window.contentItem(), "predictAdduct_[M+H]+")
    _click(window, adduct_check, qtbot)
    assert run_button.property("enabled") is True


def test_run_click_calls_predict_formulas_with_settings(
    analysis_view, annotated_analysis_model, application, find_visual_child, qtbot
):
    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    window = _open_predict_formula_window(view, root, find_visual_child, qtbot)

    _click(window, find_visual_child(window.contentItem(), "predictFeatureCheck_7"), qtbot)
    _click(window, find_visual_child(window.contentItem(), "predictAdduct_[M+H]+"), qtbot)

    application.analysis_bridge.predictFormulas = MagicMock()
    _click(window, window.findChild(QQuickItem, "predictRunButton"), qtbot)

    application.analysis_bridge.predictFormulas.assert_called_once()
    args = application.analysis_bridge.predictFormulas.call_args.args
    assert args[0] == annotated_analysis_model.analysisDbPath
    assert list(args[1]) == [7]
    assert args[2]["adducts"] == ["[M+H]+"]
    assert args[2]["top_n"] == 5
    assert args[2]["error_ppm"] == 10.0
    assert args[2]["halogen"] is False


def test_predicting_shows_loading_overlay_and_clears_on_finished(
    analysis_view, annotated_analysis_model, application, find_visual_child, qtbot
):
    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    window = _open_predict_formula_window(view, root, find_visual_child, qtbot)

    _click(window, find_visual_child(window.contentItem(), "predictFeatureCheck_7"), qtbot)
    _click(window, find_visual_child(window.contentItem(), "predictAdduct_[M+H]+"), qtbot)

    application.analysis_bridge.predictFormulas = MagicMock()
    _click(window, window.findChild(QQuickItem, "predictRunButton"), qtbot)

    assert window.property("predicting") is True
    overlay = window.findChild(QQuickItem, "predictLoadingOverlay")
    assert overlay.property("visible") is True

    application.analysis_bridge.formulaPredictionFinished.emit(
        annotated_analysis_model.analysisDbPath
    )
    qtbot.wait(50)

    assert window.property("predicting") is False
    assert overlay.property("visible") is False


def test_failed_signal_shows_error_and_clears_predicting(
    analysis_view, annotated_analysis_model, application, find_visual_child, qtbot
):
    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    window = _open_predict_formula_window(view, root, find_visual_child, qtbot)

    _click(window, find_visual_child(window.contentItem(), "predictFeatureCheck_7"), qtbot)
    _click(window, find_visual_child(window.contentItem(), "predictAdduct_[M+H]+"), qtbot)

    application.analysis_bridge.predictFormulas = MagicMock()
    _click(window, window.findChild(QQuickItem, "predictRunButton"), qtbot)

    application.analysis_bridge.formulaPredictionFailed.emit(
        annotated_analysis_model.analysisDbPath, "boom"
    )
    qtbot.wait(50)

    assert window.property("predicting") is False
    error_label = window.findChild(QQuickItem, "predictErrorLabel")
    assert error_label.property("text") == "boom"
    assert error_label.property("visible") is True


def test_finished_signal_bumps_annotations_refresh_token(
    analysis_view, annotated_analysis_model, application, find_visual_child, qtbot
):
    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    _open_predict_formula_window(view, root, find_visual_child, qtbot)
    section = find_visual_child(root, "annotationsSection")
    before = section.property("refreshToken")

    application.analysis_bridge.formulaPredictionFinished.emit(
        annotated_analysis_model.analysisDbPath
    )
    qtbot.wait(50)

    assert section.property("refreshToken") == before + 1
