from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickItem
from PySide6.QtCore import QUrl, QObject, Qt
from pathlib import Path

from msianalyzer.gui.models.project import ProjectModel
from msianalyzer.core.project.project import Project, create_project_folder


def test_copy_to_clipboard_sets_system_clipboard_text(application):
    application.router.copyToClipboard("/tmp/proj/output_0")
    assert QGuiApplication.clipboard().text() == "/tmp/proj/output_0"


def test_to_local_path(application, tmp_path):
    qurl = QUrl.fromLocalFile(str(tmp_path))
    assert qurl.scheme() == "file"

    normalized = application.router.toLocalPath(qurl)

    assert normalized == str(tmp_path)
    assert Path(normalized).exists()
    assert qurl.toString() != tmp_path
    assert "file" in qurl.toString()


def test_show_project_home_requested_changes_home_page(
    application, project, engine, qtbot
):
    window = engine.rootObjects()[0]
    stack_view = window.findChild(QQuickItem, "stackView")
    assert stack_view is not None
    current_item = stack_view.property("currentItem")
    assert current_item.objectName() == "startPage"

    project_model = ProjectModel(project, "/tmp/proj", application)

    application.router.showProjectHomeRequested.emit(project_model)
    qtbot.wait(100)

    current_item = stack_view.property("currentItem")
    assert current_item.objectName() == "projectHomePage"

    label = current_item.findChild(QQuickItem, "projectNameLabel")
    assert label is not None
    assert label.property("text") == project.name


def test_create_new_project_requested_changes_page(application, engine, qtbot):

    window = engine.rootObjects()[0]
    stack_view = window.findChild(QQuickItem, "stackView")
    assert stack_view is not None
    current_item = stack_view.property("currentItem")
    assert current_item.objectName() == "startPage"

    application.router.createProjectPageRequested.emit()
    qtbot.wait(100)

    current_item = stack_view.property("currentItem")
    assert current_item.objectName() == "createProjectPage"


def test_invalidCreateProjectName_shows_message(application, engine, qtbot):
    window = engine.rootObjects()[0]
    stack_view = window.findChild(QQuickItem, "stackView")
    assert stack_view is not None

    message_dialog = window.findChild(QObject, "errorDialog")
    assert message_dialog is not None
    assert message_dialog.property("visible") is False
    assert message_dialog.property("modality") == Qt.ApplicationModal

    error_message = "Invalid name"
    application.core_bridge.invalidCreateProjectName.emit(error_message)

    qtbot.waitUntil(lambda: message_dialog.property("visible") is True, timeout=2000)
    assert message_dialog.property("text") == error_message
    message_dialog.setProperty("visible", False)


def test_project_folder_chosen_loads_real_project_and_shows_home_page(
    application, engine, qtbot, tmp_path
):
    proj_path = tmp_path / "myproj"
    create_project_folder(path=proj_path, name="myproj")

    window = engine.rootObjects()[0]
    stack_view = window.findChild(QQuickItem, "stackView")
    assert stack_view.property("currentItem").objectName() == "startPage"

    application.router.projectFolderChosen.emit(str(proj_path))
    qtbot.wait(100)

    current_item = stack_view.property("currentItem")
    assert current_item.objectName() == "projectHomePage"
    label = current_item.findChild(QQuickItem, "projectNameLabel")
    assert label.property("text") == "myproj"


def test_run_started_then_completed_shows_running_page_then_analysis_page(
    application, engine, qtbot, tmp_path
):
    proj_path = tmp_path / "myproj"
    create_project_folder(path=proj_path, name="myproj")
    application.project_folder = str(proj_path)
    application.core_bridge.load_project(str(proj_path))
    qtbot.wait(50)

    window = engine.rootObjects()[0]
    stack_view = window.findChild(QQuickItem, "stackView")
    current_item = stack_view.property("currentItem")
    assert current_item.objectName() == "projectHomePage"

    application.core_bridge.runStarted.emit("run-1")
    qtbot.wait(100)

    current_item = stack_view.property("currentItem")
    assert current_item.objectName() == "runningAnalysisPage"

    # The real run pipeline would have persisted "run-1" into the project
    # file by the time runCompleted fires; write it in directly so the
    # post-run reload (real disk I/O, same as production) finds it.
    project = Project.load_from_yaml(proj_path / ".msianalyzer.yml")
    project.runs["run-1"] = {
        "id": "run-1",
        "start_date": "2026-01-01 12:00:00",
        "config": {
            "io": {"out_dir": str(proj_path / "output")},
            "analysis": {"db_name": None},
        },
    }
    project.export(proj_path / ".msianalyzer.yml")

    application.core_bridge.runCompleted.emit("run-1")
    qtbot.wait(100)

    current_item = stack_view.property("currentItem")
    assert current_item.objectName() == "analysisPage"


