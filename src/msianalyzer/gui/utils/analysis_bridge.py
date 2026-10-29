from __future__ import annotations

import base64
import functools
import json
import logging
import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import h5py
import numpy as np
import pandas as pd
from PySide6.QtCore import Property, QObject, Signal, Slot

from msianalyzer.core import analysis_db
from msianalyzer.core import export as core_export
from msianalyzer.core import imported_dataset
from msianalyzer.core.parser import load_raw_metadata_value
from msianalyzer.core.annotation import reannotation, target_list
from msianalyzer.core.annotation.formula_prediction import FormulaPredictionSettings
from msianalyzer.core.calibration.curve_fit import (
    fit_calibration_curve,
    interpolate_concentration,
)
from msianalyzer.core.calibration.roi_intensity import (
    roi_internal_standard_signal,
    roi_pixel_count,
)
from msianalyzer.core.calibration.units import (
    AMOUNT_UNITS,
    PIXEL_SIZE_UNITS,
    amount_from_concentration,
    area_conversion_factor,
    concentration_from_amount,
    concentration_unit as calibration_concentration_unit,
    pixel_area,
)
from msianalyzer.core.plotting import roi
from msianalyzer.core.plotting.heatmap import _RENDER_MZ_TOLERANCE_PPM, category_color
from msianalyzer.core.plotting.mirror_plot_raster import render_mirror_plot_png
from msianalyzer.core.plotting.plotter import (
    Plotter,
    _read_raw_ms2_scan,
    calibration_line_range,
    calibration_title,
    spectrum_groups,
)
from msianalyzer.core.registration import (
    attach_he_image,
    fit_and_save_registration,
    load_registration,
)
from msianalyzer.core.spectra.average_spectra import load_aggregated_spectra
from msianalyzer.core.utils import h5ad_io
from msianalyzer.core.utils import tic_normalization
from msianalyzer.gui.utils.dataset_task_worker import DatasetTaskWorker
from msianalyzer.gui.utils.export_worker import ExportWorker
from msianalyzer.gui.utils.formula_prediction_worker import FormulaPredictionWorker
from msianalyzer.gui.utils.he_image_provider import HEImageProvider
from msianalyzer.gui.utils.he_overlay_provider import HEOverlayImageProvider
from msianalyzer.gui.utils.heatmap_prefetch_worker import HeatmapPrefetchWorker
from msianalyzer.gui.utils.heatmap_provider import HeatmapImageProvider
from msianalyzer.gui.utils.heatmap_query_worker import HeatmapQueryWorker
from msianalyzer.gui.utils.local_moran_summary_model import LocalMoranSummaryModel
from msianalyzer.gui.utils.mirror_plot_worker import MirrorPlotWorker
from msianalyzer.gui.utils.spatial_correlation_model import SpatialCorrelationModel
from msianalyzer.gui.utils.config_schema import coerce_config_values
from msianalyzer.gui.utils.table_query_worker import TableQueryWorker
from msianalyzer.core.utils.lazy_import import lazy_module

ad = lazy_module("anndata")

logger = logging.getLogger(__name__)


_EMPTY_SUMMARY = {
    "n_samples": 0,
    "n_features": 0,
    "n_ms2_associated_features": 0,
    "annotation_ran": False,
    "n_annotated_features": 0,
    "n_distinct_compounds": 0,
    "outcomes_recorded": False,
    "n_outcome_no_ms2": 0,
    "n_outcome_annotated": 0,
    "n_outcome_no_shared_fragments": 0,
    "n_outcome_no_candidates": 0,
    "n_outcome_all_scans_skipped": 0,
    "ms2_background": [],
    "ms2_cleaning_ran": False,
    "precursor_ran": False,
    "n_precursor_matched_features": 0,
}

_EMPTY_ANNOTATION_METADATA = {
    "compound_name": "",
    "compound_formula": "",
    "inchikey": "",
    "library_name": "",
    "score": None,
    "dot_product_score": None,
    "coverage_score": None,
    "lib_coverage": None,
    "emp_coverage": None,
    "n_matched_peaks": None,
    "n_lib_peaks": None,
    "n_emp_peaks_raw": None,
    "n_emp_peaks_filtered": None,
    "scan_id": None,
    "precursor_mz": None,
    "fragment_ppm_tolerance": None,
}


def _dataframe_to_records(df: pd.DataFrame) -> list:
    """`df.to_dict("records")`, scrubbed for QML: numpy scalars -> native
    Python (`.item()`), NaN -> `None`. Pandas reads (`sqlite3`/`read_sql_query`)
    otherwise leak `numpy.int64`/`numpy.float64` into the records, which QML
    doesn't reliably marshal (e.g. `String(numpy_value)` can misbehave)."""
    records = []
    for row in df.to_dict("records"):
        clean = {}
        for key, value in row.items():
            if value is None or (isinstance(value, float) and pd.isna(value)):
                clean[key] = None
            elif hasattr(value, "item"):
                clean[key] = value.item()
            else:
                clean[key] = value
        records.append(clean)
    return records


#: The Annotations top-hits panel's Top-N maximum (its SpinBox `to`).
_MAX_TOP_N = 20


def _db_version(path: str) -> tuple:
    """Changes whenever the database is written (WAL writes land in -wal)."""
    return tuple(
        os.stat(p).st_mtime_ns if os.path.exists(p) else 0 for p in (path, path + "-wal")
    )


@functools.lru_cache(maxsize=4)
def _candidate_names(path: str, version: tuple) -> pd.DataFrame:
    """`load_feature_candidate_names`, cached per database version: it groups
    every MS2 annotation row (seconds on millions) and the Annotations
    table reloads on every visit."""
    return analysis_db.load_feature_candidate_names(path, limit=_MAX_TOP_N)


def _load_annotation_rows(analysis_db_path: str, include_unannotated: bool) -> pd.DataFrame:
    """The Annotations table's rows, each with its feature's best MS2 scan,
    annotation-outcome counts (ADR 0062) and candidate names, best first
    (`ms2_candidates`, `precursor_candidates`; `[]` without any)."""
    df = analysis_db.load_feature_representative_annotations(
        analysis_db_path, include_unidentified=include_unannotated
    )
    if df.empty:
        return df
    for extra in (
        analysis_db.load_feature_best_ms2_scans(analysis_db_path),
        analysis_db.load_feature_annotation_outcomes(analysis_db_path),
        # Searched by name too, up to the top-hits panel's Top-N.
        _candidate_names(analysis_db_path, _db_version(analysis_db_path)),
    ):
        if not extra.empty:
            df = df.merge(extra, on="feature_id", how="left")
    for column in ("ms2_candidates", "precursor_candidates"):
        df[column] = df[column].apply(lambda v: v if isinstance(v, list) else []) \
            if column in df else [[] for _ in range(len(df))]
    return df


def _render_best_scan_png(raw_db_path: str, scan_id: int) -> str:
    """One raw MS2 scan as the Annotations mirror plot with an empty
    library side, as a `data:` URI — a feature no library matched."""
    mz, intensity = _read_raw_ms2_scan(raw_db_path, scan_id)
    if mz is None:
        raise ValueError(f"MS2 scan {scan_id} not found in {raw_db_path!r}")
    empty = np.array([], dtype=float)
    png = render_mirror_plot_png(mz, intensity, empty, empty, 0.0)
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


_COMPOUND_NAME_MATCH_PPM = 10.0


def _nearest_compound_name(mz: float, feature_mzs: np.ndarray, feature_names: np.ndarray) -> str | None:
    """`feature_names[i]` for the `feature_mzs[i]` closest to `mz`, within
    `_COMPOUND_NAME_MATCH_PPM` — nearest rather than exact-equality since a
    SQLite REAL round-trip isn't guaranteed bit-exact against the float an
    `.h5ad`'s `var["mz"]` was built from (same reasoning as
    `heatmap._feature_column_index`), but capped by a tolerance so an mz
    with no real corresponding feature (shouldn't happen in production —
    `.h5ad` var mzs are always sourced from this same analysis' own
    `features` table — but is a real gap without a cap) doesn't silently
    borrow an unrelated feature's name just because it's the closest one
    around.
    """
    if feature_mzs.size == 0:
        return None
    idx = np.searchsorted(feature_mzs, mz)
    idx = np.clip(idx, 1, len(feature_mzs) - 1)
    left_closer = (mz - feature_mzs[idx - 1]) <= (feature_mzs[idx] - mz)
    idx = idx - 1 if left_closer else idx
    nearest_mz = feature_mzs[idx]
    if abs(nearest_mz - mz) > nearest_mz * _COMPOUND_NAME_MATCH_PPM * 1e-6:
        return None
    name = feature_names[idx]
    return None if pd.isna(name) else name


def _pivot_spatial_correlation_rows(
    flat_rows: list[dict], value_key: str = "moran_i"
) -> tuple[list[dict], list[str]]:
    """`_load_spatial_correlation_table`'s flat `(mz, sample, <value_key>,
    compound_name)` rows -> one dict per feature, that sample's value
    folded in as `row[sample_name]` — the shape
    `SpatialCorrelationModel.setRows` expects (one row per feature, one
    column per sample), and `sorted(set(...))` sample names for the
    column list. `value_key` defaults to `"moran_i"`, its only caller
    today.

    The local Moran's I table has its own, differently-shaped loader,
    `_load_local_moran_layer_data` — its rows hold raw per-pixel arrays,
    not scalars, so pivoting them isn't a fit for this function; it builds
    its own pivoted structure directly instead of reusing this one.
    """
    by_mz: dict[float, dict] = {}
    for r in flat_rows:
        row = by_mz.setdefault(r["mz"], {"mz": r["mz"], "compound_name": r["compound_name"]})
        row[r["sample"]] = r[value_key]
    samples = sorted({r["sample"] for r in flat_rows})
    return list(by_mz.values()), samples


def _inspection_samples_frame(analysis_db_path: str, rel_dir: str | None) -> pd.DataFrame:
    """The samples Visual Inspection / Spatial Correlation show: every
    analysis sample for the pipeline output, or only the samples an
    imported dataset (ADR 49, found by its `rel_dir`) actually covers —
    same `sample_id`/`name`/`polarity` shape as `analysis_db.load_samples`.
    """
    samples = analysis_db.load_samples(analysis_db_path)
    if not rel_dir or samples.empty:
        return samples
    datasets = analysis_db.load_imported_datasets(analysis_db_path)
    match = datasets[datasets["rel_dir"] == rel_dir] if not datasets.empty else datasets
    if match.empty:
        return samples.iloc[0:0]
    info = analysis_db.load_imported_dataset(analysis_db_path, int(match.iloc[0]["id"]))
    names = {s["name"] for s in (info or {}).get("samples", [])}
    return samples[samples["name"].isin(names)].reset_index(drop=True)


def _load_dataset_feature_list(analysis_db_path: str, rel_dir: str) -> pd.DataFrame:
    """Visual Inspection's feature list for an imported dataset (ADR 49):
    the features actually present in its files (a notebook may have
    filtered some out), read from one sample's `var` alone (h5py), each
    joined to the analysis feature within
    `heatmap._RENDER_MZ_TOLERANCE_PPM` to keep its `feature_id` and
    representative `compound_name`. A var with no matching analysis
    feature keeps its m/z with `feature_id`/`compound_name` `None`.

    Returns:
        `feature_id`, `mz`, `compound_name`, ordered by `mz` (the
        `analysis_db.load_feature_list` shape).
    """
    columns = ["feature_id", "mz", "compound_name"]
    samples = _inspection_samples_frame(analysis_db_path, rel_dir)
    var_mz = None
    for name in samples.get("name", []):
        path = AnalysisBridge._sample_h5ad_path(analysis_db_path, name, rel_dir)
        if not path.exists():
            continue
        with h5py.File(path, "r") as f:
            var_mz = ad.io.read_elem(f["var"])["mz"].to_numpy(dtype=float)
        break
    if var_mz is None or var_mz.size == 0:
        return pd.DataFrame(columns=columns)

    features = analysis_db.load_feature_list(analysis_db_path)
    rows = []
    for mz in np.sort(var_mz):
        feature_id, name = None, None
        if not features.empty:
            diffs = np.abs(features["mz"].to_numpy(dtype=float) - mz)
            i = int(np.argmin(diffs))
            if diffs[i] <= abs(mz) * _RENDER_MZ_TOLERANCE_PPM * 1e-6:
                feature_id = int(features.iloc[i]["feature_id"])
                name = features.iloc[i]["compound_name"]
        rows.append({"feature_id": feature_id, "mz": float(mz), "compound_name": name})
    return pd.DataFrame(rows, columns=columns)


def _load_spatial_correlation_table(
    analysis_db_path: str, rel_dir: str | None = None
) -> pd.DataFrame:
    """One row per (feature, sample) with that sample's Moran's I for that
    feature, plus its representative compound name (if annotated) — the
    flat shape `SpatialCorrelationSection.qml` pivots client-side into the
    full table (same "flat rows in, QML pivots" shape
    `AnnotationsSection.qml` already uses for its own table).

    Reads each sample's own `.h5ad` (`AnalysisBridge._sample_h5ad_path`'s
    convention) rather than a SQL table — `core.spatial.moran` writes
    Moran's I straight into `adata.uns["moranI"]` and nowhere else (see
    ADR 48), so the Moran's I values themselves are purely an `.h5ad`
    read; `compound_name` comes from `analysis_db.load_feature_list`, the
    same representative-annotation lookup Visual Inspection's feature
    picker uses, so a feature shows the same name in both places.

    Args:
        analysis_db_path: The analysis' SQLite database — used both to
            find the output folder/sample list and to look up compound
            names.
        rel_dir: An imported dataset's folder (ADR 49) to read instead
            of the pipeline's own per-sample files; `None` = pipeline.

    Returns:
        Columns `mz`, `sample`, `moran_i`, `compound_name` (`None` when
        unannotated). Empty when no sample's `.h5ad` has ever had
        `spatial_correlation` run on it.
    """
    samples = _inspection_samples_frame(analysis_db_path, rel_dir)
    columns = ["mz", "sample", "moran_i", "compound_name"]
    if samples.empty:
        return pd.DataFrame(columns=columns)

    features = analysis_db.load_feature_list(analysis_db_path)
    feature_mzs = features["mz"].to_numpy() if not features.empty else np.array([])
    feature_names = features["compound_name"].to_numpy() if not features.empty else np.array([])

    rows = []
    for name in samples["name"]:
        h5ad_path = AnalysisBridge._sample_h5ad_path(analysis_db_path, name, rel_dir)
        if not h5ad_path.exists():
            continue
        # Only `var` and `uns/moranI` are read (h5py, element by element).
        # A plain `ad.read_h5ad` loads the whole matrix, which broke on
        # several-GB files. Even `backed="r"` still decodes all of obs
        # (scan ids, obs names) per sample, and that was part of the
        # pile-up of full reads behind the GUI freezes (ADR 50).
        with h5py.File(h5ad_path, "r") as f:
            moran = h5ad_io.read_uns(f, "moranI")
            if moran is None:
                continue
            mz_by_var = ad.io.read_elem(f["var"])["mz"]
        for var_name, i_score in moran["I"].items():
            mz = float(mz_by_var.loc[var_name])
            rows.append(
                {
                    "mz": mz,
                    "sample": name,
                    "moran_i": float(i_score),
                    "compound_name": _nearest_compound_name(mz, feature_mzs, feature_names),
                }
            )

    return pd.DataFrame(rows, columns=columns)


def _load_local_moran_layer_data(
    analysis_db_path: str, rel_dir: str | None = None
) -> tuple[list[dict], list[str]]:
    """Reads every sample's `adata.layers["localMoranI"]` (written by
    `core.spatial.local_moran.compute_local_moran_layer`) and assembles
    the `LocalMoranSummaryModel.setRows` shape directly — one dict per
    feature, that sample's *raw per-pixel local Moran's I array* (not a
    pre-thresholded percentage) folded in as `row[sample_name]`, plus its
    representative compound name.

    Unlike `_load_spatial_correlation_table`, this doesn't go through the
    flat-DataFrame/`_pivot_spatial_correlation_rows` convention — that
    convention exists to keep values QML-safe via `_dataframe_to_records`,
    but the raw arrays here never reach QML directly (only the formatted
    percentage strings `LocalMoranSummaryModel.data()` computes from them
    do), so there's nothing to scrub and no reason to round-trip through a
    DataFrame just to pivot it straight back apart.

    Args:
        analysis_db_path: The analysis' SQLite database.
        rel_dir: As `_load_spatial_correlation_table`'s.

    Returns:
        `(feature_rows, samples)` — empty/empty when no sample's `.h5ad`
        has ever had `spatial_correlation.local_enabled` run on it.
    """
    samples = _inspection_samples_frame(analysis_db_path, rel_dir)
    if samples.empty:
        return [], []

    features = analysis_db.load_feature_list(analysis_db_path)
    feature_mzs = features["mz"].to_numpy() if not features.empty else np.array([])
    feature_names = features["compound_name"].to_numpy() if not features.empty else np.array([])

    by_mz: dict[float, dict] = {}
    sample_names_with_data: set[str] = set()
    for name in samples["name"]:
        h5ad_path = AnalysisBridge._sample_h5ad_path(analysis_db_path, name, rel_dir)
        if not h5ad_path.exists():
            continue
        # Only `var` and `layers/localMoranI` are read (see
        # _load_spatial_correlation_table for why not `backed="r"`).
        with h5py.File(h5ad_path, "r") as f:
            if "layers" not in f or "localMoranI" not in f["layers"]:
                continue
            layer = ad.io.read_elem(f["layers"]["localMoranI"])
            var = ad.io.read_elem(f["var"])
        if hasattr(layer, "toarray"):
            layer = layer.toarray()
        mz_by_var = var["mz"]
        sample_names_with_data.add(name)
        for col_idx, var_name in enumerate(var.index):
            mz = float(mz_by_var.loc[var_name])
            row = by_mz.setdefault(
                mz,
                {
                    "mz": mz,
                    "compound_name": _nearest_compound_name(mz, feature_mzs, feature_names),
                },
            )
            row[name] = np.asarray(layer[:, col_idx], dtype=float)

    return list(by_mz.values()), sorted(sample_names_with_data)


