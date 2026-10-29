import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import anndata as ad
import pandas as pd
import pytest
from scipy.sparse import csr_matrix

from msianalyzer.core.analysis_db import init_analysis_db, log_command, register_sample
from msianalyzer.core.parser.mzml_parser import array_to_blob
from msianalyzer.core.spectra.average_spectra import save_aggregated_spectra
from msianalyzer.gui.utils.analysis_bridge import (
    AnalysisBridge,
    _categorize_peaks,
    _load_local_moran_layer_data,
    _load_local_moran_threshold_default,
    _load_normalized_layer_name,
    _load_spatial_correlation_table,
)
import numpy as np


_ZERO_SUMMARY = {
    "n_samples": 0,
    "n_features": 0,
    "n_ms2_associated_features": 0,
    "annotation_ran": False,
    "n_annotated_features": 0,
    "n_distinct_compounds": 0,
    "outcomes_recorded": False,
    "n_outcome_no_ms2": 0,
    "n_outcome_annotated": 0,
    "n_outcome_no_shared_fragments": 0,
    "n_outcome_no_candidates": 0,
    "n_outcome_all_scans_skipped": 0,
    "ms2_background": [],
    "ms2_cleaning_ran": False,
    "precursor_ran": False,
    "n_precursor_matched_features": 0,
}


def test_ensure_schema_current_adds_a_missing_index_to_an_old_db(tmp_path):
    # Simulates an analysis run before ADR 33's index existed: create a
    # normal (already-current) db, then drop the index by hand to stand
    # in for that older schema.
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    with sqlite3.connect(db_path) as con:
        con.execute("DROP INDEX idx_ann_rank_feature_1")
        con.execute("DELETE FROM metadata WHERE key = 'schema_fingerprint'")  # an older analysis
        con.commit()
        indexes_before = {
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }
    assert "idx_ann_rank_feature_1" not in indexes_before

    bridge = AnalysisBridge()
    bridge.ensureSchemaCurrent(str(db_path))

    with sqlite3.connect(db_path) as con:
        indexes_after = {
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }
    assert "idx_ann_rank_feature_1" in indexes_after


def test_ensure_schema_current_is_a_no_op_on_an_already_current_db(tmp_path):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    _seed_annotated_feature(db_path)
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO features (feature_id, mz, members_json) VALUES (7, 300.5, '{}')"
        )
        con.commit()

    bridge = AnalysisBridge()
    bridge.ensureSchemaCurrent(str(db_path))

    # Existing data untouched — a DDL-only pass never mutates rows.
    with sqlite3.connect(db_path) as con:
        rows = con.execute("SELECT compound_name FROM ms2_annotations").fetchall()
    assert rows == [("Caffeine",)]


def test_ensure_schema_current_backfills_annotations_into_var(tmp_path):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO features (feature_id, mz, members_json) VALUES (7, 100.0, '{}')"
        )
        con.commit()
    _seed_sample_with_h5ad(db_path, "sampleA")

    bridge = AnalysisBridge()
    bridge.ensureSchemaCurrent(str(db_path))
    bridge.wait_for_var_writes()

    var = ad.read_h5ad(tmp_path / "sampleA.h5ad").var
    assert list(var.index) == ["mz_100.0000"]
    assert list(var["feature_id"]) == [7]


def test_request_schema_update_runs_in_the_background_then_signals(tmp_path, qtbot):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    with sqlite3.connect(db_path) as con:
        con.execute("DROP INDEX idx_ann_rank_feature_1")
        con.execute("DELETE FROM metadata WHERE key = 'schema_fingerprint'")  # an older analysis
        con.commit()
    bridge = AnalysisBridge()

    with qtbot.waitSignal(bridge.schemaReady, timeout=5000) as blocker:
        bridge.requestSchemaUpdate(str(db_path))

    assert blocker.args == [str(db_path)]
    with sqlite3.connect(db_path) as con:
        names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert "idx_ann_rank_feature_1" in names


def test_request_schema_update_signals_even_without_a_db(tmp_path, qtbot):
    bridge = AnalysisBridge()
    missing = str(tmp_path / "nope.db")
    with qtbot.waitSignal(bridge.schemaReady, timeout=2000) as blocker:
        bridge.requestSchemaUpdate(missing)
    assert blocker.args == [missing]


def test_var_refresh_runs_off_the_gui_thread(tmp_path, monkeypatch):
    # Rewriting .var in every .h5ad over a network share froze the window.
    import threading

    started, release = threading.Event(), threading.Event()
    threads = []

    def slow_write(db, paths):
        threads.append(threading.current_thread())
        started.set()
        release.wait(5)

    monkeypatch.setattr("msianalyzer.core.export.write_annotations_to_var", slow_write)
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    bridge = AnalysisBridge()
    bridge._refresh_var_annotations(str(db_path))  # returns at once
    assert started.wait(5)
    release.set()
    bridge.wait_for_var_writes()

    assert threads and threads[0] is not threading.main_thread()


def test_ensure_schema_current_missing_db_is_a_no_op(tmp_path):
    bridge = AnalysisBridge()
    bridge.ensureSchemaCurrent(str(tmp_path / "does_not_exist.db"))  # must not raise


def test_ensure_schema_current_empty_path_is_a_no_op():
    bridge = AnalysisBridge()
    bridge.ensureSchemaCurrent("")  # must not raise


# ---------------------------------------------------------------------------
# Export menu
# ---------------------------------------------------------------------------


def test_export_annotation_table_writes_file_and_emits_finished(tmp_path, qtbot):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO features (feature_id, mz, members_json) VALUES (7, 300.5, '{}')"
        )
        con.commit()
    dest = tmp_path / "out.csv"

    bridge = AnalysisBridge()
    with qtbot.waitSignal(bridge.exportFinished, timeout=2000) as spy:
        bridge.exportAnnotationTable(str(db_path), str(dest))

    assert dest.exists()
    assert str(dest) in spy.args[0]


def test_export_annotation_table_failure_emits_export_failed(tmp_path, qtbot):
    bridge = AnalysisBridge()
    # A destination under a path component that's actually a file, not a
    # directory — os.makedirs (via Path.mkdir(parents=True)) genuinely
    # fails on this, unlike a merely-missing directory.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    dest = blocker / "out.csv"

    with qtbot.waitSignal(bridge.exportFailed, timeout=2000):
        bridge.exportAnnotationTable(str(tmp_path / "does_not_exist.db"), str(dest))


def test_export_annotation_table_stale_request_is_discarded(tmp_path, qtbot):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()

    bridge = AnalysisBridge()
    received = []
    bridge.exportFinished.connect(received.append)

    bridge.exportAnnotationTable(str(db_path), str(tmp_path / "first.csv"))
    first_worker = bridge._export_worker
    bridge.exportAnnotationTable(str(db_path), str(tmp_path / "second.csv"))

    qtbot.waitUntil(lambda: not first_worker.isRunning(), timeout=5000)
    qtbot.waitUntil(lambda: len(received) >= 1, timeout=5000)
    qtbot.wait(50)

    assert len(received) == 1


def _seed_sample_with_h5ad(db_path: Path, name: str) -> None:
    register_sample(db_path, name=name, raw_db_path=db_path.parent / f"{name}_raw.db")
    obs = pd.DataFrame(index=["px0", "px1"])
    var = pd.DataFrame({"mz": [100.0]}, index=["mz_100.0000"])
    adata = ad.AnnData(X=csr_matrix(np.array([[1.0], [2.0]], dtype=np.float32)), obs=obs, var=var)
    adata.obsm["spatial"] = np.array([(0.0, 0.0), (1.0, 0.0)], dtype=float)
    adata.layers["TIC"] = csr_matrix(np.array([[10.0], [20.0]], dtype=np.float32))
    adata.write_h5ad(db_path.parent / f"{name}.h5ad")


def test_export_integration_tables_writes_files_and_emits_finished(tmp_path, qtbot):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO features (feature_id, mz, members_json) VALUES (7, 100.0, '{}')"
        )
        con.commit()
    _seed_sample_with_h5ad(db_path, "sampleA")
    dest_folder = tmp_path / "out"

    bridge = AnalysisBridge()
    with qtbot.waitSignal(bridge.exportFinished, timeout=2000) as spy:
        bridge.exportIntegrationTables(str(db_path), str(dest_folder), "TIC", "csv")

    assert (dest_folder / "sampleA_integration.csv").exists()
    assert "1 sample" in spy.args[0]


def test_export_integration_tables_failure_emits_export_failed(tmp_path, qtbot):
    bridge = AnalysisBridge()
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    dest_folder = blocker / "out"

    with qtbot.waitSignal(bridge.exportFailed, timeout=2000):
        bridge.exportIntegrationTables(
            str(tmp_path / "does_not_exist.db"), str(dest_folder), "TIC", "csv"
        )


def test_export_calibration_results_writes_files_and_emits_finished(tmp_path, qtbot):
    from msianalyzer.core.analysis_db import upsert_calibration_point

    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    upsert_calibration_point(
        db_path, sample_id=None, roi_name="std_a", amount=10.0, amount_unit="pmol",
        pixel_size=0.1, pixel_size_unit="mm", area=0.02, concentration=500.0,
        mean_intensity=100.0, n_pixels=2,
    )
    dest_folder = tmp_path / "out"

    bridge = AnalysisBridge()
    with qtbot.waitSignal(bridge.exportFinished, timeout=2000) as spy:
        bridge.exportCalibrationResults(str(db_path), str(dest_folder))

    assert (dest_folder / "calibration_standards.csv").exists()
    assert "1 file" in spy.args[0]


def test_export_calibration_results_no_data_reports_zero_files(tmp_path, qtbot):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    dest_folder = tmp_path / "out"

    bridge = AnalysisBridge()
    with qtbot.waitSignal(bridge.exportFinished, timeout=2000) as spy:
        bridge.exportCalibrationResults(str(db_path), str(dest_folder))

    assert "No calibration results" in spy.args[0]


def test_export_calibration_results_failure_emits_export_failed(tmp_path, qtbot):
    bridge = AnalysisBridge()
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    dest_folder = blocker / "out"

    with qtbot.waitSignal(bridge.exportFailed, timeout=2000):
        bridge.exportCalibrationResults(str(tmp_path / "does_not_exist.db"), str(dest_folder))


def test_export_visual_inspection_images_writes_files_and_emits_finished(tmp_path, qtbot):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    _seed_sample_with_h5ad(db_path, "sampleA")
    dest_folder = tmp_path / "out"

    bridge = AnalysisBridge()
    with qtbot.waitSignal(bridge.exportFinished, timeout=2000) as spy:
        bridge.exportVisualInspectionImages(
            str(db_path), str(dest_folder), "png", "feature", "100.0", "",
            "TIC", "viridis", "auto", "auto", False, False, [],
        )

    assert (dest_folder / "sampleA.png").exists()
    assert "1 sample" in spy.args[0]


def test_export_visual_inspection_images_failure_emits_export_failed(tmp_path, qtbot):
    bridge = AnalysisBridge()
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    dest_folder = blocker / "out"

    with qtbot.waitSignal(bridge.exportFailed, timeout=2000):
        bridge.exportVisualInspectionImages(
            str(tmp_path / "does_not_exist.db"), str(dest_folder), "png", "feature",
            "100.0", "", "TIC", "viridis", "auto", "auto", False, False, [],
        )


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


def _seed_annotated_feature(db_path, *, with_raw_spectra=True):
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
            "rt, n_peaks, polarity) "
            "VALUES (1, 42, 'k1', 150.1234, 12.3, 5, 'positive')"
        )

        if with_raw_spectra:
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
            "n_emp_peaks_filtered, emp_raw_mz, emp_raw_intensity, "
            "lib_raw_mz, lib_raw_intensity, rank_ms2, rank_feature) "
            "VALUES (1, 7, 1, 42, 1, 9, 'Caffeine', 'C8H10N4O2', "
            "'RYYVLZVUVIJVGH-UHFFFAOYSA-N', 0.87, 0.9, 0.8, 0.75, 0.77, "
            "2, 2, 10, 3, ?, ?, ?, ?, 1, 1)",
            (blob, blob, blob, blob),
        )
        con.commit()


