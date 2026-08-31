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
        self._connect_signals()

    def _connect_signals(self):
        self.router.projectFolderChosen.connect(self._on_project_folder_chosen)
        self.core_bridge.projectLoaded.connect(self._on_project_loaded)
        self.router.createProjectRequested.connect(self.core_bridge.create_project)

    def _on_project_folder_chosen(self, path):
        self.core_bridge.load_project(path)

    def _on_project_loaded(self, project: Project):
        self.project = project
        self.router.showProjectHomeRequested.emit(ProjectModel(project, self))
