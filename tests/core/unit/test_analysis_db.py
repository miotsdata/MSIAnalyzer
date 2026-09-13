from __future__ import annotations

import sqlite3
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from msianalyzer.core.analysis_db import (
    analysis_db_path,
    attach_raw,
    find_nearest_feature,
    init_analysis_db,
    is_command_already_run,
    load_feature_categories,
    load_feature_compound_scores,
    load_feature_list,
    load_feature_ms2_count,
    load_feature_representative_annotations,
    load_features,
    load_ms2_annotations_for_feature,
    load_samples,
    load_summary_counts,
    log_command,
    register_sample,
    save_features,
    write_metadata,
)
from msianalyzer.core.spectra.average_spectra import save_aggregated_spectra
from msianalyzer.core.spectra.mz_tools import align_mz_across_samples


# ---------------------------------------------------------------------------
# path helper
# ---------------------------------------------------------------------------


def test_analysis_db_path_default_and_override(tmp_path: Path):
    assert analysis_db_path(tmp_path, "run123") == tmp_path / "analysis_run123.db"
    assert (
        analysis_db_path(tmp_path, "run123", override="custom.db")
        == tmp_path / "custom.db"
    )


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------


def test_init_analysis_db_creates_tables(tmp_path: Path):
    db = tmp_path / "analysis.db"
    conn = init_analysis_db(db)
    try:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert {
            "metadata",
            "commands",
            "samples",
            "aggregated_spectra",
            "features",
            "ms2_associations",
            "feature_ms2_summary",
            "precursor_purity",
            "feature_ms2_consensus",
            "annotation_libraries",
            "ms2_annotations",
        } <= tables
        # commands table has the analysis-only sample_id column
        cmd_cols = {
            row[1] for row in conn.execute("PRAGMA table_info(commands)").fetchall()
        }
        assert "sample_id" in cmd_cols
        # precursor_purity carries the precursor-purity headline columns
        purity_cols = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(precursor_purity)"
            ).fetchall()
        }
        assert {
            "ms2_scan_id",
            "parent_ms1_scan_id",
            "precursor_confirmed",
            "precursor_frac",
            "precursor_mz_snapped",
            "snap_shift_ppm",
        } <= purity_cols
        # ms2_annotations carries the purity carry-through columns
        ann_cols = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(ms2_annotations)"
            ).fetchall()
        }
        assert {"precursor_confirmed", "precursor_frac"} <= ann_cols
        assert {
            "rank_ms2",
            "rank_feature",
            "rank_feature_sample",
            "rank_scan_feature",
            "rank_scan_feature_sample",
        } <= ann_cols
        # the feature_compound_scores view exists
        views = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'view'"
            ).fetchall()
        }
        assert "feature_compound_scores" in views
        # feature_ms2_consensus shape
        cons_cols = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(feature_ms2_consensus)"
            ).fetchall()
        }
        assert {
            "feature_id",
            "best_scan_id",
            "consensus_score",
            "n_ms2",
            "n_ms2_considered",
            "best_compound_name",
        } <= cons_cols
    finally:
        conn.close()

    # idempotent
    init_analysis_db(db).close()


# ---------------------------------------------------------------------------
# samples
# ---------------------------------------------------------------------------


