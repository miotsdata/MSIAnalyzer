import os
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
    runSampleProgress = Signal(int, int)
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
        """Load the project rooted at `path`.

        Args:
            path: The project's folder on disk (the one containing
                `.msianalyzer.yml`), not the yaml file itself.
        """
        if not path:
            self.invalidProjectPath.emit("no file provided.")
            return

        yaml_path = Path(path) / ".msianalyzer.yml"
        try:
            project = Project.load_from_yaml(yaml_path)
        except ValueError as ve:
            self.invalidProjectPath.emit(str(ve))
        except FileNotFoundError:
            self.invalidProjectPath.emit(f"{path} does not exists.")
        else:
            # The open project's folder becomes the process's cwd for as
            # long as it stays open — matches the mental model of "running
            # from inside the project" the core pipeline (and the CLI it
            # was originally built for) already assumes in a few places
            # (e.g. `Project.load()`'s cwd-relative project-folder walk),
            # regardless of where the GUI itself was launched from.
            os.chdir(path)
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

        os.chdir(path)  # see load_project's own comment on why
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
        worker.sampleProgress.connect(self.runSampleProgress)
        worker.finished_ok.connect(self.runCompleted)
        worker.failed.connect(self.runFailed)
        self._run_worker = worker
        worker.start()

        self.runStarted.emit(run.id)
