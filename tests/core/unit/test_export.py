"""Tests for the Export menu's backing functions (core/export.py)."""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

import anndata as ad
import numpy as np
import pandas as pd
import pytest
from scipy.sparse import csr_matrix

from msianalyzer.core.analysis_db import init_analysis_db, register_sample
from msianalyzer.core.export import export_annotation_table, export_integration_tables


def _read_rows(path: Path, delimiter: str) -> list[list[str]]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.reader(f, delimiter=delimiter))


def _seed_two_features(db: Path) -> None:
    """Feature 7: ms2-annotated, with adduct/cas/hmdb and a library name.
    Feature 9: no annotation at all."""
    with sqlite3.connect(db) as con:
        con.execute(
            "INSERT INTO annotation_libraries (id, path, name) "
            "VALUES (1, 'lib.db', 'my_library')"
        )
        con.execute(
            "INSERT INTO features (feature_id, mz, members_json) VALUES (7, 123.4567, '{}')"
        )
        con.execute(
            "INSERT INTO features (feature_id, mz, members_json) VALUES (9, 50.0, '{}')"
        )
        con.execute(
            "INSERT INTO ms2_annotations "
            "(id, feature_id, sample_id, scan_id, library_id, library_spectrum_id, "
            "compound_name, compound_formula, inchikey, adduct, cas, hmdb, "
            "score, dot_product_score, lib_coverage, emp_coverage, coverage_score, "
            "n_matched_peaks, n_lib_peaks, n_emp_peaks_raw, n_emp_peaks_filtered, "
            "rank_ms2, rank_feature) "
            "VALUES (1, 7, 1, 42, 1, 9, 'Caffeine, decaf?', 'C8H10N4O2', "
            "'RYYVLZVUVIJVGH-UHFFFAOYSA-N', '[M+H]+', '58-08-2', 'HMDB0001847', "
            "0.87, 0.9, 0.8, 0.75, 0.77, 2, 2, 10, 3, 1, 1)"
        )
        con.commit()


