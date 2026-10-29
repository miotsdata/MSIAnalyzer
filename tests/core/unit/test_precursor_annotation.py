"""Annotation by precursor m/z alone (ADR 0065)."""

import sqlite3
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from msianalyzer.core.analysis_db import (
    init_analysis_db,
    load_feature_representative_annotations,
    register_sample,
)
from msianalyzer.core.annotation.precursor_annotation import (
    n_decimals,
    reference_mz,
    run_precursor_annotation,
)

PC_FORMULA = "C42H82NO8P"  # PC 34:1, [M+H]+ 760.5851
PC_MH = 760.58508


def test_decimals_as_stored():
    assert n_decimals(760.585) == 3  # 760.5850: the trailing zero is lost
    assert n_decimals(760.5851) == 4
    assert n_decimals(760.58512) == 5


def test_reference_mz_uses_the_library_value_only_with_4_decimals():
    assert reference_mz(760.5852, PC_FORMULA, "[M+H]+") == (760.5852, "library")
    mz, source = reference_mz(760.59, PC_FORMULA, "[M+H]+")
    assert source == "formula" and mz == pytest.approx(PC_MH, abs=1e-4)
    # An adduct we don't know: the library's own delta mass.
    mz, source = reference_mz(760.59, PC_FORMULA, "[M+X]+", 1, 1.007276, 1)
    assert source == "formula" and mz == pytest.approx(PC_MH, abs=1e-4)
    # No usable formula: the stored value anyway.
    assert reference_mz(760.59, None, "[M+H]+") == (760.59, "library")


def _spectrum(lib, name, formula, inchikey, precursor, polarity, adduct_id, ce=20.0):
    lib.add_spectra(
        compound_name=name, compound_formula=formula, inchikey=inchikey,
        spectra_info={
            "precursor_mz": precursor, "polarity": polarity, "collision_energy": ce,
            "mz": np.array([184.07]), "intensity": np.array([1.0]),
        },
        adduct_id=adduct_id,
    )


@pytest.fixture
def library(tmp_path):
    from libviz.core.library import Library

    lib = Library.create(tmp_path / "lib.db", name="lipids")
    mh = next(a["id"] for a in lib.get_adducts() if a["formula"] == "[M+H]+")
    # Rounded precursor, two collision energies: one candidate, formula m/z.
    _spectrum(lib, "PC 16:0/18:1", PC_FORMULA, "PCA-KEY", 760.59, "POSITIVE", mh, 20.0)
    _spectrum(lib, "PC 16:0/18:1", PC_FORMULA, "PCA-KEY", 760.59, "POSITIVE", mh, 40.0)
    # An isomer with an accurate stored precursor.
    _spectrum(lib, "PC 18:1/16:0", PC_FORMULA, "PCB-KEY", 760.5852, "POSITIVE", mh)
    # Wrong polarity, and too far away.
    _spectrum(lib, "Neg thing", "C6H12O6", "NEG-KEY", 760.5851, "NEGATIVE", mh)
    _spectrum(lib, "Far", "C6H12O6", "FAR-KEY", 760.6000, "POSITIVE", mh)
    return tmp_path / "lib.db"


