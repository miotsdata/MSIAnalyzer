import logging
from pathlib import Path
import xml.etree.ElementTree as ET
from datetime import datetime

import pandas as pd

from msianalyzer.core.utils.logging_utils import log_call

logger = logging.getLogger(__name__)

_NAMESPACE = "{http://www.apmaldi.com/target_raster-1.0.0}"

#: Raw `units="..."` values this raster XML format is known to use,
#: normalized to `core.calibration.units.PIXEL_SIZE_UNITS` ("um"/"mm") —
#: unrecognized/missing values fall back to `None` (the run's own targeted
#: `analysis_type` already forces the user to enter a pixel size by hand in
#: that case, same as before this metadata existed at all; this is a
#: default to *offer*, never a hard requirement on the XML's shape).
_PIXEL_SIZE_UNIT_ALIASES = {
    "mm": "mm",
    "millimeter": "mm",
    "millimeters": "mm",
    "um": "um",
    "µm": "um",
    "micron": "um",
    "microns": "um",
}


def _find_local(parent: ET.Element, tag: str) -> ET.Element | None:
    """`parent.find(tag)`, trying the raster XML's own namespace first and
    falling back to a bare tag name — the same "namespace missing or
    different" tolerance `parse_raster_xml`'s own `<image>` lookup already
    uses, factored out so `<rasterConfig>`/`<spacingX>`/`<spacingY>` can
    reuse it instead of repeating the same two-step fallback three times.
    """
    found = parent.find(_NAMESPACE + tag)
    return found if found is not None else parent.find(tag)


def _normalize_pixel_size_unit(raw: str | None) -> str | None:
    if raw is None:
        return None
    return _PIXEL_SIZE_UNIT_ALIASES.get(raw.strip().lower())


# -------------------------------------------------------------------------
# 1. Parse Raster XML into Spatial Pixel DataFrame
# -------------------------------------------------------------------------
@log_call(source="xml_path")
def parse_raster_xml(
    xml_path: str | Path,
) -> tuple[pd.DataFrame, dict[str, float | int | str | None]]:
    """Parse an AP-MALDI target raster XML file into pixel time windows.

    Args:
        xml_path: Path to the raster XML file.

    Returns:
        A ``(df_pixels, metadata)`` tuple. ``df_pixels`` has columns
        ``x``, ``y``, ``t_start``, ``t_end``, ``abs_start_sec`` and
        ``abs_end_sec``; ``metadata`` holds ``grid_width``, ``grid_height``,
        ``raster_start_sec``, and — when the XML has a ``<rasterConfig>``
        section with recognized units — ``pixel_size_x``, ``pixel_size_y``
        (the raster's own X/Y step, `None` if absent) and
        ``pixel_size_unit`` (``"um"``/``"mm"``, `None` if absent or
        unrecognized). A targeted analysis' Calibration workspace uses
        these as the pixel-size field's *default*, still overridable by
        the user (and, when they're `None`, exactly the "user must define
        it themselves" behavior this already had).
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()

    # Extract start time (ISO 8601, trailing "Z" for UTC).
    start_time_str = root.attrib.get("startTime")
    if not start_time_str:
        raise ValueError(f"{xml_path}: raster XML has no 'startTime' attribute")
    dt = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
    start_timestamp_sec = dt.timestamp()

    image_node = root.find("{http://www.apmaldi.com/target_raster-1.0.0}image")
    if image_node is None:
        # Fallback if the namespace is missing or different.
        image_node = root.find("image")
    if image_node is None:
        raise ValueError(f"{xml_path}: raster XML has no <image> element")

    grid_width = int(image_node.attrib["width"])
    grid_height = int(image_node.attrib["height"])
    time_shift = float(image_node.attrib.get("timeShift", 0.0))

    pixel_data = []

    # Iterate pixel tags
    for elem in image_node:
        if elem.tag.endswith("pixel"):
            x = int(elem.attrib["x"])
            y = int(elem.attrib["y"])
            offset_ms = float(elem.attrib["offset"])
            duration_ms = float(elem.attrib["duration"])

            # Compute start and end times in seconds from raster start
            t_start = (offset_ms + time_shift) / 1000.0
            t_end = t_start + (duration_ms / 1000.0)

            pixel_data.append(
                {
                    "x": x,
                    "y": y,
                    "t_start": t_start,
                    "t_end": t_end,
                    "abs_start_sec": start_timestamp_sec + t_start,
                    "abs_end_sec": start_timestamp_sec + t_end,
                }
            )

    df_pixels = pd.DataFrame(pixel_data)

    # <rasterConfig><spacingX units="mm">0.05</spacingX>... — the
    # acquisition's own raster step, when this XML format's <rasterConfig>
    # section is present at all (older/other formats may not have one).
    pixel_size_x = pixel_size_y = pixel_size_unit = None
    raster_config = _find_local(root, "rasterConfig")
    if raster_config is not None:
        spacing_x = _find_local(raster_config, "spacingX")
        spacing_y = _find_local(raster_config, "spacingY")
        if spacing_x is not None and spacing_x.text:
            pixel_size_x = float(spacing_x.text)
            pixel_size_unit = _normalize_pixel_size_unit(spacing_x.attrib.get("units"))
        if spacing_y is not None and spacing_y.text:
            pixel_size_y = float(spacing_y.text)
            if pixel_size_unit is None:
                pixel_size_unit = _normalize_pixel_size_unit(spacing_y.attrib.get("units"))

    metadata = {
        "grid_width": grid_width,
        "grid_height": grid_height,
        "raster_start_sec": start_timestamp_sec,
        "pixel_size_x": pixel_size_x,
        "pixel_size_y": pixel_size_y,
        "pixel_size_unit": pixel_size_unit,
    }

    return df_pixels, metadata
