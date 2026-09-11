from pathlib import Path

from PySide6.QtCore import QObject

from msianalyzer.gui.models.analysis import AnalysisModel
from msianalyzer.gui.models.project import ProjectModel
from msianalyzer.gui.utils.analysis_bridge import AnalysisBridge
from msianalyzer.gui.utils.core_bridge import CoreBridge
from msianalyzer.gui.utils.router import Router
from msianalyzer.core.project import Project


class Application(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.router = Router()
        self.core_bridge = CoreBridge()
        self.analysis_bridge = AnalysisBridge()
        self.project: Project | None = None
        # Not stored on `Project` itself, so Application tracks it here and
        # threads it onto `ProjectModel` — the new-analysis and running pages
        # need it (default IOConfig paths, reloading the project on
        # completion).
        self.project_folder: str | None = None
        self.project_model: ProjectModel | None = None
        self.current_run_id: str | None = None
        # Set right before the post-run `load_project` in
        # `_on_run_completed`, consumed by `_on_project_loaded` — lets that
        # one reload path decide whether to land on the analysis that just
        # finished instead of Project Home.
        self._pending_analysis_run_id: str | None = None
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
        self.router.analysisSelected.connect(self._on_analysis_selected)

    def _on_project_folder_chosen(self, path):
        self.project_folder = path
        self.core_bridge.load_project(path)

    def _on_create_project_requested(self, name, path):
        self.project_folder = str(Path(path) / name)
        self.core_bridge.create_project(name, path)

    def _on_project_loaded(self, project: Project):
        self.project = project
        self.project_model = ProjectModel(project, self.project_folder, self)
        if self._pending_analysis_run_id is not None:
            run_id = self._pending_analysis_run_id
            self._pending_analysis_run_id = None
            self._on_analysis_selected(run_id)
        else:
            self.router.showProjectHomeRequested.emit(self.project_model)

    def _on_run_analysis_requested(self, project, config_dict):
        self.core_bridge.run_analysis(config_dict, self.project_folder)

    def _on_run_started(self, run_id: str):
        self.current_run_id = run_id
        self.router.showRunningPageRequested.emit(self.project_model, run_id)

    def _on_run_completed(self, run_id: str):
        # Reload from disk so the new run shows up in ProjectModel.runsList —
        # reuses the same load -> projectLoaded path as opening a project
        # from the start page, but lands on the analysis that just finished
        # (see `_pending_analysis_run_id`) instead of Project Home.
        self._pending_analysis_run_id = run_id
        self.core_bridge.load_project(self.project_folder)

    def _on_analysis_selected(self, run_id: str):
        if self.project is None or run_id not in self.project.runs:
            return
        run_dict = self.project.runs[run_id]
        analysis_model = AnalysisModel(self.project_model, run_id, run_dict, self)
        self.router.showAnalysisRequested.emit(analysis_model)