def test_get_annotation_table_returns_one_row_per_feature(tmp_path, qtbot):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    _seed_annotated_feature(db_path)
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO features (feature_id, mz, members_json) VALUES (7, 300.5, '{}')"
        )
        con.commit()

    bridge = AnalysisBridge()
    with qtbot.waitSignal(bridge.annotationTableReady, timeout=2000) as spy:
        bridge.requestAnnotationTable(str(db_path))
    rows = spy.args[0]

    assert len(rows) == 1
    assert rows[0]["feature_id"] == 7
    assert rows[0]["compound_name"] == "Caffeine"
    assert isinstance(rows[0]["best_score"], float)
    # Sorted by / displayed alongside the other columns in the redesigned
    # table ("feature id/number, name and mz") — the view itself has no
    # `mz`, joined in from `features`.
    assert rows[0]["mz"] == 300.5


def test_get_annotation_table_follows_representative_pick_not_raw_score(tmp_path, qtbot):
    # Feature 76's real-world case (see docs/developer/adr/0021): a
    # higher-scoring, 1-matched-peak candidate lost representative
    # selection (assign_feature_ranks' tolerance rule) to a lower-scoring,
    # 3-matched-peak one — rank_feature=1 marks the winner. The bridge
    # must show that one, not whichever merely scored higher.
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO annotation_libraries (id, path, name) "
            "VALUES (1, 'lib.db', 'my_library')"
        )
        con.execute(
            "INSERT INTO features (feature_id, mz, members_json) "
            "VALUES (76, 89.0253, '{}')"
        )
        con.executemany(
            "INSERT INTO ms2_annotations "
            "(id, feature_id, sample_id, scan_id, library_id, "
            "library_spectrum_id, compound_name, compound_formula, inchikey, "
            "score, dot_product_score, lib_coverage, emp_coverage, "
            "coverage_score, n_matched_peaks, n_lib_peaks, n_emp_peaks_raw, "
            "n_emp_peaks_filtered, rank_ms2, rank_feature) "
            "VALUES (?,76,?,?,1,?,?,?,?,?,?,1,1,1,?,?,10,10,1,?)",
            [
                (1, 2, 31748, 9, "(S)-LACTATE", "C3H6O3",
                 "JVTAAEKCZFNVCJ-REOHCLBHSA-N", 0.9330, 1.0, 1, 1, 2),
                (2, 5, 71886, 10, "Lactic acid", "C3H6O3",
                 "JVTAAEKCZFNVCJ-UWTATZPHSA-N", 0.8572, 0.9999, 3, 3, 1),
            ],
        )
        con.commit()

    bridge = AnalysisBridge()
    with qtbot.waitSignal(bridge.annotationTableReady, timeout=2000) as spy:
        bridge.requestAnnotationTable(str(db_path))
    rows = spy.args[0]

    assert len(rows) == 1
    assert rows[0]["compound_name"] == "Lactic acid"
    assert rows[0]["n_matched_peaks"] == 3


def test_get_annotation_table_empty_without_annotations(tmp_path, qtbot):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()

    bridge = AnalysisBridge()
    with qtbot.waitSignal(bridge.annotationTableReady, timeout=2000) as spy:
        bridge.requestAnnotationTable(str(db_path))
    assert spy.args[0] == []


def test_request_annotation_table_empty_path_emits_empty_list(qtbot):
    bridge = AnalysisBridge()
    with qtbot.waitSignal(bridge.annotationTableReady, timeout=2000) as spy:
        bridge.requestAnnotationTable("")
    assert spy.args[0] == []


def test_request_feature_list_reads_real_db(tmp_path, qtbot):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO features (feature_id, mz, members_json) VALUES (7, 300.5, '{}')"
        )
        con.commit()

    bridge = AnalysisBridge()
    with qtbot.waitSignal(bridge.featureListReady, timeout=2000) as spy:
        bridge.requestFeatureList(str(db_path))

    assert spy.args[0] == [{"feature_id": 7, "mz": 300.5, "compound_name": None}]


def test_request_feature_list_empty_path_emits_empty_list(qtbot):
    bridge = AnalysisBridge()
    with qtbot.waitSignal(bridge.featureListReady, timeout=2000) as spy:
        bridge.requestFeatureList("")
    assert spy.args[0] == []


def test_request_annotation_table_stale_request_is_discarded(tmp_path, qtbot):
    # Firing a second request before the first's worker thread has
    # finished must not let the first one's (now-stale) result win — same
    # pattern (and same test shape) as
    # test_request_mirror_plot_stale_request_is_discarded.
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    _seed_annotated_feature(db_path)
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO features (feature_id, mz, members_json) VALUES (7, 300.5, '{}')"
        )
        con.commit()

    bridge = AnalysisBridge()
    received = []
    bridge.annotationTableReady.connect(received.append)

    bridge.requestAnnotationTable(str(db_path))
    first_worker = bridge._annotation_table_worker
    bridge.requestAnnotationTable(str(db_path))

    qtbot.waitUntil(lambda: not first_worker.isRunning(), timeout=5000)
    qtbot.waitUntil(lambda: len(received) >= 1, timeout=5000)
    qtbot.wait(50)  # give a stray first-request signal a chance to (wrongly) arrive too

    assert len(received) == 1


def test_request_feature_list_stale_request_is_discarded(tmp_path, qtbot):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO features (feature_id, mz, members_json) VALUES (7, 300.5, '{}')"
        )
        con.commit()

    bridge = AnalysisBridge()
    received = []
    bridge.featureListReady.connect(received.append)

    bridge.requestFeatureList(str(db_path))
    first_worker = bridge._feature_list_worker
    bridge.requestFeatureList(str(db_path))

    qtbot.waitUntil(lambda: not first_worker.isRunning(), timeout=5000)
    qtbot.waitUntil(lambda: len(received) >= 1, timeout=5000)
    qtbot.wait(50)

    assert len(received) == 1


def _seed_moran_h5ad(out_dir: Path, sample_name: str, mz: float, moran_i: float) -> None:
    _seed_moran_h5ad_multi(out_dir, sample_name, [(mz, moran_i)])


def _seed_moran_h5ad_multi(out_dir: Path, sample_name: str, features: list) -> None:
    """`features`: `[(mz, moran_i), ...]` — more than one, unlike
    `_seed_moran_h5ad`, to catch bugs that only surface with multiple
    features per sample (e.g. a loop accidentally appending only the
    last one)."""
    var_names = [f"mz_test_{i}" for i in range(len(features))]
    var = pd.DataFrame({"mz": [f[0] for f in features]}, index=var_names)
    adata = ad.AnnData(X=np.zeros((1, len(features))), var=var)
    adata.uns["moranI"] = pd.DataFrame({"I": [f[1] for f in features]}, index=var_names)
    adata.write_h5ad(out_dir / f"{sample_name}.h5ad")


def test_load_spatial_correlation_table_reads_every_feature_not_just_the_last(tmp_path):
    # Regression: a loop-indentation bug once appended only the LAST
    # var_name/score pair per sample instead of one row per feature —
    # invisible with the single-feature fixture every other test here
    # uses, since "last" and "only" were indistinguishable.
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO samples (sample_id, name, raw_db_path, polarity) "
            "VALUES (1, 's1', 'a.db', 'positive')"
        )
        con.commit()
    _seed_moran_h5ad_multi(
        tmp_path, "s1", [(100.0, 0.5), (200.0, 0.6), (300.0, 0.7)]
    )

    df = _load_spatial_correlation_table(str(db_path))

    assert len(df) == 3
    assert sorted(df["mz"].tolist()) == [100.0, 200.0, 300.0]


def test_load_spatial_correlation_table_does_not_load_full_x_into_memory(tmp_path):
    # ad.read_h5ad() (no `backed`) loads the whole X matrix just to read
    # uns/var, which failed outright on a real project with multi-GB
    # .h5ad files (reported as "no correlation run" for an analysis that
    # had one). Even `backed="r"` decodes all of obs per sample (ADR 50),
    # so the loader now reads only `var` and `uns/moranI` via h5py —
    # no AnnData is opened at all.
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO samples (sample_id, name, raw_db_path, polarity) "
            "VALUES (1, 's1', 'a.db', 'positive')"
        )
        con.commit()
    _seed_moran_h5ad(tmp_path, "s1", mz=100.0, moran_i=0.5)

    with patch("msianalyzer.gui.utils.analysis_bridge.ad.read_h5ad", wraps=ad.read_h5ad) as spy:
        df = _load_spatial_correlation_table(str(db_path))

    assert spy.call_count == 0
    assert list(df["moran_i"]) == [0.5]


def test_get_spatial_correlation_model_reads_moran_scores_with_name(tmp_path, qtbot):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    _seed_annotated_feature(db_path)  # registers sample "s1", annotates feature 7 as "Caffeine"
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO features (feature_id, mz, members_json) VALUES (7, 123.4567, '{}')"
        )
        con.commit()
    _seed_moran_h5ad(tmp_path, "s1", mz=123.4567, moran_i=0.75)

    bridge = AnalysisBridge()
    model = bridge.getSpatialCorrelationModel(str(db_path))
    assert model.rowsLoading is True
    qtbot.waitUntil(lambda: not model.rowsLoading, timeout=2000)

    assert model.samples == ["s1"]
    assert model.rowCount() == 1
    assert model.data(model.index(0, 0)) == "123.4567: Caffeine"
    assert model.data(model.index(0, 1)) == "0.750"


def test_get_spatial_correlation_model_returns_same_instance_for_same_analysis(tmp_path, qtbot):
    # The whole point of the model-based redesign: re-fetching the SAME
    # analysis returns the identical, already-populated (and, from the
    # user's own past sort/search actions, already-sorted/filtered)
    # instance — not a fresh one that would silently reset that state.
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO samples (sample_id, name, raw_db_path, polarity) "
            "VALUES (1, 's1', 'a.db', 'positive')"
        )
        con.commit()
    _seed_moran_h5ad(tmp_path, "s1", mz=100.0, moran_i=0.5)

    bridge = AnalysisBridge()
    model_1 = bridge.getSpatialCorrelationModel(str(db_path))
    qtbot.waitUntil(lambda: not model_1.rowsLoading, timeout=2000)
    model_1.nameQuery = "something the user typed"

    # Mutate the .h5ad after the first fetch — if the second call hit the
    # cache (not the disk), it's still the same object with the OLD data
    # and the query the user typed still applied.
    _seed_moran_h5ad(tmp_path, "s1", mz=100.0, moran_i=0.99)

    model_2 = bridge.getSpatialCorrelationModel(str(db_path))
    assert model_2 is model_1
    assert model_2.nameQuery == "something the user typed"


def test_get_spatial_correlation_model_new_analysis_replaces_model(tmp_path, qtbot):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    db_a = tmp_path / "a" / "a.db"
    db_b = tmp_path / "b" / "b.db"
    init_analysis_db(db_a).close()
    init_analysis_db(db_b).close()
    for db_path, mz, i in [(db_a, 100.0, 0.5), (db_b, 200.0, 0.9)]:
        with sqlite3.connect(db_path) as con:
            con.execute(
                "INSERT INTO samples (sample_id, name, raw_db_path, polarity) "
                "VALUES (1, 's1', 'a.db', 'positive')"
            )
            con.commit()
        _seed_moran_h5ad(db_path.parent, "s1", mz=mz, moran_i=i)

    bridge = AnalysisBridge()
    model_a = bridge.getSpatialCorrelationModel(str(db_a))
    qtbot.waitUntil(lambda: not model_a.rowsLoading, timeout=2000)
    assert model_a.mzForRow(0) == 100.0

    model_b = bridge.getSpatialCorrelationModel(str(db_b))
    assert model_b is not model_a
    qtbot.waitUntil(lambda: not model_b.rowsLoading, timeout=2000)
    assert model_b.mzForRow(0) == 200.0


def test_get_moran_i_for_feature_reads_from_cached_model(tmp_path, qtbot):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO samples (sample_id, name, raw_db_path, polarity) "
            "VALUES (1, 's1', 'a.db', 'positive')"
        )
        con.commit()
    _seed_moran_h5ad(tmp_path, "s1", mz=100.0, moran_i=0.75)

    bridge = AnalysisBridge()
    model = bridge.getSpatialCorrelationModel(str(db_path))
    qtbot.waitUntil(lambda: not model.rowsLoading, timeout=2000)

    assert bridge.getMoranIForFeature(str(db_path), 100.0) == {"s1": 0.75}