def _load_local_moran_threshold_default(analysis_db_path: str) -> float:
    """The `local_threshold` this analysis' `spatial_correlation` pipeline
    stage was actually run with, recovered from `commands.arguments` —
    same provenance-recovery pattern `Plotter._get_scoring_params` (or
    similar) uses for `noise_threshold`/`fragment_ppm`. Seeds
    `LocalMoranSummaryModel.threshold`'s initial value when a fresh model
    is created, so the page opens showing what the config said before the
    user adjusts it live (see `getLocalMoranSummaryModel`).

    Falls back to `SpatialCorrelationConfig.local_threshold`'s own
    dataclass default (`1.0`) if this analysis never ran that stage, ran
    an older version that didn't record it, or the value can't be parsed.
    """
    fallback = 1.0
    try:
        with sqlite3.connect(f"file:{analysis_db_path}?mode=ro", uri=True) as con:
            row = con.execute(
                "SELECT arguments FROM commands WHERE command_name = 'spatial_correlation' "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return fallback
        args = json.loads(row[0])
        value = args.get("local_threshold")
        return float(value) if value is not None else fallback
    except (sqlite3.OperationalError, ValueError, TypeError, json.JSONDecodeError):
        return fallback


def _load_normalized_layer_name(analysis_db_path: str) -> str:
    """The `adata.layers` key this analysis' normalized layer actually
    lives under — `"TIC"` when `normalization.mode == "tic"`, or
    `f"mz_{reference_mz:.4f}"` for `mode == "reference_mz"` (see
    `core.utils.tic_normalization.run_normalization`'s dynamic layer
    naming). Recovered from `commands.arguments`, same provenance-recovery
    pattern as `_load_local_moran_threshold_default`.

    Anything the GUI needs to point at "the normalized layer" (the second
    `HeatmapControlsPanel.qml` data-layer button, `SpatialCorrelationConfig`
    display, the Integration export's Raw/TIC option) calls this rather
    than assuming `"TIC"`, since a reference-m/z-normalized analysis' layer
    is never actually called that.

    Falls back to `"TIC"` if this analysis never ran normalization, ran an
    older version that didn't record `mode`/`reference_mz`, or the value
    can't be parsed — every analysis before this feature existed was
    TIC-only, so this is the correct fallback, not just a safe default.
    """
    fallback = "TIC"
    try:
        with sqlite3.connect(f"file:{analysis_db_path}?mode=ro", uri=True) as con:
            row = con.execute(
                "SELECT arguments FROM commands WHERE command_name = 'normalize_tic' "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return fallback
        args = json.loads(row[0])
        mode = args.get("mode", "tic")
        if mode != "reference_mz":
            return fallback
        reference_mz = args.get("reference_mz")
        if reference_mz is None:
            return fallback
        return f"mz_{float(reference_mz):.4f}"
    except (sqlite3.OperationalError, ValueError, TypeError, json.JSONDecodeError):
        return fallback


def _load_calibration_reference_mz(analysis_db_path: str) -> float | None:
    """This targeted analysis' `NormalizationConfig.reference_mz` — the
    internal-standard channel `addCalibrationPoint`/`addCalibrationUnknown`
    divide the analyte's own intensity by (see
    `core.calibration.roi_intensity.roi_internal_standard_signal`), read
    regardless of `mode` ("tic" or "reference_mz"): a calibration curve's
    standards and unknowns can legitimately live on different samples, so
    this correction always applies, independent of which layer the run's
    *own* normalization stage happened to produce.

    Same provenance-recovery pattern as `_load_normalized_layer_name` (same
    `commands` row, in fact — `reference_mz` is always recorded there for
    a targeted run, since `Config.__init__` requires it regardless of
    `mode`).

    Returns:
        The recorded `reference_mz`, or `None` if this analysis never ran
        normalization, ran an untargeted pipeline (no `reference_mz` at
        all), or the value can't be parsed.
    """
    try:
        with sqlite3.connect(f"file:{analysis_db_path}?mode=ro", uri=True) as con:
            row = con.execute(
                "SELECT arguments FROM commands WHERE command_name = 'normalize_tic' "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        reference_mz = json.loads(row[0]).get("reference_mz")
        return float(reference_mz) if reference_mz is not None else None
    except (sqlite3.OperationalError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _categorize_peaks(mz_array: np.ndarray, feature_categories: pd.DataFrame) -> np.ndarray:
    """Nearest-feature category (`"no_ms2"`/`"annotated"`/`"non_annotated"`)
    for each peak in `mz_array` — which of `feature_categories`'s `mz`
    values each peak is closest to, then that feature's `category`. Feeds
    `Plotter.plot_spectra`'s coloring of the MS1 Spectra section's plot.

    Args:
        mz_array: The spectrum's peak m/z values.
        feature_categories: `analysis_db.load_feature_categories`'s result
            — must be sorted by `mz` ascending (as that function returns
            it), since matching is a sorted-array binary search.

    Returns:
        An array parallel to `mz_array`. `"non_annotated"` for every peak
        when there are no features at all (nothing to match against).
    """
    mz_array = np.asarray(mz_array)
    if feature_categories.empty:
        return np.full(mz_array.shape, "non_annotated", dtype=object)

    feature_mz = feature_categories["mz"].to_numpy()
    feature_category = feature_categories["category"].to_numpy()

    idx = np.searchsorted(feature_mz, mz_array)
    idx = np.clip(idx, 1, len(feature_mz) - 1)
    left_closer = (mz_array - feature_mz[idx - 1]) <= (feature_mz[idx] - mz_array)
    idx = np.where(left_closer, idx - 1, idx)
    return feature_category[idx]


class AnalysisBridge(QObject):
    """Read-only data access for the Analysis workspace's sections.

    Stateless — every method takes the analysis' `analysis_db_path`/
    `out_dir` explicitly (from an `AnalysisModel`) rather than holding its
    own "current analysis" state. Local SQLite reads are fast enough to
    stay synchronous; no QThread/worker needed for most of them, unlike
    `RunWorker`. Methods are called directly from QML (unlike `CoreBridge`,
    whose methods are only ever invoked from `Application`), so they're
    named to read naturally as QML calls — camelCase, matching
    `Router.toLocalPath`.

    Two exceptions to "QML calls in, a value flows straight back out":

    - `mirrorPlotReady`: `requestMirrorPlot` (unlike every other method
      here) does *not* return its result directly — it can involve
      `emp_source`/`lib_source="raw"`, real file I/O against a raw
      per-sample database or a spectral library file that isn't
      guaranteed to be fast or even local (a network mount). Running that
      synchronously on the call-in thread — which for a `@Slot` invoked
      from QML is the GUI thread — blocked Qt's entire event loop for as
      long as the I/O took, reported as the app going fully unresponsive
      ("python is not responding") rather than a normal wait. Offloaded
      to `MirrorPlotWorker` (a `QThread`) instead; QML calls
      `requestMirrorPlot` and listens for `mirrorPlotReady` instead of
      using a return value.
    - `formulaPredictionFinished`/`formulaPredictionFailed`:
      `predictFormulas` (see ADR 27) is fire-and-forget for the same
      reason — the *first* invocation on a machine downloads msbuddy's
      ~420MB reference database, real multi-second I/O that must not
      block the GUI thread. Offloaded to `FormulaPredictionWorker`;
      `PredictFormulaWindow.qml` calls `predictFormulas` and listens for
      these instead of using a return value.
    - `annotationTableReady`/`featureListReady`: `requestAnnotationTable`/
      `requestFeatureList` are fire-and-forget too, for the same reason —
      both queries filter `ms2_annotations` (which can hold many rows;
      one per scored candidate per scan per feature per library), and ran
      long enough on a real analysis to freeze the GUI outright (see
      ADR 33 for the query-side half of that fix; this is the other half).
      Offloaded to `TableQueryWorker`. `AnnotationsSection.qml`/
      `HeatmapControlsPanel.qml` call these and listen for the `*Ready`
      signals instead of using a return value.
    - `getSpatialCorrelationModel`: returns a `SpatialCorrelationModel`
      (a small persistent `QObject`, not a plain value) rather than
      emitting a `*Ready` signal — see that method's own docstring for
      why a whole long-lived object, not a fire-and-forget row list, is
      what this one needs (the QML section that displays it is a lazy
      `Loader`, destroyed on every tab switch away — anything it held
      itself would reset every time). The model itself still fills in
      asynchronously (`TableQueryWorker`, same "don't block the GUI
      thread" reasoning as `requestAnnotationTable`/`requestFeatureList`
      above — real per-sample `.h5ad` file I/O), signalled via the
      model's own `rowsLoading` property rather than a bridge-level
      signal.
    - `heatmapPrefetchReady`: `prefetchHeatmapSamples` warms
      `heatmap_provider`'s cache for a set of samples on a background
      thread (`HeatmapPrefetchWorker`, real `.h5ad` file I/O) — called
      by `AnalysisPage.qml` just *before* switching to the Visual
      Inspection tab, so the tab's own `ZoomableImage`s (deliberately
      *not* async, see that file's own comment) find a warm cache and
      don't block the GUI thread reading the file the moment they
      activate. Reported directly as a freeze, worst right after a
      cross-tab "Show spatial distribution"/"Inspect visually" handoff
      (first time Visual Inspection opens for that analysis), but really
      the same cost on any cold first visit.
    """

    mirrorPlotReady = Signal("QVariantMap")
    formulaPredictionFinished = Signal(str)  # analysis_db_path
    formulaPredictionFailed = Signal(str, str)  # analysis_db_path, message
    annotationTableReady = Signal(list)
    bestScanPlotReady = Signal(str)  # data: URI, "" on failure
    featureListReady = Signal(list)
    featuresForPredictionReady = Signal(str, list)  # analysis_db_path, rows
    exportFinished = Signal(str)  # human-readable "done" message
    exportFailed = Signal(str)  # human-readable error message
    heatmapPrefetchReady = Signal()
    obsColumnsReady = Signal(list)
    featureValueRangeReady = Signal(dict)
    obsValueRangeReady = Signal(dict)
    obsCategoriesReady = Signal(list)
    summaryReady = Signal(str, dict)  # analysis_db_path, counts
    # Imported analyzed .h5ad datasets (ADR 49)
    activeDatasetChanged = Signal()
    importedDatasetsChanged = Signal(str)  # analysis_db_path
    h5adInspected = Signal(dict)
    h5adInspectFailed = Signal(str)
    datasetTaskProgress = Signal(int, int, str)  # done, total, sample name
    datasetImportFinished = Signal(int, str)  # dataset id, message
    datasetImportFailed = Signal(str)
    datasetMoranFinished = Signal(str)
    datasetMoranFailed = Signal(str)
    roiSourceWriteFailed = Signal(str)  # ROI saved locally, write-back failed
    registrationChanged = Signal()  # an H&E image attached or a fit saved
    targetListMatchesChanged = Signal(str)  # analysis_db_path (ADR 60)
    featureNormalizationFinished = Signal(str, str)  # layer name, message (ADR 61)
    featureNormalizationFailed = Signal(str)
    annotationTaskFinished = Signal(str)  # message (re-annotate / restore, ADR 63)
    annotationTaskFailed = Signal(str)
    featureLayersChanged = Signal(str)  # analysis_db_path

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        # Owned here (rather than constructed in main.py) so Application is
        # the single place that wires up an AnalysisBridge; main.py just
        # registers it with the engine (`engine.addImageProvider("heatmap",
        # application.analysis_bridge.heatmap_provider)`).
        # Single-slot cache for getSpatialCorrelationModel — see that
        # method's own docstring for why.
        self._spatial_correlation_model_path: str | None = None
        self._spatial_correlation_model_rel_dir: str | None = None
        self._spatial_correlation_model: "SpatialCorrelationModel | None" = None
        # Same single-slot cache pattern, for the local Moran's I summary
        # table — see getLocalMoranSummaryModel.
        self._local_moran_summary_model_path: str | None = None
        self._local_moran_summary_model_rel_dir: str | None = None
        self._local_moran_summary_model: "LocalMoranSummaryModel | None" = None
        # Single-slot cache for requestFeatureList — see that method's
        # own docstring for why.
        self._feature_list_cache_path: str | None = None
        self._feature_list_cache_rows: list = []
        self.heatmap_provider = HeatmapImageProvider()
        # Same "own it here, main.py just registers it" reasoning as
        # heatmap_provider above.
        self.he_image_provider = HEImageProvider()
        # Shares heatmap_provider's sample cache, lock and render thread
        # (ADR 50).
        self.he_overlay_provider = HEOverlayImageProvider(self.heatmap_provider)
        # Kept alive while running — nothing else holds a reference to it
        # (same pattern as CoreBridge._run_worker). Replacing it (a new
        # request before the previous one finished) is also how a stale
        # result gets discarded: _onMirrorPlotSucceeded/_onMirrorPlotFailed
        # check `self.sender() is self._mirror_plot_worker` before
        # emitting, so toggling emp/lib source quickly can't let an
        # in-flight older (e.g. slower raw-source) request overwrite a
        # newer result.
        self._mirror_plot_worker: MirrorPlotWorker | None = None
        # Same "kept alive while running, replacing it discards a stale
        # result" pattern as _mirror_plot_worker above.
        self._formula_prediction_worker: FormulaPredictionWorker | None = None
        # Same pattern again, one worker slot each — a new request (e.g.
        # the user reopening a tab) discards whatever the previous one
        # was still fetching.
        self._annotation_table_worker: TableQueryWorker | None = None
        self._best_scan_plot_worker: TableQueryWorker | None = None
        self._feature_list_worker: TableQueryWorker | None = None
        self._prediction_features_worker: TableQueryWorker | None = None
        self._prediction_features_path = ""
        # One slot each for HeatmapControlsPanel.qml's four .h5ad-backed
        # fetches — see requestObsColumns/requestFeatureValueRange/
        # requestObsValueRange/requestObsCategories. Four separate
        # attributes (not one shared slot) because these legitimately run
        # concurrently: switching inspectionMode fires refreshAutoRange()
        # and refreshObsCategoryLegend() in the same tick.
        self._obs_columns_worker: HeatmapQueryWorker | None = None
        self._feature_value_range_worker: HeatmapQueryWorker | None = None
        self._obs_value_range_worker: HeatmapQueryWorker | None = None
        self._obs_categories_worker: HeatmapQueryWorker | None = None
        # Same pattern once more — one slot shared by every Export menu
        # action, since only one export can sensibly run at a time from
        # one workspace.
        self._export_worker: ExportWorker | None = None
        # Which dataset Visual Inspection / Spatial Correlation read
        # (ADR 49): `None` = the pipeline output, else an
        # `imported_datasets` row of `_active_db_path`. Only one analysis
        # workspace is ever open, so one slot is enough; any other
        # analysis path always resolves to its pipeline output.
        self._active_db_path: str | None = None
        self._active_dataset: dict | None = None
        self._h5ad_inspect_worker: TableQueryWorker | None = None
        self._dataset_task_worker: DatasetTaskWorker | None = None
        self._import_db_path: str | None = None
        self._normalization_db_path: str | None = None
        self._annotation_task_db: str | None = None
        self._summary_worker: TableQueryWorker | None = None
        self._summary_worker_path: str | None = None
        self._summary_cache_path: str | None = None
        self._summary_cache: dict = {}

    @Slot(str)
    def setHeatmapAnalysis(self, analysis_db_path: str) -> None:
        """Point the `image://heatmap/...` provider at this analysis.

        Called once when the Visual Inspection section loads (there's only
        ever one active analysis workspace at a time). Follows the active
        imported dataset, if any (see `setActiveDataset`).
        """
        self.heatmap_provider.setAnalysisDbPath(
            analysis_db_path, self._active_rel_dir(analysis_db_path)
        )

    @Slot(str, list)
    def prefetchHeatmapSamples(self, analysis_db_path: str, sample_names: list) -> None:
        """Warm `heatmap_provider`'s sample cache for `sample_names` on a
        background thread. `heatmapPrefetchReady` fires once done, or
        immediately if there's nothing to prefetch.

        `AnalysisPage.goToVisualInspection` calls this and waits for
        `heatmapPrefetchReady` before switching to Visual Inspection, so
        the tab opens with every tile ready at once. Tiles render off the
        GUI thread either way (ADR 50), so this is about presentation,
        not about avoiding a freeze.

        It also points the provider at `analysis_db_path` (and the active
        dataset) synchronously first, because the worker needs that to
        resolve sample names at all. It loads the layer the controls panel
        will default to: the analysis' normalized layer for the pipeline
        output, or, for an imported dataset, `TIC` if it has one, else `X`
        (`HeatmapControlsPanel.refreshDatasetLayers`'s rule).

        Args:
            analysis_db_path: The analysis' SQLite database.
            sample_names: Which samples to prefetch — every sample Visual
                Inspection's grid shows.
        """
        rel_dir = self._active_rel_dir(analysis_db_path)
        self.heatmap_provider.setAnalysisDbPath(analysis_db_path, rel_dir)
        if not sample_names:
            self.heatmapPrefetchReady.emit()
            return
        if rel_dir:
            layers = self._active_dataset["layers"]
            layer = "TIC" if "TIC" in layers else "X"
        else:
            layer = _load_normalized_layer_name(analysis_db_path)
        worker = HeatmapPrefetchWorker(self.heatmap_provider, sample_names, layer, self)
        worker.succeeded.connect(self._onHeatmapPrefetchSucceeded)
        worker.failed.connect(self._onHeatmapPrefetchFailed)
        self._heatmap_prefetch_worker = worker
        worker.start()

    @Slot()
    def _onHeatmapPrefetchSucceeded(self) -> None:
        if self.sender() is not self._heatmap_prefetch_worker:
            return  # superseded by a newer request — discard
        self.heatmapPrefetchReady.emit()

    @Slot(str)
    def _onHeatmapPrefetchFailed(self, message: str) -> None:
        if self.sender() is not self._heatmap_prefetch_worker:
            return  # superseded by a newer request — discard
        logger.warning("heatmap prefetch failed: %s", message)
        # Still proceed with the switch — a cold cache after this just
        # means the original (known, tolerable-if-not-ideal) synchronous
        # cost on first tile request, not a reason to block navigation.
        self.heatmapPrefetchReady.emit()

    @Slot(str)
    def setHeImageAnalysis(self, analysis_db_path: str) -> None:
        """Point the `image://he_image/...` and `image://he_overlay/...`
        providers at this analysis — the H&E-coregistration analogue of
        `setHeatmapAnalysis`, called from the same place (Visual
        Inspection's `onAnalysisChanged`)."""
        self.he_image_provider.setAnalysisDbPath(analysis_db_path)
        self.he_overlay_provider.setAnalysisDbPath(
            analysis_db_path, self._active_rel_dir(analysis_db_path)
        )

    @Slot(list, float, str)
    def requestFeatureValueRange(self, sample_names: list, mz: float, layer: str) -> None:
        """Start fetching the real (min, max) of one feature's values
        across `sample_names` on a background thread — what autoscale is
        actually using for the Visual Inspection grid. Result arrives via
        `featureValueRangeReady({"vmin": ..., "vmax": ...})`.

        Used to be a synchronous `@Slot` returning the range directly,
        called from `HeatmapControlsPanel.qml`'s `refreshAutoRange()` —
        real `.h5ad`-backed work on the GUI thread, one of several real
        remaining causes of Visual Inspection's freeze-on-open (ADR 48
        round 3; tile rendering itself was already fixed). Fire-and-forget
        rather than a return value, same reasoning as `requestFeatureList`
        — see the class docstring. No result caching: `mz`/`layer`/
        `sample_names` change on nearly every call, so a single-slot cache
        would rarely hit.

        Args:
            sample_names: Samples to combine — normally the currently
                visible ones.
            mz: The feature's consensus m/z.
            layer: `"raw"` or `"TIC"`.
        """
        worker = HeatmapQueryWorker(
            self.heatmap_provider.getFeatureValueRange, sample_names, mz, layer, parent=self
        )
        worker.succeeded.connect(self._onFeatureValueRangeSucceeded)
        worker.failed.connect(self._onFeatureValueRangeFailed)
        self._feature_value_range_worker = worker
        worker.start()

    @Slot(object)
    def _onFeatureValueRangeSucceeded(self, value_range: dict) -> None:
        if self.sender() is not self._feature_value_range_worker:
            return  # superseded by a newer request — discard
        self.featureValueRangeReady.emit(value_range)

    @Slot(str)
    def _onFeatureValueRangeFailed(self, message: str) -> None:
        if self.sender() is not self._feature_value_range_worker:
            return  # superseded by a newer request — discard
        logger.warning("feature value range load failed: %s", message)
        self.featureValueRangeReady.emit({"vmin": 0, "vmax": 1})

    @Slot(list)
    def requestObsColumns(self, sample_names: list) -> None:
        """Start fetching `adata.obs` columns Visual Inspection can
        overlay instead of a feature (e.g. `tic`, `rt`) on a background
        thread; result arrives via `obsColumnsReady(columns)`. See
        `requestFeatureValueRange`'s docstring for why this is async now
        (ADR 48 round 3) and `HeatmapImageProvider.getObsColumns` for the
        actual read.

        Args:
            sample_names: Samples to look for a readable `.h5ad` in —
                normally every sample of the analysis (the schema is the
                same across all of them), not just the visible ones.
        """
        worker = HeatmapQueryWorker(self.heatmap_provider.getObsColumns, sample_names, parent=self)
        worker.succeeded.connect(self._onObsColumnsSucceeded)
        worker.failed.connect(self._onObsColumnsFailed)
        self._obs_columns_worker = worker
        worker.start()

    @Slot(object)
    def _onObsColumnsSucceeded(self, columns: list) -> None:
        if self.sender() is not self._obs_columns_worker:
            return  # superseded by a newer request — discard
        self.obsColumnsReady.emit(columns)

    @Slot(str)
    def _onObsColumnsFailed(self, message: str) -> None:
        if self.sender() is not self._obs_columns_worker:
            return  # superseded by a newer request — discard
        logger.warning("obs columns load failed: %s", message)
        self.obsColumnsReady.emit([])

    @Slot(list, str)
    def requestObsValueRange(self, sample_names: list, obs_column: str) -> None:
        """Start fetching the real (min, max) of one numeric `obs` column
        across `sample_names` on a background thread — the `obs`-column
        analogue of `requestFeatureValueRange`; result arrives via
        `obsValueRangeReady({"vmin": ..., "vmax": ...})`. See
        `HeatmapImageProvider.getObsValueRange` for the actual read.
        """
        worker = HeatmapQueryWorker(
            self.heatmap_provider.getObsValueRange, sample_names, obs_column, parent=self
        )
        worker.succeeded.connect(self._onObsValueRangeSucceeded)
        worker.failed.connect(self._onObsValueRangeFailed)
        self._obs_value_range_worker = worker
        worker.start()

    @Slot(object)
    def _onObsValueRangeSucceeded(self, value_range: dict) -> None:
        if self.sender() is not self._obs_value_range_worker:
            return  # superseded by a newer request — discard
        self.obsValueRangeReady.emit(value_range)

    @Slot(str)
    def _onObsValueRangeFailed(self, message: str) -> None:
        if self.sender() is not self._obs_value_range_worker:
            return  # superseded by a newer request — discard
        logger.warning("obs value range load failed: %s", message)
        self.obsValueRangeReady.emit({"vmin": 0, "vmax": 1})

    @Slot(list, str)
    def requestObsCategories(self, sample_names: list, obs_column: str) -> None:
        """Start fetching every distinct category of one discrete `obs`
        column, combined across `sample_names`, on a background thread —
        colored in the fixed order every tile's render call must also use.
        Result arrives via `obsCategoriesReady(categories)`. See
        `HeatmapImageProvider.getObsCategories` for the actual read.

        Args:
            sample_names: Normally every sample of the analysis, not just
                the visible ones, so colors stay stable as visibility is
                toggled.
        """
        worker = HeatmapQueryWorker(
            self.heatmap_provider.getObsCategories, sample_names, obs_column, parent=self
        )
        worker.succeeded.connect(self._onObsCategoriesSucceeded)
        worker.failed.connect(self._onObsCategoriesFailed)
        self._obs_categories_worker = worker
        worker.start()

    @Slot(object)
    def _onObsCategoriesSucceeded(self, categories: list) -> None:
        if self.sender() is not self._obs_categories_worker:
            return  # superseded by a newer request — discard
        self.obsCategoriesReady.emit(categories)

    @Slot(str)
    def _onObsCategoriesFailed(self, message: str) -> None:
        if self.sender() is not self._obs_categories_worker:
            return  # superseded by a newer request — discard
        logger.warning("obs categories load failed: %s", message)
        self.obsCategoriesReady.emit([])

    @Slot(str, result=str)
    def getNormalizedLayerName(self, analysis_db_path: str) -> str:
        """This analysis' actual normalized-layer key — see
        `_load_normalized_layer_name`. Cheap, synchronous `commands` read,
        same as `getLocalMoranSummaryModel`'s threshold seed — no
        background thread needed for one small provenance lookup.
        """
        if not analysis_db_path or not Path(analysis_db_path).exists():
            return "TIC"
        return _load_normalized_layer_name(analysis_db_path)

    @Slot(str)
    def ensureSchemaCurrent(self, analysis_db_path: str) -> None:
        """Bring an existing analysis database's schema up to date —
        called once by `AnalysisPage.qml` whenever an analysis workspace
        opens, so an analysis run before a schema change (e.g. ADR 33's
        `idx_ann_rank_feature_1` index) benefits from it without needing a
        full re-run.

        Just `analysis_db.init_analysis_db` (`connect` + `create_analysis_schema`
        + `commit`) — every statement in `create_analysis_schema` is
        `CREATE TABLE`/`CREATE INDEX IF NOT EXISTS`, so this is a cheap,
        safe no-op on an already-current schema and only ever adds
        structure, never touches data. `connect`'s own "callers issue no
        DDL" note is about concurrent per-sample worker processes racing a
        schema lock *during a pipeline run* — irrelevant here, this only
        ever runs against an already-finished analysis from a single GUI
        connection.

        Args:
            analysis_db_path: The analysis' SQLite database.
        """
        if not analysis_db_path or not Path(analysis_db_path).exists():
            return
        analysis_db.init_analysis_db(analysis_db_path).close()
        # Backfill: an analysis run before annotations went into `.var`.
        paths = list(self._pipeline_sample_paths(analysis_db_path).values())
        if paths and not core_export.var_has_annotations(paths[0]):
            self._refresh_var_annotations(analysis_db_path)

    def _refresh_var_annotations(self, analysis_db_path: str) -> None:
        """Rewrite `.var`'s annotation columns in every sample `.h5ad` and
        `merged.h5ad` after an annotation changed. Best effort: a failure
        leaves the files as they were and never blocks the GUI action."""
        paths = list(self._pipeline_sample_paths(analysis_db_path).values())
        try:
            core_export.write_annotations_to_var(
                analysis_db_path, paths + [Path(analysis_db_path).parent / "merged.h5ad"]
            )
        except (OSError, sqlite3.Error, KeyError, ValueError):
            logger.exception("could not write annotations into .var for %s", analysis_db_path)

    @Slot(str, result=dict)
    def getSummary(self, analysis_db_path: str) -> dict:
        """Headline counts for the Summary section.

        Args:
            analysis_db_path: Path to the analysis' SQLite database, as
                resolved by `AnalysisModel.analysisDbPath`.

        Returns:
            See `analysis_db.load_summary_counts`; an all-zero dict when
            `analysis_db_path` is empty or doesn't exist (nothing crashes
            the section, it just reads as an empty analysis).
        """
        if not analysis_db_path or not Path(analysis_db_path).exists():
            return dict(_EMPTY_SUMMARY)
        return analysis_db.load_summary_counts(analysis_db_path)

    @Slot(str)
    def requestSummary(self, analysis_db_path: str) -> None:
        """`getSummary`, on a background thread: the result arrives via
        `summaryReady(analysis_db_path, counts)`. `SummarySection.qml` uses
        this. `feature_compound_scores` is a view aggregating over every
        MS2 annotation, and on a real 2.6 GB analysis the counts took
        6.8 s on the GUI thread when opening the workspace (ADR 50).

        Cached per analysis (single slot), since the counts don't change
        while the analysis is open. A cache hit emits synchronously.
        """
        if not analysis_db_path or not Path(analysis_db_path).exists():
            self.summaryReady.emit(analysis_db_path, dict(_EMPTY_SUMMARY))
            return
        if analysis_db_path == self._summary_cache_path:
            self.summaryReady.emit(analysis_db_path, self._summary_cache)
            return
        worker = TableQueryWorker(analysis_db.load_summary_counts, analysis_db_path, self)
        worker.succeeded.connect(self._onSummarySucceeded)
        worker.failed.connect(self._onSummaryFailed)
        self._summary_worker = worker
        self._summary_worker_path = analysis_db_path
        worker.start()

    @Slot(object)
    def _onSummarySucceeded(self, counts: dict) -> None:
        if self.sender() is not self._summary_worker:
            return  # superseded by a newer request — discard
        self._summary_cache_path = self._summary_worker_path
        self._summary_cache = dict(counts)
        self.summaryReady.emit(self._summary_worker_path, self._summary_cache)

    @Slot(str)
    def _onSummaryFailed(self, message: str) -> None:
        if self.sender() is not self._summary_worker:
            return
        logger.warning("summary counts failed: %s", message)
        self.summaryReady.emit(self._summary_worker_path, dict(_EMPTY_SUMMARY))

    @Slot(str)
    @Slot(str, bool)
    def requestAnnotationTable(
        self, analysis_db_path: str, include_unannotated: bool = False
    ) -> None:
        """Start fetching one row per feature (its representative compound
        hit) on a background thread; the result arrives via
        `annotationTableReady(rows)`.

        Fire-and-forget rather than a return value — see the class
        docstring for why. Used by the Annotations section's table, which
        shows a loading state (`LoadingOverlay`, via `AnalysisPage.qml`'s
        `currentSectionReady`) until `annotationTableReady` fires.

        Args:
            analysis_db_path: The analysis' SQLite database.

        `annotationTableReady`'s rows come from
        `analysis_db.load_feature_representative_annotations` — not
        necessarily the single highest-`score` compound, see
        `AnnotateConfig.representative_score_tolerance`. Empty when
        annotation never ran (or the db path doesn't exist).
        `include_unannotated` also lists every feature with no identity.
        Every row carries its best MS2 scan's `ms2_*` columns (see
        `analysis_db.load_feature_best_ms2_scans`), `None` without MS2.
        """
        if not analysis_db_path or not Path(analysis_db_path).exists():
            self.annotationTableReady.emit([])
            return
        worker = TableQueryWorker(
            functools.partial(_load_annotation_rows, include_unannotated=include_unannotated),
            analysis_db_path, self,
        )
        worker.succeeded.connect(self._onAnnotationTableSucceeded)
        worker.failed.connect(self._onAnnotationTableFailed)
        self._annotation_table_worker = worker
        worker.start()

    @Slot(object)
    def _onAnnotationTableSucceeded(self, df: pd.DataFrame) -> None:
        if self.sender() is not self._annotation_table_worker:
            return  # superseded by a newer request — discard
        self.annotationTableReady.emit([] if df.empty else _dataframe_to_records(df))

    @Slot(str)
    def _onAnnotationTableFailed(self, message: str) -> None:
        if self.sender() is not self._annotation_table_worker:
            return  # superseded by a newer request — discard
        logger.warning("annotation table load failed: %s", message)
        self.annotationTableReady.emit([])

    @Slot(str, result=QObject)
    def getSpatialCorrelationModel(self, analysis_db_path: str) -> SpatialCorrelationModel:
        """The `SpatialCorrelationModel` for this analysis — created
        (and its first fetch kicked off) on first call, otherwise the
        same cached instance handed back again.

        Returning a whole model object rather than emitting a `*Ready`
        row list (like `requestAnnotationTable`/`requestFeatureList`) is
        the point: `SpatialCorrelationSection.qml` is a lazy `Loader`
        (`AnalysisPage.qml`'s `spatialCorrLoader`, `active` only while its
        tab is current), so the *entire QML item* — including any plain
        `property var rows`/`sortColumn`/`nameQuery` it held — is
        destroyed on every tab switch away from it. That was reported
        directly: "if I go back I don't have the same table as before, I
        might have sorted for a column." Caching just the fetched *rows*
        in Python (an earlier version of this fix) wasn't enough, because
        the sort/filter *state* still lived in the doomed QML item. A
        `SpatialCorrelationModel` instance owned here, by this long-lived
        `AnalysisBridge`, carries both the rows and the current
        sort/filter state together, and re-binding the QML section's
        `model` property to the same instance on a later visit is instant
        and picks up exactly where the user left off. (`TableView` bound
        to a real `QAbstractTableModel` was tried first — it turned out
        not to instantiate any delegates at all under this project's
        offscreen GUI test harness, a verified environment limitation —
        see `SpatialCorrelationModel`'s own docstring.)

        Single-slot cache, keyed by `analysis_db_path` — switching to a
        *different* analysis discards the old model (`deleteLater()`) and
        builds a fresh one, matching "switching to a new analysis clears
        the memory of the table". The underlying row fetch is still
        backgrounded (`TableQueryWorker`, real per-sample `.h5ad` file
        I/O) via `model.setRowsLoading(True)` until
        `_load_spatial_correlation_table` returns.

        Args:
            analysis_db_path: The analysis' SQLite database.

        Returns:
            The (possibly still-loading — see `model.rowsLoading`)
            `SpatialCorrelationModel` for this analysis, or a fresh
            empty one for an invalid/missing path.
        """
        if not analysis_db_path or not Path(analysis_db_path).exists():
            return SpatialCorrelationModel(self)
        rel_dir = self._active_rel_dir(analysis_db_path)
        if (
            analysis_db_path == self._spatial_correlation_model_path
            and rel_dir == self._spatial_correlation_model_rel_dir
        ):
            return self._spatial_correlation_model

        if self._spatial_correlation_model is not None:
            self._spatial_correlation_model.deleteLater()
        model = SpatialCorrelationModel(self)
        model.setRowsLoading(True)
        self._spatial_correlation_model_path = analysis_db_path
        self._spatial_correlation_model_rel_dir = rel_dir
        self._spatial_correlation_model = model

        worker = TableQueryWorker(
            functools.partial(_load_spatial_correlation_table, rel_dir=rel_dir),
            analysis_db_path, self,
        )
        # Plain bound-method connections, not a lambda — PySide only
        # marshals a cross-thread signal onto the main thread's event
        # loop for a bound QObject-method slot (it inspects the
        # receiver's thread affinity); a lambda has no such receiver, so
        # it ran directly on the worker thread instead, which then wrote
        # straight to a QML-exposed property from off the GUI thread and
        # hung the whole test process (found the hard way building the
        # previous version of this cache).
        worker.succeeded.connect(self._onSpatialCorrelationSucceeded)
        worker.failed.connect(self._onSpatialCorrelationFailed)
        self._spatial_correlation_worker = worker
        worker.start()
        return model

    @Slot(object)
    def _onSpatialCorrelationSucceeded(self, df: pd.DataFrame) -> None:
        worker = self.sender()
        if worker is not self._spatial_correlation_worker:
            return  # superseded by a newer request — discard
        if self._spatial_correlation_model is None:
            return  # analysis switched again before this landed
        flat_rows = [] if df.empty else _dataframe_to_records(df)
        feature_rows, samples = _pivot_spatial_correlation_rows(flat_rows)
        self._spatial_correlation_model.setRows(feature_rows, samples)
        self._spatial_correlation_model.setRowsLoading(False)

    @Slot(str)
    def _onSpatialCorrelationFailed(self, message: str) -> None:
        if self.sender() is not self._spatial_correlation_worker:
            return  # superseded by a newer request — discard
        logger.warning("spatial correlation load failed: %s", message)
        if self._spatial_correlation_model is not None:
            self._spatial_correlation_model.setRowsLoading(False)

    @Slot(str, result=QObject)
    def getLocalMoranSummaryModel(self, analysis_db_path: str) -> LocalMoranSummaryModel:
        """The `LocalMoranSummaryModel` for this analysis — the local
        Moran's I analogue of `getSpatialCorrelationModel`, same single-
        slot cache/background-fetch/state-survives-tab-switch reasoning in
        full (see that method's own docstring), just backed by
        `_load_local_moran_layer_data`/`adata.layers["localMoranI"]`
        instead of `adata.uns["moranI"]` — and, unlike that table, this one
        seeds its `threshold` from `_load_local_moran_threshold_default`
        (this analysis' own recorded `spatial_correlation.local_threshold`)
        on creation, before any rows arrive, so the page opens at the
        configured default rather than the model's own generic fallback.
        Adjusting `threshold` afterward is a live, in-memory recompute —
        see `LocalMoranSummaryModel.setThreshold` — never a new fetch.

        Args:
            analysis_db_path: The analysis' SQLite database.

        Returns:
            The (possibly still-loading — see `model.rowsLoading`)
            `LocalMoranSummaryModel` for this analysis, or a fresh empty
            one for an invalid/missing path.
        """
        if not analysis_db_path or not Path(analysis_db_path).exists():
            return LocalMoranSummaryModel(self)
        rel_dir = self._active_rel_dir(analysis_db_path)
        if (
            analysis_db_path == self._local_moran_summary_model_path
            and rel_dir == self._local_moran_summary_model_rel_dir
        ):
            return self._local_moran_summary_model

        if self._local_moran_summary_model is not None:
            self._local_moran_summary_model.deleteLater()
        model = LocalMoranSummaryModel(self)
        model.setThreshold(_load_local_moran_threshold_default(analysis_db_path))
        model.setRowsLoading(True)
        self._local_moran_summary_model_path = analysis_db_path
        self._local_moran_summary_model_rel_dir = rel_dir
        self._local_moran_summary_model = model

        worker = TableQueryWorker(
            functools.partial(_load_local_moran_layer_data, rel_dir=rel_dir),
            analysis_db_path, self,
        )
        worker.succeeded.connect(self._onLocalMoranSummarySucceeded)
        worker.failed.connect(self._onLocalMoranSummaryFailed)
        self._local_moran_summary_worker = worker
        worker.start()
        return model

    @Slot(object)
    def _onLocalMoranSummarySucceeded(self, result: tuple) -> None:
        worker = self.sender()
        if worker is not self._local_moran_summary_worker:
            return  # superseded by a newer request — discard
        if self._local_moran_summary_model is None:
            return  # analysis switched again before this landed
        feature_rows, samples = result
        self._local_moran_summary_model.setRows(feature_rows, samples)
        self._local_moran_summary_model.setRowsLoading(False)

    @Slot(str)
    def _onLocalMoranSummaryFailed(self, message: str) -> None:
        if self.sender() is not self._local_moran_summary_worker:
            return  # superseded by a newer request — discard
        logger.warning("local Moran's I layer load failed: %s", message)
        if self._local_moran_summary_model is not None:
            self._local_moran_summary_model.setRowsLoading(False)

    @Slot(str, float, result=dict)
    def getMoranIForFeature(self, analysis_db_path: str, mz: float) -> dict:
        """Per-sample Moran's I scores for one feature, for Visual
        Inspection's per-sample-name label — a plain in-memory dict scan
        over the already-cached `SpatialCorrelationModel`'s rows, **not**
        a new fetch. Unlike the four heatmap-controls calls this same
        session made async (real `.h5ad` I/O each time), this is safe to
        keep synchronous: `getSpatialCorrelationModel` eagerly warms this
        cache at analysis-open time, so by the time the user is looking at
        a feature in Visual Inspection the data is normally already
        sitting in RAM.

        Deliberately does **not** trigger a load if the cache is missing
        or belongs to a different analysis — returns `{}` instead, since
        triggering one here would mean this "cheap" call could silently
        become a background `.h5ad` fetch, which is exactly the kind of
        surprise this split is meant to avoid.
        """
        if (
            analysis_db_path != self._spatial_correlation_model_path
            or self._spatial_correlation_model_rel_dir != self._active_rel_dir(analysis_db_path)
            or self._spatial_correlation_model is None
        ):
            return {}
        return self._spatial_correlation_model.moranIForMz(mz)

    @Slot(str, result=list)
    def getFeaturesForPrediction(self, analysis_db_path: str) -> list:
        """Every feature, with its current representative identity (if
        any) — backs the "Predict formula" window's feature picker.

        Args:
            analysis_db_path: The analysis' SQLite database.

        Returns:
            Records from `analysis_db.load_all_features_for_prediction`:
            `feature_id`, `mz`, `compound_name` (`None` when nothing
            matched yet — the picker's "select all unannotated" reads
            this), `best_score`. Empty when the db path doesn't exist or
            there are no features.
        """
        if not analysis_db_path or not Path(analysis_db_path).exists():
            return []
        return _dataframe_to_records(analysis_db.load_all_features_for_prediction(analysis_db_path))

    @Slot(str)
    def requestFeaturesForPrediction(self, analysis_db_path: str) -> None:
        """`getFeaturesForPrediction` on a background thread: the rows arrive
        via `featuresForPredictionReady(analysis_db_path, rows)` (over the
        lab share, the blocking call froze the window)."""
        if not analysis_db_path or not Path(analysis_db_path).exists():
            self.featuresForPredictionReady.emit(analysis_db_path, [])
            return
        worker = TableQueryWorker(
            analysis_db.load_all_features_for_prediction, analysis_db_path, self
        )
        worker.succeeded.connect(self._onFeaturesForPredictionSucceeded)
        worker.failed.connect(self._onFeaturesForPredictionFailed)
        self._prediction_features_worker = worker
        self._prediction_features_path = analysis_db_path
        worker.start()

    @Slot(object)
    def _onFeaturesForPredictionSucceeded(self, df: pd.DataFrame) -> None:
        if self.sender() is not self._prediction_features_worker:
            return  # superseded by a newer request — discard
        rows = [] if df.empty else _dataframe_to_records(df)
        self.featuresForPredictionReady.emit(self._prediction_features_path, rows)

    @Slot(str)
    def _onFeaturesForPredictionFailed(self, message: str) -> None:
        if self.sender() is not self._prediction_features_worker:
            return  # superseded by a newer request — discard
        logger.warning("prediction feature list load failed: %s", message)
        self.featuresForPredictionReady.emit(self._prediction_features_path, [])

    @Slot(str, list, dict)
    def predictFormulas(
        self, analysis_db_path: str, feature_ids: list, settings_dict: dict
    ) -> None:
        """Start on-demand formula prediction for `feature_ids` on a
        background thread; completion arrives via
        `formulaPredictionFinished(analysis_db_path)` or
        `formulaPredictionFailed(analysis_db_path, message)`.

        Fire-and-forget rather than a return value — see the class
        docstring for why (the first invocation on a machine can mean a
        real, multi-second ~420MB download). Used by
        `PredictFormulaWindow.qml`'s "Run" button, which shows a
        `LoadingOverlay` until one of those two signals fires.

        Args:
            analysis_db_path: The analysis' SQLite database.
            feature_ids: Which features to predict for — user-selected in
                the window's feature picker.
            settings_dict: `{"adducts", "error_ppm", "top_n", "halogen"}`
                — see `formula_prediction.FormulaPredictionSettings`.
        """
        try:
            settings = FormulaPredictionSettings(
                adducts=list(settings_dict["adducts"]),
                error_ppm=float(settings_dict.get("error_ppm", 10.0)),
                top_n=int(settings_dict.get("top_n", 5)),
                halogen=bool(settings_dict.get("halogen", False)),
            )
        except (KeyError, ValueError, TypeError) as e:
            self.formulaPredictionFailed.emit(analysis_db_path, str(e))
            return

        worker = FormulaPredictionWorker(
            analysis_db_path, [int(f) for f in feature_ids], settings, self
        )
        worker.succeeded.connect(self._onFormulaPredictionSucceeded)
        worker.failed.connect(self._onFormulaPredictionFailed)
        self._formula_prediction_worker = worker
        worker.start()

    @Slot(str, list)
    def _onFormulaPredictionSucceeded(self, analysis_db_path: str, rows: list) -> None:
        if self.sender() is not self._formula_prediction_worker:
            return  # superseded by a newer request — discard
        self._refresh_var_annotations(analysis_db_path)
        self.formulaPredictionFinished.emit(analysis_db_path)

    @Slot(str, str)
    def _onFormulaPredictionFailed(self, analysis_db_path: str, message: str) -> None:
        if self.sender() is not self._formula_prediction_worker:
            return  # superseded by a newer request — discard
        self.formulaPredictionFailed.emit(analysis_db_path, message)

    @Slot(str, int, int, result=list)
    def getFeatureTopHits(self, analysis_db_path: str, feature_id: int, top_n: int) -> list:
        """Top-N annotation hits for one feature — best row per distinct
        compound (MS2), plus, if the feature has any, its top-N predicted
        formulas (see ADR 27) appended after them, regardless of which
        adduct produced each one.

        Same "best per compound" MS2 selection as the MS1 spectra
        section's `getFeatureDetail`'s `top_hits` (kept as a standalone
        method here since the Annotations section starts from a picked
        feature directly, not a clicked spectrum point).

        Args:
            analysis_db_path: The analysis' SQLite database.
            feature_id: The feature to fetch hits for.
            top_n: How many hits to return per kind, user-selectable.

        Returns:
            Records from `analysis_db.load_ms2_annotations_for_feature`
            (`kind: "ms2"`, kept to the top-scoring row per `inchikey`,
            capped at `top_n`), then its precursor-m/z candidates
            (`kind: "precursor"`, ADR 0065, best `top_n` by |ppm|, `id`
            prefixed `"p"`, `n_feature_candidates` all of them), then
            records from
            `analysis_db.load_top_predicted_formulas_for_feature`
            (`kind: "predicted"`, `id` negated so it can never collide
            with a real `ms2_annotations.id` — this project has no
            "annotation" for a predicted row, it's a mass-decomposition
            guess, not a scored match). Empty when the feature has
            neither.
        """
        if not analysis_db_path or not Path(analysis_db_path).exists():
            return []

        hits: list = []
        ms2_df = analysis_db.load_ms2_annotations_for_feature(analysis_db_path, feature_id)
        if not ms2_df.empty:
            best_per_compound = ms2_df.drop_duplicates(subset="inchikey", keep="first")
            best_per_compound = best_per_compound.head(max(top_n, 0)).copy()
            best_per_compound["kind"] = "ms2"
            hits.extend(_dataframe_to_records(best_per_compound))

        precursor_df = analysis_db.load_precursor_annotations_for_feature(
            analysis_db_path, feature_id
        )
        if not precursor_df.empty:
            n_candidates = len(precursor_df)
            # How many candidates share each tie group, before the Top-N cut.
            precursor_df["n_tied"] = precursor_df.groupby(
                precursor_df["tie_rank"].fillna(precursor_df["rank"])
            )["id"].transform("size")
            precursor_df = precursor_df.head(max(top_n, 0)).copy()
            precursor_df["n_feature_candidates"] = n_candidates
            precursor_df["id"] = "p" + precursor_df["id"].astype(str)
            precursor_df["kind"] = "precursor"
            hits.extend(_dataframe_to_records(precursor_df))

        predicted_df = analysis_db.load_top_predicted_formulas_for_feature(
            analysis_db_path, feature_id, top_n
        )
        if not predicted_df.empty:
            predicted_df = predicted_df.copy()
            predicted_df["id"] = -predicted_df["id"]
            predicted_df["kind"] = "predicted"
            hits.extend(_dataframe_to_records(predicted_df))

        return hits

    @Slot(str, int)
    def requestBestScanPlot(self, raw_db_path: str, scan_id: int) -> None:
        """Start rendering one raw MS2 scan (a feature's best scan, from
        `requestAnnotationTable`'s `ms2_*` columns) on a background thread;
        the `data:` URI arrives via `bestScanPlotReady(uri)`, "" on failure.

        Background, unlike `getBasicMirrorPlotImage`: this reads the
        sample's raw database, which can sit on a slow network share.
        """
        worker = TableQueryWorker(
            functools.partial(_render_best_scan_png, scan_id=scan_id), raw_db_path, self
        )
        worker.succeeded.connect(self._onBestScanPlotSucceeded)
        worker.failed.connect(self._onBestScanPlotFailed)
        self._best_scan_plot_worker = worker
        worker.start()

    @Slot(object)
    def _onBestScanPlotSucceeded(self, uri: str) -> None:
        if self.sender() is not self._best_scan_plot_worker:
            return  # superseded by a newer request — discard
        self.bestScanPlotReady.emit(uri)

    @Slot(str)
    def _onBestScanPlotFailed(self, message: str) -> None:
        if self.sender() is not self._best_scan_plot_worker:
            return  # superseded by a newer request — discard
        logger.warning("best-scan plot failed: %s", message)
        self.bestScanPlotReady.emit("")

    @Slot(str, int, result=str)
    def getBasicMirrorPlotImage(self, analysis_db_path: str, annotation_id: int) -> str:
        """A fast, static empirical-vs-library mirror plot for one
        `ms2_annotations` row, always from the stored *filtered* spectra
        — the Annotations section's inline default view.

        Always synchronous: unlike `requestMirrorPlot`, this never touches
        a raw per-sample database or a library file (only the already
        max-normalised, pre-computed arrays `ms2_annotations` itself
        stores), so there's no slow/remote I/O risk to move off the GUI
        thread.

        Args:
            analysis_db_path: The analysis' SQLite database.
            annotation_id: Primary key in `ms2_annotations`.

        Returns:
            A `data:image/png;base64,...` URI for a QML `Image.source`.
            A 1x1 transparent PNG data URI on any failure (unknown id, no
            stored filtered spectra) instead — the panel's own stats
            text (from `getFeatureTopHits`' data) already explains why,
            so this just needs to not be a broken image.
        """
        try:
            data = Plotter().get_annotation_spectra(analysis_db_path, annotation_id)
            png = render_mirror_plot_png(
                data["empirical_mz"], data["empirical_intensity"],
                data["library_mz"], data["library_intensity"],
                data["fragment_ppm_tolerance"],
            )
        except (ValueError, OSError) as e:
            logger.warning("basic mirror plot for annotation %s failed: %s", annotation_id, e)
            # 1x1 transparent PNG — a valid image, just nothing to see.
            png = base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
                "+A8AAQUBAScY42YAAAAASUVORK5CYII="
            )
        return "data:image/png;base64," + base64.b64encode(png).decode("ascii")

    @Slot(str, int, result=dict)
    def getAnnotationMetadata(self, analysis_db_path: str, annotation_id: int) -> dict:
        """Compound identity/score/coverage/peak-count fields for one
        `ms2_annotations` row — the mirror-plot detail window's side
        table (`MirrorPlotDetailWindow.qml`), kept next to the plot
        instead of packed into its title/corner annotation.

        Always synchronous, like `getBasicMirrorPlotImage`: these fields
        are plain columns (plus one resolved config value), independent
        of `emp_source`/`lib_source`, so nothing here touches a raw
        per-sample database or library file.

        Args:
            analysis_db_path: The analysis' SQLite database.
            annotation_id: Primary key in `ms2_annotations`.

        Returns:
            See `Plotter.get_annotation_metadata`; an all-empty/None dict
            (same shape) when `analysis_db_path` doesn't exist or
            `annotation_id` isn't found.
        """
        if not analysis_db_path or not Path(analysis_db_path).exists():
            return dict(_EMPTY_ANNOTATION_METADATA)
        try:
            return Plotter().get_annotation_metadata(analysis_db_path, annotation_id)
        except ValueError as e:
            logger.warning("annotation metadata for id %s failed: %s", annotation_id, e)
            return dict(_EMPTY_ANNOTATION_METADATA)

    @Slot(str, int, str, str)
    def requestMirrorPlot(
        self, analysis_db_path: str, annotation_id: int, emp_source: str, lib_source: str
    ) -> None:
        """Start building the interactive mirror plot's data for one
        `ms2_annotations` row on a background thread; the result
        (`mirror_plot_data`, or `{"error": message}`) arrives via
        `mirrorPlotReady(dict)`.

        Fire-and-forget rather than a return value — see the class
        docstring for why (`emp_source`/`lib_source="raw"` can mean slow
        or remote file I/O, which must not block the GUI thread). Used by
        the Annotations section's "view details" popup, which shows a
        loading state (`LoadingOverlay`) until `mirrorPlotReady` fires.

        Args:
            analysis_db_path: The analysis' SQLite database.
            annotation_id: Primary key in `ms2_annotations`.
            emp_source: `"filtered"` or `"raw"` — which empirical spectrum
                to show (see `Plotter.plot_ms2_annotation`).
            lib_source: `"filtered"` or `"raw"` — which library spectrum
                to show.
        """
        worker = MirrorPlotWorker(analysis_db_path, annotation_id, emp_source, lib_source, self)
        worker.succeeded.connect(self._onMirrorPlotSucceeded)
        worker.failed.connect(self._onMirrorPlotFailed)
        self._mirror_plot_worker = worker
        worker.start()

    @Slot(object)
    def _onMirrorPlotSucceeded(self, data: dict) -> None:
        if self.sender() is not self._mirror_plot_worker:
            return  # superseded by a newer request — discard
        self.mirrorPlotReady.emit(data)

    @Slot(str)
    def _onMirrorPlotFailed(self, message: str) -> None:
        if self.sender() is not self._mirror_plot_worker:
            return  # superseded by a newer request — discard
        self.mirrorPlotReady.emit({"error": message})

    @Slot(str, result=list)
    def getSamples(self, analysis_db_path: str) -> list:
        """Samples for the MS1 spectra section's sample selector.

        Args:
            analysis_db_path: The analysis' SQLite database.

        Returns:
            Records (`sample_id`, `name`, `polarity`) from
            `analysis_db.load_samples`.
        """
        if not analysis_db_path or not Path(analysis_db_path).exists():
            return []
        return _dataframe_to_records(analysis_db.load_samples(analysis_db_path))

    @Slot(str, str, int, result=dict)
    def getSpectrumData(self, analysis_db_path: str, run_id: str, sample_id: int) -> dict:
        """One sample's filtered MS1 spectrum as plain data for
        MS1SpectraSection.qml's stick plot (Qt Graphs, ADR 51).

        Args:
            analysis_db_path: The analysis' SQLite database.
            run_id: The run's id (== `AnalysisModel.runId`) — spectra are
                attributed to the run that produced them.
            sample_id: Row id in the analysis `samples` table.

        Returns:
            `{"groups": [{"key", "name", "peaks": [[mz, intensity], ...]},
            ...]}`, one group per feature category (`spectrum_groups`), or
            `{"groups": [], "error": "<message>"}` if no filtered spectrum
            was ever saved for that sample.
        """
        try:
            mz, intensity = load_aggregated_spectra(
                analysis_db_path,
                run_id=run_id,
                command_name="filter_spectra",
                sample_id=sample_id,
            )
        except (TypeError, sqlite3.OperationalError) as e:
            logger.warning(
                "spectrum for sample %s (run %s) not found: %s", sample_id, run_id, e
            )
            return {"groups": [], "error": "No spectrum found for this sample."}

        categories = _categorize_peaks(
            mz, analysis_db.load_feature_categories(analysis_db_path)
        )
        return {"groups": spectrum_groups(mz, intensity, categories)}

    @Slot(str, float, int, result=dict)
    def getFeatureDetail(self, analysis_db_path: str, mz: float, top_n: int) -> dict:
        """Everything the MS1 spectra section's side panel shows for one click.

        Maps `mz` to the nearest feature, then looks up which samples have
        it (present vs. filtered-out/absent — not distinguished), its MS2
        count, and its top-N annotation hits (best row per distinct
        compound, by rank/score; empty when annotation never ran).

        Args:
            analysis_db_path: The analysis' SQLite database.
            mz: The clicked m/z.
            top_n: How many annotation hits to include, user-selectable.

        Returns:
            `{"feature_id", "mz", "samples_present", "samples_absent",
            "n_ms2", "top_hits"}`, or `{}` when there are no features at all
            (or the db path doesn't exist).
        """
        if not analysis_db_path or not Path(analysis_db_path).exists():
            return {}

        feature = analysis_db.find_nearest_feature(analysis_db_path, mz)
        if feature is None:
            return {}

        members = feature["members"]
        samples_present = sorted(name for name, idx in members.items() if idx is not None)
        samples_absent = sorted(name for name, idx in members.items() if idx is None)
        n_ms2 = analysis_db.load_feature_ms2_count(analysis_db_path, feature["feature_id"])

        candidates = analysis_db.load_ms2_annotations_for_feature(
            analysis_db_path, feature["feature_id"]
        )
        top_hits = []
        if not candidates.empty:
            best_per_compound = candidates.drop_duplicates(
                subset="inchikey", keep="first"
            )
            top_hits = _dataframe_to_records(best_per_compound.head(max(top_n, 0)))

        return {
            "feature_id": feature["feature_id"],
            "mz": feature["mz"],
            "samples_present": samples_present,
            "samples_absent": samples_absent,
            "n_ms2": n_ms2,
            "top_hits": top_hits,
        }

    @Slot(str)
    def requestFeatureList(self, analysis_db_path: str) -> None:
        """Start fetching every feature for the Visual Inspection section's
        feature selector on a background thread; the result arrives via
        `featureListReady(features)`.

        Single-slot cache, keyed by `analysis_db_path` — same pattern and
        motivation as `getSpatialCorrelationModel`'s: opening Visual
        Inspection re-fetches this on every tab switch away and back
        (its `Loader` is lazy, destroyed each time), which is wasted
        work for a list that essentially never changes mid-analysis.
        `AnalysisPage.qml` also calls this once, eagerly, right when the
        analysis is opened (`onAnalysisChanged`) — by the time the user
        actually visits Visual Inspection, this is usually already warm.
        A cache hit still emits `featureListReady` (just synchronously,
        no worker thread), so callers don't need to know which case
        happened. Known limitation: a `compound_name` set by "Predict
        formula" (ADR 27) after this cache is warm won't show up here
        until a different analysis is opened — `AnnotationsSection.qml`'s
        own table has its own, separate refresh path for that; this
        cache doesn't hook into it yet.

        Fire-and-forget rather than a return value — see the class
        docstring for why. Used by `HeatmapControlsPanel.qml`, which shows
        a loading state until `featureListReady` fires.

        Args:
            analysis_db_path: The analysis' SQLite database.

        `featureListReady`'s features (`feature_id`, `mz`, `compound_name`)
        come from `analysis_db.load_feature_list`, ordered by `mz`.
        """
        if not analysis_db_path or not Path(analysis_db_path).exists():
            self.featureListReady.emit([])
            return
        rel_dir = self._active_rel_dir(analysis_db_path)
        cache_key = (analysis_db_path, rel_dir)
        if cache_key == self._feature_list_cache_path:
            self.featureListReady.emit(self._feature_list_cache_rows)
            return
        # An imported dataset (ADR 49) lists only the features its own
        # files still contain — see _load_dataset_feature_list.
        query = (
            functools.partial(_load_dataset_feature_list, rel_dir=rel_dir)
            if rel_dir else analysis_db.load_feature_list
        )
        worker = TableQueryWorker(query, analysis_db_path, self)
        worker.succeeded.connect(self._onFeatureListSucceeded)
        worker.failed.connect(self._onFeatureListFailed)
        self._feature_list_worker = worker
        self._feature_list_worker_path = cache_key
        worker.start()

    @Slot(object)
    def _onFeatureListSucceeded(self, df: pd.DataFrame) -> None:
        if self.sender() is not self._feature_list_worker:
            return  # superseded by a newer request — discard
        rows = [] if df.empty else _dataframe_to_records(df)
        self._feature_list_cache_path = self._feature_list_worker_path
        self._feature_list_cache_rows = rows
        self.featureListReady.emit(rows)

    @Slot(str)
    def _onFeatureListFailed(self, message: str) -> None:
        if self.sender() is not self._feature_list_worker:
            return  # superseded by a newer request — discard
        logger.warning("feature list load failed: %s", message)
        self.featureListReady.emit([])

    # -----------------------------------------------------------------
    # Imported analyzed .h5ad datasets (ADR 49)
    # -----------------------------------------------------------------

    def _get_active_dataset_id(self) -> int:
        return int(self._active_dataset["id"]) if self._active_dataset else -1

    #: The imported dataset Visual Inspection / Spatial Correlation
    #: currently read, `-1` for the pipeline output.
    activeDatasetId = Property(int, _get_active_dataset_id, notify=activeDatasetChanged)

    @Slot(str, result=list)
    def getImportedDatasets(self, analysis_db_path: str) -> list:
        """Every imported dataset of this analysis, for the workspace's
        dataset switcher.

        Returns:
            Records (`id`, `name`, `source_path`, `source_kind`,
            `imported_at`), oldest first; `[]` when there are none.
        """
        if not analysis_db_path or not Path(analysis_db_path).exists():
            return []
        df = analysis_db.load_imported_datasets(analysis_db_path)
        if df.empty:
            return []
        return _dataframe_to_records(
            df[["id", "name", "source_path", "source_kind", "imported_at"]]
        )

    @Slot(str, int)
    def setActiveDataset(self, analysis_db_path: str, dataset_id: int) -> None:
        """Switch which data Visual Inspection / Spatial Correlation read:
        `dataset_id = -1` for the pipeline output, else one of this
        analysis' imported datasets. It points both heatmap-reading image
        providers at it, and it drops the feature-list and
        spatial-correlation caches, because those are keyed per dataset
        and are rebuilt on the next request. Emits `activeDatasetChanged`
        only when something actually changed.
        """
        info = None
        if dataset_id >= 0 and analysis_db_path and Path(analysis_db_path).exists():
            info = analysis_db.load_imported_dataset(analysis_db_path, dataset_id)
        if info is not None and not info.get("samples"):
            info = None
        previous = (self._active_db_path, self._get_active_dataset_id())
        self._active_db_path = analysis_db_path if info is not None else None
        self._active_dataset = info
        rel_dir = self._active_rel_dir(analysis_db_path)
        self.heatmap_provider.setAnalysisDbPath(analysis_db_path, rel_dir)
        self.he_overlay_provider.setAnalysisDbPath(analysis_db_path, rel_dir)
        if previous != (self._active_db_path, self._get_active_dataset_id()):
            self.activeDatasetChanged.emit()

    @Slot(result=dict)
    def getActiveDatasetInfo(self) -> dict:
        """The active imported dataset (`id`, `name`, `source_path`,
        `layers`, `samples` names), or `{}` for the pipeline output — for
        the layer picker and the Moran's I controls."""
        if self._active_dataset is None:
            return {}
        info = self._active_dataset
        return {
            "id": int(info["id"]),
            "name": info["name"],
            "source_path": info["source_path"],
            "layers": list(info["layers"]),
            "samples": [s["name"] for s in info["samples"]],
        }

    @Slot(str, result=list)
    def getInspectionSamples(self, analysis_db_path: str) -> list:
        """The samples Visual Inspection shows: every analysis sample for
        the pipeline output, only the covered ones for an imported
        dataset. It uses the same record shape as `getSamples` (which stays
        all-samples, because the MS1 section's selector is dataset-agnostic).
        """
        if not analysis_db_path or not Path(analysis_db_path).exists():
            return []
        return _dataframe_to_records(
            _inspection_samples_frame(analysis_db_path, self._active_rel_dir(analysis_db_path))
        )

    @Slot(str)
    def inspectH5ad(self, path: str) -> None:
        """Start reading a candidate source file's metadata (backed mode,
        never `X`) on a background thread. The result arrives via
        `h5adInspected(dict)` (`H5adInspection.to_dict`) or
        `h5adInspectFailed(message)`."""
        worker = TableQueryWorker(
            lambda p: imported_dataset.inspect_h5ad(p).to_dict(), path, self
        )
        worker.succeeded.connect(self._onH5adInspected)
        worker.failed.connect(self._onH5adInspectFailed)
        self._h5ad_inspect_worker = worker
        worker.start()

    @Slot(object)
    def _onH5adInspected(self, info: dict) -> None:
        if self.sender() is not self._h5ad_inspect_worker:
            return  # superseded by a newer request — discard
        self.h5adInspected.emit(info)

    @Slot(str)
    def _onH5adInspectFailed(self, message: str) -> None:
        if self.sender() is not self._h5ad_inspect_worker:
            return
        self.h5adInspectFailed.emit(message)

    def _start_dataset_task(self, task_fn, on_succeeded, on_failed) -> bool:
        if self._dataset_task_worker is not None and self._dataset_task_worker.isRunning():
            on_failed("Another import, Moran's I or normalization is still running.")
            return False
        worker = DatasetTaskWorker(task_fn, self)
        worker.progress.connect(self.datasetTaskProgress)
        worker.succeeded.connect(on_succeeded)
        worker.failed.connect(on_failed)
        self._dataset_task_worker = worker
        worker.start()
        return True

    @Slot(str, str, str, str, "QVariantMap")
    def importH5ad(
        self, analysis_db_path: str, source_path: str, name: str,
        sample_column: str, sample_map: dict,
    ) -> None:
        """Import `source_path` as a dataset of this analysis on a
        background thread (`imported_dataset.import_analyzed_h5ad`).
        Progress arrives via `datasetTaskProgress`, and the result via
        `datasetImportFinished(dataset_id, message)` /
        `datasetImportFailed(message)`.

        Args:
            sample_column: `""` for a single-sample file.
            sample_map: Source label -> analysis sample name, only the
                samples to import (QML drops unmapped ones).
        """
        self._import_db_path = analysis_db_path
        task = functools.partial(
            self._run_import, analysis_db_path, source_path, name,
            sample_column or None, {str(k): str(v) for k, v in dict(sample_map).items()},
        )
        self._start_dataset_task(
            task, self._onDatasetImportSucceeded, self.datasetImportFailed.emit
        )

    @staticmethod
    def _run_import(db, source, name, sample_column, sample_map, progress):
        return imported_dataset.import_analyzed_h5ad(
            db, source, name=name, sample_column=sample_column,
            sample_map=sample_map, progress=progress,
        )

    @Slot(object)
    def _onDatasetImportSucceeded(self, result) -> None:
        if self.sender() is not self._dataset_task_worker:
            return
        self.importedDatasetsChanged.emit(self._import_db_path)
        self.datasetImportFinished.emit(
            int(result.dataset_id),
            f"Imported {result.n_samples} sample(s), {result.n_pixels} pixels.",
        )

    @Slot(str, int, result=bool)
    def deleteImportedDataset(self, analysis_db_path: str, dataset_id: int) -> bool:
        """Delete an imported dataset's files and rows. The user's source
        file is kept. If it was the active dataset, the view falls back to
        the pipeline output first, so no open file handle points into the
        folder being removed."""
        if not analysis_db_path or not Path(analysis_db_path).exists():
            return False
        if self._get_active_dataset_id() == dataset_id and self._active_db_path == analysis_db_path:
            self.setActiveDataset(analysis_db_path, -1)
        removed = imported_dataset.delete_imported_dataset(analysis_db_path, dataset_id)
        if removed:
            self.importedDatasetsChanged.emit(analysis_db_path)
        return removed

    @Slot(str, str, bool, bool)
    def computeDatasetMoran(
        self, analysis_db_path: str, layer: str, run_global: bool, run_local: bool
    ) -> None:
        """Moran's I (global and/or local) over the active dataset (the
        pipeline output or an imported one), on a background thread (see
        `imported_dataset.compute_dataset_spatial_correlation`). Progress
        arrives via `datasetTaskProgress`. On success, the
        spatial-correlation caches are dropped, and
        `datasetMoranFinished(message)` fires so the section re-requests
        its models. On failure, `datasetMoranFailed(message)` fires."""
        dataset_id = (
            self._get_active_dataset_id()
            if self._active_rel_dir(analysis_db_path) is not None else None
        )
        task = functools.partial(
            self._run_dataset_moran, analysis_db_path, dataset_id,
            layer, run_global, run_local,
        )
        self._start_dataset_task(
            task, self._onDatasetMoranSucceeded, self.datasetMoranFailed.emit
        )

    @staticmethod
    def _run_dataset_moran(db, dataset_id, layer, run_global, run_local, progress):
        return imported_dataset.compute_dataset_spatial_correlation(
            db, dataset_id, layer=layer, run_global=run_global,
            run_local=run_local, progress=progress,
        )

    @Slot(object)
    def _onDatasetMoranSucceeded(self, n_samples) -> None:
        if self.sender() is not self._dataset_task_worker:
            return
        self._drop_spatial_correlation_caches()
        self.heatmap_provider.clearCaches()  # the files changed on disk
        self.datasetMoranFinished.emit(f"Moran's I computed for {n_samples} sample(s).")

    def _drop_spatial_correlation_caches(self) -> None:
        self._spatial_correlation_model_path = None
        self._spatial_correlation_model_rel_dir = None
        self._local_moran_summary_model_path = None
        self._local_moran_summary_model_rel_dir = None

    # -----------------------------------------------------------------
    # Target list after the run (ADR 60)
    # -----------------------------------------------------------------

    @Slot(str, list, str, list, float, result=dict)
    def previewPostRunTargetList(
        self, analysis_db_path: str, paths: list, polarity: str, adducts: list,
        match_ppm: float,
    ) -> dict:
        """Match a target list against this analysis' features without
        saving: `{"rows": [...]}` for the review table (see
        `target_list.preview_post_run_matches`), or `{"error": message}`.
        Synchronous: parsing a list and an m/z search is fast."""
        try:
            rows = target_list.preview_post_run_matches(
                analysis_db_path, [str(p) for p in paths], polarity,
                [str(a) for a in adducts], float(match_ppm),
            )
        except (target_list.TargetListError, OSError, ValueError) as e:
            return {"error": str(e)}
        return {"rows": rows}

    @Slot(str, str, list, "QVariantMap", result=str)
    def applyPostRunTargetList(
        self, analysis_db_path: str, run_id: str, rows: list, settings: dict
    ) -> str:
        """Save the kept rows; `""` on success, else an error message."""
        try:
            target_list.apply_post_run_matches(
                analysis_db_path, run_id, [dict(r) for r in rows], dict(settings)
            )
        except (target_list.TargetListError, sqlite3.Error, KeyError, ValueError) as e:
            return str(e)
        self._on_target_list_matches_changed(analysis_db_path)
        return ""

    @Slot(str, result=int)
    def countPostRunTargetList(self, analysis_db_path: str) -> int:
        return analysis_db.count_command_target_list_matches(
            analysis_db_path, target_list.POST_RUN_COMMAND
        )

    @Slot(str, result=int)
    def removePostRunTargetList(self, analysis_db_path: str) -> int:
        """Undo every post-run match (the pipeline's own are kept)."""
        n = analysis_db.delete_command_target_list_rows(
            analysis_db_path, target_list.POST_RUN_COMMAND
        )
        self._on_target_list_matches_changed(analysis_db_path)
        return n

    def _on_target_list_matches_changed(self, analysis_db_path: str) -> None:
        # Feature names changed: drop every cache that shows them.
        self._feature_list_cache_path = None
        self._summary_cache_path = None
        self._drop_spatial_correlation_caches()
        self._refresh_var_annotations(analysis_db_path)
        self.targetListMatchesChanged.emit(analysis_db_path)

    # -----------------------------------------------------------------
    # Re-annotate a finished analysis, with history (ADR 63)
    # -----------------------------------------------------------------

    @Slot(str, result="QVariantMap")
    def getMs2Background(self, analysis_db_path: str) -> dict:
        """`analysis_db.load_ms2_background` — the Re-annotate dialog's MS2
        cleaning tab. A few hundred rows at most, so synchronous."""
        if not analysis_db_path or not Path(analysis_db_path).exists():
            return {"ran": False, "rows": []}
        return analysis_db.load_ms2_background(analysis_db_path)

    @Slot(str, "QVariantMap", result="QVariantMap")
    def getAnnotationSettings(self, analysis_db_path: str, run_config: dict) -> dict:
        """The MS2 settings now in effect, `{group: {field: value}}` for
        `reannotation.MS2_GROUPS`: the newest history entry's, else the
        run config's (with today's defaults for fields it predates)."""
        history = reannotation.load_history(analysis_db_path) if analysis_db_path else []
        if history:
            return history[-1]["settings"]
        return reannotation.Ms2Settings.from_sections(dict(run_config)).to_dict()

    @Slot(str, result=list)
    def getAnnotationHistory(self, analysis_db_path: str) -> list:
        return reannotation.load_history(analysis_db_path) if analysis_db_path else []

    @Slot(str, str, "QVariantMap", "QVariantMap", str)
    def reannotate(
        self, analysis_db_path: str, run_id: str, settings: dict, run_config: dict,
        start_date: str,
    ) -> None:
        """Re-run the MS2 stages with `settings` on a background thread.
        Progress (one step per stage) arrives via `datasetTaskProgress`, the
        result via `annotationTaskFinished(message)` / `annotationTaskFailed`."""
        self._annotation_task_db = analysis_db_path
        task = functools.partial(
            self._run_reannotation, analysis_db_path, run_id,
            coerce_config_values(dict(settings)), dict(run_config), start_date,
        )
        self._start_dataset_task(
            task, self._onAnnotationTaskSucceeded, self.annotationTaskFailed.emit
        )

    @Slot(str, int, "QVariantMap", str)
    def restoreAnnotation(
        self, analysis_db_path: str, entry_id: int, run_config: dict, start_date: str
    ) -> None:
        """Make history entry `entry_id` current again, on a background
        thread; same result signals as `reannotate`."""
        self._annotation_task_db = analysis_db_path
        task = functools.partial(
            self._run_annotation_restore, analysis_db_path, int(entry_id),
            dict(run_config), start_date,
        )
        self._start_dataset_task(
            task, self._onAnnotationTaskSucceeded, self.annotationTaskFailed.emit
        )

    @Slot(str, int, result=str)
    def deleteAnnotationHistoryEntry(self, analysis_db_path: str, entry_id: int) -> str:
        """`""` on success, else an error message."""
        try:
            reannotation.delete_history_entry(analysis_db_path, int(entry_id))
        except (ValueError, OSError, sqlite3.Error) as e:
            return str(e)
        return ""

    @staticmethod
    def _pipeline_annotation(run_config: dict) -> dict:
        return reannotation.Ms2Settings.from_sections(run_config).to_dict()

    @staticmethod
    def _run_reannotation(db, run_id, settings, run_config, start_date, progress):
        steps = list(reannotation.MS2_STAGES)

        def on_step(step, status):
            if status != "started":
                progress(steps.index(step) + 1, len(steps), step)

        align_ppm = (run_config.get("align") or {}).get("align_ppm")
        entry_id = reannotation.reannotate(
            db, reannotation.Ms2Settings.from_sections(settings, align_ppm), run_id,
            pipeline_settings=AnalysisBridge._pipeline_annotation(run_config),
            pipeline_date=start_date or None, report=run_config.get("report"),
            on_step=on_step,
        )
        n = reannotation.load_history(db)[-1]["n_annotated_features"]
        return f"Re-annotated (annotation #{entry_id}): {n} feature(s) with a library match."

    @staticmethod
    def _run_annotation_restore(db, entry_id, run_config, start_date, progress):
        new_id = reannotation.restore_annotation(
            db, entry_id, pipeline_settings=AnalysisBridge._pipeline_annotation(run_config),
            pipeline_date=start_date or None, report=run_config.get("report"),
        )
        return f"Annotation #{entry_id} restored (now annotation #{new_id})."

    @Slot(object)
    def _onAnnotationTaskSucceeded(self, message: str) -> None:
        if self.sender() is not self._dataset_task_worker:
            return
        # Names, outcomes and .var all changed — same refresh as a
        # post-run target list.
        self._on_target_list_matches_changed(self._annotation_task_db)
        self.annotationTaskFinished.emit(message)

    # -----------------------------------------------------------------
    # Normalize to a feature after the run (ADR 61)
    # -----------------------------------------------------------------

    @Slot(str, str, int, float)
    def normalizeToFeature(
        self, analysis_db_path: str, run_id: str, feature_id: int, mz: float
    ) -> None:
        """Add `layers["feature_<id>"]` (every sample + `merged.h5ad`) on a
        background thread (`tic_normalization.add_feature_normalized_layer`).
        Progress arrives via `datasetTaskProgress`, the result via
        `featureNormalizationFinished(layer, message)` /
        `featureNormalizationFailed(message)`."""
        self._normalization_db_path = analysis_db_path
        task = functools.partial(
            self._run_feature_normalization, analysis_db_path, run_id,
            int(feature_id), float(mz),
        )
        self._start_dataset_task(
            task, self._onFeatureNormalizationSucceeded,
            self.featureNormalizationFailed.emit,
        )

    @staticmethod
    def _pipeline_sample_paths(db) -> dict:
        paths = {
            name: imported_dataset.sample_h5ad_path(db, name)
            for name in analysis_db.load_samples(db).get("name", [])
        }
        return {name: path for name, path in paths.items() if path.exists()}

    @staticmethod
    def _run_feature_normalization(db, run_id, feature_id, mz, progress):
        paths = AnalysisBridge._pipeline_sample_paths(db)
        if not paths:
            raise ValueError("This analysis has no sample .h5ad files.")
        result = tic_normalization.add_feature_normalized_layer(
            paths, Path(db).parent / "merged.h5ad", feature_id, mz, progress=progress
        )
        analysis_db.log_command(
            db, "normalize_feature",
            {"feature_id": feature_id, "mz": mz, "layer": result.layer_name},
            run_id=run_id,
        )
        return result

    @Slot(object)
    def _onFeatureNormalizationSucceeded(self, result) -> None:
        if self.sender() is not self._dataset_task_worker:
            return
        self.heatmap_provider.clearCaches()  # the files changed on disk
        message = f"Layer {result.layer_name} added to {result.n_samples} sample(s)"
        message += " and merged.h5ad." if result.merged_written else "."
        if result.n_zero_pixels:
            pct = 100.0 * result.n_zero_pixels / max(result.n_pixels, 1)
            message += (
                f" {result.n_zero_pixels} of {result.n_pixels} pixels ({pct:.0f}%) "
                "have no signal for this feature and are 0 in the new layer."
            )
        self.featureLayersChanged.emit(self._normalization_db_path)
        self.featureNormalizationFinished.emit(result.layer_name, message)

    @Slot(str, str, int, result=str)
    def removeFeatureLayer(self, analysis_db_path: str, run_id: str, feature_id: int) -> str:
        """Drop `feature_<id>` from every sample file and `merged.h5ad`;
        `""` on success, else an error message. Synchronous: deleting two
        HDF5 elements per file is fast."""
        if self._dataset_task_worker is not None and self._dataset_task_worker.isRunning():
            return "Another import, Moran's I or normalization is still running."
        try:
            tic_normalization.remove_feature_layer(
                self._pipeline_sample_paths(analysis_db_path),
                Path(analysis_db_path).parent / "merged.h5ad", int(feature_id),
            )
            analysis_db.log_command(
                analysis_db_path, "remove_feature_layer",
                {"feature_id": int(feature_id)}, run_id=run_id,
            )
        except (OSError, sqlite3.Error) as e:
            return str(e)
        self.heatmap_provider.clearCaches()
        self.featureLayersChanged.emit(analysis_db_path)
        return ""

    @Slot(str, result=list)
    def getFeatureLayers(self, analysis_db_path: str) -> list:
        """The pipeline output's `feature_<id>` layers: `[{"layer",
        "feature_id", "mz"}]`, by m/z. Read from the first sample file's
        layer names, so it is exactly what the heatmap can show."""
        if not analysis_db_path or not Path(analysis_db_path).exists():
            return []
        for name in analysis_db.load_samples(analysis_db_path).get("name", []):
            path = imported_dataset.sample_h5ad_path(analysis_db_path, name)
            if path.exists():
                with h5py.File(path, "r") as f:
                    layers = list(f["layers"].keys()) if "layers" in f else []
                break
        else:
            return []
        ids = [int(l[len("feature_"):]) for l in layers
               if l.startswith("feature_") and l[len("feature_"):].isdigit()]
        if not ids:
            return []
        with sqlite3.connect(f"file:{analysis_db_path}?mode=ro", uri=True) as con:
            mzs = dict(con.execute(
                f"SELECT feature_id, mz FROM features WHERE feature_id IN "
                f"({','.join('?' * len(ids))})", ids,
            ).fetchall())
        rows = [{"layer": tic_normalization.feature_layer_name(i), "feature_id": i,
                 "mz": float(mzs.get(i, float("nan")))} for i in ids]
        return sorted(rows, key=lambda r: r["mz"])

    # -----------------------------------------------------------------
    # ROI Design
    # -----------------------------------------------------------------

    @staticmethod
    def _sample_h5ad_path(
        analysis_db_path: str, sample_name: str, rel_dir: str | None = None
    ) -> Path:
        """Where one sample's `.h5ad` lives — `core.imported_dataset.
        sample_h5ad_path`, the one resolver `HeatmapImageProvider` also
        uses (a drifted duplicate would silently defeat
        `HeatmapImageProvider.invalidate`'s cache eviction). `rel_dir`
        selects an imported dataset (ADR 49); `None` = pipeline output."""
        return imported_dataset.sample_h5ad_path(analysis_db_path, sample_name, rel_dir)

    def _active_rel_dir(self, analysis_db_path: str) -> str | None:
        """The active imported dataset's folder if `analysis_db_path` is
        the analysis it belongs to, else `None` (pipeline output)."""
        if self._active_dataset is not None and analysis_db_path == self._active_db_path:
            return self._active_dataset["rel_dir"]
        return None

    def _inspection_h5ad_path(self, analysis_db_path: str, sample_name: str) -> Path:
        """A sample's `.h5ad` for whichever dataset is currently active —
        what every Visual Inspection / ROI slot reads and writes."""
        return self._sample_h5ad_path(
            analysis_db_path, sample_name, self._active_rel_dir(analysis_db_path)
        )

    @staticmethod
    def _merged_h5ad_path(analysis_db_path: str) -> Path:
        return Path(analysis_db_path).parent / "merged.h5ad"

    @Slot(str, result=list)
    def getRois(self, analysis_db_path: str) -> list:
        """The analysis-wide ROI catalog — every registered name/color,
        for ROI Design's name picker and a "Show ROIs" legend.

        Args:
            analysis_db_path: The analysis' SQLite database.

        Returns:
            Records (`id`, `name`, `color`, `created_at`) from
            `analysis_db.load_rois`, ordered by name. Empty when the db
            path doesn't exist or there are no ROIs yet.
        """
        if not analysis_db_path or not Path(analysis_db_path).exists():
            return []
        return _dataframe_to_records(analysis_db.load_rois(analysis_db_path))

    @Slot(str, result=str)
    def nextRoiColor(self, analysis_db_path: str) -> str:
        """A suggested default color for a brand-new ROI —
        `heatmap.category_color` at the catalog's current row count, so
        successive new ROIs cycle through the same tab20 palette discrete
        `obs` categories already use. Purely a suggestion: ROI Design seeds
        its color picker from this but the user can override it before
        saving (see `saveRoi`'s "existing name wins" rule for what actually
        gets persisted).

        Args:
            analysis_db_path: The analysis' SQLite database.

        Returns:
            `"#rrggbb"`.
        """
        if not analysis_db_path or not Path(analysis_db_path).exists():
            return category_color(0)
        existing = analysis_db.load_rois(analysis_db_path)
        return category_color(len(existing))

    @Slot(str, str, result=list)
    def getSampleRois(self, analysis_db_path: str, sample_name: str) -> list:
        """One sample's saved ROI borders — for ROI Design's "already
        drawn" list and Visual Inspection's read-only "Show ROIs" overlay.

        Args:
            analysis_db_path: The analysis' SQLite database.
            sample_name: Which sample to read.

        Returns:
            `[{"name", "color", "vertices": [[col, row], ...]}, ...]`, from
            that sample's own `.h5ad` (`roi.load_sample_rois` — not the
            analysis-wide catalog, since geometry is per-sample). `[]` if
            the sample's h5ad doesn't exist or has no ROIs.
        """
        if not analysis_db_path or not Path(analysis_db_path).exists():
            return []
        h5ad_path = self._inspection_h5ad_path(analysis_db_path, sample_name)
        if not h5ad_path.exists():
            return []
        rois = roi.load_sample_rois(h5ad_path)
        return [
            {"name": name, "color": info["color"], "vertices": info["vertices"]}
            for name, info in rois.items()
        ]

    @Slot(str, str, str, str, list, result=dict)
    def saveRoi(
        self, analysis_db_path: str, sample_name: str, name: str, color: str,
        vertices: list,
    ) -> dict:
        """Save one ROI on one sample: writes the polygon + per-pixel mask
        into that sample's own `.h5ad` (`roi.save_roi_to_sample`), keeps
        `merged.h5ad` in sync if it exists (`roi.sync_roi_to_merged`), and
        registers `name`/`color` in the analysis-wide catalog the first
        time this name is used.

        `name` may already exist in the catalog (drawing the same named
        ROI on a second sample) — in that case its registered `color`
        wins over whatever `color` this call was passed, keeping the
        name's color consistent across every sample it's drawn on.

        Args:
            analysis_db_path: The analysis' SQLite database.
            sample_name: Which sample's `.h5ad` to write into.
            name: The ROI's name.
            color: `"#rrggbb"` — only takes effect if `name` is new.
            vertices: `[[col, row], ...]`, grid-index space, >= 3 points.

        With an imported dataset active (ADR 49), the ROI goes into that
        dataset's own per-sample file instead, and its obs column is
        written back into the user's source `.h5ad` (`merged.h5ad` is
        not touched). A failed write-back (source moved, or locked by a
        notebook) doesn't fail the save — it's reported in `warning`.

        Returns:
            `{"ok": True, "pixel_count": int}` on success (plus
            `"warning": str` only if the source write-back failed), or
            `{"ok": False, "error": "<message>"}` (e.g. fewer than 3
            vertices, sample h5ad missing) — surfaced by ROI Design as an
            inline error rather than a silent no-op.
        """
        if not analysis_db_path or not Path(analysis_db_path).exists():
            return {"ok": False, "error": "No analysis database."}
        h5ad_path = self._inspection_h5ad_path(analysis_db_path, sample_name)
        if not h5ad_path.exists():
            return {"ok": False, "error": f"Sample {sample_name!r} has no .h5ad file."}

        # `create_analysis_schema` is idempotent (every statement is
        # `CREATE TABLE IF NOT EXISTS`) — this is a no-op for an analysis
        # database that already has the `rois` table, and adds it for free
        # on the first ROI ever saved against an analysis run before this
        # feature existed (there is no migration system; every existing
        # analysis DB predates `rois`).
        analysis_db.init_analysis_db(analysis_db_path).close()

        existing = analysis_db.load_rois(analysis_db_path)
        existing_row = existing[existing["name"] == name] if not existing.empty else existing
        effective_color = existing_row["color"].iloc[0] if len(existing_row) else color

        try:
            pixel_count = roi.save_roi_to_sample(h5ad_path, name, effective_color, vertices)
        except ValueError as e:
            return {"ok": False, "error": str(e)}

        if existing_row.empty:
            try:
                analysis_db.register_roi(analysis_db_path, name, effective_color)
            except sqlite3.IntegrityError:
                pass  # raced with another save of the same new name — fine, row exists now

        # Recomputed rather than threaded through save_roi_to_sample's
        # return value (which is just the pixel count), so that function's
        # own signature/tests stay merged.h5ad-agnostic. It only reads the
        # coordinates (ADR 50), so the recompute is cheap.
        sample_adata_mask = roi.sample_polygon_mask(h5ad_path, vertices)
        warning = ""
        if self._active_rel_dir(analysis_db_path):
            warning = self._write_roi_to_source(h5ad_path, name, sample_adata_mask)
        else:
            roi.sync_roi_to_merged(
                self._merged_h5ad_path(analysis_db_path), sample_name, name,
                effective_color, vertices, sample_adata_mask,
            )
        self.heatmap_provider.invalidate(sample_name)
        result = {"ok": True, "pixel_count": pixel_count}
        if warning:
            result["warning"] = warning
        return result

    @staticmethod
    def _source_obs_names(h5ad_path: Path):
        """An imported sample file's `obs["source_obs_name"]`, read alone."""
        with h5py.File(h5ad_path, "r") as f:
            return h5ad_io.read_obs_column(f, imported_dataset.SOURCE_OBS_NAME_COLUMN)

    def _write_roi_to_source(self, h5ad_path: Path, name: str, mask) -> str:
        """Best-effort ROI obs-column write-back into the active imported
        dataset's source `.h5ad` (ADR 49). Returns `""` on success, else a
        user-facing warning (also emitted as `roiSourceWriteFailed`)."""
        source = self._active_dataset["source_path"]
        try:
            imported_dataset.write_roi_to_source(
                source, roi.roi_obs_column(name),
                self._source_obs_names(h5ad_path), mask,
            )
        except (OSError, KeyError, ValueError) as e:
            logger.warning("ROI write-back to %s failed: %s", source, e)
            message = (
                f"ROI saved in MSIAnalyzer, but it could not be written back to "
                f"{Path(source).name} ({e}). Is the file moved, or open in a notebook?"
            )
            self.roiSourceWriteFailed.emit(message)
            return message
        return ""

    @Slot(str, str, str, result=bool)
    def deleteRoiFromSample(self, analysis_db_path: str, sample_name: str, name: str) -> bool:
        """Remove one ROI from one sample only (and, if it exists,
        `merged.h5ad`) — the catalog entry and every other sample's own
        copy are untouched; use `deleteRoiEverywhere` to also drop the
        catalog row across every sample.

        Returns:
            True if the sample actually had this ROI.
        """
        if not analysis_db_path or not Path(analysis_db_path).exists():
            return False
        h5ad_path = self._inspection_h5ad_path(analysis_db_path, sample_name)
        if not h5ad_path.exists():
            return False
        removed = roi.delete_roi_from_sample(h5ad_path, name)
        if removed:
            if self._active_rel_dir(analysis_db_path):
                self._remove_roi_from_source(h5ad_path, name)
            else:
                roi.sync_roi_deletion_to_merged(
                    self._merged_h5ad_path(analysis_db_path), sample_name, name
                )
            self.heatmap_provider.invalidate(sample_name)
        return removed

    def _remove_roi_from_source(self, h5ad_path: Path, name: str) -> None:
        """Deletion counterpart of `_write_roi_to_source` — best effort,
        a failure is only emitted as `roiSourceWriteFailed`."""
        source = self._active_dataset["source_path"]
        try:
            imported_dataset.remove_roi_from_source(
                source, roi.roi_obs_column(name), self._source_obs_names(h5ad_path)
            )
        except (OSError, KeyError, ValueError) as e:
            logger.warning("ROI removal from %s failed: %s", source, e)
            self.roiSourceWriteFailed.emit(
                f"ROI removed in MSIAnalyzer, but not from {Path(source).name} ({e})."
            )

    @Slot(str, list, str, result=dict)
    def deleteRoiEverywhere(self, analysis_db_path: str, sample_names: list, name: str) -> dict:
        """Remove one ROI's catalog entry and its per-sample data from
        every sample in `sample_names` — "delete this ROI" in ROI Design's
        management list, which only has the analysis' full sample list on
        hand, not which samples actually carry this ROI.

        Args:
            analysis_db_path: The analysis' SQLite database.
            sample_names: Every sample name to check/clean — normally
                every sample of the analysis, not just the one currently
                open in ROI Design.
            name: The ROI to remove.

        Returns:
            `{"catalog_removed": bool, "samples_removed": [names...]}`.
        """
        removed_samples = [
            sample_name for sample_name in sample_names
            if self.deleteRoiFromSample(analysis_db_path, sample_name, name)
        ]
        catalog_removed = False
        if analysis_db_path and Path(analysis_db_path).exists():
            catalog_removed = analysis_db.delete_roi_catalog_entry(analysis_db_path, name)
        return {"catalog_removed": catalog_removed, "samples_removed": removed_samples}

    # -----------------------------------------------------------------
    # H&E image coregistration (see core/registration/)
    # -----------------------------------------------------------------

    @staticmethod
    def _landmarks_to_rows(fit) -> list:
        return [
            {
                "heX": lm.he_x, "heY": lm.he_y,
                "gridX": lm.grid_x, "gridY": lm.grid_y,
                "error": err,
            }
            for lm, err in zip(fit.landmarks, fit.reprojection_errors)
        ]

    @Slot(str, str, result=dict)
    def getRegistrationInfo(self, analysis_db_path: str, sample_name: str) -> dict:
        """One sample's attached H&E image and fitted registration, if
        any — `CoregistrationWindow`'s state on open/sample-switch.

        Args:
            analysis_db_path: The analysis' SQLite database.
            sample_name: Which sample's raw database to read (see
                `analysis_db.get_sample_raw_db_path`).

        Returns:
            `{"hasImage": bool, "width", "height", "format", "hasFit":
            bool, "transformType", "rmse", "matrix", "landmarks":
            [{"heX", "heY", "gridX", "gridY", "error"}, ...]}`.
            `hasImage`/`hasFit` are both `False` (everything else at its
            zero value) if the sample has no raw database, or no image
            has been attached yet. `matrix` is the H&E-pixel -> MSI
            grid-index `[[a, b, c], [d, e, f]]` (`None` if `hasFit` is
            `False`) — QML's `RoiDrawingCanvas`/`CoregistrationWindow`
            need it client-side (via `Utils/AffineTransform.js`) for live
            tap-to-coordinate conversion, not round-tripped through a
            bridge call on every click.
        """
        empty = {
            "hasImage": False, "width": 0, "height": 0, "format": "",
            "hasFit": False, "transformType": "", "rmse": 0.0, "matrix": None,
            "landmarks": [],
        }
        raw_db_path = analysis_db.get_sample_raw_db_path(analysis_db_path, sample_name)
        if not raw_db_path:
            return empty
        info = load_registration(raw_db_path)
        if info is None:
            return empty
        result = {
            "hasImage": True,
            "width": info.image.width,
            "height": info.image.height,
            "format": info.image.image_format,
            "hasFit": info.fit is not None,
            "transformType": info.fit.transform_type if info.fit else "",
            "rmse": info.fit.rmse if info.fit else 0.0,
            "matrix": info.fit.matrix.tolist() if info.fit else None,
            "landmarks": self._landmarks_to_rows(info.fit) if info.fit else [],
        }
        return result

    @Slot(str, list, result=dict)
    def getRegistrationMarks(self, analysis_db_path: str, sample_names: list) -> dict:
        """`{sample: mark}` — the suffix Visual Inspection's tiles and the
        ROI/Coregistration sample combos append to a sample's name:
        `" · H&E ✓"` (fitted), `" · H&E not fitted"` (image only) or `""`.
        """
        if not analysis_db_path or not Path(analysis_db_path).exists():
            return {}
        marks = {}
        for name in sample_names:
            info = self.getRegistrationInfo(analysis_db_path, name)
            marks[name] = (" · H&E ✓" if info["hasFit"]
                           else " · H&E not fitted" if info["hasImage"] else "")
        return marks

    @Slot(str, result=dict)
    def getScaleBar(self, analysis_db_path: str) -> dict:
        """The analysis' scale bar settings (ADR 53) —
        `analysis_db.load_scale_bar`'s shape, defaults if never set."""
        if not analysis_db_path or not Path(analysis_db_path).exists():
            return {}
        return analysis_db.load_scale_bar(analysis_db_path)

    @Slot(str, str)
    def setScaleBar(self, analysis_db_path: str, settings_json: str) -> None:
        """Save the scale bar dialog's settings, passed as
        `JSON.stringify(...)` so the nested `pixel_um` map arrives as a
        plain dict, never a QJSValue."""
        analysis_db.save_scale_bar(analysis_db_path, json.loads(settings_json))

    @Slot(str, str, str, str)
    def setObsCategoryColor(
        self, analysis_db_path: str, obs_column: str, category: str, color: str
    ) -> None:
        """Save one category's custom color (ADR 57), from the legend's
        color picker. Tiles pick it up through their request id once the
        legend is refreshed (`requestObsCategories`)."""
        analysis_db.save_obs_category_color(analysis_db_path, obs_column, category, color)

    @Slot(str, str)
    def resetObsCategoryColors(self, analysis_db_path: str, obs_column: str) -> None:
        """Back to the default palette for every category of `obs_column`."""
        analysis_db.reset_obs_category_colors(analysis_db_path, obs_column)

    @Slot(str, str, str, result=dict)
    def attachHeImage(self, analysis_db_path: str, sample_name: str, image_path: str) -> dict:
        """Attach (or replace) `sample_name`'s H&E/brightfield image —
        `CoregistrationWindow`'s "Attach Image..." action.

        Args:
            analysis_db_path: The analysis' SQLite database.
            sample_name: Which sample's raw database to attach the image
                to (see `attach_he_image` — it lives there, not in this
                analysis' own database or `.h5ad`, since it doesn't change
                across re-analyses of the same sample).
            image_path: A local PNG/JPEG/TIFF file path (already resolved
                from a QML `FileDialog`'s `file://` URL via
                `Router.toLocalPath`).

        Returns:
            `{"ok": True, "width", "height", "format"}` on success, or
            `{"ok": False, "error": "<message>"}` (no raw database for
            this sample, or an unreadable/unsupported image file).
        """
        raw_db_path = analysis_db.get_sample_raw_db_path(analysis_db_path, sample_name)
        if not raw_db_path:
            return {"ok": False, "error": f"Sample {sample_name!r} has no raw database."}
        try:
            attached = attach_he_image(raw_db_path, image_path)
        except (ValueError, OSError) as e:
            return {"ok": False, "error": str(e)}
        self.registrationChanged.emit()
        return {
            "ok": True,
            "width": attached.width,
            "height": attached.height,
            "format": attached.image_format,
        }

    @Slot(str, str, list, str, result=dict)
    def saveRegistration(
        self, analysis_db_path: str, sample_name: str, landmarks: list, transform_type: str,
    ) -> dict:
        """Fit and save a registration from user-placed landmark pairs —
        `CoregistrationWindow`'s "Fit & Save" action.

        Args:
            analysis_db_path: The analysis' SQLite database.
            sample_name: Which sample's raw database to save into.
            landmarks: `[{"heX", "heY", "gridX", "gridY"}, ...]` — see
                `fit_and_save_registration` for the point-pair semantics
                and minimum count per `transform_type`.
            transform_type: `"affine"` or `"similarity"`.

        Returns:
            `{"ok": True, "rmse": float, "landmarks": [{"heX", "heY",
            "gridX", "gridY", "error"}, ...]}` on success, or
            `{"ok": False, "error": "<message>"}` (no raw database, no
            image attached yet, too few landmarks, or an unknown
            `transform_type`).
        """
        raw_db_path = analysis_db.get_sample_raw_db_path(analysis_db_path, sample_name)
        if not raw_db_path:
            return {"ok": False, "error": f"Sample {sample_name!r} has no raw database."}
        info = load_registration(raw_db_path)
        if info is None:
            return {"ok": False, "error": "No image attached to this sample yet."}

        point_pairs = [
            ((lm["heX"], lm["heY"]), (lm["gridX"], lm["gridY"])) for lm in landmarks
        ]
        try:
            fit = fit_and_save_registration(
                raw_db_path, info.image.image_id, point_pairs, transform_type,
            )
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        self.registrationChanged.emit()
        return {"ok": True, "rmse": fit.rmse, "landmarks": self._landmarks_to_rows(fit)}

    # -----------------------------------------------------------------
    # Calibration curve quantification (targeted analyses)
    # -----------------------------------------------------------------

    @Property(list, constant=True)
    def amountUnits(self) -> list:
        """`core.calibration.units.AMOUNT_UNITS`, for
        CalibrationSection.qml's amount-unit picker — one source of truth
        rather than a hand-copied QML list."""
        return list(AMOUNT_UNITS)

    @Property(list, constant=True)
    def pixelSizeUnits(self) -> list:
        """`core.calibration.units.PIXEL_SIZE_UNITS`, for
        CalibrationSection.qml's pixel-size-unit picker."""
        return list(PIXEL_SIZE_UNITS)

    @Slot(str, result=list)
    def getCalibrationPoints(self, analysis_db_path: str) -> list:
        """Every calibration standard for this analysis —
        CalibrationSection.qml's Standards table (v1: one analysis-wide
        set, not scoped per curve — see `analysis_db`'s own schema comment).

        Args:
            analysis_db_path: The analysis' SQLite database.

        Returns:
            Records from `analysis_db.load_calibration_points`, each with
            a `sample_name` column resolved from `sample_id` (joined here
            in Python — this table is always small, no need for a SQL
            join). Empty when there are no standards yet.
        """
        if not analysis_db_path or not Path(analysis_db_path).exists():
            return []
        df = analysis_db.load_calibration_points(analysis_db_path)
        if df.empty:
            return []
        samples = analysis_db.load_samples(analysis_db_path)
        id_to_name = (
            dict(zip(samples["sample_id"], samples["name"])) if not samples.empty else {}
        )
        df = df.copy()
        df["sample_name"] = df["sample_id"].map(id_to_name).fillna("")
        return _dataframe_to_records(df)

    @Slot(str, result=list)
    def getCalibrationUnknowns(self, analysis_db_path: str) -> list:
        """Every quantified ROI result for this analysis —
        CalibrationSection.qml's Unknowns table.

        Returns:
            Records from `analysis_db.load_calibration_unknowns`, with
            `sample_name` resolved the same way as `getCalibrationPoints`.
        """
        if not analysis_db_path or not Path(analysis_db_path).exists():
            return []
        df = analysis_db.load_calibration_unknowns(analysis_db_path)
        if df.empty:
            return []
        samples = analysis_db.load_samples(analysis_db_path)
        id_to_name = (
            dict(zip(samples["sample_id"], samples["name"])) if not samples.empty else {}
        )
        df = df.copy()
        df["sample_name"] = df["sample_id"].map(id_to_name).fillna("")
        return _dataframe_to_records(df)

    @Slot(str, result=float)
    def getCalibrationAnalyteMz(self, analysis_db_path: str) -> float:
        """The analyte m/z this analysis' calibration is built around, if
        any standard has ever been added (see `addCalibrationPoint`'s own
        `write_metadata` call) — lets CalibrationSection.qml preselect it
        in the analyte picker on a later visit instead of asking the user
        to reselect it every time the tab is reopened.

        Returns:
            The stored m/z, or `-1.0` (never a real m/z) if none is
            recorded yet — simpler for QML to check than round-tripping a
            `None`/`NaN` across the bridge boundary.
        """
        if not analysis_db_path or not Path(analysis_db_path).exists():
            return -1.0
        value = analysis_db.load_metadata_value(analysis_db_path, "calibration_analyte_mz")
        return float(value) if value is not None else -1.0

    @Slot(str, str, result=dict)
    def getSamplePixelSize(self, analysis_db_path: str, sample_name: str) -> dict:
        """This sample's own raster pixel size, if the raw database has
        one on record (`core.parser.mzml_parser.write_raw_metadata`,
        written during the run from the raster XML's `<rasterConfig>`
        section — see `Run._process_one_sample`) — lets
        CalibrationSection.qml *default* its pixel-size field to the
        acquisition's own real value instead of asking the user to know
        and type it in, while still leaving it fully editable (a default,
        never a locked-in value).

        Uses `pixel_size_x` as *the* pixel size (calibration assumes
        square pixels — see `core.calibration.units.pixel_area`'s own
        docstring); `pixel_size_y` is recorded on the raw DB too but not
        surfaced here, since v1 has nowhere to use a second value.

        Args:
            analysis_db_path: The analysis' SQLite database.
            sample_name: Which sample to look up.

        Returns:
            `{"pixel_size": float, "pixel_size_unit": str}` if both are on
            record for this sample's raw database, or `{}` — never a
            partial dict — if there's no analysis database, no such
            sample, no raw database on record for it, or the raster XML
            it was parsed from had no `<rasterConfig>` section (exactly
            the "user must define it themselves" case this already had).
        """
        if not analysis_db_path or not Path(analysis_db_path).exists():
            return {}
        raw_db_path = analysis_db.get_sample_raw_db_path(analysis_db_path, sample_name)
        if not raw_db_path or not Path(raw_db_path).exists():
            return {}
        pixel_size = load_raw_metadata_value(raw_db_path, "pixel_size_x")
        pixel_size_unit = load_raw_metadata_value(raw_db_path, "pixel_size_unit")
        if pixel_size is None or pixel_size_unit is None:
            return {}
        return {"pixel_size": float(pixel_size), "pixel_size_unit": pixel_size_unit}

    @Slot(str, result=dict)
    def getCalibrationCurve(self, analysis_db_path: str) -> dict:
        """The most recently fitted calibration curve for this analysis.

        Returns:
            A dict of every `calibration_curves` column (see
            `analysis_db.load_calibration_curve`), or `{}` if no curve has
            been fit yet (or the db path doesn't exist).
        """
        if not analysis_db_path or not Path(analysis_db_path).exists():
            return {}
        curve = analysis_db.load_calibration_curve(analysis_db_path)
        return curve or {}

    @Slot(str, str, str, float, float, str, float, str, result=dict)
    def addCalibrationPoint(
        self,
        analysis_db_path: str,
        sample_name: str,
        roi_name: str,
        analyte_mz: float,
        amount: float,
        amount_unit: str,
        pixel_size: float,
        pixel_size_unit: str,
    ) -> dict:
        """Register (or update) one calibration standard —
        CalibrationSection.qml's "Add Standard" flow: called right after
        `RoiDesignWindow.roiSaved` fires, from a small popup asking for
        `amount`/`amount_unit`/`pixel_size`/`pixel_size_unit` immediately
        while the spot is fresh.

        Computes this ROI's pixel count and signal straight from the
        sample's already-written `.h5ad` (the analyte's column is already
        there — every `NormalizationConfig.analyte_mzs` value was
        quantified for every sample back when the targeted run itself
        assembled its `.h5ad` files). The signal itself is an
        internal-standard-corrected ratio, not bare intensity — see
        `core.calibration.roi_intensity.roi_internal_standard_signal` —
        since a standard and an unknown quantified against the same curve
        can legitimately live on different samples/acquisitions, where raw
        intensity alone carries no correction for ionization-efficiency
        drift between them. Derives area/concentration (see
        `core.calibration.units`), and upserts — re-adding the same ROI
        name (e.g. correcting a typo'd amount) updates it in place rather
        than duplicating.

        Args:
            analysis_db_path: The analysis' SQLite database.
            sample_name: Which sample the ROI was drawn on.
            roi_name: The ROI's name.
            analyte_mz: The analyte's m/z — matched to the nearest
                `.var["mz"]`.
            amount: Total quantity of the analyte in the spot, as entered.
            amount_unit: One of `core.calibration.units.AMOUNT_UNITS`.
            pixel_size: This ROI's pixel edge length (assumed square).
            pixel_size_unit: `"um"` or `"mm"`.

        Returns:
            `{"ok": True}` on success, or `{"ok": False, "error":
            "<message>"}` (no analysis database, no `.h5ad` for that
            sample, an invalid unit, the ROI has no pixels on that sample,
            this analysis has no recorded `reference_mz`, or every pixel
            in the ROI has a non-positive reference-channel value).
        """
        if not analysis_db_path or not Path(analysis_db_path).exists():
            return {"ok": False, "error": "No analysis database."}
        if amount_unit not in AMOUNT_UNITS:
            return {"ok": False, "error": f"amount_unit must be one of {AMOUNT_UNITS}."}
        h5ad_path = self._sample_h5ad_path(analysis_db_path, sample_name)
        if not h5ad_path.exists():
            return {"ok": False, "error": f"Sample {sample_name!r} has no .h5ad file."}
        reference_mz = _load_calibration_reference_mz(analysis_db_path)
        if reference_mz is None:
            return {
                "ok": False,
                "error": "No reference m/z on record for this analysis — re-run "
                "it as a targeted analysis first.",
            }

        adata = ad.read_h5ad(h5ad_path)
        n_pixels = roi_pixel_count(adata, roi_name)
        if n_pixels == 0:
            return {
                "ok": False,
                "error": f"ROI {roi_name!r} has no pixels on sample {sample_name!r}.",
            }
        mean_intensity = roi_internal_standard_signal(
            adata, roi_name, analyte_mz, reference_mz, layer="raw"
        )
        if mean_intensity is None:
            return {
                "ok": False,
                "error": f"Reference channel has no positive signal in ROI {roi_name!r} "
                f"on sample {sample_name!r}.",
            }

        try:
            area = pixel_area(n_pixels, pixel_size, pixel_size_unit)
            concentration = concentration_from_amount(amount, area)
        except ValueError as e:
            return {"ok": False, "error": str(e)}

        analysis_db.init_analysis_db(analysis_db_path).close()
        sample_id = analysis_db.get_sample_id(analysis_db_path, sample_name)
        analysis_db.upsert_calibration_point(
            analysis_db_path,
            sample_id=sample_id,
            roi_name=roi_name,
            amount=amount,
            amount_unit=amount_unit,
            pixel_size=pixel_size,
            pixel_size_unit=pixel_size_unit,
            area=area,
            concentration=concentration,
            mean_intensity=mean_intensity,
            n_pixels=n_pixels,
        )
        # v1 is single-analyte-per-analysis — remembered here (not just
        # implied by whatever mz the GUI happens to have loaded right now)
        # so CalibrationSection.qml can restore it on a later visit
        # (getCalibrationAnalyteMz) instead of asking the user to retype it.
        analysis_db.write_metadata(
            analysis_db_path, {"calibration_analyte_mz": analyte_mz}
        )
        return {"ok": True}

    @Slot(str, str, result=bool)
    def deleteCalibrationPoint(self, analysis_db_path: str, roi_name: str) -> bool:
        """Remove one calibration standard.

        Returns:
            True if it existed.
        """
        if not analysis_db_path or not Path(analysis_db_path).exists():
            return False
        return analysis_db.delete_calibration_point(analysis_db_path, roi_name)

    @Slot(str, float, str, str, result=dict)
    def fitCalibrationCurve(
        self, analysis_db_path: str, analyte_mz: float, fit_type: str, weighting: str
    ) -> dict:
        """Fit a calibration curve from every current standard, persist it
        (append-only fit history — a re-fit adds a new row, never
        overwrites), and re-interpolate every existing unknown against the
        fresh fit.

        Args:
            analysis_db_path: The analysis' SQLite database.
            analyte_mz: The analyte's m/z, recorded on the new
                `calibration_curves` row.
            fit_type: `"linear"` or `"weighted_linear"`.
            weighting: `"1/x"`/`"1/x2"` for a weighted fit — ignored (pass
                `""`) for `"linear"`.

        Returns:
            The new curve as a dict (see
            `analysis_db.load_calibration_curve`, plus `"ok": True`), or
            `{"ok": False, "error": "<message>"}` — fewer than 2
            standards, standards whose amount/pixel-size units don't all
            match, or an invalid `fit_type`/`weighting`.
        """
        if not analysis_db_path or not Path(analysis_db_path).exists():
            return {"ok": False, "error": "No analysis database."}

        points_df = analysis_db.load_calibration_points(analysis_db_path)
        if len(points_df) < 2:
            return {"ok": False, "error": "At least 2 calibration standards are required."}

        amount_units = points_df["amount_unit"].unique()
        pixel_size_units = points_df["pixel_size_unit"].unique()
        if len(amount_units) > 1 or len(pixel_size_units) > 1:
            return {
                "ok": False,
                "error": (
                    "All standards must use the same amount and pixel-size units "
                    "to be fit together."
                ),
            }

        points = list(zip(points_df["concentration"], points_df["mean_intensity"]))
        weighting_arg = weighting if weighting else None
        try:
            fit = fit_calibration_curve(points, fit_type=fit_type, weighting=weighting_arg)
        except ValueError as e:
            return {"ok": False, "error": str(e)}

        conc_unit = calibration_concentration_unit(
            str(amount_units[0]), str(pixel_size_units[0])
        )
        run_id = analysis_db.load_metadata_value(analysis_db_path, "analysis_id") or "unknown"
        command_id = analysis_db.log_command(
            analysis_db_path,
            command_name="fit_calibration_curve",
            arguments={"analyte_mz": analyte_mz, "fit_type": fit_type, "weighting": weighting_arg},
            run_id=run_id,
        )
        curve_id = analysis_db.register_calibration_curve(
            analysis_db_path,
            analyte_mz=analyte_mz,
            fit_type=fit_type,
            weighting=weighting_arg,
            concentration_unit=conc_unit,
            slope=fit.slope,
            intercept=fit.intercept,
            r_squared=fit.r_squared,
            lod=fit.lod,
            loq=fit.loq,
            n_points=fit.n_points,
            command_id=command_id,
        )

        if fit.slope != 0:
            for _, row in analysis_db.load_calibration_unknowns(analysis_db_path).iterrows():
                new_conc = interpolate_concentration(fit, row["mean_intensity"])
                new_amount = amount_from_concentration(new_conc, row["area"])
                analysis_db.update_calibration_unknown_result(
                    analysis_db_path, row["roi_name"], curve_id, new_conc, new_amount
                )

        curve = analysis_db.load_calibration_curve(analysis_db_path, curve_id)
        curve["ok"] = True
        return curve

    @Slot(str, str, str, float, float, str, result=dict)
    def addCalibrationUnknown(
        self,
        analysis_db_path: str,
        sample_name: str,
        roi_name: str,
        analyte_mz: float,
        pixel_size: float,
        pixel_size_unit: str,
    ) -> dict:
        """Register (or update) one quantified ROI —
        CalibrationSection.qml's "Add Sample to Quantify" flow, called
        right after `RoiDesignWindow.roiSaved` fires (a separate trigger
        from "Add Standard" — see the calibration workflow's own docs).

        Computes this ROI's pixel count/area/signal the same way
        `addCalibrationPoint` does — including the same internal-standard
        (`reference_mz`-ratio) correction, so an unknown quantified against
        a curve fit on a *different* sample stays comparable to it — and,
        if a calibration curve already exists, interpolates immediately. If
        not, the result is still recorded, with a null
        `interpolated_conc`/`amount` — the GUI shows "no curve yet" for it
        until `fitCalibrationCurve` runs and updates every existing unknown
        in place.

        Returns:
            `{"ok": True}` on success, or `{"ok": False, "error":
            "<message>"}`.
        """
        if not analysis_db_path or not Path(analysis_db_path).exists():
            return {"ok": False, "error": "No analysis database."}
        h5ad_path = self._sample_h5ad_path(analysis_db_path, sample_name)
        if not h5ad_path.exists():
            return {"ok": False, "error": f"Sample {sample_name!r} has no .h5ad file."}
        reference_mz = _load_calibration_reference_mz(analysis_db_path)
        if reference_mz is None:
            return {
                "ok": False,
                "error": "No reference m/z on record for this analysis — re-run "
                "it as a targeted analysis first.",
            }

        adata = ad.read_h5ad(h5ad_path)
        n_pixels = roi_pixel_count(adata, roi_name)
        if n_pixels == 0:
            return {
                "ok": False,
                "error": f"ROI {roi_name!r} has no pixels on sample {sample_name!r}.",
            }
        mean_intensity = roi_internal_standard_signal(
            adata, roi_name, analyte_mz, reference_mz, layer="raw"
        )
        if mean_intensity is None:
            return {
                "ok": False,
                "error": f"Reference channel has no positive signal in ROI {roi_name!r} "
                f"on sample {sample_name!r}.",
            }

        try:
            area = pixel_area(n_pixels, pixel_size, pixel_size_unit)
        except ValueError as e:
            return {"ok": False, "error": str(e)}

        curve = analysis_db.load_calibration_curve(analysis_db_path)
        curve_id = interpolated_conc = amount = None
        if curve is not None and curve["slope"] != 0:
            curve_id = curve["id"]
            fit_like = SimpleNamespace(slope=curve["slope"], intercept=curve["intercept"])
            interpolated_conc = interpolate_concentration(fit_like, mean_intensity)
            amount = amount_from_concentration(interpolated_conc, area)

        analysis_db.init_analysis_db(analysis_db_path).close()
        sample_id = analysis_db.get_sample_id(analysis_db_path, sample_name)
        analysis_db.upsert_calibration_unknown(
            analysis_db_path,
            sample_id=sample_id,
            roi_name=roi_name,
            pixel_size=pixel_size,
            pixel_size_unit=pixel_size_unit,
            area=area,
            mean_intensity=mean_intensity,
            n_pixels=n_pixels,
            curve_id=curve_id,
            interpolated_conc=interpolated_conc,
            amount=amount,
        )
        return {"ok": True}

    @Slot(str, int, result=bool)
    def deleteCalibrationUnknown(self, analysis_db_path: str, unknown_id: int) -> bool:
        """Remove one quantified ROI result.

        Returns:
            True if it existed.
        """
        if not analysis_db_path or not Path(analysis_db_path).exists():
            return False
        return analysis_db.delete_calibration_unknown(analysis_db_path, unknown_id)

    @Slot(str, str, result=dict)
    def getCalibrationPlotData(self, analysis_db_path: str, display_area_unit: str = "") -> dict:
        """The fitted calibration curve as plain data for
        CalibrationSection.qml's Step 2 plot (Qt Graphs, ADR 51).

        Cheap enough (a handful of points, no raw spectrum I/O) to build
        synchronously — unlike `requestMirrorPlot`, no background worker.

        Args:
            analysis_db_path: The analysis' SQLite database.
            display_area_unit: `"um"` or `"mm"` — redisplay the curve in
                this square-length unit instead of whatever unit it was
                actually fit in (`curve["concentration_unit"]`'s own area
                part), converting concentrations/slope/LOD/LOQ via
                `area_conversion_factor`. A live, non-destructive display
                choice (CalibrationSection.qml's
                `concentrationAreaUnitCombo`) — nothing stored changes.
                `""` (the default) or a value matching the curve's own
                unit already is a no-op.

        Returns:
            `{"points": [[concentration, signal], ...], "fitLine": [[x0, y0],
            [x1, y1]], "title": "y = ...  R² = ...", "xTitle", "yTitle"}`,
            or `{}` if there's no analysis database, no curve has been fit
            yet, or it has no standards on record.
        """
        if not analysis_db_path or not Path(analysis_db_path).exists():
            return {}
        curve = analysis_db.load_calibration_curve(analysis_db_path)
        if curve is None:
            return {}
        points_df = analysis_db.load_calibration_points(analysis_db_path)
        if points_df.empty:
            return {}
        points = list(zip(points_df["concentration"], points_df["mean_intensity"]))
        slope, intercept, lod, loq = curve["slope"], curve["intercept"], curve["lod"], curve["loq"]
        concentration_unit_label = curve["concentration_unit"]

        # concentration_unit_label looks like "pmol/mm2" — the area unit is
        # everything after the "/", minus the trailing "2".
        curve_area_unit = concentration_unit_label.split("/")[-1].rstrip("2")
        if display_area_unit and display_area_unit in PIXEL_SIZE_UNITS \
                and display_area_unit != curve_area_unit:
            # `factor` = how many `display_area_unit`-squared fit in one
            # `curve_area_unit`-squared. A concentration is amount/area, so
            # redisplaying it in a unit with MORE of it per physical area
            # (curve_area_unit -> display_area_unit, factor > 1) makes the
            # concentration NUMBER SMALLER (divide by factor) — e.g.
            # 2.667e-7 pmol/um2 becomes 0.2667 pmol/mm2, factor = 1e-6
            # (um -> mm), 2.667e-7 / 1e-6 = 0.2667. Slope inverts that
            # (multiply by factor) so slope*concentration is unchanged;
            # LOD/LOQ are concentration-like, so they scale like
            # concentration does.
            factor = area_conversion_factor(curve_area_unit, display_area_unit)
            points = [(x / factor, y) for x, y in points]
            slope = slope * factor
            lod = lod / factor if lod is not None else None
            loq = loq / factor if loq is not None else None
            amount_unit_part = concentration_unit_label.split("/")[0]
            concentration_unit_label = calibration_concentration_unit(
                amount_unit_part, display_area_unit
            )

        fit = SimpleNamespace(slope=slope, intercept=intercept, r_squared=curve["r_squared"])
        x0, x1 = calibration_line_range([x for x, _ in points])
        return {
            "points": [[float(x), float(y)] for x, y in points],
            "fitLine": [[x0, slope * x0 + intercept], [x1, slope * x1 + intercept]],
            "title": calibration_title(fit),
            "xTitle": f"Concentration ({concentration_unit_label})",
            "yTitle": "Signal (analyte / reference)",
        }

    # -----------------------------------------------------------------
    # Export menu
    # -----------------------------------------------------------------

    def _run_export(self, export_fn) -> None:
        """Shared fire-and-forget plumbing for every Export menu action —
        see the class docstring's own note on `exportFinished`/
        `exportFailed` for why this is threaded rather than a return
        value, and `ExportWorker`'s docstring for why it's generic over
        which export function runs.
        """
        worker = ExportWorker(export_fn, self)
        worker.succeeded.connect(self._onExportSucceeded)
        worker.failed.connect(self._onExportFailed)
        self._export_worker = worker
        worker.start()

    @Slot(str)
    def _onExportSucceeded(self, message: str) -> None:
        if self.sender() is not self._export_worker:
            return  # superseded by a newer request — discard
        self.exportFinished.emit(message)

    @Slot(str)
    def _onExportFailed(self, message: str) -> None:
        if self.sender() is not self._export_worker:
            return  # superseded by a newer request — discard
        logger.warning("export failed: %s", message)
        self.exportFailed.emit(message)

    @Slot(str, str)
    def exportAnnotationTable(self, analysis_db_path: str, dest_path: str) -> None:
        """Start writing the Annotation export (one row per feature,
        every tier's identity fields, including features with none at
        all) on a background thread; completion arrives via
        `exportFinished(message)`/`exportFailed(message)`.

        Args:
            analysis_db_path: The analysis' SQLite database.
            dest_path: Where to write the export, as chosen in the save
                dialog — `.txt` for tab-delimited, anything else
                (`.csv` included) for comma-delimited (see
                `core.export.export_annotation_table`).
        """
        def _do_export() -> str:
            core_export.export_annotation_table(analysis_db_path, dest_path)
            return f"Annotation table exported to {dest_path}"

        self._run_export(_do_export)

    @Slot(str, str, str, str)
    def exportIntegrationTables(
        self, analysis_db_path: str, dest_folder: str, layer: str, file_format: str
    ) -> None:
        """Start writing the Integration export (one pixel x feature
        matrix per sample) on a background thread; completion arrives via
        `exportFinished(message)`/`exportFailed(message)`.

        Args:
            analysis_db_path: The analysis' SQLite database.
            dest_folder: Destination folder, as chosen in the folder
                dialog — one `<sample_name>_integration.<ext>` file per
                sample is written into it.
            layer: `"raw"` or `"TIC"`.
            file_format: `"csv"` (comma) or `"txt"` (tab) — a folder
                dialog gives no filename to key a suffix off, so this is
                its own explicit choice (see
                `core.export.export_integration_tables`).
        """
        def _do_export() -> str:
            n = core_export.export_integration_tables(
                analysis_db_path, dest_folder, layer, file_format
            )
            return f"Integration tables exported for {n} sample(s) to {dest_folder}"

        self._run_export(_do_export)

    @Slot(str, str)
    def exportCalibrationResults(self, analysis_db_path: str, dest_folder: str) -> None:
        """Start writing a targeted analysis' calibration results
        (standards/curve/unknowns, see `core.export.export_calibration_results`)
        on a background thread; completion arrives via
        `exportFinished(message)`/`exportFailed(message)`.

        Args:
            analysis_db_path: The analysis' SQLite database.
            dest_folder: Destination folder — up to 3 CSV files are
                written into it (whichever of standards/curve/unknowns
                actually exist).
        """
        def _do_export() -> str:
            n = core_export.export_calibration_results(analysis_db_path, dest_folder)
            if n == 0:
                return "No calibration results to export yet."
            return f"Calibration results ({n} file(s)) exported to {dest_folder}"

        self._run_export(_do_export)

    @Slot(str, str, str, str, str, str, str, str, str, str, bool, bool, "QVariantList")
    def exportVisualInspectionImages(
        self,
        analysis_db_path: str,
        dest_folder: str,
        image_format: str,
        mode: str,
        mz_str: str,
        obs_column: str,
        layer: str,
        colormap: str,
        vmin_token: str,
        vmax_token: str,
        show_rois: bool,
        include_scale_bar: bool,
        hidden_categories: list,
    ) -> None:
        """Start writing the Image export (one PNG/PDF/SVG per sample,
        matching Visual Inspection's own current display settings) on a
        background thread; completion arrives via
        `exportFinished(message)`/`exportFailed(message)`.

        Args:
            analysis_db_path: The analysis' SQLite database.
            dest_folder: Destination folder, as chosen in the folder
                dialog — one `<sample_name>.<image_format>` file per
                sample is written into it.
            image_format: `"png"`, `"pdf"`, or `"svg"`.
            mode: `"feature"` or `"obs"` — mirrors
                `HeatmapControlsPanel.inspectionMode`.
            mz_str: The selected feature's m/z as a string (`mode="feature"`
                only) — `""` otherwise.
            obs_column: The selected obs column (`mode="obs"` only) — `""`
                otherwise.
            layer: `"raw"` or `"TIC"` — mirrors
                `HeatmapControlsPanel.dataLayer`.
            colormap: Mirrors `HeatmapControlsPanel.colormap`.
            vmin_token: `HeatmapControlsPanel.vminToken()`'s own value —
                `"auto"` or a numeric string.
            vmax_token: `HeatmapControlsPanel.vmaxToken()`'s own value.
            show_rois: Mirrors Visual Inspection's "Show ROIs" checkbox.
            include_scale_bar: The export dialog's "Include scale bar"
                checkbox (ADR 53).
            hidden_categories: Categories to export black and leave out of
                the legend; `[]` exports every category.
        """
        # Resolved now, on the GUI thread — the export follows whatever
        # dataset Visual Inspection is showing when the user clicked.
        rel_dir = self._active_rel_dir(analysis_db_path)

        def _do_export() -> str:
            mz = float(mz_str) if mz_str else None
            vmin = None if vmin_token == "auto" else float(vmin_token)
            vmax = None if vmax_token == "auto" else float(vmax_token)
            n = core_export.export_visual_inspection_images(
                analysis_db_path,
                dest_folder,
                image_format,
                mode,
                mz=mz,
                obs_column=obs_column or None,
                layer=layer,
                colormap=colormap,
                vmin=vmin,
                vmax=vmax,
                show_rois=show_rois,
                rel_dir=rel_dir,
                include_scale_bar=include_scale_bar,
                hidden_categories=[str(c) for c in hidden_categories],
            )
            return f"Images exported for {n} sample(s) to {dest_folder}"

        self._run_export(_do_export)