@pytest.fixture
def analysis(tmp_path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    register_sample(db, "s0", str(tmp_path / "s0.db"), "+")
    with sqlite3.connect(db) as con:
        con.executemany(
            "INSERT INTO features (feature_id, mz, members_json) VALUES (?, ?, '{}')",
            [(1, PC_MH), (2, 500.0)],
        )
    return db


def test_every_candidate_ranked_by_ppm_and_the_feature_named_by_formula(analysis, library):
    n = run_precursor_annotation(
        analysis, SimpleNamespace(library_path=str(library), precursor_ppm=5.0)
    )
    assert n == 1
    with sqlite3.connect(analysis) as con:
        rows = con.execute(
            "SELECT compound_name, rank, ref_source, ppm_error FROM precursor_annotations "
            "WHERE feature_id = 1 ORDER BY rank"
        ).fetchall()
    # One row per compound + adduct; negative-mode and far spectra left out.
    assert [r[0] for r in rows] == ["PC 16:0/18:1", "PC 18:1/16:0"]
    assert [r[1] for r in rows] == [1, 2]
    assert rows[0][2] == "formula" and rows[1][2] == "library"
    assert abs(rows[0][3]) <= abs(rows[1][3])

    rep = load_feature_representative_annotations(analysis).set_index("feature_id")
    assert rep.loc[1, "source"] == "precursor"
    assert rep.loc[1, "compound_name"] == f"{PC_FORMULA} + [M+H]+ (2 candidates)"
    assert rep.loc[1, "n_precursor_candidates"] == 2
    assert 2 not in rep.index  # nothing matched 500.0


def test_an_ms2_match_keeps_naming_the_feature(analysis, library):
    run_precursor_annotation(
        analysis, SimpleNamespace(library_path=str(library), precursor_ppm=5.0)
    )
    with sqlite3.connect(analysis) as con:
        con.execute(
            "INSERT INTO ms2_annotations (sample_id, scan_id, feature_id, library_id, "
            "library_spectrum_id, compound_name, inchikey, score, dot_product_score, "
            "lib_coverage, emp_coverage, coverage_score, n_matched_peaks, n_lib_peaks, "
            "n_emp_peaks_raw, n_emp_peaks_filtered, rank_ms2, rank_feature) "
            "VALUES (1, 1, 1, 1, 1, 'MS2 hit', 'K', 0.9, 0.9, 1, 1, 1, 3, 3, 3, 3, 1, 1)"
        )
    rep = load_feature_representative_annotations(analysis).set_index("feature_id")
    assert rep.loc[1, "source"] == "ms2"
    assert rep.loc[1, "compound_name"] == "MS2 hit"
    # The precursor tier is still there, in its own columns.
    assert rep.loc[1, "precursor_name"].endswith("(2 candidates)")


def test_candidates_go_to_uns_with_var_names(analysis, library, tmp_path):
    import anndata as ad

    from msianalyzer.core.export import PRECURSOR_UNS_KEY, write_annotations_to_var

    run_precursor_annotation(
        analysis, SimpleNamespace(library_path=str(library), precursor_ppm=5.0)
    )
    var = pd.DataFrame({"mz": [PC_MH, 500.0]}, index=["mz_760.5851", "mz_500.0000"])
    path = tmp_path / "s0.h5ad"
    ad.AnnData(X=np.zeros((2, 2)), var=var).write_h5ad(path)

    write_annotations_to_var(analysis, [path])
    adata = ad.read_h5ad(path)
    table = adata.uns[PRECURSOR_UNS_KEY]
    assert list(table["feature"]) == ["mz_760.5851", "mz_760.5851"]
    assert list(table["rank"]) == [1, 2]
    assert adata.var.loc["mz_760.5851", "n_precursor_candidates"] == 2
    assert adata.var.loc["mz_500.0000", "n_precursor_candidates"] == 0


def test_candidate_names_best_first_per_kind(analysis, library):
    from msianalyzer.core.analysis_db import load_feature_candidate_names

    run_precursor_annotation(
        analysis, SimpleNamespace(library_path=str(library), precursor_ppm=5.0)
    )
    with sqlite3.connect(analysis) as con:
        con.executemany(
            "INSERT INTO ms2_annotations (sample_id, scan_id, feature_id, library_id, "
            "library_spectrum_id, compound_name, inchikey, score, dot_product_score, "
            "lib_coverage, emp_coverage, coverage_score, n_matched_peaks, n_lib_peaks, "
            "n_emp_peaks_raw, n_emp_peaks_filtered, rank_ms2, rank_feature) "
            "VALUES (1, ?, 2, 1, 1, ?, ?, 0.5, 0.5, 1, 1, 1, 3, 3, 3, 3, 1, ?)",
            # Two scans of B (ranks 2 and 4) and one of A (rank 1).
            [(1, "B", "KB", 2), (2, "B", "KB", 4), (3, "A", "KA", 1)],
        )
    names = load_feature_candidate_names(analysis, limit=1).set_index("feature_id")
    assert names.loc[2, "ms2_candidates"] == ["A"]
    assert names.loc[2, "precursor_candidates"] == []
    assert names.loc[1, "precursor_candidates"] == ["PC 16:0/18:1"]  # limit 1
    full = load_feature_candidate_names(analysis).set_index("feature_id")
    assert full.loc[2, "ms2_candidates"] == ["A", "B"]
    assert full.loc[1, "precursor_candidates"] == ["PC 16:0/18:1", "PC 18:1/16:0"]


def test_ppm_from_the_weighted_mz_and_ties_within_the_sample_spread(analysis, library, tmp_path):
    # Two samples 1 ppm apart, the stronger one higher: the m/z leans to it,
    # and the two PC isomers (0.15 ppm apart) can't be told apart.
    from msianalyzer.core.analysis_db import log_command
    from msianalyzer.core.spectra.average_spectra import save_aggregated_spectra

    s1 = register_sample(analysis, "s1", str(tmp_path / "s1.db"), "+")
    for sid, mz, intensity in ((1, 760.5847, 1.0), (s1, 760.5855, 3.0)):
        cid = log_command(analysis, "filter_spectra", {}, run_id="r", sample_id=sid)
        save_aggregated_spectra(
            np.array([mz]), np.array([intensity]),
            analysis_db_path=analysis, run_id="r", sample_id=sid, command_id=cid,
        )
    with sqlite3.connect(analysis) as con:
        con.execute("UPDATE features SET members_json = '{\"s0\": 0, \"s1\": 0}' WHERE feature_id = 1")

    run_precursor_annotation(
        analysis, SimpleNamespace(library_path=str(library), precursor_ppm=5.0)
    )
    with sqlite3.connect(analysis) as con:
        mz_w, spread = con.execute(
            "SELECT mz_weighted, mz_spread_ppm FROM features WHERE feature_id = 1"
        ).fetchone()
        rows = con.execute(
            "SELECT rank, tie_rank, ppm_error, ref_mz FROM precursor_annotations "
            "WHERE feature_id = 1 ORDER BY rank"
        ).fetchall()
    assert 760.5851 < mz_w < 760.5855
    assert spread == pytest.approx(0.53, abs=0.05)
    assert [(r[0], r[1]) for r in rows] == [(1, 1), (2, 1)]
    assert rows[0][2] == pytest.approx((mz_w - rows[0][3]) / rows[0][3] * 1e6)
