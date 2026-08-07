import sqlite3
import uuid
import zlib
from base64 import b64encode
from pathlib import Path
import numpy as np
import pytest

from msianalyzer.core.parser.mzml_parser import (
    MzmlFile,
    MzmlParser,
    array_to_blob,
    blob_to_array,
    log_command,
    _parse_binary_arrays,
    _parse_collision_energy,
    _parse_filter_string,
    _parse_instrument_info,
    _parse_isolation_lower,
    _parse_isolation_target,
    _parse_isolation_upper,
    _parse_ms_level,
    _parse_parent_scan_id,
    _parse_precursor_charge,
    _parse_precursor_intensity,
    _parse_precursor_mz,
    _parse_rt,
    _parse_scan_id,
    _parse_scan_polarity,
    _parse_tic,
)

# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def sample_run_id() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def synthetic_mzml_file(tmp_path: Path) -> Path:
    """Generates a minimal, valid mzML XML file on disk containing MS1 and MS2 scans."""
    mzml_path = tmp_path / "sample.mzml"

    # Generate binary base64 data for arrays
    mz_data = np.array([100.0, 200.0, 300.0], dtype=np.float32).tobytes()
    int_data = np.array([1000.0, 5000.0, 2500.0], dtype=np.float32).tobytes()

    mz_b64 = b64encode(zlib.compress(mz_data)).decode("ascii")
    int_b64 = b64encode(zlib.compress(int_data)).decode("ascii")

    content = f"""<?xml version="1.0" encoding="utf-8"?>
<indexedmzML xmlns="http://psi.hupo.org/ms/mzml">
  <mzML>
    <instrumentConfigurationList count="1">
      <instrumentConfiguration id="IC1">
        <cvParam accession="MS:1000031" name="instrument model" value="Orbitrap Exploris 480"/>
        <cvParam accession="MS:1000529" name="instrument serial number" value="SN12345"/>
        <componentList count="2">
          <source order="1">
            <cvParam accession="MS:1000073" name="electrospray ionization"/>
          </source>
          <analyzer order="2">
            <cvParam accession="MS:1000484" name="orbitrap"/>
          </analyzer>
        </componentList>
      </instrumentConfiguration>
    </instrumentConfigurationList>
    <run id="run1" startTimeStamp="2026-01-01T10:00:00Z">
      <sourceFilePath name="raw_sample.raw"/>
      <spectrumList count="2">
        <spectrum index="0" id="controllerType=0 controllerNumber=1 scan=1" defaultArrayLength="3">
          <cvParam accession="MS:1000511" name="ms level" value="1"/>
          <cvParam accession="MS:1000130" name="positive scan"/>
          <cvParam accession="MS:1000285" name="total ion current" value="8500.0"/>
          <scanList count="1">
            <scan>
              <cvParam accession="MS:1000016" name="scan start time" value="1.5" unitAccession="UO:0000031"/>
            </scan>
          </scanList>
          <binaryDataArray list>
            <cvParam accession="MS:1000514" name="m/z array"/>
            <cvParam accession="MS:1000574" name="zlib compression"/>
            <binary>{mz_b64}</binary>
          </binaryDataArray>
          <binaryDataArray list>
            <cvParam accession="MS:1000515" name="intensity array"/>
            <cvParam accession="MS:1000574" name="zlib compression"/>
            <binary>{int_b64}</binary>
          </binaryDataArray>
        </spectrum>
        <spectrum index="1" id="controllerType=0 controllerNumber=1 scan=2" defaultArrayLength="3">
          <cvParam accession="MS:1000511" name="ms level" value="2"/>
          <cvParam accession="MS:1000130" name="positive scan"/>
          <cvParam accession="MS:1000285" name="total ion current" value="4000.0"/>
          <cvParam accession="MS:1000512" name="filter string" value="FTMS + p NSI d Full ms2 200.00@hcd30.00"/>
          <scanList count="1">
            <scan>
              <cvParam accession="MS:1000016" name="scan start time" value="95.0" unitAccession="UO:0000010"/>
            </scan>
          </scanList>
          <precursorList count="1">
            <precursor spectrumRef="controllerType=0 controllerNumber=1 scan=1">
              <isolationWindow>
                <cvParam accession="MS:1000827" name="isolation window target m/z" value="200.0"/>
                <cvParam accession="MS:1000828" name="isolation window lower offset" value="0.5"/>
                <cvParam accession="MS:1000829" name="isolation window upper offset" value="0.5"/>
              </isolationWindow>
              <selectedIonList count="1">
                <selectedIon>
                  <cvParam accession="MS:1000744" name="selected ion m/z" value="200.0123"/>
                  <cvParam accession="MS:1000041" name="charge state" value="2"/>
                  <cvParam accession="MS:1000042" name="peak intensity" value="50000.0"/>
                </selectedIon>
              </selectedIonList>
              <activation>
                <cvParam accession="MS:1000045" name="collision energy" value="30.0"/>
              </activation>
            </precursor>
          </precursorList>
          <binaryDataArray list>
            <cvParam accession="MS:1000514" name="m/z array"/>
            <cvParam accession="MS:1000574" name="zlib compression"/>
            <binary>{mz_b64}</binary>
          </binaryDataArray>
          <binaryDataArray list>
            <cvParam accession="MS:1000515" name="intensity array"/>
            <cvParam accession="MS:1000574" name="zlib compression"/>
            <binary>{int_b64}</binary>
          </binaryDataArray>
        </spectrum>
      </spectrumList>
    </run>
  </mzML>
</indexedmzML>
"""
    mzml_path.write_text(content, encoding="utf-8")
    return mzml_path