def test_get_moran_i_for_feature_does_not_trigger_a_load(tmp_path):
    # Cache miss (no prior getSpatialCorrelationModel call for this path)
    # must return {} without kicking off a background .h5ad fetch — this
    # is meant to be a cheap in-memory lookup only, never a surprise I/O.
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()

    bridge = AnalysisBridge()
    assert bridge.getMoranIForFeature(str(db_path), 100.0) == {}
    assert bridge._spatial_correlation_model_path is None


def test_get_moran_i_for_feature_different_analysis_returns_empty(tmp_path, qtbot):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    db_a = tmp_path / "a" / "a.db"
    db_b = tmp_path / "b" / "b.db"
    init_analysis_db(db_a).close()
    init_analysis_db(db_b).close()
    with sqlite3.connect(db_a) as con:
        con.execute(
            "INSERT INTO samples (sample_id, name, raw_db_path, polarity) "
            "VALUES (1, 's1', 'a.db', 'positive')"
        )
        con.commit()
    _seed_moran_h5ad(db_a.parent, "s1", mz=100.0, moran_i=0.5)

    bridge = AnalysisBridge()
    model = bridge.getSpatialCorrelationModel(str(db_a))
    qtbot.waitUntil(lambda: not model.rowsLoading, timeout=2000)

    assert bridge.getMoranIForFeature(str(db_b), 100.0) == {}


#: Pixel count for synthetic localMoranI fixtures below — divisible enough
#: (200) that every percentage these tests actually use (42.5, 10.0, 20.0,
#: 30.0, 90.0) is realizable as an exact whole number of pixels.
_LOCAL_MORAN_FIXTURE_N_PIXELS = 200


def _seed_local_moran_h5ad(out_dir: Path, sample_name: str, mz: float, pct: float) -> None:
    _seed_local_moran_h5ad_multi(out_dir, sample_name, [(mz, pct)])


def _seed_local_moran_h5ad_multi(out_dir: Path, sample_name: str, features: list) -> None:
    """`features`: `[(mz, pct_above_threshold), ...]` — realized as a
    synthetic `adata.layers["localMoranI"]` column per feature (some
    pixels at `2.0`, above the model's default `1.0` threshold, the rest
    at `0.0`) rather than a stored scalar summary — see
    `_load_local_moran_layer_data`'s own docstring for why this `.h5ad`
    format holds full per-pixel arrays now, not a pre-thresholded number.
    `pct_above_threshold` is realized exactly (see
    `_LOCAL_MORAN_FIXTURE_N_PIXELS`), assuming the model reading it back
    uses the same default `1.0` threshold (true here — none of these
    tests seed a `commands` row overriding it).
    """
    n = _LOCAL_MORAN_FIXTURE_N_PIXELS
    var_names = [f"mz_test_{i}" for i in range(len(features))]
    var = pd.DataFrame({"mz": [f[0] for f in features]}, index=var_names)
    adata = ad.AnnData(X=np.zeros((n, len(features))), var=var)
    layer = np.zeros((n, len(features)))
    for col, (_, pct) in enumerate(features):
        n_above = round(pct / 100.0 * n)
        layer[:n_above, col] = 2.0
    adata.layers["localMoranI"] = layer
    adata.write_h5ad(out_dir / f"{sample_name}.h5ad")


def test_get_local_moran_summary_model_reads_percentages_with_name(tmp_path, qtbot):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    _seed_annotated_feature(db_path)  # registers sample "s1", annotates feature 7 as "Caffeine"
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO features (feature_id, mz, members_json) VALUES (7, 123.4567, '{}')"
        )
        con.commit()
    _seed_local_moran_h5ad(tmp_path, "s1", mz=123.4567, pct=42.5)

    bridge = AnalysisBridge()
    model = bridge.getLocalMoranSummaryModel(str(db_path))
    assert model.rowsLoading is True
    qtbot.waitUntil(lambda: not model.rowsLoading, timeout=2000)

    assert model.samples == ["s1"]
    assert model.rowCount() == 1
    assert model.data(model.index(0, 0)) == "123.4567: Caffeine"
    assert model.data(model.index(0, 1)) == "42.5%"


def test_get_local_moran_summary_model_returns_same_instance_for_same_analysis(tmp_path, qtbot):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO samples (sample_id, name, raw_db_path, polarity) "
            "VALUES (1, 's1', 'a.db', 'positive')"
        )
        con.commit()
    _seed_local_moran_h5ad(tmp_path, "s1", mz=100.0, pct=10.0)

    bridge = AnalysisBridge()
    model_1 = bridge.getLocalMoranSummaryModel(str(db_path))
    qtbot.waitUntil(lambda: not model_1.rowsLoading, timeout=2000)
    model_1.nameQuery = "something the user typed"

    model_2 = bridge.getLocalMoranSummaryModel(str(db_path))
    assert model_2 is model_1
    assert model_2.nameQuery == "something the user typed"


def test_get_local_moran_summary_model_new_analysis_replaces_model(tmp_path, qtbot):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    db_a = tmp_path / "a" / "a.db"
    db_b = tmp_path / "b" / "b.db"
    init_analysis_db(db_a).close()
    init_analysis_db(db_b).close()
    for db_path, mz, pct in [(db_a, 100.0, 10.0), (db_b, 200.0, 90.0)]:
        with sqlite3.connect(db_path) as con:
            con.execute(
                "INSERT INTO samples (sample_id, name, raw_db_path, polarity) "
                "VALUES (1, 's1', 'a.db', 'positive')"
            )
            con.commit()
        _seed_local_moran_h5ad(db_path.parent, "s1", mz=mz, pct=pct)

    bridge = AnalysisBridge()
    model_a = bridge.getLocalMoranSummaryModel(str(db_a))
    qtbot.waitUntil(lambda: not model_a.rowsLoading, timeout=2000)
    assert model_a.mzForRow(0) == 100.0

    model_b = bridge.getLocalMoranSummaryModel(str(db_b))
    assert model_b is not model_a
    qtbot.waitUntil(lambda: not model_b.rowsLoading, timeout=2000)
    assert model_b.mzForRow(0) == 200.0


def test_load_local_moran_layer_data_reads_every_feature_not_just_the_last(tmp_path):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO samples (sample_id, name, raw_db_path, polarity) "
            "VALUES (1, 's1', 'a.db', 'positive')"
        )
        con.commit()
    _seed_local_moran_h5ad_multi(
        tmp_path, "s1", [(100.0, 10.0), (200.0, 20.0), (300.0, 30.0)]
    )

    feature_rows, samples = _load_local_moran_layer_data(str(db_path))

    assert samples == ["s1"]
    assert len(feature_rows) == 3
    assert sorted(row["mz"] for row in feature_rows) == [100.0, 200.0, 300.0]
    # Each row's per-sample value is the raw per-pixel array, not a scalar.
    row_100 = next(r for r in feature_rows if r["mz"] == 100.0)
    assert isinstance(row_100["s1"], np.ndarray)
    assert row_100["s1"].shape == (_LOCAL_MORAN_FIXTURE_N_PIXELS,)


def test_load_local_moran_threshold_default_reads_from_commands_arguments(tmp_path):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO commands (command_name, datetime, arguments, run_id) "
            "VALUES ('spatial_correlation', '2026-01-01T00:00:00', ?, 'run1')",
            (json.dumps({"local_threshold": 2.5}),),
        )
        con.commit()

    assert _load_local_moran_threshold_default(str(db_path)) == 2.5


def test_load_local_moran_threshold_default_falls_back_when_never_run(tmp_path):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()

    assert _load_local_moran_threshold_default(str(db_path)) == 1.0


def test_load_local_moran_threshold_default_falls_back_on_malformed_arguments(tmp_path):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO commands (command_name, datetime, arguments, run_id) "
            "VALUES ('spatial_correlation', '2026-01-01T00:00:00', 'not json', 'run1')"
        )
        con.commit()

    assert _load_local_moran_threshold_default(str(db_path)) == 1.0


def test_get_feature_top_hits_for_feature(tmp_path):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    _seed_annotated_feature(db_path)

    bridge = AnalysisBridge()
    rows = bridge.getFeatureTopHits(str(db_path), 7, 5)

    assert len(rows) == 1
    assert rows[0]["sample_name"] == "s1"
    assert rows[0]["library_name"] == "my_library"
    assert rows[0]["compound_name"] == "Caffeine"
    assert rows[0]["id"] == 1  # the ms2_annotations row id, for getMirrorPlotUrl
    assert rows[0]["kind"] == "ms2"


def test_get_feature_top_hits_capped_at_top_n(tmp_path):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    _seed_annotated_feature(db_path)
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO ms2_annotations "
            "(id, feature_id, sample_id, scan_id, library_id, "
            "library_spectrum_id, compound_name, inchikey, score, "
            "dot_product_score, lib_coverage, emp_coverage, coverage_score, "
            "n_matched_peaks, n_lib_peaks, n_emp_peaks_raw, "
            "n_emp_peaks_filtered, rank_ms2, rank_feature) "
            "VALUES (2, 7, 1, 42, 1, 10, 'SecondHit', "
            "'AAAAAAAAAAAAAA-BBBBBBBBBB-N', 0.5, 0.5, 0.5, 0.5, 0.5, "
            "1, 1, 10, 3, 2, 2)"
        )
        con.commit()

    bridge = AnalysisBridge()
    rows = bridge.getFeatureTopHits(str(db_path), 7, 1)

    assert len(rows) == 1
    assert rows[0]["compound_name"] == "Caffeine"  # the higher-scoring one


def test_get_feature_top_hits_empty_for_unknown_feature(tmp_path):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    _seed_annotated_feature(db_path)

    bridge = AnalysisBridge()
    assert bridge.getFeatureTopHits(str(db_path), 999, 5) == []


def test_get_feature_top_hits_includes_predicted_formulas_for_unannotated_feature(
    tmp_path,
):
    from msianalyzer.core.analysis_db import save_predicted_formulas
    from msianalyzer.core.annotation.formula_prediction import PredictedFormula

    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO features (feature_id, mz, members_json) VALUES (2, 181.07, '{}')"
        )
        con.commit()
    save_predicted_formulas(
        db_path,
        [
            PredictedFormula(
                feature_id=2, adduct="[M+H]+", formula="C6H12O6",
                mass_error=0.00003, mass_error_ppm=0.2, rank=1,
            ),
            PredictedFormula(
                feature_id=2, adduct="[M+Na]+", formula="C7H16OS2",
                mass_error=0.0008, mass_error_ppm=4.6, rank=1,
            ),
        ],
    )

    bridge = AnalysisBridge()
    rows = bridge.getFeatureTopHits(str(db_path), 2, 5)

    assert len(rows) == 2
    assert all(r["kind"] == "predicted" for r in rows)
    assert rows[0]["formula"] == "C6H12O6"  # closest ppm error first
    assert rows[0]["adduct"] == "[M+H]+"
    # Negative, distinct id space — never collides with a real
    # ms2_annotations.id, which is always a positive AUTOINCREMENT PK.
    assert rows[0]["id"] < 0


def test_get_feature_top_hits_ms2_then_predicted_when_a_feature_has_both(tmp_path):
    from msianalyzer.core.analysis_db import save_predicted_formulas
    from msianalyzer.core.annotation.formula_prediction import PredictedFormula

    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    _seed_annotated_feature(db_path)  # ms2_annotations row for feature 7 -> "Caffeine"
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO features (feature_id, mz, members_json) VALUES (7, 123.4567, '{}')"
        )
        con.commit()
    save_predicted_formulas(
        db_path,
        [
            PredictedFormula(
                feature_id=7, adduct="[M+H]+", formula="C99H99O99",
                mass_error=0.0, mass_error_ppm=1.0, rank=1,
            ),
        ],
    )

    bridge = AnalysisBridge()
    rows = bridge.getFeatureTopHits(str(db_path), 7, 5)

    assert [r["kind"] for r in rows] == ["ms2", "predicted"]


