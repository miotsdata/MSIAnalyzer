from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

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
    db_paths: list = field(default_factory=list)

    def raw_db_paths(self) -> list[Path]:
        default_dir = Path(self.project_folder) / "parsed"
        return [
            self.db_paths[i]
            if i < len(self.db_paths)
            else default_dir / f"{Path(m).stem}.db"
            for i, m in enumerate(self.mzml_paths)
        ]


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
class MockGroupMs2Config:
    assoc_ppm: float = 10.0
    include_unmatched: bool = True
    default_isolation_half_width: float = 0.5
    precursor_only_tic_frac: float = 0.8
    precursor_only_mz_tol_da: float = 2.0
    flat_fragmentation_min_peaks: int = 3
    flat_fragmentation_cv_threshold: float = 0.2
    flat_fragmentation_min_rel_intensity: float = 0.01


@dataclass
class MockPurityConfig:
    enabled: bool = True
    ppm_precursor_match: float = 20.0
    default_half_window_da: float = 0.5
    min_rel_intensity: float = 0.01
    merge_ppm: float = 5.0
    use_next_ms1: bool = True
    max_interpixel_gap_sec: float | None = None


@dataclass
class MockAnnotateConfig:
    library_path: str | None = None
    noise_threshold: float = 0.01
    candidate_ppm: float = 10.0
    fragment_ppm: float = 10.0
    mz_power: float = 2.0
    int_power: float = 0.5
    min_matched_peaks: int = 1
    annotate_chimeric: bool = True
    store_filtered_spectra: bool = True
    batch_size: int = 200
    n_workers: int | None = 1


@dataclass
class MockConsensusConfig:
    enabled: bool = True
    target_peaks: int = 10
    neutral_purity: float = 0.5
    min_purity: float | None = None


@dataclass
class MockReportConfig:
    enabled: bool = True
    overlap_top_n: int = 30
    purity_cutoff: float = 0.8


@dataclass
class MockH5ADConfig:
    n_workers: int = 1


@dataclass
class MockAnalysisConfig:
    db_name: str | None = None


@dataclass
class MockNormalizationConfig:
    enabled: bool = True


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
        self.group_ms2 = MockGroupMs2Config()
        self.purity = MockPurityConfig()
        self.annotate = MockAnnotateConfig()
        self.consensus = MockConsensusConfig()
        self.report = MockReportConfig()
        self.h5ad = MockH5ADConfig()
        self.normalization = MockNormalizationConfig()
        self.analysis = MockAnalysisConfig()

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


def test_run_init_without_config_file():
    """`cli` constructs `Run()` with no arguments."""
    run = Run()
    assert isinstance(run.id, str)


def test_run_to_dict_conversions():
    run = Run("test_config.yml")
    run.start_date = datetime(2026, 1, 1, 12, 0, 0)
    run.end_date = datetime(2026, 1, 1, 12, 30, 0)
    run.config = DummyObjectWithToDict()
    run.project = object()  # back-reference must be dropped

    run.custom_list = [DummyObjectWithToDict(), "simple_string", 42]

    d = run.to_dict()

    assert d["id"] == run.id
    assert d["start_date"] == "2026-01-01 12:00:00"
    assert d["end_date"] == "2026-01-01 12:30:00"
    # enum -> name, and the whole thing is YAML-safe
    assert d["status"] == "RUNNING"
    assert d["config"] == {"key": "value"}
    assert "project" not in d
    assert d["custom_list"] == [{"key": "value"}, "simple_string", 42]

    import yaml

    yaml.safe_dump(d)  # must not raise


def test_run_to_dict_excludes_on_step_includes_config_path():
    run = Run("test_config.yml")
    run.config = DummyObjectWithToDict()
    run.config_path = "/tmp/config.yml"
    run.on_step = lambda step, status: None  # not YAML-safe; must be dropped

    d = run.to_dict()

    assert "on_step" not in d
    assert d["config_path"] == "/tmp/config.yml"

    import yaml

    yaml.safe_dump(d)  # must not raise


# ==============================================================================
# TESTS FOR is_command_already_run (raw database, project-scoped)
# ==============================================================================


