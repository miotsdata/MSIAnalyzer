from pathlib import Path

from PySide6.QtCore import Property, QObject, Signal

from msianalyzer.core import project
from msianalyzer.core.config import Config
from msianalyzer.core.project.project import Project, create_project_folder
from msianalyzer.core.run.run import RUN_STEPS, Run
from msianalyzer.gui.utils.config_schema import coerce_config_values
from msianalyzer.gui.utils.run_worker import RunWorker


class CoreBridge(QObject):
    projectLoaded = Signal(Project)
    invalidProjectPath = Signal(str)
    invalidCreateProjectName = Signal(str)
    invalidCreateProjectPath = Signal(str)
    invalidConfig = Signal(str)
    runStarted = Signal(str)
    runStepChanged = Signal(str, str)
    runCompleted = Signal(str)
    runFailed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # Kept alive while running — nothing else holds a reference to it.
        self._run_worker: RunWorker | None = None

    @Property(list, constant=True)
    def RUN_STEPS(self) -> list[str]:
        """Canonical, ordered `Run` stage names — see `core.run.run.RUN_STEPS`."""
        return list(RUN_STEPS)

    def load_project(self, path):
        try:
            project = Project.load_from_yaml(path)
        except ValueError as ve:
            self.invalidProjectPath.emit(str(ve))
        except FileNotFoundError:
            self.invalidProjectPath.emit(f"{path} does not exists.")
        else:
            self.projectLoaded.emit(project)

    def create_project(self, name, path):
        try:
            project = Project(name)
        except ValueError as ve:
            self.invalidCreateProjectName.emit(str(ve))
            return None

        try:
            path = f"{path}/{name}"
            create_project_folder(path=path, name=name)
        except (OSError, PermissionError, FileExistsError) as e:
            self.invalidCreateProjectPath.emit(str(e))
            return None

        self.projectLoaded.emit(project)
        return project

    def run_analysis(self, config_dict: dict, project_folder: str) -> None:
        """Build a `Config` from `config_dict` and run it on a background thread.

        `io.project_folder` is always forced to `project_folder` — the GUI
        never lets the config's own idea of the project folder win, even if
        the submitted dict carries one.

        Args:
            config_dict: Nested `{group: {field: value}}` mapping, as
                assembled by `NewAnalysisPage.qml`'s `collectConfig()`.
            project_folder: The current project's folder on disk.
        """
        config_dict = coerce_config_values(config_dict)
        config_dict["io"] = dict(config_dict.get("io") or {})
        config_dict["io"]["project_folder"] = str(project_folder)
        config_dict["version"] = Config.version

        try:
            config = Config.from_dict(config_dict)
        except ValueError as ve:
            self.invalidConfig.emit(str(ve))
            return

        run = Run()
        config_path = Path(project_folder) / "configs" / f"run_{run.id}.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config.export(config_path)

        worker = RunWorker(run, config, str(config_path), self)
        worker.stepChanged.connect(self.runStepChanged)
        worker.finished_ok.connect(self.runCompleted)
        worker.failed.connect(self.runFailed)
        self._run_worker = worker
        worker.start()

        self.runStarted.emit(run.id)
