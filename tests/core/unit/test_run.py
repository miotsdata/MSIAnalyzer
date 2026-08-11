from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import sqlite3

import numpy as np
import pandas as pd
import pytest

from msianalyzer.core.run.run import Run, RunStatus, SampleResult

# ==============================================================================
# MOCK CONFIG & DUMMY OBJECTS
# ==============================================================================

DUMMY_XML_CONTENT = """<?xml version="1.0" encoding="UTF-8"?>
<raster startTime="2026-08-11T12:00:00Z">
  <image width="1" height="1">
    <pixel x="0" y="0" offset="0.0" duration="100.0"/>
  </image>
</raster>
"""


@dataclass
class MockIOConfig:
    project_folder: str
    out_dir: str
    mzml_paths: list
    xml_paths: list


@dataclass
class MockMS1Config:
    bin_size: float = 0.01


@dataclass
class MockCentroidConfig:
    min_snr: float = 3.0


@dataclass
class MockPeakConfig:
    filter_mad: bool = True
    filter_mad_log: bool = True
    filter_mad_nmads: float = 3.0
    peak_height_threshold: float = 100.0


@dataclass
class MockAlignConfig:
    sample_names: list | None = None
    align_ppm: float = 10.0
    mz_decimals: int = 4


@dataclass
class MockH5ADConfig:
    n_workers: int = 1


class DummyConfig:
    def __init__(self, tmp_path):
        self.io = MockIOConfig(
            project_folder=str(tmp_path),
            out_dir=str(tmp_path / "out"),
            mzml_paths=[tmp_path / "sample1.mzML", tmp_path / "sample2.mzML"],
            xml_paths=[tmp_path / "sample1.xml", tmp_path / "sample2.xml"],
        )
        self.ms1 = MockMS1Config()
        self.centroid = MockCentroidConfig()
        self.peak = MockPeakConfig()
        self.align = MockAlignConfig()
        self.h5ad = MockH5ADConfig()

    @classmethod
    def from_yaml(cls, path):
        return cls(Path(path).parent)


class DummyObjectWithToDict:
    def to_dict(self):
        return {"key": "value"}


# ==============================================================================
# TESTS FOR DATACLASSES & ENUMS
# ==============================================================================


def test_run_status_enum():
    assert RunStatus.RUNNING.value == 1
    assert RunStatus.COMPLETED.value == 0


def test_sample_result_dataclass(tmp_path):
    path = tmp_path / "test.db"
    peaks = np.array([100.1, 200.2])
    res = SampleResult(out_db_path=path, peaks_mzs=peaks)
    assert res.out_db_path == path
    np.testing.assert_array_equal(res.peaks_mzs, peaks)


# ==============================================================================
# TESTS FOR Run CLASS INITIALIZATION AND TO_DICT
# ==============================================================================


def test_run_init():
    run = Run("test_config.yml")
    assert isinstance(run.id, str)
    assert run.start_date is None
    assert run.end_date is None
    assert run.status == RunStatus.RUNNING
    assert run.config is None
    assert run.project is None


def test_run_to_dict_conversions():
    run = Run("test_config.yml")
    run.start_date = datetime(2026, 1, 1, 12, 0, 0)
    run.end_date = datetime(2026, 1, 1, 12, 30, 0)

    run.custom_list = [DummyObjectWithToDict(), "simple_string", 42]

    d = run.to_dict()

    assert d["id"] == run.id
    assert d["start_date"] == "2026-01-01 12:00:00"
    assert d["end_date"] == "2026-01-01 12:30:00"
    assert d["status"] == RunStatus.RUNNING
    assert d["custom_list"] == [{"key": "value"}, "simple_string", 42]


# ==============================================================================
# TESTS FOR is_command_already_run
# ==============================================================================


def test_is_command_already_run_returns_true(tmp_path):
    db_path = tmp_path / "test.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE commands (id TEXT, command_name TEXT, run_id TEXT)"
        )
        conn.execute(
            "INSERT INTO commands VALUES ('cmd1', 'parse_spectra', 'proj_123')"
        )
        conn.commit()

    assert Run.is_command_already_run("parse_spectra", "proj_123", db_path) is True


def test_is_command_already_run_returns_false(tmp_path):
    db_path = tmp_path / "test.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE commands (id TEXT, command_name TEXT, run_id TEXT)"
        )
        conn.commit()

    assert Run.is_command_already_run("parse_spectra", "proj_123", db_path) is False


# ==============================================================================
# TESTS FOR _process_one_sample (USING mocker)
# ==============================================================================


