import sqlite3

from msianalyzer.core.analysis_db import init_analysis_db
from msianalyzer.core.parser.mzml_parser import array_to_blob
from msianalyzer.gui.utils.analysis_bridge import AnalysisBridge
import numpy as np

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


def test_get_mirror_plot_html_returns_html_for_valid_annotation(tmp_path):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    _seed_annotated_feature(db_path)

    bridge = AnalysisBridge()
    html = bridge.getMirrorPlotHtml(str(db_path), 1)

    assert "<div" in html
    assert "plotly" in html.lower()


def test_get_mirror_plot_html_returns_error_message_for_unknown_id(tmp_path):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()

    bridge = AnalysisBridge()
    html = bridge.getMirrorPlotHtml(str(db_path), 999)

    assert "<p" in html
    assert "No annotation found" in html


def test_get_mirror_plot_html_returns_error_message_without_filtered_spectra(tmp_path):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    _seed_annotated_feature(db_path, with_filtered_spectra=False)

    bridge = AnalysisBridge()
    html = bridge.getMirrorPlotHtml(str(db_path), 1)

    assert "<p" in html
    assert "no stored filtered spectra" in html
