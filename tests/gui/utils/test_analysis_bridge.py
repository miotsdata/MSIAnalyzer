import sqlite3

from msianalyzer.core.analysis_db import init_analysis_db
from msianalyzer.gui.utils.analysis_bridge import AnalysisBridge

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