def _request_mirror_plot_sync(bridge, qtbot, *args, timeout=5000) -> dict:
    """`requestMirrorPlot` is fire-and-forget (see the class docstring —
    it runs on a background `MirrorPlotWorker` so slow/remote raw-source
    I/O never blocks the GUI thread); tests wait for `mirrorPlotReady`
    and return the dict it carried."""
    with qtbot.waitSignal(bridge.mirrorPlotReady, timeout=timeout) as blocker:
        bridge.requestMirrorPlot(*args)
    return blocker.args[0]


def test_request_mirror_plot_emits_plot_data_for_valid_annotation(tmp_path, qtbot):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    _seed_annotated_feature(db_path)

    bridge = AnalysisBridge()
    data = _request_mirror_plot_sync(bridge, qtbot, str(db_path), 1, "filtered", "filtered")

    assert "error" not in data
    assert data["title"].startswith("Caffeine  (C8H10N4O2)")
    assert {g["key"] for g in data["groups"]} <= {"matched", "unmatched"}
    peaks = [p for g in data["groups"] for p in g["peaks"]]
    # Empirical sticks point up, library sticks down.
    assert any(y > 0 for _, y in peaks) and any(y < 0 for _, y in peaks)
    for mz, top, bottom in data["connectors"]:
        assert top > 0 > bottom


def test_request_mirror_plot_emits_error_for_unknown_id(tmp_path, qtbot):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()

    bridge = AnalysisBridge()
    data = _request_mirror_plot_sync(bridge, qtbot, str(db_path), 999, "filtered", "filtered")

    assert "No annotation found" in data["error"]


def test_request_mirror_plot_emits_error_without_raw_spectra(tmp_path, qtbot):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    _seed_annotated_feature(db_path, with_raw_spectra=False)

    bridge = AnalysisBridge()
    data = _request_mirror_plot_sync(bridge, qtbot, str(db_path), 1, "filtered", "filtered")

    assert "raw empirical spectrum not found" in data["error"]


def test_request_mirror_plot_emits_error_for_unavailable_raw_source(tmp_path, qtbot):
    # No stored emp_raw_*, and 'a.db' (the seeded sample's raw_db_path)
    # doesn't exist on disk either -> both resolution paths fail.
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    _seed_annotated_feature(db_path, with_raw_spectra=False)

    bridge = AnalysisBridge()
    data = _request_mirror_plot_sync(bridge, qtbot, str(db_path), 1, "raw", "filtered")

    assert "raw empirical spectrum not found" in data["error"]


def test_request_mirror_plot_stale_request_is_discarded(tmp_path, qtbot):
    # Firing a second request before the first's worker thread has
    # finished must not let the first one's (now-stale) result win —
    # only one mirrorPlotReady should reach QML, for the newer request.
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    _seed_annotated_feature(db_path)

    bridge = AnalysisBridge()
    received = []
    bridge.mirrorPlotReady.connect(received.append)

    bridge.requestMirrorPlot(str(db_path), 1, "filtered", "filtered")
    first_worker = bridge._mirror_plot_worker
    bridge.requestMirrorPlot(str(db_path), 1, "filtered", "filtered")

    qtbot.waitUntil(lambda: not first_worker.isRunning(), timeout=5000)
    qtbot.waitUntil(lambda: len(received) >= 1, timeout=5000)
    qtbot.wait(50)  # give a stray first-request signal a chance to (wrongly) arrive too

    assert len(received) == 1


def test_get_basic_mirror_plot_image_returns_png_data_uri(tmp_path):
    # Always synchronous (filtered-only, no raw I/O) — the Annotations
    # section's inline default view, no WebEngineView involved.
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    _seed_annotated_feature(db_path)

    bridge = AnalysisBridge()
    uri = bridge.getBasicMirrorPlotImage(str(db_path), 1)

    assert uri.startswith("data:image/png;base64,")
    import base64
    png = base64.b64decode(uri.split(",", 1)[1])
    assert png.startswith(b"\x89PNG\r\n\x1a\n")


def test_get_basic_mirror_plot_image_returns_placeholder_for_unknown_id(tmp_path):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()

    bridge = AnalysisBridge()
    uri = bridge.getBasicMirrorPlotImage(str(db_path), 999)

    assert uri.startswith("data:image/png;base64,")
    import base64
    png = base64.b64decode(uri.split(",", 1)[1])
    assert png.startswith(b"\x89PNG\r\n\x1a\n")


def test_get_annotation_metadata_returns_scores_and_identifiers(tmp_path):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    _seed_annotated_feature(db_path)

    bridge = AnalysisBridge()
    meta = bridge.getAnnotationMetadata(str(db_path), 1)

    assert meta["compound_name"] == "Caffeine"
    assert meta["compound_formula"] == "C8H10N4O2"
    assert meta["library_name"] == "my_library"
    assert meta["score"] == 0.87
    assert meta["scan_id"] == 42
    assert meta["precursor_mz"] == 150.1234
    assert meta["fragment_ppm_tolerance"] == 10.0  # class default, no commands row


def test_get_annotation_metadata_unknown_id_returns_empty_dict(tmp_path):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()

    bridge = AnalysisBridge()
    meta = bridge.getAnnotationMetadata(str(db_path), 999)

    assert meta["compound_name"] == ""
    assert meta["score"] is None


def test_get_annotation_metadata_missing_db_returns_empty_dict(tmp_path):
    bridge = AnalysisBridge()
    meta = bridge.getAnnotationMetadata(str(tmp_path / "does_not_exist.db"), 1)

    assert meta["compound_name"] == ""
    assert meta["scan_id"] is None


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


def _write_heatmap_fixture_h5ad(tmp_path: Path, obs: pd.DataFrame, mz: float = 100.0) -> None:
    var = pd.DataFrame({"mz": [mz]}, index=["mz_test"])
    X = csr_matrix(np.array([5.0, 10.0, 15.0, 20.0], dtype=np.float32).reshape(-1, 1))
    adata = ad.AnnData(X=X, obs=obs, var=var)
    adata.obsm["spatial"] = np.array(
        [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0)], dtype=float
    )
    adata.write_h5ad(tmp_path / "s1.h5ad")


def test_request_feature_value_range_emits_range_from_heatmap_provider(tmp_path, qtbot):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    _write_heatmap_fixture_h5ad(tmp_path, pd.DataFrame(index=["a", "b", "c", "d"]))

    bridge = AnalysisBridge()
    bridge.setHeatmapAnalysis(str(db_path))

    with qtbot.waitSignal(bridge.featureValueRangeReady, timeout=2000) as spy:
        bridge.requestFeatureValueRange(["s1"], 100.0, "raw")

    assert spy.args[0] == {"vmin": 5.0, "vmax": 20.0}


def test_request_feature_value_range_stale_request_is_discarded(tmp_path, qtbot):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    _write_heatmap_fixture_h5ad(tmp_path, pd.DataFrame(index=["a", "b", "c", "d"]))

    bridge = AnalysisBridge()
    bridge.setHeatmapAnalysis(str(db_path))
    received = []
    bridge.featureValueRangeReady.connect(received.append)

    bridge.requestFeatureValueRange(["s1"], 100.0, "raw")
    first_worker = bridge._feature_value_range_worker
    bridge.requestFeatureValueRange(["s1"], 100.0, "raw")

    qtbot.waitUntil(lambda: not first_worker.isRunning(), timeout=5000)
    qtbot.waitUntil(lambda: len(received) >= 1, timeout=5000)
    qtbot.wait(50)  # give a stray first-request signal a chance to (wrongly) arrive too

    assert len(received) == 1


def test_request_obs_columns_emits_columns_from_heatmap_provider(tmp_path, qtbot):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    _write_heatmap_fixture_h5ad(
        tmp_path,
        pd.DataFrame(
            {"tic": [1.0, 2.0, 3.0, 4.0], "region": ["a", "a", "b", "b"]},
            index=["a", "b", "c", "d"],
        ),
    )

    bridge = AnalysisBridge()
    bridge.setHeatmapAnalysis(str(db_path))

    with qtbot.waitSignal(bridge.obsColumnsReady, timeout=2000) as spy:
        bridge.requestObsColumns(["s1"])

    columns = {c["name"]: c["numeric"] for c in spy.args[0]}
    assert columns["tic"] is True
    assert columns["region"] is False


def test_request_obs_columns_stale_request_is_discarded(tmp_path, qtbot):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    _write_heatmap_fixture_h5ad(
        tmp_path, pd.DataFrame({"tic": [1.0, 2.0, 3.0, 4.0]}, index=["a", "b", "c", "d"])
    )

    bridge = AnalysisBridge()
    bridge.setHeatmapAnalysis(str(db_path))
    received = []
    bridge.obsColumnsReady.connect(received.append)

    bridge.requestObsColumns(["s1"])
    first_worker = bridge._obs_columns_worker
    bridge.requestObsColumns(["s1"])

    qtbot.waitUntil(lambda: not first_worker.isRunning(), timeout=5000)
    qtbot.waitUntil(lambda: len(received) >= 1, timeout=5000)
    qtbot.wait(50)

    assert len(received) == 1


def test_request_obs_value_range_emits_range_from_heatmap_provider(tmp_path, qtbot):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    _write_heatmap_fixture_h5ad(
        tmp_path, pd.DataFrame({"tic": [1.0, 2.0, 3.0, 40.0]}, index=["a", "b", "c", "d"])
    )

    bridge = AnalysisBridge()
    bridge.setHeatmapAnalysis(str(db_path))

    with qtbot.waitSignal(bridge.obsValueRangeReady, timeout=2000) as spy:
        bridge.requestObsValueRange(["s1"], "tic")

    assert spy.args[0] == {"vmin": 1.0, "vmax": 40.0}


def test_request_obs_value_range_stale_request_is_discarded(tmp_path, qtbot):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    _write_heatmap_fixture_h5ad(
        tmp_path, pd.DataFrame({"tic": [1.0, 2.0, 3.0, 40.0]}, index=["a", "b", "c", "d"])
    )

    bridge = AnalysisBridge()
    bridge.setHeatmapAnalysis(str(db_path))
    received = []
    bridge.obsValueRangeReady.connect(received.append)

    bridge.requestObsValueRange(["s1"], "tic")
    first_worker = bridge._obs_value_range_worker
    bridge.requestObsValueRange(["s1"], "tic")

    qtbot.waitUntil(lambda: not first_worker.isRunning(), timeout=5000)
    qtbot.waitUntil(lambda: len(received) >= 1, timeout=5000)
    qtbot.wait(50)

    assert len(received) == 1


def test_request_obs_categories_emits_categories_from_heatmap_provider(tmp_path, qtbot):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    _write_heatmap_fixture_h5ad(
        tmp_path,
        pd.DataFrame(
            {"polarity": ["positive", "positive", "negative", "negative"]},
            index=["a", "b", "c", "d"],
        ),
    )

    bridge = AnalysisBridge()
    bridge.setHeatmapAnalysis(str(db_path))

    with qtbot.waitSignal(bridge.obsCategoriesReady, timeout=2000) as spy:
        bridge.requestObsCategories(["s1"], "polarity")

    assert [c["category"] for c in spy.args[0]] == ["negative", "positive"]


def test_request_obs_categories_stale_request_is_discarded(tmp_path, qtbot):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    _write_heatmap_fixture_h5ad(
        tmp_path, pd.DataFrame({"polarity": ["positive", "negative"] * 2}, index=["a", "b", "c", "d"])
    )

    bridge = AnalysisBridge()
    bridge.setHeatmapAnalysis(str(db_path))
    received = []
    bridge.obsCategoriesReady.connect(received.append)

    bridge.requestObsCategories(["s1"], "polarity")
    first_worker = bridge._obs_categories_worker
    bridge.requestObsCategories(["s1"], "polarity")

    qtbot.waitUntil(lambda: not first_worker.isRunning(), timeout=5000)
    qtbot.waitUntil(lambda: len(received) >= 1, timeout=5000)
    qtbot.wait(50)

    assert len(received) == 1


def test_categorize_peaks_matches_nearest_feature():
    feature_categories = pd.DataFrame(
        {"feature_id": [1, 2, 3], "mz": [100.0, 200.0, 300.0],
         "category": ["no_ms2", "non_annotated", "annotated"]}
    )

    result = _categorize_peaks(np.array([100.1, 199.0, 350.0]), feature_categories)

    assert list(result) == ["no_ms2", "non_annotated", "annotated"]


