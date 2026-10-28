from pathlib import Path

from PySide6.QtCore import Property, QObject

from msianalyzer.core import analysis_db
from msianalyzer.gui.utils.formatting import format_minute_precision


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

    @Property(QObject, constant=True)
    def project(self) -> QObject:
        """The owning project's model — for navigating back to it
        (`Router.showProjectHomeRequested`)."""
        return self._project_model

    @Property(str, constant=True)
    def projectName(self) -> str:
        return self._project_model.name if self._project_model else ""

    @Property(str, constant=True)
    def outDir(self) -> str:
        """The run's output folder in this project folder on this machine:
        stored relative to the project, or absolute from before the
        project moved / from another OS (see `resolve_in_project`)."""
        io = (self._run_dict.get("config") or {}).get("io") or {}
        out_dir = io.get("out_dir") or ""
        if self._project_model is None:
            return out_dir
        return self._project_model.resolve_path(out_dir)

    @Property(bool, constant=True)
    def outDirMissing(self) -> bool:
        """True when `outDir` can't be found here (moved/deleted, or the
        project folder was copied without its `output/`), so the workspace
        can say so instead of showing empty tabs."""
        return not self.outDir or not Path(self.outDir).is_dir()

    @Property(str, constant=True)
    def analysisType(self) -> str:
        """`"untargeted"` (default) or `"targeted"` — read straight from
        the saved config, same as `Config.analysis_type`. Drives which
        `AnalysisPage.qml` tabs show (e.g. `CalibrationSection` only for
        `"targeted"`)."""
        config = self._run_dict.get("config") or {}
        return config.get("analysis_type") or "untargeted"

    @Property(str, constant=True)
    def analysisDbPath(self) -> str:
        out_dir = self.outDir
        if not out_dir:
            return ""
        analysis_cfg = (self._run_dict.get("config") or {}).get("analysis") or {}
        db_name = analysis_cfg.get("db_name")
        return str(analysis_db.analysis_db_path(out_dir, self._run_id, db_name))

    @Property("QVariantMap", constant=True)
    def runConfig(self) -> dict:
        """The run's saved config, every group (`Config.to_dict()` shape)."""
        return dict(self._run_dict.get("config") or {})

    @Property("QVariantMap", constant=True)
    def targetListConfig(self) -> dict:
        """The run's `target_list` config section (polarity, adducts,
        match_ppm, paths), to pre-fill post-run matching (ADR 60)."""
        return dict((self._run_dict.get("config") or {}).get("target_list") or {})

    @Property(str, constant=True)
    def startDate(self) -> str:
        return self._run_dict.get("start_date") or ""

    @Property(str, constant=True)
    def startDateDisplay(self) -> str:
        """`startDate` truncated to minute precision, for the Analysis
        workspace header — same convention as Project Home's run list."""
        return format_minute_precision(self.startDate)
