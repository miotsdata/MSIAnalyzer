from PySide6.QtCore import Property, QObject

from msianalyzer.core.project.project import Project


class ProjectModel(QObject):
    def __init__(self, project: Project, parent=None) -> None:
        super().__init__()
        self._project = project

    @Property(str)
    def name(self) -> str:
        return self._project.name

    @Property(str)
    def uuid(self) -> str:
        return self._project.uuid

    @Property(dict)
    def runs(self) -> dict:
        return self._project.runs
