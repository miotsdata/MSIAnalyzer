"""
test_mzml_parser.py
Pytest test suite for mzml_parser.py.

Fixtures build minimal but structurally valid mzML snippets that match
the real file format (64-bit zlib arrays, Thermo-style scan IDs).
"""

from __future__ import annotations

import sqlite3
import zlib
from base64 import b64encode
from pathlib import Path

import numpy as np
import pytest

from msianalyzer.core.mzml_parser import (
    MzmlFile,
    MzmlParser,
    array_to_blob,
    blob_to_array,
    _parse_rt,
    _parse_ms_level,
    _parse_scan_id,
    _parse_precursor_mz,
    _parse_precursor_charge,
    _parse_binary_arrays,
)


# ---------------------------------------------------------------------------
# Helpers — build realistic mzML binary blocks
# ---------------------------------------------------------------------------

def _encode_array(arr: np.ndarray, dtype=np.float64) -> str:
    """Encode a numpy array as zlib-compressed base64, as mzML does."""
    raw = arr.astype(dtype).tobytes()
    return b64encode(zlib.compress(raw)).decode()


def _mz_block(arr: np.ndarray) -> str:
    enc = _encode_array(arr, np.float64)
    return f"""
        <binaryDataArray encodedLength="{len(enc)}">
          <cvParam accession="MS:1000514" name="m/z array"/>
          <cvParam accession="MS:1000523" name="64-bit float"/>
          <cvParam accession="MS:1000574" name="zlib compression"/>
          <binary>{enc}</binary>
        </binaryDataArray>"""


def _int_block(arr: np.ndarray) -> str:
    enc = _encode_array(arr, np.float64)
    return f"""
        <binaryDataArray encodedLength="{len(enc)}">
          <cvParam accession="MS:1000515" name="intensity array"/>
          <cvParam accession="MS:1000523" name="64-bit float"/>
          <cvParam accession="MS:1000574" name="zlib compression"/>
          <binary>{enc}</binary>
        </binaryDataArray>"""


def _ms1_spectrum(scan: int, rt_min: float, mz: np.ndarray, intensity: np.ndarray) -> str:
    return f"""
    <spectrum id="controllerType=0 controllerNumber=1 scan={scan}" index="{scan-1}" defaultArrayLength="{len(mz)}">
      <cvParam accession="MS:1000511" value="1" name="ms level"/>
      <scanList count="1">
        <scan>
          <cvParam accession="MS:1000016" value="{rt_min}" name="scan start time"
                   unitAccession="UO:0000031" unitName="minute" unitCvRef="UO"/>
        </scan>
      </scanList>
      <binaryDataArrayList count="2">
        {_mz_block(mz)}
        {_int_block(intensity)}
      </binaryDataArrayList>
    </spectrum>"""


def _ms2_spectrum(
    scan: int, rt_min: float,
    precursor_mz: float, precursor_charge: int,
    mz: np.ndarray, intensity: np.ndarray,
) -> str:
    return f"""
    <spectrum id="controllerType=0 controllerNumber=1 scan={scan}" index="{scan-1}" defaultArrayLength="{len(mz)}">
      <cvParam accession="MS:1000511" value="2" name="ms level"/>
      <scanList count="1">
        <scan>
          <cvParam accession="MS:1000016" value="{rt_min}" name="scan start time"
                   unitAccession="UO:0000031" unitName="minute" unitCvRef="UO"/>
        </scan>
      </scanList>
      <precursorList count="1">
        <precursor>
          <selectedIon>
            <cvParam accession="MS:1000744" value="{precursor_mz}" name="selected ion m/z"/>
            <cvParam accession="MS:1000041" value="{precursor_charge}" name="charge state"/>
          </selectedIon>
        </precursor>
      </precursorList>
      <binaryDataArrayList count="2">
        {_mz_block(mz)}
        {_int_block(intensity)}
      </binaryDataArrayList>
    </spectrum>"""


