from PySide6.QtCore import Property, QObject

from msianalyzer.core import analysis_db


class AnalysisModel(QObject):
    """Identifies one run for the Analysis workspace's sections.

    Wraps a run's `Run.to_dict()`-shaped dict (as stored in
    `Project.runs[run_id]`) plus the owning project's model, and resolves
    the two paths every section needs: `outDir` and `analysisDbPath`.
    Sections read data through `AnalysisBridge`, passing these paths
    explicitly — this model carries no data itself.
    """

    def __init__(self, project_model, run_id: str, run_dict: dict, parent=None) -> None:
        super().__init__(parent)
        self._project_model = project_model
        self._run_id = run_id
        self._run_dict = run_dict or {}

    @Property(str, constant=True)
    def runId(self) -> str:
        return self._run_id

    @Property(str, constant=True)
    def projectName(self) -> str:
        return self._project_model.name if self._project_model else ""

    @Property(str, constant=True)
    def outDir(self) -> str:
        io = (self._run_dict.get("config") or {}).get("io") or {}
        return io.get("out_dir") or ""

    @Property(str, constant=True)
    def analysisDbPath(self) -> str:
        out_dir = self.outDir
        if not out_dir:
            return ""
        analysis_cfg = (self._run_dict.get("config") or {}).get("analysis") or {}
        db_name = analysis_cfg.get("db_name")
        return str(analysis_db.analysis_db_path(out_dir, self._run_id, db_name))

    @Property(str, constant=True)
    def startDate(self) -> str:
        return self._run_dict.get("start_date") or ""
