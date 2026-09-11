import sqlite3
from pathlib import Path

from PySide6.QtCore import QUrl

from msianalyzer.core.analysis_db import init_analysis_db, log_command, register_sample
from msianalyzer.core.parser.mzml_parser import array_to_blob
from msianalyzer.core.spectra.average_spectra import save_aggregated_spectra
from msianalyzer.gui.utils.analysis_bridge import AnalysisBridge
import numpy as np


def _read_url(url: str) -> str:
    """Reads back the HTML a `file://` URL (as returned by getSpectrumUrl /
    getMirrorPlotUrl) points at — those write to disk instead of returning
    HTML directly (WebEngineView.loadHtml()/setHtml() silently fail past
    Qt's ~2MB limit, which a plot with Plotly.js embedded already exceeds)."""
    return Path(QUrl(url).toLocalFile()).read_text(encoding="utf-8")

_ZERO_SUMMARY = {
    "n_samples": 0,
    "n_features": 0,
    "n_ms2_associated_features": 0,
    "annotation_ran": False,
    "n_annotated_features": 0,
    "n_distinct_compounds": 0,
}


def test_get_summary_missing_db_returns_zero_dict(tmp_path):
    bridge = AnalysisBridge()
    assert bridge.getSummary(str(tmp_path / "does_not_exist.db")) == _ZERO_SUMMARY


def test_get_summary_empty_string_returns_zero_dict():
    bridge = AnalysisBridge()
    assert bridge.getSummary("") == _ZERO_SUMMARY


def test_get_summary_reads_real_db(tmp_path):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()

    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO samples (sample_id, name, raw_db_path, polarity) "
            "VALUES (1, 's1', 'a.db', 'positive')"
        )
        con.executemany(
            "INSERT INTO features (feature_id, mz, members_json) VALUES (?, ?, '{}')",
            [(1, 100.0), (2, 200.0)],
        )
        con.commit()

    bridge = AnalysisBridge()
    result = bridge.getSummary(str(db_path))

    assert result["n_samples"] == 1
    assert result["n_features"] == 2
    assert result["annotation_ran"] is False


def _seed_annotated_feature(db_path, *, with_filtered_spectra=True):
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO samples (sample_id, name, raw_db_path, polarity) "
            "VALUES (1, 's1', 'a.db', 'positive')"
        )
        con.execute(
            "INSERT INTO annotation_libraries (id, path, name) "
            "VALUES (1, 'lib.db', 'my_library')"
        )
        con.execute(
            "INSERT INTO ms2_associations "
            "(sample_id, scan_id, match_key, precursor_mz, "
            "n_features_in_window, rt, n_peaks, polarity) "
            "VALUES (1, 42, 'k1', 150.1234, 1, 12.3, 5, 'positive')"
        )

        if with_filtered_spectra:
            arr = np.array([100.0, 200.0], dtype=np.float32)
            blob = array_to_blob(arr)
        else:
            blob = None

        con.execute(
            "INSERT INTO ms2_annotations "
            "(id, feature_id, sample_id, scan_id, library_id, "
            "library_spectrum_id, compound_name, compound_formula, inchikey, "
            "score, dot_product_score, lib_coverage, emp_coverage, "
            "coverage_score, n_matched_peaks, n_lib_peaks, n_emp_peaks_raw, "
            "n_emp_peaks_filtered, emp_filtered_mz, emp_filtered_intensity, "
            "lib_filtered_mz, lib_filtered_intensity, rank_ms2, rank_feature) "
            "VALUES (1, 7, 1, 42, 1, 9, 'Caffeine', 'C8H10N4O2', "
            "'RYYVLZVUVIJVGH-UHFFFAOYSA-N', 0.87, 0.9, 0.8, 0.75, 0.77, "
            "2, 2, 10, 3, ?, ?, ?, ?, 1, 1)",
            (blob, blob, blob, blob),
        )
        con.commit()


def test_get_annotation_table_returns_one_row_per_feature(tmp_path):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    _seed_annotated_feature(db_path)

    bridge = AnalysisBridge()
    rows = bridge.getAnnotationTable(str(db_path))

    assert len(rows) == 1
    assert rows[0]["feature_id"] == 7
    assert rows[0]["compound_name"] == "Caffeine"
    assert isinstance(rows[0]["best_score"], float)


def test_get_annotation_table_empty_without_annotations(tmp_path):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()

    bridge = AnalysisBridge()
    assert bridge.getAnnotationTable(str(db_path)) == []


def test_get_annotation_candidates_for_feature(tmp_path):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    _seed_annotated_feature(db_path)

    bridge = AnalysisBridge()
    rows = bridge.getAnnotationCandidates(str(db_path), 7)

    assert len(rows) == 1
    assert rows[0]["sample_name"] == "s1"
    assert rows[0]["library_name"] == "my_library"


def test_get_annotation_candidates_empty_for_unknown_feature(tmp_path):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    _seed_annotated_feature(db_path)

    bridge = AnalysisBridge()
    assert bridge.getAnnotationCandidates(str(db_path), 999) == []