def test_analysis_selected_shows_analysis_page(application, engine, qtbot, tmp_path):
    proj_path = tmp_path / "myproj"
    create_project_folder(path=proj_path, name="myproj")
    application.project_folder = str(proj_path)
    application.core_bridge.load_project(str(proj_path))
    qtbot.wait(50)

    application.project.runs["run-1"] = {
        "id": "run-1",
        "start_date": "2026-01-01 12:00:00",
        "config": {
            "io": {"out_dir": str(proj_path / "output")},
            "analysis": {"db_name": None},
        },
    }
    # the ProjectModel snapshot predates this run; rebuild it, mirroring the
    # reload Application does after a run actually completes
    application.project_model = ProjectModel(application.project, application.project_folder)

    window = engine.rootObjects()[0]
    stack_view = window.findChild(QQuickItem, "stackView")

    application.router.analysisSelected.emit("run-1")
    qtbot.wait(100)

    current_item = stack_view.property("currentItem")
    assert current_item.objectName() == "analysisPage"


def test_invalidCreateProjectPath_shows_message(application, engine, qtbot):
    window = engine.rootObjects()[0]
    stack_view = window.findChild(QQuickItem, "stackView")
    assert stack_view is not None

    message_dialog = window.findChild(QObject, "errorDialog")
    assert message_dialog is not None
    assert message_dialog.property("visible") is False

    error_message = "Invalid path"
    application.core_bridge.invalidCreateProjectPath.emit(error_message)

    qtbot.waitUntil(lambda: message_dialog.property("visible") is True, timeout=2000)
    assert message_dialog.property("text") == error_message
    message_dialog.setProperty("visible", False)


# ---------------------------------------------------------------------------
# Menu bar
# ---------------------------------------------------------------------------


def _project_with_n_runs(name, n):
    p = Project(name=name)
    for i in range(n):
        run_id = f"run-{i}"
        p.runs[run_id] = {
            "id": run_id,
            "start_date": f"2026-01-{i + 1:02d} 12:00:00",
            "end_date": f"2026-01-{i + 1:02d} 12:30:00",
            "status": "COMPLETED",
            "config": {"io": {"out_dir": f"/tmp/proj/output_{i}"}},
            "config_path": f"/tmp/proj/configs/run_{i}.yaml",
        }
    return p


def test_project_menu_new_project_opens_create_project_page(
    application, engine, qtbot
):
    window = engine.rootObjects()[0]
    stack_view = window.findChild(QQuickItem, "stackView")
    menu_item = window.findChild(QObject, "newProjectMenuItem")
    assert menu_item is not None

    menu_item.click()
    qtbot.wait(100)

    assert stack_view.property("currentItem").objectName() == "createProjectPage"


def test_analyses_menu_disabled_without_a_current_project(application, engine):
    window = engine.rootObjects()[0]
    analyses_menu = window.findChild(QObject, "analysesMenu")
    new_item = window.findChild(QObject, "newAnalysisMenuItem")
    open_menu = window.findChild(QObject, "openAnalysisMenu")
    close_item = window.findChild(QObject, "closeProjectMenuItem")

    # The whole top-level entry is disabled, not just its children — it
    # can't do anything at all without an open project, so it shouldn't
    # even be openable.
    assert analyses_menu.property("enabled") is False
    assert new_item.property("enabled") is False
    assert open_menu.property("enabled") is False
    assert close_item.property("enabled") is False


def test_analyses_menu_enabled_once_a_project_is_open(
    application, project, engine, qtbot
):
    window = engine.rootObjects()[0]
    analyses_menu = window.findChild(QObject, "analysesMenu")
    assert analyses_menu.property("enabled") is False

    project_model = ProjectModel(project, "/tmp/proj", application)
    application.router.showProjectHomeRequested.emit(project_model)
    qtbot.wait(100)

    assert analyses_menu.property("enabled") is True


def test_project_menu_close_project_returns_to_start_and_clears_state(
    application, project, engine, qtbot
):
    window = engine.rootObjects()[0]
    stack_view = window.findChild(QQuickItem, "stackView")
    project_model = ProjectModel(project, "/tmp/proj", application)
    application.project = project
    application.project_model = project_model
    application.project_folder = "/tmp/proj"

    application.router.showProjectHomeRequested.emit(project_model)
    qtbot.wait(100)
    assert window.property("currentProject") is not None

    close_item = window.findChild(QObject, "closeProjectMenuItem")
    assert close_item.property("enabled") is True
    close_item.click()
    qtbot.wait(100)

    assert stack_view.property("currentItem").objectName() == "startPage"
    assert window.property("currentProject") is None
    assert application.project is None
    assert application.project_model is None
    assert application.project_folder is None


def test_analyses_menu_new_opens_new_analysis_page_for_current_project(
    application, project, engine, qtbot
):
    window = engine.rootObjects()[0]
    stack_view = window.findChild(QQuickItem, "stackView")
    project_model = ProjectModel(project, "/tmp/proj", application)

    application.router.showProjectHomeRequested.emit(project_model)
    qtbot.wait(100)

    new_item = window.findChild(QObject, "newAnalysisMenuItem")
    assert new_item.property("enabled") is True
    new_item.click()
    qtbot.wait(100)

    current_item = stack_view.property("currentItem")
    assert current_item.objectName() == "newAnalysisPage"


