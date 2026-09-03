from __future__ import annotations

from pathlib import Path
from typing import Any
from dataclasses import dataclass, fields
import yaml

import tomllib

import tomli_w

from msianalyzer.core.project.project import NotInProjectFolderError, get_project_folder

#: Directory (under the project folder) where parsed raw databases live by
#: default. Raw databases are immutable and shared across every analysis in the
#: project, so they belong here rather than inside an analysis output folder.
PARSED_DIRNAME = "parsed"


@dataclass
class IOConfig:
    """Input/output paths for a processing run.

    All fields are coerced to `pathlib.Path` (or lists thereof) in
    `__post_init__`. Relative paths can later be anchored to
    `project_folder` by calling `resolve_paths`.

    Attributes:
        project_folder: Root folder of the msianalyzer project.
        mzml_paths: Input mzML files to process.
        xml_paths: Raster XML files providing pixel timing, paired with
            `mzml_paths`.
        db_paths: Parsed raw-database path for each input, paired with
            `mzml_paths`. Entries left unset (or an empty list) fall back to
            `project_folder / "parsed" / "<mzml stem>.db"` — see
            `raw_db_paths`.
        out_dir: Directory where this analysis' outputs are written.
    """

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
        """Resolve `out_dir` and every input path against `project_folder`.

        Absolute paths are left unchanged; relative paths are interpreted
        relative to the resolved `project_folder` and made absolute.
        """
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

    def raw_db_paths(self) -> list[Path]:
        """Effective parsed raw-database path for each mzML input.

        For index ``i`` the path is ``db_paths[i]`` when provided, otherwise
        ``project_folder / PARSED_DIRNAME / "<mzml stem>.db"``. Raw databases
        are immutable and shared across analyses, so the default keeps them
        out of any single analysis' ``out_dir``.

        Returns:
            One path per entry in ``mzml_paths``.
        """
        default_dir = Path(self.project_folder) / PARSED_DIRNAME
        paths: list[Path] = []
        for i, mzml in enumerate(self.mzml_paths):
            if i < len(self.db_paths) and self.db_paths[i] is not None:
                paths.append(Path(self.db_paths[i]))
            else:
                paths.append(default_dir / f"{Path(mzml).stem}.db")
        return paths


@dataclass
class MS1Config:
    """Parameters for building the averaged MS1 spectrum.

    Attributes:
        chunk_size: Number of spectra read per database chunk.
        bin_width: Width of each m/z bin, in Da.
        min_mz: Lower bound of the binned m/z range.
        max_mz: Upper bound of the binned m/z range.
    """

    chunk_size: int = 2000
    bin_width: float = 0.0001
    min_mz: float = 70.0
    max_mz: float = 900.0


@dataclass
class CentroidConfig:
    """Parameters for centroid (peak) detection on the averaged MS1 spectrum.

    Attributes:
        prominence_factor: Minimum peak prominence as a multiple of the
            baseline.
        baseline_factor: Peaks must exceed baseline + `baseline_factor` *
            baseline.
        baseline_method: Either `"global"` or `"local"` baseline estimation.
        baseline_percentile: Percentile of non-zero intensities used as the
            baseline.
        local_window: Rolling window size, in bins, for local baseline
            estimation.
        smooth_sigma: Gaussian smoothing sigma, in bins, applied to the
            local baseline.
        merge_ppm: PPM tolerance for merging nearby detected peaks.
    """

    prominence_factor: float = 0.1
    baseline_factor: float = 100
    baseline_method: str = "local"
    baseline_percentile: int = 10
    local_window: int = 501
    smooth_sigma: int = 10
    merge_ppm: float = 5


@dataclass
class PeakConfig:
    """Parameters for filtering detected MS1 peaks by intensity.

    Attributes:
        peak_height_threshold: Absolute intensity cutoff used when
            `filter_mad` is False.
        filter_mad: If True, filter peaks with a median-absolute-deviation
            threshold instead of `peak_height_threshold`.
        filter_mad_log: Compute the MAD threshold in log10 intensity space.
        filter_mad_nmads: Number of MADs above the median to set the
            threshold.
    """

    peak_height_threshold: float = 1000.0
    filter_mad: bool = True
    filter_mad_log: bool = True
    filter_mad_nmads: float = 2.5


@dataclass
class AlignMzSamples:
    """Parameters for aligning m/z values across samples.

    Attributes:
        align_ppm: PPM tolerance for grouping peaks from different samples.
        sample_names: Column names for the samples; defaults to the mzML
            paths when None.
        mz_decimals: Number of decimal places to round aligned m/z values to.
    """

    align_ppm: float = 5.0
    sample_names: list[str] | None = None
    mz_decimals: int = 4


@dataclass
class GroupMs2Config:
    """Parameters for associating MS2 scans with aligned features.

    Stage A of annotation (`core.annotation.group_ms2`): every MS2 scan is
    snapped to a feature from `align_mz_across_samples`; nothing is
    filtered out, only flagged.

    Attributes:
        assoc_ppm: PPM tolerance for accepting a precursor-to-feature
            match. Should be >= `align.align_ppm` — it must cover the
            feature's own width plus the extra slack of a single
            survey-scan precursor. A warning is emitted at run time when it
            is tighter than `align.align_ppm`.
        include_unmatched: Keep MS2 scans that matched no feature (stored
            with a NULL feature id) instead of dropping them.
        default_isolation_half_width: Isolation half-width, in Da, assumed
            when a scan carries no isolation-window offsets.
        precursor_only_tic_frac: A scan is flagged `precursor_only` when at
            least this fraction of its fragment TIC lies within
            `precursor_only_mz_tol_da` of the precursor (i.e. fragmentation
            did not really occur).
        precursor_only_mz_tol_da: Half-width, in Da, of the "on the
            precursor" band used for the `precursor_only` test.
    """

    assoc_ppm: float = 10.0
    include_unmatched: bool = True
    default_isolation_half_width: float = 0.5
    precursor_only_tic_frac: float = 0.8
    precursor_only_mz_tol_da: float = 2.0


