"""
test_mzml_parser.py
"""
from __future__ import annotations

import json
import zlib
from base64 import b64encode

import numpy as np
import pytest

from msianalyzer.core.mzml_parser import (
    MzmlFile,
    MzmlParser,
    array_to_blob,
    blob_to_array,
    log_command,
    _parse_rt,
    _parse_ms_level,
    _parse_scan_id,
    _parse_precursor_mz,
    _parse_precursor_charge,
    _parse_precursor_intensity,
    _parse_parent_scan_id,
    _parse_tic,
    _parse_binary_arrays,
    _parse_instrument_info,
)


# ---------------------------------------------------------------------------
# mzML fixture builders
# ---------------------------------------------------------------------------

def _encode(arr: np.ndarray, dtype=np.float64) -> str:
    return b64encode(zlib.compress(arr.astype(dtype).tobytes())).decode()


def _mz_block(arr):
    enc = _encode(arr)
    return f"""
        <binaryDataArray encodedLength="{len(enc)}">
          <cvParam accession="MS:1000514" name="m/z array"/>
          <cvParam accession="MS:1000523" name="64-bit float"/>
          <cvParam accession="MS:1000574" name="zlib compression"/>
          <binary>{enc}</binary>
        </binaryDataArray>"""


def _int_block(arr):
    enc = _encode(arr)
    return f"""
        <binaryDataArray encodedLength="{len(enc)}">
          <cvParam accession="MS:1000515" name="intensity array"/>
          <cvParam accession="MS:1000523" name="64-bit float"/>
          <cvParam accession="MS:1000574" name="zlib compression"/>
          <binary>{enc}</binary>
        </binaryDataArray>"""


def _ms1_spectrum(scan, rt_min, mz, intensity, polarity="NEGATIVE"):
    return f"""
    <spectrum id="controllerType=0 controllerNumber=1 scan={scan}" index="{scan-1}">
      <cvParam accession="MS:1000511" value="1" name="ms level"/>
      <cvParam cvRef="MS" accession="MS:1000130" name="{polarity.lower()} scan"/>
      <cvParam accession="MS:1000285" value="99999" name="total ion current"/>
      <scanList count="1">
        <scan>
          <cvParam accession="MS:1000016" value="{rt_min}"
                   name="scan start time" unitAccession="UO:0000031"
                   unitName="minute" unitCvRef="UO"/>
        </scan>
      </scanList>
      <binaryDataArrayList count="2">
        {_mz_block(mz)}{_int_block(intensity)}
      </binaryDataArrayList>
    </spectrum>"""


def _ms2_spectrum(scan, rt_min, precursor_mz, precursor_charge,
                  precursor_intensity, parent_scan, mz, intensity,
                  polarity="NEGATIVE"):
    pol_acc = "MS:1000129" if polarity == "NEGATIVE" else "MS:1000130"
    return f"""
    <spectrum id="controllerType=0 controllerNumber=1 scan={scan}" index="{scan-1}">
      <cvParam accession="MS:1000511" value="2" name="ms level"/>
      <cvParam cvRef="MS" accession="{pol_acc}" name="{polarity.lower()} scan"/>
      <cvParam accession="MS:1000285" value="12345" name="total ion current"/>
      <scanList count="1">
        <scan>
          <cvParam accession="MS:1000016" value="{rt_min}"
                   name="scan start time" unitAccession="UO:0000031"
                   unitName="minute" unitCvRef="UO"/>
        </scan>
      </scanList>
      <precursorList count="1">
        <precursor spectrumRef="controllerType=0 controllerNumber=1 scan={parent_scan}">
          <selectedIonList count="1">
            <selectedIon>
              <cvParam accession="MS:1000744" value="{precursor_mz}" name="selected ion m/z"/>
              <cvParam accession="MS:1000041" value="{precursor_charge}" name="charge state"/>
              <cvParam accession="MS:1000042" value="{precursor_intensity}" name="peak intensity"/>
            </selectedIon>
          </selectedIonList>
        </precursor>
      </precursorList>
      <binaryDataArrayList count="2">
        {_mz_block(mz)}{_int_block(intensity)}
      </binaryDataArrayList>
    </spectrum>"""