def test_categorize_peaks_defaults_to_non_annotated_without_features():
    result = _categorize_peaks(np.array([100.0, 200.0]), pd.DataFrame())

    assert list(result) == ["non_annotated", "non_annotated"]


def test_get_spectrum_data_returns_peaks_for_saved_spectrum(tmp_path):
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
    data = bridge.getSpectrumData(str(db_path), "run-1", sample_id)

    # No features at all -> every peak is "non_annotated".
    assert data["groups"] == [
        {"key": "non_annotated", "name": "Non-annotated", "peaks": [[100.0, 10.0], [200.0, 20.0]]}
    ]


def test_get_spectrum_data_groups_peaks_by_feature_category(tmp_path):
    # "I want to distinguish between features with no ms2 (gray), annotated
    # features (blue) and non annotated features (black)."
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    sample_id = register_sample(db_path, name="s1", raw_db_path=tmp_path / "s1.db")
    command_id = log_command(
        db_path, "filter_spectra", {}, run_id="run-1", sample_id=sample_id
    )
    save_aggregated_spectra(
        np.array([100.0, 200.0, 300.0]),
        np.array([10.0, 20.0, 30.0]),
        analysis_db_path=db_path,
        run_id="run-1",
        sample_id=sample_id,
        command_id=command_id,
    )
    with sqlite3.connect(db_path) as con:
        con.executemany(
            "INSERT INTO features (feature_id, mz, members_json) VALUES (?, ?, ?)",
            [
                (1, 100.0, '{"s1": 0}'),  # no MS2 -> gray
                (2, 200.0, '{"s1": 0}'),  # MS2, no annotation -> black
                (3, 300.0, '{"s1": 0}'),  # MS2 + annotation -> blue
            ],
        )
        con.execute(
            "INSERT INTO feature_ms2_summary (feature_id, feature_mz, n_ms2, "
            "n_samples, mean_fragmentation_factor, n_single_peak, "
            "n_flat_fragmentation, median_n_peaks) "
            "VALUES (2, 200.0, 3, 1, 0, 0, 0, 3.0)"
        )
        con.execute(
            "INSERT INTO feature_ms2_summary (feature_id, feature_mz, n_ms2, "
            "n_samples, mean_fragmentation_factor, n_single_peak, "
            "n_flat_fragmentation, median_n_peaks) "
            "VALUES (3, 300.0, 2, 1, 0, 0, 0, 3.0)"
        )
        con.execute(
            "INSERT INTO annotation_libraries (id, path, name) "
            "VALUES (1, 'lib.db', 'my_library')"
        )
        con.execute(
            "INSERT INTO ms2_associations "
            "(sample_id, scan_id, match_key, precursor_mz, "
            "rt, n_peaks, polarity) "
            "VALUES (1, 42, 'k1', 300.1, 12.3, 5, 'positive')"
        )
        con.execute(
            "INSERT INTO ms2_annotations "
            "(id, feature_id, sample_id, scan_id, library_id, "
            "library_spectrum_id, compound_name, compound_formula, inchikey, "
            "score, dot_product_score, lib_coverage, emp_coverage, "
            "coverage_score, n_matched_peaks, n_lib_peaks, n_emp_peaks_raw, "
            "n_emp_peaks_filtered, rank_ms2, rank_feature) "
            "VALUES (1, 3, 1, 42, 1, 9, 'Caffeine', 'C8H10N4O2', "
            "'RYYVLZVUVIJVGH-UHFFFAOYSA-N', 0.87, 0.9, 0.8, 0.75, 0.77, "
            "2, 2, 10, 3, 1, 1)"
        )
        con.commit()

    bridge = AnalysisBridge()
    groups = bridge.getSpectrumData(str(db_path), "run-1", sample_id)["groups"]

    # Colors are the plot's (Theme.qml); the bridge only splits and labels.
    assert [(g["key"], g["name"], g["peaks"]) for g in groups] == [
        ("no_ms2", "No MS2", [[100.0, 10.0]]),
        ("non_annotated", "Non-annotated", [[200.0, 20.0]]),
        ("annotated", "Annotated", [[300.0, 30.0]]),
    ]


def test_get_spectrum_data_returns_error_when_missing(tmp_path):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()

    bridge = AnalysisBridge()
    data = bridge.getSpectrumData(str(db_path), "run-1", 1)

    assert data["groups"] == []
    assert "No spectrum found" in data["error"]


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
            "n_samples, mean_fragmentation_factor, n_single_peak, "
            "n_flat_fragmentation, median_n_peaks) "
            "VALUES (7, 150.0, 5, 1, 0, 0, 0, 3.0)"
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
    # "top hit also should show the library in which it has been hit"
    assert detail["top_hits"][0]["library_name"] == "my_library"


def test_get_feature_detail_empty_when_no_features(tmp_path):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()

    bridge = AnalysisBridge()
    assert bridge.getFeatureDetail(str(db_path), 100.0, 5) == {}


def test_get_feature_detail_empty_for_missing_db(tmp_path):
    bridge = AnalysisBridge()
    assert bridge.getFeatureDetail(str(tmp_path / "nope.db"), 100.0, 5) == {}


# ---------------------------------------------------------------------------
# ROI Design
# ---------------------------------------------------------------------------


def _write_roi_sample_h5ad(path):
    import anndata as ad
    import pandas as pd
    from scipy.sparse import csr_matrix

    obs = pd.DataFrame(index=["a", "b", "c", "d"])
    var = pd.DataFrame({"mz": [100.0]}, index=["mz_100.0000"])
    X = csr_matrix(np.array([5.0, 10.0, 15.0, 20.0], dtype=np.float32).reshape(-1, 1))
    adata = ad.AnnData(X=X, obs=obs, var=var)
    adata.obsm["spatial"] = np.array(
        [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0)], dtype=float
    )
    adata.write_h5ad(path)


_ROI_VERTICES = [[0, 0], [2, 0], [2, 2], [0, 2]]  # covers the whole 2x2 grid


def test_save_roi_writes_h5ad_and_registers_catalog_entry(tmp_path):
    import anndata as ad

    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    _write_roi_sample_h5ad(tmp_path / "s1.h5ad")

    bridge = AnalysisBridge()
    result = bridge.saveRoi(str(db_path), "s1", "liver", "#ff0000", _ROI_VERTICES)

    assert result == {"ok": True, "pixel_count": 4}
    reread = ad.read_h5ad(tmp_path / "s1.h5ad")
    assert reread.obs["roi_liver"].sum() == 4
    catalog = bridge.getRois(str(db_path))
    assert catalog == [{"id": 1, "name": "liver", "color": "#ff0000", "created_at": catalog[0]["created_at"]}]


def test_save_roi_reuses_existing_catalog_color_when_name_exists(tmp_path):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    _write_roi_sample_h5ad(tmp_path / "s1.h5ad")
    _write_roi_sample_h5ad(tmp_path / "s2.h5ad")

    bridge = AnalysisBridge()
    bridge.saveRoi(str(db_path), "s1", "liver", "#ff0000", _ROI_VERTICES)
    result = bridge.saveRoi(str(db_path), "s2", "liver", "#0000ff", _ROI_VERTICES)

    assert result["ok"] is True
    catalog = bridge.getRois(str(db_path))
    assert len(catalog) == 1
    assert catalog[0]["color"] == "#ff0000"  # first save's color wins, not "#0000ff"


def test_save_roi_rejects_fewer_than_3_vertices(tmp_path):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    _write_roi_sample_h5ad(tmp_path / "s1.h5ad")

    bridge = AnalysisBridge()
    result = bridge.saveRoi(str(db_path), "s1", "liver", "#ff0000", [[0, 0], [1, 1]])

    assert result["ok"] is False
    assert bridge.getRois(str(db_path)) == []


def test_save_roi_missing_sample_h5ad_returns_error(tmp_path):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()

    bridge = AnalysisBridge()
    result = bridge.saveRoi(str(db_path), "nope", "liver", "#ff0000", _ROI_VERTICES)

    assert result["ok"] is False


def test_save_roi_invalidates_heatmap_provider_cache(tmp_path):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    _write_roi_sample_h5ad(tmp_path / "s1.h5ad")

    bridge = AnalysisBridge()
    bridge.setHeatmapAnalysis(str(db_path))
    # Straight to the provider, not through the (now-async) bridge Slot —
    # this test only cares that saveRoi() invalidates the provider's own
    # cache, not about the request/response plumbing on top of it.
    bridge.heatmap_provider.getFeatureValueRange(["s1"], 100.0, "raw")  # populates the cache
    assert str(tmp_path / "s1.h5ad") in bridge.heatmap_provider._cache

    bridge.saveRoi(str(db_path), "s1", "liver", "#ff0000", _ROI_VERTICES)

    assert str(tmp_path / "s1.h5ad") not in bridge.heatmap_provider._cache


def test_get_sample_rois_reads_back_saved_roi(tmp_path):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    _write_roi_sample_h5ad(tmp_path / "s1.h5ad")

    bridge = AnalysisBridge()
    bridge.saveRoi(str(db_path), "s1", "liver", "#ff0000", _ROI_VERTICES)

    rois = bridge.getSampleRois(str(db_path), "s1")

    assert len(rois) == 1
    assert rois[0]["name"] == "liver"
    assert rois[0]["color"] == "#ff0000"


def test_get_sample_rois_empty_for_sample_without_h5ad(tmp_path):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()

    bridge = AnalysisBridge()
    assert bridge.getSampleRois(str(db_path), "nope") == []


def test_delete_roi_from_sample_removes_it(tmp_path):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    _write_roi_sample_h5ad(tmp_path / "s1.h5ad")

    bridge = AnalysisBridge()
    bridge.saveRoi(str(db_path), "s1", "liver", "#ff0000", _ROI_VERTICES)

    removed = bridge.deleteRoiFromSample(str(db_path), "s1", "liver")

    assert removed is True
    assert bridge.getSampleRois(str(db_path), "s1") == []


def test_delete_roi_everywhere_removes_catalog_and_every_sample(tmp_path):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    _write_roi_sample_h5ad(tmp_path / "s1.h5ad")
    _write_roi_sample_h5ad(tmp_path / "s2.h5ad")

    bridge = AnalysisBridge()
    bridge.saveRoi(str(db_path), "s1", "liver", "#ff0000", _ROI_VERTICES)
    bridge.saveRoi(str(db_path), "s2", "liver", "#ff0000", _ROI_VERTICES)

    result = bridge.deleteRoiEverywhere(str(db_path), ["s1", "s2"], "liver")

    assert result["catalog_removed"] is True
    assert set(result["samples_removed"]) == {"s1", "s2"}
    assert bridge.getRois(str(db_path)) == []
    assert bridge.getSampleRois(str(db_path), "s1") == []
    assert bridge.getSampleRois(str(db_path), "s2") == []


def test_next_roi_color_cycles_by_catalog_size(tmp_path):
    from msianalyzer.core.plotting.heatmap import category_color

    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    _write_roi_sample_h5ad(tmp_path / "s1.h5ad")

    bridge = AnalysisBridge()
    assert bridge.nextRoiColor(str(db_path)) == category_color(0)

    bridge.saveRoi(str(db_path), "s1", "liver", "#ff0000", _ROI_VERTICES)

    assert bridge.nextRoiColor(str(db_path)) == category_color(1)


def test_next_roi_color_missing_db_returns_first_palette_color(tmp_path):
    from msianalyzer.core.plotting.heatmap import category_color

    bridge = AnalysisBridge()
    assert bridge.nextRoiColor(str(tmp_path / "nope.db")) == category_color(0)


# ---------------------------------------------------------------------------
# H&E image coregistration (attachHeImage / getRegistrationInfo / saveRegistration)
# ---------------------------------------------------------------------------


def _make_he_png(path, size=(20, 10)):
    from PIL import Image as PILImage

    width, height = size
    arr = np.zeros((height, width, 3), dtype=np.uint8)
    PILImage.fromarray(arr).save(path, format="PNG")


