"""Tests for the Export menu's backing functions (core/export.py)."""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

import pytest

from msianalyzer.core.analysis_db import init_analysis_db
from msianalyzer.core.export import export_annotation_table


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
