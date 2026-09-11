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
        self.current_run_id: str | None = None
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
        self.router.runAnalysisRequested.connect(self._on_run_analysis_requested)
        self.core_bridge.invalidConfig.connect(self.router.showErrorRequested)
        self.core_bridge.runStarted.connect(self._on_run_started)
        self.core_bridge.runCompleted.connect(self._on_run_completed)

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

    def _on_run_analysis_requested(self, project, config_dict):
        self.core_bridge.run_analysis(config_dict, self.project_folder)

    def _on_run_started(self, run_id: str):
        self.current_run_id = run_id
        self.router.showRunningPageRequested.emit(self.project_model, run_id)

    def _on_run_completed(self, run_id: str):
        # Reload from disk so the new run shows up in ProjectModel.runsList —
        # reuses the same load -> projectLoaded -> showProjectHomeRequested
        # path as opening a project from the start page. core_bridge.load_project
        # takes the `.msianalyzer.yml` file itself, not the project folder.
        project_file = str(Path(self.project_folder) / ".msianalyzer.yml")
        self.core_bridge.load_project(project_file)
