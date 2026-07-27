from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

import tomllib

import tomli_w


class Config:
    version: int = 1

    def __init__(
        self,
        mzml_paths: list[str | Path],
        xml_paths: list[str | Path],
        out_dir: str | Path,
        # Average MS1
        ms1_chunk_size: int = 2000,
        ms1_bin_width: float = 0.0001,
        ms1_min_mz: float = 70.0,
        ms1_max_mz: float = 900.0,
        # Detect centroids
        snr_threshold: float = 3.0,
        min_prominence_factor: float = 0.1,
        min_distance_bins: int = 5,
        # Peak threshold
        peak_height_threshold: float = 1000.0,
        # Find all mzs
        merge_mz_ppm: float = 5.0,
        sample_names: list[str] | None = None,
        # Create h5ad
        integration_ppm: float = 5.0,
        integration_batch_size: int = 1000,
        integration_scan_handling: str = "average",
        n_workers: int | None = None,
    ) -> None:
        self.mzml_paths: list[Path] = [Path(m) for m in mzml_paths]
        self.xml_paths: list[Path] = [Path(x) for x in xml_paths]
        self.out_dir: Path = Path(out_dir)

        # Average MS1
        self.ms1_chunk_size: int = ms1_chunk_size
        self.ms1_bin_width: float = ms1_bin_width
        self.ms1_min_mz: float = ms1_min_mz
        self.ms1_max_mz: float = ms1_max_mz

        # Detect centroids
        self.snr_threshold: float = snr_threshold
        self.min_prominence_factor: float = min_prominence_factor
        self.min_distance_bins: int = min_distance_bins

        # Peak threshold
        self.peak_height_threshold: float = peak_height_threshold

        # Find all mzs
        self.merge_mz_ppm: float = merge_mz_ppm
        self.sample_names: list[str] | None = sample_names

        # Create h5ad
        self.integration_ppm: float = integration_ppm
        self.integration_batch_size: int = integration_batch_size
        self.integration_scan_handling: str = integration_scan_handling
        self.n_workers: int | None = n_workers

    # ------------------------------------------------------------------ #
    # (de)serialization helpers
    # ------------------------------------------------------------------ #

    def to_dict(self) -> dict[str, Any]:
        """Flatten to a plain, YAML/TOML-friendly dict (paths -> str)."""
        d: dict[str, Any] = {"version": self.version}
        for key, value in vars(self).items():
            if isinstance(value, Path):
                d[key] = str(value)
            elif isinstance(value, list) and value and isinstance(value[0], Path):
                d[key] = [str(v) for v in value]
            else:
                d[key] = value
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Config":
        data = dict(data)  # don't mutate caller's dict
        file_version = data.pop("version", None)
        if file_version is not None and file_version != cls.version:
            raise ValueError(
                f"Config file version {file_version!r} does not match "
                f"expected version {cls.version!r}"
            )

        try:
            return cls(**data)
        except TypeError as e:
            raise ValueError(f"Invalid or incomplete config: {e}") from e

    # ------------------------------------------------------------------ #
    # loading
    # ------------------------------------------------------------------ #

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        """Load from a .yml/.yaml or .toml file, format inferred from suffix."""
        path = Path(path)
        suffix = path.suffix.lower()

        if suffix in (".yml", ".yaml"):
            with open(path, "r") as f:
                data = yaml.safe_load(f)
        elif suffix == ".toml":
            with open(path, "rb") as f:
                data = tomllib.load(f)
        else:
            raise ValueError(
                f"Unsupported config format {suffix!r}; use .yaml/.yml or .toml"
            )

        if not isinstance(data, dict):
            raise ValueError(f"Config file {path} did not parse to a mapping")

        return cls.from_dict(data)

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
        groups: list[tuple[str, list[str]]] = [
            ("input/output", ["mzml_paths", "xml_paths", "out_dir"]),
            (
                "average MS1",
                ["ms1_chunk_size", "ms1_bin_width", "ms1_min_mz", "ms1_max_mz"],
            ),
            (
                "detect centroids",
                ["snr_threshold", "min_prominence_factor", "min_distance_bins"],
            ),
            ("peak threshold", ["peak_height_threshold"]),
            ("find all mzs", ["merge_mz_ppm", "sample_names"]),
            (
                "create h5ad",
                [
                    "integration_ppm",
                    "integration_batch_size",
                    "integration_scan_handling",
                    "n_workers",
                ],
            ),
        ]

        width = max(len(attr) for _, attrs in groups for attr in attrs)

        lines = [f"Config(version={self.version})"]
        for title, attrs in groups:
            lines.append(f"  {title}:")
            for attr in attrs:
                value = getattr(self, attr)
                lines.append(f"    {attr:<{width}} = {value!r}")

        return "\n".join(lines)
