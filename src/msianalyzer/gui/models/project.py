from PySide6.QtCore import Property, QObject, Signal

from msianalyzer.core.project.project import Project


class ProjectModel(QObject):
    nameChanged = Signal()
    uuidChanged = Signal()
    runsChanged = Signal()

    def __init__(self, project: Project, parent=None) -> None:
        super().__init__(parent)
        self._project = project

    @Property(str, notify=nameChanged)
    def name(self) -> str:
        return self._project.name

    @Property(str, notify=uuidChanged)
    def uuid(self) -> str:
        return self._project.uuid

    @Property(dict, notify=runsChanged)
    def runs(self) -> dict:
        return self._project.runs
