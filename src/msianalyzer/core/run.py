from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from pathlib import Path
import sqlite3
from threading import local
import numpy as np
import pandas as pd
import logging

from concurrent.futures import ProcessPoolExecutor

from sqlalchemy.util import ellipses_string

from msianalyzer.core.config import Project, Config
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


@dataclass
class SampleResult:
    out_db_path: Path
    peaks_mzs: np.ndarray


def is_command_already_run(
    command_name: str, project_id: str, ms_db_path: str | Path
) -> bool:

    with sqlite3.connect(Path(ms_db_path)) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id FROM commands WHERE command_name = ? AND project_id = ?",
            (command_name, project_id),
        )
        return cursor.fetchone() is not None


def _process_one_sample(
    mzml_path: Path, xml_path: Path, config: Config, project_id: str
) -> SampleResult:
    logger.debug("%s: Starting processing.", mzml_path)
    out_dir = Path(config.io.out_dir)
    out_db_path = out_dir / f"{mzml_path.stem}.db"

    if not out_db_path.exists() or not is_command_already_run(
        "parse_spectra", project_id, out_db_path
    ):
        mzml_parser = MzmlParser()
        mzml_parser.parse(
            mzml_path=mzml_path, ms1_db_path=out_db_path, project_id=project_id
        )
        command_id = log_command(
            db_path=out_db_path,
            project_id=project_id,
            command_name="parse_spectra",
            arguments={},
        )
    else:
        logger.debug("%s: Already parsed spectra.", mzml_path)

    # MAP PIXELS
    if not is_command_already_run("map_pixels_to_db", project_id, out_db_path):
        df_pixels, _ = parse_raster_xml(xml_path)
        map_pixels_to_db(db_path=out_db_path, df_pixels=df_pixels)
        command_id = log_command(
            db_path=out_db_path,
            project_id=project_id,
            command_name="map_pixels_to_db",
            arguments={},
        )
        logger.debug("%s: Assigned %d pixels", mzml_path, df_pixels.shape[0])
    else:
        logger.debug("%s: Already run map_pixels_to_db", mzml_path)

    # GET AVERAGE MS1 SPECTRA
    if not is_command_already_run("get_average_ms1_spectra", project_id, out_db_path):
        average_ms1_mzs, average_ms1_intensities = get_average_ms1_spectra(
            db_path=out_db_path, **vars(config.ms1)
        )
        command_id = log_command(
            db_path=out_db_path,
            project_id=project_id,
            command_name="get_average_ms1_spectra",
            arguments={**vars(config.ms1)},
        )

        save_aggregated_spectra(
            average_ms1_mzs,
            average_ms1_intensities,
            ms1_db_path=out_db_path,
            project_id=project_id,
            command_id=command_id,
        )

        logger.debug("%s: Found %d average ms1 mzs", mzml_path, len(average_ms1_mzs))
    else:
        logger.debug("%s: Already run get_average_ms1_spectra", mzml_path)

    # DETECT MS1 CENTROIDS
    if not is_command_already_run("detect_ms1_centroids", project_id, out_db_path):
        if "average_ms1_mzs" not in locals():
            average_ms1_mzs, average_ms1_intensities = load_aggregated_spectra(
                out_db_path,
                project_id=project_id,
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
            project_id=project_id,
            arguments={**vars(config.centroid)},
        )

        save_aggregated_spectra(
            peaks_mzs,
            peaks_intensities,
            ms1_db_path=out_db_path,
            project_id=project_id,
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
    if not is_command_already_run("filter_spectra", project_id, out_db_path):
        if "peaks_mzs" not in locals():
            peaks_mzs, peaks_intensities = load_aggregated_spectra(
                out_db_path, project_id=project_id, command_name="detect_ms1_centroids"
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
            project_id=project_id,
            arguments={**vars(config.peak)},
        )

        save_aggregated_spectra(
            filtered_peaks_mzs,
            filtered_peaks_intensities,
            ms1_db_path=out_db_path,
            project_id=project_id,
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
                out_db_path, project_id=project_id, command_name="filter_spectra"
            )

        pl = Plotter()
        f = pl.plot_spectra(peaks_mzs, peaks_intensities)
        f.write_html(figure_path)
    else:
        logger.debug("%s: Already run create figure", mzml_path)

    peaks_df_path = out_dir / f"{mzml_path.stem}_peaks_data.csv"
    if not peaks_df_path.exists():
        if "filtered_peaks_mzs" not in locals():
            filtered_peaks_mzs, filtered_peaks_intensities = load_aggregated_spectra(
                out_db_path, project_id=project_id, command_name="filter_spectra"
            )

        peaks_df = pd.DataFrame(
            {"mz": filtered_peaks_mzs, "intensity": filtered_peaks_intensities}
        )
        peaks_df.to_csv(peaks_df_path)

    if "filtered_peaks_mzs" not in locals():
        filtered_peaks_mzs, filtered_peaks_intensities = load_aggregated_spectra(
            out_db_path, project_id=project_id, command_name="filter_spectra"
        )

    return SampleResult(out_db_path=out_db_path, peaks_mzs=filtered_peaks_mzs)


def run_core(project: Project, config: Config) -> None:
    worker = partial(_process_one_sample, config=config, project_id=project.uuid)

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

        mzs_df.to_csv()
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
                project_id=project.uuid,
                **vars(config.h5ad),
            )

            tmp_adata.write_h5ad(out_adata_path)
        else:
            logger.debug("%s: Already created adata object", db_path)
