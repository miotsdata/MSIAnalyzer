"""Tests for the Export menu's backing functions (core/export.py)."""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

import anndata as ad
import numpy as np
import pandas as pd
import pytest
from scipy.sparse import csr_matrix

from msianalyzer.core.analysis_db import (
    init_analysis_db,
    register_calibration_curve,
    register_sample,
    save_scale_bar,
    upsert_calibration_point,
    upsert_calibration_unknown,
)
from msianalyzer.core.export import (
    VAR_ANNOTATION_COLUMNS,
    export_annotation_table,
    export_calibration_results,
    export_integration_tables,
    export_visual_inspection_images,
    var_has_annotations,
    write_annotations_to_var,
)
from msianalyzer.core.plotting.roi import save_roi_to_sample


def _read_rows(path: Path, delimiter: str) -> list[list[str]]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.reader(f, delimiter=delimiter))


def _seed_two_features(db: Path) -> None:
    """Feature 7: ms2-annotated, with adduct/cas/hmdb and a library name.
    Feature 9: no annotation at all."""
    with sqlite3.connect(db) as con:
        con.execute(
            "INSERT INTO annotation_libraries (id, path, name) "
            "VALUES (1, 'lib.db', 'my_library')"
        )
        con.execute(
            "INSERT INTO features (feature_id, mz, members_json) VALUES (7, 123.4567, '{}')"
        )
        con.execute(
            "INSERT INTO features (feature_id, mz, members_json) VALUES (9, 50.0, '{}')"
        )
        con.execute(
            "INSERT INTO ms2_annotations "
            "(id, feature_id, sample_id, scan_id, library_id, library_spectrum_id, "
            "compound_name, compound_formula, inchikey, adduct, cas, hmdb, "
            "score, dot_product_score, lib_coverage, emp_coverage, coverage_score, "
            "n_matched_peaks, n_lib_peaks, n_emp_peaks_raw, n_emp_peaks_filtered, "
            "rank_ms2, rank_feature) "
            "VALUES (1, 7, 1, 42, 1, 9, 'Caffeine, decaf?', 'C8H10N4O2', "
            "'RYYVLZVUVIJVGH-UHFFFAOYSA-N', '[M+H]+', '58-08-2', 'HMDB0001847', "
            "0.87, 0.9, 0.8, 0.75, 0.77, 2, 2, 10, 3, 1, 1)"
        )
        con.commit()