def _seed_coreg_sample(tmp_path, sample_name="s1"):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    raw_db_path = tmp_path / f"{sample_name}.db"
    register_sample(db_path, name=sample_name, raw_db_path=raw_db_path)
    return db_path, raw_db_path


_COREG_LANDMARKS = [
    {"heX": 0.0, "heY": 0.0, "gridX": 0.0, "gridY": 0.0},
    {"heX": 10.0, "heY": 0.0, "gridX": 5.0, "gridY": 0.0},
    {"heX": 0.0, "heY": 10.0, "gridX": 0.0, "gridY": 5.0},
]


def test_get_registration_info_empty_for_sample_without_raw_db(tmp_path):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()

    bridge = AnalysisBridge()
    info = bridge.getRegistrationInfo(str(db_path), "nope")

    assert info["hasImage"] is False
    assert info["hasFit"] is False
    assert info["landmarks"] == []


def test_attach_he_image_missing_sample_returns_error(tmp_path):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    image_path = tmp_path / "slide.png"
    _make_he_png(image_path)

    bridge = AnalysisBridge()
    result = bridge.attachHeImage(str(db_path), "nope", str(image_path))

    assert result["ok"] is False


def test_attach_he_image_success_reflected_in_registration_info(tmp_path):
    db_path, _raw_db_path = _seed_coreg_sample(tmp_path)
    image_path = tmp_path / "slide.png"
    _make_he_png(image_path, size=(20, 10))

    bridge = AnalysisBridge()
    result = bridge.attachHeImage(str(db_path), "s1", str(image_path))

    assert result["ok"] is True
    assert result["width"] == 20
    assert result["height"] == 10
    assert result["format"] == "PNG"

    info = bridge.getRegistrationInfo(str(db_path), "s1")
    assert info["hasImage"] is True
    assert info["hasFit"] is False
    assert info["width"] == 20
    assert info["height"] == 10


def test_attach_he_image_rejects_unsupported_format(tmp_path):
    db_path, _raw_db_path = _seed_coreg_sample(tmp_path)
    image_path = tmp_path / "slide.bmp"
    from PIL import Image as PILImage

    PILImage.fromarray(np.zeros((5, 5, 3), dtype=np.uint8)).save(image_path, format="BMP")

    bridge = AnalysisBridge()
    result = bridge.attachHeImage(str(db_path), "s1", str(image_path))

    assert result["ok"] is False
    assert "unsupported" in result["error"]


def test_save_registration_round_trips_through_registration_info(tmp_path):
    db_path, _raw_db_path = _seed_coreg_sample(tmp_path)
    image_path = tmp_path / "slide.png"
    _make_he_png(image_path)

    bridge = AnalysisBridge()
    bridge.attachHeImage(str(db_path), "s1", str(image_path))

    result = bridge.saveRegistration(str(db_path), "s1", _COREG_LANDMARKS, "affine")

    assert result["ok"] is True
    assert result["rmse"] == pytest.approx(0.0, abs=1e-6)
    assert len(result["landmarks"]) == 3

    info = bridge.getRegistrationInfo(str(db_path), "s1")
    assert info["hasFit"] is True
    assert info["transformType"] == "affine"
    assert len(info["landmarks"]) == 3
    # _COREG_LANDMARKS: (0,0)->(0,0), (10,0)->(5,0), (0,10)->(0,5) -> a
    # uniform 0.5x scale, no rotation/translation.
    np.testing.assert_allclose(info["matrix"], [[0.5, 0.0, 0.0], [0.0, 0.5, 0.0]], atol=1e-9)


def test_get_registration_info_matrix_none_without_fit(tmp_path):
    db_path, _raw_db_path = _seed_coreg_sample(tmp_path)
    image_path = tmp_path / "slide.png"
    _make_he_png(image_path)
    bridge = AnalysisBridge()
    bridge.attachHeImage(str(db_path), "s1", str(image_path))

    info = bridge.getRegistrationInfo(str(db_path), "s1")

    assert info["hasFit"] is False
    assert info["matrix"] is None


def test_get_registration_marks_per_sample_state(tmp_path):
    db_path, _ = _seed_coreg_sample(tmp_path, "s1")
    register_sample(db_path, name="s2", raw_db_path=tmp_path / "s2.db")
    image_path = tmp_path / "slide.png"
    _make_he_png(image_path)
    bridge = AnalysisBridge()
    changes = []
    bridge.registrationChanged.connect(lambda: changes.append(1))

    bridge.attachHeImage(str(db_path), "s1", str(image_path))
    assert bridge.getRegistrationMarks(str(db_path), ["s1", "s2"]) == {
        "s1": " · H&E not fitted", "s2": "",
    }

    bridge.saveRegistration(str(db_path), "s1", _COREG_LANDMARKS, "affine")
    assert bridge.getRegistrationMarks(str(db_path), ["s1"]) == {"s1": " · H&E ✓"}
    assert len(changes) == 2


def test_save_registration_without_attached_image_returns_error(tmp_path):
    db_path, _raw_db_path = _seed_coreg_sample(tmp_path)

    bridge = AnalysisBridge()
    result = bridge.saveRegistration(str(db_path), "s1", _COREG_LANDMARKS, "affine")

    assert result["ok"] is False


def test_save_registration_rejects_too_few_landmarks(tmp_path):
    db_path, _raw_db_path = _seed_coreg_sample(tmp_path)
    image_path = tmp_path / "slide.png"
    _make_he_png(image_path)

    bridge = AnalysisBridge()
    bridge.attachHeImage(str(db_path), "s1", str(image_path))

    result = bridge.saveRegistration(str(db_path), "s1", _COREG_LANDMARKS[:2], "affine")

    assert result["ok"] is False


# ---------------------------------------------------------------------------
# formula prediction (predictFormulas / getFeaturesForPrediction)
# ---------------------------------------------------------------------------


def test_get_features_for_prediction_missing_db_returns_empty(tmp_path):
    bridge = AnalysisBridge()
    assert bridge.getFeaturesForPrediction(str(tmp_path / "nope.db")) == []


def test_get_features_for_prediction_shows_status_columns(tmp_path):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    _seed_annotated_feature(db_path)  # ms2_annotations row for feature 7 -> "Caffeine"
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO features (feature_id, mz, members_json) VALUES (7, 123.4567, '{}')"
        )
        con.execute(
            "INSERT INTO features (feature_id, mz, members_json) VALUES (2, 50.0, '{}')"
        )
        con.commit()

    bridge = AnalysisBridge()
    rows = {r["feature_id"]: r for r in bridge.getFeaturesForPrediction(str(db_path))}

    assert rows[7]["compound_name"] == "Caffeine"
    assert rows[2]["compound_name"] is None


def _predict_formulas_sync(bridge, qtbot, *args, timeout=5000):
    """`predictFormulas` is fire-and-forget (see the class docstring — the
    first invocation on a machine can mean a real, multi-second ~420MB
    download, so it must never block the GUI thread); tests wait for
    either completion signal and return `(signal_name, args)`."""
    from unittest.mock import patch

    from msianalyzer.core.annotation.formula_prediction import PredictedFormula

    fake_rows = [
        PredictedFormula(
            feature_id=2, adduct="[M+H]+", formula="C6H12O6",
            mass_error=0.00003, mass_error_ppm=0.2, rank=1,
        )
    ]
    with patch(
        "msianalyzer.gui.utils.formula_prediction_worker.run_formula_prediction",
        return_value=fake_rows,
    ):
        with qtbot.waitSignal(
            bridge.formulaPredictionFinished, timeout=timeout, raising=False
        ) as finished_blocker:
            bridge.predictFormulas(*args)
        qtbot.wait(50)
    return finished_blocker


def test_predict_formulas_emits_finished_on_success(tmp_path, qtbot):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO features (feature_id, mz, members_json) VALUES (2, 181.0707, '{}')"
        )
        con.commit()

    bridge = AnalysisBridge()
    blocker = _predict_formulas_sync(
        bridge, qtbot, str(db_path), [2], {"adducts": ["[M+H]+"]}
    )

    assert blocker.signal_triggered
    assert blocker.args == [str(db_path)]


def test_predict_formulas_emits_failed_for_invalid_settings(tmp_path, qtbot):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()

    bridge = AnalysisBridge()
    with qtbot.waitSignal(bridge.formulaPredictionFailed, timeout=5000) as blocker:
        bridge.predictFormulas(str(db_path), [1], {"adducts": []})  # empty -> invalid

    assert blocker.args[0] == str(db_path)
    assert "adducts" in blocker.args[1]


def test_predict_formulas_stale_request_is_discarded(tmp_path, qtbot):
    # Same "second request supersedes the first" discard rule as
    # requestMirrorPlot — only the newer request's completion should
    # reach QML.
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO features (feature_id, mz, members_json) VALUES (2, 100.0, '{}')"
        )
        con.commit()

    from unittest.mock import patch

    from msianalyzer.core.annotation.formula_prediction import PredictedFormula

    bridge = AnalysisBridge()
    received = []
    bridge.formulaPredictionFinished.connect(received.append)

    with patch(
        "msianalyzer.gui.utils.formula_prediction_worker.run_formula_prediction",
        return_value=[
            PredictedFormula(
                feature_id=2, adduct="[M+H]+", formula="A",
                mass_error=0.0, mass_error_ppm=1.0, rank=1,
            )
        ],
    ):
        bridge.predictFormulas(str(db_path), [2], {"adducts": ["[M+H]+"]})
        first_worker = bridge._formula_prediction_worker
        bridge.predictFormulas(str(db_path), [2], {"adducts": ["[M+H]+"]})

        qtbot.waitUntil(lambda: not first_worker.isRunning(), timeout=5000)
        qtbot.waitUntil(lambda: len(received) >= 1, timeout=5000)
        qtbot.wait(50)

    assert len(received) == 1


# ==============================================================================
# TESTS FOR _load_normalized_layer_name / getNormalizedLayerName
# ==============================================================================


def test_load_normalized_layer_name_defaults_to_tic_when_never_run(tmp_path):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()

    assert _load_normalized_layer_name(str(db_path)) == "TIC"


def test_load_normalized_layer_name_tic_mode(tmp_path):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO commands (command_name, datetime, arguments, run_id) "
            "VALUES ('normalize_tic', '2026-01-01T00:00:00', ?, 'run1')",
            (json.dumps({"mode": "tic"}),),
        )
        con.commit()

    assert _load_normalized_layer_name(str(db_path)) == "TIC"


def test_load_normalized_layer_name_reference_mz_mode(tmp_path):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO commands (command_name, datetime, arguments, run_id) "
            "VALUES ('normalize_tic', '2026-01-01T00:00:00', ?, 'run1')",
            (json.dumps({"mode": "reference_mz", "reference_mz": 154.0863}),),
        )
        con.commit()

    assert _load_normalized_layer_name(str(db_path)) == "mz_154.0863"


def test_load_normalized_layer_name_falls_back_on_malformed_arguments(tmp_path):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO commands (command_name, datetime, arguments, run_id) "
            "VALUES ('normalize_tic', '2026-01-01T00:00:00', 'not json', 'run1')"
        )
        con.commit()

    assert _load_normalized_layer_name(str(db_path)) == "TIC"


def test_get_normalized_layer_name_delegates_for_valid_db(tmp_path):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO commands (command_name, datetime, arguments, run_id) "
            "VALUES ('normalize_tic', '2026-01-01T00:00:00', ?, 'run1')",
            (json.dumps({"mode": "reference_mz", "reference_mz": 154.0863}),),
        )
        con.commit()

    bridge = AnalysisBridge()
    assert bridge.getNormalizedLayerName(str(db_path)) == "mz_154.0863"


def test_get_normalized_layer_name_missing_db_returns_tic(tmp_path):
    bridge = AnalysisBridge()
    assert bridge.getNormalizedLayerName(str(tmp_path / "nope.db")) == "TIC"


# ==============================================================================
# TESTS FOR calibration curve quantification (targeted analyses)
# ==============================================================================


#: Constant across every pixel in `_seed_calibration_fixture`'s h5ad, so a
#: standard/unknown's internal-standard-corrected `mean_intensity` (analyte
#: ratio to this) is just each pixel's raw analyte value / 5.0 — easy to
#: hand-compute for assertions.
_SEED_REFERENCE_MZ = 259.0
_SEED_REFERENCE_VALUE = 5.0


