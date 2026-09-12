from pathlib import Path

from PySide6.QtCore import Property, QObject, Signal, Slot

from msianalyzer.core.project.project import Project
from msianalyzer.gui.utils.formatting import format_minute_precision as _format_minute_precision

_DATA_FILE_SUFFIXES = {
    "mzml": (".mzml",),
    "xml": (".xml",),
}


def _relative_to_folder(path: str, folder: str) -> str:
    """`path` relative to `folder`, for display — e.g. an analysis' output
    directory shown as "output_0" instead of "/home/user/project/output_0".
    Falls back to `path` unchanged if either is empty or `path` isn't
    actually inside `folder` (an older/foreign path shouldn't be shown as
    a confusing/wrong relative fragment); never raises."""
    if not path or not folder:
        return path
    try:
        return str(Path(path).resolve().relative_to(Path(folder).resolve()))
    except (ValueError, OSError):
        return path


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

        Each entry: `{id, start_date, start_date_display, end_date, status,
        out_dir, out_dir_display, config_path, config_path_display}`, read
        out of the `Run.to_dict()`-shaped nested dict every run is stored
        as. The `_display` fields are what Project Home actually shows —
        `out_dir`/`config_path` relative to the project folder, `start_date`
        truncated to minute precision — while the plain fields stay the
        full absolute path / timestamp, e.g. for a "copy full path" action.
        """
        entries = []
        for run_id, run in self._project.runs.items():
            config = run.get("config") or {}
            io = config.get("io") or {}
            start_date = run.get("start_date") or ""
            out_dir = io.get("out_dir") or ""
            config_path = run.get("config_path") or ""
            entries.append(
                {
                    "id": run_id,
                    "start_date": start_date,
                    "start_date_display": _format_minute_precision(start_date),
                    "end_date": run.get("end_date") or "",
                    "status": run.get("status") or "",
                    "out_dir": out_dir,
                    "out_dir_display": _relative_to_folder(out_dir, self._folder),
                    "config_path": config_path,
                    "config_path_display": _relative_to_folder(config_path, self._folder),
                }
            )
        entries.sort(key=lambda e: e["start_date"], reverse=True)
        return entries

    @Slot(str, result=str)
    def deleteRun(self, run_id: str) -> str:
        """Delete one analysis — its output folder, its config file, and its
        `runsList` entry — then re-export `.msianalyzer.yml` so the removal
        persists. Project Home's right-click "Delete analysis" action.

        Returns:
            `""` on success (and `runsChanged` is emitted so the list
            updates immediately); otherwise an error message to show the
            user, with nothing on disk or in `runsList` changed.
        """
        try:
            self._project.delete_run(run_id, project_folder=self._folder)
        except (KeyError, OSError) as e:
            return str(e) or repr(e)
        if self._folder:
            self._project.export(Path(self._folder) / ".msianalyzer.yml")
        self.runsChanged.emit()
        return ""

    @Property(list, notify=folderChanged)
    def mzmlFiles(self) -> list[str]:
        """mzML filenames (no path) in the project's `data/` directory."""
        return self._list_data_files("mzml")

    @Property(list, notify=folderChanged)
    def xmlFiles(self) -> list[str]:
        """XML filenames (no path) in the project's `data/` directory."""
        return self._list_data_files("xml")

    def _list_data_files(self, kind: str) -> list[str]:
        if not self._folder:
            return []
        data_dir = Path(self._folder) / "data"
        if not data_dir.is_dir():
            return []
        suffixes = _DATA_FILE_SUFFIXES[kind]
        return sorted(
            p.name
            for p in data_dir.iterdir()
            if p.is_file() and p.suffix.lower() in suffixes
        )
