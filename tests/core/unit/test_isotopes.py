"""Isotope detection (ADR 54): per-sample linking, cross-sample vote, and the
stage that moves isotopes from `features` to `feature_isotopes`."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import anndata as ad
import numpy as np
import pandas as pd
import pytest
from scipy import sparse

from msianalyzer.core import analysis_db
from msianalyzer.core.config.config import IsotopesConfig
from msianalyzer.core.spectra.isotopes import (
    C13_DELTA,
    FeatureLink,
    candidate_indices,
    link_isotopes,
    max_m1_ratio,
    run_detect_isotopes,
    spatial_corr,
    vote_isotope_links,
)

MONO = 300.0
M1 = MONO + C13_DELTA
M2 = MONO + 2 * C13_DELTA


def _tissue(n_pixels: int = 200, n_on: int = 150, seed: int = 0) -> np.ndarray:
    """A random on-tissue pattern, zero on the last `n_pixels - n_on` pixels."""
    rng = np.random.default_rng(seed)
    t = np.zeros(n_pixels)
    t[:n_on] = rng.gamma(2.0, 1.0, n_on)
    return t


def _noisy(x: np.ndarray, rel: float = 0.05, seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return x * (1 + rel * rng.standard_normal(x.size)).clip(0)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def test_max_m1_ratio_grows_with_mass_and_charge():
    # at most one carbon per 14 Da: 1.1 x 1.07 % per carbon x (300/14) + 0.02
    assert max_m1_ratio(300.0, 1) == pytest.approx(1.1 * 0.0107 * 300 / 14 + 0.02)
    assert max_m1_ratio(600.0, 1) > max_m1_ratio(300.0, 1)
    assert max_m1_ratio(300.0, 2) == pytest.approx(max_m1_ratio(600.0, 1))


def test_candidate_indices_keeps_isotope_partners_only():
    mz = np.array([MONO, M1, M2, 450.0])
    cfg = IsotopesConfig()
    assert set(candidate_indices(mz, cfg).tolist()) == {0, 1, 2}


def test_candidate_indices_respects_ppm_and_max_charge():
    mz = np.array([MONO, MONO + C13_DELTA / 2, 450.0])
    assert candidate_indices(mz, IsotopesConfig(max_charge=1)).size == 0
    assert set(candidate_indices(mz, IsotopesConfig(max_charge=2)).tolist()) == {0, 1}
    # 20 ppm off the expected M+1: outside the default 5 ppm
    far = np.array([MONO, M1 * (1 + 20e-6)])
    assert candidate_indices(far, IsotopesConfig()).size == 0


def test_spatial_corr_high_for_co_localized_images():
    mono = _tissue()
    assert spatial_corr(mono, _noisy(0.1 * mono)) > 0.95


def test_spatial_corr_ignores_shared_off_tissue_zeros():
    # Two unrelated on-tissue ions: correlated over all pixels only because
    # both are zero off tissue. Over the mono's non-zero pixels they aren't.
    mono = _tissue(seed=0)
    other = _tissue(seed=7)
    assert np.corrcoef(mono, other)[0, 1] > 0.3
    assert spatial_corr(mono, other) < 0.3


def test_spatial_corr_degenerate_inputs_return_minus_one():
    assert spatial_corr(np.array([0.0, 0.0, 1.0]), np.array([1.0, 2.0, 3.0])) == -1.0
    assert spatial_corr(np.array([1.0, 1.0, 1.0, 1.0]), np.arange(4.0)) == -1.0


# ---------------------------------------------------------------------------
# per-sample linking
# ---------------------------------------------------------------------------


def _images(*columns: np.ndarray) -> np.ndarray:
    return np.column_stack(columns)


def test_link_isotopes_links_m1_and_m2_chain():
    t = _tissue()
    mz = np.array([MONO, M1, M2, 450.0])
    images = _images(1000 * t, _noisy(200 * t, seed=2), _noisy(30 * t, seed=3), 500 * _tissue(seed=9))
    links = link_isotopes(mz, images, IsotopesConfig())
    assert {(l.mono, l.isotope, l.k, l.z) for l in links} == {(0, 1, 1, 1), (0, 2, 2, 1)}
    m1 = next(l for l in links if l.k == 1)
    assert m1.ratio == pytest.approx(0.2, rel=0.05)
    assert m1.corr > 0.95


def test_link_isotopes_rejects_isotope_more_intense_than_mono():
    t = _tissue()
    links = link_isotopes(np.array([MONO, M1]), _images(100 * t, 300 * t), IsotopesConfig())
    # visited from the most intense: 300.0 has no partner below it at +1.003
    assert links == []


def test_max_m1_ratio_rejects_implausible_lipid_ratio_but_keeps_a_real_one():
    # Seen on real data: 789.57 -> 790.58 at 0.94 would mean ~88 carbons.
    assert max_m1_ratio(789.57, 1) < 0.94
    # A real C44 lipid at the same m/z: 44 x 1.07 % ~ 0.47.
    assert max_m1_ratio(789.57, 1) > 44 * 0.0107


def test_link_isotopes_rejects_m1_above_carbon_bound():
    t = _tissue()
    ratio = max_m1_ratio(MONO, 1) + 0.1
    links = link_isotopes(np.array([MONO, M1]), _images(1000 * t, ratio * 1000 * t), IsotopesConfig())
    assert links == []


def test_link_isotopes_rejects_not_co_localized():
    links = link_isotopes(
        np.array([MONO, M1]),
        _images(1000 * _tissue(seed=0), 100 * _tissue(seed=7)),
        IsotopesConfig(),
    )
    assert links == []


def test_link_isotopes_chain_stops_at_first_gap():
    t = _tissue()
    mz = np.array([MONO, M1, MONO + 3 * C13_DELTA])  # no M+2
    links = link_isotopes(mz, _images(1000 * t, 200 * t, 10 * t), IsotopesConfig())
    assert [(l.isotope, l.k) for l in links] == [(1, 1)]


def test_link_isotopes_prefers_longest_chain_charge():
    t = _tissue()
    half = C13_DELTA / 2
    mz = np.array([MONO, MONO + half, MONO + 2 * half, MONO + 3 * half])
    images = _images(1000 * t, 300 * t, 200 * t, 50 * t)
    links = link_isotopes(mz, images, IsotopesConfig(max_charge=2))
    assert {(l.isotope, l.k, l.z) for l in links} == {(1, 1, 2), (2, 2, 2), (3, 3, 2)}


def test_link_isotopes_max_isotopes_caps_the_chain():
    t = _tissue()
    mz = np.array([MONO, M1, M2])
    links = link_isotopes(mz, _images(1000 * t, 200 * t, 30 * t), IsotopesConfig(max_isotopes=1))
    assert [(l.isotope, l.k) for l in links] == [(1, 1)]


# ---------------------------------------------------------------------------
# cross-sample vote
# ---------------------------------------------------------------------------


def _present(rows: dict[int, list[bool]], samples=("s1", "s2", "s3")) -> pd.DataFrame:
    return pd.DataFrame.from_dict(rows, orient="index", columns=list(samples))


def _link(mono, iso, k=1, z=1, ratio=0.2, corr=0.9):
    return FeatureLink(mono_id=mono, isotope_id=iso, k=k, z=z, ratio=ratio, corr=corr)


def test_vote_accepts_majority_of_samples_where_both_present():
    present = _present({1: [True, True, True], 2: [True, True, True]})
    by_sample = {"s1": [_link(1, 2, ratio=0.2, corr=0.9)], "s2": [_link(1, 2, ratio=0.3, corr=0.8)], "s3": []}
    [acc] = vote_isotope_links(by_sample, present, protected=set())
    assert (acc.mono_feature_id, acc.feature_id, acc.isotope_index, acc.charge) == (1, 2, 1, 1)
    assert (acc.n_samples_linked, acc.n_samples_both) == (2, 3)
    assert acc.intensity_ratio == pytest.approx(0.25)
    assert acc.spatial_corr == pytest.approx(0.85)


def test_vote_rejects_minority_and_ignores_samples_missing_either_feature():
    # Both present in s1..s3, linked only in s1 -> 1/3, rejected.
    present = _present({1: [True, True, True], 2: [True, True, True]})
    assert vote_isotope_links({"s1": [_link(1, 2)], "s2": [], "s3": []}, present, set()) == []
    # Isotope present only in s1 -> 1/1, accepted.
    present = _present({1: [True, True, True], 2: [True, False, False]})
    assert len(vote_isotope_links({"s1": [_link(1, 2)], "s2": [], "s3": []}, present, set())) == 1


def test_vote_exactly_half_is_not_a_majority():
    present = _present({1: [True, True], 2: [True, True]}, samples=("s1", "s2"))
    assert vote_isotope_links({"s1": [_link(1, 2)], "s2": []}, present, set()) == []


def test_vote_never_removes_protected_feature():
    present = _present({1: [True, True, True], 2: [True, True, True]})
    by_sample = {s: [_link(1, 2)] for s in ("s1", "s2", "s3")}
    assert vote_isotope_links(by_sample, present, protected={2}) == []


def test_vote_drops_m2_when_m1_rejected():
    present = _present({1: [True] * 3, 2: [True] * 3, 3: [True] * 3})
    by_sample = {
        "s1": [_link(1, 2, k=1), _link(1, 3, k=2)],
        "s2": [_link(1, 3, k=2)],
        "s3": [_link(1, 3, k=2)],
    }
    assert vote_isotope_links(by_sample, present, set()) == []


def test_vote_isotope_claimed_by_two_monos_keeps_most_voted():
    present = _present({1: [True] * 3, 5: [True] * 3, 2: [True] * 3})
    by_sample = {
        "s1": [_link(1, 2)],
        "s2": [_link(1, 2)],
        "s3": [_link(5, 2)],
    }
    [acc] = vote_isotope_links(by_sample, present, set())
    assert acc.mono_feature_id == 1


def test_vote_accepted_isotope_cannot_be_a_mono():
    # 2 is M+1 of 1 (all samples) and 3 was linked as M+1 of 2 in all samples.
    present = _present({1: [True] * 3, 2: [True] * 3, 3: [True] * 3})
    by_sample = {s: [_link(1, 2), _link(2, 3)] for s in ("s1", "s2", "s3")}
    accepted = vote_isotope_links(by_sample, present, set())
    assert [(a.mono_feature_id, a.feature_id) for a in accepted] == [(1, 2)]


# ---------------------------------------------------------------------------
# stage: links from the .h5ad matrices, isotopes moved out of features and
# dropped from every .h5ad (ADR 55)
# ---------------------------------------------------------------------------

FEATURE_MZS = [MONO, round(M1, 4), 450.0]


def _h5ad_with_isotope(path: Path, seed: int) -> Path:
    """A mono at 300, its M+1, and an unrelated ion at 450."""
    t = _tissue(n_pixels=60, n_on=45, seed=seed)
    other = _tissue(n_pixels=60, n_on=45, seed=seed + 100)
    X = np.column_stack([1000 * t, 150 * t, 800 * other]).astype(np.float32)
    var = pd.DataFrame({"mz": FEATURE_MZS}, index=[f"mz_{m:.4f}" for m in FEATURE_MZS])
    ad.AnnData(X=sparse.csr_matrix(X), var=var).write_h5ad(path)
    return path


def _analysis_db(tmp_path: Path, samples: list[str]) -> Path:
    db = tmp_path / "analysis.db"
    analysis_db.init_analysis_db(db).close()
    analysis_db.save_features(db, pd.DataFrame({s: [0, 1, 2] for s in samples}, index=FEATURE_MZS))
    return db


def _feature_mzs(db: Path) -> list[float]:
    with sqlite3.connect(db) as con:
        return [r[0] for r in con.execute("SELECT mz FROM features ORDER BY mz")]


def test_run_detect_isotopes_moves_isotope_out_of_features_and_h5ad(tmp_path):
    samples = ["s1", "s2"]
    h5ads = {s: _h5ad_with_isotope(tmp_path / f"{s}.h5ad", seed=i) for i, s in enumerate(samples)}
    before = {s: ad.read_h5ad(p) for s, p in h5ads.items()}
    db = _analysis_db(tmp_path, samples)

    summary = run_detect_isotopes(db, h5ads, IsotopesConfig())

    assert (summary.n_features_before, summary.n_isotopes) == (3, 1)
    assert _feature_mzs(db) == [MONO, 450.0]
    with sqlite3.connect(db) as con:
        row = con.execute(
            "SELECT i.mz, f.mz, i.isotope_index, i.charge, i.n_samples_linked, "
            "i.n_samples_both, i.intensity_ratio, i.spatial_corr, i.members_json "
            "FROM feature_isotopes i JOIN features f ON f.feature_id = i.mono_feature_id"
        ).fetchone()
    mz, mono_mz, k, z, n_linked, n_both, ratio, corr, members = row
    assert (mz, mono_mz, k, z, n_linked, n_both) == (round(M1, 4), MONO, 1, 1, 2, 2)
    assert ratio == pytest.approx(0.15, rel=0.05)
    assert corr > 0.95
    assert json.loads(members) == {"s1": 1, "s2": 1}

    for s, p in h5ads.items():
        after = ad.read_h5ad(p)
        assert after.var["mz"].tolist() == [MONO, 450.0]
        kept = before[s][:, before[s].var["mz"] != round(M1, 4)]
        np.testing.assert_array_equal(after.X.toarray(), kept.X.toarray())


def test_run_detect_isotopes_keeps_target_list_features(tmp_path):
    h5ads = {"s1": _h5ad_with_isotope(tmp_path / "s1.h5ad", seed=0)}
    db = _analysis_db(tmp_path, ["s1"])
    with sqlite3.connect(db) as con:
        m1_id = con.execute("SELECT feature_id FROM features WHERE mz = ?", (round(M1, 4),)).fetchone()[0]
        con.execute(
            "INSERT INTO target_list_compounds (id, name, formula, neutral_mass, "
            "source_file, row_number) VALUES (1, 'x', 'C10H10', 130.0, 't.csv', 1)"
        )
        con.execute(
            "INSERT INTO target_list_matches (target_compound_id, feature_id, "
            "adduct_label, adduct_charge, adduct_delta_mass, multiplication_factor, "
            "theoretical_mz, ppm_diff, match_type) "
            "VALUES (1, ?, '[M-H]-', -1, -1.007, 1, ?, 0, 'existing')",
            (m1_id, round(M1, 4)),
        )

    summary = run_detect_isotopes(db, h5ads, IsotopesConfig())

    assert summary.n_isotopes == 0
    assert ad.read_h5ad(h5ads["s1"]).n_vars == 3


def test_run_detect_isotopes_never_drops_the_reference_mz_column(tmp_path):
    # Normalization by a reference m/z needs that column even when the
    # feature at that m/z turns out to be an isotope.
    h5ads = {"s1": _h5ad_with_isotope(tmp_path / "s1.h5ad", seed=0)}
    db = _analysis_db(tmp_path, ["s1"])

    summary = run_detect_isotopes(db, h5ads, IsotopesConfig(), keep_mzs={round(M1, 4)})

    assert summary.n_isotopes == 1  # still out of the feature list
    assert ad.read_h5ad(h5ads["s1"]).var["mz"].tolist() == FEATURE_MZS


def test_run_detect_isotopes_without_isotopes_leaves_h5ad_untouched(tmp_path):
    p = tmp_path / "s1.h5ad"
    var = pd.DataFrame({"mz": [300.0, 450.0]}, index=["mz_300.0000", "mz_450.0000"])
    ad.AnnData(X=sparse.csr_matrix(np.ones((5, 2), dtype=np.float32)), var=var).write_h5ad(p)
    mtime = p.stat().st_mtime_ns
    db = tmp_path / "analysis.db"
    analysis_db.init_analysis_db(db).close()
    analysis_db.save_features(db, pd.DataFrame({"s1": [0, 1]}, index=[300.0, 450.0]))

    summary = run_detect_isotopes(db, {"s1": p}, IsotopesConfig())

    assert (summary.n_features_before, summary.n_candidates, summary.n_isotopes) == (2, 0, 0)
    assert p.stat().st_mtime_ns == mtime
