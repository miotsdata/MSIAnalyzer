import sqlite3
from pathlib import Path

import anndata as ad
import pandas as pd
import pytest
from PySide6.QtCore import QUrl
from scipy.sparse import csr_matrix

from msianalyzer.core.analysis_db import init_analysis_db, log_command, register_sample
from msianalyzer.core.parser.mzml_parser import array_to_blob
from msianalyzer.core.spectra.average_spectra import save_aggregated_spectra
from msianalyzer.gui.utils.analysis_bridge import AnalysisBridge, _categorize_peaks
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


def test_ensure_schema_current_adds_a_missing_index_to_an_old_db(tmp_path):
    # Simulates an analysis run before ADR 33's index existed: create a
    # normal (already-current) db, then drop the index by hand to stand
    # in for that older schema.
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    with sqlite3.connect(db_path) as con:
        con.execute("DROP INDEX idx_ann_rank_feature_1")
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


def test_export_visual_inspection_images_writes_files_and_emits_finished(tmp_path, qtbot):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    _seed_sample_with_h5ad(db_path, "sampleA")
    dest_folder = tmp_path / "out"

    bridge = AnalysisBridge()
    with qtbot.waitSignal(bridge.exportFinished, timeout=2000) as spy:
        bridge.exportVisualInspectionImages(
            str(db_path), str(dest_folder), "png", "feature", "100.0", "",
            "TIC", "viridis", "auto", "auto", False,
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
            "100.0", "", "TIC", "viridis", "auto", "auto", False,
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


def _request_mirror_plot_sync(bridge, qtbot, *args, timeout=5000) -> str:
    """`requestMirrorPlot` is fire-and-forget (see the class docstring —
    it runs on a background `MirrorPlotWorker` so slow/remote raw-source
    I/O never blocks the GUI thread); tests wait for `mirrorPlotReady`
    and return the file:// URL it carried."""
    with qtbot.waitSignal(bridge.mirrorPlotReady, timeout=timeout) as blocker:
        bridge.requestMirrorPlot(*args)
    return blocker.args[0]


def test_request_mirror_plot_emits_ready_with_html_for_valid_annotation(tmp_path, qtbot):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    _seed_annotated_feature(db_path)

    bridge = AnalysisBridge()
    url = _request_mirror_plot_sync(bridge, qtbot, str(db_path), 1, "filtered", "filtered")
    html = _read_url(url)

    assert url.startswith("file://")
    assert "<div" in html
    assert "plotly" in html.lower()


def test_request_mirror_plot_fills_container_instead_of_fixed_height(tmp_path, qtbot):
    # "mirror plot part should take 60% [of the panel] and cannot scroll,
    # so plot adapts to it" — same fix as MS1's getSpectrumUrl: autosize,
    # no fixed pixel height baked into the figure, and CSS clearing the
    # default body margin/overflow so nothing forces a scrollbar.
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    _seed_annotated_feature(db_path)

    bridge = AnalysisBridge()
    html = _read_url(
        _request_mirror_plot_sync(bridge, qtbot, str(db_path), 1, "filtered", "filtered")
    )

    assert '"autosize": true' in html or '"autosize":true' in html
    assert "overflow: hidden" in html
    # The figure's own fixed height (default 500) must not survive into
    # the emitted layout JSON.
    assert '"height": 500' not in html and '"height":500' not in html


def test_request_mirror_plot_emits_error_message_for_unknown_id(tmp_path, qtbot):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()

    bridge = AnalysisBridge()
    html = _read_url(
        _request_mirror_plot_sync(bridge, qtbot, str(db_path), 999, "filtered", "filtered")
    )

    assert "<p" in html
    assert "No annotation found" in html


def test_request_mirror_plot_emits_error_message_without_raw_spectra(tmp_path, qtbot):
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    _seed_annotated_feature(db_path, with_raw_spectra=False)

    bridge = AnalysisBridge()
    html = _read_url(
        _request_mirror_plot_sync(bridge, qtbot, str(db_path), 1, "filtered", "filtered")
    )

    assert "<p" in html
    assert "raw empirical spectrum not found" in html


def test_request_mirror_plot_emits_error_message_for_unavailable_raw_source(tmp_path, qtbot):
    # No stored emp_raw_*, and 'a.db' (the seeded sample's raw_db_path)
    # doesn't exist on disk either -> both resolution paths fail.
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    _seed_annotated_feature(db_path, with_raw_spectra=False)

    bridge = AnalysisBridge()
    html = _read_url(
        _request_mirror_plot_sync(bridge, qtbot, str(db_path), 1, "raw", "filtered")
    )

    assert "<p" in html
    assert "raw empirical spectrum not found" in html


def test_request_mirror_plot_url_is_unique_per_call(tmp_path, qtbot):
    # Each render needs a distinct URL — an unchanged QML `url` binding
    # doesn't reload, even when the underlying file's content changed.
    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()
    _seed_annotated_feature(db_path)

    bridge = AnalysisBridge()
    url1 = _request_mirror_plot_sync(bridge, qtbot, str(db_path), 1, "filtered", "filtered")
    url2 = _request_mirror_plot_sync(bridge, qtbot, str(db_path), 1, "filtered", "filtered")

    assert url1 != url2


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


def test_get_feature_value_range_delegates_to_heatmap_provider(tmp_path):
    import anndata as ad
    import pandas as pd
    from scipy.sparse import csr_matrix

    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()

    obs = pd.DataFrame(index=["a", "b", "c", "d"])
    var = pd.DataFrame({"mz": [100.0]}, index=["mz_100.0000"])
    X = csr_matrix(np.array([5.0, 10.0, 15.0, 20.0], dtype=np.float32).reshape(-1, 1))
    adata = ad.AnnData(X=X, obs=obs, var=var)
    adata.obsm["spatial"] = np.array(
        [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0)], dtype=float
    )
    adata.write_h5ad(tmp_path / "s1.h5ad")

    bridge = AnalysisBridge()
    bridge.setHeatmapAnalysis(str(db_path))

    result = bridge.getFeatureValueRange(["s1"], 100.0, "raw")

    assert result == {"vmin": 5.0, "vmax": 20.0}


