import sqlite3

import pytest

from msianalyzer.core.analysis_db import init_analysis_db, register_sample, save_features
from msianalyzer.core.annotation import reannotation
from msianalyzer.core.annotation.reannotation import (
    Ms2Settings,
    delete_history_entry,
    load_history,
    reannotate,
    restore_annotation,
    run_ms2_stages,
)


@pytest.fixture
def annotated_db(tmp_path, ms2_grouper_mock_data, make_ms2_db, make_library_db):
    """An analysis DB after the pipeline's MS2 stages, with one library
    compound that matches the planted "single" feature."""
    mock = ms2_grouper_mock_data(n=1000)
    adb = tmp_path / "out" / "analysis.db"
    adb.parent.mkdir()
    init_analysis_db(adb).close()
    register_sample(adb, "s0", str(make_ms2_db(mock, name="s0_ms2.db")), "+")
    save_features(adb, mock.features_df)
    lib = make_library_db(mock, case="single")
    sections = {
        "annotate": {"library_path": str(lib), "candidate_ppm": 25.0, "n_workers": 1},
        "group_ms2": {"assoc_ppm": mock.assoc_ppm},
    }
    run_ms2_stages(adb, Ms2Settings.from_sections(sections, mock.align_ppm), "run-1")
    return adb, sections, mock.align_ppm


def _n_hits(adb):
    with sqlite3.connect(adb) as con:
        return con.execute("SELECT COUNT(*) FROM ms2_annotations").fetchone()[0]


def _strict(sections, align_ppm):
    """The same settings, but no candidate can ever be stored."""
    strict = {**sections, "annotate": {**sections["annotate"], "min_matched_peaks": 999}}
    return Ms2Settings.from_sections(strict, align_ppm)


def test_reannotate_replaces_the_ms2_tables_and_keeps_both_states(annotated_db):
    adb, sections, align_ppm = annotated_db
    assert _n_hits(adb) > 0

    reannotate(adb, _strict(sections, align_ppm), "run-1", pipeline_settings=sections)

    assert _n_hits(adb) == 0
    history = load_history(adb)
    assert [e["kind"] for e in history] == ["pipeline", "reannotate"]
    assert history[-1]["current"] and history[-1]["settings"]["annotate"]["min_matched_peaks"] == 999
    assert history[0]["n_annotated_features"] > 0 and history[1]["n_annotated_features"] == 0
    for e in history:
        assert (adb.parent / "annotation_history" / e["snapshot"]).is_file()


def test_restore_brings_an_earlier_annotation_back_as_a_new_state(annotated_db):
    adb, sections, align_ppm = annotated_db
    before = _n_hits(adb)
    reannotate(adb, _strict(sections, align_ppm), "run-1", pipeline_settings=sections)
    pipeline_id = load_history(adb)[0]["id"]

    restore_annotation(adb, pipeline_id, pipeline_settings=sections)

    assert _n_hits(adb) == before
    history = load_history(adb)
    assert [e["kind"] for e in history] == ["pipeline", "reannotate", "restore"]
    assert history[-1]["restored_from"] == pipeline_id


def test_a_failed_reannotation_puts_the_previous_annotation_back(annotated_db, monkeypatch):
    adb, sections, align_ppm = annotated_db
    before = _n_hits(adb)

    def boom(*args, **kwargs):
        raise RuntimeError("library unreadable")

    monkeypatch.setattr(reannotation, "run_annotation", boom)
    with pytest.raises(RuntimeError):
        reannotate(adb, _strict(sections, align_ppm), "run-1", pipeline_settings=sections)

    assert _n_hits(adb) == before
    assert [e["kind"] for e in load_history(adb)] == ["pipeline"]


def test_delete_history_entry_removes_its_snapshot_but_not_the_current_one(annotated_db):
    adb, sections, align_ppm = annotated_db
    reannotate(adb, _strict(sections, align_ppm), "run-1", pipeline_settings=sections)
    pipeline, current = load_history(adb)

    delete_history_entry(adb, pipeline["id"])

    assert not (adb.parent / "annotation_history" / pipeline["snapshot"]).exists()
    assert [e["id"] for e in load_history(adb)] == [current["id"]]
    with pytest.raises(ValueError):
        delete_history_entry(adb, current["id"])


def test_settings_from_an_older_config_ignore_unknown_keys():
    settings = Ms2Settings.from_sections(
        {"annotate": {"candidate_ppm": 7.0, "removed_long_ago": 1}, "align": {"align_ppm": 3.0}}
    )
    assert settings.annotate.candidate_ppm == 7.0
    assert settings.align_ppm == 3.0
    assert settings.to_dict()["annotate"]["candidate_ppm"] == 7.0
