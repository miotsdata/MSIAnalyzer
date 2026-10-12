"""
imported_dataset.py
Re-import a notebook-analyzed `.h5ad` (the analysis' `merged.h5ad`, or one
sample's `.h5ad`, after QC / DimRed / clustering / ... outside this app)
back into an analysis as a *linked dataset* (ADR 49).

The source file is split once, at import time, into one `.h5ad` per
analysis sample under `<analysis folder>/imported/<slug>/` — the exact
per-sample layout every existing Visual Inspection / ROI / H&E overlay /
Moran's I code path already reads (`sample_h5ad_path` is the one
resolver), so none of them needs to know about merged files or about
pixel matching. The pipeline's own `.h5ad` files are never touched.

Per sample, the split:
  - maps every source row back to the pipeline sample's own pixel id
    (the source obs name minus `ad.concat(index_unique="-")`'s
    `-<sample>` suffix, falling back to matching x/y coordinates), and
    refuses the import if any row doesn't belong to that sample;
  - keeps the source's own obs name in `obs["source_obs_name"]` — how an
    ROI drawn later finds its rows again in the user's file
    (`write_roi_to_source`);
  - restores `obsm["spatial"]` from the pipeline sample, and stores the
    pipeline sample's *full* grid axes in `uns[GRID_UNS_KEY]` so a
    QC-filtered edge row/column can't shift the grid (see
    `heatmap.pixel_grid_indices`);
  - drops `obsp` (whole-dataset pixel graphs mean nothing once split);
  - keeps only per-sample ROI polygons in `uns["rois"]`: a source built
    from `merged.h5ad` carries its polygon-less entries, which take the
    pipeline sample's polygon of the same name (same grid) or are dropped.

Splitting runs one process per sample (`ProcessPoolExecutor`); each worker
opens the source in backed mode and only materializes its own sample's
rows, so peak memory is about one sample per worker, not the whole file.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import sqlite3
import time
import uuid
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import h5py
import numpy as np
import pandas as pd

from msianalyzer.core import analysis_db
from msianalyzer.core.plotting import roi
from msianalyzer.core.plotting.heatmap import GRID_UNS_KEY
from msianalyzer.core.utils import h5ad_io
from msianalyzer.core.utils.logging_utils import log_call, worker_logging
from msianalyzer.core.utils.lazy_import import lazy_module

ad = lazy_module("anndata")

logger = logging.getLogger(__name__)

__all__ = [
    "GRID_UNS_KEY",
    "IMPORTED_DIRNAME",
    "SOURCE_OBS_NAME_COLUMN",
    "H5adInspection",
    "ImportResult",
    "ImportedDatasetError",
    "delete_imported_dataset",
    "import_analyzed_h5ad",
    "inspect_h5ad",
    "remove_roi_from_source",
    "sample_h5ad_path",
    "write_roi_to_source",
]

#: Folder (inside the analysis folder) every imported dataset lives under.
IMPORTED_DIRNAME = "imported"
#: Obs column holding each row's obs name in the user's source file.
SOURCE_OBS_NAME_COLUMN = "source_obs_name"
#: A categorical/string obs column with at most this many distinct values
#: is offered as a "which sample is this row" candidate.
_MAX_SAMPLE_COLUMN_VALUES = 64
_VAR_NAME_MZ = re.compile(r"^mz_([0-9]+(?:\.[0-9]+)?)$")


class ImportedDatasetError(ValueError):
    """An import that can't proceed — the message is meant for the user."""


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


def sample_h5ad_path(
    analysis_db_path: Path | str, sample_name: str, rel_dir: str | None = None
) -> Path:
    """Where one sample's `.h5ad` lives for the pipeline output
    (`rel_dir=None`, next to the analysis database) or for an imported
    dataset (`rel_dir` = its `imported_datasets.rel_dir`). The single
    source of truth for this convention (GUI bridge, image providers,
    exports all resolve through it)."""
    base = Path(analysis_db_path).parent
    if rel_dir:
        base = base / rel_dir
    return base / f"{sample_name}.h5ad"


