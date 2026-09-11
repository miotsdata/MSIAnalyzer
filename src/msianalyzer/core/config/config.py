from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from dataclasses import dataclass, field, fields
import yaml

import tomllib

import tomli_w

from msianalyzer.core.project.project import NotInProjectFolderError, get_project_folder
from msianalyzer.core.utils.logging_utils import log_call

logger = logging.getLogger(__name__)

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
        filter_mad: If True, filter peaks with a median-absolute-deviation
            threshold instead of `peak_height_threshold`.
        filter_mad_log: Compute the MAD threshold in log10 intensity space.
            Only used when `filter_mad` is True.
        filter_mad_nmads: Number of MADs above the median to set the
            threshold. Only used when `filter_mad` is True.
        peak_height_threshold: Absolute intensity cutoff used when
            `filter_mad` is False.
    """

    filter_mad: bool = True
    filter_mad_log: bool = field(
        default=True, metadata={"enabled_when": "filter_mad"}
    )
    filter_mad_nmads: float = field(
        default=2.5, metadata={"enabled_when": "filter_mad"}
    )
    peak_height_threshold: float = field(
        default=1000.0, metadata={"enabled_when": "not filter_mad"}
    )


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
        flat_fragmentation_min_peaks: A scan needs at least this many peaks
            (after `flat_fragmentation_min_rel_intensity` filtering) before
            the `flat_fragmentation` test is even applied; below it the
            coefficient of variation is too noisy a signal to trust, so the
            scan is left unflagged (`False`).
        flat_fragmentation_cv_threshold: A scan is flagged
            `flat_fragmentation` when its surviving peaks' coefficient of
            variation (`std(intensity) / mean(intensity)`) is `<=` this
            value — many peaks at different m/z but near-identical height,
            more consistent with chemical/electronic noise or an isobaric
            co-isolation smear than real CID/HCD fragmentation (which decays:
            one or a few dominant fragments, several minor ones, high CV).
            Soft QC flag, not a filter — flagged scans are still scored and
            stored.
        flat_fragmentation_min_rel_intensity: Peaks below this fraction of
            the scan's base peak are dropped before both the peak count and
            the CV are computed (independent of `AnnotateConfig.
            noise_threshold` — this stage runs before annotation).
    """

    assoc_ppm: float = 10.0
    include_unmatched: bool = True
    default_isolation_half_width: float = 0.5
    precursor_only_tic_frac: float = 0.8
    precursor_only_mz_tol_da: float = 2.0
    flat_fragmentation_min_peaks: int = 3
    flat_fragmentation_cv_threshold: float = 0.2
    flat_fragmentation_min_rel_intensity: float = 0.01


@dataclass
class PurityConfig:
    """Parameters for the precursor-ion-purity stage.

    Stage A' of annotation (`core.annotation.precursor_purity`): for every
    MS2 scan, measure how much of the isolation-window ion current in its
    *parent* MS1 scan (and the next MS1 scan when it lies on the same
    raster line) belonged to the precursor. Produces `purity`,
    `n_peaks_in_window` and `runner_up_rel_int` per scan — a per-acquisition
    chimericity signal that, unlike the grouper's `n_features_in_window`,
    does not depend on the analysis-wide feature list.

    Attributes:
        enabled: Run the stage. When False it is skipped entirely.
        ppm_precursor_match: ppm tolerance for deciding which in-window MS1
            peak is the precursor (matched against `precursor_mz`, or
            `isolation_window_target` when the scan carries no precursor
            m/z).
        default_half_window_da: Isolation half-width, in Da, assumed when a
            scan carries no isolation-window offsets.
        min_rel_intensity: In-window MS1 peaks below this fraction of the
            window's base peak are dropped before counting and scoring.
        merge_ppm: ppm tolerance for merging split profile peaks within the
            window slice.
        use_next_ms1: Interpolate purity across the parent MS1 and the
            immediately following MS1 scan when the latter is the same
            pixel, or an adjacent pixel on the same raster line. When False,
            only the parent MS1 is used.
        max_interpixel_gap_sec: Largest tolerated time gap between the
            parent pixel and an adjacent-line next pixel for interpolation.
            None derives it per sample from the median in-line pixel gap.
        precursor_confirm_ppm: Half-width, in ppm, of the band around the
            recorded `precursor_mz` used for the peak-detection-free
            confirmation: `precursor_frac = I(band) / I(isolation window)`.
        precursor_confirm_min_frac: `precursor_frac >= this` sets the
            `precursor_confirmed` flag — the precursor carries at least this
            fraction of the isolation window's above-baseline ion current in
            its own parent MS1.
        precursor_snap_ppm: Snap the recorded `precursor_mz` to the nearest
            parent-MS1 local maximum within this many ppm (stored as
            `precursor_mz_snapped`; a no-op when the recorded value is
            already on a peak). `0` disables snapping.
        n_workers: Number of samples to score in parallel, one process per
            sample. `None` / `0` uses `os.cpu_count()`; `1` forces the serial
            path. Capped at the sample count. Each worker holds one sample's
            in-memory scan index, so lower this if memory is tight.
    """

    enabled: bool = True
    ppm_precursor_match: float = 20.0
    default_half_window_da: float = 0.5
    min_rel_intensity: float = 0.01
    merge_ppm: float = 5.0
    use_next_ms1: bool = True
    max_interpixel_gap_sec: float | None = None
    precursor_confirm_ppm: float = 25.0
    precursor_confirm_min_frac: float = 0.01
    precursor_snap_ppm: float = 15.0
    n_workers: int | None = None