def test_get_obs_columns_delegates_to_heatmap_provider(tmp_path):
    import anndata as ad
    import pandas as pd
    from scipy.sparse import csr_matrix

    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()

    obs = pd.DataFrame(
        {"tic": [1.0, 2.0, 3.0, 4.0], "region": ["a", "a", "b", "b"]},
        index=["a", "b", "c", "d"],
    )
    var = pd.DataFrame({"mz": [100.0]}, index=["mz_100.0000"])
    X = csr_matrix(np.array([5.0, 10.0, 15.0, 20.0], dtype=np.float32).reshape(-1, 1))
    adata = ad.AnnData(X=X, obs=obs, var=var)
    adata.obsm["spatial"] = np.array(
        [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0)], dtype=float
    )
    adata.write_h5ad(tmp_path / "s1.h5ad")

    bridge = AnalysisBridge()
    bridge.setHeatmapAnalysis(str(db_path))

    columns = {c["name"]: c["numeric"] for c in bridge.getObsColumns(["s1"])}

    assert columns["tic"] is True
    assert columns["region"] is False


def test_get_obs_value_range_delegates_to_heatmap_provider(tmp_path):
    import anndata as ad
    import pandas as pd
    from scipy.sparse import csr_matrix

    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()

    obs = pd.DataFrame({"tic": [1.0, 2.0, 3.0, 40.0]}, index=["a", "b", "c", "d"])
    var = pd.DataFrame({"mz": [100.0]}, index=["mz_100.0000"])
    X = csr_matrix(np.array([5.0, 10.0, 15.0, 20.0], dtype=np.float32).reshape(-1, 1))
    adata = ad.AnnData(X=X, obs=obs, var=var)
    adata.obsm["spatial"] = np.array(
        [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0)], dtype=float
    )
    adata.write_h5ad(tmp_path / "s1.h5ad")

    bridge = AnalysisBridge()
    bridge.setHeatmapAnalysis(str(db_path))

    result = bridge.getObsValueRange(["s1"], "tic")

    assert result == {"vmin": 1.0, "vmax": 40.0}


def test_get_obs_categories_delegates_to_heatmap_provider(tmp_path):
    import anndata as ad
    import pandas as pd
    from scipy.sparse import csr_matrix

    db_path = tmp_path / "analysis.db"
    init_analysis_db(db_path).close()

    obs = pd.DataFrame(
        {"polarity": ["positive", "positive", "negative", "negative"]},
        index=["a", "b", "c", "d"],
    )
    var = pd.DataFrame({"mz": [100.0]}, index=["mz_100.0000"])
    X = csr_matrix(np.array([5.0, 10.0, 15.0, 20.0], dtype=np.float32).reshape(-1, 1))
    adata = ad.AnnData(X=X, obs=obs, var=var)
    adata.obsm["spatial"] = np.array(
        [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0)], dtype=float
    )
    adata.write_h5ad(tmp_path / "s1.h5ad")

    bridge = AnalysisBridge()
    bridge.setHeatmapAnalysis(str(db_path))

    result = bridge.getObsCategories(["s1"], "polarity")

    assert [c["category"] for c in result] == ["negative", "positive"]


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


def test_get_spectrum_url_adapts_to_container_instead_of_scrolling(tmp_path):
    # "The top row, the one with the plot, shouldn't be scrollable. Adapt
    # the content to the row" — the plot used to render at a fixed pixel
    # height regardless of the WebEngineView's actual size, showing a
    # scrollbar whenever the container was shorter.
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
    html = _read_url(bridge.getSpectrumUrl(str(db_path), "run-1", sample_id))

    assert "overflow: hidden" in html
    assert '"responsive": true' in html
    assert '"autosize":true' in html


def test_get_spectrum_url_colors_peaks_by_feature_category(tmp_path):
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
    html = _read_url(bridge.getSpectrumUrl(str(db_path), "run-1", sample_id))

    assert "No MS2" in html
    assert "Annotated" in html
    assert "Non-annotated" in html
    assert "#999999" in html  # no_ms2 gray
    assert "#1f77b4" in html  # annotated blue
    assert "#000000" in html  # non_annotated black


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
    bridge.getFeatureValueRange(["s1"], 100.0, "raw")  # populates the cache
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
