from __future__ import annotations

import datetime
import logging
import os
import sqlite3
import uuid
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from enum import Enum
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import numpy as np
import pandas as pd

from msianalyzer.core import analysis_db
from msianalyzer.core.annotation.annotate import normalize_library_paths
from msianalyzer.core.annotation.reannotation import Ms2Settings, run_ms2_stages
from msianalyzer.core.annotation.target_list import (
    normalize_target_list_paths,
    run_target_list_matching,
)
from msianalyzer.core.report.summary import build_summary_report
from msianalyzer.core.config import Config
from msianalyzer.core.export import write_annotations_to_var
from msianalyzer.core.parser import (
    MzmlParser,
    is_profile_mzml,
    load_raw_metadata_value,
    log_command,
    parse_raster_xml,
    profile_mzml_message,
    write_raw_metadata,
)
from msianalyzer.core.plotting.plotter import Plotter
from msianalyzer.core.project import Project, project_file
from msianalyzer.core.spectra.average_spectra import (
    detect_ms1_centroids,
    filter_intensities_mad,
    get_average_ms1_spectra,
    load_aggregated_spectra,
    save_aggregated_spectra,
)
from msianalyzer.core.spatial.local_moran import compute_local_moran_layer
from msianalyzer.core.spatial.moran import compute_spatial_autocorr
from msianalyzer.core.spectra.isotopes import run_detect_isotopes
from msianalyzer.core.spectra.mz_tools import align_mz_across_samples
from msianalyzer.core.utils.create_adata import create_spatial_adata
from msianalyzer.core.utils.logging_utils import log_call, run_log, worker_logging
from msianalyzer.core.utils.spectra_pixels_association import map_pixels_to_db
from msianalyzer.core.utils.tic_normalization import run_normalization
from msianalyzer.core.utils.paths import relative_to_project

logger = logging.getLogger(__name__)


class RunStatus(Enum):
    """Lifecycle state of a `Run`."""

    RUNNING = 1
    COMPLETED = 0
    FAILED = 2


#: Canonical, ordered list of `run_core` stages. Consumers (e.g. the GUI) can
#: render the full checklist up front and fill statuses in as `on_step` fires.
#: See `Run.start`'s `on_step` parameter.
RUN_STEPS: tuple[str, ...] = (
    "process_samples",
    "align_mz",
    "match_target_list",
    "assemble_adata",
    "detect_isotopes",
    "group_ms2",
    "precursor_purity",
    "annotate_ms2",
    "annotate_precursor",
    "ms2_consensus",
    "normalize_tic",
    "spatial_correlation",
    "summary_report",
)


@dataclass
class SampleResult:
    """Per-sample output of `Run._process_one_sample`.

    Attributes:
        out_db_path: Path to the sample's raw SQLite database.
        peaks_mzs: Filtered MS1 peak m/z values for the sample. Empty for a
            `targeted` sample — targeted mode has no peak-discovery suffix
            to produce them (see `Run._process_one_sample`'s `targeted`
            flag).
    """

    out_db_path: Path
    peaks_mzs: np.ndarray


def _write_pixel_size_metadata(raw_db_path: Path, raster_metadata: dict) -> None:
    """Write `parse_raster_xml`'s pixel-size fields onto a raw database's
    `metadata` table, if it actually has one (`<rasterConfig>` present,
    recognized units) — a no-op otherwise.
    """
    if raster_metadata.get("pixel_size_x") is None:
        return
    write_raw_metadata(
        raw_db_path,
        {
            k: v
            for k, v in (
                ("pixel_size_x", raster_metadata["pixel_size_x"]),
                ("pixel_size_y", raster_metadata["pixel_size_y"]),
                ("pixel_size_unit", raster_metadata["pixel_size_unit"]),
            )
            if v is not None
        },
    )


def _backfill_pixel_size_metadata(raw_db_path: Path, xml_path: Path) -> None:
    """Re-derive and write pixel size for an already-mapped raw database
    that doesn't have it on record yet — the "already mapped, skip it"
    branch of `_process_one_sample`'s pixel-mapping step used to skip the
    one-time write along with everything else in that block, so re-running
    against a raw database parsed before this metadata existed (a normal,
    expected workflow here: raw databases are meant to be reused across
    analyses) silently never picked it up at all.

    Cheap to call unconditionally on every "already mapped" pass — this is
    a small XML re-read (`parse_raster_xml`), never the expensive
    per-pixel database write `map_pixels_to_db` itself does, and a no-op
    (via the `load_raw_metadata_value` check) once the value is already on
    record.
    """
    if load_raw_metadata_value(raw_db_path, "pixel_size_x") is not None:
        return
    _, raster_metadata = parse_raster_xml(xml_path)
    _write_pixel_size_metadata(raw_db_path, raster_metadata)


#: Canonical, ordered list of `_run_core_targeted`'s stages — a subset of
#: `RUN_STEPS`, since targeted mode skips feature discovery/alignment and
#: everything downstream of it. No `spatial_correlation` stage — pointless
#: for this workflow's ROI/calibration focus (per user feedback), unlike
#: the untargeted pipeline.
TARGETED_RUN_STEPS: tuple[str, ...] = (
    "process_samples",
    "assemble_adata",
    "normalize_tic",
)


@dataclass
class _SampleProcessingHead:
    """Shared setup + parallel sample processing, common to every analysis
    type — the output of `Run._run_pipeline_head`.

    Attributes:
        out_dir: Analysis output folder.
        run_id: Project-scoped id, used for raw-DB bookkeeping/caching.
        analysis_id: This run's id, used for analysis-DB identity.
        adb_path: Path to this run's analysis database.
        raw_db_paths: Each sample's raw database path, in submission order.
        sample_ids: Each sample's row id in the analysis `samples` table,
            same order as `raw_db_paths`.
        out_db_paths: Each `SampleResult.out_db_path`, same order.
        all_peaks_mzs: Each `SampleResult.peaks_mzs`, same order — empty
            arrays throughout for a `targeted` run.
    """

    out_dir: Path
    run_id: str
    analysis_id: str
    adb_path: Path
    raw_db_paths: list[Path]
    sample_ids: list[int]
    out_db_paths: list[Path]
    all_peaks_mzs: list[np.ndarray]


