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
    # handler in AnalysisBridge.getSpectrumUrl does downstream.
    application.analysis_bridge.spectrumPointClicked.emit(150.1)
    qtbot.wait(50)

    # Scoped to detailPanel, not the whole page `root` — searching from
    # `root` right after this click reproducibly segfaulted inside
    # PySide6's childItems()-based traversal (find_visual_child) while it
    # was walking freshly-created delegates from the *three* Repeaters
    # (top hits, samples present, samples absent) that all populate at
    # once when `featureDetail` changes; a real PySide6 wrapper-lifecycle
    # bug (crash was inside `PySide::getWrapperForQObject`, confirmed via
    # gdb), not anything wrong with the QML itself — `root.findChild`
    # reaches the exact same items without crashing, and a scoped search
    # (fewer nodes to walk) avoids it entirely. The original single-Repeater
    # version of this section never hit it.
    detail_panel = find_visual_child(root, "detailPanel")

    feature_id_label = find_visual_child(detail_panel, "detailFeatureId")
    assert "Feature 7" in feature_id_label.property("text")

    n_ms2_label = find_visual_child(detail_panel, "detailNMs2")
    assert n_ms2_label.property("text") == "MS2 scans: 5"

    samples_present_repeater = find_visual_child(detail_panel, "samplesPresentRepeater")
    assert samples_present_repeater.property("count") == 1
    samples_present_item = find_visual_child(detail_panel, "samplesPresentItem_0")
    assert samples_present_item.property("text") == "s1"

    samples_absent_empty = find_visual_child(detail_panel, "samplesAbsentEmptyLabel")
    assert samples_absent_empty.property("visible") is True

    top_hit = find_visual_child(detail_panel, "topHit_0")
    assert "Caffeine" in top_hit.property("text")

    no_annotation_label = find_visual_child(detail_panel, "detailNoAnnotationLabel")
    assert no_annotation_label.property("visible") is False


def test_layout_is_two_rows_not_two_columns(
    analysis_view, ms1_analysis_model, find_visual_child, qtbot
):
    # "instead of a 2 column layout, it is better a 2 rows layout, as MS1
    # spectra are wide" — the spectrum plot sits above the feature-detail
    # panel (not beside it), and both span close to the section's full width.
    view = analysis_view(ms1_analysis_model)
    root = view.rootObject()
    _open_ms1_tab(view, root, find_visual_child, qtbot)

    spectrum_view = find_visual_child(root, "spectrumView")
    detail_panel = find_visual_child(root, "detailPanel")
    ms1_section = find_visual_child(root, "ms1Section")

    assert detail_panel.y() > spectrum_view.y()
    # Both roughly span the section's width (not squeezed into a narrow
    # side column) — allow for the section's own margins.
    assert spectrum_view.width() > ms1_section.width() * 0.7
    assert detail_panel.width() > ms1_section.width() * 0.7


def test_samples_present_and_absent_lists_cap_height_before_scrolling(
    analysis_view, ms1_analysis_model, find_visual_child, qtbot, application
):
    # "present in and absent in should have list of files (scrollable, max
    # 10 files height before scrolling)"
    view = analysis_view(ms1_analysis_model)
    root = view.rootObject()
    _open_ms1_tab(view, root, find_visual_child, qtbot)

    application.analysis_bridge.spectrumPointClicked.emit(150.1)
    qtbot.wait(50)

    ms1_section = find_visual_child(root, "ms1Section")
    detail_panel = find_visual_child(root, "detailPanel")

    assert ms1_section.property("detailListMaxHeight") == 180  # 18px * 10 rows

    samples_present_flickable = find_visual_child(detail_panel, "samplesPresentFlickable")
    assert samples_present_flickable.height() <= 180


def test_top_n_spinbox_is_keyboard_editable(
    analysis_view, ms1_analysis_model, find_visual_child, qtbot
):
    # SpinBox defaults to editable: false — arrow-buttons-only, no way to
    # type a number directly.
    view = analysis_view(ms1_analysis_model)
    root = view.rootObject()
    _open_ms1_tab(view, root, find_visual_child, qtbot)

    assert find_visual_child(root, "topNSpinBox").property("editable") is True