def test_process_one_sample_fresh_run_with_mad_filter(mocker, tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    mzml_path = tmp_path / "sample.mzML"
    xml_path = tmp_path / "sample.xml"
    mzml_path.touch()
    xml_path.write_text(DUMMY_XML_CONTENT, encoding="utf-8")

    config = DummyConfig(tmp_path)
    run_id = "proj_test"

    # Set up patches via mocker at the exact import location in run.py
    mock_mzml_parser_cls = mocker.patch("msianalyzer.core.run.run.MzmlParser")
    mock_log_cmd = mocker.patch("msianalyzer.core.run.run.log_command")
    mock_parse_xml = mocker.patch(
        "msianalyzer.core.run.run.parse_raster_xml",
        return_value=(pd.DataFrame({"x": [1, 2]}), None),
    )
    mock_map_pixels = mocker.patch("msianalyzer.core.run.run.map_pixels_to_db")
    mock_get_avg = mocker.patch(
        "msianalyzer.core.run.run.get_average_ms1_spectra",
        return_value=(np.array([100.0, 200.0]), np.array([10.0, 20.0])),
    )
    mocker.patch("msianalyzer.core.run.run.save_aggregated_spectra")
    mocker.patch("msianalyzer.core.run.run.load_aggregated_spectra")
    mock_detect_centroids = mocker.patch(
        "msianalyzer.core.run.run.detect_ms1_centroids",
        return_value=(np.array([100.0, 200.0]), np.array([10.0, 20.0])),
    )
    mock_filter_mad = mocker.patch(
        "msianalyzer.core.run.run.filter_intensities_mad",
        return_value=(np.array([100.0]), np.array([20.0])),
    )

    mock_fig = mocker.MagicMock()
    mock_plotter = mocker.patch("msianalyzer.core.run.run.Plotter")
    mock_plotter.return_value.plot_spectra.return_value = mock_fig

    mocker.patch.object(Run, "is_command_already_run", return_value=False)

    res = Run._process_one_sample(mzml_path, xml_path, config, run_id)

    assert isinstance(res, SampleResult)
    assert res.out_db_path == out_dir / "sample.db"
    np.testing.assert_array_equal(res.peaks_mzs, np.array([100.0]))

    # Verify parser and processing functions were called
    mock_mzml_parser_cls.return_value.parse.assert_called_once()
    mock_map_pixels.assert_called_once()
    mock_get_avg.assert_called_once()
    mock_detect_centroids.assert_called_once()
    mock_filter_mad.assert_called_once()

    # Verify plot export was called on the mock figure
    mock_fig.write_html.assert_called_once_with(out_dir / "sample_filtered_ms1.html")

    # Verify real CSV file creation (pandas wasn't mocked, so to_csv actually wrote to disk)
    assert (out_dir / "sample_peaks_data.csv").exists()


def test_process_one_sample_cached_run(mocker, tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    mzml_path = tmp_path / "sample.mzML"
    xml_path = tmp_path / "sample.xml"
    db_path = out_dir / "sample.db"

    mzml_path.touch()
    xml_path.write_text(DUMMY_XML_CONTENT, encoding="utf-8")
    db_path.touch()

    # Create dummy files to pass existence checks
    (out_dir / "sample_filtered_ms1.html").touch()
    (out_dir / "sample_peaks_data.csv").touch()

    config = DummyConfig(tmp_path)
    run_id = "proj_test"

    mock_load_agg = mocker.patch(
        "msianalyzer.core.run.run.load_aggregated_spectra",
        side_effect=[(np.array([100.0]), np.array([50.0]))],
    )
    mocker.patch("msianalyzer.core.run.run.Plotter")
    mocker.patch.object(Run, "is_command_already_run", return_value=True)

    res = Run._process_one_sample(mzml_path, xml_path, config, run_id)

    assert res.out_db_path == db_path
    np.testing.assert_array_equal(res.peaks_mzs, np.array([100.0]))
    mock_load_agg.assert_called_once_with(
        db_path, run_id=run_id, command_name="filter_spectra"
    )


def test_process_one_sample_peak_threshold_filtering(mocker, tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    mzml_path = tmp_path / "sample.mzML"
    xml_path = tmp_path / "sample.xml"
    mzml_path.touch()
    xml_path.write_text(DUMMY_XML_CONTENT, encoding="utf-8")

    config = DummyConfig(tmp_path)
    config.peak.filter_mad = False
    config.peak.peak_height_threshold = 15.0

    run_id = "proj_test"

    mock_mad = mocker.patch("msianalyzer.core.run.run.filter_intensities_mad")
    mocker.patch("msianalyzer.core.run.run.save_aggregated_spectra")
    mocker.patch("msianalyzer.core.run.run.log_command")
    mocker.patch.object(Run, "is_command_already_run", return_value=False)
    mocker.patch("msianalyzer.core.run.run.MzmlParser")
    mocker.patch(
        "msianalyzer.core.run.run.parse_raster_xml",
        return_value=(pd.DataFrame(), None),
    )
    mocker.patch("msianalyzer.core.run.run.map_pixels_to_db")
    mocker.patch(
        "msianalyzer.core.run.run.get_average_ms1_spectra",
        return_value=(np.array([10.0, 20.0]), np.array([100.0, 200.0])),
    )
    mocker.patch(
        "msianalyzer.core.run.run.detect_ms1_centroids",
        return_value=(
            np.array([100.0, 200.0, 300.0]),
            np.array([10.0, 20.0, 5.0]),  # 10 and 5 are < threshold 15.0
        ),
    )
    mocker.patch("msianalyzer.core.run.run.Plotter")

    res = Run._process_one_sample(mzml_path, xml_path, config, run_id)

    mock_mad.assert_not_called()
    np.testing.assert_array_equal(res.peaks_mzs, np.array([200.0]))


# ==============================================================================
# TESTS FOR run_core (USING mocker)
# ==============================================================================


def test_run_core_executes_successfully(mocker, tmp_path):
    run = Run("test_config.yml")
    config = DummyConfig(tmp_path)
    out_dir = Path(config.io.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    project_mock = mocker.MagicMock()
    project_mock.uuid = "proj_uuid_123"

    run.config = config
    run.project = project_mock

    res1 = SampleResult(
        out_db_path=out_dir / "sample1.db", peaks_mzs=np.array([100.0, 200.0])
    )
    res2 = SampleResult(
        out_db_path=out_dir / "sample2.db", peaks_mzs=np.array([100.0, 300.0])
    )

    mock_align = mocker.patch(
        "msianalyzer.core.run.run.align_mz_across_samples",
        return_value=pd.DataFrame(index=[100.0, 200.0, 300.0]),
    )
    mock_adata = mocker.MagicMock()
    mock_create_adata = mocker.patch(
        "msianalyzer.core.run.run.create_spatial_adata", return_value=mock_adata
    )

    # Mock ProcessPoolExecutor to avoid pickling MagicMocks across processes
    mock_executor = mocker.MagicMock()
    mock_executor.__enter__.return_value.map.return_value = [res1, res2]
    mocker.patch(
        "msianalyzer.core.run.run.ProcessPoolExecutor",
        return_value=mock_executor,
    )

    run.run_core()

    mock_align.assert_called_once()
    assert (out_dir / "aligned_mzs.csv").exists()

    assert mock_create_adata.call_count == 2
    assert mock_adata.write_h5ad.call_count == 2


def test_run_core_skips_existing_outputs(mocker, tmp_path):
    run = Run("test_config.yml")
    config = DummyConfig(tmp_path)
    out_dir = Path(config.io.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Pre-create output files
    aligned_csv = out_dir / "aligned_mzs.csv"
    pd.DataFrame(index=[100.0, 200.0]).to_csv(aligned_csv)

    (out_dir / "sample1.h5ad").touch()
    (out_dir / "sample2.h5ad").touch()

    project_mock = mocker.MagicMock()
    project_mock.uuid = "proj_uuid_123"

    run.config = config
    run.project = project_mock

    res1 = SampleResult(out_db_path=out_dir / "sample1.db", peaks_mzs=np.array([100.0]))
    res2 = SampleResult(out_db_path=out_dir / "sample2.db", peaks_mzs=np.array([100.0]))

    mock_align = mocker.patch("msianalyzer.core.run.run.align_mz_across_samples")
    mock_create_adata = mocker.patch("msianalyzer.core.run.run.create_spatial_adata")

    # Mock ProcessPoolExecutor to run synchronously without pickling
    mock_executor = mocker.MagicMock()
    mock_executor.__enter__.return_value.map.return_value = [res1, res2]
    mocker.patch(
        "msianalyzer.core.run.run.ProcessPoolExecutor",
        return_value=mock_executor,
    )

    run.run_core()

    mock_align.assert_not_called()
    mock_create_adata.assert_not_called()


# ==============================================================================
# TESTS FOR start METHOD (USING mocker)
# ==============================================================================


def test_start_method_orchestration(mocker, tmp_path):
    run = Run("test_config.yml")
    config_file = tmp_path / "config.yml"
    config_file.touch()

    mock_cfg = DummyConfig(tmp_path)
    mock_config_cls = mocker.patch("msianalyzer.core.run.run.Config")
    mock_config_cls.from_yaml.return_value = mock_cfg

    mock_proj_inst = mocker.MagicMock()
    mock_proj_inst.runs = {}
    mock_project_cls = mocker.patch("msianalyzer.core.run.run.Project")
    mock_project_cls.load.return_value = mock_proj_inst

    mock_run_core = mocker.patch.object(run, "run_core")

    run.start(config_file)

    assert run.status == RunStatus.COMPLETED
    assert isinstance(run.start_date, datetime)
    assert isinstance(run.end_date, datetime)
    assert run.id in mock_proj_inst.runs

    mock_run_core.assert_called_once()
    assert mock_proj_inst.export.call_count == 2