import xml.etree.ElementTree as ET
from datetime import datetime

import pandas as pd


# -------------------------------------------------------------------------
# 1. Parse Raster XML into Spatial Pixel DataFrame
# -------------------------------------------------------------------------
def parse_raster_xml(xml_path: str) -> tuple[pd.DataFrame, dict[str, float | int]]:
    """
    Parses AP-MALDI target raster XML file and returns pixel time windows
    along with global metadata (grid dimensions, start time).
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()

    # Extract start time (ISO format)
    start_time_str = root.attrib.get("startTime")
    # Parse UTC timestamp
    dt = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
    start_timestamp_sec = dt.timestamp()

    image_node = root.find("{http://www.apmaldi.com/target_raster-1.0.0}image")
    if image_node is None:
        # Fallback if namespace is missing/different
        image_node = root.find("image")

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

    metadata = {
        "grid_width": grid_width,
        "grid_height": grid_height,
        "raster_start_sec": start_timestamp_sec,
    }

    return df_pixels, metadata
