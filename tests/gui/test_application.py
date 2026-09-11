from unittest.mock import MagicMock

from msianalyzer.gui.models.project import ProjectModel


def test_project_folder_chosen_triggers_core_load(application):
    application.core_bridge.load_project = MagicMock()

    application.router.projectFolderChosen.emit("/some/path")

    application.core_bridge.load_project.assert_called_once_with("/some/path")


def test_on_project_folder_chosen_triggers_show_project_home_requested(application):
    project = MagicMock(name="proj1", uuid="sdffd")
    received = []
    application.router.showProjectHomeRequested.connect(received.append)

    application.core_bridge.projectLoaded.emit(project)

    assert len(received) == 1
    emitted_model = received[0]
    assert isinstance(emitted_model, ProjectModel)
    assert emitted_model.name == project.name
    assert emitted_model.uuid == project.uuid


def test_core_bridge_invalidCreateProjectName_triggers_router_showErrorRequested(
    application,
):
    error_message = "Name error"
    received = []

    application.router.showErrorRequested.connect(received.append)

    application.core_bridge.invalidCreateProjectName.emit(error_message)

    assert len(received) == 1
    assert received[0] == error_message


def test_core_bridge_invalidCreateProjectName_triggers_router_showErrorRequested(
    application,
):
    error_message = "Path error"
    received = []

    application.router.showErrorRequested.connect(received.append)

    application.core_bridge.invalidCreateProjectPath.emit(error_message)

    assert len(received) == 1
    assert received[0] == error_message


def test_project_folder_chosen_sets_application_project_folder(application):
    application.core_bridge.load_project = MagicMock()

    application.router.projectFolderChosen.emit("/some/path")

    assert application.project_folder == "/some/path"


def test_create_project_requested_triggers_core_create_and_stores_folder(
    application,
):
    application.core_bridge.create_project = MagicMock()

    application.router.createProjectRequested.emit("My Project", "/some/parent")

    application.core_bridge.create_project.assert_called_once_with(
        "My Project", "/some/parent"
    )
    assert application.project_folder == "/some/parent/My Project"


def test_project_loaded_builds_project_model_with_folder(application):
    application.project_folder = "/some/parent/My Project"
    project = MagicMock(name="proj1", uuid="sdffd")
    received = []
    application.router.showProjectHomeRequested.connect(received.append)

    application.core_bridge.projectLoaded.emit(project)

    assert len(received) == 1
    model = received[0]
    assert model.folder == "/some/parent/My Project"
    assert application.project_model is model


def test_project_loaded_builds_runs_list_from_project(application, project_with_runs):
    application.project_folder = "/tmp/proj"
    received = []
    application.router.showProjectHomeRequested.connect(received.append)

    application.core_bridge.projectLoaded.emit(project_with_runs)

    model = received[0]
    runs_list = model.runsList
    assert len(runs_list) == len(project_with_runs.runs)
    assert runs_list[0]["start_date"] >= runs_list[-1]["start_date"]  # newest first


def test_run_analysis_requested_calls_core_bridge_with_application_project_folder(
    application, project
):
    application.project_folder = "/some/proj"
    application.core_bridge.run_analysis = MagicMock()
    config_dict = {"io": {"mzml_paths": []}}
    project_model = ProjectModel(project, "/some/proj")

    application.router.runAnalysisRequested.emit(project_model, config_dict)

    application.core_bridge.run_analysis.assert_called_once_with(
        config_dict, "/some/proj"
    )


def test_invalidConfig_triggers_router_showErrorRequested(application):
    received = []
    application.router.showErrorRequested.connect(received.append)

    application.core_bridge.invalidConfig.emit("bad config")

    assert received == ["bad config"]


def test_run_started_sets_current_run_id(application):
    assert application.current_run_id is None

    application.core_bridge.runStarted.emit("run-123")

    assert application.current_run_id == "run-123"
