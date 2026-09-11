from __future__ import annotations

import datetime
import logging
import os
import sqlite3
import uuid
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from enum import Enum
from functools import partial
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from msianalyzer.core import analysis_db
from msianalyzer.core.annotation.annotate import run_annotation
from msianalyzer.core.annotation.consensus import run_consensus
from msianalyzer.core.annotation.group_ms2 import run_grouper
from msianalyzer.core.annotation.precursor_purity import run_precursor_purity
from msianalyzer.core.report.summary import build_summary_report
from msianalyzer.core.config import Config
from msianalyzer.core.parser import MzmlParser, log_command, parse_raster_xml
from msianalyzer.core.plotting.plotter import Plotter
from msianalyzer.core.project import Project
from msianalyzer.core.spectra.average_spectra import (
    detect_ms1_centroids,
    filter_intensities_mad,
    get_average_ms1_spectra,
    load_aggregated_spectra,
    save_aggregated_spectra,
)
from msianalyzer.core.spectra.mz_tools import align_mz_across_samples
from msianalyzer.core.utils.create_adata import create_spatial_adata
from msianalyzer.core.utils.logging_utils import log_call, worker_logging
from msianalyzer.core.utils.spectra_pixels_association import map_pixels_to_db
from msianalyzer.core.utils.tic_normalization import run_tic_normalization

logger = logging.getLogger(__name__)


class RunStatus(Enum):
    """Lifecycle state of a `Run`."""

    RUNNING = 1
    COMPLETED = 0


#: Canonical, ordered list of `run_core` stages. Consumers (e.g. the GUI) can
#: render the full checklist up front and fill statuses in as `on_step` fires.
#: See `Run.start`'s `on_step` parameter.
RUN_STEPS: tuple[str, ...] = (
    "process_samples",
    "align_mz",
    "group_ms2",
    "precursor_purity",
    "annotate_ms2",
    "ms2_consensus",
    "assemble_adata",
    "normalize_tic",
    "summary_report",
)


