from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# Import the target function (adjust import path as necessary)
from msianalyzer.core.parser import parse_raster_xml

# ==============================================================================
# XML GENERATION HELPERS
# ==============================================================================

NAMESPACED_XML_CONTENT = """<?xml version="1.0" encoding="UTF-8"?>
<raster startTime="2026-08-11T12:00:00Z">
  <ns:image xmlns:ns="http://www.apmaldi.com/target_raster-1.0.0" width="10" height="20" timeShift="500.0">
    <ns:pixel x="0" y="0" offset="100.0" duration="500.0"/>
    <ns:pixel x="1" y="0" offset="700.0" duration="500.0"/>
  </ns:image>
</raster>
"""

NON_NAMESPACED_XML_CONTENT = """<?xml version="1.0" encoding="UTF-8"?>
<raster startTime="2026-08-11T12:00:00Z">
  <image width="5" height="5" timeShift="0.0">
    <pixel x="2" y="3" offset="200.0" duration="1000.0"/>
  </image>
</raster>
"""

NO_TIMESHIFT_XML_CONTENT = """<?xml version="1.0" encoding="UTF-8"?>
<raster startTime="2026-08-11T12:00:00Z">
  <image width="8" height="8">
    <pixel x="0" y="0" offset="1000.0" duration="2000.0"/>
  </image>
</raster>
"""

NO_PIXELS_XML_CONTENT = """<?xml version="1.0" encoding="UTF-8"?>
<raster startTime="2026-08-11T12:00:00Z">
  <image width="4" height="4" timeShift="0.0">
  </image>
</raster>
"""

EXPECTED_COLUMNS = [
    "x",
    "y",
    "t_start",
    "t_end",
    "abs_start_sec",
    "abs_end_sec",
]


# ==============================================================================
# TESTS
# ==============================================================================


def test_parse_raster_xml_happy_path_namespaced(tmp_path: Path):
    """Tests standard parsing when XML uses the AP-MALDI namespace."""
    xml_file = tmp_path / "namespaced.xml"
    xml_file.write_text(NAMESPACED_XML_CONTENT, encoding="utf-8")

    df_pixels, metadata = parse_raster_xml(xml_file)

    # 1. Verify Metadata
    expected_start_sec = datetime(
        2026, 8, 11, 12, 0, 0, tzinfo=timezone.utc
    ).timestamp()

    assert metadata["grid_width"] == 10
    assert metadata["grid_height"] == 20
    assert metadata["raster_start_sec"] == pytest.approx(expected_start_sec)

    # 2. Verify DataFrame Structure & Contents
    assert list(df_pixels.columns) == EXPECTED_COLUMNS
    assert len(df_pixels) == 2

    # Pixel 0: offset=100ms, duration=500ms, timeShift=500ms
    # t_start = (100 + 500) / 1000 = 0.6s
    # t_end   = 0.6 + (500 / 1000) = 1.1s
    row0 = df_pixels.iloc[0]
    assert row0["x"] == 0
    assert row0["y"] == 0
    assert row0["t_start"] == pytest.approx(0.6)
    assert row0["t_end"] == pytest.approx(1.1)
    assert row0["abs_start_sec"] == pytest.approx(expected_start_sec + 0.6)
    assert row0["abs_end_sec"] == pytest.approx(expected_start_sec + 1.1)

    # Pixel 1: offset=700ms, duration=500ms, timeShift=500ms
    # t_start = (700 + 500) / 1000 = 1.2s
    # t_end   = 1.2 + (500 / 1000) = 1.7s
    row1 = df_pixels.iloc[1]
    assert row1["x"] == 1
    assert row1["y"] == 0
    assert row1["t_start"] == pytest.approx(1.2)
    assert row1["t_end"] == pytest.approx(1.7)


def test_parse_raster_xml_non_namespaced_fallback(tmp_path: Path):
    """Tests fallback logic when <image> node does not have a namespace prefix."""
    xml_file = tmp_path / "non_namespaced.xml"
    xml_file.write_text(NON_NAMESPACED_XML_CONTENT, encoding="utf-8")

    df_pixels, metadata = parse_raster_xml(xml_file)

    assert metadata["grid_width"] == 5
    assert metadata["grid_height"] == 5
    assert len(df_pixels) == 1

    row = df_pixels.iloc[0]
    assert row["x"] == 2
    assert row["y"] == 3
    # offset=200ms, duration=1000ms, timeShift=0ms
    assert row["t_start"] == pytest.approx(0.2)
    assert row["t_end"] == pytest.approx(1.2)


def test_parse_raster_xml_default_timeshift(tmp_path: Path):
    """Tests that timeShift defaults to 0.0 when missing from <image> attributes."""
    xml_file = tmp_path / "no_timeshift.xml"
    xml_file.write_text(NO_TIMESHIFT_XML_CONTENT, encoding="utf-8")

    df_pixels, _ = parse_raster_xml(xml_file)

    row = df_pixels.iloc[0]
    # offset=1000ms, duration=2000ms, no timeShift (defaults to 0.0)
    # t_start = 1000 / 1000 = 1.0s
    # t_end   = 1.0 + 2.0 = 3.0s
    assert row["t_start"] == pytest.approx(1.0)
    assert row["t_end"] == pytest.approx(3.0)


@pytest.mark.parametrize("path_type", ["path_obj", "str_obj"])
def test_parse_raster_xml_accepts_str_and_path(tmp_path: Path, path_type: str):
    """Verifies that the function accepts both pathlib.Path and string file paths."""
    xml_file = tmp_path / "test_path.xml"
    xml_file.write_text(NON_NAMESPACED_XML_CONTENT, encoding="utf-8")

    input_path = xml_file if path_type == "path_obj" else str(xml_file)
    df_pixels, metadata = parse_raster_xml(input_path)

    assert metadata["grid_width"] == 5
    assert len(df_pixels) == 1


def test_parse_raster_xml_no_pixels(tmp_path: Path):
    """Tests behavior when the <image> element contains no <pixel> children."""
    xml_file = tmp_path / "no_pixels.xml"
    xml_file.write_text(NO_PIXELS_XML_CONTENT, encoding="utf-8")

    df_pixels, metadata = parse_raster_xml(xml_file)

    assert metadata["grid_width"] == 4
    assert metadata["grid_height"] == 4
    assert isinstance(df_pixels, pd.DataFrame)
    assert df_pixels.empty


def test_parse_raster_xml_invalid_file_raises_error(tmp_path: Path):
    """Tests that FileNotFoundError and ET.ParseError propagate correctly."""
    non_existent = tmp_path / "does_not_exist.xml"
    with pytest.raises(FileNotFoundError):
        parse_raster_xml(non_existent)

    malformed = tmp_path / "malformed.xml"
    malformed.write_text("<raster><unclosed_tag>", encoding="utf-8")
    with pytest.raises(Exception):
        parse_raster_xml(malformed)