def test_export_annotation_table_writes_header_and_rows(tmp_path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    _seed_two_features(db)
    dest = tmp_path / "out.csv"

    export_annotation_table(db, dest)

    rows = _read_rows(dest, ",")
    assert rows[0] == [
        "feature_id", "mz", "compound_name", "compound_formula", "adduct",
        "inchikey", "cas", "hmdb", "library_name", "source",
    ]
    assert len(rows) == 3  # header + 2 features


def test_export_annotation_table_populates_ms2_tier_fields(tmp_path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    _seed_two_features(db)
    dest = tmp_path / "out.csv"

    export_annotation_table(db, dest)

    rows = {r[0]: r for r in _read_rows(dest, ",")[1:]}
    row7 = rows["7"]
    assert row7[1] == "123.4567"
    assert row7[2] == "Caffeine, decaf?"  # the embedded comma survives quoting
    assert row7[3] == "C8H10N4O2"
    assert row7[4] == "[M+H]+"
    assert row7[5] == "RYYVLZVUVIJVGH-UHFFFAOYSA-N"
    assert row7[6] == "58-08-2"
    assert row7[7] == "HMDB0001847"
    assert row7[8] == "my_library"
    assert row7[9] == "ms2"


def test_export_annotation_table_includes_unidentified_feature_with_blank_fields(
    tmp_path,
):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    _seed_two_features(db)
    dest = tmp_path / "out.csv"

    export_annotation_table(db, dest)

    rows = {r[0]: r for r in _read_rows(dest, ",")[1:]}
    row9 = rows["9"]
    assert row9[1] == "50.0"
    assert row9[2:] == [""] * 8  # every identity field blank, not "None"/"nan"


def test_export_annotation_table_txt_extension_is_tab_delimited(tmp_path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    _seed_two_features(db)
    dest = tmp_path / "out.txt"

    export_annotation_table(db, dest)

    rows = _read_rows(dest, "\t")
    assert rows[0][0] == "feature_id"
    assert len(rows) == 3
    # Confirms it's genuinely tab-delimited, not comma content that
    # happens to also parse when split on tabs.
    raw = dest.read_text(encoding="utf-8")
    assert "\t" in raw.splitlines()[0]


def test_export_annotation_table_csv_extension_is_comma_delimited(tmp_path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    _seed_two_features(db)
    dest = tmp_path / "out.csv"

    export_annotation_table(db, dest)

    raw = dest.read_text(encoding="utf-8")
    assert "," in raw.splitlines()[0]
    assert "\t" not in raw


def test_export_annotation_table_quotes_every_string_field_even_without_a_comma(
    tmp_path,
):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    _seed_two_features(db)
    dest = tmp_path / "out.csv"

    export_annotation_table(db, dest)

    header_line = dest.read_text(encoding="utf-8").splitlines()[0]
    # QUOTE_NONNUMERIC quotes every non-numeric *field*, including the
    # header row itself (every column name is a plain str) — not just
    # values that happen to need it.
    assert '"feature_id"' in header_line
    assert '"compound_name"' in header_line

    lines = dest.read_text(encoding="utf-8").splitlines()
    row7_line = next(l for l in lines if l.startswith("7,"))
    assert '"Caffeine, decaf?"' in row7_line
    assert '"ms2"' in row7_line
    # feature_id/mz themselves stay unquoted numbers.
    assert row7_line.startswith("7,123.4567,")


def test_export_annotation_table_creates_destination_directory(tmp_path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    _seed_two_features(db)
    dest = tmp_path / "nested" / "dir" / "out.csv"

    export_annotation_table(db, dest)

    assert dest.exists()


def test_export_annotation_table_empty_analysis_writes_header_only(tmp_path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    dest = tmp_path / "out.csv"

    export_annotation_table(db, dest)

    rows = _read_rows(dest, ",")
    assert len(rows) == 1  # header only, no features at all


def _seed_sample_with_h5ad(
    db: Path, name: str, *, raw_values, tic_values, mzs=(100.0, 200.0)
) -> None:
    """Register a sample and write a matching 2-pixel `.h5ad` next to
    `db`, same layout `test_heatmap.py`'s own `_make_grid_adata` uses —
    one row per pixel, `X` = raw, `layers["TIC"]` = TIC-normalized."""
    register_sample(db, name=name, raw_db_path=db.parent / f"{name}_raw.db")
    obs = pd.DataFrame(index=["px0", "px1"])
    var = pd.DataFrame({"mz": list(mzs)}, index=[f"mz_{m:.4f}" for m in mzs])
    X = csr_matrix(np.array(raw_values, dtype=np.float32))
    adata = ad.AnnData(X=X, obs=obs, var=var)
    adata.obsm["spatial"] = np.array([(0.0, 0.0), (1.0, 0.0)], dtype=float)
    adata.layers["TIC"] = csr_matrix(np.array(tic_values, dtype=np.float32))
    adata.write_h5ad(db.parent / f"{name}.h5ad")


def _seed_two_feature_rows(db: Path, mzs=(100.0, 200.0)) -> None:
    with sqlite3.connect(db) as con:
        for i, mz in enumerate(mzs, start=1):
            con.execute(
                "INSERT INTO features (feature_id, mz, members_json) VALUES (?, ?, '{}')",
                (i, mz),
            )
        con.commit()


def test_export_integration_tables_writes_one_file_per_sample(tmp_path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    _seed_two_feature_rows(db)
    _seed_sample_with_h5ad(
        db, "sampleA", raw_values=[[1.0, 2.0], [3.0, 4.0]], tic_values=[[10.0, 20.0], [30.0, 40.0]]
    )
    _seed_sample_with_h5ad(
        db, "sampleB", raw_values=[[5.0, 6.0], [7.0, 8.0]], tic_values=[[50.0, 60.0], [70.0, 80.0]]
    )

    written = export_integration_tables(db, tmp_path / "out", "TIC")

    assert written == 2
    assert (tmp_path / "out" / "sampleA_integration.csv").exists()
    assert (tmp_path / "out" / "sampleB_integration.csv").exists()


def test_export_integration_tables_header_is_x_y_and_mz_to_4_decimals(tmp_path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    _seed_two_feature_rows(db, mzs=(100.5, 200.25))
    _seed_sample_with_h5ad(
        db, "sampleA", raw_values=[[1.0, 2.0], [3.0, 4.0]], tic_values=[[10.0, 20.0], [30.0, 40.0]],
        mzs=(100.5, 200.25),
    )

    export_integration_tables(db, tmp_path / "out", "TIC")

    rows = _read_rows(tmp_path / "out" / "sampleA_integration.csv", ",")
    assert rows[0] == ["x", "y", "100.5000", "200.2500"]


def test_export_integration_tables_raw_vs_tic_selects_the_right_layer(tmp_path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    _seed_two_feature_rows(db)
    _seed_sample_with_h5ad(
        db, "sampleA", raw_values=[[1.0, 2.0], [3.0, 4.0]], tic_values=[[10.0, 20.0], [30.0, 40.0]]
    )

    export_integration_tables(db, tmp_path / "raw_out", "raw")
    export_integration_tables(db, tmp_path / "tic_out", "TIC")

    raw_rows = _read_rows(tmp_path / "raw_out" / "sampleA_integration.csv", ",")
    tic_rows = _read_rows(tmp_path / "tic_out" / "sampleA_integration.csv", ",")
    assert raw_rows[1] == ["0.0", "0.0", "1.0", "2.0"]
    assert tic_rows[1] == ["0.0", "0.0", "10.0", "20.0"]


def test_export_integration_tables_txt_extension_is_tab_delimited(tmp_path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    _seed_two_feature_rows(db)
    _seed_sample_with_h5ad(
        db, "sampleA", raw_values=[[1.0, 2.0], [3.0, 4.0]], tic_values=[[10.0, 20.0], [30.0, 40.0]]
    )

    export_integration_tables(db, tmp_path / "out", "TIC", file_format="txt")

    dest = tmp_path / "out" / "sampleA_integration.txt"
    assert dest.exists()
    raw = dest.read_text(encoding="utf-8")
    assert "\t" in raw.splitlines()[0]
    assert "," not in raw.splitlines()[0].replace('"', "")


def test_export_integration_tables_skips_sample_with_missing_h5ad(tmp_path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    _seed_two_feature_rows(db)
    register_sample(db, name="ghost", raw_db_path=tmp_path / "ghost_raw.db")

    written = export_integration_tables(db, tmp_path / "out", "TIC")

    assert written == 0
    assert not (tmp_path / "out" / "ghost_integration.csv").exists()


def test_export_integration_tables_creates_destination_directory(tmp_path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    _seed_two_feature_rows(db)
    _seed_sample_with_h5ad(
        db, "sampleA", raw_values=[[1.0, 2.0], [3.0, 4.0]], tic_values=[[10.0, 20.0], [30.0, 40.0]]
    )

    export_integration_tables(db, tmp_path / "nested" / "dir", "TIC")

    assert (tmp_path / "nested" / "dir" / "sampleA_integration.csv").exists()
