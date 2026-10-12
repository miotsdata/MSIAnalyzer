"""Stdlib lazy module loading — Python 3.13's stand-in for 3.15's `lazy import`.

`ad = lazy_module("anndata")` returns a module object whose real import runs
on the first attribute access (`ad.read_h5ad`, `ad.AnnData`, ...), not at
module load. Used for the heavy scientific libraries the GUI only needs once
a dataset is actually opened, so they stay off the startup path.

Two rules for callers:
- Annotations must stay unevaluated (`from __future__ import annotations`),
  or `def f(a: ad.AnnData)` triggers the import at definition time.
- PyInstaller can't see a module named only in a string, so every name
  passed here must also be listed in the spec's `hiddenimports`.
"""

import importlib.util
import sys
from types import ModuleType


def lazy_module(name: str) -> ModuleType:
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.find_spec(name)
    if spec is None:
        raise ModuleNotFoundError(f"No module named {name!r}", name=name)
    loader = importlib.util.LazyLoader(spec.loader)
    spec.loader = loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    loader.exec_module(module)
    return module
