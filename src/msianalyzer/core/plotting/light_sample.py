"""
light_sample.py
A render-only, lazily filled AnnData for one sample's `.h5ad` (ADR 50).

Visual Inspection used to cache full `ad.read_h5ad` objects. Each one
loads `.X` plus every layer (`raw`, `TIC`, sometimes `localMoranI`, so
three or four copies of the matrix) and the whole `obs`. At 0.35–0.8 s
per sample, and with a 4-entry cache in front of 16-sample projects,
every feature switch re-read every file. That is what froze the GUI.

`LightSample` opens the file once with h5py and reads only what
rendering needs:

- at construction: `var`, `obsm["spatial"]`, the imported-dataset grid
  axes (`uns[GRID_UNS_KEY]`), and the names/dtypes of the obs columns and
  layers on disk (attributes only, no data);
- on demand: exactly one layer (or `.X`), converted to CSC so a feature
  column is a sub-millisecond slice instead of a scan of the whole CSR
  matrix;
- on demand: one obs column at a time.

`.adata` is a real `AnnData` holding just those pieces. So every existing
`core.plotting.heatmap` render function (and local Moran's I, and the
H&E overlay warp) works on it unchanged, with no parallel render code.
The one requirement: call `prepare(parts)` (or `ensure_layer` /
`ensure_obs`) before rendering, so the needed data is present.

Not thread-safe on its own. `HeatmapImageProvider` serializes access
with its lock.
"""

from __future__ import annotations

from pathlib import Path

import anndata as ad
import h5py
import numpy as np
import pandas as pd
from anndata.io import read_elem
from scipy.sparse import csc_matrix, issparse

from msianalyzer.core.plotting.heatmap import (
    _EXCLUDED_OBS_COLUMNS,
    _LOCAL_MORAN_LAYER,
    GRID_UNS_KEY,
)

#: Layer local Moran's I is scored against (`render_local_moran_heatmap`'s
#: own default), falling back to `.X` like every other layer lookup.
_LOCAL_MORAN_SOURCE_LAYER = "TIC"


def _is_numeric_element(elem) -> bool:
    """`heatmap.is_numeric_obs_column`'s rule (numeric, but not bool),
    decided from the on-disk element alone, without reading its data.
    Categoricals, strings and nullable booleans are groups or non-numeric
    datasets; a nullable integer is the one numeric group encoding."""
    encoding = elem.attrs.get("encoding-type", "")
    if isinstance(encoding, bytes):
        encoding = encoding.decode()
    if encoding == "nullable-integer":
        return True
    return isinstance(elem, h5py.Dataset) and elem.dtype.kind in "iuf"


def _as_csc(matrix):
    if issparse(matrix):
        return matrix.tocsc()
    return csc_matrix(np.asarray(matrix))


class LightSample:
    """One sample's `.h5ad`, loaded piecemeal for rendering. See the module
    docstring."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        with h5py.File(self.path, "r") as f:
            var = read_elem(f["var"])
            if "obsm" in f and "spatial" in f["obsm"]:
                xy = np.asarray(f["obsm"]["spatial"][:], dtype=float)
            else:
                xy = np.column_stack(
                    [read_elem(f["obs"]["x"]), read_elem(f["obs"]["y"])]
                ).astype(float)
            uns = {}
            if "uns" in f and GRID_UNS_KEY in f["uns"]:
                uns[GRID_UNS_KEY] = read_elem(f["uns"][GRID_UNS_KEY])
            obs_group = f["obs"]
            order = obs_group.attrs.get("column-order", [])
            self._obs_on_disk = [str(c) for c in order]
            self._obs_numeric = {c: _is_numeric_element(obs_group[c]) for c in self._obs_on_disk}
            self._layers_on_disk = (
                {str(k) for k in f["layers"].keys()} if "layers" in f else set()
            )
        n_obs = xy.shape[0]
        self.adata = ad.AnnData(
            obs=pd.DataFrame(index=pd.RangeIndex(n_obs).astype(str)),
            var=var,
            obsm={"spatial": xy},
            uns=uns,
        )

    # -- lazy loading ------------------------------------------------------

    def ensure_layer(self, layer: str) -> None:
        """Make `layer` available the way `heatmap._layer_column` looks it
        up: `adata.layers[layer]` if the file has that layer, else `.X`."""
        if layer in self._layers_on_disk:
            if layer not in self.adata.layers:
                with h5py.File(self.path, "r") as f:
                    self.adata.layers[layer] = _as_csc(read_elem(f["layers"][layer]))
        elif self.adata.X is None:
            with h5py.File(self.path, "r") as f:
                self.adata.X = _as_csc(read_elem(f["X"]))

    def ensure_obs(self, column: str) -> None:
        if column in self.adata.obs.columns or column not in self._obs_numeric:
            return
        with h5py.File(self.path, "r") as f:
            self.adata.obs[column] = read_elem(f["obs"][column])

    def prepare(self, parts: list[str]) -> None:
        """Load whatever `render_heatmap_by_target(self.adata, parts[1],
        parts)` will read — one obs column, or one layer."""
        target = parts[1]
        if target.startswith("obs:"):
            self.ensure_obs(target[len("obs:"):])
            return
        layer = parts[2]
        self.ensure_layer(_LOCAL_MORAN_SOURCE_LAYER if layer == _LOCAL_MORAN_LAYER else layer)

    # -- metadata ----------------------------------------------------------

    def obs_columns(self) -> list[dict]:
        """`heatmap.list_obs_columns`'s result, from metadata only."""
        return [
            {"name": c, "numeric": self._obs_numeric[c]}
            for c in self._obs_on_disk
            if c not in _EXCLUDED_OBS_COLUMNS
        ]

    def nbytes(self) -> int:
        """Approximate memory held — for the provider's cache budget."""
        total = self.adata.obsm["spatial"].nbytes
        matrices = list(self.adata.layers.values())
        if self.adata.X is not None:
            matrices.append(self.adata.X)
        for m in matrices:
            if issparse(m):
                total += m.data.nbytes + m.indices.nbytes + m.indptr.nbytes
            else:
                total += np.asarray(m).nbytes
        for col in self.adata.obs.columns:
            total += int(self.adata.obs[col].memory_usage(deep=False))
        return total
