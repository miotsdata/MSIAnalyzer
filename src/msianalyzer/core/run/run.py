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
from typing import Any

import numpy as np
import pandas as pd

from msianalyzer.core import analysis_db
from msianalyzer.core.annotation.annotate import run_annotation
from msianalyzer.core.annotation.group_ms2 import run_grouper
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
from msianalyzer.core.utils.spectra_pixels_association import map_pixels_to_db

logger = logging.getLogger(__name__)


class RunStatus(Enum):
    """Lifecycle state of a `Run`."""

    RUNNING = 1
    COMPLETED = 0


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
        project: The owning `Project`, or None before `start`.
    """

    def __init__(self, config_file: str | Path | None = None):
        self.id: str = str(uuid.uuid4())
        self.start_date: datetime.datetime | None = None
        self.end_date: datetime.datetime | None = None
        self.status: RunStatus = RunStatus.RUNNING
        self.config: Config | None = None
        self.project: Project | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise the run to a plain, YAML-friendly dict.

        `datetime` values are stringified and list items exposing `to_dict`
        are expanded.

        Returns:
            A dict representation of the run.
        """
        d = {}
        for key, value in vars(self).items():
            if isinstance(value, datetime.datetime):
                d[key] = str(value)
            elif isinstance(value, list):
                d[key] = [
                    item.to_dict() if hasattr(item, "to_dict") else item
                    for item in value
                ]
            else:
                d[key] = value
        return d

    def start(
        self,
        config_file: str | Path | None = None,
        config: Config | None = None,
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

        Raises:
            ValueError: If neither `config_file` nor `config` is provided.
        """
        if config is not None:
            self.config = config
        elif config_file is not None:
            self.config = Config.from_yaml(config_file)
        else:
            raise ValueError("Neither config_file nor config was provided.")

        project_path = Path(self.config.io.project_folder) / ".msianalyzer.yml"
        self.project = Project.load()
        self.start_date = datetime.datetime.now()
        self.project.runs[self.id] = self.to_dict()
        self.project.export(project_path)

        os.chdir(self.config.io.project_folder)

        self.run_core()

        self.end_date = datetime.datetime.now()
        self.status = RunStatus.COMPLETED
        self.project.runs[self.id] = self.to_dict()
        self.project.export(project_path)

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
            logger.debug("%s: Assigned %d pixels", mzml_path, df_pixels.shape[0])
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
            logger.debug("%s: Found %d average ms1 mzs", mzml_path, len(average_ms1_mzs))
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
            logger.debug(
                "%s: Detected %d centroids", mzml_path, len(peaks_mzs)
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
            logger.debug(
                "%s: After filtering: %d peaks", mzml_path, len(filtered_peaks_mzs)
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

        with ProcessPoolExecutor(max_workers=config.h5ad.n_workers) as executor:
            results = list(
                executor.map(
                    worker,
                    config.io.mzml_paths,
                    config.io.xml_paths,
                    sample_ids,
                    raw_db_paths,
                )
            )

        out_db_paths = [r.out_db_path for r in results]
        all_peaks_mzs = [r.peaks_mzs for r in results]

        # --- ALIGN m/z ACROSS SAMPLES ---
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
        else:
            logger.debug("Already run align mz across samples")
            mzs_df = pd.read_csv(aligned_df_path, index_col=0)

        # --- ASSOCIATE MS2 SCANS WITH FEATURES (analysis DB) ---
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
            )
            logger.info(
                "Associated %d MS2 scans across %d features with MS2 coverage",
                len(grouping.associations),
                len(grouping.feature_summary),
            )
        else:
            logger.debug("Already run group_ms2")

        # --- ANNOTATE MS2 AGAINST SPECTRAL LIBRARY (analysis DB) ---
        if config.annotate.library_path:
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
        else:
            logger.info(
                "No annotation library configured; skipping MS2 annotation"
            )

        # --- SPATIAL AnnData PER SAMPLE ---
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
            else:
                logger.debug("%s: Already created adata object", db_path)