# ===========================================================================
# Unit Tests: Blob Helpers
# ===========================================================================


def test_blob_roundtrip_compressed():
    original = np.array([100.123456, 200.654321, 300.999999], dtype=np.float32)
    blob = array_to_blob(original, decimal_places=4, compressed=True)
    restored = blob_to_array(blob, decimal_places=4, compressed=True)

    assert isinstance(blob, bytes)
    assert len(restored) == len(original)
    np.testing.assert_almost_equal(restored, np.round(original, 4))


def test_blob_roundtrip_uncompressed():
    original = np.array([10.5, 20.25, 30.125], dtype=np.float32)
    blob = array_to_blob(original, decimal_places=2, compressed=False)
    restored = blob_to_array(blob, decimal_places=2, compressed=False)

    np.testing.assert_almost_equal(restored, np.round(original, 2))


# ===========================================================================
# Unit Tests: Regex & Parsing Helpers
# ===========================================================================


def test_parse_rt_units():
    # Unit UO:0000031 = minutes -> should convert to seconds (* 60)
    xml_min = '<cvParam accession="MS:1000016" value="2.5" unitAccession="UO:0000031"/>'
    assert _parse_rt(xml_min) == 150.0

    # Unit UO:0000010 = seconds -> should keep raw value
    xml_sec = (
        '<cvParam accession="MS:1000016" value="90.0" unitAccession="UO:0000010"/>'
    )
    assert _parse_rt(xml_sec) == 90.0

    # Missing RT
    assert _parse_rt('<cvParam accession="MS:1000000" value="1.0"/>') is None


def test_parse_ms_level():
    assert _parse_ms_level('accession="MS:1000511" value="2"') == 2
    assert _parse_ms_level("<no_ms_level/>") == 1


def test_parse_scan_id():
    xml_scan_num = 'id="controllerType=0 controllerNumber=1 scan=42"'
    assert _parse_scan_id(xml_scan_num) == 42

    xml_custom_id = 'id="spectrum_abc123"'
    assert _parse_scan_id(xml_custom_id) == "spectrum_abc123"

    assert _parse_scan_id("<no_id>") == -1


def test_parse_scan_polarity():
    pos_xml = '<cvParam cvRef="MS" accession="MS:1000130" name="positive scan"/>'
    neg_xml = '<cvParam cvRef="MS" accession="MS:1000129" name="negative scan"/>'

    assert _parse_scan_polarity(pos_xml) == "POSITIVE"
    assert _parse_scan_polarity(neg_xml) == "NEGATIVE"
    assert _parse_scan_polarity("<no_polarity/>") is None


