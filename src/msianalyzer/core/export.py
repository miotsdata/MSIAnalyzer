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
import sqlite3
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from .analysis_db import (
    connect,
    load_calibration_curve,
    load_calibration_points,
    load_calibration_unknowns,
    load_feature_ids_and_mzs,
    load_feature_representative_annotations,
    load_obs_category_colors,
    load_samples,
    load_scale_bar,
)
from .plotting.heatmap import (
    _feature_column_index,
    _layer_column,
    category_colors,
    is_numeric_obs_column,
    obs_categories,
    render_heatmap_figure,
    render_overlay_figure,
    scale_bar_spec,
)
from .plotting.feature_overlay import parse_channels
from .plotting.light_sample import LightSample
from .plotting.roi import load_sample_rois

from msianalyzer.core.utils.lazy_import import lazy_module

ad = lazy_module("anndata")

logger = logging.getLogger(__name__)

#: Column order in the exported file (see
#: `load_feature_representative_annotations`'s own docstring for what's
#: populated per tier). `feature_id`/`mz`/`score` are the only numeric
#: columns — every other field is free text, some of it library-supplied
#: and never guaranteed comma-free (a compound name, in particular).
#: `score` is the MS2 representative's spectral score, blank for a
#: target-list/predicted/unidentified row (none of those has one).
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
    "score",
    "ms2_annotation_outcome",
    "precursor_name",
    "precursor_ppm",
    "n_precursor_candidates",
]

#: Export column -> DataFrame column, where the two names differ.
_ANNOTATION_EXPORT_SOURCE_COLUMNS = {"score": "best_score"}

_NUMERIC_ANNOTATION_EXPORT_COLUMNS = {"feature_id", "mz", "n_precursor_candidates"}

#: Numeric but only set for some rows: a missing value is an empty cell.
_OPTIONAL_NUMERIC_ANNOTATION_EXPORT_COLUMNS = {"score", "precursor_ppm"}


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
    newline is actually present) would make it. `feature_id`/`mz`/`score`
    are the only fields written unquoted (a missing `score` is an empty
    cell).
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
                    _export_annotation_cell(col, record)
                    for col in _ANNOTATION_EXPORT_COLUMNS
                ]
            )
    logger.info("exported annotation table (%d features) to %s", len(df), dest_path)


#: `.var` columns `write_annotations_to_var` owns: the Annotation export's
#: columns minus `mz`, which `.var` already has (and everything reads).
VAR_ANNOTATION_COLUMNS = [c for c in _ANNOTATION_EXPORT_COLUMNS if c != "mz"]


def annotation_var_table(db_path: str | Path, var: pd.DataFrame) -> pd.DataFrame:
    """The Annotation export's rows, one per `.var` row, indexed and
    ordered like `var` (so `.X` columns stay aligned). Joined on the
    exact m/z: `.var["mz"]` is copied verbatim from `features.mz`. A
    `.var` column with no feature (a targeted run's reference m/z) gets
    `feature_id` -1, blank text and a `NaN` score."""
    from msianalyzer.core.spectra.isotopes import _column_keys

    df = load_feature_representative_annotations(db_path, include_unidentified=True)
    records = {
        k: {c: _export_annotation_cell(c, r) for c in VAR_ANNOTATION_COLUMNS}
        for k, r in zip(_column_keys(df["mz"]), df.to_dict("records"))
    }
    blank = {c: "" for c in VAR_ANNOTATION_COLUMNS} | {
        "feature_id": -1, "score": None, "precursor_ppm": None, "n_precursor_candidates": 0,
    }
    out = pd.DataFrame(
        [records.get(k, blank) for k in _column_keys(var["mz"])],
        index=var.index,
        columns=VAR_ANNOTATION_COLUMNS,
    )
    out["feature_id"] = out["feature_id"].astype(np.int64)
    out["n_precursor_candidates"] = out["n_precursor_candidates"].astype(np.int64)
    out["score"] = out["score"].astype(float)
    out["precursor_ppm"] = out["precursor_ppm"].astype(float)
    return out


#: `.uns` key of every precursor-m/z candidate (ADR 0065), one row each.
PRECURSOR_UNS_KEY = "precursor_candidates"