def test_is_command_already_run_returns_true(tmp_path):
    db_path = tmp_path / "test.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE commands (id TEXT, command_name TEXT, run_id TEXT)")
        conn.execute("INSERT INTO commands VALUES ('cmd1', 'parse_spectra', 'proj_123')")
        conn.commit()

    assert Run.is_command_already_run("parse_spectra", "proj_123", db_path) is True


def test_is_command_already_run_returns_false(tmp_path):
    db_path = tmp_path / "test.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE commands (id TEXT, command_name TEXT, run_id TEXT)")
        conn.commit()

    assert Run.is_command_already_run("parse_spectra", "proj_123", db_path) is False


# ==============================================================================
# TESTS FOR _process_one_sample (USING mocker)
# ==============================================================================


def _patch_common(mocker):
    """Patch every collaborator of `_process_one_sample` and return the mocks."""
    m = {}
    m["MzmlParser"] = mocker.patch("msianalyzer.core.run.run.MzmlParser")
    m["log_command"] = mocker.patch("msianalyzer.core.run.run.log_command")
    m["parse_raster_xml"] = mocker.patch(
        "msianalyzer.core.run.run.parse_raster_xml",
        return_value=(pd.DataFrame({"x": [1, 2]}), None),
    )
    m["map_pixels_to_db"] = mocker.patch("msianalyzer.core.run.run.map_pixels_to_db")
    m["get_average_ms1_spectra"] = mocker.patch(
        "msianalyzer.core.run.run.get_average_ms1_spectra",
        return_value=(np.array([100.0, 200.0]), np.array([10.0, 20.0])),
    )
    m["save_aggregated_spectra"] = mocker.patch(
        "msianalyzer.core.run.run.save_aggregated_spectra"
    )
    m["load_aggregated_spectra"] = mocker.patch(
        "msianalyzer.core.run.run.load_aggregated_spectra"
    )
    m["detect_ms1_centroids"] = mocker.patch(
        "msianalyzer.core.run.run.detect_ms1_centroids",
        return_value=(np.array([100.0, 200.0]), np.array([10.0, 20.0])),
    )
    m["filter_intensities_mad"] = mocker.patch(
        "msianalyzer.core.run.run.filter_intensities_mad",
        return_value=(np.array([100.0]), np.array([20.0])),
    )
    m["Plotter"] = mocker.patch("msianalyzer.core.run.run.Plotter")
    m["analysis_db"] = mocker.patch("msianalyzer.core.run.run.analysis_db")
    m["analysis_db"].log_command.return_value = 1
    return m


