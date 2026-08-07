from __future__ import annotations

from pathlib import Path
from typing import Any
from dataclasses import dataclass, fields
import yaml

import tomllib

import tomli_w

from msianalyzer.core.project.project import NotInProjectFolderError, get_project_folder


@dataclass
class IOConfig:
    project_folder: Path
    mzml_paths: list[Path]
    xml_paths: list[Path]
    db_paths: list[Path]
    out_dir: Path

    def __post_init__(self) -> None:

        self.project_folder = Path(self.project_folder)

        self.mzml_paths = [Path(m) for m in self.mzml_paths]

        self.xml_paths = [Path(x) for x in self.xml_paths]

        self.db_paths = [Path(d) for d in self.db_paths]

        self.out_dir = Path(self.out_dir)

    def resolve_paths(self) -> None:
        base_dir = self.project_folder.resolve()

        self.out_dir = (base_dir / self.out_dir).resolve()

        self.mzml_paths = [
            p if p.is_absolute() else (base_dir / p).resolve() for p in self.mzml_paths
        ]

        self.xml_paths = [
            p if p.is_absolute() else (base_dir / p).resolve() for p in self.xml_paths
        ]

        self.db_paths = [
            p if p.is_absolute() else (base_dir / p).resolve() for p in self.db_paths
        ]


@dataclass
class MS1Config:
    chunk_size: int = 2000
    bin_width: float = 0.0001
    min_mz: float = 70.0
    max_mz: float = 900.0


@dataclass
class CentroidConfig:
    prominence_factor: float = 0.1
    baseline_factor: float = 100
    baseline_method: str = "local"
    baseline_percentile: int = 10
    local_window: int = 501
    smooth_sigma: int = 10
    merge_ppm: float = 5


@dataclass
class PeakConfig:
    peak_height_threshold: float = 1000.0
    filter_mad: bool = True
    filter_mad_log: bool = True
    filter_mad_nmads: float = 2.5


@dataclass
class AlignMzSamples:
    align_ppm: float = 5.0
    sample_names: list[str] | None = None
    mz_decimals: int = 4


@dataclass
class H5adConfig:
    integration_ppm: float = 5.0
    batch_size: int = 1000
    scan_handling: str = "average"
    n_workers: int | None = None


# Maps group name (used as the nested key in dicts/files) -> dataclass type,
# and doubles as the canonical group order for __str__ and CLI wiring.
GROUPS: dict[str, type] = {
    "io": IOConfig,
    "ms1": MS1Config,
    "centroid": CentroidConfig,
    "peak": PeakConfig,
    "align": AlignMzSamples,
    "h5ad": H5adConfig,
}

# Human-readable titles for __str__, in the same order as GROUPS.
GROUP_TITLES: dict[str, str] = {
    "io": "input/output",
    "ms1": "average MS1",
    "centroid": "detect centroids",
    "peak": "peak threshold",
    "align": "align all mzs",
    "h5ad": "create h5ad",
}


