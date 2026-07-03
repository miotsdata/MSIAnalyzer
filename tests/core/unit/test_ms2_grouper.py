"""test_ms2_grouper.py"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from msianalyzer.core.ms2_grouper import ppm_diff, _cluster_rows, group_ms2_by_precursor_ppm


class TestPpmDiff:
    def test_identical_values(self):
        assert ppm_diff(100.0, 100.0) == 0.0

    def test_known_ppm(self):
        # 100.001 vs 100.0 -> 10 ppm
        assert ppm_diff(100.001, 100.0) == pytest.approx(10.0, rel=1e-3)

    def test_zero_reference_returns_inf(self):
        assert ppm_diff(5.0, 0.0) == float("inf")

    def test_symmetric_absolute(self):
        assert ppm_diff(99.999, 100.0) == pytest.approx(ppm_diff(100.001, 100.0), rel=1e-6)


class TestClusterRows:
    def test_empty_input(self):
        assert _cluster_rows([], ppm_tolerance=10.0) == []

    def test_single_row(self):
        groups = _cluster_rows([(1, 100.0)], ppm_tolerance=10.0)
        assert len(groups) == 1
        assert groups[0].scan_ids == [1]
        assert groups[0].mz_center == pytest.approx(100.0)

    def test_two_close_rows_same_group(self):
        # 100.0 and 100.0005 -> 5 ppm apart, within 10 ppm tolerance
        rows = [(1, 100.0), (2, 100.0005)]
        groups = _cluster_rows(rows, ppm_tolerance=10.0)
        assert len(groups) == 1
        assert groups[0].scan_ids == [1, 2]

    def test_two_far_rows_different_groups(self):
        rows = [(1, 100.0), (2, 200.0)]
        groups = _cluster_rows(rows, ppm_tolerance=10.0)
        assert len(groups) == 2
        assert groups[0].scan_ids == [1]
        assert groups[1].scan_ids == [2]

    def test_chain_grouping(self):
        """Sequential clustering: each step within tolerance of its
        immediate predecessor, even if first/last are far apart."""
        rows = [(1, 100.0), (2, 100.0009), (3, 100.0018)]
        # each step ~9 ppm, tolerance 10 ppm -> all one group
        groups = _cluster_rows(rows, ppm_tolerance=10.0)
        assert len(groups) == 1
        assert groups[0].scan_ids == [1, 2, 3]

    def test_group_breaks_mid_chain(self):
        rows = [(1, 100.0), (2, 100.0005), (3, 150.0)]
        groups = _cluster_rows(rows, ppm_tolerance=10.0)
        assert len(groups) == 2
        assert groups[0].scan_ids == [1, 2]
        assert groups[1].scan_ids == [3]

    def test_group_id_sequential(self):
        rows = [(1, 100.0), (2, 200.0), (3, 300.0)]
        groups = _cluster_rows(rows, ppm_tolerance=1.0)
        assert [g.group_id for g in groups] == [0, 1, 2]

    def test_mz_min_max_center(self):
        rows = [(1, 100.0), (2, 100.0005)]
        groups = _cluster_rows(rows, ppm_tolerance=10.0)
        g = groups[0]
        assert g.mz_min == pytest.approx(100.0)
        assert g.mz_max == pytest.approx(100.0005)
        assert g.mz_center == pytest.approx((100.0 + 100.0005) / 2)

    def test_size_property(self):
        rows = [(1, 100.0), (2, 100.0001), (3, 100.0002)]
        groups = _cluster_rows(rows, ppm_tolerance=10.0)
        assert groups[0].size == 3

    def test_zero_tolerance_isolates_every_row(self):
        rows = [(1, 100.0), (2, 100.0001), (3, 100.0002)]
        groups = _cluster_rows(rows, ppm_tolerance=0.0)
        assert len(groups) == 3

    def test_all_rows_accounted_for(self):
        rows = [(i, 100.0 + i * 50.0) for i in range(10)]
        groups = _cluster_rows(rows, ppm_tolerance=10.0)
        all_scan_ids = sorted(sid for g in groups for sid in g.scan_ids)
        assert all_scan_ids == list(range(10))


class TestGroupMs2ByPrecursorPpm:
    @pytest.fixture()
    def ms2_db(self, tmp_path: Path) -> Path:
        db = tmp_path / "ms2.db"
        con = sqlite3.connect(db)
        con.execute("""
            CREATE TABLE ms2_scans (
                scan_id INTEGER PRIMARY KEY,
                rt REAL,
                precursor_mz REAL,
                precursor_charge INTEGER,
                n_peaks INTEGER,
                mz_array BLOB,
                intensity_array BLOB
            )
        """)
        rows = [
            (1, 1.0, 100.0,    1, 0, b"", b""),
            (2, 1.1, 100.0005, 1, 0, b"", b""),
            (3, 1.2, 250.0,    1, 0, b"", b""),
            (4, 1.3, None,     1, 0, b"", b""),  # should be skipped
        ]
        con.executemany(
            "INSERT INTO ms2_scans VALUES (?,?,?,?,?,?,?)", rows
        )
        con.commit()
        con.close()
        return db

    def test_groups_from_db(self, ms2_db):
        groups = group_ms2_by_precursor_ppm(ms2_db, ppm_tolerance=10.0)
        assert len(groups) == 2
        assert groups[0].scan_ids == [1, 2]
        assert groups[1].scan_ids == [3]

    def test_null_precursor_mz_excluded(self, ms2_db):
        groups = group_ms2_by_precursor_ppm(ms2_db, ppm_tolerance=10.0)
        all_scan_ids = [sid for g in groups for sid in g.scan_ids]
        assert 4 not in all_scan_ids

    def test_empty_db_returns_empty_list(self, tmp_path):
        db = tmp_path / "empty.db"
        con = sqlite3.connect(db)
        con.execute("""
            CREATE TABLE ms2_scans (
                scan_id INTEGER PRIMARY KEY, rt REAL, precursor_mz REAL,
                precursor_charge INTEGER, n_peaks INTEGER,
                mz_array BLOB, intensity_array BLOB
            )
        """)
        con.commit()
        con.close()
        assert group_ms2_by_precursor_ppm(db, ppm_tolerance=10.0) == []