# ---------------------------------------------------------------------------
# Inspection
# ---------------------------------------------------------------------------


@dataclass
class H5adInspection:
    """What `inspect_h5ad` found — enough for the import dialog to
    propose a sample mapping without ever loading `X`."""

    path: str
    kind: str  # "merged" | "single"
    n_obs: int
    n_vars: int
    sample_column: str | None
    #: candidate sample column -> its sorted distinct values
    sample_values: dict[str, list[str]] = field(default_factory=dict)
    #: `uns["spatial"]` keys — the pipeline writes the sample name there
    spatial_keys: list[str] = field(default_factory=list)
    layers: list[str] = field(default_factory=list)
    obs_columns: list[str] = field(default_factory=list)
    #: The analysis the file came from — the pipeline stores its run id
    #: as `uns["project_id"]` (historical name; `create_spatial_adata`
    #: is passed the analysis id), and `ad.concat(uns_merge="unique")`
    #: keeps it in the merged file since every sample shares it.
    analysis_id: str | None = None
    has_mz: bool = True

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def _candidate_sample_columns(obs: pd.DataFrame) -> dict[str, list[str]]:
    out = {}
    for col in obs.columns:
        series = obs[col]
        if not (
            isinstance(series.dtype, pd.CategoricalDtype)
            or pd.api.types.is_object_dtype(series)
            or pd.api.types.is_string_dtype(series)
        ):
            continue
        values = pd.unique(series.dropna().astype(str))
        if 0 < len(values) <= _MAX_SAMPLE_COLUMN_VALUES:
            out[str(col)] = sorted(values)
    return out


def _layer_names(adata: ad.AnnData) -> list[str]:
    """`adata.layers` keys that are real layer names. `ad.concat` (and so
    the pipeline's own `merged.h5ad`) can carry a `None` key next to the
    real ones; it isn't selectable by name, so it's left out."""
    return [str(k) for k in adata.layers.keys() if k is not None]


def _has_mz(var: pd.DataFrame) -> bool:
    if "mz" in var.columns:
        return True
    return all(_VAR_NAME_MZ.match(str(n)) for n in var.index)


@log_call(source="path")
def inspect_h5ad(path: Path | str) -> H5adInspection:
    """Read a candidate source file's shape and metadata in backed mode
    (`X` is never loaded — these files are routinely several GB).

    A file with a `"sample"` obs column is `"merged"` (the pipeline's own
    `ad.concat(label="sample")` convention); anything else is `"single"`.

    Raises:
        ImportedDatasetError: not a readable `.h5ad`.
    """
    path = Path(path)
    try:
        adata = ad.read_h5ad(path, backed="r")
    except Exception as exc:  # noqa: BLE001 — surface any reader failure to the user
        raise ImportedDatasetError(f"Could not read {path.name} as an .h5ad file: {exc}") from exc
    try:
        candidates = _candidate_sample_columns(adata.obs)
        sample_column = "sample" if "sample" in adata.obs.columns else None
        project_id = adata.uns.get("project_id")
        spatial = adata.uns.get("spatial") or {}
        return H5adInspection(
            path=str(path),
            kind="merged" if sample_column else "single",
            n_obs=int(adata.n_obs),
            n_vars=int(adata.n_vars),
            sample_column=sample_column,
            sample_values=candidates,
            spatial_keys=[str(k) for k in spatial.keys()] if hasattr(spatial, "keys") else [],
            layers=_layer_names(adata),
            obs_columns=[str(c) for c in adata.obs.columns],
            analysis_id=str(project_id) if project_id is not None else None,
            has_mz=_has_mz(adata.var),
        )
    finally:
        adata.file.close()


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


@dataclass
class ImportResult:
    dataset_id: int
    rel_dir: str
    n_samples: int
    n_pixels: int
    layers: list[str]


