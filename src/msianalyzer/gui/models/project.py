from PySide6.QtCore import Property, QObject, Signal

from msianalyzer.core.project.project import Project


class ProjectModel(QObject):
    nameChanged = Signal()
    uuidChanged = Signal()
    runsChanged = Signal()
    folderChanged = Signal()

    def __init__(self, project: Project, folder: str | None = None, parent=None) -> None:
        super().__init__(parent)
        self._project = project
        self._folder = folder

    @Property(str, notify=nameChanged)
    def name(self) -> str:
        return self._project.name

    @Property(str, notify=uuidChanged)
    def uuid(self) -> str:
        return self._project.uuid

    @Property(str, notify=folderChanged)
    def folder(self) -> str:
        """The project's folder on disk (not stored on `Project` itself)."""
        return self._folder or ""

    @Property(dict, notify=runsChanged)
    def runs(self) -> dict:
        return self._project.runs

    @Property(list, notify=runsChanged)
    def runsList(self) -> list[dict]:
        """`self._project.runs` flattened to a list, newest first.

        Each entry: `{id, start_date, end_date, status, out_dir,
        config_path}`, read out of the `Run.to_dict()`-shaped nested dict
        every run is stored as.
        """
        entries = []
        for run_id, run in self._project.runs.items():
            config = run.get("config") or {}
            io = config.get("io") or {}
            entries.append(
                {
                    "id": run_id,
                    "start_date": run.get("start_date") or "",
                    "end_date": run.get("end_date") or "",
                    "status": run.get("status") or "",
                    "out_dir": io.get("out_dir") or "",
                    "config_path": run.get("config_path") or "",
                }
            )
        entries.sort(key=lambda e: e["start_date"], reverse=True)
        return entries