def _seed_calibration_h5ad(
    tmp_path: Path, sample_name: str, mz: float, values, roi_masks,
    reference_mz: float = _SEED_REFERENCE_MZ,
):
    n = len(values)
    obs = pd.DataFrame(index=[str(i) for i in range(n)])
    for name, mask in roi_masks.items():
        obs[f"roi_{name}"] = mask
    # Two features: the analyte, and a reference/internal-standard channel
    # — addCalibrationPoint/addCalibrationUnknown compute their signal as
    # analyte/reference per pixel (roi_internal_standard_signal), not bare
    # analyte intensity, so a real second column is required for that
    # computation to mean anything (with only one column, the "reference"
    # lookup would silently nearest-match the analyte itself).
    var = pd.DataFrame(
        {"mz": [mz, reference_mz]}, index=[f"mz_{mz:.4f}", f"mz_{reference_mz:.4f}"]
    )
    reference_values = [_SEED_REFERENCE_VALUE] * n
    X = csr_matrix(np.column_stack([values, reference_values]).astype(np.float32))
    adata = ad.AnnData(X=X, obs=obs, var=var)
    adata.obsm["spatial"] = np.array([(float(i), 0.0) for i in range(n)], dtype=float)
    adata.layers["raw"] = X.copy()
    adata.write_h5ad(tmp_path / f"{sample_name}.h5ad")


def _seed_calibration_fixture(tmp_path: Path) -> Path:
    """Analysis DB + one sample "s1" whose .h5ad already carries an
    analyte column (mz=200.0), a reference/internal-standard column
    (mz=259.0, constant 5.0 at every pixel), and two ROI masks —
    "std_high" (analyte mean 15.0 -> signal 3.0, 2 pixels) and "std_low"
    (analyte mean 6.5 -> signal 1.3, 2 pixels). Also logs a `normalize_tic`
    command recording `reference_mz` — the provenance
    `_load_calibration_reference_mz` reads, same as a real targeted run
    would leave behind."""
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    register_sample(db_path, name="s1", raw_db_path=tmp_path / "s1.db")
    log_command(
        db_path,
        command_name="normalize_tic",
        arguments={
            "enabled": True, "mode": "tic",
            "reference_mz": _SEED_REFERENCE_MZ, "reference_ppm": 5.0,
            "analyte_mzs": [200.0],
        },
        run_id="test-run",
    )
    _seed_calibration_h5ad(
        tmp_path, "s1", mz=200.0,
        values=[10.0, 20.0, 5.0, 8.0],
        roi_masks={
            "std_high": [True, True, False, False],
            "std_low": [False, False, True, True],
        },
    )
    return db_path


def test_amount_units_and_pixel_size_units_properties():
    bridge = AnalysisBridge()
    assert bridge.amountUnits == ["pmol", "nmol", "umol", "mmol", "mol"]
    assert bridge.pixelSizeUnits == ["um", "mm"]


def test_get_calibration_points_empty_when_none_added(tmp_path):
    db_path = _seed_calibration_fixture(tmp_path)
    bridge = AnalysisBridge()
    assert bridge.getCalibrationPoints(str(db_path)) == []


def test_add_calibration_point_computes_mean_intensity_and_concentration(tmp_path):
    db_path = _seed_calibration_fixture(tmp_path)
    bridge = AnalysisBridge()

    result = bridge.addCalibrationPoint(
        str(db_path), "s1", "std_high", 200.0, 10.0, "pmol", 0.1, "mm",
    )

    assert result == {"ok": True}
    points = bridge.getCalibrationPoints(str(db_path))
    assert len(points) == 1
    row = points[0]
    assert row["sample_name"] == "s1"
    assert row["mean_intensity"] == pytest.approx(3.0)  # analyte/reference: mean(10/5, 20/5)
    assert row["n_pixels"] == 2
    assert row["area"] == pytest.approx(2 * 0.1**2)
    assert row["concentration"] == pytest.approx(10.0 / (2 * 0.1**2))


def test_add_calibration_point_missing_reference_mz_returns_error(tmp_path):
    # An analysis with an h5ad but no recorded `normalize_tic` command at
    # all (e.g. an untargeted analysis, or a targeted one from before this
    # internal-standard correction existed) has no reference channel to
    # divide by.
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    register_sample(db_path, name="s1", raw_db_path=tmp_path / "s1.db")
    _seed_calibration_h5ad(
        tmp_path, "s1", mz=200.0, values=[10.0, 20.0, 5.0, 8.0],
        roi_masks={"std_high": [True, True, False, False]},
    )
    bridge = AnalysisBridge()

    result = bridge.addCalibrationPoint(
        str(db_path), "s1", "std_high", 200.0, 10.0, "pmol", 0.1, "mm",
    )
    assert result["ok"] is False
    assert "reference" in result["error"].lower()


def test_add_calibration_point_reference_channel_all_zero_returns_error(tmp_path):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    register_sample(db_path, name="s1", raw_db_path=tmp_path / "s1.db")
    log_command(
        db_path, command_name="normalize_tic",
        arguments={"mode": "tic", "reference_mz": _SEED_REFERENCE_MZ},
        run_id="test-run",
    )
    n = 4
    obs = pd.DataFrame(index=[str(i) for i in range(n)])
    obs["roi_std_high"] = [True, True, False, False]
    var = pd.DataFrame(
        {"mz": [200.0, _SEED_REFERENCE_MZ]},
        index=["mz_200.0000", f"mz_{_SEED_REFERENCE_MZ:.4f}"],
    )
    X = csr_matrix(np.array(
        [[10.0, 0.0], [20.0, 0.0], [5.0, 0.0], [8.0, 0.0]], dtype=np.float32
    ))
    adata = ad.AnnData(X=X, obs=obs, var=var)
    adata.obsm["spatial"] = np.array([(float(i), 0.0) for i in range(n)], dtype=float)
    adata.layers["raw"] = X.copy()
    adata.write_h5ad(tmp_path / "s1.h5ad")
    bridge = AnalysisBridge()

    result = bridge.addCalibrationPoint(
        str(db_path), "s1", "std_high", 200.0, 10.0, "pmol", 0.1, "mm",
    )
    assert result["ok"] is False
    assert "reference channel" in result["error"].lower()


def test_add_calibration_point_upserts_on_same_roi_name(tmp_path):
    db_path = _seed_calibration_fixture(tmp_path)
    bridge = AnalysisBridge()

    bridge.addCalibrationPoint(str(db_path), "s1", "std_high", 200.0, 10.0, "pmol", 0.1, "mm")
    bridge.addCalibrationPoint(str(db_path), "s1", "std_high", 200.0, 20.0, "pmol", 0.1, "mm")

    points = bridge.getCalibrationPoints(str(db_path))
    assert len(points) == 1
    assert points[0]["amount"] == 20.0


def test_add_calibration_point_missing_analysis_db_returns_error(tmp_path):
    bridge = AnalysisBridge()
    result = bridge.addCalibrationPoint(
        str(tmp_path / "nope.db"), "s1", "std_high", 200.0, 10.0, "pmol", 0.1, "mm",
    )
    assert result["ok"] is False
    assert "No analysis database" in result["error"]


def test_add_calibration_point_missing_h5ad_returns_error(tmp_path):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    bridge = AnalysisBridge()

    result = bridge.addCalibrationPoint(
        str(db_path), "s1", "std_high", 200.0, 10.0, "pmol", 0.1, "mm",
    )
    assert result["ok"] is False
    assert "no .h5ad file" in result["error"]


def test_add_calibration_point_invalid_amount_unit_returns_error(tmp_path):
    db_path = _seed_calibration_fixture(tmp_path)
    bridge = AnalysisBridge()

    result = bridge.addCalibrationPoint(
        str(db_path), "s1", "std_high", 200.0, 10.0, "grams", 0.1, "mm",
    )
    assert result["ok"] is False
    assert "amount_unit" in result["error"]


def test_add_calibration_point_missing_roi_returns_error(tmp_path):
    db_path = _seed_calibration_fixture(tmp_path)
    bridge = AnalysisBridge()

    result = bridge.addCalibrationPoint(
        str(db_path), "s1", "does_not_exist", 200.0, 10.0, "pmol", 0.1, "mm",
    )
    assert result["ok"] is False
    assert "no pixels" in result["error"]


def test_add_calibration_point_invalid_pixel_size_unit_returns_error(tmp_path):
    db_path = _seed_calibration_fixture(tmp_path)
    bridge = AnalysisBridge()

    result = bridge.addCalibrationPoint(
        str(db_path), "s1", "std_high", 200.0, 10.0, "pmol", 0.1, "cm",
    )
    assert result["ok"] is False
    assert "pixel_size_unit" in result["error"]


def test_delete_calibration_point(tmp_path):
    db_path = _seed_calibration_fixture(tmp_path)
    bridge = AnalysisBridge()
    bridge.addCalibrationPoint(str(db_path), "s1", "std_high", 200.0, 10.0, "pmol", 0.1, "mm")

    removed = bridge.deleteCalibrationPoint(str(db_path), "std_high")

    assert removed is True
    assert bridge.getCalibrationPoints(str(db_path)) == []


def test_delete_calibration_point_missing_returns_false(tmp_path):
    db_path = _seed_calibration_fixture(tmp_path)
    bridge = AnalysisBridge()
    assert bridge.deleteCalibrationPoint(str(db_path), "nope") is False


def test_fit_calibration_curve_requires_at_least_two_standards(tmp_path):
    db_path = _seed_calibration_fixture(tmp_path)
    bridge = AnalysisBridge()
    bridge.addCalibrationPoint(str(db_path), "s1", "std_high", 200.0, 10.0, "pmol", 0.1, "mm")

    result = bridge.fitCalibrationCurve(str(db_path), 200.0, "linear", "")

    assert result["ok"] is False
    assert "At least 2" in result["error"]


def test_fit_calibration_curve_rejects_mismatched_units(tmp_path):
    db_path = _seed_calibration_fixture(tmp_path)
    bridge = AnalysisBridge()
    bridge.addCalibrationPoint(str(db_path), "s1", "std_high", 200.0, 10.0, "pmol", 0.1, "mm")
    bridge.addCalibrationPoint(str(db_path), "s1", "std_low", 200.0, 1.0, "nmol", 0.1, "mm")

    result = bridge.fitCalibrationCurve(str(db_path), 200.0, "linear", "")

    assert result["ok"] is False
    assert "same amount and pixel-size units" in result["error"]


def test_fit_calibration_curve_success_persists_and_returns_curve(tmp_path):
    db_path = _seed_calibration_fixture(tmp_path)
    bridge = AnalysisBridge()
    bridge.addCalibrationPoint(str(db_path), "s1", "std_high", 200.0, 10.0, "pmol", 0.1, "mm")
    bridge.addCalibrationPoint(str(db_path), "s1", "std_low", 200.0, 4.0, "pmol", 0.1, "mm")

    result = bridge.fitCalibrationCurve(str(db_path), 200.0, "linear", "")

    assert result["ok"] is True
    assert result["fit_type"] == "linear"
    assert result["concentration_unit"] == "pmol/mm2"
    assert result["n_points"] == 2

    curve = bridge.getCalibrationCurve(str(db_path))
    assert curve["id"] == result["id"]


def test_fit_calibration_curve_invalid_fit_type_returns_error(tmp_path):
    db_path = _seed_calibration_fixture(tmp_path)
    bridge = AnalysisBridge()
    bridge.addCalibrationPoint(str(db_path), "s1", "std_high", 200.0, 10.0, "pmol", 0.1, "mm")
    bridge.addCalibrationPoint(str(db_path), "s1", "std_low", 200.0, 4.0, "pmol", 0.1, "mm")

    result = bridge.fitCalibrationCurve(str(db_path), 200.0, "quadratic", "")

    assert result["ok"] is False


