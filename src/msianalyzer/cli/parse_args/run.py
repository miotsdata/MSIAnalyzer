from __future__ import annotations

import argparse
from pathlib import Path
import pandas as pd
import numpy as np

from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from functools import partial

from msianalyzer.core.config import Config
from msianalyzer.core.spectra.average_spectra import (
    detect_ms1_centroids,
    get_average_ms1_spectra,
    save_average_ms1_spectra,
)
from msianalyzer.core.spectra.mz_tools import align_mz_across_samples
from msianalyzer.core.utils.create_adata import create_spatial_adata
from msianalyzer.core.utils.spectra_pixels_association import map_pixels_to_db
from msianalyzer.core.parser import MzmlParser, parse_raster_xml, log_command


def _add_run_parser(subparsers: argparse._SubParsersAction) -> None:
    run = subparsers.add_parser(
        "run",
        help="Run the full workflow",
        description=(
            "Run the full MSI workflow. Load settings from a config file "
            "with -c/--config, and/or override individual settings via "
            "the flags below. CLI flags always take precedence over the "
            "config file."
        ),
    )

    run.add_argument(
        "-c",
        "--config",
        type=Path,
        default=None,
        help="Path to a .yaml/.yml or .toml config file.",
    )

    # --- required inputs (optional here, since they may come from -c) ---
    io_group = run.add_argument_group("input/output")
    io_group.add_argument(
        "--mzml-paths",
        nargs="+",
        action="extend",
        type=Path,
        default=None,
        metavar="PATH",
        help="One or more mzML files. Repeatable and/or space-separated.",
    )
    io_group.add_argument(
        "--xml-paths",
        nargs="+",
        action="extend",
        type=Path,
        default=None,
        metavar="PATH",
        help="One or more XML files. Repeatable and/or space-separated.",
    )
    io_group.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Output directory.",
    )

    # --- Average MS1 ---
    ms1_group = run.add_argument_group("average MS1")
    ms1_group.add_argument("--ms1-chunk-size", type=int, default=None)
    ms1_group.add_argument("--ms1-bin-width", type=float, default=None)
    ms1_group.add_argument("--ms1-min-mz", type=float, default=None)
    ms1_group.add_argument("--ms1-max-mz", type=float, default=None)

    # --- Detect centroids ---
    centroid_group = run.add_argument_group("detect centroids")
    centroid_group.add_argument("--snr-threshold", type=float, default=None)
    centroid_group.add_argument("--min-prominence-factor", type=float, default=None)
    centroid_group.add_argument("--min-distance-bins", type=int, default=None)

    # --- Peak threshold ---
    peak_group = run.add_argument_group("peak threshold")
    peak_group.add_argument("--peak-height-threshold", type=float, default=None)

    # --- Find all mzs ---
    mz_group = run.add_argument_group("find all mzs")
    mz_group.add_argument("--merge-mz-ppm", type=float, default=None)
    mz_group.add_argument(
        "--sample-names",
        nargs="+",
        action="extend",
        type=str,
        default=None,
        metavar="NAME",
    )

    # --- Create h5ad ---
    h5ad_group = run.add_argument_group("create h5ad")
    h5ad_group.add_argument("--integration-ppm", type=float, default=None)
    h5ad_group.add_argument("--integration-batch-size", type=int, default=None)
    h5ad_group.add_argument(
        "--integration-scan-handling",
        type=str,
        default=None,
        choices=["sum", "mean", "max"],  # adjust to your actual allowed values
    )
    h5ad_group.add_argument("--n-workers", type=int, default=None)

    run.set_defaults(func=run_command)


@dataclass
class SampleResult:
    out_db_path: Path
    peaks_mzs: np.ndarray