INSTRUMENT_HEADER = """<?xml version="1.0" encoding="utf-8"?>
<indexedmzML xmlns="http://psi.hupo.org/ms/mzml">
  <mzML>
    <referenceableParamGroupList count="1">
      <referenceableParamGroup id="commonInstrumentParams">
        <cvParam cvRef="MS" accession="MS:1000483" value="" name="Thermo Fisher Scientific instrument model"/>
        <cvParam cvRef="MS" accession="MS:1000529" value="FSN12345" name="instrument serial number"/>
      </referenceableParamGroup>
    </referenceableParamGroupList>
    <instrumentConfigurationList count="1">
      <instrumentConfiguration id="IC1">
        <referenceableParamGroupRef ref="commonInstrumentParams"/>
        <componentList count="3">
          <source order="1">
            <cvParam cvRef="MS" accession="MS:1000073" value="" name="electrospray ionization"/>
          </source>
          <analyzer order="2">
            <cvParam cvRef="MS" accession="MS:1000484" value="" name="orbitrap"/>
          </analyzer>
        </componentList>
      </instrumentConfiguration>
    </instrumentConfigurationList>
    <run id="test" startTimeStamp="2025-01-26T10:00:00Z">
      <spectrumList count="3">"""

FOOTER = """
      </spectrumList>
    </run>
  </mzML>
</indexedmzML>"""


MZ_1   = np.array([100.0, 150.0, 200.0, 250.0])
INT_1  = np.array([1000.0, 2000.0, 3000.0, 4000.0])
MZ_2   = np.array([50.0, 60.0, 70.0])
INT_2  = np.array([500.0, 600.0, 700.0])
MZ_3   = np.array([80.0, 90.0])
INT_3  = np.array([800.0, 900.0])


@pytest.fixture()
def simple_mzml(tmp_path):
    content = (
        INSTRUMENT_HEADER
        + _ms1_spectrum(1, 0.10, MZ_1, INT_1, polarity="NEGATIVE")
        + _ms1_spectrum(2, 0.20, MZ_2, INT_2, polarity="POSITIVE")
        + _ms2_spectrum(3, 0.21, 150.0, 1, 4654285.0, parent_scan=2,
                        mz=MZ_3, intensity=INT_3, polarity="POSITIVE")
        + FOOTER
    )
    p = tmp_path / "test.mzML"
    p.write_text(content, encoding="utf-8")
    return p


@pytest.fixture()
def parser(tmp_path):
    return MzmlParser()


@pytest.fixture()
def result(parser, simple_mzml, tmp_path):
    print(tmp_path)
    return parser.parse(simple_mzml, tmp_path / "ms1.db", tmp_path / "ms2.db")


# ---------------------------------------------------------------------------
# Unit tests — pure parsing helpers
# ---------------------------------------------------------------------------

class TestParseRt:
    def test_minutes_to_seconds(self):
        text = '<cvParam accession="MS:1000016" value="1.0" unitAccession="UO:0000031"/>'
        assert _parse_rt(text) == pytest.approx(60.0)

    def test_fractional_minutes(self):
        text = '<cvParam accession="MS:1000016" value="0.5" unitAccession="UO:0000031"/>'
        assert _parse_rt(text) == pytest.approx(30.0)

    def test_missing_returns_none(self):
        assert _parse_rt("<spectrum></spectrum>") is None

    def test_scientific_notation(self):
        text = '<cvParam accession="MS:1000016" value="1.0e1" unitAccession="UO:0000031"/>'
        assert _parse_rt(text) == pytest.approx(600.0)


class TestParseMsLevel:
    def test_ms1(self):
        assert _parse_ms_level('<cvParam accession="MS:1000511" value="1"/>') == 1

    def test_ms2(self):
        assert _parse_ms_level('<cvParam accession="MS:1000511" value="2"/>') == 2

    def test_missing_defaults_to_1(self):
        assert _parse_ms_level("<spectrum/>") == 1


class TestParseScanId:
    def test_thermo_style(self):
        assert _parse_scan_id('id="controllerType=0 controllerNumber=1 scan=42"') == 42

    def test_missing_returns_minus_one(self):
        assert _parse_scan_id("<spectrum/>") == -1


class TestParsePrecursor:
    BLOCK = """
    <selectedIon>
      <cvParam accession="MS:1000744" value="185.1270" name="selected ion m/z"/>
      <cvParam accession="MS:1000041" value="2" name="charge state"/>
      <cvParam accession="MS:1000042" value="4654285.5" name="peak intensity"/>
    </selectedIon>"""

    def test_mz(self):
        assert _parse_precursor_mz(self.BLOCK) == pytest.approx(185.127)

    def test_charge(self):
        assert _parse_precursor_charge(self.BLOCK) == 2

    def test_intensity(self):
        assert _parse_precursor_intensity(self.BLOCK) == pytest.approx(4654285.5)

    def test_missing_intensity_returns_none(self):
        assert _parse_precursor_intensity("<spectrum/>") is None