class Run:
    """A single end-to-end processing run (an *analysis*) over a set of samples.

    Loads a `Config`, records the run in the enclosing `Project`, and
    executes the parsing, pixel-mapping, averaging, centroiding,
    filtering, alignment and `AnnData` assembly steps.

    Raw per-sample databases hold only ground-truth scan data and pixel
    geometry. They live once per project (by default under
    `<project_folder>/parsed/`, see `IOConfig.raw_db_paths`) and are reused
    by every analysis. Every parameter-dependent artefact of this run is
    written to a dedicated analysis database, `analysis_<run.id>.db`, in the
    output folder.

    Attributes:
        id: Randomly generated run identifier; also the analysis identity.
        start_date: When `start` was called, or None before then.
        end_date: When the run finished, or None while running.
        status: Current `RunStatus`.
        config: The loaded `Config`, or None before `start`.
        config_path: Path the config was loaded from / exported to, for
            provenance (e.g. shown in the GUI project page). None before
            `start`.
        project: The owning `Project`, or None before `start`.
        on_step: Optional callback invoked as `on_step(step, status)` for
            each stage in `RUN_STEPS`, `status` one of `"started"`,
            `"completed"`, `"skipped"`, `"failed"`. Not persisted (excluded
            from `to_dict`) — purely a runtime hook, e.g. for the GUI.
        on_sample_progress: Optional callback invoked as
            `on_sample_progress(done, total)` as each sample finishes
            during the `"process_samples"` stage — the one stage `on_step`
            alone can't give any finer progress than "started"/"completed"
            for, since it's usually the longest-running one. Not persisted,
            same as `on_step`.
    """

    def __init__(self, config_file: str | Path | None = None):
        self.id: str = str(uuid.uuid4())
        self.start_date: datetime.datetime | None = None
        self.end_date: datetime.datetime | None = None
        self.status: RunStatus = RunStatus.RUNNING
        self.config: Config | None = None
        self.config_path: str | None = None
        self.project: Project | None = None
        self.on_step: Callable[[str, str], None] | None = None
        self.on_sample_progress: Callable[[int, int], None] | None = None
        # Step currently "started", so a failure can mark it "failed".
        self._current_step: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise the run to a plain, YAML-friendly dict.

        `datetime` values are stringified, enums become their name, and
        nested objects exposing `to_dict` (e.g. the `Config`) are expanded.
        The back-reference to the owning `project` is omitted — the run is
        stored *inside* the project file.

        Returns:
            A dict representation of the run.
        """
        d: dict[str, Any] = {}
        for key, value in vars(self).items():
            if key in ("project", "on_step", "on_sample_progress", "_current_step"):
                continue
            if isinstance(value, datetime.datetime):
                d[key] = str(value)
            elif isinstance(value, Enum):
                d[key] = value.name
            elif isinstance(value, list):
                d[key] = [
                    item.to_dict() if hasattr(item, "to_dict") else item
                    for item in value
                ]
            elif hasattr(value, "to_dict"):
                d[key] = value.to_dict()
            else:
                d[key] = value
        io = getattr(getattr(self, "config", None), "io", None)
        if io is not None:
            d["config_path"] = relative_to_project(d.get("config_path"), io.project_folder)
        return d

    @log_call
    def start(
        self,
        config_file: str | Path | None = None,
        config: Config | None = None,
        config_path: str | Path | None = None,
        on_step: Callable[[str, str], None] | None = None,
        on_sample_progress: Callable[[int, int], None] | None = None,
    ) -> None:
        """Load configuration, register the run, and execute the pipeline.

        Resolves the configuration, records the run in the project file,
        changes into the project folder, runs the core pipeline, then marks
        the run completed and re-exports the project file.

        Args:
            config_file: Path to a YAML config file. Used when `config` is
                not given.
            config: An already-built `Config`. Takes precedence over
                `config_file`.
            config_path: Path to record as this run's config provenance
                (`self.config_path`). Defaults to `config_file` when that is
                given and `config_path` is not; otherwise None.
            on_step: Optional per-stage progress callback, see `RUN_STEPS`
                and the `Run.on_step` attribute.
            on_sample_progress: Optional per-sample progress callback
                during `"process_samples"`, see the `Run.on_sample_progress`
                attribute.

        Raises:
            ValueError: If neither `config_file` nor `config` is provided.
        """
        self.on_step = on_step
        self.on_sample_progress = on_sample_progress

        if config is not None:
            self.config = config
            self.config_path = str(config_path) if config_path is not None else None
        elif config_file is not None:
            self.config = Config.from_yaml(config_file)
            self.config_path = (
                str(config_path) if config_path is not None else str(config_file)
            )
        else:
            raise ValueError("Neither config_file nor config was provided.")

        project_path = project_file(self.config.io.project_folder)
        # Explicit start_path, not cwd-derived — the calling process's cwd
        # (the GUI's launch directory, not necessarily inside any project)
        # has no bearing on which project this run belongs to; that's
        # already known from the config. Relying on the default cwd walk
        # here made a run fail outright whenever the GUI was launched from
        # outside the project folder tree, before ever reaching the
        # `os.chdir` below.
        self.project = Project.load(self.config.io.project_folder)
        self.start_date = datetime.datetime.now()
        self.project.runs[self.id] = self.to_dict()
        self.project.export(project_path)

        try:
            os.chdir(self.config.io.project_folder)
            # Relative paths (out_dir, inputs) resolve against the project
            # folder, so the log and the checks come only after the chdir.
            with run_log(
                self.config.io.out_dir, self.config.analysis.log_level, run_id=self.id
            ):
                Run._check_input_paths(self.config)

                logger.info("run %s: pipeline started", self.id)
                self.run_core()
                logger.info(
                    "run %s: pipeline complete in %s",
                    self.id,
                    datetime.datetime.now() - self.start_date,
                )
        except Exception:
            # Without this the GUI keeps the running step spinning and the
            # project records the run as RUNNING forever.
            if self._current_step is not None:
                self._emit_step(self._current_step, "failed")
            self.end_date = datetime.datetime.now()
            self.status = RunStatus.FAILED
            self.project.runs[self.id] = self.to_dict()
            self.project.export(project_path)
            raise

        self.end_date = datetime.datetime.now()
        self.status = RunStatus.COMPLETED
        self.project.runs[self.id] = self.to_dict()
        self.project.export(project_path)

    @staticmethod
    @log_call
    def _check_input_paths(config: Config) -> None:
        """Fail fast, before any work, if any configured input is missing or unusable.

        Checks the mzML/XML inputs, the raw databases of already-parsed
        (db-only) samples, the spectral libraries and the target lists, then
        that no mzML stores its MS1 spectra in profile mode.

        Raises:
            FileNotFoundError: Listing every missing path, not just the first.
            ValueError: Listing every profile-mode mzML.
        """
        io = config.io
        paths = [*io.mzml_paths, *io.xml_paths]
        paths += [
            db for m, db in zip(io.mzml_paths, io.raw_db_paths()) if m is None
        ]
        paths += normalize_library_paths(config.annotate.library_path)
        paths += normalize_target_list_paths(config.target_list.paths)
        missing = [str(p) for p in paths if p is not None and not Path(p).exists()]
        if missing:
            raise FileNotFoundError(
                "Input path(s) not found:\n" + "\n".join(missing)
            )
        # Checked on every mzML, not only those still to parse: a raw DB
        # reused from an earlier run is as unusable as a fresh parse.
        profile = [p for p in io.mzml_paths if p is not None and is_profile_mzml(p)]
        if profile:
            raise ValueError(profile_mzml_message(profile))

    @staticmethod
    def is_command_already_run(
        command_name: str, run_id: str, ms_db_path: str | Path
    ) -> bool:
        """Check whether a command has already run, against a raw database.

        Args:
            command_name: Name recorded in the database `commands` table.
            run_id: Identifier of the run to check.
            ms_db_path: Path to the SQLite database holding the `commands`
                table.

        Returns:
            True if a matching `commands` row exists, False otherwise.
        """
        with sqlite3.connect(Path(ms_db_path)) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id FROM commands WHERE command_name = ? AND run_id = ?",
                (command_name, run_id),
            )
            return cursor.fetchone() is not None

    @staticmethod
    def _raw_db_is_ready(db_path: str | Path) -> bool:
        """Whether a raw database already has scans and pixel mapping.

        Used for an "already-parsed, db-only" sample (`io.mzml_paths[i] is
        None`) — there's no mzML/XML to (re-)parse from, so this is the only
        check available that the given database is actually usable, rather
        than an empty or half-finished one. Independent of any `run_id` —
        unlike `is_command_already_run`, this reads the database's own
        content directly, since a user-supplied database may well have been
        parsed under a different project (a different `run_id`) entirely.

        Args:
            db_path: Path to the raw per-sample database to check.

        Returns:
            True if the database has at least one MS1 scan and at least one
            mapped pixel; False if either table is missing, empty, or the
            file doesn't exist at all.
        """
        db_path = Path(db_path)
        if not db_path.exists():
            return False
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as con:
            try:
                n_scans = con.execute("SELECT COUNT(*) FROM ms1_scans").fetchone()[0]
                n_pixels = con.execute(
                    "SELECT COUNT(*) FROM spatial_pixels"
                ).fetchone()[0]
            except sqlite3.OperationalError:
                return False
        return n_scans > 0 and n_pixels > 0

    @staticmethod
    @log_call(source="raw_db_path")
    def _process_one_sample(
        mzml_path: Path | None,
        xml_path: Path | None,
        sample_id: int,
        raw_db_path: Path,
        *,
        config: Config,
        run_id: str,
        analysis_id: str,
        analysis_db_path: Path,
        targeted: bool = False,
    ) -> SampleResult:
        """Process one sample: parse + map pixels (raw DB), then average /
        centroid / filter MS1 peaks (analysis DB) — the latter suffix
        skipped entirely for a `targeted` sample (see `targeted`).

        Args:
            mzml_path: Source mzML file, or `None` for an already-parsed,
                db-only sample (see `xml_path`).
            xml_path: Raster XML providing pixel timing, or `None` exactly
                when `mzml_path` is — an already-parsed sample's pixel
                mapping is already in `raw_db_path`, so there's nothing left
                to parse or map; both steps below are skipped for it, after
                confirming `raw_db_path` is actually ready
                (`Run._raw_db_is_ready`).
            sample_id: Row id of this sample in the analysis `samples` table.
            raw_db_path: This sample's raw database — parsed into fresh when
                `mzml_path` is given, read as-is (already parsed) otherwise.
                Project-scoped, shared across analyses.
            config: The run configuration.
            run_id: Project-scoped id used for raw-DB command bookkeeping.
            analysis_id: This run's id, used for analysis-DB bookkeeping.
            analysis_db_path: Path to the run's analysis database.
            targeted: When True, skip the average/centroid/filter suffix
                entirely — a targeted run has no unknown-peak discovery to
                do (the analyte/reference m/z are user-specified, not
                detected), so parse + pixel-map is all this sample needs
                before `assemble_adata`.

        Raises:
            FileNotFoundError: `mzml_path` is None (an already-parsed
                sample) but `raw_db_path` doesn't exist, or exists but isn't
                actually parsed and pixel-mapped yet.
        """
        out_dir = Path(config.io.out_dir)
        out_db_path = Path(raw_db_path)
        # The sample's own display name/log label, and the stem used for
        # this run's own output files below — the mzML's stem for a
        # normal sample, the raw database's own stem for an already-parsed
        # one (there's no mzML to name it after).
        sample_stem = Path(mzml_path).stem if mzml_path is not None else out_db_path.stem
        logger.debug("%s: Starting processing.", sample_stem)

        if mzml_path is None:
            # --- ALREADY PARSED (raw DB provided as-is) ---
            out_db_path.parent.mkdir(parents=True, exist_ok=True)
            if not Run._raw_db_is_ready(out_db_path):
                raise FileNotFoundError(
                    f"io: sample {sample_id} ({out_db_path}) has no mzml_path "
                    "to parse from, but its raw database is missing, or "
                    "isn't fully parsed and pixel-mapped yet — an "
                    "already-parsed sample must point db_paths at a "
                    "database that has already been through both steps."
                )
            logger.debug("%s: Already-parsed database provided as-is.", sample_stem)
        else:
            out_db_path.parent.mkdir(parents=True, exist_ok=True)

            # --- PARSE (raw DB) ---
            if not out_db_path.exists() or not Run.is_command_already_run(
                "parse_spectra", run_id, out_db_path
            ):
                MzmlParser().parse(mzml_path=mzml_path, ms1_db_path=out_db_path)
                log_command(
                    db_path=out_db_path,
                    run_id=run_id,
                    command_name="parse_spectra",
                    arguments={},
                )
            else:
                logger.debug("%s: Already parsed spectra.", sample_stem)

            # --- MAP PIXELS (raw DB) ---
            if not Run.is_command_already_run("map_pixels_to_db", run_id, out_db_path):
                df_pixels, raster_metadata = parse_raster_xml(xml_path)
                map_pixels_to_db(db_path=out_db_path, df_pixels=df_pixels)
                log_command(
                    db_path=out_db_path,
                    run_id=run_id,
                    command_name="map_pixels_to_db",
                    arguments={},
                )
                # The raster XML's own pixel size (<rasterConfig>), when it
                # has one — a default a targeted analysis' Calibration
                # workspace can offer for its pixel-size field, still
                # overridable by the user (and, for an XML without this
                # section, simply absent — the field stays exactly as
                # user-defined as it always was). Ground-truth acquisition
                # info belongs on the raw DB, not the analysis DB — see
                # write_raw_metadata's own docstring.
                _write_pixel_size_metadata(out_db_path, raster_metadata)
                logger.info(
                    "%s: mapped %d pixels", sample_stem, df_pixels.shape[0],
                    extra={"source_file": raw_db_path},
                )
            else:
                logger.debug("%s: Already run map_pixels_to_db", sample_stem)
                # Backfill pixel size for a raw db that was already mapped
                # before this metadata existed — a real gap, not
                # hypothetical: reusing already-parsed raw databases
                # across analyses is the normal workflow here, and the
                # "already mapped" branch above used to skip the one-time
                # write entirely along with everything else in that block.
                _backfill_pixel_size_metadata(out_db_path, xml_path)

        if targeted:
            logger.debug(
                "%s: targeted run — skipping average/centroid/filter suffix",
                sample_stem,
            )
            return SampleResult(out_db_path=out_db_path, peaks_mzs=np.array([]))

        # --- GET AVERAGE MS1 SPECTRA (analysis DB) ---
        if not analysis_db.is_command_already_run(
            "get_average_ms1_spectra", analysis_id, analysis_db_path, sample_id
        ):
            average_ms1_mzs, average_ms1_intensities = get_average_ms1_spectra(
                db_path=out_db_path, **vars(config.ms1)
            )
            command_id = analysis_db.log_command(
                analysis_db_path,
                command_name="get_average_ms1_spectra",
                arguments={**vars(config.ms1)},
                run_id=analysis_id,
                sample_id=sample_id,
            )
            save_aggregated_spectra(
                average_ms1_mzs,
                average_ms1_intensities,
                analysis_db_path=analysis_db_path,
                run_id=analysis_id,
                sample_id=sample_id,
                command_id=command_id,
            )
            logger.info(
                "%s: averaged MS1 -> %d bins", sample_stem, len(average_ms1_mzs),
                extra={"source_file": raw_db_path},
            )
        else:
            logger.debug("%s: Already run get_average_ms1_spectra", sample_stem)

        # --- DETECT MS1 CENTROIDS (analysis DB) ---
        if not analysis_db.is_command_already_run(
            "detect_ms1_centroids", analysis_id, analysis_db_path, sample_id
        ):
            if "average_ms1_mzs" not in locals():
                average_ms1_mzs, average_ms1_intensities = load_aggregated_spectra(
                    analysis_db_path,
                    run_id=analysis_id,
                    command_name="get_average_ms1_spectra",
                    sample_id=sample_id,
                )

            peaks_mzs, peaks_intensities = detect_ms1_centroids(
                bin_centers=average_ms1_mzs,
                mean_intensities=average_ms1_intensities,
                **vars(config.centroid),
            )

            command_id = analysis_db.log_command(
                analysis_db_path,
                command_name="detect_ms1_centroids",
                arguments={**vars(config.centroid)},
                run_id=analysis_id,
                sample_id=sample_id,
            )
            save_aggregated_spectra(
                peaks_mzs,
                peaks_intensities,
                analysis_db_path=analysis_db_path,
                run_id=analysis_id,
                sample_id=sample_id,
                command_id=command_id,
            )
            logger.info(
                "%s: detected %d centroids", sample_stem, len(peaks_mzs),
                extra={"source_file": raw_db_path},
            )
        else:
            logger.debug("%s: Already run detect_ms1_centroids", sample_stem)

        # --- PEAK FILTERING (analysis DB) ---
        if not analysis_db.is_command_already_run(
            "filter_spectra", analysis_id, analysis_db_path, sample_id
        ):
            if "peaks_mzs" not in locals():
                peaks_mzs, peaks_intensities = load_aggregated_spectra(
                    analysis_db_path,
                    run_id=analysis_id,
                    command_name="detect_ms1_centroids",
                    sample_id=sample_id,
                )

            if config.peak.filter_mad:
                logger.debug(
                    "%s: Filtering with mad, log %s, nmads %.2f",
                    sample_stem,
                    str(config.peak.filter_mad_log),
                    config.peak.filter_mad_nmads,
                )
                filtered_peaks_mzs, filtered_peaks_intensities = filter_intensities_mad(
                    peaks_mzs,
                    peaks_intensities,
                    log=config.peak.filter_mad_log,
                    n_mads=config.peak.filter_mad_nmads,
                )
            else:
                mask = peaks_intensities >= config.peak.peak_height_threshold
                filtered_peaks_mzs = peaks_mzs[mask]
                filtered_peaks_intensities = peaks_intensities[mask]

            command_id = analysis_db.log_command(
                analysis_db_path,
                command_name="filter_spectra",
                arguments={**vars(config.peak)},
                run_id=analysis_id,
                sample_id=sample_id,
            )
            save_aggregated_spectra(
                filtered_peaks_mzs,
                filtered_peaks_intensities,
                analysis_db_path=analysis_db_path,
                run_id=analysis_id,
                sample_id=sample_id,
                command_id=command_id,
            )
            logger.info(
                "%s: filtered -> %d peaks", sample_stem, len(filtered_peaks_mzs),
                extra={"source_file": raw_db_path},
            )
        else:
            logger.debug("%s: Already run filter_spectra", sample_stem)

        # --- FIGURE ---
        figure_path = out_dir / f"{sample_stem}_filtered_ms1.html"
        if not figure_path.exists():
            if "filtered_peaks_mzs" not in locals():
                filtered_peaks_mzs, filtered_peaks_intensities = load_aggregated_spectra(
                    analysis_db_path,
                    run_id=analysis_id,
                    command_name="filter_spectra",
                    sample_id=sample_id,
                )
            f = Plotter().plot_spectra(filtered_peaks_mzs, filtered_peaks_intensities)
            f.write_html(figure_path)
        else:
            logger.debug("%s: Already created figure", sample_stem)

        # --- PEAKS CSV ---
        peaks_df_path = out_dir / f"{sample_stem}_peaks_data.csv"
        if not peaks_df_path.exists():
            if "filtered_peaks_mzs" not in locals():
                filtered_peaks_mzs, filtered_peaks_intensities = load_aggregated_spectra(
                    analysis_db_path,
                    run_id=analysis_id,
                    command_name="filter_spectra",
                    sample_id=sample_id,
                )
            pd.DataFrame(
                {"mz": filtered_peaks_mzs, "intensity": filtered_peaks_intensities}
            ).to_csv(peaks_df_path)

        if "filtered_peaks_mzs" not in locals():
            filtered_peaks_mzs, filtered_peaks_intensities = load_aggregated_spectra(
                analysis_db_path,
                run_id=analysis_id,
                command_name="filter_spectra",
                sample_id=sample_id,
            )

        return SampleResult(out_db_path=out_db_path, peaks_mzs=filtered_peaks_mzs)

    def _emit_step(self, step: str, status: str) -> None:
        """Invoke `self.on_step(step, status)` when a callback is set.

        Args:
            step: One of `RUN_STEPS`.
            status: `"started"`, `"completed"`, `"skipped"` or `"failed"`.
        """
        self._current_step = step if status == "started" else None
        if self.on_step is not None:
            self.on_step(step, status)

    def _emit_sample_progress(self, done: int, total: int) -> None:
        """Invoke `self.on_sample_progress(done, total)` when a callback is set.

        Args:
            done: Number of samples finished so far.
            total: Total number of samples this run is processing.
        """
        if self.on_sample_progress is not None:
            self.on_sample_progress(done, total)

    def _run_pipeline_head(self, *, targeted: bool) -> _SampleProcessingHead:
        """Shared setup + parallel sample processing, common to every
        analysis type.

        Creates the analysis database, registers every sample, and
        processes each mzML/XML pair in parallel (parse + pixel-map, plus —
        for an `untargeted` run only — average/centroid/filter MS1 peaks;
        see `_process_one_sample`'s `targeted` flag). Steps whose outputs
        already exist are skipped.

        Args:
            targeted: Forwarded to `_process_one_sample` for every sample.

        Returns:
            A `_SampleProcessingHead` with everything the caller's
            analysis-type-specific stages need next.
        """
        config = self.config
        project = self.project

        out_dir = Path(config.io.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        run_id = project.uuid  # project-scoped: raw-DB bookkeeping / caching
        analysis_id = self.id  # this analysis: analysis-DB identity

        adb_path = analysis_db.analysis_db_path(
            out_dir, analysis_id, config.analysis.db_name
        )
        analysis_db.init_analysis_db(adb_path).close()
        analysis_db.write_metadata(
            adb_path,
            {
                "analysis_id": analysis_id,
                "project_id": run_id,
                "created": datetime.datetime.now().astimezone().isoformat(),
                "n_samples": len(config.io.mzml_paths),
            },
        )

        # Raw databases live once per project (shared by every analysis), not
        # inside this run's out_dir.
        raw_db_paths = config.io.raw_db_paths()
        for p in raw_db_paths:
            p.parent.mkdir(parents=True, exist_ok=True)
        logger.info(
            "run %s: raw databases -> %s",
            analysis_id,
            ", ".join(str(p) for p in raw_db_paths),
        )

        sample_ids = [
            analysis_db.register_sample(
                adb_path,
                # An already-parsed, db-only sample (m is None) has no mzML
                # to name itself after — use its raw database's own stem.
                name=Path(m).stem if m is not None else raw_db_paths[i].stem,
                raw_db_path=raw_db_paths[i],
            )
            for i, m in enumerate(config.io.mzml_paths)
        ]

        worker = partial(
            Run._process_one_sample,
            config=config,
            run_id=run_id,
            analysis_id=analysis_id,
            analysis_db_path=adb_path,
            targeted=targeted,
        )

        total_samples = len(config.io.mzml_paths)
        logger.info(
            "run %s: processing %d sample(s) with %s worker(s)",
            analysis_id,
            total_samples,
            config.h5ad.n_workers or "os.cpu_count()",
        )
        self._emit_step("process_samples", "started")
        self._emit_sample_progress(0, total_samples)
        try:
            with worker_logging() as (log_queue, initializer):
                with ProcessPoolExecutor(
                    max_workers=config.h5ad.n_workers,
                    initializer=initializer,
                    initargs=(log_queue,),
                ) as executor:
                    # submit()+as_completed(), not map() — map() yields
                    # results back in submission order regardless of which
                    # sample actually finishes first, so consuming it via
                    # list(...) blocks on sample 1 even if sample 3
                    # finished first, and gives no way to observe progress
                    # as samples complete. as_completed() yields each
                    # future the moment it's actually done; parallelism is
                    # identical either way (every sample is still submitted
                    # to the same pool up front) — only the order results
                    # are collected in changes. Original submission order
                    # still matters downstream (out_db_paths/all_peaks_mzs
                    # below are zipped positionally against sample_ids), so
                    # each result is written back to its own index rather
                    # than appended in completion order.
                    futures = {
                        executor.submit(worker, mzml, xml, sample_id, raw_db_path): i
                        for i, (mzml, xml, sample_id, raw_db_path) in enumerate(
                            zip(
                                config.io.mzml_paths,
                                config.io.xml_paths,
                                sample_ids,
                                raw_db_paths,
                            )
                        )
                    }
                    results: list[SampleResult | None] = [None] * total_samples
                    done = 0
                    for future in as_completed(futures):
                        results[futures[future]] = future.result()
                        done += 1
                        self._emit_sample_progress(done, total_samples)
        except Exception:
            logger.error(
                "run %s: a sample failed to process — aborting the run "
                "(later stages will not run)",
                analysis_id,
            )
            self._emit_step("process_samples", "failed")
            raise
        self._emit_step("process_samples", "completed")

        out_db_paths = [r.out_db_path for r in results]
        all_peaks_mzs = [r.peaks_mzs for r in results]

        return _SampleProcessingHead(
            out_dir=out_dir,
            run_id=run_id,
            analysis_id=analysis_id,
            adb_path=adb_path,
            raw_db_paths=raw_db_paths,
            sample_ids=sample_ids,
            out_db_paths=out_db_paths,
            all_peaks_mzs=all_peaks_mzs,
        )

    def _write_var_annotations(self, head: _SampleProcessingHead) -> None:
        """Copy the Annotation export into every `.h5ad`'s `.var`, once
        every annotation step is done. Before normalization, whose
        `merged.h5ad` concat then carries the columns along; `merged.h5ad`
        is listed too for a resumed run that already wrote it."""
        paths = [head.out_dir / f"{p.stem}.h5ad" for p in head.out_db_paths]
        write_annotations_to_var(head.adb_path, paths + [head.out_dir / "merged.h5ad"])
        logger.info("run %s: wrote annotations into .var", head.analysis_id)

    def _run_normalization_stage(self, head: _SampleProcessingHead) -> None:
        """Normalize every sample's `.h5ad`, if `config.normalization` is
        enabled — shared verbatim by both `untargeted` and `targeted` runs.
        """
        config = self.config
        norm_cfg = getattr(config, "normalization", None)
        if norm_cfg is not None and getattr(norm_cfg, "enabled", True):
            self._emit_step("normalize_tic", "started")
            if not analysis_db.is_command_already_run(
                "normalize_tic", head.analysis_id, head.adb_path
            ):
                analysis_db.log_command(
                    head.adb_path,
                    command_name="normalize_tic",
                    arguments={**vars(norm_cfg)},
                    run_id=head.analysis_id,
                )
                sample_h5ad_paths = {
                    db_path.stem: head.out_dir / f"{db_path.stem}.h5ad"
                    for db_path in head.out_db_paths
                }
                norm_result = run_normalization(
                    sample_h5ad_paths,
                    out_dir=head.out_dir,
                    mode=norm_cfg.mode,
                    reference_mz=norm_cfg.reference_mz,
                    reference_ppm=norm_cfg.reference_ppm,
                )
                logger.info(
                    "run %s: normalized (%s) %d sample(s), %d pixel(s), "
                    "reference median %.3g -> layer %s -> %s",
                    head.analysis_id,
                    norm_cfg.mode,
                    norm_result.n_samples,
                    norm_result.n_pixels,
                    norm_result.reference_median,
                    norm_result.layer_name,
                    norm_result.merged_path.name,
                )
            else:
                logger.debug("Already run normalize_tic")
            self._emit_step("normalize_tic", "completed")
        else:
            logger.info("Normalization disabled; skipping")
            self._emit_step("normalize_tic", "skipped")

    def _run_spatial_correlation_stage(self, head: _SampleProcessingHead) -> None:
        """Compute Moran's I (global and/or local) for every sample's
        `.h5ad`, if `config.spatial_correlation` enables either sub-pass —
        shared verbatim by both `untargeted` and `targeted` runs.

        Two independent sub-passes, `enabled` (global, per-sample score)
        and `local_enabled` (per-pixel summary) — either, both, or neither
        can be on; see `SpatialCorrelationConfig`'s own docstring for why
        they're separate. Sharing one "spatial_correlation" step/command
        name (rather than two) matches every other stage's granularity in
        this pipeline and is safe here too: `analysis_id` is a fresh UUID
        per `Run` instance (see `self.id`), so `is_command_already_run`
        only ever matters for resuming *this* run if it crashes mid-way —
        it's never a stale "already ran with different config" check
        across separate invocations.
        """
        config = self.config
        spatial_corr_cfg = getattr(config, "spatial_correlation", None)
        run_global_moran = spatial_corr_cfg is not None and getattr(
            spatial_corr_cfg, "enabled", False
        )
        run_local_moran = spatial_corr_cfg is not None and getattr(
            spatial_corr_cfg, "local_enabled", False
        )
        if run_global_moran or run_local_moran:
            self._emit_step("spatial_correlation", "started")
            if not analysis_db.is_command_already_run(
                "spatial_correlation", head.analysis_id, head.adb_path
            ):
                analysis_db.log_command(
                    head.adb_path,
                    command_name="spatial_correlation",
                    arguments={**vars(spatial_corr_cfg)},
                    run_id=head.analysis_id,
                )
                for db_path in head.out_db_paths:
                    out_adata_path = head.out_dir / f"{db_path.stem}.h5ad"
                    if run_global_moran:
                        compute_spatial_autocorr(
                            out_adata_path,
                            layer=spatial_corr_cfg.layer,
                            n_jobs=spatial_corr_cfg.n_jobs,
                        )
                        logger.info(
                            "%s: computed Moran's I -> %s",
                            db_path,
                            out_adata_path.name,
                            extra={"source_file": db_path},
                        )
                    if run_local_moran:
                        compute_local_moran_layer(
                            out_adata_path,
                            layer=spatial_corr_cfg.layer,
                            n_workers=spatial_corr_cfg.local_n_workers,
                        )
                        logger.info(
                            "%s: computed local Moran's I layer -> %s",
                            db_path,
                            out_adata_path.name,
                            extra={"source_file": db_path},
                        )
            else:
                logger.debug("Already run spatial_correlation")
            self._emit_step("spatial_correlation", "completed")
        else:
            logger.info("Spatial correlation disabled; skipping")
            self._emit_step("spatial_correlation", "skipped")

    @log_call
    def _run_core_targeted(self) -> None:
        """Run the MS1-only, calibration-curve-oriented pipeline.

        Shared sample processing (parse + pixel-map only, no peak
        discovery) -> assemble one `.h5ad` per sample around the reference
        m/z *and* every configured analyte -> normalize. No alignment,
        target-list matching, MS2 association/annotation, spatial
        correlation, or summary report — see `TARGETED_RUN_STEPS`. Every
        analyte is quantified here, at run time, in this one already-
        parallel `create_spatial_adata` pass — not extracted on demand
        later by the GUI (that used to make picking an analyte to visualize
        painfully slow, since it re-read the raw database from scratch on
        every "Load" click).
        """
        config = self.config
        head = self._run_pipeline_head(targeted=True)

        # --- SPATIAL AnnData PER SAMPLE (reference m/z + every analyte) ---
        self._emit_step("assemble_adata", "started")
        analyte_mzs = list(config.normalization.analyte_mzs)
        target_mz_set = {config.normalization.reference_mz, *analyte_mzs}
        for db_path in head.out_db_paths:
            out_adata_path = head.out_dir / f"{db_path.stem}.h5ad"
            if not out_adata_path.exists():
                tmp_adata = create_spatial_adata(
                    db_path=db_path,
                    target_mz_set=target_mz_set,
                    project_id=head.analysis_id,
                    **vars(config.h5ad),
                )
                tmp_adata.write_h5ad(out_adata_path)
                logger.info(
                    "%s: wrote %s (%d pixels x %d features)",
                    db_path,
                    out_adata_path.name,
                    tmp_adata.n_obs,
                    tmp_adata.n_vars,
                    extra={"source_file": db_path},
                )
            else:
                logger.debug("%s: Already created adata object", db_path)

        # Register the analytes (never the reference channel — that's a
        # normalization detail, not a compound of interest) as browsable
        # "injected" features, exactly like an untargeted run's target-list
        # matches — see CalibrationSection.qml's analyte picker, which is
        # otherwise just Visual Inspection's ordinary feature dropdown.
        if analyte_mzs and not analysis_db.is_command_already_run(
            "register_calibration_analytes", head.analysis_id, head.adb_path
        ):
            command_id = analysis_db.log_command(
                head.adb_path,
                command_name="register_calibration_analytes",
                arguments={"analyte_mzs": analyte_mzs},
                run_id=head.analysis_id,
            )
            analysis_db.append_injected_features(
                head.adb_path,
                [SimpleNamespace(mz=mz, members_json="{}") for mz in analyte_mzs],
                command_id=command_id,
            )
        self._emit_step("assemble_adata", "completed")

        self._write_var_annotations(head)
        self._run_normalization_stage(head)

    @log_call
    def run_core(self) -> None:
        """Run the pipeline across all samples and assemble outputs.

        Dispatches to `_run_core_targeted` when
        `config.analysis_type == "targeted"`. Otherwise: creates the
        analysis database, registers every sample, processes each mzML/XML
        pair in parallel, aligns peak m/z values across samples
        (persisting them to `features` and `aligned_mzs.csv`), and writes
        one spatial `AnnData` (.h5ad) file per sample. Steps whose outputs
        already exist are skipped.
        """
        if self.config.analysis_type == "targeted":
            self._run_core_targeted()
            return

        config = self.config
        head = self._run_pipeline_head(targeted=False)
        out_dir = head.out_dir
        analysis_id = head.analysis_id
        adb_path = head.adb_path
        raw_db_paths = head.raw_db_paths
        sample_ids = head.sample_ids
        out_db_paths = head.out_db_paths
        all_peaks_mzs = head.all_peaks_mzs

        # --- ALIGN m/z ACROSS SAMPLES ---
        self._emit_step("align_mz", "started")
        aligned_df_path: Path = out_dir / "aligned_mzs.csv"
        if not aligned_df_path.exists():
            sample_names = (
                config.align.sample_names
                if config.align.sample_names is not None
                else [
                    Path(m).stem if m is not None else db.stem
                    for m, db in zip(config.io.mzml_paths, out_db_paths)
                ]
            )
            mzs_df = align_mz_across_samples(
                mz_arrays=all_peaks_mzs,
                sample_names=sample_names,
                align_ppm=config.align.align_ppm,
                mz_decimals=config.align.mz_decimals,
            )
            mzs_df.to_csv(aligned_df_path)

            command_id = analysis_db.log_command(
                adb_path,
                command_name="align_mz_across_samples",
                arguments={**vars(config.align)},
                run_id=analysis_id,
            )
            analysis_db.save_features(adb_path, mzs_df, command_id=command_id)
            logger.info(
                "run %s: aligned %d features across %d samples",
                analysis_id,
                len(mzs_df.index),
                len(mzs_df.columns),
            )
        else:
            logger.debug("Already run align mz across samples")
            mzs_df = pd.read_csv(aligned_df_path, index_col=0)
        self._emit_step("align_mz", "completed")

        # --- TARGET-LIST COMPOUND MATCHING (analysis DB) ---
        # MS1-only (needs only `features`, from align_mz above) — independent
        # of group_ms2/purity/annotate/consensus below, but must complete
        # before assemble_adata so an injected feature's exact m/z (see
        # target_mz_set below) reaches every sample's .h5ad. See ADR 0026.
        self._emit_step("match_target_list", "started")
        if config.target_list.paths:
            if not analysis_db.is_command_already_run(
                "match_target_list", analysis_id, adb_path
            ):
                command_id = analysis_db.log_command(
                    adb_path,
                    command_name="match_target_list",
                    arguments={**vars(config.target_list)},
                    run_id=analysis_id,
                )
                tl_summary = run_target_list_matching(
                    adb_path,
                    config.target_list,
                    command_id=command_id,
                    sample_names=list(mzs_df.columns),
                )
                logger.info(
                    "run %s: target list — %d compound(s), %d matched to "
                    "existing features, %d injected",
                    analysis_id, tl_summary.n_compounds,
                    tl_summary.n_matched_existing, tl_summary.n_injected_features,
                )
            else:
                logger.debug("Already run match_target_list")
            self._emit_step("match_target_list", "completed")
            # Every sample's .h5ad must be able to show an injected
            # feature's heatmap too — union its m/z in here (read fresh
            # from the DB, so this is correct whether match_target_list
            # just ran above or was already-done and skipped).
            target_mz_set = set(mzs_df.index) | set(
                analysis_db.load_injected_feature_mzs(adb_path)
            )
        else:
            logger.info("No target list configured; skipping")
            self._emit_step("match_target_list", "skipped")
            target_mz_set = set(mzs_df.index)

        # A reference-m/z normalization needs that m/z present in every
        # sample's .h5ad too, exactly like an injected target-list compound
        # above — union it in here, before assemble_adata — and isotope
        # detection must never drop its column.
        keep_mzs = (
            {config.normalization.reference_mz}
            if getattr(config, "normalization", None) is not None
            and config.normalization.mode == "reference_mz"
            else set()
        )
        target_mz_set = target_mz_set | keep_mzs

        # --- SPATIAL AnnData PER SAMPLE ---
        self._emit_step("assemble_adata", "started")
        for db_path in out_db_paths:
            out_adata_path = out_dir / f"{db_path.stem}.h5ad"
            if not out_adata_path.exists():
                tmp_adata = create_spatial_adata(
                    db_path=db_path,
                    target_mz_set=target_mz_set,
                    project_id=analysis_id,
                    **vars(config.h5ad),
                )
                tmp_adata.write_h5ad(out_adata_path)
                logger.info(
                    "%s: wrote %s (%d pixels x %d features)",
                    db_path,
                    out_adata_path.name,
                    tmp_adata.n_obs,
                    tmp_adata.n_vars,
                    extra={"source_file": db_path},
                )
            else:
                logger.debug("%s: Already created adata object", db_path)
        self._emit_step("assemble_adata", "completed")

        # --- ISOTOPES (analysis DB + .h5ad) ---
        # Right after the .h5ad files, whose matrices are the ion images,
        # and before group_ms2, so isotopes are never associated or
        # annotated: they move from `features` to `feature_isotopes` and
        # their columns leave every .h5ad (ADR 54, 55). Target-list features
        # and the normalization reference m/z are never dropped.
        if config.isotopes.enabled:
            self._emit_step("detect_isotopes", "started")
            if not analysis_db.is_command_already_run(
                "detect_isotopes", analysis_id, adb_path
            ):
                command_id = analysis_db.log_command(
                    adb_path,
                    command_name="detect_isotopes",
                    arguments={**vars(config.isotopes)},
                    run_id=analysis_id,
                )
                iso = run_detect_isotopes(
                    adb_path,
                    {
                        name: out_dir / f"{db_path.stem}.h5ad"
                        for name, db_path in zip(mzs_df.columns, out_db_paths)
                    },
                    config.isotopes,
                    command_id=command_id,
                    keep_mzs=keep_mzs,
                )
                logger.info(
                    "run %s: %d of %d features are isotopes (%d candidates), "
                    "moved to feature_isotopes",
                    analysis_id, iso.n_isotopes, iso.n_features_before,
                    iso.n_candidates,
                )
            else:
                logger.debug("Already run detect_isotopes")
            self._emit_step("detect_isotopes", "completed")
        else:
            logger.info("Isotope detection disabled; skipping")
            self._emit_step("detect_isotopes", "skipped")

        # --- MS2: group -> purity -> annotate -> consensus (analysis DB) ---
        # Shared with re-annotating a finished analysis (ADR 63).
        run_ms2_stages(
            adb_path,
            Ms2Settings(
                config.ms2_cleaning, config.group_ms2, config.purity, config.annotate,
                config.consensus, align_ppm=config.align.align_ppm,
            ),
            analysis_id,
            skip_done=True,
            on_step=self._emit_step,
        )

        self._write_var_annotations(head)
        self._run_normalization_stage(head)
        self._run_spatial_correlation_stage(head)

        # --- SUMMARY REPORT ---
        report_cfg = getattr(config, "report", None)
        if report_cfg is not None and getattr(report_cfg, "enabled", True):
            self._emit_step("summary_report", "started")
            report_path = out_dir / "summary_report.html"
            if not report_path.exists():
                try:
                    build_summary_report(
                        adb_path,
                        raw_db_paths={
                            sid: p for sid, p in zip(sample_ids, raw_db_paths)
                        },
                        out_dir=out_dir,
                        config=report_cfg,
                    )
                except Exception:
                    logger.exception(
                        "run %s: summary report failed (pipeline outputs are "
                        "unaffected)",
                        analysis_id,
                    )
            else:
                logger.debug("Already built summary report")
            self._emit_step("summary_report", "completed")
        else:
            logger.info("Summary report disabled; skipping")
            self._emit_step("summary_report", "skipped")