def test_export_annotation_table_writes_header_and_rows(tmp_path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    _seed_two_features(db)
    dest = tmp_path / "out.csv"

    export_annotation_table(db, dest)

    rows = _read_rows(dest, ",")
    assert rows[0] == [
        "feature_id", "mz", "compound_name", "compound_formula", "adduct",
        "inchikey", "cas", "hmdb", "library_name", "source", "score",
        "ms2_annotation_outcome", "precursor_name", "precursor_ppm",
        "n_precursor_candidates",
    ]
    assert len(rows) == 3  # header + 2 features


def test_export_annotation_table_populates_ms2_tier_fields(tmp_path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    _seed_two_features(db)
    dest = tmp_path / "out.csv"

    export_annotation_table(db, dest)

    rows = {r[0]: r for r in _read_rows(dest, ",")[1:]}
    row7 = rows["7"]
    assert row7[1] == "123.4567"
    assert row7[2] == "Caffeine, decaf?"  # the embedded comma survives quoting
    assert row7[3] == "C8H10N4O2"
    assert row7[4] == "[M+H]+"
    assert row7[5] == "RYYVLZVUVIJVGH-UHFFFAOYSA-N"
    assert row7[6] == "58-08-2"
    assert row7[7] == "HMDB0001847"
    assert row7[8] == "my_library"
    assert row7[9] == "ms2"
    assert float(row7[10]) == pytest.approx(0.87)


def test_export_annotation_table_includes_unidentified_feature_with_blank_fields(
    tmp_path,
):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    _seed_two_features(db)
    dest = tmp_path / "out.csv"

    export_annotation_table(db, dest)

    rows = {r[0]: r for r in _read_rows(dest, ",")[1:]}
    row9 = rows["9"]
    assert row9[1] == "50.0"
    # Every identity field blank, not "None"/"nan"/"0"; no precursor candidate.
    assert row9[2:] == [""] * 12 + ["0"]


def test_export_annotation_table_txt_extension_is_tab_delimited(tmp_path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    _seed_two_features(db)
    dest = tmp_path / "out.txt"

    export_annotation_table(db, dest)

    rows = _read_rows(dest, "\t")
    assert rows[0][0] == "feature_id"
    assert len(rows) == 3
    # Confirms it's genuinely tab-delimited, not comma content that
    # happens to also parse when split on tabs.
    raw = dest.read_text(encoding="utf-8")
    assert "\t" in raw.splitlines()[0]


def test_export_annotation_table_csv_extension_is_comma_delimited(tmp_path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    _seed_two_features(db)
    dest = tmp_path / "out.csv"

    export_annotation_table(db, dest)

    raw = dest.read_text(encoding="utf-8")
    assert "," in raw.splitlines()[0]
    assert "\t" not in raw


def test_export_annotation_table_quotes_every_string_field_even_without_a_comma(
    tmp_path,
):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    _seed_two_features(db)
    dest = tmp_path / "out.csv"

    export_annotation_table(db, dest)

    header_line = dest.read_text(encoding="utf-8").splitlines()[0]
    # QUOTE_NONNUMERIC quotes every non-numeric *field*, including the
    # header row itself (every column name is a plain str) — not just
    # values that happen to need it.
    assert '"feature_id"' in header_line
    assert '"compound_name"' in header_line

    lines = dest.read_text(encoding="utf-8").splitlines()
    row7_line = next(l for l in lines if l.startswith("7,"))
    assert '"Caffeine, decaf?"' in row7_line
    assert '"ms2"' in row7_line
    # feature_id/mz themselves stay unquoted numbers.
    assert row7_line.startswith("7,123.4567,")
    # score and the precursor count are unquoted numbers too
    assert row7_line.endswith(',"ms2",0.87,"","","",0')


def test_export_annotation_table_creates_destination_directory(tmp_path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    _seed_two_features(db)
    dest = tmp_path / "nested" / "dir" / "out.csv"

    export_annotation_table(db, dest)

    assert dest.exists()


def test_export_annotation_table_empty_analysis_writes_header_only(tmp_path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    dest = tmp_path / "out.csv"

    export_annotation_table(db, dest)

    rows = _read_rows(dest, ",")
    assert len(rows) == 1  # header only, no features at all


def _seed_sample_with_h5ad(
    db: Path, name: str, *, raw_values, tic_values, mzs=(100.0, 200.0)
) -> None:
    """Register a sample and write a matching 2-pixel `.h5ad` next to
    `db`, same layout `test_heatmap.py`'s own `_make_grid_adata` uses —
    one row per pixel, `X` = raw, `layers["TIC"]` = TIC-normalized."""
    register_sample(db, name=name, raw_db_path=db.parent / f"{name}_raw.db")
    obs = pd.DataFrame(index=["px0", "px1"])
    var = pd.DataFrame({"mz": list(mzs)}, index=[f"mz_{m:.4f}" for m in mzs])
    X = csr_matrix(np.array(raw_values, dtype=np.float32))
    adata = ad.AnnData(X=X, obs=obs, var=var)
    adata.obsm["spatial"] = np.array([(0.0, 0.0), (1.0, 0.0)], dtype=float)
    adata.layers["TIC"] = csr_matrix(np.array(tic_values, dtype=np.float32))
    adata.write_h5ad(db.parent / f"{name}.h5ad")


def _seed_two_feature_rows(db: Path, mzs=(100.0, 200.0)) -> None:
    with sqlite3.connect(db) as con:
        for i, mz in enumerate(mzs, start=1):
            con.execute(
                "INSERT INTO features (feature_id, mz, members_json) VALUES (?, ?, '{}')",
                (i, mz),
            )
        con.commit()


def test_export_integration_tables_writes_one_file_per_sample(tmp_path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    _seed_two_feature_rows(db)
    _seed_sample_with_h5ad(
        db, "sampleA", raw_values=[[1.0, 2.0], [3.0, 4.0]], tic_values=[[10.0, 20.0], [30.0, 40.0]]
    )
    _seed_sample_with_h5ad(
        db, "sampleB", raw_values=[[5.0, 6.0], [7.0, 8.0]], tic_values=[[50.0, 60.0], [70.0, 80.0]]
    )

    written = export_integration_tables(db, tmp_path / "out", "TIC")

    assert written == 2
    assert (tmp_path / "out" / "sampleA_integration.csv").exists()
    assert (tmp_path / "out" / "sampleB_integration.csv").exists()


def test_export_integration_tables_header_is_x_y_and_mz_to_4_decimals(tmp_path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    _seed_two_feature_rows(db, mzs=(100.5, 200.25))
    _seed_sample_with_h5ad(
        db, "sampleA", raw_values=[[1.0, 2.0], [3.0, 4.0]], tic_values=[[10.0, 20.0], [30.0, 40.0]],
        mzs=(100.5, 200.25),
    )

    export_integration_tables(db, tmp_path / "out", "TIC")

    rows = _read_rows(tmp_path / "out" / "sampleA_integration.csv", ",")
    assert rows[0] == ["x", "y", "100.5000", "200.2500"]


def test_export_integration_tables_raw_vs_tic_selects_the_right_layer(tmp_path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    _seed_two_feature_rows(db)
    _seed_sample_with_h5ad(
        db, "sampleA", raw_values=[[1.0, 2.0], [3.0, 4.0]], tic_values=[[10.0, 20.0], [30.0, 40.0]]
    )

    export_integration_tables(db, tmp_path / "raw_out", "raw")
    export_integration_tables(db, tmp_path / "tic_out", "TIC")

    raw_rows = _read_rows(tmp_path / "raw_out" / "sampleA_integration.csv", ",")
    tic_rows = _read_rows(tmp_path / "tic_out" / "sampleA_integration.csv", ",")
    assert raw_rows[1] == ["0.0", "0.0", "1.0", "2.0"]
    assert tic_rows[1] == ["0.0", "0.0", "10.0", "20.0"]


def test_export_integration_tables_txt_extension_is_tab_delimited(tmp_path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    _seed_two_feature_rows(db)
    _seed_sample_with_h5ad(
        db, "sampleA", raw_values=[[1.0, 2.0], [3.0, 4.0]], tic_values=[[10.0, 20.0], [30.0, 40.0]]
    )

    export_integration_tables(db, tmp_path / "out", "TIC", file_format="txt")

    dest = tmp_path / "out" / "sampleA_integration.txt"
    assert dest.exists()
    raw = dest.read_text(encoding="utf-8")
    assert "\t" in raw.splitlines()[0]
    assert "," not in raw.splitlines()[0].replace('"', "")


def test_export_integration_tables_skips_sample_with_missing_h5ad(tmp_path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    _seed_two_feature_rows(db)
    register_sample(db, name="ghost", raw_db_path=tmp_path / "ghost_raw.db")

    written = export_integration_tables(db, tmp_path / "out", "TIC")

    assert written == 0
    assert not (tmp_path / "out" / "ghost_integration.csv").exists()


def test_export_integration_tables_creates_destination_directory(tmp_path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    _seed_two_feature_rows(db)
    _seed_sample_with_h5ad(
        db, "sampleA", raw_values=[[1.0, 2.0], [3.0, 4.0]], tic_values=[[10.0, 20.0], [30.0, 40.0]]
    )

    export_integration_tables(db, tmp_path / "nested" / "dir", "TIC")

    assert (tmp_path / "nested" / "dir" / "sampleA_integration.csv").exists()


def _seed_sample_with_obs(
    db: Path, name: str, *, obs_columns: dict | None = None
) -> None:
    register_sample(db, name=name, raw_db_path=db.parent / f"{name}_raw.db")
    obs = pd.DataFrame(index=["px0", "px1", "px2", "px3"])
    for col, values in (obs_columns or {}).items():
        obs[col] = values
    var = pd.DataFrame({"mz": [100.0]}, index=["mz_100.0000"])
    X = csr_matrix(np.array([[1.0], [2.0], [3.0], [4.0]], dtype=np.float32))
    adata = ad.AnnData(X=X, obs=obs, var=var)
    adata.obsm["spatial"] = np.array(
        [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0)], dtype=float
    )
    adata.layers["TIC"] = csr_matrix(np.array([[10.0], [20.0], [30.0], [40.0]], dtype=np.float32))
    adata.write_h5ad(db.parent / f"{name}.h5ad")


def test_export_visual_inspection_images_writes_one_file_per_sample(tmp_path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    _seed_sample_with_obs(db, "sampleA")
    _seed_sample_with_obs(db, "sampleB")

    written = export_visual_inspection_images(
        db, tmp_path / "out", "png", "feature", mz=100.0
    )

    assert written == 2
    assert (tmp_path / "out" / "sampleA.png").exists()
    assert (tmp_path / "out" / "sampleB.png").exists()


def test_export_visual_inspection_images_respects_format_extension(tmp_path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    _seed_sample_with_obs(db, "sampleA")

    export_visual_inspection_images(db, tmp_path / "out", "svg", "feature", mz=100.0)

    dest = tmp_path / "out" / "sampleA.svg"
    assert dest.exists()
    assert dest.read_text(encoding="utf-8").lstrip().startswith("<?xml")


def test_export_visual_inspection_images_obs_categorical_mode(tmp_path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    _seed_sample_with_obs(
        db, "sampleA", obs_columns={"polarity": ["neg", "pos", "neg", "pos"]}
    )

    written = export_visual_inspection_images(
        db, tmp_path / "out", "png", "obs", obs_column="polarity"
    )

    assert written == 1
    assert (tmp_path / "out" / "sampleA.png").exists()


def test_export_visual_inspection_images_skips_sample_with_missing_h5ad(tmp_path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    register_sample(db, name="ghost", raw_db_path=tmp_path / "ghost_raw.db")

    written = export_visual_inspection_images(
        db, tmp_path / "out", "png", "feature", mz=100.0
    )

    assert written == 0


def test_export_visual_inspection_images_with_rois_still_writes_file(tmp_path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    _seed_sample_with_obs(db, "sampleA")
    save_roi_to_sample(
        db.parent / "sampleA.h5ad", "tumor", "#ff0000", [[0, 0], [1, 0], [1, 1], [0, 1]]
    )

    written = export_visual_inspection_images(
        db, tmp_path / "out", "png", "feature", mz=100.0, show_rois=True
    )

    assert written == 1
    assert (tmp_path / "out" / "sampleA.png").exists()


def test_export_visual_inspection_images_creates_destination_directory(tmp_path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    _seed_sample_with_obs(db, "sampleA")

    export_visual_inspection_images(
        db, tmp_path / "nested" / "dir", "png", "feature", mz=100.0
    )

    assert (tmp_path / "nested" / "dir" / "sampleA.png").exists()


# ---------------------------------------------------------------------------
# export_calibration_results
# ---------------------------------------------------------------------------


def test_export_calibration_results_writes_nothing_when_analysis_is_empty(tmp_path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()

    written = export_calibration_results(db, tmp_path / "out")

    assert written == 0
    assert not (tmp_path / "out" / "calibration_standards.csv").exists()


def test_export_calibration_results_writes_standards_curve_and_unknowns(tmp_path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    upsert_calibration_point(
        db, sample_id=None, roi_name="std_a", amount=10.0, amount_unit="pmol",
        pixel_size=0.1, pixel_size_unit="mm", area=0.02, concentration=500.0,
        mean_intensity=100.0, n_pixels=2,
    )
    curve_id = register_calibration_curve(
        db, analyte_mz=200.0, fit_type="linear", weighting=None,
        concentration_unit="pmol/mm2", slope=0.2, intercept=0.0, r_squared=0.99,
        lod=1.0, loq=3.0, n_points=2,
    )
    upsert_calibration_unknown(
        db, sample_id=None, roi_name="spot_1", pixel_size=0.1, pixel_size_unit="mm",
        area=0.02, mean_intensity=50.0, n_pixels=2, curve_id=curve_id,
        interpolated_conc=250.0, amount=5.0,
    )

    written = export_calibration_results(db, tmp_path / "out")

    assert written == 3
    standards = pd.read_csv(tmp_path / "out" / "calibration_standards.csv")
    assert list(standards["roi_name"]) == ["std_a"]
    curve = pd.read_csv(tmp_path / "out" / "calibration_curve.csv")
    assert curve.iloc[0]["slope"] == pytest.approx(0.2)
    unknowns = pd.read_csv(tmp_path / "out" / "calibration_unknowns.csv")
    assert unknowns.iloc[0]["roi_name"] == "spot_1"
    assert unknowns.iloc[0]["interpolated_conc"] == pytest.approx(250.0)


def test_export_calibration_results_only_writes_existing_tables(tmp_path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    upsert_calibration_point(
        db, sample_id=None, roi_name="std_a", amount=10.0, amount_unit="pmol",
        pixel_size=0.1, pixel_size_unit="mm", area=0.02, concentration=500.0,
        mean_intensity=100.0, n_pixels=2,
    )

    written = export_calibration_results(db, tmp_path / "out")

    assert written == 1
    assert (tmp_path / "out" / "calibration_standards.csv").exists()
    assert not (tmp_path / "out" / "calibration_curve.csv").exists()
    assert not (tmp_path / "out" / "calibration_unknowns.csv").exists()


def test_export_calibration_results_creates_destination_directory(tmp_path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    upsert_calibration_point(
        db, sample_id=None, roi_name="std_a", amount=10.0, amount_unit="pmol",
        pixel_size=0.1, pixel_size_unit="mm", area=0.02, concentration=500.0,
        mean_intensity=100.0, n_pixels=2,
    )

    export_calibration_results(db, tmp_path / "nested" / "dir")

    assert (tmp_path / "nested" / "dir" / "calibration_standards.csv").exists()


def test_export_visual_inspection_images_scale_bar_only_when_requested(tmp_path, monkeypatch):
    import msianalyzer.core.export as export_module

    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    _seed_sample_with_obs(db, "sampleA")
    _seed_sample_with_obs(db, "sampleB")
    # Off in the GUI, sampleB has no pixel size: the export checkbox still
    # wins, and sampleB gets no bar.
    save_scale_bar(db, {"enabled": False, "bar_um": 20.0, "color": "#000000",
                        "pixel_um": {"sampleA": 10.0}})
    seen = {}
    real = export_module.render_heatmap_figure

    def spy(adata, **kwargs):
        seen[kwargs["title"]] = kwargs["scale_bar"]
        return real(adata, **kwargs)

    monkeypatch.setattr(export_module, "render_heatmap_figure", spy)

    export_visual_inspection_images(
        db, tmp_path / "with", "png", "feature", mz=100.0, include_scale_bar=True
    )
    assert seen == {
        "sampleA": {"length_px": 2.0, "label": "20 µm", "color": "#000000"},
        "sampleB": None,
    }

    export_visual_inspection_images(db, tmp_path / "without", "png", "feature", mz=100.0)
    assert seen == {"sampleA": None, "sampleB": None}


def test_export_visual_inspection_images_passes_hidden_categories(tmp_path, monkeypatch):
    import msianalyzer.core.export as export_module

    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    _seed_sample_with_obs(db, "sampleA", obs_columns={"polarity": ["neg", "pos", "neg", "pos"]})
    seen = []
    real = export_module.render_heatmap_figure

    def spy(adata, **kwargs):
        seen.append(kwargs["hidden_categories"])
        return real(adata, **kwargs)

    monkeypatch.setattr(export_module, "render_heatmap_figure", spy)

    export_visual_inspection_images(
        db, tmp_path / "shown", "png", "obs", obs_column="polarity", hidden_categories=["neg"]
    )
    export_visual_inspection_images(db, tmp_path / "all", "png", "obs", obs_column="polarity")
    assert seen == [{"neg"}, set()]


def _write_var_h5ad(path: Path, mzs) -> None:
    var = pd.DataFrame({"mz": list(mzs)}, index=[f"mz_{m:.4f}" for m in mzs])
    X = csr_matrix(np.arange(2 * len(mzs), dtype=np.float32).reshape(2, -1))
    ad.AnnData(X=X, obs=pd.DataFrame(index=["px0", "px1"]), var=var).write_h5ad(path)


def test_write_annotations_to_var_matches_export_and_keeps_var_names(tmp_path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    _seed_two_features(db)
    h5ad = tmp_path / "s1.h5ad"
    # 300.0 has no feature (e.g. a targeted run's reference m/z).
    _write_var_h5ad(h5ad, [50.0, 123.4567, 300.0])
    before = ad.read_h5ad(h5ad)

    write_annotations_to_var(db, [h5ad, tmp_path / "missing.h5ad"])
    write_annotations_to_var(db, [h5ad])  # idempotent: replaces, never duplicates

    after = ad.read_h5ad(h5ad)
    assert list(after.var_names) == list(before.var_names)
    assert (after.X != before.X).nnz == 0
    assert list(after.var.columns) == ["mz", *VAR_ANNOTATION_COLUMNS]
    assert list(after.var["feature_id"]) == [9, 7, -1]
    row = after.var.loc["mz_123.4567"]
    assert row["compound_name"] == "Caffeine, decaf?"
    assert row["adduct"] == "[M+H]+"
    assert row["source"] == "ms2"
    assert row["score"] == pytest.approx(0.87)
    assert after.var.loc["mz_50.0000", "compound_name"] == ""
    assert np.isnan(after.var.loc["mz_50.0000", "score"])
    assert var_has_annotations(h5ad)

    # merged.h5ad is an `ad.concat(merge="same")` of the samples: the
    # columns must survive it.
    merged = ad.concat(
        {"a": after, "b": after}, label="sample", index_unique="-", merge="same"
    )
    assert list(merged.var["compound_name"]) == list(after.var["compound_name"])