def _process_one_sample(
    mzml_path: Path,
    xml_path: Path,
    out_dir: Path,
    ms1_chunk_size: int,
    ms1_bin_width: float,
    ms1_min_mz: float,
    ms1_max_mz: float,
    snr_threshold: float,
    min_prominence_factor: float,
    min_distance_bins: int,
    peak_height_threshold: float,
) -> SampleResult:
    out_db_path = out_dir / f"{mzml_path.stem}.db"

    mzml_parser = MzmlParser()
    mzml_parser.parse(mzml_path=mzml_path, ms1_db_path=out_db_path)

    df_pixels, _ = parse_raster_xml(xml_path)
    map_pixels_to_db(db_path=out_db_path, df_pixels=df_pixels)

    average_ms1_mzs, average_ms1_intensities = get_average_ms1_spectra(
        db_path=out_db_path,
        chunk_size=ms1_chunk_size,
        bin_width=ms1_bin_width,
        min_mz=ms1_min_mz,
        max_mz=ms1_max_mz,
    )
    log_command(
        db_path=out_db_path,
        command_name="get_average_ms1_spectra",
        arguments={
            "chunk_size": ms1_chunk_size,
            "bin_width": ms1_bin_width,
            "min_mz": ms1_min_mz,
            "max_mz": ms1_max_mz,
        },
    )

    save_average_ms1_spectra(
        average_ms1_mzs,
        average_ms1_intensities,
        ms1_bin_width,
        ms1_db_path=out_db_path,
    )

    peaks_mzs, peaks_intensities = detect_ms1_centroids(
        bin_centers=average_ms1_mzs,
        mean_intensities=average_ms1_intensities,
        snr_threshold=snr_threshold,
        min_prominence_factor=min_prominence_factor,
        min_distance_bins=min_distance_bins,
    )
    log_command(
        db_path=out_db_path,
        command_name="detect_ms1_centroids",
        arguments={
            "snr_threshold": snr_threshold,
            "min_prominence_factor": min_prominence_factor,
            "min_distance_bins": min_distance_bins,
        },
    )

    mask = peaks_intensities >= peak_height_threshold
    peaks_mzs = peaks_mzs[mask]
    peaks_intensities = peaks_intensities[mask]

    peaks_df = pd.DataFrame({"mz": peaks_mzs, "intensity": peaks_intensities})
    peaks_df.to_csv(out_dir / f"{mzml_path.stem}_peaks_data.csv")

    return SampleResult(out_db_path=out_db_path, peaks_mzs=peaks_mzs)


def run_command(args: argparse.Namespace) -> None:
    config: Config = resolve_config(args)
    out_dir = Path(config.out_dir)
    mzml_paths = [Path(m) for m in config.mzml_paths]
    xml_paths = [Path(x) for x in config.xml_paths]

    worker = partial(
        _process_one_sample,
        out_dir=out_dir,
        ms1_chunk_size=config.ms1_chunk_size,
        ms1_bin_width=config.ms1_bin_width,
        ms1_min_mz=config.ms1_min_mz,
        ms1_max_mz=config.ms1_max_mz,
        snr_threshold=config.snr_threshold,
        min_prominence_factor=config.min_prominence_factor,
        min_distance_bins=config.min_distance_bins,
        peak_height_threshold=config.peak_height_threshold,
    )

    with ProcessPoolExecutor(max_workers=config.n_workers) as executor:
        results = list(executor.map(worker, mzml_paths, xml_paths))

    out_db_paths = [r.out_db_path for r in results]
    all_peaks_mzs = [r.peaks_mzs for r in results]

    # Align all mzs
    mzs_df = align_mz_across_samples(
        mz_arrays=all_peaks_mzs,
        sample_names=config.sample_names
        if config.sample_names is not None
        else [str(m) for m in config.mzml_paths],
        ppm=config.merge_mz_ppm,
    )

    mzs_df.to_csv(str(config.out_dir) + "/aligned_mzs.csv")

    for db_path in out_db_paths:
        tmp_adata = create_spatial_adata(
            db_path=db_path,
            target_mz_set=set(mzs_df.index),
            batch_size=config.integration_batch_size,
            ppm_val=config.integration_ppm,
            scan_handling=config.integration_scan_handling,
        )

        tmp_adata.write_h5ad(str(config.out_dir) + "/" + str(db_path.stem) + ".h5ad")


def resolve_config(args: argparse.Namespace) -> Config:
    """Merge a config file (if given) with CLI overrides.

    CLI flags that were explicitly set (i.e. not None) always win over
    the config file; the config file wins over Config's own defaults.
    """
    if args.config is not None:
        base = Config.load(args.config).to_dict()
    else:
        base = {}

    overrides = {
        "mzml_paths": args.mzml_paths,
        "xml_paths": args.xml_paths,
        "out_dir": args.out_dir,
        "ms1_chunk_size": args.ms1_chunk_size,
        "ms1_bin_width": args.ms1_bin_width,
        "ms1_min_mz": args.ms1_min_mz,
        "ms1_max_mz": args.ms1_max_mz,
        "snr_threshold": args.snr_threshold,
        "min_prominence_factor": args.min_prominence_factor,
        "min_distance_bins": args.min_distance_bins,
        "peak_height_threshold": args.peak_height_threshold,
        "merge_mz_ppm": args.merge_mz_ppm,
        "sample_names": args.sample_names,
        "integration_ppm": args.integration_ppm,
        "integration_batch_size": args.integration_batch_size,
        "integration_scan_handling": args.integration_scan_handling,
        "n_workers": args.n_workers,
    }
    for key, value in overrides.items():
        if value is not None:
            base[key] = value

    missing = [k for k in ("mzml_paths", "xml_paths", "out_dir") if k not in base]
    if missing:
        raise SystemExit(
            f"Missing required argument(s): {', '.join(missing)}. "
            "Provide via -c/--config or the corresponding CLI flag."
        )

    return Config.from_dict(base)
