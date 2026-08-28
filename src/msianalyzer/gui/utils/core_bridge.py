from PySide6.QtCore import QObject, Signal

from msianalyzer.core.project.project import Project


class CoreBridge(QObject):
    projectLoaded = Signal(Project)

    def load_project(self, path):
        project = Project.load(path)
        self.projectLoaded.emit(project)
