import pytest
import yaml
from pathlib import Path
from msianalyzer.core.project import Project, create_project_folder

# --- Tests for Project Class ---


def test_project_init():
    """Verify initial attributes are set correctly."""
    p = Project("test_proj")
    assert p.name == "test_proj"
    assert p.version == 1
    assert p.uuid is not None
    assert p.date is not None


def test_project_to_dict():
    """Verify serialization to dictionary converts datetime to string."""
    p = Project("test_proj")
    d = p.to_dict()

    assert d["name"] == "test_proj"
    assert isinstance(d["date"], str)
    assert d["uuid"] == p.uuid


def test_project_export_and_load(tmp_path: Path):
    """Test exporting to YAML and loading it back."""
    target_file = tmp_path / "my_project.yml"
    p = Project("alpha")
    p.export(target_file)

    assert target_file.exists()

    loaded_p = Project.load(target_file)
    assert loaded_p.name == "alpha"


def test_project_load_non_dict_yaml(tmp_path: Path):
    """Verify loading a YAML file that contains a scalar instead of a dict raises ValueError."""
    file_path = tmp_path / "bad.yml"
    file_path.write_text("just a plain string")

    with pytest.raises(ValueError, match="did not parse to a mapping"):
        Project.load(file_path)


def test_project_load_missing_keys(tmp_path: Path):
    """Verify loading YAML missing required fields raises ValueError."""
    file_path = tmp_path / "incomplete.yml"
    with open(file_path, "w") as f:
        yaml.safe_dump({"name": "test_proj"}, f)  # Missing 'uuid' and 'date'

    with pytest.raises(ValueError, match="missing"):
        Project.load(file_path)


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
    p = Project.load(yml_file)
    assert p.name == proj_name


def test_create_project_folder_already_exists(tmp_path: Path):
    """Verify error when target folder already exists."""
    target_dir = tmp_path / "existing_proj"
    target_dir.mkdir()

    with pytest.raises(FileExistsError):
        create_project_folder(path=target_dir, name="existing_proj")