def precursor_uns_table(db_path: str | Path, var: pd.DataFrame) -> pd.DataFrame:
    """Every precursor candidate of the features in `var`, with `feature`
    the feature's `.var_names` entry: ``feature, rank, tie_rank, name,
    formula, adduct, mz_ref, ppm, library`` (`tie_rank`: candidates within
    the feature's m/z spread across samples share it). Empty when there
    are none."""
    from msianalyzer.core.spectra.isotopes import _column_keys

    with connect(db_path) as con:
        try:
            df = pd.read_sql_query(
                "SELECT f.mz, p.rank, COALESCE(p.tie_rank, p.rank) AS tie_rank, p.compound_name AS name, p.compound_formula AS formula, "
                "p.adduct, p.ref_mz AS mz_ref, p.ppm_error AS ppm, l.name AS library "
                "FROM precursor_annotations p JOIN features f ON f.feature_id = p.feature_id "
                "LEFT JOIN annotation_libraries l ON l.id = p.library_id "
                "ORDER BY p.feature_id, p.rank",
                con,
            )
        except (pd.errors.DatabaseError, sqlite3.OperationalError):
            return pd.DataFrame()
    names = dict(zip(_column_keys(var["mz"]), var.index.astype(str)))
    df.insert(0, "feature", [names.get(k) for k in _column_keys(df["mz"])])
    df = df[df["feature"].notna()].drop(columns="mz").reset_index(drop=True)
    for col in ("feature", "name", "formula", "adduct", "library"):
        df[col] = df[col].fillna("").astype(str)
    return df


def write_annotations_to_var(db_path: str | Path, h5ad_paths) -> None:
    """Write the Annotation export into each `.h5ad`'s `.var`, and every
    precursor-m/z candidate into `.uns["precursor_candidates"]` (ADR 0065),
    in place (only those elements are rewritten, ADR 50). Replaces any earlier
    copy, so it's called again whenever an annotation changes after the
    run. Missing files are skipped."""
    from msianalyzer.core.utils import h5ad_io

    for path in h5ad_paths:
        if not Path(path).exists():
            continue
        with h5ad_io.open_for_write(path) as f:
            var = ad.io.read_elem(f["var"])
            var = var.drop(columns=[c for c in VAR_ANNOTATION_COLUMNS if c in var])
            var = var.join(annotation_var_table(db_path, var))
            # Written aside, then swapped in: a reader on another thread
            # sees the old or the new `var`, never none.
            if "_var_new" in f:
                del f["_var_new"]
            ad.io.write_elem(f, "_var_new", var)
            del f["var"]
            f.move("_var_new", "var")
            uns = f.require_group("uns")
            if PRECURSOR_UNS_KEY in uns:
                del uns[PRECURSOR_UNS_KEY]
            candidates = precursor_uns_table(db_path, var)
            if not candidates.empty:
                ad.io.write_elem(uns, PRECURSOR_UNS_KEY, candidates)
        logger.debug("%s: wrote annotations into .var", path)


def var_has_annotations(h5ad_path: str | Path) -> bool:
    """Whether `write_annotations_to_var` already ran on this file."""
    import h5py

    with h5py.File(h5ad_path, "r") as f:
        order = f["var"].attrs.get("column-order", [])
        return "feature_id" in [c.decode() if isinstance(c, bytes) else c for c in order]


def _export_annotation_cell(col: str, record: dict):
    """One cell of the Annotation export. `score` is numeric but genuinely
    optional (only the ms2 tier has one), so a missing score is written
    as an empty cell rather than `_numeric_or_blank`'s `0` — a `0` would
    read as a real (terrible) score."""
    value = record.get(_ANNOTATION_EXPORT_SOURCE_COLUMNS.get(col, col))
    if col in _OPTIONAL_NUMERIC_ANNOTATION_EXPORT_COLUMNS:
        if value is None or (isinstance(value, float) and value != value):  # NaN
            return None
        return float(value)
    if col in _NUMERIC_ANNOTATION_EXPORT_COLUMNS:
        return _numeric_or_blank(value)
    return _text_or_blank(value)


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