def test_process_one_sample_fresh_run_with_mad_filter(mocker, tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    mzml_path = tmp_path / "sample.mzML"
    xml_path = tmp_path / "sample.xml"
    mzml_path.touch()
    xml_path.write_text(DUMMY_XML_CONTENT, encoding="utf-8")

    config = DummyConfig(tmp_path)
    m = _patch_common(mocker)
    m["analysis_db"].is_command_already_run.return_value = False
    mocker.patch.object(Run, "is_command_already_run", return_value=False)

    mock_fig = mocker.MagicMock()
    m["Plotter"].return_value.plot_spectra.return_value = mock_fig

    res = Run._process_one_sample(
        mzml_path,
        xml_path,
        1,
        tmp_path / "parsed" / "sample.db",
        config=config,
        run_id="proj_test",
        analysis_id="ana_test",
        analysis_db_path=out_dir / "analysis.db",
    )

    assert isinstance(res, SampleResult)
    assert res.out_db_path == tmp_path / "parsed" / "sample.db"
    np.testing.assert_array_equal(res.peaks_mzs, np.array([100.0]))

    m["MzmlParser"].return_value.parse.assert_called_once()
    m["map_pixels_to_db"].assert_called_once()
    m["get_average_ms1_spectra"].assert_called_once()
    m["detect_ms1_centroids"].assert_called_once()
    m["filter_intensities_mad"].assert_called_once()

    # three aggregated spectra written (avg, centroids, filtered), all to the analysis DB
    assert m["save_aggregated_spectra"].call_count == 3
    for call in m["save_aggregated_spectra"].call_args_list:
        assert call.kwargs["analysis_db_path"] == out_dir / "analysis.db"
        assert call.kwargs["sample_id"] == 1

    mock_fig.write_html.assert_called_once_with(out_dir / "sample_filtered_ms1.html")
    assert (out_dir / "sample_peaks_data.csv").exists()


def test_process_one_sample_cached_run(mocker, tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    mzml_path = tmp_path / "sample.mzML"
    xml_path = tmp_path / "sample.xml"
    db_path = tmp_path / "parsed" / "sample.db"

    mzml_path.touch()
    xml_path.write_text(DUMMY_XML_CONTENT, encoding="utf-8")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.touch()

    (out_dir / "sample_filtered_ms1.html").touch()
    (out_dir / "sample_peaks_data.csv").touch()

    config = DummyConfig(tmp_path)
    m = _patch_common(mocker)
    m["analysis_db"].is_command_already_run.return_value = True
    m["load_aggregated_spectra"].side_effect = [(np.array([100.0]), np.array([50.0]))]
    mocker.patch.object(Run, "is_command_already_run", return_value=True)

    res = Run._process_one_sample(
        mzml_path,
        xml_path,
        2,
        db_path,
        config=config,
        run_id="proj_test",
        analysis_id="ana_test",
        analysis_db_path=out_dir / "analysis.db",
    )

    assert res.out_db_path == db_path
    np.testing.assert_array_equal(res.peaks_mzs, np.array([100.0]))
    m["save_aggregated_spectra"].assert_not_called()
    m["load_aggregated_spectra"].assert_called_once_with(
        out_dir / "analysis.db",
        run_id="ana_test",
        command_name="filter_spectra",
        sample_id=2,
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

    m = _patch_common(mocker)
    m["analysis_db"].is_command_already_run.return_value = False
    m["detect_ms1_centroids"].return_value = (
        np.array([100.0, 200.0, 300.0]),
        np.array([10.0, 20.0, 5.0]),  # 10 and 5 are < threshold 15.0
    )
    mocker.patch.object(Run, "is_command_already_run", return_value=False)

    res = Run._process_one_sample(
        mzml_path,
        xml_path,
        1,
        tmp_path / "parsed" / "sample.db",
        config=config,
        run_id="proj_test",
        analysis_id="ana_test",
        analysis_db_path=out_dir / "analysis.db",
    )

    m["filter_intensities_mad"].assert_not_called()
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

    mock_adb = mocker.patch("msianalyzer.core.run.run.analysis_db")
    mock_adb.analysis_db_path.return_value = out_dir / "analysis_ana.db"
    mock_adb.register_sample.side_effect = [1, 2]
    mock_adb.is_command_already_run.return_value = False

    mock_run_grouper = mocker.patch(
        "msianalyzer.core.run.run.run_grouper",
        return_value=mocker.MagicMock(associations=[], feature_summary=[]),
    )
    mock_run_purity = mocker.patch(
        "msianalyzer.core.run.run.run_precursor_purity",
        return_value=mocker.MagicMock(
            n_scans=0, n_multi_peak=0, n_precursor_missing=0, n_interpolated=0
        ),
    )

    config.annotate.library_path = str(tmp_path / "lib.db")
    mock_run_annotation = mocker.patch(
        "msianalyzer.core.run.run.run_annotation",
        return_value=mocker.MagicMock(
            rows=[], n_scans_annotated=0, n_features_annotated=0
        ),
    )
    mock_run_consensus = mocker.patch(
        "msianalyzer.core.run.run.run_consensus",
        return_value=mocker.MagicMock(n_features=0, n_features_scored=0),
    )
    mock_build_report = mocker.patch(
        "msianalyzer.core.run.run.build_summary_report"
    )

    mock_align = mocker.patch(
        "msianalyzer.core.run.run.align_mz_across_samples",
        return_value=pd.DataFrame(index=[100.0, 200.0, 300.0]),
    )
    mock_adata = mocker.MagicMock()
    mock_create_adata = mocker.patch(
        "msianalyzer.core.run.run.create_spatial_adata", return_value=mock_adata
    )

    mock_norm_result = mocker.MagicMock(
        n_samples=2, n_pixels=10, median_tic=123.0
    )
    mock_norm_result.merged_path.name = "merged.h5ad"
    mock_run_tic_normalization = mocker.patch(
        "msianalyzer.core.run.run.run_tic_normalization",
        return_value=mock_norm_result,
    )

    mock_executor = mocker.MagicMock()
    mock_executor.__enter__.return_value.map.return_value = [res1, res2]
    mocker.patch(
        "msianalyzer.core.run.run.ProcessPoolExecutor", return_value=mock_executor
    )

    run.run_core()

    mock_adb.init_analysis_db.assert_called_once()
    assert mock_adb.register_sample.call_count == 2
    mock_adb.save_features.assert_called_once()

    mock_align.assert_called_once()
    assert (out_dir / "aligned_mzs.csv").exists()

    mock_run_grouper.assert_called_once()
    grouper_kwargs = mock_run_grouper.call_args.kwargs
    assert grouper_kwargs["assoc_ppm"] == config.group_ms2.assoc_ppm
    assert grouper_kwargs["align_ppm"] == config.align.align_ppm

    mock_run_purity.assert_called_once()
    assert mock_run_purity.call_args.args[1] is config.purity

    mock_run_annotation.assert_called_once()
    ann_args = mock_run_annotation.call_args
    assert ann_args.args[1] is config.annotate

    mock_run_consensus.assert_called_once()
    assert mock_run_consensus.call_args.args[1] is config.consensus
    mock_build_report.assert_called_once()
    assert mock_build_report.call_args.kwargs["out_dir"] == out_dir

    assert mock_create_adata.call_count == 2
    assert mock_adata.write_h5ad.call_count == 2

    mock_run_tic_normalization.assert_called_once()
    norm_kwargs = mock_run_tic_normalization.call_args
    assert norm_kwargs.kwargs["out_dir"] == out_dir
    sample_paths = norm_kwargs.args[0]
    assert sample_paths == {
        "sample1": out_dir / "sample1.h5ad",
        "sample2": out_dir / "sample2.h5ad",
    }


def test_run_core_skips_existing_outputs(mocker, tmp_path):
    run = Run("test_config.yml")
    config = DummyConfig(tmp_path)
    out_dir = Path(config.io.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    aligned_csv = out_dir / "aligned_mzs.csv"
    pd.DataFrame(index=[100.0, 200.0]).to_csv(aligned_csv)

    (out_dir / "sample1.h5ad").touch()
    (out_dir / "sample2.h5ad").touch()
    (out_dir / "summary_report.html").touch()

    project_mock = mocker.MagicMock()
    project_mock.uuid = "proj_uuid_123"

    run.config = config
    run.project = project_mock

    res1 = SampleResult(out_db_path=out_dir / "sample1.db", peaks_mzs=np.array([100.0]))
    res2 = SampleResult(out_db_path=out_dir / "sample2.db", peaks_mzs=np.array([100.0]))

    mock_adb = mocker.patch("msianalyzer.core.run.run.analysis_db")
    mock_adb.analysis_db_path.return_value = out_dir / "analysis_ana.db"
    mock_adb.register_sample.side_effect = [1, 2]
    mock_adb.is_command_already_run.return_value = True

    config.annotate.library_path = str(tmp_path / "lib.db")
    mock_align = mocker.patch("msianalyzer.core.run.run.align_mz_across_samples")
    mock_run_grouper = mocker.patch("msianalyzer.core.run.run.run_grouper")
    mock_run_purity = mocker.patch("msianalyzer.core.run.run.run_precursor_purity")
    mock_run_annotation = mocker.patch("msianalyzer.core.run.run.run_annotation")
    mock_run_consensus = mocker.patch("msianalyzer.core.run.run.run_consensus")
    mock_build_report = mocker.patch(
        "msianalyzer.core.run.run.build_summary_report"
    )
    mock_create_adata = mocker.patch("msianalyzer.core.run.run.create_spatial_adata")
    mock_run_tic_normalization = mocker.patch(
        "msianalyzer.core.run.run.run_tic_normalization"
    )

    mock_executor = mocker.MagicMock()
    mock_executor.__enter__.return_value.map.return_value = [res1, res2]
    mocker.patch(
        "msianalyzer.core.run.run.ProcessPoolExecutor", return_value=mock_executor
    )

    run.run_core()

    mock_align.assert_not_called()
    mock_run_grouper.assert_not_called()
    mock_run_purity.assert_not_called()
    mock_run_annotation.assert_not_called()
    mock_run_consensus.assert_not_called()
    mock_build_report.assert_not_called()
    mock_create_adata.assert_not_called()
    mock_run_tic_normalization.assert_not_called()


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


def test_start_sets_config_path_default_from_config_file(mocker, tmp_path):
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

    mocker.patch.object(run, "run_core")

    run.start(config_file)

    assert run.config_path == str(config_file)


def test_start_with_config_object_records_explicit_config_path_and_on_step(
    mocker, tmp_path
):
    run = Run()
    config = DummyConfig(tmp_path)

    mock_proj_inst = mocker.MagicMock()
    mock_proj_inst.runs = {}
    mock_project_cls = mocker.patch("msianalyzer.core.run.run.Project")
    mock_project_cls.load.return_value = mock_proj_inst

    mock_run_core = mocker.patch.object(run, "run_core")

    events = []
    explicit_path = str(tmp_path / "explicit.yml")
    run.start(
        config=config,
        config_path=explicit_path,
        on_step=lambda step, status: events.append((step, status)),
    )

    assert run.config_path == explicit_path
    assert run.on_step is not None
    mock_run_core.assert_called_once()


def test_start_with_config_object_and_no_config_path_leaves_it_none(mocker, tmp_path):
    run = Run()
    config = DummyConfig(tmp_path)

    mock_proj_inst = mocker.MagicMock()
    mock_proj_inst.runs = {}
    mock_project_cls = mocker.patch("msianalyzer.core.run.run.Project")
    mock_project_cls.load.return_value = mock_proj_inst

    mocker.patch.object(run, "run_core")

    run.start(config=config)

    assert run.config_path is None


# ==============================================================================
# TESTS FOR on_step PROGRESS CALLBACK (USING mocker)
# ==============================================================================

_FULL_RUN_STAGES_SUCCESS = [
    ("process_samples", "started"),
    ("process_samples", "completed"),
    ("align_mz", "started"),
    ("align_mz", "completed"),
    ("group_ms2", "started"),
    ("group_ms2", "completed"),
    ("precursor_purity", "started"),
    ("precursor_purity", "completed"),
    ("annotate_ms2", "started"),
    ("annotate_ms2", "completed"),
    ("ms2_consensus", "started"),
    ("ms2_consensus", "completed"),
    ("assemble_adata", "started"),
    ("assemble_adata", "completed"),
    ("normalize_tic", "started"),
    ("normalize_tic", "completed"),
    ("summary_report", "started"),
    ("summary_report", "completed"),
]


def _patch_run_core_collaborators(mocker, mzs_index=(100.0, 200.0, 300.0)):
    mocker.patch(
        "msianalyzer.core.run.run.run_grouper",
        return_value=mocker.MagicMock(associations=[], feature_summary=[]),
    )
    mocker.patch(
        "msianalyzer.core.run.run.run_precursor_purity",
        return_value=mocker.MagicMock(
            n_scans=0, n_multi_peak=0, n_precursor_missing=0, n_interpolated=0
        ),
    )
    mocker.patch(
        "msianalyzer.core.run.run.run_annotation",
        return_value=mocker.MagicMock(
            rows=[], n_scans_annotated=0, n_features_annotated=0
        ),
    )
    mocker.patch(
        "msianalyzer.core.run.run.run_consensus",
        return_value=mocker.MagicMock(n_features=0, n_features_scored=0),
    )
    mocker.patch("msianalyzer.core.run.run.build_summary_report")
    mocker.patch(
        "msianalyzer.core.run.run.align_mz_across_samples",
        return_value=pd.DataFrame(index=list(mzs_index)),
    )
    mocker.patch(
        "msianalyzer.core.run.run.create_spatial_adata", return_value=mocker.MagicMock()
    )
    mocker.patch("msianalyzer.core.run.run.run_tic_normalization")


def test_run_core_emits_on_step_progress_for_every_stage(mocker, tmp_path):
    run = Run("test_config.yml")
    config = DummyConfig(tmp_path)
    config.annotate.library_path = str(tmp_path / "lib.db")
    out_dir = Path(config.io.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    project_mock = mocker.MagicMock()
    project_mock.uuid = "proj_uuid_123"
    run.config = config
    run.project = project_mock

    events = []
    run.on_step = lambda step, status: events.append((step, status))

    res1 = SampleResult(
        out_db_path=out_dir / "sample1.db", peaks_mzs=np.array([100.0, 200.0])
    )
    res2 = SampleResult(
        out_db_path=out_dir / "sample2.db", peaks_mzs=np.array([100.0, 300.0])
    )

    mock_adb = mocker.patch("msianalyzer.core.run.run.analysis_db")
    mock_adb.analysis_db_path.return_value = out_dir / "analysis_ana.db"
    mock_adb.register_sample.side_effect = [1, 2]
    mock_adb.is_command_already_run.return_value = False

    _patch_run_core_collaborators(mocker)

    mock_executor = mocker.MagicMock()
    mock_executor.__enter__.return_value.map.return_value = [res1, res2]
    mocker.patch(
        "msianalyzer.core.run.run.ProcessPoolExecutor", return_value=mock_executor
    )

    run.run_core()

    assert events == _FULL_RUN_STAGES_SUCCESS


def test_run_core_emits_skipped_for_disabled_stages(mocker, tmp_path):
    run = Run("test_config.yml")
    config = DummyConfig(tmp_path)
    config.purity.enabled = False
    config.annotate.library_path = None
    config.consensus.enabled = False
    config.report.enabled = False
    config.normalization.enabled = False
    out_dir = Path(config.io.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    project_mock = mocker.MagicMock()
    project_mock.uuid = "proj_uuid_123"
    run.config = config
    run.project = project_mock

    events = []
    run.on_step = lambda step, status: events.append((step, status))

    res1 = SampleResult(out_db_path=out_dir / "sample1.db", peaks_mzs=np.array([100.0]))
    res2 = SampleResult(out_db_path=out_dir / "sample2.db", peaks_mzs=np.array([100.0]))

    mock_adb = mocker.patch("msianalyzer.core.run.run.analysis_db")
    mock_adb.analysis_db_path.return_value = out_dir / "analysis_ana.db"
    mock_adb.register_sample.side_effect = [1, 2]
    mock_adb.is_command_already_run.return_value = False

    mocker.patch(
        "msianalyzer.core.run.run.run_grouper",
        return_value=mocker.MagicMock(associations=[], feature_summary=[]),
    )
    mocker.patch(
        "msianalyzer.core.run.run.align_mz_across_samples",
        return_value=pd.DataFrame(index=[100.0]),
    )
    mocker.patch(
        "msianalyzer.core.run.run.create_spatial_adata", return_value=mocker.MagicMock()
    )

    mock_executor = mocker.MagicMock()
    mock_executor.__enter__.return_value.map.return_value = [res1, res2]
    mocker.patch(
        "msianalyzer.core.run.run.ProcessPoolExecutor", return_value=mock_executor
    )

    run.run_core()

    assert events == [
        ("process_samples", "started"),
        ("process_samples", "completed"),
        ("align_mz", "started"),
        ("align_mz", "completed"),
        ("group_ms2", "started"),
        ("group_ms2", "completed"),
        ("precursor_purity", "skipped"),
        ("annotate_ms2", "skipped"),
        ("ms2_consensus", "skipped"),
        ("assemble_adata", "started"),
        ("assemble_adata", "completed"),
        ("normalize_tic", "skipped"),
        ("summary_report", "skipped"),
    ]


def test_run_core_emits_failed_when_sample_processing_raises(mocker, tmp_path):
    run = Run("test_config.yml")
    config = DummyConfig(tmp_path)
    out_dir = Path(config.io.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    project_mock = mocker.MagicMock()
    project_mock.uuid = "proj_uuid_123"
    run.config = config
    run.project = project_mock

    events = []
    run.on_step = lambda step, status: events.append((step, status))

    mock_adb = mocker.patch("msianalyzer.core.run.run.analysis_db")
    mock_adb.analysis_db_path.return_value = out_dir / "analysis_ana.db"
    mock_adb.register_sample.side_effect = [1, 2]

    mock_executor = mocker.MagicMock()
    mock_executor.__enter__.return_value.map.side_effect = RuntimeError("boom")
    mocker.patch(
        "msianalyzer.core.run.run.ProcessPoolExecutor", return_value=mock_executor
    )

    with pytest.raises(RuntimeError):
        run.run_core()

    assert events == [("process_samples", "started"), ("process_samples", "failed")]
