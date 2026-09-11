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


def test_run_failed_shows_error_banner_and_back_button(
    running_analysis_view, project, application, qtbot
):
    _, root, _ = _make_page(running_analysis_view, project)

    error_text = root.findChild(QQuickItem, "errorText")
    back_button = root.findChild(QQuickItem, "backButton")
    assert error_text.property("visible") is False
    assert back_button.property("visible") is False

    application.core_bridge.runFailed.emit("sample processing failed")
    qtbot.wait(50)

    assert error_text.property("visible") is True
    assert "sample processing failed" in error_text.property("text")
    assert back_button.property("visible") is True