def test_register_sample_is_idempotent(tmp_path: Path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()

    sid1 = register_sample(db, name="s1", raw_db_path=tmp_path / "s1.db", polarity="POSITIVE")
    sid2 = register_sample(db, name="s1", raw_db_path=tmp_path / "s1.db")
    sid_other = register_sample(db, name="s2", raw_db_path=tmp_path / "s2.db")

    assert sid1 == sid2
    assert sid_other != sid1

    with sqlite3.connect(db) as con:
        rows = con.execute("SELECT name, polarity FROM samples ORDER BY sample_id").fetchall()
    assert rows == [("s1", "POSITIVE"), ("s2", None)]


# ---------------------------------------------------------------------------
# commands / provenance
# ---------------------------------------------------------------------------


def test_log_command_and_is_command_already_run(tmp_path: Path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()

    assert is_command_already_run("detect", "run1", db) is False

    cid = log_command(db, "detect", {"a": 1}, run_id="run1", sample_id=3)
    assert isinstance(cid, int)

    # run-scoped match
    assert is_command_already_run("detect", "run1", db) is True
    # sample-scoped match
    assert is_command_already_run("detect", "run1", db, sample_id=3) is True
    # wrong sample -> no match
    assert is_command_already_run("detect", "run1", db, sample_id=99) is False
    # wrong run -> no match
    assert is_command_already_run("detect", "other", db, sample_id=3) is False


def test_write_metadata_upserts(tmp_path: Path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()

    write_metadata(db, {"analysis_id": "abc", "n_samples": 2})
    write_metadata(db, {"n_samples": 3})

    with sqlite3.connect(db) as con:
        meta = dict(con.execute("SELECT key, value FROM metadata").fetchall())
    assert meta == {"analysis_id": "abc", "n_samples": "3"}


# ---------------------------------------------------------------------------
# features round-trip
# ---------------------------------------------------------------------------


def test_features_round_trip(tmp_path: Path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()

    aligned = align_mz_across_samples(
        mz_arrays=[np.array([100.0, 200.0, 500.0]), np.array([100.0005, 300.0])],
        sample_names=["A", "B"],
        align_ppm=10.0,
    )

    save_features(db, aligned, command_id=None)
    restored = load_features(db)

    pd.testing.assert_index_equal(restored.index, aligned.index)
    assert list(restored.columns) == ["A", "B"]
    assert restored["A"].dtype == "Int64"
    # membership preserved (NaN where a sample did not contribute)
    assert restored.loc[300.0, "B"] == 1
    assert pd.isna(restored.loc[300.0, "A"])


def test_save_features_replaces_previous(tmp_path: Path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()

    df1 = align_mz_across_samples([np.array([100.0])], sample_names=["A"])
    df2 = align_mz_across_samples(
        [np.array([200.0, 300.0])], sample_names=["A"]
    )
    save_features(db, df1)
    save_features(db, df2)

    with sqlite3.connect(db) as con:
        n = con.execute("SELECT COUNT(*) FROM features").fetchone()[0]
    assert n == 2


def test_load_features_empty(tmp_path: Path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    assert load_features(db).empty


# ---------------------------------------------------------------------------
# feature_compound_scores view / load_feature_compound_scores
# ---------------------------------------------------------------------------


_ANN_INSERT = (
    "INSERT INTO ms2_annotations (sample_id, scan_id, feature_id, library_id, "
    "library_spectrum_id, inchikey, compound_name, score, dot_product_score, "
    "lib_coverage, emp_coverage, coverage_score, n_matched_peaks, n_lib_peaks, "
    "n_emp_peaks_raw, n_emp_peaks_filtered, rank_ms2) "
    "VALUES (?,?,?,1,?,?,?,?,?,1,1,1,3,3,5,4,?)"
)


def _seed_two_feature_annotations(db: Path) -> None:
    with sqlite3.connect(db) as con:
        # FK enforcement is per-connection and off by default here, so we can
        # seed ms2_annotations without materialising `features` rows.
        con.execute(
            "INSERT INTO annotation_libraries (id, path, name) "
            "VALUES (1, 'lib.db', 'lib')"
        )
        con.executemany(
            _ANN_INSERT,
            [
                # feature 7, compound AAA: 3 candidate rows over 2 scans,
                # best 0.80 on s1/scan 11
                (1, 10, 7, 100, "AAA0000000000A", "Acid", 0.60, 0.6, 1),
                (1, 11, 7, 101, "AAA0000000000A", "Acid", 0.80, 0.8, 1),
                (1, 11, 7, 102, "AAA0000000000A", "Acid", 0.55, 0.5, 2),
                # feature 7, compound BBB: single row 0.70
                (1, 11, 7, 103, "BBB0000000000B", "Base", 0.70, 0.7, 3),
                # feature 9, compound CCC: 0.40
                (2, 30, 9, 104, "CCC0000000000C", "Ketone", 0.40, 0.4, 1),
                # a NULL-inchikey row is ignored by the view
                (1, 10, 7, 105, None, None, 0.99, 0.9, 4),
            ],
        )
        con.commit()


def test_feature_compound_scores_view_best_per_compound(tmp_path: Path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    _seed_two_feature_annotations(db)

    with sqlite3.connect(db) as con:
        rows = con.execute(
            "SELECT feature_id, inchikey, best_score, best_sample_id, "
            "best_scan_id, n_candidate_rows, n_scans "
            "FROM feature_compound_scores ORDER BY feature_id, best_score DESC"
        ).fetchall()

    assert rows == [
        (7, "AAA0000000000A", 0.80, 1, 11, 3, 2),  # 3 rows, 2 distinct scans
        (7, "BBB0000000000B", 0.70, 1, 11, 1, 1),
        (9, "CCC0000000000C", 0.40, 2, 30, 1, 1),
    ]


def test_load_feature_compound_scores_helper(tmp_path: Path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    _seed_two_feature_annotations(db)

    all_df = load_feature_compound_scores(db)
    assert list(all_df["best_score"]) == [0.80, 0.70, 0.40]  # feature, score desc

    one = load_feature_compound_scores(db, feature_id=7)
    assert set(one["inchikey"]) == {"AAA0000000000A", "BBB0000000000B"}
    assert one.iloc[0]["best_score"] == 0.80


def test_load_feature_compound_scores_includes_feature_mz(tmp_path: Path):
    # The view itself has no `mz` (pure aggregation over ms2_annotations) —
    # the GUI Annotations table sorts by it, so the helper joins it in from
    # `features`. NULL (not a missing column/dropped row) when a feature
    # has annotation rows but no `features` entry (FK enforcement is off
    # here, matching _seed_two_feature_annotations' own precedent).
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    _seed_two_feature_annotations(db)
    with sqlite3.connect(db) as con:
        con.execute(
            "INSERT INTO features (feature_id, mz, members_json) "
            "VALUES (7, 123.4567, '{}')"
        )
        con.commit()

    df = load_feature_compound_scores(db)
    assert "mz" in df.columns
    feature_7 = df[df["feature_id"] == 7]
    assert (feature_7["mz"] == 123.4567).all()
    feature_9 = df[df["feature_id"] == 9]
    assert feature_9["mz"].isna().all()


def test_load_feature_compound_scores_empty_without_annotations(tmp_path: Path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    assert load_feature_compound_scores(db).empty


# ---------------------------------------------------------------------------
# load_feature_representative_annotations
# ---------------------------------------------------------------------------


def _seed_representative_scenario(db: Path) -> None:
    """Two candidates on the same feature — a higher raw `score` (fewer
    matched peaks) that lost representative selection to a lower-scoring,
    richer match, exactly as `annotate.assign_feature_ranks` would leave
    them with `representative_score_tolerance` set: rank_feature=1 on the
    row that should win, regardless of which one has the higher `score`.
    """
    with sqlite3.connect(db) as con:
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
                # higher score, only 1 matched peak -> loses representative pick
                (1, 2, 31748, 9, "(S)-LACTATE", "C3H6O3",
                 "JVTAAEKCZFNVCJ-REOHCLBHSA-N", 0.9330, 1.0, 1, 1, 2),
                # lower score, 3 matched peaks -> wins (rank_feature=1)
                (2, 5, 71886, 10, "Lactic acid", "C3H6O3",
                 "JVTAAEKCZFNVCJ-UWTATZPHSA-N", 0.8572, 0.9999, 3, 3, 1),
            ],
        )
        con.commit()


def test_load_feature_representative_annotations_follows_rank_feature_not_score(
    tmp_path: Path,
):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    _seed_representative_scenario(db)

    df = load_feature_representative_annotations(db)

    assert len(df) == 1  # one row per feature
    row = df.iloc[0]
    assert row["feature_id"] == 76
    assert row["compound_name"] == "Lactic acid"
    assert row["n_matched_peaks"] == 3
    # the winning row's own score, not the higher-scoring loser's
    assert row["best_score"] == pytest.approx(0.8572)


def test_load_feature_representative_annotations_empty_without_annotations(
    tmp_path: Path,
):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    assert load_feature_representative_annotations(db).empty


# ---------------------------------------------------------------------------
# load_summary_counts
# ---------------------------------------------------------------------------


def _seed_samples_features_ms2_summary(db: Path) -> None:
    with sqlite3.connect(db) as con:
        con.execute(
            "INSERT INTO samples (sample_id, name, raw_db_path, polarity) "
            "VALUES (1, 's1', 'a.db', 'positive')"
        )
        con.execute(
            "INSERT INTO samples (sample_id, name, raw_db_path, polarity) "
            "VALUES (2, 's2', 'b.db', 'positive')"
        )
        con.executemany(
            "INSERT INTO features (feature_id, mz, members_json) VALUES (?, ?, '{}')",
            [(1, 100.0), (2, 200.0), (3, 300.0)],
        )
        con.execute(
            "INSERT INTO feature_ms2_summary (feature_id, feature_mz, n_ms2, "
            "n_samples, n_precursor_only, n_single_peak, "
            "n_flat_fragmentation, median_n_peaks) "
            "VALUES (1, 100.0, 5, 2, 0, 0, 0, 3.0)"
        )
        con.execute(
            "INSERT INTO feature_ms2_summary (feature_id, feature_mz, n_ms2, "
            "n_samples, n_precursor_only, n_single_peak, "
            "n_flat_fragmentation, median_n_peaks) "
            "VALUES (2, 200.0, 0, 0, 0, 0, 0, 0.0)"
        )
        con.commit()


def test_load_summary_counts_empty_db(tmp_path: Path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()

    assert load_summary_counts(db) == {
        "n_samples": 0,
        "n_features": 0,
        "n_ms2_associated_features": 0,
        "annotation_ran": False,
        "n_annotated_features": 0,
        "n_distinct_compounds": 0,
    }


def test_load_summary_counts_basic(tmp_path: Path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    _seed_samples_features_ms2_summary(db)

    counts = load_summary_counts(db)

    assert counts["n_samples"] == 2
    assert counts["n_features"] == 3
    # only feature 1 has n_ms2 > 0
    assert counts["n_ms2_associated_features"] == 1
    assert counts["annotation_ran"] is False
    assert counts["n_annotated_features"] == 0
    assert counts["n_distinct_compounds"] == 0


def test_load_summary_counts_with_annotations(tmp_path: Path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    _seed_two_feature_annotations(db)

    counts = load_summary_counts(db)

    assert counts["annotation_ran"] is True
    assert counts["n_annotated_features"] == 2  # features 7 and 9
    assert counts["n_distinct_compounds"] == 3  # AAA, BBB, CCC


# ---------------------------------------------------------------------------
# load_ms2_annotations_for_feature
# ---------------------------------------------------------------------------


def test_load_ms2_annotations_for_feature_returns_every_candidate(tmp_path: Path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    with sqlite3.connect(db) as con:
        con.execute(
            "INSERT INTO samples (sample_id, name, raw_db_path, polarity) "
            "VALUES (1, 's1', 'a.db', 'positive')"
        )
        con.execute(
            "INSERT INTO samples (sample_id, name, raw_db_path, polarity) "
            "VALUES (2, 's2', 'b.db', 'positive')"
        )
        con.commit()
    _seed_two_feature_annotations(db)

    df = load_ms2_annotations_for_feature(db, 7)

    # feature 7 has 5 rows: AAA x3, BBB x1, plus one NULL-inchikey row (that
    # feature_compound_scores excludes but this raw candidate list doesn't)
    assert len(df) == 5
    assert set(df["sample_name"]) == {"s1"}
    assert set(df["library_name"]) == {"lib"}
    assert (df["score"].iloc[0] >= df["score"].iloc[-1]) or df["rank_feature"].notna().any()


def test_load_ms2_annotations_for_feature_empty_for_unknown_feature(tmp_path: Path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    _seed_two_feature_annotations(db)

    assert load_ms2_annotations_for_feature(db, 999).empty


def test_load_ms2_annotations_for_feature_empty_db(tmp_path: Path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()

    assert load_ms2_annotations_for_feature(db, 1).empty


# ---------------------------------------------------------------------------
# load_samples
# ---------------------------------------------------------------------------


def test_load_samples_returns_every_sample(tmp_path: Path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    register_sample(db, name="s1", raw_db_path=tmp_path / "s1.db")
    register_sample(db, name="s2", raw_db_path=tmp_path / "s2.db")

    df = load_samples(db)

    assert list(df["name"]) == ["s1", "s2"]
    assert list(df["sample_id"]) == [1, 2]


def test_load_samples_empty_db(tmp_path: Path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()

    assert load_samples(db).empty


# ---------------------------------------------------------------------------
# find_nearest_feature
# ---------------------------------------------------------------------------


def test_find_nearest_feature_picks_closest_mz(tmp_path: Path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    with sqlite3.connect(db) as con:
        con.executemany(
            "INSERT INTO features (feature_id, mz, members_json) VALUES (?, ?, ?)",
            [
                (1, 100.0, '{"s1": 0, "s2": null}'),
                (2, 200.0, '{"s1": null, "s2": 3}'),
            ],
        )
        con.commit()

    found = find_nearest_feature(db, 101.0)

    assert found["feature_id"] == 1
    assert found["mz"] == 100.0
    assert found["members"] == {"s1": 0, "s2": None}


def test_find_nearest_feature_none_when_no_features(tmp_path: Path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()

    assert find_nearest_feature(db, 100.0) is None


# ---------------------------------------------------------------------------
# load_feature_ms2_count
# ---------------------------------------------------------------------------


def test_load_feature_ms2_count_returns_n_ms2(tmp_path: Path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    with sqlite3.connect(db) as con:
        con.execute(
            "INSERT INTO feature_ms2_summary (feature_id, feature_mz, n_ms2, "
            "n_samples, n_precursor_only, n_single_peak, "
            "n_flat_fragmentation, median_n_peaks) "
            "VALUES (1, 100.0, 7, 2, 0, 0, 0, 3.0)"
        )
        con.commit()

    assert load_feature_ms2_count(db, 1) == 7


def test_load_feature_ms2_count_zero_for_unknown_feature(tmp_path: Path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()

    assert load_feature_ms2_count(db, 999) == 0


# ---------------------------------------------------------------------------
# load_feature_list
# ---------------------------------------------------------------------------


def test_load_feature_list_labels_annotated_and_unannotated_features(tmp_path: Path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    with sqlite3.connect(db) as con:
        con.executemany(
            "INSERT INTO features (feature_id, mz, members_json) VALUES (?, ?, ?)",
            [
                (1, 100.0, '{"s1": 0}'),
                (2, 200.0, '{"s1": 0}'),
            ],
        )
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
            "VALUES (1, 42, 'k1', 100.1, 12.3, 5, 'positive')"
        )
        con.execute(
            "INSERT INTO ms2_annotations "
            "(id, feature_id, sample_id, scan_id, library_id, "
            "library_spectrum_id, compound_name, compound_formula, inchikey, "
            "score, dot_product_score, lib_coverage, emp_coverage, "
            "coverage_score, n_matched_peaks, n_lib_peaks, n_emp_peaks_raw, "
            "n_emp_peaks_filtered, rank_ms2, rank_feature) "
            "VALUES (1, 1, 1, 42, 1, 9, 'Caffeine', 'C8H10N4O2', "
            "'RYYVLZVUVIJVGH-UHFFFAOYSA-N', 0.87, 0.9, 0.8, 0.75, 0.77, "
            "2, 2, 10, 3, 1, 1)"
        )
        con.commit()

    df = load_feature_list(db)

    assert list(df["feature_id"]) == [1, 2]
    assert list(df["mz"]) == [100.0, 200.0]
    assert df.loc[df["feature_id"] == 1, "compound_name"].iloc[0] == "Caffeine"
    assert pd.isna(df.loc[df["feature_id"] == 2, "compound_name"].iloc[0])


def test_load_feature_list_labels_by_rank_feature_not_raw_score(tmp_path: Path):
    # A feature can have a higher-scoring candidate that lost
    # representative selection (assign_feature_ranks' tolerance/peak-count
    # rule) — the label must follow rank_feature, not just best_score.
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    _seed_representative_scenario(db)

    df = load_feature_list(db)

    assert df.loc[df["feature_id"] == 76, "compound_name"].iloc[0] == "Lactic acid"


def test_load_feature_list_empty_without_features(tmp_path: Path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()

    assert load_feature_list(db).empty


# ---------------------------------------------------------------------------
# load_feature_categories
# ---------------------------------------------------------------------------


def test_load_feature_categories_classifies_no_ms2_annotated_and_non_annotated(
    tmp_path: Path,
):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    with sqlite3.connect(db) as con:
        con.executemany(
            "INSERT INTO features (feature_id, mz, members_json) VALUES (?, ?, ?)",
            [
                (1, 100.0, '{"s1": 0}'),  # no MS2 at all
                (2, 200.0, '{"s1": 0}'),  # MS2 but no library match
                (3, 300.0, '{"s1": 0}'),  # MS2 and annotated
            ],
        )
        con.execute(
            "INSERT INTO feature_ms2_summary (feature_id, feature_mz, n_ms2, "
            "n_samples, n_precursor_only, n_single_peak, "
            "n_flat_fragmentation, median_n_peaks) "
            "VALUES (2, 200.0, 3, 1, 0, 0, 0, 3.0)"
        )
        con.execute(
            "INSERT INTO feature_ms2_summary (feature_id, feature_mz, n_ms2, "
            "n_samples, n_precursor_only, n_single_peak, "
            "n_flat_fragmentation, median_n_peaks) "
            "VALUES (3, 300.0, 2, 1, 0, 0, 0, 3.0)"
        )
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

    df = load_feature_categories(db)

    categories = dict(zip(df["feature_id"], df["category"]))
    assert categories == {1: "no_ms2", 2: "non_annotated", 3: "annotated"}
    # ordered by mz, ascending — nearest-mz matching against this relies on it
    assert list(df["mz"]) == [100.0, 200.0, 300.0]


def test_load_feature_categories_empty_without_features(tmp_path: Path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()

    assert load_feature_categories(db).empty


# ---------------------------------------------------------------------------
# attach_raw
# ---------------------------------------------------------------------------


def test_attach_raw_allows_cross_db_read(tmp_path: Path):
    raw = tmp_path / "raw.db"
    with sqlite3.connect(raw) as con:
        con.execute("CREATE TABLE ms1_scans (scan_id INTEGER)")
        con.execute("INSERT INTO ms1_scans VALUES (1), (2)")

    adb = tmp_path / "analysis.db"
    conn = init_analysis_db(adb)
    try:
        attach_raw(conn, raw, alias="raw")
        n = conn.execute("SELECT COUNT(*) FROM raw.ms1_scans").fetchone()[0]
        assert n == 2
    finally:
        conn.close()


def test_attach_raw_rejects_bad_alias(tmp_path: Path):
    adb = tmp_path / "analysis.db"
    conn = init_analysis_db(adb)
    try:
        with pytest.raises(ValueError):
            attach_raw(conn, tmp_path / "raw.db", alias="bad alias;")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# concurrent writers (the per-sample workers all append to one file)
# ---------------------------------------------------------------------------


def _hammer_analysis_db(args) -> int:
    """Worker: log a command + write an aggregated spectrum, a few times."""
    db_path, sample_id, n = args
    mz = np.linspace(100.0, 900.0, 20_000)
    inten = np.ones_like(mz)
    for i in range(n):
        cid = log_command(
            db_path, "detect_ms1_centroids", {"i": i}, run_id="r", sample_id=sample_id
        )
        save_aggregated_spectra(
            mz, inten,
            analysis_db_path=db_path, run_id="r", sample_id=sample_id, command_id=cid,
        )
    return sample_id


def test_concurrent_workers_do_not_corrupt_the_analysis_db(tmp_path: Path):
    """4 processes appending in parallel must not corrupt the WAL file.

    Regression for ``sqlite3.DatabaseError: database disk image is malformed``
    seen on a real 6-sample run — caused by concurrent ``CREATE TABLE`` DDL
    and a zero busy timeout.
    """
    adb = tmp_path / "analysis.db"
    init_analysis_db(adb).close()
    for k in range(4):
        register_sample(adb, name=f"s{k}", raw_db_path=tmp_path / f"s{k}.db")

    jobs = [(str(adb), k + 1, 8) for k in range(4)]
    with ProcessPoolExecutor(max_workers=4) as ex:
        done = sorted(ex.map(_hammer_analysis_db, jobs))
    assert done == [1, 2, 3, 4]

    with sqlite3.connect(adb) as con:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("SELECT COUNT(*) FROM aggregated_spectra").fetchone()[0] == 32
        assert con.execute(
            "SELECT COUNT(*) FROM commands WHERE command_name = 'detect_ms1_centroids'"
        ).fetchone()[0] == 32