@dataclass
class AnnotateConfig:
    """Parameters for annotating MS2 scans against spectral libraries.

    Stage B of annotation (`core.annotation.annotate`): every MS2 scan that
    the grouper snapped to a feature is compared against library spectra
    whose precursor m/z is near that feature's m/z, scored with a
    coverage-aware reverse dot product, and every candidate sharing at
    least `min_matched_peaks` fragments is stored with its `rank_ms2`.

    Leaving `library_path` empty (`None` or `[]`) disables the whole step.

    Attributes:
        library_path: Path to a libviz library database, or a list of them.
            Candidates from every library are pooled per scan before
            ranking. When None/empty no annotation is performed.
        noise_threshold: Fraction in `[0, 1]`. After both spectra are
            max-normalised to 1, peaks below this fraction of the base peak
            are dropped (empirical and library alike).
        candidate_ppm: PPM tolerance for pulling library candidates — a
            library spectrum is a candidate when its precursor m/z is within
            this of the master feature m/z.
        fragment_ppm: PPM tolerance for aligning individual fragment peaks
            during scoring.
        mz_power: MSDial-style m/z weighting exponent in the dot product.
        int_power: MSDial-style intensity weighting exponent.
        score_weight_dot: Exponent applied to `dot_product_score` when
            combining it with `lib_coverage` / `emp_coverage` into the
            stored `score` (see `spectral_match.reverse_dot_product`).
            Default `1.0`.
        score_weight_lib_coverage: Exponent applied to `lib_coverage`.
            Default `0.5`.
        score_weight_emp_coverage: Exponent applied to `emp_coverage`.
            Default `0.5`. The defaults reproduce the original
            `dot_product_score * sqrt(lib_coverage * emp_coverage)`
            formula. Lower this when real, library-absent background/matrix
            peaks in the empirical spectrum are suppressing otherwise good
            matches (high dot product, high library coverage, low empirical
            coverage) — set to `0` to drop the term entirely. The unweighted
            `dot_product_score` / `lib_coverage` / `emp_coverage` /
            `coverage_score` columns are always stored too, so a re-run is
            the only way to see a new weighting reflected in `score`.
        min_matched_peaks: A candidate is stored only when it shares at
            least this many fragment peaks with the filtered empirical
            spectrum.
        min_purity: When set, skip scans whose precursor-ion purity (from
            the `purity` stage) is known and below this value. `None`
            annotates every scan regardless of purity. Every stored row
            still carries the scan's `purity` and `runner_up_rel_int`.
        annotate_chimeric: Score scans whose isolation window held more than
            one feature (against their primary feature). They are always
            flagged `is_chimeric`; set False to skip them entirely.
        store_filtered_spectra: Persist the noise-filtered, max-normalised
            m/z + intensity of both the empirical scan and the matched
            library spectrum on every annotation row, for later mirror
            plots. Set False to keep the table small.
        batch_size: Number of features handed to each worker process.
        n_workers: Parallel worker count; defaults to `os.cpu_count()` when
            None.
    """

    library_path: str | list[str] | None = None
    noise_threshold: float = 0.01
    candidate_ppm: float = 10.0
    fragment_ppm: float = 10.0
    mz_power: float = 2.0
    int_power: float = 0.5
    score_weight_dot: float = 1.0
    score_weight_lib_coverage: float = 0.5
    score_weight_emp_coverage: float = 0.5
    min_matched_peaks: int = 1
    min_purity: float | None = None
    annotate_chimeric: bool = True
    store_filtered_spectra: bool = True
    batch_size: int = 200
    n_workers: int | None = None


@dataclass
class ConsensusConfig:
    """Parameters for the per-feature MS2 consensus stage.

    Stage A'' of annotation (`core.annotation.consensus`): for each feature
    that carries MS2, pick the single most trustworthy scan by folding the
    best library score (when annotation ran), the precursor-ion `purity`
    and the fragment-peak count into one `consensus_score`. Output:
    `feature_ms2_consensus`, one row per feature.

    Attributes:
        enabled: Run the stage. When False it is skipped entirely.
        target_peaks: Fragment-peak count at which the peak-richness term
            saturates to 1; scans with fewer peaks are scaled down linearly.
        neutral_purity: Purity value assumed for a scan the purity stage
            could not score (`precursor_found = 0`, or the stage disabled).
        min_purity: When set, scans with a known purity below this are
            excluded from the pick (they still count in `n_ms2`).
    """

    enabled: bool = True
    target_peaks: int = 10
    neutral_purity: float = 0.5
    min_purity: float | None = None