def _slugify(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", name.strip()).strip("._")
    return slug or "dataset"


def _ensure_var_mz(adata: ad.AnnData, source_name: str) -> None:
    if "mz" in adata.var.columns:
        adata.var["mz"] = adata.var["mz"].astype(float)
        return
    matches = [_VAR_NAME_MZ.match(str(n)) for n in adata.var_names]
    if not all(matches):
        raise ImportedDatasetError(
            f"{source_name}: var has no 'mz' column and its var names aren't "
            "'mz_<value>' — can't tell which m/z each feature is."
        )
    adata.var["mz"] = [float(m.group(1)) for m in matches]


def _source_coordinates(adata: ad.AnnData) -> np.ndarray | None:
    if "spatial" in adata.obsm:
        return np.asarray(adata.obsm["spatial"], dtype=float)[:, :2]
    if {"x", "y"} <= set(adata.obs.columns):
        return adata.obs[["x", "y"]].to_numpy(dtype=float)
    return None


def _match_rows(
    sub: ad.AnnData, source_label: str, ref_names: pd.Index, ref_xy: np.ndarray,
    sample_name: str,
) -> np.ndarray:
    """Row index into the pipeline sample for every `sub` row — by pixel
    id (obs name minus the concat suffix), else by exact x/y."""
    suffix = f"-{source_label}"
    names = [
        n[: -len(suffix)] if suffix != "-" and n.endswith(suffix) else n
        for n in sub.obs_names.astype(str)
    ]
    idx = ref_names.get_indexer(names)
    if (idx < 0).any():
        coords = _source_coordinates(sub)
        if coords is not None:
            lookup = {(float(x), float(y)): i for i, (x, y) in enumerate(ref_xy)}
            idx = np.array(
                [lookup.get((float(x), float(y)), -1) for x, y in coords], dtype=int
            )
    n_bad = int((idx < 0).sum())
    if n_bad:
        raise ImportedDatasetError(
            f"{n_bad} of {sub.n_obs} rows mapped to sample {sample_name!r} match none "
            "of its pixels (neither by pixel id nor by x/y coordinates) — is this "
            "file from this analysis, and is the sample mapping right?"
        )
    if len(np.unique(idx)) != idx.size:
        raise ImportedDatasetError(
            f"Several rows mapped to sample {sample_name!r} resolve to the same pixel."
        )
    return idx


def _import_one_sample(
    source_path: str,
    sample_column: str | None,
    source_label: str,
    sample_name: str,
    pipeline_h5ad: str,
    out_path: str,
) -> tuple[str, int, list[str]]:
    """Worker: split one sample out of the source and write it. Top-level
    so `ProcessPoolExecutor` can pickle it.

    Returns:
        `(sample_name, n_pixels, layer_names)`.
    """
    t0 = time.perf_counter()
    src = ad.read_h5ad(source_path, backed="r")
    try:
        if sample_column is not None:
            mask = (src.obs[sample_column].astype(str) == source_label).to_numpy()
            if not mask.any():
                raise ImportedDatasetError(
                    f"No rows with {sample_column} == {source_label!r} in the source file."
                )
            sub = src[mask].to_memory()
        else:
            sub = src.to_memory()
    finally:
        src.file.close()

    t1 = time.perf_counter()
    ref = ad.read_h5ad(pipeline_h5ad, backed="r")
    try:
        ref_names = pd.Index(ref.obs_names.astype(str))
        ref_xy = np.asarray(ref.obsm["spatial"], dtype=float)[:, :2]
        ref_rois = ref.uns.get("rois")
    finally:
        ref.file.close()

    idx = _match_rows(sub, source_label, ref_names, ref_xy, sample_name)

    sub.obs[SOURCE_OBS_NAME_COLUMN] = np.asarray(sub.obs_names.astype(str), dtype=object)
    sub.obs_names = ref_names[idx]
    sub.obsm["spatial"] = ref_xy[idx]
    sub.uns[GRID_UNS_KEY] = {"x": np.unique(ref_xy[:, 0]), "y": np.unique(ref_xy[:, 1])}
    for key in list(sub.obsp.keys()):
        del sub.obsp[key]
    if "rois" in sub.uns:
        sub.uns["rois"] = roi.per_sample_rois(sub.uns["rois"], ref_rois)
    _ensure_var_mz(sub, Path(source_path).name)

    t2 = time.perf_counter()
    sub.write_h5ad(out_path)
    logger.debug(
        "import %s: read rows %.2f s, match pixels %.2f s, write %.2f s",
        sample_name, t1 - t0, t2 - t1, time.perf_counter() - t2,
    )
    return sample_name, int(sub.n_obs), _layer_names(sub)


@log_call(source="source_path")
def import_analyzed_h5ad(
    analysis_db_path: Path | str,
    source_path: Path | str,
    *,
    name: str,
    sample_column: str | None,
    sample_map: dict[str, str],
    n_workers: int | None = None,
    progress: Callable[[int, int, str], None] | None = None,
) -> ImportResult:
    """Split `source_path` into per-sample `.h5ad` files under
    `<analysis folder>/imported/<slug>/` and register it as a dataset of
    this analysis.

    Args:
        analysis_db_path: The analysis database the dataset is linked to.
        source_path: The notebook-analyzed `.h5ad` (merged or single).
        name: Display name — unique within the analysis.
        sample_column: The obs column naming each row's sample (a merged
            file); `None` for a single-sample file.
        sample_map: Source label -> analysis sample name. For a merged
            file, one entry per sample to import (labels not listed are
            skipped). For a single-sample file, exactly one entry; its key
            is only used to strip an `-<label>` obs-name suffix.
        n_workers: Parallel sample processes (default: `os.cpu_count()`,
            capped at the number of samples).
        progress: Called as `progress(done, total, sample_name)` after
            each sample finishes.

    Raises:
        ImportedDatasetError: duplicate name, unknown sample, missing
            pipeline `.h5ad`, or rows that don't belong to their sample.
            Nothing is left on disk or in the database on failure.
    """
    analysis_db_path = Path(analysis_db_path)
    source_path = Path(source_path).resolve()
    name = name.strip()
    if not name:
        raise ImportedDatasetError("The dataset needs a name.")
    if not sample_map:
        raise ImportedDatasetError("No samples selected to import.")
    if sample_column is None and len(sample_map) != 1:
        raise ImportedDatasetError("A single-sample file maps to exactly one sample.")

    existing = analysis_db.load_imported_datasets(analysis_db_path)
    if not existing.empty and name in set(existing["name"]):
        raise ImportedDatasetError(f"A dataset named {name!r} already exists.")

    known = set(analysis_db.load_samples(analysis_db_path).get("name", pd.Series(dtype=str)))
    for sample_name in sample_map.values():
        if sample_name not in known:
            raise ImportedDatasetError(
                f"{sample_name!r} is not a sample of this analysis."
            )
        if not sample_h5ad_path(analysis_db_path, sample_name).exists():
            raise ImportedDatasetError(
                f"Sample {sample_name!r} has no pipeline .h5ad to match pixels against."
            )
    if len(set(sample_map.values())) != len(sample_map):
        raise ImportedDatasetError("Two source samples are mapped to the same analysis sample.")

    slug = _slugify(name)
    rel_dir = f"{IMPORTED_DIRNAME}/{slug}"
    if (analysis_db_path.parent / rel_dir).exists():
        rel_dir = f"{IMPORTED_DIRNAME}/{slug}_{uuid.uuid4().hex[:6]}"
    final_dir = analysis_db_path.parent / rel_dir
    tmp_dir = final_dir.with_name(f".{final_dir.name}.partial")
    shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir(parents=True)

    total = len(sample_map)
    workers = max(1, min(n_workers or os.cpu_count() or 1, total))
    labels_by_sample = {s: label for label, s in sample_map.items()}
    pixels: dict[str, int] = {}
    layers: list[str] = []
    t_start = time.perf_counter()
    try:
        with worker_logging() as (log_queue, initializer):
            with ProcessPoolExecutor(
                max_workers=workers, initializer=initializer, initargs=(log_queue,)
            ) as executor:
                futures = [
                    executor.submit(
                        _import_one_sample,
                        str(source_path),
                        sample_column,
                        str(label),
                        sample_name,
                        str(sample_h5ad_path(analysis_db_path, sample_name)),
                        str(tmp_dir / f"{sample_name}.h5ad"),
                    )
                    for label, sample_name in sample_map.items()
                ]
                logger.debug(
                    "import: %d worker(s) started, %d task(s) submitted in %.2f s",
                    workers, total, time.perf_counter() - t_start,
                )
                for done, future in enumerate(as_completed(futures), start=1):
                    sample_name, n_pixels, sample_layers = future.result()
                    logger.debug(
                        "import: %s done (%d/%d) at %.2f s",
                        sample_name, done, total, time.perf_counter() - t_start,
                    )
                    pixels[sample_name] = n_pixels
                    layers = layers or sample_layers
                    if progress is not None:
                        progress(done, total, sample_name)

        tmp_dir.rename(final_dir)
        try:
            dataset_id = analysis_db.insert_imported_dataset(
                analysis_db_path,
                name=name,
                rel_dir=rel_dir,
                source_path=str(source_path),
                source_kind="merged" if sample_column is not None else "single",
                sample_column=sample_column,
                layers=layers,
                samples=[
                    (s, labels_by_sample[s] if sample_column is not None else None, n)
                    for s, n in pixels.items()
                ],
            )
        except (sqlite3.Error, ValueError) as exc:
            shutil.rmtree(final_dir, ignore_errors=True)
            raise ImportedDatasetError(f"Could not register the dataset: {exc}") from exc
    except BaseException:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

    logger.info(
        "imported %s as dataset %r (%d samples, %d pixels) -> %s",
        source_path, name, len(pixels), sum(pixels.values()), rel_dir,
    )
    return ImportResult(
        dataset_id=dataset_id,
        rel_dir=rel_dir,
        n_samples=len(pixels),
        n_pixels=sum(pixels.values()),
        layers=layers,
    )


@log_call(source="analysis_db_path")
def delete_imported_dataset(analysis_db_path: Path | str, dataset_id: int) -> bool:
    """Remove an imported dataset's per-sample files and its database rows.
    The user's own source file is never touched.

    Returns:
        True if the dataset existed.
    """
    info = analysis_db.load_imported_dataset(analysis_db_path, dataset_id)
    if info is None:
        return False
    folder = Path(analysis_db_path).parent / info["rel_dir"]
    # Guard: only ever delete inside this analysis' own imported/ folder.
    imported_root = (Path(analysis_db_path).parent / IMPORTED_DIRNAME).resolve()
    if folder.resolve().parent == imported_root:
        shutil.rmtree(folder, ignore_errors=True)
    return analysis_db.delete_imported_dataset_rows(analysis_db_path, dataset_id)


# ---------------------------------------------------------------------------
# ROI write-back into the user's source file (obs column only)
# ---------------------------------------------------------------------------


@log_call(source="source_path")
def write_roi_to_source(
    source_path: Path | str,
    column: str,
    source_obs_names: pd.Series | np.ndarray | list,
    mask: np.ndarray,
) -> bool:
    """Set one ROI's boolean obs column in the user's source `.h5ad` for
    one sample's rows — `True` where `mask`, `False` for that sample's
    other rows; other samples' rows keep whatever they had (`False` when
    the column is new).

    Only that one column is written. The obs index is read to find the
    rows, and nothing else is loaded or rewritten (`core.utils.h5ad_io`,
    ADR 50).

    Args:
        source_path: The user's source file (merged or single).
        column: `roi.roi_obs_column(name)`.
        source_obs_names: That sample's rows, as source obs names (the
            imported file's `obs["source_obs_name"]`, same order as `mask`).
        mask: Per-row membership, parallel to `source_obs_names`.

    Returns:
        True once written.

    Raises:
        FileNotFoundError: the source file is gone.
        OSError: it can't be opened for writing (e.g. locked by a notebook).
    """
    source_path = Path(source_path)
    if not source_path.exists():
        raise FileNotFoundError(source_path)
    names = pd.Index(np.asarray(source_obs_names, dtype=str))
    values = np.asarray(mask, dtype=bool)

    with h5py.File(source_path, "r+") as f:
        index = h5ad_io.read_obs_index(f).astype(str)
        rows = index.get_indexer(names)
        found = rows >= 0
        if not found.all():
            logger.warning(
                "%s: %d ROI rows no longer exist in the source file; skipped",
                source_path, int((~found).sum()),
            )
        col = (
            np.asarray(h5ad_io.read_obs_column(f, column), dtype=bool).copy()
            if h5ad_io.has_obs_column(f, column) else np.zeros(len(index), dtype=bool)
        )
        col[rows[found]] = values[found]
        h5ad_io.set_obs_column(f, column, col)
    return True


@log_call(source="source_path")
def remove_roi_from_source(
    source_path: Path | str,
    column: str,
    source_obs_names: pd.Series | np.ndarray | list,
) -> bool:
    """Clear one sample's rows of an ROI column in the source file; drop
    the column entirely once no row is `True` any more.

    Returns:
        True if the file was changed; False if the column wasn't there.

    Raises:
        FileNotFoundError / OSError: as `write_roi_to_source`.
    """
    source_path = Path(source_path)
    if not source_path.exists():
        raise FileNotFoundError(source_path)
    names = pd.Index(np.asarray(source_obs_names, dtype=str))

    with h5py.File(source_path, "r+") as f:
        if not h5ad_io.has_obs_column(f, column):
            return False
        index = h5ad_io.read_obs_index(f).astype(str)
        rows = index.get_indexer(names)
        col = np.asarray(h5ad_io.read_obs_column(f, column), dtype=bool).copy()
        col[rows[rows >= 0]] = False
        if col.any():
            h5ad_io.set_obs_column(f, column, col)
        else:
            h5ad_io.drop_obs_column(f, column)
    return True


# ---------------------------------------------------------------------------
# Moran's I on an imported dataset
# ---------------------------------------------------------------------------


@log_call(source="analysis_db_path")
def compute_dataset_spatial_correlation(
    analysis_db_path: Path | str,
    dataset_id: int,
    *,
    layer: str,
    run_global: bool = True,
    run_local: bool = False,
    n_workers: int | None = None,
    progress: Callable[[int, int, str], None] | None = None,
) -> int:
    """Global and/or local Moran's I for every sample file of an imported
    dataset, written into those files exactly as the pipeline's
    `spatial_correlation` stage writes the pipeline's own
    (`uns["moranI"]`, `layers["localMoranI"]`), so the Spatial Correlation
    section reads them unchanged.

    Samples run one after another, and each is parallelized internally.
    That is the same shape as the pipeline stage:
    `compute_spatial_autocorr`'s squidpy `n_jobs`, and
    `compute_local_moran_layer`'s feature-chunked process pool.

    Args:
        layer: The `.layers` key to score; anything else (e.g. `"X"`)
            falls back to `.X`, the same as the pipeline stage.
        n_workers: CPU parallelism per sample (`None` = every core).

    Returns:
        The number of samples processed.

    Raises:
        ImportedDatasetError: unknown dataset, or neither pass selected.
    """
    # Local imports: squidpy (scanpy/numba) is heavy, and this module is
    # imported on the GUI's startup path.
    from msianalyzer.core.spatial.local_moran import compute_local_moran_layer
    from msianalyzer.core.spatial.moran import compute_spatial_autocorr

    if not (run_global or run_local):
        raise ImportedDatasetError("Select global and/or local Moran's I.")
    info = analysis_db.load_imported_dataset(analysis_db_path, dataset_id)
    if info is None:
        raise ImportedDatasetError("That imported dataset no longer exists.")
    n_cpu = n_workers or os.cpu_count() or 1
    samples = [s["name"] for s in info["samples"]]
    for done, sample_name in enumerate(samples, start=1):
        path = sample_h5ad_path(analysis_db_path, sample_name, info["rel_dir"])
        if run_global:
            compute_spatial_autocorr(path, layer=layer, n_jobs=n_cpu)
        if run_local:
            compute_local_moran_layer(path, layer=layer, n_workers=n_cpu)
        if progress is not None:
            progress(done, len(samples), sample_name)
    return len(samples)
