import pytest
from pathlib import Path
import yaml
import tomllib
from unittest.mock import patch, MagicMock

from msianalyzer.core.config.config import (
    IOConfig,
    MS1Config,
    CentroidConfig,
    PeakConfig,
    AlignMzSamples,
    GroupMs2Config,
    PurityConfig,
    AnnotateConfig,
    ConsensusConfig,
    ReportConfig,
    H5adConfig,
    NormalizationConfig,
    AnalysisConfig,
    Config,
    GROUPS,
    create_config_file,
    NotInProjectFolderError,
)


# ===========================================================================
# Dataclass Unit Tests
# ===========================================================================


def test_io_config_post_init_converts_strings_to_paths():
    """Verify that string inputs in IOConfig are coerced into Path objects."""
    io = IOConfig(
        project_folder="/tmp/project",
        mzml_paths=["raw1.mzml", "raw2.mzml"],
        xml_paths=["meta.xml"],
        db_paths=["scans.db"],
        out_dir="results",
    )

    assert isinstance(io.project_folder, Path)
    assert all(isinstance(p, Path) for p in io.mzml_paths)
    assert all(isinstance(p, Path) for p in io.xml_paths)
    assert all(isinstance(p, Path) for p in io.db_paths)
    assert isinstance(io.out_dir, Path)


def test_io_config_resolve_paths(tmp_path: Path):
    """Verify relative paths are resolved against project_folder while absolute paths stay unchanged."""
    proj_dir = (tmp_path / "my_project").resolve()
    proj_dir.mkdir()

    abs_mzml = (tmp_path / "external.mzml").resolve()

    io = IOConfig(
        project_folder=proj_dir,
        mzml_paths=["data/sample1.mzml", abs_mzml],
        xml_paths=["configs/meta.xml"],
        db_paths=["db/ms1.db"],
        out_dir="output",
    )

    io.resolve_paths()

    assert io.out_dir == (proj_dir / "output").resolve()
    assert io.mzml_paths[0] == (proj_dir / "data/sample1.mzml").resolve()
    assert io.mzml_paths[1] == abs_mzml  # Absolute path preserved
    assert io.xml_paths[0] == (proj_dir / "configs/meta.xml").resolve()
    assert io.db_paths[0] == (proj_dir / "db/ms1.db").resolve()


def test_io_config_raw_db_paths_default_and_override(tmp_path: Path):
    """raw_db_paths falls back to <project>/parsed/<stem>.db per sample, and
    honours explicit db_paths entries."""
    io = IOConfig(
        project_folder=tmp_path / "proj",
        mzml_paths=["a.mzML", "b.mzML"],
        xml_paths=[],
        db_paths=[tmp_path / "shared" / "a_raw.db"],  # only the first is explicit
        out_dir="out",
    )

    raw = io.raw_db_paths()

    assert raw[0] == tmp_path / "shared" / "a_raw.db"
    assert raw[1] == tmp_path / "proj" / "parsed" / "b.db"
    # the default location is NOT inside out_dir
    assert io.out_dir not in raw[1].parents


def test_default_dataclass_initializations():
    """Verify default values for all sub-config dataclasses."""
    assert MS1Config().chunk_size == 2000
    assert CentroidConfig().baseline_method == "local"
    assert PeakConfig().peak_height_threshold == 1000.0
    assert AlignMzSamples().align_ppm == 5.0
    assert GroupMs2Config().assoc_ppm == 10.0
    assert GroupMs2Config().include_unmatched is True
    assert PurityConfig().enabled is True
    assert PurityConfig().ppm_precursor_match == 20.0
    assert PurityConfig().use_next_ms1 is True
    assert PurityConfig().max_interpixel_gap_sec is None
    assert PurityConfig().precursor_confirm_ppm == 25.0
    assert PurityConfig().precursor_snap_ppm == 15.0
    assert AnnotateConfig().min_purity is None
    assert ConsensusConfig().enabled is True
    assert ConsensusConfig().target_peaks == 10
    assert ReportConfig().enabled is True
    assert ReportConfig().purity_cutoff == 0.8
    assert AnnotateConfig().library_path is None
    assert AnnotateConfig().noise_threshold == 0.01
    assert AnnotateConfig().candidate_ppm == 10.0
    assert AnnotateConfig().store_filtered_spectra is True
    # library_path may be a single path or a list of them
    assert AnnotateConfig(library_path=["a.db", "b.db"]).library_path == ["a.db", "b.db"]
    assert H5adConfig().scan_handling == "average"
    assert NormalizationConfig().enabled is True
    assert AnalysisConfig().db_name is None


# ===========================================================================
# Config Serialization & Deserialization Tests
# ===========================================================================


@pytest.fixture
def sample_io_config(tmp_path: Path) -> IOConfig:
    return IOConfig(
        project_folder=tmp_path / "demo_proj",
        mzml_paths=[tmp_path / "data/raw.mzml"],
        xml_paths=[],
        db_paths=[tmp_path / "output/scans.db"],
        out_dir=tmp_path / "output",
    )


def test_config_to_dict_converts_paths_to_strings(sample_io_config: IOConfig):
    """Verify to_dict flattens Config into a nested dictionary with string paths."""
    config = Config(io=sample_io_config)
    d = config.to_dict()

    assert d["version"] == 13
    assert isinstance(d["io"]["project_folder"], str)
    assert isinstance(d["io"]["mzml_paths"][0], str)
    assert d["ms1"]["chunk_size"] == 2000