class Config:
    version: int = 2

    def __init__(
        self,
        io: IOConfig,
        ms1: MS1Config | None = None,
        centroid: CentroidConfig | None = None,
        peak: PeakConfig | None = None,
        align: AlignMzSamples | None = None,
        h5ad: H5adConfig | None = None,
    ) -> None:
        self.io: IOConfig = io
        self.ms1: MS1Config = ms1 or MS1Config()
        self.centroid: CentroidConfig = centroid or CentroidConfig()
        self.peak: PeakConfig = peak or PeakConfig()
        self.align: AlignMzSamples = align or AlignMzSamples()
        self.h5ad: H5adConfig = h5ad or H5adConfig()

    # ------------------------------------------------------------------ #
    # (de)serialization helpers
    # ------------------------------------------------------------------ #

    def to_dict(self) -> dict[str, Any]:
        """Flatten to a nested, YAML/TOML-friendly dict (paths -> str)."""

        def convert(value: Any) -> Any:
            if isinstance(value, Path):
                return str(value)
            if isinstance(value, list) and value and isinstance(value[0], Path):
                return [str(v) for v in value]
            return value

        d: dict[str, Any] = {"version": self.version}
        for group_name in GROUPS:
            group_obj = getattr(self, group_name)
            d[group_name] = {
                f.name: convert(getattr(group_obj, f.name)) for f in fields(group_obj)
            }
        return d

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
    ) -> "Config":
        data = dict(data)

        file_version = data.pop("version", None)
        if file_version is not None and file_version != cls.version:
            raise ValueError(
                f"Config file version {file_version!r} does not match "
                f"expected version {cls.version!r}"
            )

        try:
            group_instances = {
                group_name: group_type(**data.get(group_name, {}))
                for group_name, group_type in GROUPS.items()
            }

            config = cls(**group_instances)

        except TypeError as e:
            raise ValueError(f"Invalid or incomplete config: {e}") from e

        return config

    # ------------------------------------------------------------------ #
    # loading
    # ------------------------------------------------------------------ #

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        """Load a config file and resolve relative paths."""

        path = Path(path).resolve()

        if path.suffix.lower() in (".yml", ".yaml"):
            with open(path, "r") as f:
                data = yaml.safe_load(f)

        elif path.suffix.lower() == ".toml":
            with open(path, "rb") as f:
                data = tomllib.load(f)

        else:
            raise ValueError(f"Unsupported config format {path.suffix!r}")

        if not isinstance(data, dict):
            raise ValueError(f"Config file {path} did not parse to a mapping")

        return cls.from_dict(
            data,
        )

    @classmethod
    def from_yaml(cls, path: str | Path) -> "Config":
        return cls.load(path)

    @classmethod
    def from_toml(cls, path: str | Path) -> "Config":
        return cls.load(path)

    # ------------------------------------------------------------------ #
    # exporting
    # ------------------------------------------------------------------ #

    def export(self, path: str | Path) -> None:
        """Export to a .yml/.yaml or .toml file, format inferred from suffix."""
        path = Path(path)
        suffix = path.suffix.lower()
        data = self.to_dict()

        if suffix in (".yml", ".yaml"):
            with open(path, "w") as f:
                yaml.safe_dump(data, f, sort_keys=False)
        elif suffix == ".toml":
            if tomli_w is None:
                raise RuntimeError(
                    "Writing TOML requires the 'tomli-w' package (pip install tomli-w)."
                )
            with open(path, "wb") as f:
                tomli_w.dump(data, f)
        else:
            raise ValueError(
                f"Unsupported config format {suffix!r}; use .yaml/.yml or .toml"
            )

    def to_yaml(self, path: str | Path) -> None:
        self.export(
            Path(path).with_suffix(".yaml") if Path(path).suffix == "" else path
        )

    def to_toml(self, path: str | Path) -> None:
        self.export(path)

    def __str__(self) -> str:
        width = max(
            len(f.name) for group_type in GROUPS.values() for f in fields(group_type)
        )

        lines = [f"Config(version={self.version})"]
        for group_name, title in GROUP_TITLES.items():
            group_obj = getattr(self, group_name)
            lines.append(f"  {title}:")
            for f in fields(group_obj):
                value = getattr(group_obj, f.name)
                lines.append(f"    {f.name:<{width}} = {value!r}")

        return "\n".join(lines)


def create_config_file(
    file: Path,
    project_folder: Path | None = None,
    mzml_files: list[Path] | None = None,
    xml_files: list[Path] | None = None,
    db_files: list[Path] | None = None,
    out_dir: Path | None = None,
    force: bool = False,
) -> None:
    # Safely assign empty lists if None was passed
    mzml_files = mzml_files if mzml_files is not None else []
    xml_files = xml_files if xml_files is not None else []
    db_files = db_files if db_files is not None else []
    out_dir = out_dir if out_dir is not None else Path(".")

    if file.exists() and not force:
        raise FileExistsError(
            "File already exists. If you want to override, use force = True."
        )

    if project_folder is None:
        project_folder = get_project_folder()
    else:
        if not project_folder.exists():
            raise FileNotFoundError(f"Project folder not found: {project_folder}.")
        if not (project_folder / ".msianalyzer.yml").exists():
            raise NotInProjectFolderError(
                f"No project found at provided folder {project_folder}."
            )

    io = IOConfig(
        project_folder=project_folder,
        mzml_paths=mzml_files,
        xml_paths=xml_files,
        db_paths=db_files,
        out_dir=out_dir,
    )

    config = Config(io=io)

    config.to_yaml(file)
