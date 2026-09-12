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
    """Which files this run reads, and where its results are written.

    The one required section — everything else in `Config` has usable
    defaults, but a run has to be told what data to process and where to
    put the output. All fields are coerced to `pathlib.Path` (or lists
    thereof) in `__post_init__`; a relative path (e.g. `"data/s1.mzML"`) is
    later anchored to `project_folder` by `resolve_paths`, so paths in a
    config file are usually written relative to the project root rather
    than as full absolute paths.

    Attributes:
        project_folder: The MSIAnalyzer project's root directory — the one
            containing the `.msianalyzer.yml` marker file created by
            `msianalyzer init`/`create_project`. Every relative path in
            this config (mzML/XML/DB paths, `out_dir`) is resolved against
            this.
        mzml_paths: The raw MS data files to process for this run, e.g.
            `["data/sample_1.mzML", "data/sample_2.mzML"]`. Every other
            per-sample list (`xml_paths`, `db_paths`) is paired with this
            one by position — index 0 of each list all describe the same
            sample.
        xml_paths: One raster/imaging-metadata XML file per entry in
            `mzml_paths`, giving each scan's pixel position and timing
            (needed to reconstruct the 2D image). Must be the same length
            and sample order as `mzml_paths`.
        db_paths: Where each sample's *parsed* raw database (built once
            from its mzML/XML pair, then reused by every analysis that
            touches that sample) should live. Leave this empty (the normal
            case) and it defaults to `<project_folder>/parsed/<mzml
            stem>.db` — recommended, since parsed databases are immutable
            and shared across every analysis in the project, not specific
            to this one run. Only set it to point somewhere else on
            purpose (e.g. re-using a database already parsed elsewhere).
        out_dir: Directory this analysis' own outputs (the analysis
            database, `.h5ad` files, the summary report, logs) are written
            to — usually something like `results/run_1`, distinct per run
            so that re-running with different settings doesn't overwrite a
            previous analysis of the same samples.
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
    """Build one averaged MS1 spectrum per sample from every pixel's scan.

    Every pixel in an imaging run has its own MS1 scan; before peaks can be
    detected, all of a sample's scans are binned onto a common m/z axis and
    averaged into a single representative spectrum. This stage controls
    that binning — how fine the m/z axis is, and which mass range it
    covers.

    Attributes:
        chunk_size: How many scans are read from the raw database and
            averaged into the running total at once. Purely a
            memory/speed knob — smaller keeps peak memory use down on very
            large runs at the cost of more, slightly slower database
            reads; it never changes the resulting averaged spectrum.
        bin_width: Width, in Da, of each bin on the shared m/z axis, e.g.
            `0.0001` (the default) means every m/z value is rounded to the
            nearest 0.0001 Da before averaging. Smaller preserves more mass
            resolution but produces a larger, slower-to-process spectrum
            (and can under-average true replicate peaks that jitter by more
            than the bin width); coarser is faster but can blur together
            two real, close-mass peaks.
        min_mz: Lower edge, in Da, of the m/z range kept in the averaged
            spectrum — anything below this (e.g. very-low-mass background
            ions) is discarded. Default `70.0`.
        max_mz: Upper edge, in Da, of the m/z range kept in the averaged
            spectrum. Default `900.0`. Narrowing `min_mz`/`max_mz` to the
            mass range you actually care about (e.g. `100`–`500` for small
            metabolites) reduces memory and downstream processing time with
            no loss of relevant peaks.
    """

    chunk_size: int = 2000
    bin_width: float = 0.0001
    min_mz: float = 70.0
    max_mz: float = 900.0


@dataclass
class CentroidConfig:
    """Find discrete peaks in each sample's averaged (profile) MS1 spectrum.

    The averaged spectrum from `ms1` is still a continuous curve, not a
    list of peaks. This stage estimates a noise "baseline" for that curve,
    then reports every local maximum that rises far enough above it as a
    detected peak — the input to `peak` filtering and later cross-sample
    alignment.

    Attributes:
        prominence_factor: How much a local maximum must stand out from its
            immediate surroundings (not just the baseline) to count as a
            peak, as a multiple of the baseline value — e.g. `0.1` (the
            default) requires the peak to rise at least 10% of the local
            baseline above its neighboring valleys. Raise it to ignore
            small shoulders/ripples on the side of a bigger peak; lower it
            to pick up smaller, real but subtle features.
        baseline_factor: A candidate peak must exceed `baseline +
            baseline_factor * baseline` to be kept at all, e.g. with the
            default `100`, a peak must reach 101× the estimated noise
            level. This is the main knob for "how far above the noise
            floor" — raise it on noisy data to suppress false peaks, lower
            it if genuine low-intensity peaks are being missed.
        baseline_method: `"local"` (default) estimates a separate baseline
            for each region of the spectrum via a rolling window
            (`local_window`/`smooth_sigma`) — better when noise level
            varies across the mass range. `"global"` uses one single
            baseline value for the whole spectrum — simpler and faster,
            reasonable when noise is roughly uniform across the mass range.
        baseline_percentile: Which percentile of non-zero intensities is
            treated as "baseline" — e.g. the default `10` uses the
            intensity below which the lowest 10% of non-zero points fall,
            a robust noise-floor estimate that isn't thrown off by the
            handful of very tall real peaks.
        local_window: Width, in bins (see `ms1.bin_width`), of the rolling
            window used to estimate a local baseline. Only used when
            `baseline_method` is `"local"`. Wider smooths out more
            local variation (more stable baseline, but less able to track
            genuine changes in background level across the spectrum);
            narrower tracks local changes more closely but is noisier.
        smooth_sigma: Gaussian smoothing width, in bins, applied to the
            local baseline curve after estimation — softens sharp jumps
            between neighboring windows. Only used when `baseline_method`
            is `"local"`.
        merge_ppm: If two detected peaks land within this many ppm of each
            other, they're merged into one — cleans up a single real peak
            that got split into two adjacent local maxima by noise. Not the
            same as `align.align_ppm`, which merges peaks *across*
            different samples rather than within one.
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
    """Drop low-intensity peaks left over after centroid detection.

    `centroid` can still report small, likely-noise peaks alongside real
    ones. This stage sets the final intensity cutoff — either an adaptive
    one that adjusts to each sample's own noise level (`filter_mad`,
    recommended, and the default), or one fixed number applied to every
    sample the same way.

    Attributes:
        filter_mad: When `True` (the default), the cutoff is computed
            per-sample from the median and median-absolute-deviation (MAD)
            of the peak intensities — adapts automatically to how noisy or
            intense a given sample happens to be. When `False`, every
            sample instead uses the single fixed `peak_height_threshold`
            value — simpler and fully predictable, but only appropriate
            when every sample in the run has comparable intensity scale
            (e.g. all acquired in the same batch with the same instrument
            settings).
        filter_mad_log: Compute the median/MAD in log10 intensity space
            rather than on raw intensities. Recommended (and the default,
            `True`) for MS data, whose intensities span several orders of
            magnitude — a log-space MAD isn't dominated by a handful of
            very tall peaks the way a linear-space one would be. Only used
            when `filter_mad` is `True`.
        filter_mad_nmads: How many MADs above the median sets the cutoff,
            e.g. the default `2.5` keeps peaks at or above `median + 2.5 ×
            MAD`. Raise it (e.g. to `3.5`–`4`) to keep only the most
            confident peaks on a noisy dataset; lower it (e.g. to `1.5`) to
            retain more borderline peaks when sensitivity matters more than
            precision. Only used when `filter_mad` is `True`.
        peak_height_threshold: Flat, absolute intensity cutoff — any peak
            below this value is dropped, e.g. `1000.0` (the default) drops
            every peak with intensity under 1000 counts. Only used when
            `filter_mad` is `False`; because it's an absolute number rather
            than adaptive, the right value here depends entirely on your
            instrument and acquisition settings, so check a sample's own
            intensity scale before relying on this.
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
    """Line up each sample's detected peaks into one shared feature list.

    Every sample was centroided independently, so the "same" molecule
    lands at a slightly different m/z in each one (instrument drift,
    calibration). This stage groups peaks across samples that are within
    `align_ppm` of each other into one "feature" with a single consensus
    m/z — everything downstream (MS2 association, annotation, the
    per-feature tables) is keyed on these features, not on any one
    sample's raw peak list.

    Attributes:
        align_ppm: How close two peaks from different samples must be (in
            parts per million of their m/z) to be treated as the same
            feature. Example: at 5.0 ppm, a peak at m/z 400 in sample A and
            one at m/z 400.002 in sample B (a difference of 5 ppm) are just
            barely grouped together; 400.003 is not. Too tight and the same
            real compound gets split into several near-duplicate features
            across samples; too loose and distinct, close-mass compounds
            get merged into one. Should usually be a bit looser than
            `centroid.merge_ppm` (peaks within one sample are already
            merged at that tolerance) since it also has to absorb
            run-to-run calibration drift.
        sample_names: Optional column/label name for each sample in the
            aligned feature table, in the same order as `io.mzml_paths`
            (e.g. `["control_1", "control_2", "treated_1"]` instead of the
            mzML file names). Leave unset (`None`) to use each mzML file's
            own name — the normal case. Not exposed in the GUI's New
            Analysis wizard (a GUI-started run always uses the mzML file
            names); settable only by hand-editing or scripting a config
            file.
        mz_decimals: Number of decimal places a feature's consensus m/z is
            rounded to before being stored and displayed (e.g. `4` shows
            `400.1234`). Mainly cosmetic — it does not change which peaks
            get grouped together (that's `align_ppm`'s job) — but too few
            decimals can make two genuinely different, close-mass features
            display as identical values in tables and plots.
    """

    align_ppm: float = 5.0
    sample_names: list[str] | None = field(
        default=None, metadata={"gui_hidden": True}
    )
    mz_decimals: int = 4


@dataclass
class GroupMs2Config:
    """Attach each MS2 (fragmentation) scan to the feature it belongs to.

    Stage A of annotation (`core.annotation.group_ms2`): an imaging run
    interleaves MS1 (whole-spectrum) and MS2 (fragmentation, one selected
    precursor m/z) scans. This stage snaps each MS2 scan's precursor m/z to
    the nearest feature from `align`, so later stages know "this
    fragmentation spectrum belongs to that feature." Nothing is discarded
    here — a scan that doesn't match anything, or looks unreliable, is
    flagged rather than dropped, so it's still visible for inspection.

    Attributes:
        assoc_ppm: How close (in ppm) an MS2 scan's precursor m/z must be
            to a feature's m/z to be associated with it. Should be a bit
            looser than `align.align_ppm` — e.g. if `align_ppm` is `5.0`,
            something like `10.0` (the default) — because it has to cover
            both the feature's own width across samples and the extra
            imprecision of a single survey-scan precursor reading. A
            warning is logged at run time if this ends up tighter than
            `align.align_ppm`.
        include_unmatched: When `True` (default), MS2 scans whose precursor
            matched no feature at all are still kept in the database (with
            a NULL feature id) instead of being silently dropped — useful
            for auditing why a scan didn't associate. Set `False` to
            discard them and keep the association table smaller.
        default_isolation_half_width: Half-width, in Da, of the isolation
            window assumed around a scan's precursor when the raw file
            itself doesn't record isolation-window offsets. Only matters
            for instruments/methods that omit this metadata; e.g. `0.5`
            (the default) assumes a ±0.5 Da window.
        precursor_only_tic_frac: Flags a scan as `precursor_only` — likely
            failed fragmentation, since almost all of the ion signal is
            still sitting on the precursor mass rather than spread across
            fragment peaks — when at least this fraction of the scan's
            total fragment-ion current lies within
            `precursor_only_mz_tol_da` of the precursor m/z. Example: the
            default `0.8` flags a scan where 80%+ of its fragment TIC is
            still "on the precursor." This is a QC flag, not a filter —
            flagged scans are kept and still scored downstream.
        precursor_only_mz_tol_da: Half-width, in Da, of the "counts as
            still on the precursor" band used by the `precursor_only_tic_frac`
            test above.
        flat_fragmentation_min_peaks: Minimum number of surviving fragment
            peaks (after the `flat_fragmentation_min_rel_intensity` cutoff)
            a scan needs before the `flat_fragmentation` QC check even
            runs. Below this, e.g. the default `3`, there simply aren't
            enough peaks to compute a trustworthy coefficient of variation,
            so the scan is left unflagged either way.
        flat_fragmentation_cv_threshold: A scan is flagged
            `flat_fragmentation` — likely noise or an isobaric co-isolation
            smear rather than a genuine fragmentation spectrum — when its
            surviving peaks' coefficient of variation (`std(intensity) /
            mean(intensity)`) is at or below this value, e.g. the default
            `0.2`. Real CID/HCD fragmentation decays (one or a few
            dominant fragments, several much smaller ones, so a *high*
            CV); many peaks all at roughly the same height (a *low* CV)
            looks more like chemical/electronic noise. Soft QC flag, not a
            filter — flagged scans are still scored and stored.
        flat_fragmentation_min_rel_intensity: Peaks below this fraction of
            a scan's own base peak are dropped before both the peak count
            and the coefficient of variation above are computed, e.g. the
            default `0.01` drops anything under 1% of the scan's tallest
            peak. Independent of `AnnotateConfig.noise_threshold` — this
            check runs before annotation even starts.
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
    """Measure how "clean" each MS2 scan's precursor selection was.

    Stage A′ of annotation (`core.annotation.precursor_purity`): a
    fragmentation scan's isolation window can accidentally capture more
    than one co-eluting compound at similar m/z — the resulting spectrum is
    a mix, which weakens or misleads library matching. For every MS2 scan,
    this stage looks at its *parent* MS1 scan and measures what fraction of
    the ion current in the isolation window actually belongs to the
    intended precursor versus other, unrelated ions —
    `precursor_frac`, from 0 (mostly other ions) to 1 (window is
    essentially pure precursor). This needs no peak detection (a direct
    profile-area integration around the precursor m/z), so it stays
    computable even in dense, matrix-heavy, low-mass windows where
    resolving a discrete peak often fails; and unlike the grouper's old
    feature-list-density flag, it needs no aligned feature list, so it's
    meaningful even before/without alignment (see ADR 0019).

    Attributes:
        enabled: Run this stage. Default `True`; set `False` to skip it
            entirely (e.g. to save time on a quick preliminary run) — every
            downstream `precursor_frac`/`precursor_confirmed` field then
            stays NULL and `annotate.min_precursor_frac`/
            `consensus.min_precursor_frac` become no-ops.
        default_half_window_da: Half-width, in Da, of the isolation window
            assumed when a scan's raw metadata doesn't record one. Default
            `0.5` (a ±0.5 Da window).
        precursor_confirm_ppm: Half-width, in ppm, of a band placed exactly
            on the recorded `precursor_mz`, used to compute `precursor_frac
            = I(that band) / I(whole isolation window)` — the fraction of
            the window's above-baseline ion current that sits on the
            precursor.
        precursor_confirm_min_frac: The `precursor_confirmed` flag is set
            when `precursor_frac` (see above) is at least this value —
            e.g. the default `0.01` only requires the precursor band to
            carry 1%+ of the window's ion current, a deliberately lenient
            bar (it's a sanity check that *some* signal is where it should
            be, not a purity threshold — use `annotate.min_precursor_frac`/
            `consensus.min_precursor_frac` elsewhere for that).
        precursor_snap_ppm: If the recorded precursor m/z isn't exactly on
            a real local intensity maximum in the parent MS1 (common with
            some instrument/converter combinations), snap it to the
            nearest one within this many ppm and store the result as
            `precursor_mz_snapped` — a no-op when the recorded value is
            already on a peak. Set to `0` to disable snapping entirely and
            always trust the recorded value as-is. Default `15.0`.
        n_workers: How many samples to score in parallel, one worker
            process per sample. `None` or `0` (the default is `None`) uses
            `os.cpu_count()`; `1` forces a fully serial run (useful for
            debugging). Automatically capped at the number of samples in
            the run. Each worker holds one whole sample's scan index in
            memory, so lower this if you're running low on RAM with many
            large samples.
    """

    enabled: bool = True
    default_half_window_da: float = 0.5
    precursor_confirm_ppm: float = 25.0
    precursor_confirm_min_frac: float = 0.01
    precursor_snap_ppm: float = 15.0
    n_workers: int | None = None


@dataclass
class AnnotateConfig:
    """Put a name to each MS2 spectrum by matching it against a library.

    Stage B of annotation (`core.annotation.annotate`): every MS2 scan the
    grouper attached to a feature is compared against reference spectra
    from one or more spectral libraries — first narrowed down to
    "candidates" whose precursor m/z is close enough to the feature's own
    m/z (`candidate_ppm`), then scored against each candidate with a
    coverage-aware reverse dot product (a standard spectral-similarity
    metric, similar to what tools like MS-DIAL use). Every candidate that
    shares at least `min_matched_peaks` fragments with the scan is kept and
    ranked; the best-ranked one (`rank_ms2 == 1`) is that scan's proposed
    identification.

    Leaving `library_path` empty (`None` or `[]`) disables this whole stage
    — every other setting in this section is then unused.

    Attributes:
        library_path: Path to one spectral-library database (built with
            `libviz`), or a list of several, e.g.
            `"libraries/positive_mode.db"` or
            `["lib_a.db", "lib_b.db"]`. Every library given is searched for
            candidates and results are pooled together before ranking, so a
            scan's top hit can come from any of them. Leaving this `None`
            (the default) or an empty list skips annotation entirely — no
            library, nothing to match against.
        noise_threshold: Before scoring, both the empirical scan and each
            library candidate are scaled so their tallest peak is `1.0`,
            then any peak below this fraction of that is dropped as noise.
            Example: the default `0.01` drops any peak under 1% of the
            spectrum's own base peak. Raise it (e.g. `0.05`) to score only
            on the strongest, most confident peaks; lower it (e.g.
            `0.001`) to keep more small peaks, at the risk of scoring on
            noise.
        candidate_ppm: How close a library spectrum's precursor m/z must be
            to a feature's own m/z (in ppm) to even be considered a
            candidate for that feature, before any spectral scoring
            happens. Wider (e.g. `20.0`) considers more candidates per
            feature (slower, but won't miss a real match with an
            unusually large calibration offset); narrower (e.g. `5.0`) is
            faster and stricter.
        fragment_ppm: How close two fragment peaks (one from the scan, one
            from the library candidate) must be, in ppm, to be counted as
            "the same peak" during scoring — the tolerance the reverse dot
            product itself uses when lining up the two spectra's peaks
            against each other.
        mz_power: Exponent applied to each peak's m/z when weighting it in
            the dot product (the MSDial-style weighting scheme). Together
            with `int_power`, controls whether high-mass or high-intensity
            peaks dominate the similarity score. Leave at the default
            (`2.0`) unless you're deliberately reproducing a specific
            published scoring convention that uses different exponents.
        int_power: Exponent applied to each peak's intensity when weighting
            it in the dot product — see `mz_power`. Default `0.5` (i.e.
            weighting by the square root of intensity, which softens the
            influence of one very tall peak dominating the whole score).
        score_weight_dot: Exponent applied to the raw spectral-similarity
            score (`dot_product_score`) when it's combined with
            `lib_coverage`/`emp_coverage` into the final stored `score`
            (see `spectral_match.reverse_dot_product`). Default `1.0`
            (full weight).
        score_weight_lib_coverage: Exponent applied to `lib_coverage` — the
            fraction of the *library* candidate's own peaks that were
            actually matched in the scan. Default `0.5`.
        score_weight_emp_coverage: Exponent applied to `emp_coverage` — the
            fraction of the *empirical scan's* own peaks that were matched
            in the library candidate. Default `0.5`. Together the three
            defaults reproduce the classic `dot_product_score *
            sqrt(lib_coverage * emp_coverage)` formula. Lower this
            (down to `0` to drop the term entirely) if real
            sample-background or matrix peaks that aren't in any library
            are dragging down otherwise-good matches (you'd see a high dot
            product and high library coverage, but low empirical
            coverage, on those hits). Note: `dot_product_score`,
            `lib_coverage`, `emp_coverage` and `coverage_score` are always
            stored unweighted regardless of these settings, so a config
            change here only shows up in `score` after a re-run.
        min_matched_peaks: The minimum number of shared fragment peaks a
            candidate must have with the scan to be stored at all, e.g. the
            default `1` keeps any candidate sharing even a single peak.
            Raise this (e.g. to `3`) to only keep candidates with
            reasonably substantial spectral overlap, cutting down on
            spurious single-peak "matches."
        min_precursor_frac: When set (e.g. `0.5`), scans whose precursor
            purity (`precursor_frac` from the `purity` stage) is known and
            below this value are skipped entirely — not scored, not
            stored. `None` (the default) annotates every scan regardless
            of precursor purity; every stored row still carries that
            scan's own `precursor_frac`/`precursor_confirmed` either way,
            so you can filter on it later without needing to have set this
            at run time. Every MS2 scan associated with a feature is
            scored by default — there is no longer a way to skip scoring
            based on how many *other* aligned features happen to share a
            scan's isolation window (see ADR 0019): that count says
            nothing about what actually co-fragmented into any one scan's
            own spectrum, and over-flagged badly once a feature list grew
            dense (a denser feature list, e.g. from a looser
            `peak.filter_mad_nmads`, used to silently collapse the number
            of scans ever scored). `precursor_frac` is the real, per-scan
            signal to filter on instead.
        store_raw_spectra: Persist the untouched (pre-noise-filtering) m/z
            + intensity arrays of both the empirical scan and its matched
            library candidate on every stored row, so a later mirror plot
            never needs to re-open the raw per-sample database or the
            library file itself (either of which can be on a slow or
            unreliable network mount). The noise-filtered, normalised view
            actually used for scoring is reconstructed on demand from these
            raw arrays plus this run's own `noise_threshold` rather than
            stored a second time (see ADR 0018) — it's fully derivable, so
            storing both would just waste space. Set `False` only if
            database size is a real concern and you don't need mirror
            plots for this run.
        batch_size: Number of features handed to each worker process at a
            time. A pure performance/memory-chunking knob — it doesn't
            change any result, only how work is split across processes.
        n_workers: Number of worker processes used for annotation.
            `None` (the default) uses `os.cpu_count()` — every available
            CPU core. Lower this on a shared machine, or if you're running
            low on memory (each worker holds its own copy of the relevant
            library data).
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
    min_precursor_frac: float | None = None
    store_raw_spectra: bool = True
    batch_size: int = 200
    n_workers: int | None = None


@dataclass
class ConsensusConfig:
    """Pick one "best" MS2 scan to represent each feature.

    Stage A″ of annotation (`core.annotation.consensus`): a feature usually
    has several MS2 scans behind it (one per sample/pixel where it was
    fragmented), of varying quality. This stage folds each scan's best
    library score (when `annotate` ran), its precursor purity
    (`precursor_frac`), and how many fragment peaks it has into one
    `consensus_score = best_score × precursor_frac_term × peak_term`, and
    picks the highest-scoring scan as *the* representative one for that
    feature. Runs independently of whether annotation or purity actually
    ran — a feature with neither still gets a consensus pick based on peak
    count alone (`best_score` and `precursor_frac_term` both default to
    neutral values in that case). Output:
    `feature_ms2_consensus`, one row per feature — a separate table this
    stage only *reads from* `ms2_annotations`/`precursor_purity`, never
    writes back to: `consensus_score` has no effect whatsoever on
    `annotate`'s own `score`, `rank_ms2` or `rank_feature`, which are
    already final by the time this stage runs. The two answer different
    questions — "how good is this (scan, candidate) match" (`annotate`) vs.
    "which of this feature's several scans is the best one to show" (this
    stage).

    Attributes:
        enabled: Run this stage. Default `True`; set `False` to skip it —
            `feature_ms2_consensus` is then left empty and any GUI/report
            view relying on "the best scan for this feature" has nothing
            to show.
        target_peaks: The peak-richness term is `peak_term = min(1,
            n_peaks / target_peaks)` — `target_peaks` is the fragment-peak
            count at which a scan gets *full credit* (`peak_term = 1.0`);
            more peaks than that don't earn any extra bonus (capped at
            1.0), and fewer scale down proportionally. Worked example at
            the default `target_peaks = 10`: a scan with 10+ peaks scores
            `peak_term = 1.0`, one with 5 peaks scores `0.5`, one with 2
            peaks scores `0.2`. This multiplies straight into
            `consensus_score`, so a sparse, few-peak scan is penalized in
            the pick even if its library score or purity looked good —
            raise `target_peaks` to weigh peak richness more heavily in
            the pick; lower it if your data is naturally low-peak-count and
            you don't want that to dominate over score/precursor purity.
        neutral_precursor_frac: Precursor-purity value substituted into the
            score for a scan the purity stage couldn't score (e.g.
            `purity.enabled = False`). Default `0.5` — a neutral middle
            value that neither rewards nor penalizes a scan just because
            purity data is missing for it.
        min_precursor_frac: When set (e.g. `0.5`), scans with a *known*
            `precursor_frac` below this value are excluded from the pick
            entirely — they still count toward that feature's `n_ms2`
            total, they just can't be chosen as the representative scan.
            `None` (the default) considers every scan regardless of
            precursor purity.
    """

    enabled: bool = True
    target_peaks: int = 10
    neutral_precursor_frac: float = 0.5
    min_precursor_frac: float | None = None


@dataclass
class ReportConfig:
    """Write a human-readable HTML summary of the whole run.

    `core.report.summary`: after every other stage finishes, writes
    `summary_report.html` (open it in any browser) + `summary.json` (the
    same numbers, machine-readable — also what the GUI's Analysis Summary
    section reads) to `io.out_dir`. Covers per-sample scan/peak counts, a
    feature-overlap "UpSet" plot (which combinations of samples share which
    features), and distributions of MS2 association / chimericity /
    purity across the run — a quick sanity check without having to query
    the analysis database by hand.

    Attributes:
        enabled: Build the report. Default `True`; set `False` to skip it
            on a quick/exploratory run where you don't need the HTML
            summary.
        overlap_top_n: Maximum number of sample-combination bars drawn in
            the feature-overlap UpSet plot, largest (most shared features)
            first — e.g. the default `30` shows only the 30 largest
            combinations, which keeps the plot readable when there are
            many samples (a run with `n` samples has up to `2^n - 1`
            possible combinations). Excess combinations are simply omitted
            from the plot, not merged into an "other" bucket.
        purity_cutoff: `precursor_frac` value (e.g. `0.8`, the default)
            below which a scan counts as "low purity" for the report's
            summary count, and where the reference line is drawn on the
            precursor-purity histogram — purely a reporting/visualization
            threshold, it does not affect `annotate.min_precursor_frac` or
            `consensus.min_precursor_frac`, which are set independently.
    """

    enabled: bool = True
    overlap_top_n: int = 30
    purity_cutoff: float = 0.8


@dataclass
class H5adConfig:
    """Build the per-sample spatial data object (`.h5ad`) used for imaging.

    For every aligned feature, quantifies its intensity at every pixel of
    every sample and assembles the result into an `AnnData` object (the
    standard format used by `scanpy`/`squidpy` and this project's own
    Visual Inspection view) — pixels × features, plus each pixel's spatial
    (x, y) coordinates. One `.h5ad` file is written per sample.

    Attributes:
        integration_ppm: How wide a window (in ppm around each feature's
            m/z) to sum intensity over when quantifying that feature at
            each pixel, e.g. the default `5.0` integrates ±5 ppm around the
            feature's m/z at every pixel. Widening it tolerates more
            per-pixel mass drift at the risk of picking up a neighboring
            peak's signal; narrowing it is more mass-specific but can miss
            signal on pixels with slightly larger drift.
        batch_size: Number of spectra processed per chunk while building
            the object — a memory/speed tuning knob only, doesn't affect
            the result.
        scan_handling: `"average"` (default) uses the pixel's averaged MS1
            spectrum (see `ms1`) when quantifying — smooths out per-scan
            noise, the more robust choice for most data. `"first"` instead
            uses only the first raw MS1 scan recorded at that pixel —
            faster and closer to a single instantaneous reading, but
            noisier; mainly useful when a pixel's averaged spectrum isn't
            representative for some reason (e.g. only one real scan was
            ever acquired there).
        n_workers: Number of parallel CPU worker processes used while
            assembling the object. `None` (the default) uses
            `os.cpu_count()` — every available core.
    """

    integration_ppm: float = 5.0
    batch_size: int = 1000
    scan_handling: str = "average"
    n_workers: int | None = None


@dataclass
class AnalysisConfig:
    """Name the per-run SQLite database that collects this analysis' results.

    Every run writes one analysis database (features, MS2 associations,
    purity, annotations, consensus picks — everything derived during this
    run, as opposed to the raw per-sample databases, which are shared and
    immutable). This section only controls what that file is called.

    Attributes:
        db_name: File name for the analysis database, written inside
            `io.out_dir`, e.g. `"my_analysis.db"`. When left `None` (the
            default), a name is derived automatically from the run id as
            `analysis_<run_id>.db` — usually fine to leave as-is unless you
            specifically want a predictable, human-chosen filename (e.g.
            for a script that always looks for the same path).
    """

    db_name: str | None = None


@dataclass
class NormalizationConfig:
    """Make feature intensities comparable across pixels and samples.

    `core.utils.tic_normalization`: runs after `h5ad` assembly, once every
    per-sample `.h5ad` file exists. Raw intensity at a pixel depends partly
    on how much total ion signal that pixel happened to produce (tissue
    thickness, ionization efficiency) rather than purely on how much of a
    given compound is there — this stage corrects for that. It compares
    each pixel's total ion current (`obs['tic']`, already recorded on every
    `.h5ad`) to the median TIC across every pixel of every sample in the
    analysis, scales each feature's raw intensity by that ratio, then
    applies a log1p compression so the result stays on a usable, roughly
    intensity-like scale rather than a raw ratio. Writes the result back as
    a new `layers['TIC']` on every per-sample `.h5ad` (`layers['raw']` keeps
    the untouched original for comparison), and also persists the full
    cross-sample concatenation as `merged.h5ad` in `io.out_dir` — computing
    the median already requires loading every sample at once, so saving
    that combined object is essentially free and is reused by later
    cross-sample analyses.

    Attributes:
        enabled: Run this stage. Default `True`; set `False` to skip it
            entirely — no `TIC` layer is added to the per-sample `.h5ad`
            files (only the original `raw` intensities remain) and no
            `merged.h5ad` is written. Turning this off is reasonable if TIC
            normalization doesn't make sense for your acquisition method,
            or simply to save the extra processing time/disk space when
            you only need raw intensities.
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

    version: int = 15

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