def _wrap_mzml(*spectra: str) -> str:
    body = "\n".join(spectra)
    return f"""<?xml version="1.0" encoding="utf-8"?>
<indexedmzML xmlns="http://psi.hupo.org/ms/mzml">
  <mzML>
    <run>
      <spectrumList count="{len(spectra)}">
        {body}
      </spectrumList>
    </run>
  </mzML>
</indexedmzML>"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MZ_1   = np.array([100.0, 150.0, 200.0, 250.0])
INT_1  = np.array([1000.0, 2000.0, 3000.0, 4000.0])
MZ_2   = np.array([50.0, 60.0, 70.0])
INT_2  = np.array([500.0, 600.0, 700.0])
MZ_3   = np.array([80.0, 90.0])
INT_3  = np.array([800.0, 900.0])


@pytest.fixture()
def simple_mzml(tmp_path: Path) -> Path:
    """mzML with 2 MS1 and 1 MS2 spectrum."""
    content = _wrap_mzml(
        _ms1_spectrum(1, 0.10, MZ_1, INT_1),
        _ms1_spectrum(2, 0.20, MZ_2, INT_2),
        _ms2_spectrum(3, 0.21, precursor_mz=150.0, precursor_charge=1,
                      mz=MZ_3, intensity=INT_3),
    )
    p = tmp_path / "test.mzML"
    p.write_text(content, encoding="utf-8")
    return p


@pytest.fixture()
def ms1_only_mzml(tmp_path: Path) -> Path:
    """mzML with only MS1 spectra."""
    content = _wrap_mzml(
        _ms1_spectrum(1, 0.10, MZ_1, INT_1),
        _ms1_spectrum(2, 0.20, MZ_2, INT_2),
    )
    p = tmp_path / "ms1_only.mzML"
    p.write_text(content, encoding="utf-8")
    return p


@pytest.fixture()
def parser(tmp_path: Path) -> MzmlParser:
    return MzmlParser(
        ms1_db_path=tmp_path / "ms1.db",
        ms2_db_path=tmp_path / "ms2.db",
    )


# ---------------------------------------------------------------------------
# Unit tests — pure parsing helpers
# ---------------------------------------------------------------------------

class TestParseRt:
    def test_minutes_converted_to_seconds(self):
        text = '<cvParam accession="MS:1000016" value="1.0" name="scan start time" unitAccession="UO:0000031" unitName="minute" unitCvRef="UO"/>'
        assert _parse_rt(text) == pytest.approx(60.0)

    def test_minutes_fractional(self):
        text = '<cvParam accession="MS:1000016" value="0.5" name="scan start time" unitAccession="UO:0000031" unitName="minute" unitCvRef="UO"/>'
        assert _parse_rt(text) == pytest.approx(30.0)

    def test_missing_rt_returns_none(self):
        assert _parse_rt("<spectrum></spectrum>") is None

    def test_scientific_notation(self):
        text = '<cvParam accession="MS:1000016" value="1.0e1" name="scan start time" unitAccession="UO:0000031" unitName="minute" unitCvRef="UO"/>'
        assert _parse_rt(text) == pytest.approx(600.0)


class TestParseMsLevel:
    def test_ms1(self):
        assert _parse_ms_level('<cvParam accession="MS:1000511" value="1"/>') == 1

    def test_ms2(self):
        assert _parse_ms_level('<cvParam accession="MS:1000511" value="2"/>') == 2

    def test_missing_defaults_to_1(self):
        assert _parse_ms_level("<spectrum></spectrum>") == 1


class TestParseScanId:
    def test_thermo_style(self):
        text = 'id="controllerType=0 controllerNumber=1 scan=42"'
        assert _parse_scan_id(text) == 42

    def test_missing_returns_minus_one(self):
        assert _parse_scan_id("<spectrum></spectrum>") == -1

    def test_non_numeric_id_returned_as_string(self):
        text = 'id="some-non-numeric-id"'
        result = _parse_scan_id(text)
        assert isinstance(result, str)


class TestParsePrecursor:
    BLOCK = """
    <selectedIon>
      <cvParam accession="MS:1000744" value="150.0625" name="selected ion m/z"/>
      <cvParam accession="MS:1000041" value="2" name="charge state"/>
    </selectedIon>"""

    def test_precursor_mz(self):
        assert _parse_precursor_mz(self.BLOCK) == pytest.approx(150.0625)

    def test_precursor_charge(self):
        assert _parse_precursor_charge(self.BLOCK) == 2

    def test_missing_precursor_mz(self):
        assert _parse_precursor_mz("<spectrum></spectrum>") is None

    def test_missing_charge(self):
        assert _parse_precursor_charge("<spectrum></spectrum>") is None


class TestParseBinaryArrays:
    def test_mz_array_decoded_correctly(self):
        expected = np.array([100.0, 150.0, 200.0])
        text = f"<spectrum>{_mz_block(expected)}{_int_block(expected * 10)}</spectrum>"
        mz, _ = _parse_binary_arrays(text)
        assert mz is not None
        np.testing.assert_allclose(mz, expected, rtol=1e-5)

    def test_intensity_array_decoded_correctly(self):
        mz_exp  = np.array([100.0, 150.0])
        int_exp = np.array([5000.0, 8000.0])
        text = f"<spectrum>{_mz_block(mz_exp)}{_int_block(int_exp)}</spectrum>"
        _, intensity = _parse_binary_arrays(text)
        assert intensity is not None
        np.testing.assert_allclose(intensity, int_exp, rtol=1e-5)

    def test_empty_binary_returns_none(self):
        text = "<spectrum><binaryDataArray><binary></binary></binaryDataArray></spectrum>"
        mz, intensity = _parse_binary_arrays(text)
        assert mz is None
        assert intensity is None

    def test_float32_precision_sufficient(self):
        """float32 gives ~6 significant digits — enough for 4-6 decimal places."""
        val = np.array([123.456789])
        text = f"<spectrum>{_mz_block(val)}</spectrum>"
        mz, _ = _parse_binary_arrays(text)
        assert mz is not None
        # float32 relative precision ~1e-7; 4 decimal places on ~100 Da = 0.0001 → fine
        assert abs(float(mz[0]) - 123.456789) < 0.001


# ---------------------------------------------------------------------------
# Unit tests — blob helpers
# ---------------------------------------------------------------------------

class TestBlobHelpers:
    def test_roundtrip(self):
        arr = np.array([1.0, 2.5, 100.123], dtype=np.float32)
        np.testing.assert_array_equal(blob_to_array(array_to_blob(arr)), arr)

    def test_compression_reduces_size(self):
        arr = np.zeros(1000, dtype=np.float32)
        assert len(array_to_blob(arr)) < arr.nbytes

    def test_float64_input_stored_as_float32(self):
        arr64 = np.array([1.0, 2.0, 3.0], dtype=np.float64)
        result = blob_to_array(array_to_blob(arr64))
        assert result.dtype == np.float32


# ---------------------------------------------------------------------------
# Integration tests — MzmlParser + MzmlFile
# ---------------------------------------------------------------------------

class TestMzmlParser:
    def test_parse_returns_mzml_file(self, parser, simple_mzml):
        result = parser.parse(simple_mzml)
        assert isinstance(result, MzmlFile)

    def test_counts_ms1(self, parser, simple_mzml):
        result = parser.parse(simple_mzml)
        assert result.n_ms1 == 2

    def test_counts_ms2(self, parser, simple_mzml):
        result = parser.parse(simple_mzml)
        assert result.n_ms2 == 1

    def test_ms2_disabled(self, tmp_path, simple_mzml):
        p = MzmlParser(
            tmp_path / "ms1.db", tmp_path / "ms2.db",
            include_ms2=False,
        )
        result = p.parse(simple_mzml)
        assert result.n_ms2 == 0
        assert result.n_ms1 == 2

    def test_source_path_recorded(self, parser, simple_mzml):
        result = parser.parse(simple_mzml)
        assert result.source_path == simple_mzml

    def test_db_files_created(self, parser, simple_mzml):
        result = parser.parse(simple_mzml)
        assert result.ms1_db_path.exists()
        assert result.ms2_db_path.exists()

    def test_rt_range(self, parser, simple_mzml):
        result = parser.parse(simple_mzml)
        # RT: 0.10 min and 0.21 min → 6.0 s and 12.6 s
        assert result.rt_range[0] == pytest.approx(6.0,  rel=1e-3)
        assert result.rt_range[1] == pytest.approx(12.6, rel=1e-3)

    def test_ms1_mz_range(self, parser, simple_mzml):
        result = parser.parse(simple_mzml)
        # MS1 scans have mz: [100,150,200,250] and [50,60,70]
        assert result.ms1_mz_range[0] == pytest.approx(50.0,  rel=1e-3)
        assert result.ms1_mz_range[1] == pytest.approx(250.0, rel=1e-3)

    def test_ms2_precursor_mz_range(self, parser, simple_mzml):
        result = parser.parse(simple_mzml)
        assert result.ms2_precursor_mz_range[0] == pytest.approx(150.0, rel=1e-3)
        assert result.ms2_precursor_mz_range[1] == pytest.approx(150.0, rel=1e-3)

    def test_ms1_only_file(self, tmp_path, ms1_only_mzml):
        p = MzmlParser(tmp_path / "ms1.db", tmp_path / "ms2.db")
        result = p.parse(ms1_only_mzml)
        assert result.n_ms1 == 2
        assert result.n_ms2 == 0

    def test_progress_callback_called(self, tmp_path, simple_mzml):
        calls = []
        p = MzmlParser(
            tmp_path / "ms1.db", tmp_path / "ms2.db",
            progress_callback=lambda n1, n2: calls.append((n1, n2)),
        )
        p.parse(simple_mzml)
        # 3 spectra → 3 callback calls
        assert len(calls) == 3
        assert calls[-1] == (2, 1)


class TestMzmlFileDatabase:
    """Verify the SQLite schema and stored values are correct."""

    @pytest.fixture()
    def result(self, parser, simple_mzml) -> MzmlFile:
        return parser.parse(simple_mzml)

    def test_ms1_table_row_count(self, result):
        with result.ms1_connection() as con:
            n = con.execute("SELECT COUNT(*) FROM ms1_scans").fetchone()[0]
        assert n == 2

    def test_ms2_table_row_count(self, result):
        with result.ms2_connection() as con:
            n = con.execute("SELECT COUNT(*) FROM ms2_scans").fetchone()[0]
        assert n == 1

    def test_ms1_scan_id_stored(self, result):
        with result.ms1_connection() as con:
            ids = {r[0] for r in con.execute("SELECT scan_id FROM ms1_scans")}
        assert ids == {1, 2}

    def test_ms1_rt_stored_in_seconds(self, result):
        with result.ms1_connection() as con:
            rts = sorted(r[0] for r in con.execute("SELECT rt FROM ms1_scans"))
        assert rts[0] == pytest.approx(6.0,  rel=1e-3)
        assert rts[1] == pytest.approx(12.0, rel=1e-3)

    def test_ms1_mz_min_max_stored(self, result):
        with result.ms1_connection() as con:
            row = con.execute(
                "SELECT mz_min, mz_max FROM ms1_scans WHERE scan_id=1"
            ).fetchone()
        assert row[0] == pytest.approx(100.0, rel=1e-3)
        assert row[1] == pytest.approx(250.0, rel=1e-3)

    def test_ms1_blob_roundtrip(self, result):
        with result.ms1_connection() as con:
            row = con.execute(
                "SELECT mz_array, intensity_array FROM ms1_scans WHERE scan_id=1"
            ).fetchone()
        mz  = blob_to_array(row[0])
        ints = blob_to_array(row[1])
        np.testing.assert_allclose(mz,  MZ_1,  rtol=1e-4)
        np.testing.assert_allclose(ints, INT_1, rtol=1e-4)

    def test_ms2_precursor_mz_stored(self, result):
        with result.ms2_connection() as con:
            pmz = con.execute(
                "SELECT precursor_mz FROM ms2_scans WHERE scan_id=3"
            ).fetchone()[0]
        assert pmz == pytest.approx(150.0, rel=1e-3)

    def test_ms2_precursor_charge_stored(self, result):
        with result.ms2_connection() as con:
            charge = con.execute(
                "SELECT precursor_charge FROM ms2_scans WHERE scan_id=3"
            ).fetchone()[0]
        assert charge == 1

    def test_ms2_blob_roundtrip(self, result):
        with result.ms2_connection() as con:
            row = con.execute(
                "SELECT mz_array, intensity_array FROM ms2_scans WHERE scan_id=3"
            ).fetchone()
        mz   = blob_to_array(row[0])
        ints = blob_to_array(row[1])
        np.testing.assert_allclose(mz,  MZ_3,  rtol=1e-4)
        np.testing.assert_allclose(ints, INT_3, rtol=1e-4)

    def test_ms1_n_peaks_stored(self, result):
        with result.ms1_connection() as con:
            n = con.execute(
                "SELECT n_peaks FROM ms1_scans WHERE scan_id=1"
            ).fetchone()[0]
        assert n == len(MZ_1)

    def test_precursor_mz_index_exists(self, result):
        with result.ms2_connection() as con:
            idxs = {
                r[1] for r in con.execute("PRAGMA index_list(ms2_scans)")
            }
        assert "idx_ms2_precursor_mz" in idxs

    def test_ms1_connections_are_independent(self, result):
        """Each call to ms1_connection() returns a distinct connection object."""
        con1 = result.ms1_connection()
        con2 = result.ms1_connection()
        assert con1 is not con2
        con1.close()
        con2.close()