class TestParseParentScanId:
    def test_thermo_specref(self):
        text = '<precursor spectrumRef="controllerType=0 controllerNumber=1 scan=42">'
        assert _parse_parent_scan_id(text) == 42

    def test_missing_returns_none(self):
        assert _parse_parent_scan_id("<spectrum/>") is None


class TestParseTic:
    def test_tic_parsed(self):
        text = '<cvParam accession="MS:1000285" value="13104868"/>'
        assert _parse_tic(text) == pytest.approx(13104868.0)

    def test_missing_returns_none(self):
        assert _parse_tic("<spectrum/>") is None


class TestParseBinaryArrays:
    def test_mz_decoded(self):
        expected = np.array([100.0, 150.0, 200.0])
        text = f"<spectrum>{_mz_block(expected)}{_int_block(expected)}</spectrum>"
        mz, _ = _parse_binary_arrays(text)
        np.testing.assert_allclose(mz, expected, rtol=1e-5)

    def test_intensity_decoded(self):
        mz_e  = np.array([100.0, 150.0])
        int_e = np.array([5000.0, 8000.0])
        text  = f"<spectrum>{_mz_block(mz_e)}{_int_block(int_e)}</spectrum>"
        _, intensity = _parse_binary_arrays(text)
        np.testing.assert_allclose(intensity, int_e, rtol=1e-5)

    def test_empty_binary_returns_none(self):
        mz, i = _parse_binary_arrays(
            "<spectrum><binaryDataArray><binary></binary></binaryDataArray></spectrum>"
        )
        assert mz is None and i is None


class TestParseInstrumentInfo:
    def test_extracts_from_fixture(self, simple_mzml):
        info = _parse_instrument_info(simple_mzml)
        assert info["ionization"] == "electrospray ionization"
        assert info["analyzer"]   == "orbitrap"
        assert info["serial_number"] == "FSN12345"
        assert info["acquisition_start"] == "2025-01-26T10:00:00Z"

    def test_missing_fields_are_none(self, tmp_path):
        p = tmp_path / "minimal.mzML"
        p.write_text("<mzML><instrumentConfigurationList/></mzML>")
        info = _parse_instrument_info(p)
        assert info["instrument_model"] is None
        assert info["analyzer"]         is None


# ---------------------------------------------------------------------------
# Blob helpers
# ---------------------------------------------------------------------------

class TestBlobHelpers:
    def test_roundtrip(self):
        arr = np.array([1.0, 2.5, 100.123], dtype=np.float32)
        np.testing.assert_array_equal(blob_to_array(array_to_blob(arr)), arr)

    def test_float64_stored_as_float32(self):
        arr64 = np.array([1.0, 2.0, 3.0], dtype=np.float64)
        assert blob_to_array(array_to_blob(arr64)).dtype == np.float32


# ---------------------------------------------------------------------------
# Integration — MzmlParser
# ---------------------------------------------------------------------------

class TestMzmlParser:
    def test_returns_mzml_file(self, result):
        assert isinstance(result, MzmlFile)

    def test_counts(self, result):
        assert result.n_ms1 == 2
        assert result.n_ms2 == 1

    def test_db_files_created(self, result):
        assert result.ms1_db_path.exists()
        assert result.ms2_db_path.exists()

    def test_rt_range(self, result):
        assert result.rt_range[0] == pytest.approx(6.0,  rel=1e-3)
        assert result.rt_range[1] == pytest.approx(12.6, rel=1e-3)

    def test_ms1_mz_range(self, result):
        assert result.ms1_mz_range[0] == pytest.approx(50.0,  rel=1e-3)
        assert result.ms1_mz_range[1] == pytest.approx(250.0, rel=1e-3)

    def test_instrument_info_populated(self, result):
        assert result.instrument_info["ionization"] == "electrospray ionization"
        assert result.instrument_info["analyzer"]   == "orbitrap"

    def test_no_ms2_flag(self, tmp_path, simple_mzml):
        p = MzmlParser(include_ms2=False)
        r = p.parse(simple_mzml, tmp_path / "a.db", tmp_path / "b.db", )
        assert r.n_ms2 == 0

    def test_progress_callback(self, tmp_path, simple_mzml):
        calls = []
        p = MzmlParser(progress_callback=lambda n1, n2: calls.append((n1, n2)))
        p.parse(simple_mzml, tmp_path / "a.db", tmp_path / "b.db")
        assert len(calls) == 3
        assert calls[-1] == (2, 1)


