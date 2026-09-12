from msianalyzer.core.project.project import Project
from msianalyzer.gui.models.project import (
    ProjectModel,
    _format_minute_precision,
    _relative_to_folder,
)


def _project_with_run(folder, out_dir, config_path, start_date="2026-01-01 12:00:00"):
    p = Project(name="test_proj")
    p.runs["run-0"] = {
        "id": "run-0",
        "start_date": start_date,
        "config": {"io": {"out_dir": out_dir}},
        "config_path": config_path,
    }
    return ProjectModel(p, folder)


def test_relative_to_folder_strips_project_prefix():
    assert _relative_to_folder("/tmp/proj/output_0", "/tmp/proj") == "output_0"
    assert (
        _relative_to_folder("/tmp/proj/configs/run_0.yaml", "/tmp/proj")
        == "configs/run_0.yaml"
    )


def test_relative_to_folder_falls_back_to_absolute_when_not_inside():
    # An older/foreign path outside the current project folder shouldn't
    # be shown as a nonsensical relative fragment.
    assert _relative_to_folder("/elsewhere/output_0", "/tmp/proj") == "/elsewhere/output_0"


def test_relative_to_folder_handles_empty_inputs():
    assert _relative_to_folder("", "/tmp/proj") == ""
    assert _relative_to_folder("/tmp/proj/output_0", "") == "/tmp/proj/output_0"


def test_format_minute_precision_drops_seconds():
    assert _format_minute_precision("2026-01-01 12:34:56") == "2026-01-01 12:34"


def test_format_minute_precision_handles_microseconds():
    assert _format_minute_precision("2026-01-01 12:34:56.123456") == "2026-01-01 12:34"


def test_format_minute_precision_falls_back_on_unparseable_input():
    assert _format_minute_precision("not-a-date") == "not-a-date"


def test_format_minute_precision_handles_empty_string():
    assert _format_minute_precision("") == ""


def test_runs_list_display_fields_are_relative_and_minute_precision():
    model = _project_with_run(
        folder="/tmp/proj",
        out_dir="/tmp/proj/output_0",
        config_path="/tmp/proj/configs/run_0.yaml",
        start_date="2026-01-01 12:34:56",
    )

    entry = model.runsList[0]

    assert entry["out_dir"] == "/tmp/proj/output_0"
    assert entry["out_dir_display"] == "output_0"
    assert entry["config_path"] == "/tmp/proj/configs/run_0.yaml"
    assert entry["config_path_display"] == "configs/run_0.yaml"
    assert entry["start_date"] == "2026-01-01 12:34:56"
    assert entry["start_date_display"] == "2026-01-01 12:34"


def test_mzml_files_lists_only_mzml_extension_case_insensitively(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "b.mzML").write_bytes(b"")
    (data_dir / "a.mzml").write_bytes(b"")
    (data_dir / "c.xml").write_bytes(b"")
    (data_dir / "notes.txt").write_bytes(b"")

    p = Project(name="test_proj")
    model = ProjectModel(p, str(tmp_path))

    assert model.mzmlFiles == ["a.mzml", "b.mzML"]


def test_xml_files_lists_only_xml_extension(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "a.xml").write_bytes(b"")
    (data_dir / "b.mzml").write_bytes(b"")

    p = Project(name="test_proj")
    model = ProjectModel(p, str(tmp_path))

    assert model.xmlFiles == ["a.xml"]


def test_data_files_empty_without_data_dir():
    p = Project(name="test_proj")
    model = ProjectModel(p, "/tmp/does_not_exist_at_all")

    assert model.mzmlFiles == []
    assert model.xmlFiles == []


def test_data_files_empty_without_folder():
    p = Project(name="test_proj")
    model = ProjectModel(p, None)

    assert model.mzmlFiles == []
    assert model.xmlFiles == []
