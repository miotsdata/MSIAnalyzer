from pathlib import Path

from msianalyzer.core import analysis_db
from msianalyzer.gui.models.analysis import AnalysisModel
from msianalyzer.gui.models.project import ProjectModel


def test_analysis_model_basic_properties(project):
    project_model = ProjectModel(project, "/tmp/proj")
    run_dict = {
        "start_date": "2026-01-01 12:00:00",
        "config": {
            "io": {"out_dir": "/tmp/proj/output/run1"},
            "analysis": {"db_name": None},
        },
    }
    model = AnalysisModel(project_model, "run-123", run_dict)

    assert model.runId == "run-123"
    assert model.projectName == project.name
    assert model.outDir == "/tmp/proj/output/run1"
    assert model.startDate == "2026-01-01 12:00:00"
    assert model.startDateDisplay == "2026-01-01 12:00"
    assert model.analysisDbPath == str(
        analysis_db.analysis_db_path("/tmp/proj/output/run1", "run-123", None)
    )


def test_analysis_model_explicit_db_name(project):
    project_model = ProjectModel(project, "/tmp/proj")
    run_dict = {
        "config": {
            "io": {"out_dir": "/tmp/proj/output/run1"},
            "analysis": {"db_name": "custom.db"},
        },
    }
    model = AnalysisModel(project_model, "run-123", run_dict)

    assert model.analysisDbPath == str(Path("/tmp/proj/output/run1") / "custom.db")


def test_analysis_model_missing_out_dir_yields_empty_db_path(project):
    project_model = ProjectModel(project, "/tmp/proj")
    model = AnalysisModel(project_model, "run-123", {})

    assert model.outDir == ""
    assert model.analysisDbPath == ""


def test_analysis_model_no_project_model_yields_empty_project_name():
    model = AnalysisModel(None, "run-123", {})
    assert model.projectName == ""


def test_analysis_model_analysis_type_defaults_to_untargeted(project):
    project_model = ProjectModel(project, "/tmp/proj")
    model = AnalysisModel(project_model, "run-123", {})
    assert model.analysisType == "untargeted"


def test_analysis_model_analysis_type_reads_targeted(project):
    project_model = ProjectModel(project, "/tmp/proj")
    run_dict = {"config": {"analysis_type": "targeted"}}
    model = AnalysisModel(project_model, "run-123", run_dict)
    assert model.analysisType == "targeted"


def test_out_dir_written_before_project_moved_resolves_into_project(project, tmp_path):
    # "/data/Projects/P/output/run1" stored on Linux, project now opened
    # from the NAS / Windows: the run's output is found in this folder.
    (tmp_path / "output" / "run1").mkdir(parents=True)
    project_model = ProjectModel(project, str(tmp_path))
    run_dict = {"config": {"io": {"out_dir": "/data/Projects/P/output/run1"}}}
    model = AnalysisModel(project_model, "run-123", run_dict)

    assert model.outDir == str(tmp_path / "output" / "run1")
    assert not model.outDirMissing


def test_relative_out_dir_and_missing_flag(project, tmp_path):
    project_model = ProjectModel(project, str(tmp_path))
    model = AnalysisModel(project_model, "r", {"config": {"io": {"out_dir": "output/run1"}}})

    assert model.outDir == str(tmp_path / "output" / "run1")
    assert model.outDirMissing
