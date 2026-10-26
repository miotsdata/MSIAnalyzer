"""Project-relative paths, so a project folder can be moved, or opened from
another OS or mount point (e.g. a NAS seen as `/mnt/...` on Linux and as a
drive letter on Windows), without losing its analyses."""

from __future__ import annotations

import os
from pathlib import Path, PureWindowsPath


def relative_to_project(path: str | Path | None, project_folder: str | Path | None):
    """`path` as a string relative to `project_folder` when it lies inside
    it, for storing in project/config files. Paths outside the project (a
    shared spectral library, raw data on another share) and `None` are
    returned unchanged (as `str` for a path)."""
    if path is None:
        return None
    if not project_folder or not Path(path).is_absolute():
        return str(path)
    try:
        return Path(path).relative_to(Path(project_folder)).as_posix()
    except ValueError:
        return str(path)


def resolve_in_project(path: str | Path, project_folder: str | Path) -> Path:
    """Locate a stored path on this machine.

    A relative path is taken from `project_folder`. An absolute path was
    written on another machine, OS or mount, or before the project moved:
    the longest trailing part of it that exists under `project_folder` is
    used (`/data/Projects/P/output/run1` opened as `Z:\\P` becomes
    `Z:\\P\\output\\run1`). The opened project wins even when the old
    location still exists (a local copy of a project also on a NAS), so
    you always see the results of the folder you opened. Otherwise the
    path is returned unchanged: it lives outside the project, or is
    missing and callers report it.
    """
    raw = os.fspath(path)
    # PureWindowsPath splits on both `/` and `\`, so a path written on
    # either OS parses the same way on both.
    pure = PureWindowsPath(raw)
    if not pure.anchor:
        return Path(project_folder, *pure.parts)
    parts = pure.parts[1:]
    for i in range(len(parts)):
        candidate = Path(project_folder, *parts[i:])
        if candidate.exists():
            return candidate
    return Path(raw)