def test_parse_precursor_and_isolation():
    xml = """
    <precursor spectrumRef="scan=100">
      <isolationWindow>
        <cvParam accession="MS:1000827" value="500.2500"/>
        <cvParam accession="MS:1000828" value="1.0"/>
        <cvParam accession="MS:1000829" value="1.0"/>
      </isolationWindow>
      <selectedIonList>
        <selectedIon>
          <cvParam accession="MS:1000744" value="500.2541"/>
          <cvParam accession="MS:1000041" value="3"/>
          <cvParam accession="MS:1000042" value="123456.7"/>
        </selectedIon>
      </selectedIonList>
      <activation>
        <cvParam accession="MS:1000045" value="28.5"/>
      </activation>
    </precursor>
    """
    assert _parse_parent_scan_id(xml) == 100
    assert _parse_isolation_target(xml, decimal_places=2) == 500.25
    assert _parse_isolation_lower(xml) == 1.0
    assert _parse_isolation_upper(xml) == 1.0
    assert _parse_precursor_mz(xml, decimal_places=4) == 500.2541
    assert _parse_precursor_charge(xml) == 3
    assert _parse_precursor_intensity(xml) == 123456.7
    assert _parse_collision_energy(xml) == 28.5


def test_parse_binary_arrays_zlib():
    raw_mz = np.array([100.0, 200.0], dtype=np.float32).tobytes()
    raw_int = np.array([50.0, 150.0], dtype=np.float32).tobytes()

    b64_mz = b64encode(zlib.compress(raw_mz)).decode()
    b64_int = b64encode(zlib.compress(raw_int)).decode()

    text = f"""
    <binaryDataArray>
        <cvParam accession="MS:1000514"/> <!-- m/z -->
        <cvParam accession="MS:1000574"/> <!-- zlib -->
        <binary>{b64_mz}</binary>
    </binaryDataArray>
    <binaryDataArray>
        <cvParam accession="MS:1000515"/> <!-- intensity -->
        <cvParam accession="MS:1000574"/> <!-- zlib -->
        <binary>{b64_int}</binary>
    </binaryDataArray>
    """
    mz, intensity = _parse_binary_arrays(text)
    assert mz is not None and intensity is not None
    np.testing.assert_array_almost_equal(mz, [100.0, 200.0])
    np.testing.assert_array_almost_equal(intensity, [50.0, 150.0])


def test_parse_instrument_info(synthetic_mzml_file: Path):
    info = _parse_instrument_info(synthetic_mzml_file)

    assert info["instrument_model"] == "Orbitrap Exploris 480"
    assert info["serial_number"] == "SN12345"
    assert info["ionization"] == "electrospray ionization"
    assert info["analyzer"] == "orbitrap"
    assert info["acquisition_start"] == "2026-01-01T10:00:00Z"
    assert info["source_file"] == "raw_sample.raw"


# ===========================================================================
# Unit Tests: Filter String & TIC Parsing
# ===========================================================================


def test_parse_filter_string():
    """Verify filter string extraction for Thermo/Orbitrap scans and default fallback."""
    # Standard Orbitrap MS2 filter string
    xml_with_filter = (
        '<cvParam accession="MS:1000512" name="filter string" '
        'value="FTMS + p NSI d Full ms2 200.00@hcd30.00 [100.00-1500.00]"/>'
    )
    assert (
        _parse_filter_string(xml_with_filter)
        == "FTMS + p NSI d Full ms2 200.00@hcd30.00 [100.00-1500.00]"
    )

    # Missing filter string cvParam (returns fallback default)
    xml_missing_filter = '<cvParam accession="MS:1000511" value="1"/>'
    assert _parse_filter_string(xml_missing_filter) == 1


def test_parse_tic():
    """Verify Total Ion Current (TIC) parsing for normal values, scientific notation, and missing tags."""
    # Standard decimal value
    xml_standard = (
        '<cvParam accession="MS:1000285" name="total ion current" value="8500.5"/>'
    )
    assert _parse_tic(xml_standard) == 8500.5

    # Scientific notation value
    xml_scientific = (
        '<cvParam accession="MS:1000285" name="total ion current" value="1.25e+07"/>'
    )
    assert _parse_tic(xml_scientific) == 12500000.0

    # Missing TIC cvParam
    xml_missing = '<cvParam accession="MS:1000511" value="1"/>'
    assert _parse_tic(xml_missing) is None