@dataclass
class ReportConfig:
    """Parameters for the end-of-run summary report.

    `core.report.summary`: after every other stage, write
    `summary_report.html` + `summary.json` to `io.out_dir` — per-sample
    scan / peak counts, a feature-overlap UpSet plot, and the MS2
    association / `n_features_in_window` / purity distributions.

    Attributes:
        enabled: Build the report. When False it is skipped.
        overlap_top_n: Maximum number of sample-combination bars drawn in
            the feature-overlap UpSet plot (largest first).
        purity_cutoff: Reference line drawn on the purity histogram and used
            for the "low purity" count in the report summary.
    """

    enabled: bool = True
    overlap_top_n: int = 30
    purity_cutoff: float = 0.8


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


@dataclass
class NormalizationConfig:
    """Parameters for the TIC-normalization stage.

    `core.utils.tic_normalization`: run after `h5ad` assembly, once all
    per-sample `.h5ad` files exist. Computes each pixel's total ion current
    (`obs['tic']`, already present on every `.h5ad`) relative to the
    dataset-wide median TIC (across every pixel of every sample in the
    analysis), uses that ratio to normalize each feature's raw intensity,
    then log1p-compresses the result. Writes `layers['raw']` (untouched
    copy of `.X`) and `layers['TIC']` (the normalized matrix) back into
    every per-sample `.h5ad`, and persists the full cross-sample
    concatenation as `merged.h5ad` in `io.out_dir` — the median needs every
    sample anyway, and the merged object is reused by later, not-yet-built
    cross-sample analyses.

    Attributes:
        enabled: Run the stage. When False it is skipped entirely — no
            `raw`/`TIC` layers are added to the per-sample `.h5ad` files,
            and no `merged.h5ad` is written.
    """

    enabled: bool = True


# Maps group name (used as the nested key in dicts/files) -> dataclass type,
# and doubles as the canonical group order for __str__ and CLI wiring.
GROUPS: dict[str, type] = {
    "io": IOConfig,
    "ms1": MS1Config,
    "centroid": CentroidConfig,
    "peak": PeakConfig,
    "align": AlignMzSamples,
    "group_ms2": GroupMs2Config,
    "purity": PurityConfig,
    "annotate": AnnotateConfig,
    "consensus": ConsensusConfig,
    "report": ReportConfig,
    "h5ad": H5adConfig,
    "normalization": NormalizationConfig,
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
    "purity": "precursor purity",
    "annotate": "annotate MS2",
    "consensus": "MS2 consensus",
    "report": "summary report",
    "h5ad": "create h5ad",
    "normalization": "TIC normalization",
    "analysis": "analysis database",
}


class Config:
    """Full configuration for a processing run, grouped by pipeline stage.

    Wraps one settings object per stage (`io`, `ms1`, `centroid`, `peak`,
    `align`, `group_ms2`, `purity`, `annotate`, `consensus`, `report`,
    `h5ad`, `normalization`, `analysis`) and provides (de)serialization to
    and from YAML and TOML. Only `io` is required; the remaining groups fall
    back to their dataclass defaults.

    Attributes:
        version: Config schema version; checked on load.
        io: Input/output paths.
        ms1: Averaged MS1 spectrum parameters.
        centroid: Centroid detection parameters.
        peak: Peak filtering parameters.
        align: Cross-sample m/z alignment parameters.
        group_ms2: MS2-to-feature association parameters.
        purity: Precursor-ion-purity parameters.
        annotate: MS2 spectral-library annotation parameters.
        consensus: Per-feature MS2 consensus parameters.
        report: End-of-run summary report parameters.
        h5ad: Spatial `AnnData` assembly parameters.
        normalization: TIC-normalization parameters.
        analysis: Per-analysis database parameters.
    """

    version: int = 13

    def __init__(
        self,
        io: IOConfig,
        ms1: MS1Config | None = None,
        centroid: CentroidConfig | None = None,
        peak: PeakConfig | None = None,
        align: AlignMzSamples | None = None,
        group_ms2: GroupMs2Config | None = None,
        purity: PurityConfig | None = None,
        annotate: AnnotateConfig | None = None,
        consensus: ConsensusConfig | None = None,
        report: ReportConfig | None = None,
        h5ad: H5adConfig | None = None,
        normalization: NormalizationConfig | None = None,
        analysis: AnalysisConfig | None = None,
    ) -> None:
        self.io: IOConfig = io
        self.ms1: MS1Config = ms1 or MS1Config()
        self.centroid: CentroidConfig = centroid or CentroidConfig()
        self.peak: PeakConfig = peak or PeakConfig()
        self.align: AlignMzSamples = align or AlignMzSamples()
        self.group_ms2: GroupMs2Config = group_ms2 or GroupMs2Config()
        self.purity: PurityConfig = purity or PurityConfig()
        self.annotate: AnnotateConfig = annotate or AnnotateConfig()
        self.consensus: ConsensusConfig = consensus or ConsensusConfig()
        self.report: ReportConfig = report or ReportConfig()
        self.h5ad: H5adConfig = h5ad or H5adConfig()
        self.normalization: NormalizationConfig = (
            normalization or NormalizationConfig()
        )
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
    @log_call
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
    @log_call(source="path")
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

    @log_call(source="path")
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


@log_call(source="file")
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