# ---------------------------------------------------------------------------
# Integration — metadata table
# ---------------------------------------------------------------------------

class TestMetadataTable:
    def test_ms1_metadata_keys(self, result):
        with result.ms1_connection() as con:
            rows = dict(con.execute("SELECT key, value FROM metadata").fetchall())
        assert "source_file"       in rows
        assert "instrument_model"  in rows
        assert "ionization"        in rows
        assert "analyzer"          in rows
        assert "db_creation_date"  in rows
        assert "msianalyzer_version" in rows

    def test_ms2_metadata_keys(self, result):
        with result.ms2_connection() as con:
            rows = dict(con.execute("SELECT key, value FROM metadata").fetchall())
        assert "source_file"  in rows
        assert "analyzer"     in rows

    def test_source_file_correct(self, result, simple_mzml):
        with result.ms1_connection() as con:
            val = con.execute(
                "SELECT value FROM metadata WHERE key='source_file'"
            ).fetchone()[0]
        assert val == simple_mzml.name

    def test_analyzer_value(self, result):
        with result.ms1_connection() as con:
            val = con.execute(
                "SELECT value FROM metadata WHERE key='analyzer'"
            ).fetchone()[0]
        assert val == "orbitrap"


# ---------------------------------------------------------------------------
# Integration — commands table
# ---------------------------------------------------------------------------

class TestCommandsTable:
    def test_parse_command_written(self, result):
        with result.ms1_connection() as con:
            rows = con.execute("SELECT command_name FROM commands").fetchall()
        assert any(r[0] == "parse" for r in rows)

    def test_command_arguments_json(self, result):
        with result.ms1_connection() as con:
            row = con.execute(
                "SELECT arguments FROM commands WHERE command_name='parse'"
            ).fetchone()
        args = json.loads(row[0])
        assert "source_file"    in args
        assert "decimal_places" in args
        assert "include_ms2"    in args

    def test_command_datetime_set(self, result):
        with result.ms1_connection() as con:
            dt = con.execute(
                "SELECT datetime FROM commands WHERE command_name='parse'"
            ).fetchone()[0]
        assert dt  # non-empty ISO string

    def test_log_command_helper(self, result):
        log_command(result.ms1_db_path, "test_cmd", {"foo": "bar", "n": 42})
        with result.ms1_connection() as con:
            row = con.execute(
                "SELECT arguments FROM commands WHERE command_name='test_cmd'"
            ).fetchone()
        assert json.loads(row[0]) == {"foo": "bar", "n": 42}


# ---------------------------------------------------------------------------
# Integration — ms2_scans new columns
# ---------------------------------------------------------------------------

class TestMs2NewColumns:
    def test_precursor_intensity_stored(self, result):
        with result.ms2_connection() as con:
            val = con.execute(
                "SELECT precursor_intensity FROM ms2_scans WHERE scan_id=3"
            ).fetchone()[0]
        assert val == pytest.approx(4654285.0, rel=1e-3)

    def test_parent_scan_id_stored(self, result):
        with result.ms2_connection() as con:
            val = con.execute(
                "SELECT parent_scan_id FROM ms2_scans WHERE scan_id=3"
            ).fetchone()[0]
        assert val == 2

    def test_group_id_null_on_parse(self, result):
        with result.ms2_connection() as con:
            val = con.execute(
                "SELECT group_id FROM ms2_scans WHERE scan_id=3"
            ).fetchone()[0]
        assert val is None

    def test_polarity_per_scan_ms1(self, result):
        with result.ms1_connection() as con:
            rows = dict(con.execute(
                "SELECT scan_id, polarity FROM ms1_scans"
            ).fetchall())
        assert rows[1] == "NEGATIVE"
        assert rows[2] == "POSITIVE"

    def test_tic_stored_ms2(self, result):
        with result.ms2_connection() as con:
            val = con.execute(
                "SELECT tic FROM ms2_scans WHERE scan_id=3"
            ).fetchone()[0]
        assert val == pytest.approx(12345.0)