def test_fit_calibration_curve_reinterpolates_existing_unknowns(tmp_path):
    db_path = _seed_calibration_fixture(tmp_path)
    bridge = AnalysisBridge()
    # Two standards define a real (non-degenerate) curve — this test is
    # about reinterpolation happening once a curve exists, not the
    # resulting numbers.
    bridge.addCalibrationPoint(str(db_path), "s1", "std_high", 200.0, 10.0, "pmol", 0.1, "mm")
    bridge.addCalibrationPoint(str(db_path), "s1", "std_low", 200.0, 4.0, "pmol", 0.1, "mm")

    # unknown added before any curve exists
    unk_result = bridge.addCalibrationUnknown(str(db_path), "s1", "std_low", 200.0, 0.1, "mm")
    assert unk_result["ok"] is True
    unknowns = bridge.getCalibrationUnknowns(str(db_path))
    assert unknowns[0]["interpolated_conc"] is None

    bridge.fitCalibrationCurve(str(db_path), 200.0, "linear", "")

    unknowns = bridge.getCalibrationUnknowns(str(db_path))
    assert unknowns[0]["interpolated_conc"] is not None
    assert unknowns[0]["amount"] is not None


def test_add_calibration_unknown_missing_h5ad_returns_error(tmp_path):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    bridge = AnalysisBridge()

    result = bridge.addCalibrationUnknown(str(db_path), "s1", "spot", 200.0, 0.1, "mm")
    assert result["ok"] is False
    assert "no .h5ad file" in result["error"]


def test_add_calibration_unknown_missing_reference_mz_returns_error(tmp_path):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    register_sample(db_path, name="s1", raw_db_path=tmp_path / "s1.db")
    _seed_calibration_h5ad(
        tmp_path, "s1", mz=200.0, values=[10.0, 20.0, 5.0, 8.0],
        roi_masks={"spot": [True, True, False, False]},
    )
    bridge = AnalysisBridge()

    result = bridge.addCalibrationUnknown(str(db_path), "s1", "spot", 200.0, 0.1, "mm")
    assert result["ok"] is False
    assert "reference" in result["error"].lower()


def test_delete_calibration_unknown(tmp_path):
    db_path = _seed_calibration_fixture(tmp_path)
    bridge = AnalysisBridge()
    bridge.addCalibrationUnknown(str(db_path), "s1", "std_low", 200.0, 0.1, "mm")
    unknown_id = bridge.getCalibrationUnknowns(str(db_path))[0]["id"]

    removed = bridge.deleteCalibrationUnknown(str(db_path), unknown_id)

    assert removed is True
    assert bridge.getCalibrationUnknowns(str(db_path)) == []


def test_get_calibration_plot_data_empty_without_curve(tmp_path):
    db_path = _seed_calibration_fixture(tmp_path)
    bridge = AnalysisBridge()
    assert bridge.getCalibrationPlotData(str(db_path)) == {}


def _fit_two_standards(bridge, db_path):
    bridge.addCalibrationPoint(str(db_path), "s1", "std_high", 200.0, 10.0, "pmol", 0.1, "mm")
    bridge.addCalibrationPoint(str(db_path), "s1", "std_low", 200.0, 4.0, "pmol", 0.1, "mm")
    bridge.fitCalibrationCurve(str(db_path), 200.0, "linear", "")


def test_get_calibration_plot_data_after_fit(tmp_path):
    db_path = _seed_calibration_fixture(tmp_path)
    bridge = AnalysisBridge()
    _fit_two_standards(bridge, db_path)

    data = bridge.getCalibrationPlotData(str(db_path))

    assert len(data["points"]) == 2
    xs = [x for x, _ in data["points"]]
    # The fit line runs 5% past the standards on both ends.
    (x0, _), (x1, _) = data["fitLine"]
    assert x0 < min(xs) and x1 > max(xs)
    assert data["title"].startswith("y = ") and "R²" in data["title"]
    assert data["xTitle"] == "Concentration (pmol/mm2)"
    assert data["yTitle"] == "Signal (analyte / reference)"


def test_get_calibration_plot_data_converts_to_requested_display_area_unit(tmp_path):
    # Reported directly: toggling CalibrationSection.qml's "Report area/
    # concentration per" combo did nothing for an already-fitted curve —
    # the plot always rendered in whatever unit the curve was originally
    # fit in. `display_area_unit` makes this a live, non-destructive
    # redisplay: same underlying fit, converted axis/values.
    db_path = _seed_calibration_fixture(tmp_path)
    bridge = AnalysisBridge()
    _fit_two_standards(bridge, db_path)

    native = bridge.getCalibrationPlotData(str(db_path))
    converted = bridge.getCalibrationPlotData(str(db_path), "um")

    assert converted["xTitle"] == "Concentration (pmol/um2)"
    # 1 mm2 = 1e6 um2: the same amount per area is 1e6 times smaller per um2.
    for (x_mm, y_mm), (x_um, y_um) in zip(native["points"], converted["points"]):
        assert x_um == pytest.approx(x_mm * 1e-6)
        assert y_um == pytest.approx(y_mm)


def test_get_calibration_plot_data_display_area_unit_matching_native_is_a_noop(tmp_path):
    db_path = _seed_calibration_fixture(tmp_path)
    bridge = AnalysisBridge()
    _fit_two_standards(bridge, db_path)

    assert bridge.getCalibrationPlotData(str(db_path), "mm") == bridge.getCalibrationPlotData(
        str(db_path)
    )


def test_get_calibration_analyte_mz_none_recorded_yet(tmp_path):
    db_path = _seed_calibration_fixture(tmp_path)
    bridge = AnalysisBridge()
    assert bridge.getCalibrationAnalyteMz(str(db_path)) == -1.0


def test_get_calibration_analyte_mz_recorded_after_first_standard(tmp_path):
    db_path = _seed_calibration_fixture(tmp_path)
    bridge = AnalysisBridge()
    bridge.addCalibrationPoint(str(db_path), "s1", "std_high", 200.0, 10.0, "pmol", 0.1, "mm")

    assert bridge.getCalibrationAnalyteMz(str(db_path)) == pytest.approx(200.0)


def test_get_calibration_analyte_mz_missing_db_returns_sentinel(tmp_path):
    bridge = AnalysisBridge()
    assert bridge.getCalibrationAnalyteMz(str(tmp_path / "nope.db")) == -1.0


def test_get_sample_pixel_size_reads_from_raw_db_metadata(tmp_path):
    from msianalyzer.core.parser.mzml_parser import write_raw_metadata

    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    raw_db_path = tmp_path / "s1.db"
    with sqlite3.connect(raw_db_path) as conn:
        conn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
        conn.commit()
    register_sample(db_path, name="s1", raw_db_path=str(raw_db_path))
    write_raw_metadata(raw_db_path, {"pixel_size_x": 0.05, "pixel_size_unit": "mm"})

    bridge = AnalysisBridge()
    result = bridge.getSamplePixelSize(str(db_path), "s1")

    assert result == {"pixel_size": pytest.approx(0.05), "pixel_size_unit": "mm"}


def test_get_sample_pixel_size_empty_dict_when_raster_xml_had_no_raster_config(tmp_path):
    # The raw DB exists and is otherwise fully valid — it just never had
    # pixel_size_x/pixel_size_unit written (an XML with no <rasterConfig>
    # section) — exactly the "user must define it themselves" case this
    # already had, not an error.
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    raw_db_path = tmp_path / "s1.db"
    with sqlite3.connect(raw_db_path) as conn:
        conn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
        conn.commit()
    register_sample(db_path, name="s1", raw_db_path=str(raw_db_path))

    bridge = AnalysisBridge()
    assert bridge.getSamplePixelSize(str(db_path), "s1") == {}


def test_get_sample_pixel_size_empty_dict_for_unknown_sample(tmp_path):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()

    bridge = AnalysisBridge()
    assert bridge.getSamplePixelSize(str(db_path), "does_not_exist") == {}


def test_get_sample_pixel_size_empty_dict_missing_analysis_db(tmp_path):
    bridge = AnalysisBridge()
    assert bridge.getSamplePixelSize(str(tmp_path / "nope.db"), "s1") == {}


def test_request_summary_emits_counts_from_a_worker_and_caches(tmp_path, qtbot):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    register_sample(db_path, name="s1", raw_db_path=tmp_path / "s1.db")
    bridge = AnalysisBridge()

    with qtbot.waitSignal(bridge.summaryReady, timeout=3000) as spy:
        bridge.requestSummary(str(db_path))
    assert spy.args[0] == str(db_path)
    assert spy.args[1]["n_samples"] == 1

    with patch("msianalyzer.core.analysis_db.load_summary_counts") as never:
        with qtbot.waitSignal(bridge.summaryReady, timeout=1000) as spy:
            bridge.requestSummary(str(db_path))
        never.assert_not_called()
    assert spy.args[1]["n_samples"] == 1


def test_request_summary_missing_db_emits_zero_summary(tmp_path, qtbot):
    bridge = AnalysisBridge()
    with qtbot.waitSignal(bridge.summaryReady, timeout=1000) as spy:
        bridge.requestSummary(str(tmp_path / "nope.db"))
    assert spy.args[1] == _ZERO_SUMMARY


def _seed_unannotated_features(db_path, raw_db_path):
    """Feature 8: no library match, best MS2 scan 5 in sample s2's raw db
    (seeded too). Feature 9: no MS2 at all."""
    from msianalyzer.core.parser.mzml_parser import array_to_blob

    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO samples (sample_id, name, raw_db_path, polarity) "
            "VALUES (2, 's2', ?, 'positive')",
            (str(raw_db_path),),
        )
        con.executemany(
            "INSERT INTO features (feature_id, mz, members_json) VALUES (?, ?, '{}')",
            [(8, 200.0), (9, 250.0)],
        )
        con.execute(
            "INSERT INTO feature_ms2_consensus (feature_id, feature_mz, best_sample_id, "
            "best_scan_id, n_ms2, n_ms2_considered, n_ms2_scored, consensus_score, "
            "purity_score, n_peaks) VALUES (8, 200.0, 2, 5, 3, 3, 0, 0.5, 0.9, 2)"
        )
        con.commit()
    with sqlite3.connect(raw_db_path) as con:
        con.execute("CREATE TABLE ms2_scans (scan_id INTEGER, mz_array BLOB, intensity_array BLOB)")
        con.execute(
            "INSERT INTO ms2_scans VALUES (5, ?, ?)",
            (array_to_blob(np.array([80.0, 120.0])), array_to_blob(np.array([10.0, 40.0]))),
        )
        con.commit()


def test_annotation_table_with_unannotated_lists_every_feature_with_its_best_scan(
    tmp_path, qtbot
):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    _seed_annotated_feature(db_path)
    with sqlite3.connect(db_path) as con:
        con.execute("INSERT INTO features (feature_id, mz, members_json) VALUES (7, 300.5, '{}')")
        con.commit()
    _seed_unannotated_features(db_path, tmp_path / "raw_s2.db")

    bridge = AnalysisBridge()
    with qtbot.waitSignal(bridge.annotationTableReady, timeout=2000) as spy:
        bridge.requestAnnotationTable(str(db_path), True)
    rows = {r["feature_id"]: r for r in spy.args[0]}

    assert sorted(rows) == [7, 8, 9]
    assert rows[8]["source"] is None
    assert rows[8]["ms2_scan_id"] == 5
    assert rows[8]["ms2_sample_name"] == "s2"
    assert rows[8]["ms2_raw_db_path"] == str(tmp_path / "raw_s2.db")
    assert rows[9]["ms2_scan_id"] is None

    # Default stays annotated-only.
    with qtbot.waitSignal(bridge.annotationTableReady, timeout=2000) as spy:
        bridge.requestAnnotationTable(str(db_path))
    assert [r["feature_id"] for r in spy.args[0]] == [7]


def test_request_best_scan_plot_renders_the_raw_scan(tmp_path, qtbot):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    raw_db = tmp_path / "raw_s2.db"
    _seed_unannotated_features(db_path, raw_db)

    bridge = AnalysisBridge()
    with qtbot.waitSignal(bridge.bestScanPlotReady, timeout=5000) as spy:
        bridge.requestBestScanPlot(str(raw_db), 5)
    assert spy.args[0].startswith("data:image/png;base64,")

    with qtbot.waitSignal(bridge.bestScanPlotReady, timeout=5000) as spy:
        bridge.requestBestScanPlot(str(raw_db), 999)
    assert spy.args[0] == ""