# ===========================================================================
# Integration Tests: MzmlParser & SQLite Database
# ===========================================================================


def test_mzml_parser_end_to_end(
    synthetic_mzml_file: Path, tmp_path: Path, sample_run_id: str
):
    ms1_db_path = tmp_path / "ms1_test.db"

    progress_calls = []

    def progress_cb(n_ms1: int, n_ms2: int):
        progress_calls.append((n_ms1, n_ms2))

    parser = MzmlParser(
        include_ms2=True, progress_callback=progress_cb, decimal_places=4
    )

    result: MzmlFile = parser.parse(
        mzml_path=synthetic_mzml_file, ms1_db_path=ms1_db_path
    )

    # Check return object
    assert result.n_ms1 == 1
    assert result.n_ms2 == 1
    assert result.rt_range == (90.0, 95.0)  # Scan 1: 1.5 min (90s), Scan 2: 95s
    assert result.ms1_mz_range == (100.0, 300.0)
    assert result.ms2_precursor_mz_range == (200.0123, 200.0123)
    assert "Orbitrap" in str(result.instrument_info["instrument_model"])

    # Check progress callback was executed
    assert len(progress_calls) == 2

    # Check SQLite contents
    conn = result.ms1_connection()
    try:
        # 1. Metadata Table
        meta = dict(conn.execute("SELECT key, value FROM metadata").fetchall())
        assert meta["instrument_model"] == "Orbitrap Exploris 480"

        # 2. Commands Table
        cmd = conn.execute("SELECT command_name, run_id FROM commands").fetchone()
        assert cmd[0] == "parse"
        assert cmd[1] == "parse"

        # 3. MS1 Scans Table
        ms1_row = conn.execute(
            "SELECT scan_id, rt, tic, polarity FROM ms1_scans"
        ).fetchone()
        assert ms1_row[0] == 1
        assert ms1_row[1] == 90.0
        assert ms1_row[2] == 8500.0
        assert ms1_row[3] == "POSITIVE"

        # 4. MS2 Scans Table
        ms2_row = conn.execute(
            "SELECT scan_id, parent_scan_id, precursor_mz, precursor_charge FROM ms2_scans"
        ).fetchone()
        assert ms2_row[0] == 2
        assert ms2_row[1] == 1
        assert ms2_row[2] == 200.0123
        assert ms2_row[3] == 2
    finally:
        conn.close()


def test_mzml_parser_exclude_ms2(
    synthetic_mzml_file: Path, tmp_path: Path, sample_run_id: str
):
    ms1_db_path = tmp_path / "ms1_only.db"

    parser = MzmlParser(include_ms2=False)
    result = parser.parse(synthetic_mzml_file, ms1_db_path)

    assert result.n_ms1 == 1
    assert result.n_ms2 == 0

    conn = sqlite3.connect(ms1_db_path)
    count_ms2 = conn.execute("SELECT COUNT(*) FROM ms2_scans").fetchone()[0]
    conn.close()

    assert count_ms2 == 0


def test_log_command_helper(tmp_path: Path, sample_run_id: str):
    db_path = tmp_path / "test_cmd.db"

    # Pre-create schema
    parser = MzmlParser()
    conn = parser._init_ms1_db(db_path)
    conn.close()

    cmd_id = log_command(
        db_path=db_path,
        command_name="denoise_ms2",
        arguments={"threshold": 100},
        run_id=sample_run_id,
    )

    assert cmd_id is not None

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT command_name, arguments FROM commands WHERE id=?", (cmd_id,)
    ).fetchone()
    conn.close()

    assert row[0] == "denoise_ms2"
    assert '"threshold": 100' in row[1]


def test_mzml_file_repr(tmp_path: Path):
    mzfile = MzmlFile(
        source_path=Path("sample.mzML"),
        ms1_db_path=tmp_path / "sample_ms1.db",
        n_ms1=10,
        n_ms2=50,
        rt_range=(0.0, 120.0),
        ms1_mz_range=(100.0, 1000.0),
        ms2_precursor_mz_range=(150.0, 800.0),
    )
    repr_str = repr(mzfile)
    assert "source   = 'sample.mzML'" in repr_str
    assert "10 scans" in repr_str
    assert "50 scans" in repr_str
