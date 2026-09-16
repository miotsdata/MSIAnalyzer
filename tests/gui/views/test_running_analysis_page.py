from PySide6.QtQuick import QQuickItem

from msianalyzer.gui.models.project import ProjectModel
from msianalyzer.core.run.run import RUN_STEPS


def _make_page(running_analysis_view, project):
    model = ProjectModel(project, "/tmp/proj")
    view = running_analysis_view(model, "run-1")
    return view, view.rootObject(), model


def test_all_steps_render_as_pending_initially(
    running_analysis_view, project, find_visual_child
):
    _, root, _ = _make_page(running_analysis_view, project)

    for step in RUN_STEPS:
        status = find_visual_child(root, "stepStatus_" + step)
        assert status is not None
        assert status.property("text") == "pending"


def test_step_changed_updates_matching_row(
    running_analysis_view, project, application, find_visual_child, qtbot
):
    _, root, _ = _make_page(running_analysis_view, project)

    application.core_bridge.runStepChanged.emit("align_mz", "completed")
    qtbot.wait(50)

    status = find_visual_child(root, "stepStatus_align_mz")
    assert status.property("text") == "completed"

    other_status = find_visual_child(root, "stepStatus_group_ms2")
    assert other_status.property("text") == "pending"


def test_progress_bar_hidden_for_pending_step(
    running_analysis_view, project, find_visual_child
):
    _, root, _ = _make_page(running_analysis_view, project)

    # Searched from the row, not `root` — see find_visual_child's own
    # docstring: a search from a large subtree (many Repeater delegates,
    # each now a full QQC2 ProgressBar with its own background/contentItem
    # children, not just a plain Label) is a known PySide6 wrapper-lifecycle
    # fragility in this environment, not something specific to this test.
    row = find_visual_child(root, "stepRow_align_mz")
    bar = find_visual_child(row, "stepProgressBar_align_mz")
    assert bar is not None
    assert bar.property("visible") is False


def test_non_sample_step_shows_indeterminate_progress_bar_while_started(
    running_analysis_view, project, application, find_visual_child, qtbot
):
    _, root, _ = _make_page(running_analysis_view, project)

    application.core_bridge.runStepChanged.emit("align_mz", "started")
    qtbot.wait(50)

    row = find_visual_child(root, "stepRow_align_mz")
    bar = find_visual_child(row, "stepProgressBar_align_mz")
    assert bar.property("visible") is True
    assert bar.property("indeterminate") is True


def test_process_samples_step_shows_real_progress_and_count_while_started(
    running_analysis_view, project, application, find_visual_child, qtbot
):
    _, root, _ = _make_page(running_analysis_view, project)

    application.core_bridge.runStepChanged.emit("process_samples", "started")
    application.core_bridge.runSampleProgress.emit(1, 3)
    qtbot.wait(50)

    row = find_visual_child(root, "stepRow_process_samples")
    bar = find_visual_child(row, "stepProgressBar_process_samples")
    assert bar.property("visible") is True
    assert bar.property("indeterminate") is False
    assert bar.property("value") == 1
    assert bar.property("to") == 3

    status = find_visual_child(row, "stepStatus_process_samples")
    assert status.property("text") == "started (1/3)"


def test_process_samples_progress_bar_hidden_once_completed(
    running_analysis_view, project, application, find_visual_child, qtbot
):
    _, root, _ = _make_page(running_analysis_view, project)

    application.core_bridge.runStepChanged.emit("process_samples", "started")
    application.core_bridge.runSampleProgress.emit(3, 3)
    application.core_bridge.runStepChanged.emit("process_samples", "completed")
    qtbot.wait(50)

    row = find_visual_child(root, "stepRow_process_samples")
    bar = find_visual_child(row, "stepProgressBar_process_samples")
    assert bar.property("visible") is False

    status = find_visual_child(row, "stepStatus_process_samples")
    assert status.property("text") == "completed"


def test_back_button_is_always_available(running_analysis_view, project):
    _, root, _ = _make_page(running_analysis_view, project)

    back_button = root.findChild(QQuickItem, "backButton")
    assert back_button is not None
    assert back_button.property("visible") is True


def test_back_button_navigates_without_cancelling_the_run(
    running_analysis_view, project, application, qtbot
):
    view, root, model = _make_page(running_analysis_view, project)
    back_button = root.findChild(QQuickItem, "backButton")

    received = []
    application.router.showProjectHomeRequested.connect(received.append)

    from PySide6.QtCore import Qt

    center = back_button.mapToScene(back_button.boundingRect().center()).toPoint()
    qtbot.mouseClick(view, Qt.LeftButton, pos=center)
    qtbot.wait(50)

    assert len(received) == 1
    assert received[0].name == model.name


def test_run_failed_shows_error_banner(
    running_analysis_view, project, application, qtbot
):
    _, root, _ = _make_page(running_analysis_view, project)

    error_text = root.findChild(QQuickItem, "errorText")
    assert error_text.property("visible") is False

    application.core_bridge.runFailed.emit("sample processing failed")
    qtbot.wait(50)

    assert error_text.property("visible") is True
    assert "sample processing failed" in error_text.property("text")
    # Style/Theme.qml singleton (2026-09-14) — a raw "red" literal read
    # harsh against the new dark palette; this should be Theme.errorColor.
    from PySide6.QtGui import QColor

    assert error_text.property("color") == QColor("#e06c75")