def test_get_mirror_plot_url_returns_html_for_valid_annotation(tmp_path):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    _seed_annotated_feature(db_path)

    bridge = AnalysisBridge()
    url = bridge.getMirrorPlotUrl(str(db_path), 1)
    html = _read_url(url)

    assert url.startswith("file://")
    assert "<div" in html
    assert "plotly" in html.lower()


def test_get_mirror_plot_url_returns_error_message_for_unknown_id(tmp_path):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()

    bridge = AnalysisBridge()
    html = _read_url(bridge.getMirrorPlotUrl(str(db_path), 999))

    assert "<p" in html
    assert "No annotation found" in html


def test_get_mirror_plot_url_returns_error_message_without_filtered_spectra(tmp_path):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    _seed_annotated_feature(db_path, with_filtered_spectra=False)

    bridge = AnalysisBridge()
    html = _read_url(bridge.getMirrorPlotUrl(str(db_path), 1))

    assert "<p" in html
    assert "no stored filtered spectra" in html


def test_get_mirror_plot_url_is_unique_per_call(tmp_path):
    # Each render needs a distinct URL — an unchanged QML `url` binding
    # doesn't reload, even when the underlying file's content changed.
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    _seed_annotated_feature(db_path)

    bridge = AnalysisBridge()
    url1 = bridge.getMirrorPlotUrl(str(db_path), 1)
    url2 = bridge.getMirrorPlotUrl(str(db_path), 1)

    assert url1 != url2


def test_get_samples_returns_every_sample(tmp_path):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    register_sample(db_path, name="s1", raw_db_path=tmp_path / "s1.db")
    register_sample(db_path, name="s2", raw_db_path=tmp_path / "s2.db")

    bridge = AnalysisBridge()
    rows = bridge.getSamples(str(db_path))

    assert [r["name"] for r in rows] == ["s1", "s2"]


def test_get_samples_empty_for_missing_db(tmp_path):
    bridge = AnalysisBridge()
    assert bridge.getSamples(str(tmp_path / "nope.db")) == []


def test_get_spectrum_url_returns_plot_for_saved_spectrum(tmp_path):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    sample_id = register_sample(db_path, name="s1", raw_db_path=tmp_path / "s1.db")
    command_id = log_command(
        db_path, "filter_spectra", {}, run_id="run-1", sample_id=sample_id
    )
    save_aggregated_spectra(
        np.array([100.0, 200.0]),
        np.array([10.0, 20.0]),
        analysis_db_path=db_path,
        run_id="run-1",
        sample_id=sample_id,
        command_id=command_id,
    )

    bridge = AnalysisBridge()
    url = bridge.getSpectrumUrl(str(db_path), "run-1", sample_id)
    html = _read_url(url)

    assert url.startswith("file://")
    assert "plotly" in html.lower()
    assert "onSpectrumPointClicked" in html
    assert "qtwebchannel/qwebchannel.js" in html


def test_get_spectrum_url_returns_error_message_when_missing(tmp_path):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()

    bridge = AnalysisBridge()
    html = _read_url(bridge.getSpectrumUrl(str(db_path), "run-1", 1))

    assert "<p" in html
    assert "No spectrum found" in html


def test_get_spectrum_url_is_unique_per_call(tmp_path):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()

    bridge = AnalysisBridge()
    url1 = bridge.getSpectrumUrl(str(db_path), "run-1", 1)
    url2 = bridge.getSpectrumUrl(str(db_path), "run-1", 1)

    assert url1 != url2


def test_on_spectrum_point_clicked_emits_signal(tmp_path):
    bridge = AnalysisBridge()
    received = []
    bridge.spectrumPointClicked.connect(received.append)

    bridge.onSpectrumPointClicked(123.456)

    assert received == [123.456]


def test_get_feature_detail_reports_presence_ms2_and_hits(tmp_path):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO features (feature_id, mz, members_json) "
            "VALUES (7, 150.0, '{\"s1\": 0, \"s2\": null}')"
        )
        con.execute(
            "INSERT INTO feature_ms2_summary (feature_id, feature_mz, n_ms2, "
            "n_samples, n_precursor_only, n_single_peak, n_chimeric, "
            "n_flat_fragmentation, median_n_peaks) "
            "VALUES (7, 150.0, 5, 1, 0, 0, 0, 0, 3.0)"
        )
        con.commit()
    _seed_annotated_feature(db_path)

    bridge = AnalysisBridge()
    detail = bridge.getFeatureDetail(str(db_path), 150.1, 5)

    assert detail["feature_id"] == 7
    assert detail["samples_present"] == ["s1"]
    assert detail["samples_absent"] == ["s2"]
    assert detail["n_ms2"] == 5
    assert len(detail["top_hits"]) == 1
    assert detail["top_hits"][0]["compound_name"] == "Caffeine"


def test_get_feature_detail_empty_when_no_features(tmp_path):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()

    bridge = AnalysisBridge()
    assert bridge.getFeatureDetail(str(db_path), 100.0, 5) == {}


def test_get_feature_detail_empty_for_missing_db(tmp_path):
    bridge = AnalysisBridge()
    assert bridge.getFeatureDetail(str(tmp_path / "nope.db"), 100.0, 5) == {}