def test_open_analysis_submenu_lists_5_most_recent_runs_and_navigates(
    application, engine, qtbot
):
    # 7 runs seeded; only the 5 most recent (runsList is already newest
    # first) should show as menu slots.
    proj = _project_with_n_runs("menu_test_proj", 7)
    project_model = ProjectModel(proj, "/tmp/proj", application)
    application.project = proj
    application.project_model = project_model
    application.project_folder = "/tmp/proj"

    window = engine.rootObjects()[0]
    stack_view = window.findChild(QQuickItem, "stackView")

    application.router.showProjectHomeRequested.emit(project_model)
    qtbot.wait(100)

    items = [
        window.findChild(QObject, f"openAnalysisMenuItem_{i}") for i in range(5)
    ]
    # `visible` on a MenuItem that's never actually been popped open isn't
    # a reliable read via QObject.property() in this offscreen test
    # environment (a Popup-family quirk seen elsewhere in this codebase —
    # see conftest.py's find_visual_child docstring for the general
    # class) — `run`/`text` are ordinary bound properties and do read
    # correctly, so assert through those instead.
    assert all(item.property("run") is not None for item in items)
    # newest first: run-6 (2026-01-07) is the most recent of the 7 seeded.
    assert items[0].property("text").startswith("2026-01-07")
    assert items[4].property("text").startswith("2026-01-03")

    items[0].click()
    qtbot.wait(100)

    assert stack_view.property("currentItem").objectName() == "analysisPage"


def test_open_analysis_submenu_hides_slots_beyond_available_runs(
    application, engine, qtbot
):
    # `visible` itself isn't reliably readable via QObject.property() for
    # a MenuItem that's never been popped open in this offscreen test
    # environment — see the previous test's comment. `run`/`text` (which
    # `visible` is itself bound to, `run !== null`) read correctly and are
    # what the QML actually gates the item's presence on.
    proj = _project_with_n_runs("menu_test_proj_2", 2)
    project_model = ProjectModel(proj, "/tmp/proj", application)
    application.project = proj
    application.project_model = project_model
    application.project_folder = "/tmp/proj"

    window = engine.rootObjects()[0]
    application.router.showProjectHomeRequested.emit(project_model)
    qtbot.wait(100)

    has_run = [
        window.findChild(QObject, f"openAnalysisMenuItem_{i}").property("run") is not None
        for i in range(5)
    ]
    assert has_run == [True, True, False, False, False]


def test_about_dialog_shows_software_name_and_version(application, engine, qtbot):
    window = engine.rootObjects()[0]
    about_item = window.findChild(QObject, "aboutMenuItem")
    about_dialog = window.findChild(QObject, "aboutDialog")
    assert about_dialog.property("visible") is False

    about_item.click()
    qtbot.wait(50)

    assert about_dialog.property("visible") is True
    version_label = about_dialog.findChild(QObject, "aboutVersionLabel")
    assert version_label is not None
    assert version_label.property("text").startswith("Version ")


# ---------------------------------------------------------------------------
# View menu — light/dark theme switch
# ---------------------------------------------------------------------------


def test_view_menu_dark_is_checked_by_default(application, engine):
    window = engine.rootObjects()[0]
    dark_item = window.findChild(QObject, "darkThemeMenuItem")
    light_item = window.findChild(QObject, "lightThemeMenuItem")

    assert dark_item.property("checked") is True
    assert light_item.property("checked") is False


def test_view_menu_light_switches_window_color_live_and_back(
    application, engine, qtbot
):
    window = engine.rootObjects()[0]
    dark_color = window.property("color")

    light_item = window.findChild(QObject, "lightThemeMenuItem")
    light_item.click()
    qtbot.wait(50)

    dark_item = window.findChild(QObject, "darkThemeMenuItem")
    assert light_item.property("checked") is True
    assert dark_item.property("checked") is False
    light_color = window.property("color")
    assert light_color != dark_color

    dark_item.click()
    qtbot.wait(50)

    assert dark_item.property("checked") is True
    assert light_item.property("checked") is False
    assert window.property("color") == dark_color


def test_open_user_guide_opens_local_built_docs(monkeypatch, application):
    # The repo's own `site/index.html` (built via `mkdocs build`, see
    # docs/ CI/dev workflow) — real path resolution, but QDesktopServices
    # itself is mocked out so the test doesn't actually launch a browser.
    opened = []
    monkeypatch.setattr(
        "msianalyzer.gui.utils.router.QDesktopServices.openUrl",
        lambda url: opened.append(url) or True,
    )

    result = application.router.openUserGuide()

    assert result is True
    assert len(opened) == 1
    assert opened[0].toLocalFile().endswith("site/index.html")


def test_open_user_guide_returns_false_when_docs_not_built(monkeypatch, application):
    monkeypatch.setattr(
        "msianalyzer.gui.utils.router.Path.exists", lambda self: False, raising=False
    )
    assert application.router.openUserGuide() is False