@dataclass
class SampleResult:
    """Per-sample output of `Run._process_one_sample`.

    Attributes:
        out_db_path: Path to the sample's raw SQLite database.
        peaks_mzs: Filtered MS1 peak m/z values for the sample.
    """

    out_db_path: Path
    peaks_mzs: np.ndarray


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
            if key in ("project", "on_step"):
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
        return d

    @log_call
    def start(
        self,
        config_file: str | Path | None = None,
        config: Config | None = None,
        config_path: str | Path | None = None,
        on_step: Callable[[str, str], None] | None = None,
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

        Raises:
            ValueError: If neither `config_file` nor `config` is provided.
        """
        self.on_step = on_step

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

        project_path = Path(self.config.io.project_folder) / ".msianalyzer.yml"
        self.project = Project.load()
        self.start_date = datetime.datetime.now()
        self.project.runs[self.id] = self.to_dict()
        self.project.export(project_path)

        os.chdir(self.config.io.project_folder)

        logger.info("run %s: pipeline started", self.id)
        self.run_core()

        self.end_date = datetime.datetime.now()
        self.status = RunStatus.COMPLETED
        self.project.runs[self.id] = self.to_dict()
        self.project.export(project_path)
        logger.info(
            "run %s: pipeline complete in %s",
            self.id,
            self.end_date - self.start_date,
        )

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
    @log_call(source="mzml_path")
    def _process_one_sample(
        mzml_path: Path,
        xml_path: Path,
        sample_id: int,
        raw_db_path: Path,
        *,
        config: Config,
        run_id: str,
        analysis_id: str,
        analysis_db_path: Path,
    ) -> SampleResult:
        """Process one sample: parse + map pixels (raw DB), then average /
        centroid / filter MS1 peaks (analysis DB).

        Args:
            mzml_path: Source mzML file.
            xml_path: Raster XML providing pixel timing.
            sample_id: Row id of this sample in the analysis `samples` table.
            raw_db_path: Destination of this sample's parsed raw database
                (project-scoped, shared across analyses).
            config: The run configuration.
            run_id: Project-scoped id used for raw-DB command bookkeeping.
            analysis_id: This run's id, used for analysis-DB bookkeeping.
            analysis_db_path: Path to the run's analysis database.
        """
        logger.debug("%s: Starting processing.", mzml_path)
        out_dir = Path(config.io.out_dir)
        out_db_path = Path(raw_db_path)
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
            logger.debug("%s: Already parsed spectra.", mzml_path)

        # --- MAP PIXELS (raw DB) ---
        if not Run.is_command_already_run("map_pixels_to_db", run_id, out_db_path):
            df_pixels, _ = parse_raster_xml(xml_path)
            map_pixels_to_db(db_path=out_db_path, df_pixels=df_pixels)
            log_command(
                db_path=out_db_path,
                run_id=run_id,
                command_name="map_pixels_to_db",
                arguments={},
            )
            logger.info(
                "%s: mapped %d pixels", mzml_path, df_pixels.shape[0],
                extra={"source_file": mzml_path},
            )
        else:
            logger.debug("%s: Already run map_pixels_to_db", mzml_path)

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
                "%s: averaged MS1 -> %d bins", mzml_path, len(average_ms1_mzs),
                extra={"source_file": mzml_path},
            )
        else:
            logger.debug("%s: Already run get_average_ms1_spectra", mzml_path)

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
                "%s: detected %d centroids", mzml_path, len(peaks_mzs),
                extra={"source_file": mzml_path},
            )
        else:
            logger.debug("%s: Already run detect_ms1_centroids", mzml_path)

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
                    mzml_path,
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
                "%s: filtered -> %d peaks", mzml_path, len(filtered_peaks_mzs),
                extra={"source_file": mzml_path},
            )
        else:
            logger.debug("%s: Already run filter_spectra", mzml_path)

        # --- FIGURE ---
        figure_path = out_dir / f"{mzml_path.stem}_filtered_ms1.html"
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
            logger.debug("%s: Already created figure", mzml_path)

        # --- PEAKS CSV ---
        peaks_df_path = out_dir / f"{mzml_path.stem}_peaks_data.csv"
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
        if self.on_step is not None:
            self.on_step(step, status)

    @log_call
    def run_core(self) -> None:
        """Run the pipeline across all samples and assemble outputs.

        Creates the analysis database, registers every sample, processes
        each mzML/XML pair in parallel, aligns peak m/z values across
        samples (persisting them to `features` and `aligned_mzs.csv`), and
        writes one spatial `AnnData` (.h5ad) file per sample. Steps whose
        outputs already exist are skipped.
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
                name=Path(m).stem,
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
        )

        logger.info(
            "run %s: processing %d sample(s) with %s worker(s)",
            analysis_id,
            len(config.io.mzml_paths),
            config.h5ad.n_workers or "os.cpu_count()",
        )
        self._emit_step("process_samples", "started")
        try:
            with worker_logging() as (log_queue, initializer):
                with ProcessPoolExecutor(
                    max_workers=config.h5ad.n_workers,
                    initializer=initializer,
                    initargs=(log_queue,),
                ) as executor:
                    results = list(
                        executor.map(
                            worker,
                            config.io.mzml_paths,
                            config.io.xml_paths,
                            sample_ids,
                            raw_db_paths,
                        )
                    )
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

        # --- ALIGN m/z ACROSS SAMPLES ---
        self._emit_step("align_mz", "started")
        aligned_df_path: Path = out_dir / "aligned_mzs.csv"
        if not aligned_df_path.exists():
            sample_names = (
                config.align.sample_names
                if config.align.sample_names is not None
                else [Path(m).stem for m in config.io.mzml_paths]
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

        # --- ASSOCIATE MS2 SCANS WITH FEATURES (analysis DB) ---
        self._emit_step("group_ms2", "started")
        if not analysis_db.is_command_already_run(
            "group_ms2", analysis_id, adb_path
        ):
            command_id = analysis_db.log_command(
                adb_path,
                command_name="group_ms2",
                arguments={
                    **vars(config.group_ms2),
                    "align_ppm": config.align.align_ppm,
                },
                run_id=analysis_id,
            )
            grouping = run_grouper(
                adb_path,
                assoc_ppm=config.group_ms2.assoc_ppm,
                align_ppm=config.align.align_ppm,
                include_unmatched=config.group_ms2.include_unmatched,
                command_id=command_id,
                default_isolation_half_width=(
                    config.group_ms2.default_isolation_half_width
                ),
                precursor_only_tic_frac=config.group_ms2.precursor_only_tic_frac,
                precursor_only_mz_tol_da=config.group_ms2.precursor_only_mz_tol_da,
                flat_fragmentation_min_peaks=(
                    config.group_ms2.flat_fragmentation_min_peaks
                ),
                flat_fragmentation_cv_threshold=(
                    config.group_ms2.flat_fragmentation_cv_threshold
                ),
                flat_fragmentation_min_rel_intensity=(
                    config.group_ms2.flat_fragmentation_min_rel_intensity
                ),
            )
            logger.info(
                "Associated %d MS2 scans across %d features with MS2 coverage",
                len(grouping.associations),
                len(grouping.feature_summary),
            )
        else:
            logger.debug("Already run group_ms2")
        self._emit_step("group_ms2", "completed")

        # --- PRECURSOR ION PURITY (analysis DB) ---
        purity_cfg = getattr(config, "purity", None)
        if purity_cfg is not None and getattr(purity_cfg, "enabled", True):
            self._emit_step("precursor_purity", "started")
            if not analysis_db.is_command_already_run(
                "precursor_purity", analysis_id, adb_path
            ):
                command_id = analysis_db.log_command(
                    adb_path,
                    command_name="precursor_purity",
                    arguments={**vars(purity_cfg)},
                    run_id=analysis_id,
                )
                purity_result = run_precursor_purity(
                    adb_path, purity_cfg, command_id=command_id
                )
                logger.info(
                    "Precursor purity: scored %d MS2 scans (%d with >1 in-window "
                    "peak, %d with no precursor peak, %d interpolated)",
                    purity_result.n_scans,
                    purity_result.n_multi_peak,
                    purity_result.n_precursor_missing,
                    purity_result.n_interpolated,
                )
            else:
                logger.debug("Already run precursor_purity")
            self._emit_step("precursor_purity", "completed")
        else:
            logger.info("Precursor purity disabled; skipping")
            self._emit_step("precursor_purity", "skipped")

        # --- ANNOTATE MS2 AGAINST SPECTRAL LIBRARY (analysis DB) ---
        if config.annotate.library_path:
            self._emit_step("annotate_ms2", "started")
            if not analysis_db.is_command_already_run(
                "annotate_ms2", analysis_id, adb_path
            ):
                command_id = analysis_db.log_command(
                    adb_path,
                    command_name="annotate_ms2",
                    arguments={**vars(config.annotate)},
                    run_id=analysis_id,
                )
                annotation = run_annotation(
                    adb_path, config.annotate, command_id=command_id
                )
                logger.info(
                    "Annotated %d MS2 scans over %d features (%d candidate rows)",
                    annotation.n_scans_annotated,
                    annotation.n_features_annotated,
                    len(annotation.rows),
                )
            else:
                logger.debug("Already run annotate_ms2")
            self._emit_step("annotate_ms2", "completed")
        else:
            logger.info(
                "No annotation library configured; skipping MS2 annotation"
            )
            self._emit_step("annotate_ms2", "skipped")

        # --- PER-FEATURE MS2 CONSENSUS (analysis DB) ---
        consensus_cfg = getattr(config, "consensus", None)
        if consensus_cfg is not None and getattr(consensus_cfg, "enabled", True):
            self._emit_step("ms2_consensus", "started")
            if not analysis_db.is_command_already_run(
                "ms2_consensus", analysis_id, adb_path
            ):
                command_id = analysis_db.log_command(
                    adb_path,
                    command_name="ms2_consensus",
                    arguments={**vars(consensus_cfg)},
                    run_id=analysis_id,
                )
                consensus = run_consensus(
                    adb_path, consensus_cfg, command_id=command_id
                )
                logger.info(
                    "MS2 consensus: picked a scan for %d feature(s) "
                    "(%d backed by a library hit)",
                    consensus.n_features,
                    consensus.n_features_scored,
                )
            else:
                logger.debug("Already run ms2_consensus")
            self._emit_step("ms2_consensus", "completed")
        else:
            logger.info("MS2 consensus disabled; skipping")
            self._emit_step("ms2_consensus", "skipped")

        # --- SPATIAL AnnData PER SAMPLE ---
        self._emit_step("assemble_adata", "started")
        for db_path in out_db_paths:
            out_adata_path = out_dir / f"{db_path.stem}.h5ad"
            if not out_adata_path.exists():
                tmp_adata = create_spatial_adata(
                    db_path=db_path,
                    target_mz_set=mzs_df.index,
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

        # --- TIC NORMALIZATION (writes layers into each .h5ad + merged.h5ad) ---
        norm_cfg = getattr(config, "normalization", None)
        if norm_cfg is not None and getattr(norm_cfg, "enabled", True):
            self._emit_step("normalize_tic", "started")
            if not analysis_db.is_command_already_run(
                "normalize_tic", analysis_id, adb_path
            ):
                analysis_db.log_command(
                    adb_path,
                    command_name="normalize_tic",
                    arguments={**vars(norm_cfg)},
                    run_id=analysis_id,
                )
                sample_h5ad_paths = {
                    db_path.stem: out_dir / f"{db_path.stem}.h5ad"
                    for db_path in out_db_paths
                }
                norm_result = run_tic_normalization(sample_h5ad_paths, out_dir=out_dir)
                logger.info(
                    "run %s: TIC-normalized %d sample(s), %d pixel(s), "
                    "median TIC %.3g -> %s",
                    analysis_id,
                    norm_result.n_samples,
                    norm_result.n_pixels,
                    norm_result.median_tic,
                    norm_result.merged_path.name,
                )
            else:
                logger.debug("Already run normalize_tic")
            self._emit_step("normalize_tic", "completed")
        else:
            logger.info("TIC normalization disabled; skipping")
            self._emit_step("normalize_tic", "skipped")

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