def _sample_h5ad_path(
    db_path: str | Path, sample_name: str, rel_dir: str | None = None
) -> Path:
    """`core.imported_dataset.sample_h5ad_path` — the one resolver
    `AnalysisBridge`/`HeatmapImageProvider` share: next to the analysis
    database (`run.py`'s own writer), or inside an imported dataset's
    folder when `rel_dir` is given (ADR 49)."""
    from msianalyzer.core.imported_dataset import sample_h5ad_path

    return sample_h5ad_path(db_path, sample_name, rel_dir)


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

        # Only the one layer: a full read also loads `.X`, every other
        # layer and `localMoranI`, GBs per sample over a share (ADR 50).
        light = LightSample(h5ad_path)
        light.ensure_layer(layer)
        adata = light.adata
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

    logger.info("exported %d integration table(s) to %s", written, dest_folder)
    return written


def _existing_sample_h5ads(db_path: str | Path, rel_dir: str | None) -> dict[str, Path]:
    """`{sample_name: .h5ad path}` for every sample whose file exists; a
    missing one is skipped (logged), not an error."""
    paths: dict[str, Path] = {}
    for sample_name in load_samples(db_path)["name"]:
        h5ad_path = _sample_h5ad_path(db_path, sample_name, rel_dir)
        if not h5ad_path.exists():
            logger.warning(
                "skipping %s in image export: no h5ad at %s", sample_name, h5ad_path
            )
            continue
        paths[sample_name] = h5ad_path
    return paths


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
    rel_dir: str | None = None,
    include_scale_bar: bool = False,
    hidden_categories: list[str] | None = None,
    progress: Callable[[int, int], None] | None = None,
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
        rel_dir: Export an imported dataset's files (ADR 49) instead of
            the pipeline output — whichever Visual Inspection is showing.
            Samples the dataset doesn't cover are skipped.
        include_scale_bar: Draw the analysis' scale bar (ADR 53) on
            every sample that has a pixel size set, even while it's
            switched off in Visual Inspection; the others are exported
            without one (logged).
        hidden_categories: Categories switched off in Visual Inspection,
            exported black and left out of the legend (the export dialog's
            "Only the categories shown"). `None` exports every category.
        progress: Called with `(done, total)` after each sample.

    Each sample is read as a `LightSample` (only the layer or obs column
    drawn) and dropped once saved: a full `ad.read_h5ad` of every sample
    held `.X`, every layer and `localMoranI` in memory at once, minutes
    and tens of GB on a real project.

    Returns:
        The number of sample images written. A sample with no `.h5ad`
        on disk is skipped, not an error.
    """
    dest_folder = Path(dest_folder)
    dest_folder.mkdir(parents=True, exist_ok=True)
    ext = image_format.lower().lstrip(".")

    paths = _existing_sample_h5ads(db_path, rel_dir)

    def _load(path: Path) -> ad.AnnData:
        light = LightSample(path)
        if mode == "feature":
            light.ensure_layer(layer)
        elif obs_column:
            light.ensure_obs(obs_column)
        return light.adata

    logger.info("exporting %d visual inspection image(s) to %s", len(paths), dest_folder)
    categories: list[str] | None = None
    if mode == "obs" and obs_column:
        union: set[str] = set()
        for path in paths.values():
            adata = _load(path)
            if obs_column in adata.obs.columns and not is_numeric_obs_column(
                adata, obs_column
            ):
                union |= set(obs_categories(adata, obs_column))
        categories = sorted(union) if union else None
    colors = (
        category_colors(categories, load_obs_category_colors(db_path, obs_column))
        if categories else None
    )

    # The export's own checkbox decides, whatever the GUI's on/off switch says.
    scale_bar_settings = (
        {**load_scale_bar(db_path), "enabled": True} if include_scale_bar else None
    )

    written = 0
    for sample_name, path in paths.items():
        adata = _load(path)
        scale_bar = None
        if scale_bar_settings is not None:
            scale_bar = scale_bar_spec(scale_bar_settings, sample_name)
            if scale_bar is None:
                logger.warning("no scale bar on %s: no pixel size set", sample_name)

        if mode == "feature":
            render_mode = "feature"
        elif obs_column and is_numeric_obs_column(adata, obs_column):
            render_mode = "obs_numeric"
        else:
            render_mode = "obs_categorical"

        rois = (
            load_sample_rois(_sample_h5ad_path(db_path, sample_name, rel_dir))
            if show_rois
            else None
        )

        fig = render_heatmap_figure(
            adata,
            mode=render_mode,
            mz=mz,
            obs_column=obs_column,
            categories=categories,
            colors=colors,
            layer=layer,
            colormap=colormap,
            vmin=vmin,
            vmax=vmax,
            rois=rois,
            title=sample_name,
            scale_bar=scale_bar,
            hidden_categories=set(hidden_categories or ()),
        )
        dest_path = dest_folder / f"{sample_name}.{ext}"
        fig.savefig(dest_path, bbox_inches="tight")
        written += 1
        if progress is not None:
            progress(written, len(paths))

    logger.info("exported %d visual inspection image(s) to %s", written, dest_folder)
    return written


def export_overlay_images(
    db_path: str | Path,
    dest_folder: str | Path,
    image_format: str,
    channels: str,
    labels: list[str],
    *,
    layer: str = "TIC",
    mode: str = "add",
    show_rois: bool = False,
    rel_dir: str | None = None,
    include_scale_bar: bool = False,
    progress: Callable[[int, int], None] | None = None,
) -> int:
    """Write `<dest_folder>/<sample_name>_overlay.<image_format>` for every
    sample: the Overlay tab's image (ADR 67) with a legend of its channels.

    Args:
        channels: The tile id's channels part (`feature_overlay.parse_channels`),
            visible channels only, exactly as the tab renders them.
        labels: One legend label per channel, same order.
        layer, mode: As in the tab.
        show_rois, rel_dir, include_scale_bar, progress: As in
            `export_visual_inspection_images`.

    Returns:
        The number of sample images written.
    """
    dest_folder = Path(dest_folder)
    dest_folder.mkdir(parents=True, exist_ok=True)
    ext = image_format.lower().lstrip(".")
    parsed = parse_channels(channels)
    paths = _existing_sample_h5ads(db_path, rel_dir)
    scale_bar_settings = (
        {**load_scale_bar(db_path), "enabled": True} if include_scale_bar else None
    )

    written = 0
    for sample_name, path in paths.items():
        light = LightSample(path)
        light.ensure_layer(layer)
        scale_bar = None
        if scale_bar_settings is not None:
            scale_bar = scale_bar_spec(scale_bar_settings, sample_name)
            if scale_bar is None:
                logger.warning("no scale bar on %s: no pixel size set", sample_name)
        fig = render_overlay_figure(
            light.adata,
            parsed,
            labels,
            layer=layer,
            mode=mode,
            rois=load_sample_rois(path) if show_rois else None,
            title=sample_name,
            scale_bar=scale_bar,
        )
        fig.savefig(dest_folder / f"{sample_name}_overlay.{ext}", bbox_inches="tight")
        written += 1
        if progress is not None:
            progress(written, len(paths))

    logger.info("exported %d overlay image(s) to %s", written, dest_folder)
    return written


def export_calibration_results(db_path: str | Path, dest_folder: str | Path) -> int:
    """Write a targeted analysis' calibration results to `dest_folder`:
    `calibration_standards.csv` (every standard point), `calibration_curve.csv`
    (the current fit's parameters/statistics), and `calibration_unknowns.csv`
    (every quantified ROI result).

    No free-text/comma-risk fields here (unlike `export_annotation_table`'s
    library-supplied compound names) — a plain `DataFrame.to_csv` is enough,
    no hand-rolled `csv.writer` quoting needed.

    Args:
        db_path: The analysis' SQLite database.
        dest_folder: Destination folder, created if it doesn't exist.

    Returns:
        The number of files actually written (0-3) — a table that doesn't
        exist yet (no standards added, or no curve fit yet) is simply
        skipped, not an error.
    """
    dest_folder = Path(dest_folder)
    dest_folder.mkdir(parents=True, exist_ok=True)
    written = 0

    points_df = load_calibration_points(db_path)
    if not points_df.empty:
        points_df.to_csv(dest_folder / "calibration_standards.csv", index=False)
        written += 1

    curve = load_calibration_curve(db_path)
    if curve is not None:
        pd.DataFrame([curve]).to_csv(dest_folder / "calibration_curve.csv", index=False)
        written += 1

    unknowns_df = load_calibration_unknowns(db_path)
    if not unknowns_df.empty:
        unknowns_df.to_csv(dest_folder / "calibration_unknowns.csv", index=False)
        written += 1

    logger.info("exported calibration results (%d file(s)) to %s", written, dest_folder)
    return written
