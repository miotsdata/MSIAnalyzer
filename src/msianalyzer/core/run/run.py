from __future__ import annotations

from dataclasses import dataclass
import datetime
from functools import partial
from pathlib import Path
import sqlite3
import numpy as np
import pandas as pd
import logging
import uuid
from typing import Any
import os

from concurrent.futures import ProcessPoolExecutor
from enum import Enum



from msianalyzer.core.config import Config
from msianalyzer.core.project import Project
from msianalyzer.core.parser import MzmlParser, log_command, parse_raster_xml
from msianalyzer.core.plotting.plotter import Plotter
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
    RUNNING = 1
    COMPLETED = 0

@dataclass
class SampleResult:
    out_db_path: Path
    peaks_mzs: np.ndarray

class Run:

    def __init__(self, config_file: str | Path):
        self.id: str = str(uuid.uuid4())
        self.start_date: datetime.datetime | None = None
        self.end_date: datetime.datetime | None = None
        self.status: RunStatus = RunStatus.RUNNING
        self.config: Config | None = None
        self.project: Project | None = None

    def to_dict(self) -> dict[str, Any]:
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


    def start(self, config_file: str | Path | None = None, config: Config | None = None) -> None:
        if config is not None:
            self.config = config
        elif config_file is not None:
            self.config = Config.from_yaml(config_file)
        else:
            raise ValueError("None of config_file or config provided.")
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
        with sqlite3.connect(Path(ms_db_path)) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id FROM commands WHERE command_name = ? AND run_id = ?",
                (command_name, run_id),
            )
            return cursor.fetchone() is not None


    @staticmethod
    def _process_one_sample(
        mzml_path: Path, xml_path: Path, config: Config, run_id: str
    ) -> SampleResult:
        logger.debug("%s: Starting processing.", mzml_path)
        out_dir = Path(config.io.out_dir)
        out_db_path = out_dir / f"{mzml_path.stem}.db"

        if not out_db_path.exists() or not Run.is_command_already_run(
            "parse_spectra", run_id, out_db_path
        ):
            mzml_parser = MzmlParser()
            mzml_parser.parse(
                mzml_path=mzml_path, ms1_db_path=out_db_path
            )
            command_id = log_command(
                db_path=out_db_path,
                run_id=run_id,
                command_name="parse_spectra",
                arguments={},
            )
        else:
            logger.debug("%s: Already parsed spectra.", mzml_path)

        # MAP PIXELS
        if not Run.is_command_already_run("map_pixels_to_db", run_id, out_db_path):
            df_pixels, _ = parse_raster_xml(xml_path)
            map_pixels_to_db(db_path=out_db_path, df_pixels=df_pixels)
            command_id = log_command(
                db_path=out_db_path,
                run_id=run_id,
                command_name="map_pixels_to_db",
                arguments={},
            )
            logger.debug("%s: Assigned %d pixels", mzml_path, df_pixels.shape[0])
        else:
            logger.debug("%s: Already run map_pixels_to_db", mzml_path)

        # GET AVERAGE MS1 SPECTRA
        if not Run.is_command_already_run("get_average_ms1_spectra", run_id, out_db_path):
            average_ms1_mzs, average_ms1_intensities = get_average_ms1_spectra(
                db_path=out_db_path, **vars(config.ms1)
            )
            command_id = log_command(
                db_path=out_db_path,
                run_id=run_id,
                command_name="get_average_ms1_spectra",
                arguments={**vars(config.ms1)},
            )

            save_aggregated_spectra(
                average_ms1_mzs,
                average_ms1_intensities,
                ms1_db_path=out_db_path,
                run_id=run_id,
                command_id=command_id,
            )

            logger.debug("%s: Found %d average ms1 mzs", mzml_path, len(average_ms1_mzs))
        else:
            logger.debug("%s: Already run get_average_ms1_spectra", mzml_path)

        # DETECT MS1 CENTROIDS
        if not Run.is_command_already_run("detect_ms1_centroids", run_id, out_db_path):
            if "average_ms1_mzs" not in locals():
                average_ms1_mzs, average_ms1_intensities = load_aggregated_spectra(
                    out_db_path,
                    run_id=run_id,
                    command_name="get_average_ms1_spectra",
                )

            peaks_mzs, peaks_intensities = detect_ms1_centroids(
                bin_centers=average_ms1_mzs,
                mean_intensities=average_ms1_intensities,
                **vars(config.centroid),
            )

            command_id = log_command(
                db_path=out_db_path,
                command_name="detect_ms1_centroids",
                run_id=run_id,
                arguments={**vars(config.centroid)},
            )

            save_aggregated_spectra(
                peaks_mzs,
                peaks_intensities,
                ms1_db_path=out_db_path,
                run_id=run_id,
                command_id=command_id,
            )

            logger.debug(
                "%s: Detected %d centroids for file %s",
                mzml_path,
                len(peaks_mzs),
                mzml_path,
            )
        else:
            logger.debug("%s: Already run detect_ms1_centroids", mzml_path)

        # PEAK FILTERING
        if not Run.is_command_already_run("filter_spectra", run_id, out_db_path):
            if "peaks_mzs" not in locals():
                peaks_mzs, peaks_intensities = load_aggregated_spectra(
                    out_db_path, run_id=run_id, command_name="detect_ms1_centroids"
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

            command_id = log_command(
                db_path=out_db_path,
                command_name="filter_spectra",
                run_id=run_id,
                arguments={**vars(config.peak)},
            )

            save_aggregated_spectra(
                filtered_peaks_mzs,
                filtered_peaks_intensities,
                ms1_db_path=out_db_path,
                run_id=run_id,
                command_id=command_id,
            )

            logger.debug(
                "%s: After filtering: %d peaks, min int %d, max int %d",
                mzml_path,
                len(filtered_peaks_mzs),
                min(filtered_peaks_intensities),
                max(filtered_peaks_intensities),
            )
        else:
            logger.debug("%s: Already run filter_spectra", mzml_path)

        figure_path = out_dir / f"{mzml_path.stem}_filtered_ms1.html"
        if not figure_path.exists():
            if "filtered_peaks_mzs" not in locals():
                filtered_peaks_mzs, filtered_peaks_intensities = load_aggregated_spectra(
                    out_db_path, run_id=run_id, command_name="filter_spectra"
                )

            pl = Plotter()
            f = pl.plot_spectra(filtered_peaks_mzs, filtered_peaks_intensities)
            f.write_html(figure_path)
        else:
            logger.debug("%s: Already run create figure", mzml_path)

        peaks_df_path = out_dir / f"{mzml_path.stem}_peaks_data.csv"
        if not peaks_df_path.exists():
            if "filtered_peaks_mzs" not in locals():
                filtered_peaks_mzs, filtered_peaks_intensities = load_aggregated_spectra(
                    out_db_path, run_id=run_id, command_name="filter_spectra"
                )

            peaks_df = pd.DataFrame(
                {"mz": filtered_peaks_mzs, "intensity": filtered_peaks_intensities}
            )
            peaks_df.to_csv(peaks_df_path)

        if "filtered_peaks_mzs" not in locals():
            filtered_peaks_mzs, filtered_peaks_intensities = load_aggregated_spectra(
                out_db_path, run_id=run_id, command_name="filter_spectra"
            )

        return SampleResult(out_db_path=out_db_path, peaks_mzs=filtered_peaks_mzs)


    def run_core(self) -> None:
        config = self.config
        project = self.project

        worker = partial(Run._process_one_sample, config=config, run_id=project.uuid)

        with ProcessPoolExecutor(max_workers=config.h5ad.n_workers) as executor:
            results = list(executor.map(worker, config.io.mzml_paths, config.io.xml_paths))

        out_db_paths = [r.out_db_path for r in results]
        all_peaks_mzs = [r.peaks_mzs for r in results]

        # Align all mzs
        out_dir = Path(config.io.out_dir)
        aligned_df_path: Path = out_dir / "aligned_mzs.csv"
        if not aligned_df_path.exists():
            mzs_df = align_mz_across_samples(
                mz_arrays=all_peaks_mzs,
                sample_names=config.align.sample_names
                if config.align.sample_names is not None
                else [str(m) for m in config.io.mzml_paths],
                align_ppm=config.align.align_ppm,
                mz_decimals=config.align.mz_decimals,
            )

            mzs_df.to_csv(aligned_df_path)
        else:
            logger.debug("Already run align mz across samples")

        if "mzs_df" not in locals():
            mzs_df = pd.read_csv(aligned_df_path)

        for db_path in out_db_paths:
            out_adata_path = out_dir / f"{db_path.stem}.h5ad"
            if not out_adata_path.exists():
                tmp_adata = create_spatial_adata(
                    db_path=db_path,
                    target_mz_set=mzs_df.index,
                    run_id=project.uuid,
                    **vars(config.h5ad),
                )

                tmp_adata.write_h5ad(out_adata_path)
            else:
                logger.debug("%s: Already created adata object", db_path)
