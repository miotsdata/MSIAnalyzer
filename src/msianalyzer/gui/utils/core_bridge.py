from PySide6.QtCore import QObject, Signal

from msianalyzer.core.project.project import Project


class CoreBridge(QObject):
    projectLoaded = Signal(Project)
    invalidProjectPath = Signal(str)

    def load_project(self, path):
        try:
            project = Project.load_from_yaml(path)
        except ValueError as ve:
            self.invalidProjectPath.emit(str(ve))
        except FileNotFoundError:
            self.invalidProjectPath.emit(f"{path} does not exists.")
        else:
            self.projectLoaded.emit(project)
