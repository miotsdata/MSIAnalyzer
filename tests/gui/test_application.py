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
