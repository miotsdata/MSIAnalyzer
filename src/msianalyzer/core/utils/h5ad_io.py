"""
h5ad_io.py
Element-level reads and writes on an `.h5ad` file, without loading the
AnnData (ADR 50).

An `.h5ad` stores each `obs` column, and each `uns` entry, as its own
HDF5 element, and `obs.attrs["column-order"]` lists the columns. So
adding, replacing or dropping one ROI column plus its `uns/rois` entry
touches only those elements. Loading and rewriting the whole object
(`ad.read_h5ad` + `write_h5ad`) costs seconds on a multi-GB file and is
what froze ROI saves, including the `merged.h5ad` sync.

Every function takes an open `h5py.File` (or group), so a caller can
batch several edits into one open.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any

import h5py
import numpy as np
import pandas as pd

from msianalyzer.core.utils.lazy_import import lazy_module

ad = lazy_module("anndata")

#: Held by every in-app `.h5ad` write. Two handles writing one HDF5 file at
#: once can corrupt it, and writes now run on several threads (the GUI
#: thread, `.var` refreshes, normalization, Moran's I).
write_lock = threading.RLock()


@contextmanager
def open_for_write(path):
    """`h5py.File(path, "r+")`, one writer at a time across threads."""
    with write_lock, h5py.File(path, "r+") as f:
        yield f


def _column_order(obs: h5py.Group) -> list[str]:
    order = obs.attrs.get("column-order", [])
    return [c.decode() if isinstance(c, bytes) else str(c) for c in order]


def _set_column_order(obs: h5py.Group, order: list[str]) -> None:
    obs.attrs["column-order"] = np.array(order, dtype=h5py.string_dtype())


def read_obs_column(f: h5py.File, name: str) -> Any:
    return ad.io.read_elem(f["obs"][name])


def read_obs_index(f: h5py.File) -> pd.Index:
    obs = f["obs"]
    index_key = obs.attrs.get("_index", "_index")
    if isinstance(index_key, bytes):
        index_key = index_key.decode()
    return pd.Index(ad.io.read_elem(obs[index_key]))


def has_obs_column(f: h5py.File, name: str) -> bool:
    return name in _column_order(f["obs"])


def set_obs_column(f: h5py.File, name: str, values: np.ndarray) -> None:
    """Add or replace one obs column in place."""
    obs = f["obs"]
    if name in obs:
        del obs[name]
    ad.io.write_elem(obs, name, np.asarray(values))
    order = _column_order(obs)
    if name not in order:
        _set_column_order(obs, order + [name])


def drop_obs_column(f: h5py.File, name: str) -> bool:
    obs = f["obs"]
    order = _column_order(obs)
    if name not in order and name not in obs:
        return False
    if name in obs:
        del obs[name]
    _set_column_order(obs, [c for c in order if c != name])
    return True


def read_uns(f: h5py.File, key: str, default: Any = None) -> Any:
    if "uns" in f and key in f["uns"]:
        return ad.io.read_elem(f["uns"][key])
    return default


def write_uns(f: h5py.File, key: str, value: Any) -> None:
    """Replace `uns[key]`. A `None` or empty dict removes it."""
    uns = f.require_group("uns")
    if key in uns:
        del uns[key]
    if value is None or (isinstance(value, dict) and not value):
        return
    ad.io.write_elem(uns, key, value)


#: Per-pixel count of features with a value above 0 (detected), from `.X`
#: (raw intensities; normalization only rescales a pixel, zeros stay zeros).
N_FEATURES_COLUMN = "nFeatures"


def count_detected_per_row(X, chunk_rows: int = 50_000) -> np.ndarray:
    """Values > 0 per row of an on-disk `.X` (CSR group or dense dataset),
    read `chunk_rows` rows at a time so a multi-GB `merged.h5ad` never has
    to fit in memory."""
    if isinstance(X, h5py.Group):  # CSR: indptr + data
        indptr = X["indptr"][:]
        n_rows = len(indptr) - 1
        counts = np.zeros(n_rows, dtype=np.int64)
        for start in range(0, n_rows, chunk_rows):
            stop = min(start + chunk_rows, n_rows)
            bounds = indptr[start:stop + 1] - indptr[start]
            # Running count of detected values; a row's count is the
            # difference at its two bounds (works for empty rows too).
            running = np.concatenate([[0], np.cumsum(X["data"][indptr[start]:indptr[stop]] > 0)])
            counts[start:stop] = np.diff(running[bounds])
        return counts
    return np.concatenate([
        (X[start:start + chunk_rows] > 0).sum(axis=1) for start in range(0, X.shape[0], chunk_rows)
    ]).astype(np.int64)


def ensure_n_features(path, chunk_rows: int = 50_000) -> bool:
    """Add `obs[N_FEATURES_COLUMN]` to an `.h5ad` made before the pipeline
    wrote it. Returns True if it was added, False if already there."""
    with open_for_write(path) as f:
        if has_obs_column(f, N_FEATURES_COLUMN):
            return False
        set_obs_column(f, N_FEATURES_COLUMN, count_detected_per_row(f["X"], chunk_rows))
    return True
