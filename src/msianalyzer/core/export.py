"""Export menu backing functions (Annotation / Integration / Image).

One module for all three, not split across `core/annotation/` (where
only the Annotation export would conceptually belong) — Integration and
Image both operate over every feature/sample in the analysis, not
anything annotation-specific, so grouping the three together as "what the
Export menu does" reads more honestly than scattering them by superficial
topic.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path

import anndata as ad
import matplotlib.pyplot as plt
import numpy as np

from .analysis_db import (
    load_feature_ids_and_mzs,
    load_feature_representative_annotations,
    load_samples,
)
from .plotting.heatmap import (
    _feature_column_index,
    _layer_column,
    is_numeric_obs_column,
    obs_categories,
    render_heatmap_figure,
)
from .plotting.roi import load_sample_rois

logger = logging.getLogger(__name__)

#: Column order in the exported file, and the DataFrame column each comes
#: from (see `load_feature_representative_annotations`'s own docstring for
#: what's populated per tier). `feature_id`/`mz` are the only numeric
#: columns — every other field is free text, some of it library-supplied
#: and never guaranteed comma-free (a compound name, in particular).
_ANNOTATION_EXPORT_COLUMNS = [
    "feature_id",
    "mz",
    "compound_name",
    "compound_formula",
    "adduct",
    "inchikey",
    "cas",
    "hmdb",
    "library_name",
    "source",
]

_NUMERIC_ANNOTATION_EXPORT_COLUMNS = {"feature_id", "mz"}


def _csv_dialect_for(dest_path: Path) -> str:
    """`"\\t"` for `.txt`, `","` for anything else (`.csv` included) —
    the file extension the user picked in the save dialog is what decides
    the delimiter, not a separate format choice."""
    return "\t" if dest_path.suffix.lower() == ".txt" else ","


def export_annotation_table(db_path: str | Path, dest_path: str | Path) -> None:
    """Write one row per feature — its representative identity, across
    every tier, plus a row for a feature with no identity at all — to
    ``dest_path`` as CSV or tab-delimited text.

    Args:
        db_path: The analysis' SQLite database.
        dest_path: Where to write the export. ``.txt`` -> tab-delimited,
            anything else (``.csv`` included) -> comma-delimited.

    Every string field is quoted (``"..."``) regardless of whether it
    strictly needs to be — a compound name is free text from a library
    file and isn't guaranteed comma-free, so this isn't optional the way
    `csv`'s own default (quote only when the delimiter/quote character/a
    newline is actually present) would make it. `feature_id`/`mz` are the
    only fields written unquoted.
    """
    dest_path = Path(dest_path)
    df = load_feature_representative_annotations(db_path, include_unidentified=True)

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(dest_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(
            f, delimiter=_csv_dialect_for(dest_path), quoting=csv.QUOTE_NONNUMERIC
        )
        writer.writerow(_ANNOTATION_EXPORT_COLUMNS)
        for record in df.to_dict("records"):
            writer.writerow(
                [
                    _numeric_or_blank(record.get(col))
                    if col in _NUMERIC_ANNOTATION_EXPORT_COLUMNS
                    else _text_or_blank(record.get(col))
                    for col in _ANNOTATION_EXPORT_COLUMNS
                ]
            )
    logger.info("exported annotation table (%d features) to %s", len(df), dest_path)


def _numeric_or_blank(value):
    """`None`/`NaN` -> `0` (still numeric, so `QUOTE_NONNUMERIC` leaves it
    unquoted) — `feature_id`/`mz` are never actually missing in practice
    (every row comes from the `features` table itself), this is only a
    defensive fallback, not a real case."""
    if value is None or (isinstance(value, float) and value != value):  # NaN
        return 0
    return value


def _text_or_blank(value) -> str:
    """`None`/`NaN` -> `""`, everything else -> `str(value)` — so a
    missing field writes as an empty (still quoted) cell, not the literal
    text ``"None"``/``"nan"``."""
    if value is None or (isinstance(value, float) and value != value):  # NaN
        return ""
    return str(value)


def _sample_h5ad_path(db_path: str | Path, sample_name: str) -> Path:
    """Same convention as `AnalysisBridge._sample_h5ad_path`/
    `HeatmapImageProvider._load_adata` and `run.py`'s own writer — every
    sample's `.h5ad` lives next to the analysis database itself, named
    after the sample."""
    return Path(db_path).parent / f"{sample_name}.h5ad"


def export_integration_tables(
    db_path: str | Path,
    dest_folder: str | Path,
    layer: str,
    file_format: str = "csv",
) -> int:
    """Write one pixel x feature intensity matrix per sample into
    `dest_folder` — `<sample_name>_integration.<ext>`, header `x, y,
    <mz_1>, <mz_2>, ...` (every analysis feature, m/z to 4 decimals,
    matching every other m/z display in this app), one row per pixel.

    No spatial aggregation: this is the full per-pixel matrix, not a
    per-sample summary row.

    Args:
        db_path: The analysis' SQLite database.
        dest_folder: Directory to write into (created if missing).
        layer: `"raw"` or `"TIC"` — which `adata` layer to read, same
            convention as `core.plotting.heatmap._layer_column`.
        file_format: `"csv"` (comma) or `"txt"` (tab) — chosen explicitly
            here rather than keyed off a destination filename's suffix
            (unlike `export_annotation_table`), since a folder picker
            never gives the user a filename to type an extension into.

    Returns:
        The number of sample files written. A sample with no `.h5ad` on
        disk is skipped, not an error — same convention as
        `HeatmapImageProvider._load_adata`.
    """
    dest_folder = Path(dest_folder)
    dest_folder.mkdir(parents=True, exist_ok=True)
    delimiter = "\t" if file_format.lower() == "txt" else ","
    ext = ".txt" if file_format.lower() == "txt" else ".csv"

    features = load_feature_ids_and_mzs(db_path)
    mz_values = features["mz"].to_numpy(dtype=float)
    header = ["x", "y"] + [f"{mz:.4f}" for mz in mz_values]

    samples = load_samples(db_path)
    written = 0
    for sample_name in samples["name"]:
        h5ad_path = _sample_h5ad_path(db_path, sample_name)
        if not h5ad_path.exists():
            logger.warning(
                "skipping %s in integration export: no h5ad at %s",
                sample_name,
                h5ad_path,
            )
            continue

        adata = ad.read_h5ad(h5ad_path)
        xy = np.asarray(adata.obsm["spatial"], dtype=float)
        columns = [
            _layer_column(adata, layer, _feature_column_index(adata, mz))
            for mz in mz_values
        ]

        dest_path = dest_folder / f"{sample_name}_integration{ext}"
        with open(dest_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, delimiter=delimiter, quoting=csv.QUOTE_NONNUMERIC)
            writer.writerow(header)
            for row_idx in range(xy.shape[0]):
                writer.writerow(
                    [xy[row_idx, 0], xy[row_idx, 1]]
                    + [column[row_idx] for column in columns]
                )
        written += 1

    logger.info(
        "exported %d integration table(s) to %s", written, dest_folder
    )
    return written


def export_visual_inspection_images(
    db_path: str | Path,
    dest_folder: str | Path,
    image_format: str,
    mode: str,
    *,
    mz: float | None = None,
    obs_column: str | None = None,
    layer: str = "TIC",
    colormap: str = "viridis",
    vmin: float | None = None,
    vmax: float | None = None,
    show_rois: bool = False,
) -> int:
    """Write `<dest_folder>/<sample_name>.<image_format>` for every
    sample — the same raster + colorbar/legend `render_heatmap_figure`
    builds, matching whatever Visual Inspection is currently showing
    (every argument here is meant to be read straight from
    `HeatmapControlsPanel`'s own live property values, not re-derived).

    Args:
        db_path: The analysis' SQLite database.
        dest_folder: Directory to write into (created if missing).
        image_format: `"png"`, `"pdf"`, or `"svg"` — Matplotlib's
            `savefig` picks the writer purely from the extension.
        mode: `"feature"` or `"obs"` — whether `mz` or `obs_column`
            drives the raster. For `"obs"`, whether a given sample's
            column is numeric or categorical is decided per sample
            (`is_numeric_obs_column`), same as the GUI's own tile
            requests — a category union across every sample (not just
            this one) is computed once up front so a category always
            gets the same color/legend entry everywhere, same as
            `HeatmapControlsPanel.obsCategoryLegend`'s own convention.
        mz: Required for `mode="feature"`.
        obs_column: Required for `mode="obs"`.
        layer: `"raw"` or `"TIC"` — `mode="feature"` only.
        colormap: Any registered Matplotlib colormap name.
        vmin: Lower color-scale bound. `None` autoscales per sample
            (matching `HeatmapControlsPanel.vminToken()`'s own `"auto"`
            convention once translated to `None` by the caller).
        vmax: Upper color-scale bound. `None` autoscales per sample.
        show_rois: Draw each sample's own saved ROI polygons on top,
            matching Visual Inspection's "Show ROIs" checkbox.

    Returns:
        The number of sample images written. A sample with no `.h5ad`
        on disk is skipped, not an error.
    """
    dest_folder = Path(dest_folder)
    dest_folder.mkdir(parents=True, exist_ok=True)
    ext = image_format.lower().lstrip(".")

    samples = load_samples(db_path)
    loaded: dict[str, ad.AnnData] = {}
    for sample_name in samples["name"]:
        h5ad_path = _sample_h5ad_path(db_path, sample_name)
        if not h5ad_path.exists():
            logger.warning(
                "skipping %s in image export: no h5ad at %s", sample_name, h5ad_path
            )
            continue
        loaded[sample_name] = ad.read_h5ad(h5ad_path)

    categories: list[str] | None = None
    if mode == "obs" and obs_column:
        union: set[str] = set()
        for adata in loaded.values():
            if obs_column in adata.obs.columns and not is_numeric_obs_column(
                adata, obs_column
            ):
                union |= set(obs_categories(adata, obs_column))
        categories = sorted(union) if union else None

    written = 0
    for sample_name, adata in loaded.items():
        if mode == "feature":
            render_mode = "feature"
        elif obs_column and is_numeric_obs_column(adata, obs_column):
            render_mode = "obs_numeric"
        else:
            render_mode = "obs_categorical"

        rois = (
            load_sample_rois(_sample_h5ad_path(db_path, sample_name))
            if show_rois
            else None
        )

        fig = render_heatmap_figure(
            adata,
            mode=render_mode,
            mz=mz,
            obs_column=obs_column,
            categories=categories,
            layer=layer,
            colormap=colormap,
            vmin=vmin,
            vmax=vmax,
            rois=rois,
            title=sample_name,
        )
        dest_path = dest_folder / f"{sample_name}.{ext}"
        fig.savefig(dest_path, bbox_inches="tight")
        plt.close(fig)
        written += 1

    logger.info(
        "exported %d visual inspection image(s) to %s", written, dest_folder
    )
    return written