def test_config_from_dict_success(sample_io_config: IOConfig):
    """Verify creating Config from a valid dictionary."""
    raw_data = {
        "version": 13,
        "io": {
            "project_folder": str(sample_io_config.project_folder),
            "mzml_paths": [str(p) for p in sample_io_config.mzml_paths],
            "xml_paths": [],
            "db_paths": [str(p) for p in sample_io_config.db_paths],
            "out_dir": str(sample_io_config.out_dir),
        },
        "ms1": {"chunk_size": 1000},
    }

    config = Config.from_dict(raw_data)
    assert isinstance(config.io, IOConfig)
    assert config.ms1.chunk_size == 1000
    assert config.peak.peak_height_threshold == 1000.0  # Uses default


def test_config_from_dict_version_mismatch():
    """Verify ValueError is raised if config file version does not match expected version."""
    bad_data = {"version": 99, "io": {}}
    with pytest.raises(ValueError, match="Config file version 99 does not match"):
        Config.from_dict(bad_data)


def test_config_from_dict_missing_required_io_fields():
    """Verify TypeError inside dataclasses wraps cleanly into ValueError."""
    bad_data = {"version": 13, "io": {}}  # Missing required IO fields
    with pytest.raises(ValueError, match="Invalid or incomplete config"):
        Config.from_dict(bad_data)


# ===========================================================================
# File I/O Tests (YAML & TOML)
# ===========================================================================


def test_yaml_export_and_load_roundtrip(sample_io_config: IOConfig, tmp_path: Path):
    """Test exporting to YAML and loading it back."""
    yaml_file = tmp_path / "config.yaml"
    config = Config(io=sample_io_config)

    config.export(yaml_file)
    assert yaml_file.exists()

    loaded = Config.load(yaml_file)
    assert str(loaded.io.project_folder) == str(sample_io_config.project_folder)
    assert loaded.ms1.chunk_size == config.ms1.chunk_size

def test_to_yaml_appends_extension_if_missing(
    sample_io_config: IOConfig, tmp_path: Path
):
    """Verify to_yaml adds .yaml extension when given a path without suffix."""
    target_path = tmp_path / "config_file"
    config = Config(io=sample_io_config)

    config.to_yaml(target_path)
    assert (tmp_path / "config_file.yaml").exists()


def test_load_unsupported_extension(tmp_path: Path):
    """Verify load raises ValueError for unrecognized file formats."""
    invalid_file = tmp_path / "config.json"
    invalid_file.write_text("{}")

    with pytest.raises(ValueError, match="Unsupported config format"):
        Config.load(invalid_file)


def test_load_invalid_content(tmp_path: Path):
    """Verify load raises ValueError if YAML/TOML does not parse to a dictionary."""
    yaml_file = tmp_path / "scalar.yaml"
    yaml_file.write_text("just a plain string")

    with pytest.raises(ValueError, match="did not parse to a mapping"):
        Config.load(yaml_file)


# ===========================================================================
# Representation Test (__str__)
# ===========================================================================


def test_config_str_representation(sample_io_config: IOConfig):
    """Verify string formatting outputs formatted titles and key-value pairs."""
    config = Config(io=sample_io_config)
    output = str(config)

    assert "Config(version=13)" in output
    assert "input/output:" in output
    assert "detect centroids:" in output
    assert "group MS2:" in output
    assert "precursor purity:" in output
    assert "annotate MS2:" in output
    assert "MS2 consensus:" in output
    assert "summary report:" in output
    assert "prominence_factor" in output
    assert "assoc_ppm" in output
    assert "library_path" in output


# ===========================================================================
# Helper Function Tests: create_config_file
# ===========================================================================


def test_create_config_file_success(tmp_path: Path):
    """Verify create_config_file writes a valid configuration file."""
    proj_folder = tmp_path / "valid_project"
    proj_folder.mkdir()
    (proj_folder / ".msianalyzer.yml").touch()

    config_file = tmp_path / "pipeline.yaml"

    create_config_file(
        file=config_file,
        project_folder=proj_folder,
        mzml_files=[Path("data.mzml")],
    )

    assert config_file.exists()
    loaded = Config.load(config_file)
    assert loaded.io.mzml_paths == [Path("data.mzml")]


def test_create_config_file_already_exists_error(tmp_path: Path):
    """Verify FileExistsError is raised when file exists and force=False."""
    existing_file = tmp_path / "config.yaml"
    existing_file.touch()

    with pytest.raises(FileExistsError, match="File already exists"):
        create_config_file(file=existing_file, force=False)


def test_create_config_file_force_overwrite(tmp_path: Path):
    """Verify force=True overwrites an existing configuration file."""
    proj_folder = tmp_path / "valid_project"
    proj_folder.mkdir()
    (proj_folder / ".msianalyzer.yml").touch()

    config_file = tmp_path / "config.yaml"
    config_file.write_text("old_data: True")

    create_config_file(
        file=config_file,
        project_folder=proj_folder,
        force=True,
    )

    loaded = Config.load(config_file)
    assert loaded.version == 13


def test_create_config_file_nonexistent_project_folder(tmp_path: Path):
    """Verify FileNotFoundError is raised if user passes a non-existent project_folder."""
    config_file = tmp_path / "config.yaml"
    fake_proj = tmp_path / "ghost_folder"

    with pytest.raises(FileNotFoundError, match="Project folder not found"):
        create_config_file(file=config_file, project_folder=fake_proj)


def test_create_config_file_invalid_project_folder(tmp_path: Path):
    """Verify NotInProjectFolderError is raised if .msianalyzer.yml marker is missing."""
    config_file = tmp_path / "config.yaml"
    empty_folder = tmp_path / "not_a_project"
    empty_folder.mkdir()

    with pytest.raises(
        NotInProjectFolderError, match="No project found at provided folder"
    ):
        create_config_file(file=config_file, project_folder=empty_folder)
