import sqlite3

import numpy as np
import pytest
from PySide6.QtCore import Qt


def _open_ms1_tab(view, root, find_visual_child, qtbot):
    nav_button = find_visual_child(root, "navButton_1")
    center = nav_button.mapToScene(nav_button.boundingRect().center()).toPoint()
    qtbot.mouseClick(view, Qt.LeftButton, pos=center)
    qtbot.wait(50)


@pytest.fixture
def ms1_analysis_model(annotated_analysis_model):
    """`annotated_analysis_model` (feature 7 / sample s1 / Caffeine), plus a
    saved filtered spectrum for sample 1 (so the sample selector + spectrum
    view have something to show) and a `features`/`feature_ms2_summary` row
    for feature 7 (so a simulated `spectrumPointClicked` resolves to it)."""
    from msianalyzer.core.analysis_db import log_command
    from msianalyzer.core.spectra.average_spectra import save_aggregated_spectra

    db_path = annotated_analysis_model.analysisDbPath

    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO features (feature_id, mz, members_json) "
            "VALUES (7, 150.0, '{\"s1\": 0}')"
        )
        con.execute(
            "INSERT INTO feature_ms2_summary (feature_id, feature_mz, n_ms2, "
            "n_samples, n_precursor_only, n_single_peak, n_chimeric, "
            "n_flat_fragmentation, median_n_peaks) "
            "VALUES (7, 150.0, 5, 1, 0, 0, 0, 0, 3.0)"
        )
        con.commit()

    command_id = log_command(
        db_path, "filter_spectra", {}, run_id="test-run", sample_id=1
    )
    save_aggregated_spectra(
        np.array([100.0, 150.0, 200.0]),
        np.array([10.0, 50.0, 20.0]),
        analysis_db_path=db_path,
        run_id="test-run",
        sample_id=1,
        command_id=command_id,
    )
    return annotated_analysis_model


def test_ms1_empty_state_without_samples(
    analysis_view, analysis_model, find_visual_child, qtbot
):
    view = analysis_view(analysis_model)
    root = view.rootObject()
    _open_ms1_tab(view, root, find_visual_child, qtbot)

    empty_label = find_visual_child(root, "ms1EmptyStateLabel")
    assert empty_label is not None
    assert empty_label.property("visible") is True


def test_ms1_shows_sample_selector_for_seeded_samples(
    analysis_view, ms1_analysis_model, find_visual_child, qtbot
):
    view = analysis_view(ms1_analysis_model)
    root = view.rootObject()
    _open_ms1_tab(view, root, find_visual_child, qtbot)

    empty_label = find_visual_child(root, "ms1EmptyStateLabel")
    assert empty_label.property("visible") is False

    combo = find_visual_child(root, "sampleCombo")
    assert combo is not None
    assert combo.property("count") == 1

    detail_empty = find_visual_child(root, "detailEmptyLabel")
    assert detail_empty.property("visible") is True


def test_ms1_spectrum_point_click_updates_detail_panel(
    analysis_view, ms1_analysis_model, find_visual_child, qtbot, application
):
    view = analysis_view(ms1_analysis_model)
    root = view.rootObject()
    _open_ms1_tab(view, root, find_visual_child, qtbot)

    # The WebEngineView's own click handling isn't safe/useful to simulate
    # here (see AnnotationsSection tests) — instead fire the signal the
    # embedded JS would emit via QWebChannel, exactly as the real click
    # handler in AnalysisBridge.getSpectrumHtml does downstream.
    application.analysis_bridge.spectrumPointClicked.emit(150.1)
    qtbot.wait(50)

    feature_id_label = find_visual_child(root, "detailFeatureId")
    assert "Feature 7" in feature_id_label.property("text")

    n_ms2_label = find_visual_child(root, "detailNMs2")
    assert n_ms2_label.property("text") == "MS2 scans: 5"

    present_label = find_visual_child(root, "detailSamplesPresent")
    assert present_label.property("text") == "s1"

    top_hit = find_visual_child(root, "topHit_0")
    assert "Caffeine" in top_hit.property("text")

    no_annotation_label = find_visual_child(root, "detailNoAnnotationLabel")
    assert no_annotation_label.property("visible") is False
