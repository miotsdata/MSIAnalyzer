from pathlib import Path

from PySide6.QtCore import QObject

from msianalyzer.gui.models.project import ProjectModel
from msianalyzer.gui.utils.core_bridge import CoreBridge
from msianalyzer.gui.utils.router import Router
from msianalyzer.core.project import Project


class Application(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.router = Router()
        self.core_bridge = CoreBridge()
        self.project: Project | None = None
        # Not stored on `Project` itself, so Application tracks it here and
        # threads it onto `ProjectModel` — the new-analysis and running pages
        # need it (default IOConfig paths, reloading the project on
        # completion).
        self.project_folder: str | None = None
        self.project_model: ProjectModel | None = None
        self._connect_signals()

    def _connect_signals(self):
        self.router.projectFolderChosen.connect(self._on_project_folder_chosen)
        self.core_bridge.projectLoaded.connect(self._on_project_loaded)
        self.router.createProjectRequested.connect(self._on_create_project_requested)
        self.core_bridge.invalidCreateProjectName.connect(
            self.router.showErrorRequested
        )
        self.core_bridge.invalidCreateProjectPath.connect(
            self.router.showErrorRequested
        )

    def _on_project_folder_chosen(self, path):
        self.project_folder = path
        self.core_bridge.load_project(path)

    def _on_create_project_requested(self, name, path):
        self.project_folder = str(Path(path) / name)
        self.core_bridge.create_project(name, path)

    def _on_project_loaded(self, project: Project):
        self.project = project
        self.project_model = ProjectModel(project, self.project_folder, self)
        self.router.showProjectHomeRequested.emit(self.project_model)