@dataclass
class H5adConfig:
    """Parameters for assembling the spatial `AnnData` (.h5ad) object.

    Attributes:
        integration_ppm: PPM tolerance for quantifying target m/z values.
        batch_size: Number of spectra per processing chunk.
        scan_handling: Either `"average"` or `"first"` MS1 scan per pixel.
        n_workers: Number of parallel CPU workers; defaults to
            `os.cpu_count()` when None.
    """

    integration_ppm: float = 5.0
    batch_size: int = 1000
    scan_handling: str = "average"
    n_workers: int | None = None


@dataclass
class AnalysisConfig:
    """Parameters for the per-analysis database.

    Attributes:
        db_name: File name for the analysis database, written inside
            `io.out_dir`. When None, a name is derived from the run id as
            `analysis_<run_id>.db`.
    """

    db_name: str | None = None


# Maps group name (used as the nested key in dicts/files) -> dataclass type,
# and doubles as the canonical group order for __str__ and CLI wiring.
GROUPS: dict[str, type] = {
    "io": IOConfig,
    "ms1": MS1Config,
    "centroid": CentroidConfig,
    "peak": PeakConfig,
    "align": AlignMzSamples,
    "group_ms2": GroupMs2Config,
    "h5ad": H5adConfig,
    "analysis": AnalysisConfig,
}

# Human-readable titles for __str__, in the same order as GROUPS.
GROUP_TITLES: dict[str, str] = {
    "io": "input/output",
    "ms1": "average MS1",
    "centroid": "detect centroids",
    "peak": "peak threshold",
    "align": "align all mzs",
    "group_ms2": "group MS2",
    "h5ad": "create h5ad",
    "analysis": "analysis database",
}


class Config:
    """Full configuration for a processing run, grouped by pipeline stage.

    Wraps one settings object per stage (`io`, `ms1`, `centroid`, `peak`,
    `align`, `group_ms2`, `h5ad`, `analysis`) and provides (de)serialization
    to and from YAML and TOML. Only `io` is required; the remaining groups
    fall back to their dataclass defaults.

    Attributes:
        version: Config schema version; checked on load.
        io: Input/output paths.
        ms1: Averaged MS1 spectrum parameters.
        centroid: Centroid detection parameters.
        peak: Peak filtering parameters.
        align: Cross-sample m/z alignment parameters.
        group_ms2: MS2-to-feature association parameters.
        h5ad: Spatial `AnnData` assembly parameters.
        analysis: Per-analysis database parameters.
    """

    version: int = 4

    def __init__(
        self,
        io: IOConfig,
        ms1: MS1Config | None = None,
        centroid: CentroidConfig | None = None,
        peak: PeakConfig | None = None,
        align: AlignMzSamples | None = None,
        group_ms2: GroupMs2Config | None = None,
        h5ad: H5adConfig | None = None,
        analysis: AnalysisConfig | None = None,
    ) -> None:
        self.io: IOConfig = io
        self.ms1: MS1Config = ms1 or MS1Config()
        self.centroid: CentroidConfig = centroid or CentroidConfig()
        self.peak: PeakConfig = peak or PeakConfig()
        self.align: AlignMzSamples = align or AlignMzSamples()
        self.group_ms2: GroupMs2Config = group_ms2 or GroupMs2Config()
        self.h5ad: H5adConfig = h5ad or H5adConfig()
        self.analysis: AnalysisConfig = analysis or AnalysisConfig()

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
        """Build a `Config` from a nested mapping.

        Args:
            data: Nested dict with an optional `"version"` key and one
                sub-mapping per config group. Missing groups use their
                dataclass defaults.

        Returns:
            The constructed `Config`.

        Raises:
            ValueError: If `data["version"]` does not match `Config.version`,
                or if a group mapping has invalid or missing keys.
        """
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
        """Load a `Config` from a YAML file.

        Thin wrapper around `load`.

        Args:
            path: Path to a `.yml`/`.yaml` file.

        Returns:
            The loaded `Config`.
        """
        return cls.load(path)

    @classmethod
    def from_toml(cls, path: str | Path) -> "Config":
        """Load a `Config` from a TOML file.

        Thin wrapper around `load`.

        Args:
            path: Path to a `.toml` file.

        Returns:
            The loaded `Config`.
        """
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
        """Export the config to a YAML file.

        Args:
            path: Output path. A missing suffix is replaced with `.yaml`.
        """
        self.export(
            Path(path).with_suffix(".yaml") if Path(path).suffix == "" else path
        )

    def to_toml(self, path: str | Path) -> None:
        """Export the config to a TOML file.

        Args:
            path: Output path, expected to end in `.toml`.
        """
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
    """Write a new config file pre-populated with input/output paths.

    Builds an `IOConfig` from the given paths and writes a default
    `Config` to `file` as YAML.

    Args:
        file: Destination path for the config file.
        project_folder: Project root. Located automatically from the
            current working directory when None.
        mzml_files: Input mzML paths. Defaults to an empty list.
        xml_files: Raster XML paths. Defaults to an empty list.
        db_files: SQLite database paths. Defaults to an empty list.
        out_dir: Output directory. Defaults to `Path(".")`.
        force: Overwrite `file` if it already exists. Defaults to False.

    Raises:
        FileExistsError: If `file` exists and `force` is False.
        FileNotFoundError: If `project_folder` is given but does not exist.
        NotInProjectFolderError: If `project_folder` is given but contains
            no `.msianalyzer.yml`.
    """
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
