import pytest
import yaml
from pathlib import Path
from msianalyzer.core.project import Project, create_project_folder
from msianalyzer.core.run import Run
from hypothesis import given, settings, strategies as st
import string


# --- Tests for Project Class ---
def test_project_init():
    """Verify initial attributes are set correctly."""
    p = Project("test_proj")
    assert p.name == "test_proj"
    assert p.version == 1
    assert p.uuid is not None
    assert p.date is not None
    assert p.runs == {}


VALID_CHARS = string.ascii_letters + string.digits + "_- "
INVALID_CHARS = "".join(
    c for c in (chr(i) for i in range(32, 127)) if c not in VALID_CHARS
)


@settings(max_examples=50)
@given(st.text(alphabet=VALID_CHARS, min_size=1, max_size=50))
def test_validate_name_accepts_valid_characters(name):
    p = Project(name)  # should not raise


@settings(max_examples=50)
@given(
    st.text(alphabet=VALID_CHARS, min_size=0, max_size=20),
    st.sampled_from(INVALID_CHARS),
    st.text(alphabet=VALID_CHARS, min_size=0, max_size=20),
)
def test_validate_name_rejects_any_invalid_character(prefix, bad_char, suffix):
    name = prefix + bad_char + suffix
    with pytest.raises(
        ValueError,
        match=r"only letters, numbers, spaces, underscores and - \(minus\) are allowed\.",
    ):
        p = Project(name)


def test_validate_name_rejects_empty_string():
    with pytest.raises(ValueError):
        p = Project("")


def test_project_to_dict():
    """Verify serialization to dictionary converts datetime to string."""
    p = Project("test_proj")
    r = Run("test_config.yml")
    p.runs[r.id] = r
    d = p.to_dict()

    assert d["name"] == "test_proj"
    assert isinstance(d["date"], str)
    assert d["uuid"] == p.uuid


def test_project_export_and_load(tmp_path: Path):
    """Round-trip: name, uuid, date and runs all survive export + load.

    The uuid in particular must be preserved — it is used as the raw-DB
    command cache key (run_id), so a fresh id on every load would defeat
    parse / pixel-mapping caching.
    """
    target_file = tmp_path / "my_project.yml"
    p = Project("alpha")
    p.runs = {"run-1": {"id": "run-1", "status": "COMPLETED"}}
    p.export(target_file)

    assert target_file.exists()

    loaded_p = Project.load_from_yaml(target_file)
    assert loaded_p.name == "alpha"
    assert loaded_p.uuid == p.uuid
    assert loaded_p.date == p.date
    assert loaded_p.runs == p.runs


def test_project_load_non_dict_yaml(tmp_path: Path):
    """Verify loading a YAML file that contains a scalar instead of a dict raises ValueError."""
    file_path = tmp_path / "bad.yml"
    file_path.write_text("just a plain string")

    with pytest.raises(ValueError, match="not a valid project yaml file"):
        Project.load_from_yaml(file_path)


def test_project_load_missing_keys(tmp_path: Path):
    """Verify loading YAML missing required fields raises ValueError."""
    file_path = tmp_path / "incomplete.yml"
    with open(file_path, "w") as f:
        yaml.safe_dump({"name": "test_proj"}, f)  # Missing 'uuid' and 'date'

    with pytest.raises(ValueError, match="missing"):
        Project.load_from_yaml(file_path)


# --- Tests for Project.delete_run ---


def _run_entry(out_dir: Path, config_path: Path) -> dict:
    return {
        "id": "run-1",
        "status": "COMPLETED",
        "config": {"io": {"out_dir": str(out_dir)}},
        "config_path": str(config_path),
    }


def test_delete_run_removes_output_dir_config_file_and_entry(tmp_path: Path):
    out_dir = tmp_path / "output" / "run-1"
    out_dir.mkdir(parents=True)
    (out_dir / "analysis_run-1.db").write_bytes(b"data")
    config_path = tmp_path / "configs" / "run_run-1.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("io: {}")

    p = Project("demo")
    p.runs["run-1"] = _run_entry(out_dir, config_path)

    removed = p.delete_run("run-1")

    assert removed["id"] == "run-1"
    assert "run-1" not in p.runs
    assert not out_dir.exists()
    assert not config_path.exists()


def test_delete_run_tolerates_already_missing_files(tmp_path: Path):
    # Output folder/config already gone (moved, or never written) — the
    # entry should still be removed cleanly, not raise.
    out_dir = tmp_path / "output" / "run-1"
    config_path = tmp_path / "configs" / "run_run-1.yaml"

    p = Project("demo")
    p.runs["run-1"] = _run_entry(out_dir, config_path)

    p.delete_run("run-1")

    assert "run-1" not in p.runs


def test_delete_run_unknown_id_raises_and_leaves_runs_untouched():
    p = Project("demo")
    p.runs["run-1"] = _run_entry(Path("/tmp/x"), Path("/tmp/y"))

    with pytest.raises(KeyError):
        p.delete_run("does-not-exist")

    assert "run-1" in p.runs


def test_delete_run_resolves_relative_paths_against_project_folder(tmp_path: Path):
    (tmp_path / "output" / "run-1").mkdir(parents=True)
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "run_run-1.yaml").write_text("io: {}")

    p = Project("demo")
    p.runs["run-1"] = _run_entry(
        Path("output/run-1"), Path("configs/run_run-1.yaml")
    )

    p.delete_run("run-1", project_folder=tmp_path)

    assert not (tmp_path / "output" / "run-1").exists()
    assert not (tmp_path / "configs" / "run_run-1.yaml").exists()


def test_delete_run_does_not_touch_other_runs_or_parsed_raw_dbs(tmp_path: Path):
    (tmp_path / "parsed").mkdir()
    (tmp_path / "parsed" / "sample.db").write_bytes(b"raw")
    out_dir_1 = tmp_path / "output" / "run-1"
    out_dir_1.mkdir(parents=True)
    out_dir_2 = tmp_path / "output" / "run-2"
    out_dir_2.mkdir(parents=True)

    p = Project("demo")
    p.runs["run-1"] = _run_entry(out_dir_1, tmp_path / "c1.yaml")
    p.runs["run-2"] = _run_entry(out_dir_2, tmp_path / "c2.yaml")

    p.delete_run("run-1")

    assert "run-2" in p.runs
    assert out_dir_2.exists()
    assert (tmp_path / "parsed" / "sample.db").exists()


# --- Tests for create_project_folder ---
def test_create_project_folder_success(tmp_path: Path):
    """Verify folder structure and project file creation."""
    proj_name = "demo_proj"
    target_dir = tmp_path / proj_name

    create_project_folder(path=target_dir, name=proj_name)

    # Check root directory exists
    assert target_dir.is_dir()

    # Check expected subdirectories
    for subdir in ["output", "data", "configs"]:
        assert (target_dir / subdir).is_dir()

    # Check project YAML file
    yml_file = target_dir / ".msianalyzer.yml"
    assert yml_file.is_file()

    # Check project file can be reloaded
    p = Project.load_from_yaml(yml_file)
    assert p.name == proj_name


def test_create_project_folder_already_exists(tmp_path: Path):
    """Verify error when target folder already exists."""
    target_dir = tmp_path / "existing_proj"
    target_dir.mkdir()

    with pytest.raises(FileExistsError):
        create_project_folder(path=target_dir, name="existing_proj")
