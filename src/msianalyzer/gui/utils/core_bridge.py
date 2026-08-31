from PySide6.QtCore import QObject, Signal

from msianalyzer.core import project
from msianalyzer.core.project.project import Project, create_project_folder


class CoreBridge(QObject):
    projectLoaded = Signal(Project)
    invalidProjectPath = Signal(str)
    invalidCreateProjectName = Signal(str)
    invalidCreateProjectPath = Signal(str)

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
            path = path / name
            create_project_folder(path=path, name=name)
        except (OSError, PermissionError, FileExistsError) as e:
            self.invalidCreateProjectPath.emit(str(e))
            return None

        self.projectLoaded.emit(project)
        return project
