"""Tests for :mod:`msianalyzer.core.annotation.target_list`."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from msianalyzer.core.analysis_db import (
    init_analysis_db,
    load_feature_ids_and_mzs,
    log_command,
)
from msianalyzer.core.annotation.target_list import (
    ADDUCTS,
    ELECTRON_MASS,
    NEGATIVE_ADDUCTS,
    POSITIVE_ADDUCTS,
    PROTON_MASS,
    Adduct,
    InvalidFormulaError,
    TargetCompound,
    TargetListError,
    adduct_by_label,
    adduct_mz,
    adducts_for_polarity,
    match_target_list,
    normalize_target_list_paths,
    parse_target_list_file,
    parse_target_list_files,
    run_target_list_matching,
)


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------


def _write_csv(path: Path, rows: list[dict]) -> Path:
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def test_parse_target_list_file_raises_invalid_formula_error_with_file_row_and_compound_name(
    tmp_path: Path,
):
    path = _write_csv(
        tmp_path / "targets.csv",
        [
            {"name": "Glucose", "formula": "C6H12O6", "inchikey": "X"},
            {"name": "Bogus", "formula": "NotAFormula123$$", "inchikey": ""},
        ],
    )
    with pytest.raises(InvalidFormulaError) as exc_info:
        parse_target_list_file(path)
    msg = str(exc_info.value)
    assert str(path) in msg
    assert "row 2" in msg
    assert "Bogus" in msg
    assert "NotAFormula123$$" in msg


def test_parse_target_list_file_raises_on_missing_required_column(tmp_path: Path):
    path = _write_csv(
        tmp_path / "targets.csv",
        [{"name": "Glucose", "formula": "C6H12O6"}],  # no inchikey column
    )
    with pytest.raises(TargetListError, match="inchikey"):
        parse_target_list_file(path)


def test_parse_target_list_file_raises_on_empty_file(tmp_path: Path):
    path = _write_csv(tmp_path / "targets.csv", [])
    # pandas needs at least header columns to exist for the empty-file case
    path.write_text("name,formula,inchikey\n")
    with pytest.raises(TargetListError, match="no data rows"):
        parse_target_list_file(path)


def test_parse_target_list_file_allows_blank_inchikey(tmp_path: Path):
    path = _write_csv(
        tmp_path / "targets.csv",
        [{"name": "Glucose", "formula": "C6H12O6", "inchikey": ""}],
    )
    compounds = parse_target_list_file(path)
    assert len(compounds) == 1
    assert compounds[0].inchikey is None
    assert compounds[0].neutral_mass == pytest.approx(180.0634, abs=1e-3)
    assert compounds[0].row_number == 1
    assert compounds[0].source_file == str(path)


def test_parse_target_list_file_column_names_are_case_insensitive(tmp_path: Path):
    path = tmp_path / "targets.csv"
    path.write_text("Name,Formula,InChIKey\nGlucose,C6H12O6,ABC\n")
    compounds = parse_target_list_file(path)
    assert compounds[0].name == "Glucose"
    assert compounds[0].inchikey == "ABC"


def test_normalize_target_list_paths_none_and_empty_disable():
    assert normalize_target_list_paths(None) == []
    assert normalize_target_list_paths("") == []
    assert normalize_target_list_paths([]) == []


def test_normalize_target_list_paths_bare_string_becomes_one_element_list():
    assert normalize_target_list_paths("targets.csv") == ["targets.csv"]


def test_normalize_target_list_paths_deduplicates_preserving_order():
    assert normalize_target_list_paths(["a.csv", "b.csv", "a.csv"]) == [
        "a.csv", "b.csv",
    ]


def test_parse_target_list_files_concatenates_in_order(tmp_path: Path):
    a = _write_csv(
        tmp_path / "a.csv", [{"name": "A", "formula": "C6H12O6", "inchikey": ""}]
    )
    b = _write_csv(
        tmp_path / "b.csv", [{"name": "B", "formula": "H2O", "inchikey": ""}]
    )
    compounds = parse_target_list_files([str(a), str(b)])
    assert [c.name for c in compounds] == ["A", "B"]


# ---------------------------------------------------------------------------
# adducts
# ---------------------------------------------------------------------------


def test_adducts_for_polarity_returns_only_that_polaritys_adducts():
    positive = adducts_for_polarity("positive")
    negative = adducts_for_polarity("negative")
    assert positive == POSITIVE_ADDUCTS
    assert negative == NEGATIVE_ADDUCTS
    assert all(a.polarity == "positive" for a in positive)
    assert all(a.polarity == "negative" for a in negative)
    assert set(positive) | set(negative) == set(ADDUCTS)


def test_adduct_by_label_unknown_label_raises():
    with pytest.raises(TargetListError, match="unknown adduct"):
        adduct_by_label("[M+bogus]+")


def test_adduct_mz_single_charge_adds_delta_mass():
    adduct = Adduct("[M+H]+", "positive", 1, PROTON_MASS)
    assert adduct_mz(100.0, adduct) == pytest.approx(100.0 + PROTON_MASS)


def test_adduct_mz_dimer_multiplication_factor_doubles_neutral_mass():
    monomer = Adduct("[M+H]+", "positive", 1, PROTON_MASS, multiplication_factor=1)
    dimer = Adduct("[2M+H]+", "positive", 1, PROTON_MASS, multiplication_factor=2)
    neutral = 100.0
    assert adduct_mz(neutral, dimer) == pytest.approx(
        2 * neutral + PROTON_MASS
    )
    assert adduct_mz(neutral, dimer) != adduct_mz(neutral, monomer)


def test_adduct_mz_doubly_charged_divides_by_two():
    adduct = Adduct("[M+2H]2+", "positive", 2, 2 * PROTON_MASS)
    assert adduct_mz(100.0, adduct) == pytest.approx((100.0 + 2 * PROTON_MASS) / 2)


@pytest.mark.parametrize(
    "label, expected_delta",
    [
        ("[M+H]+", PROTON_MASS),
        ("[M-H]-", -PROTON_MASS),
        ("[M+Na]+", 22.989770 - ELECTRON_MASS),
        ("[M+K]+", 38.963707 - ELECTRON_MASS),
        ("[M+NH4]+", 18.033823),
        ("[M+Cl]-", 34.968853 + ELECTRON_MASS),
        ("[M-2H]2-", -2 * PROTON_MASS),
        ("[M+2H]2+", 2 * PROTON_MASS),
    ],
)
def test_adduct_delta_mass_matches_published_values(label, expected_delta):
    """Pins each standard adduct's delta_mass to a literature value (4
    decimals) — flagged in ADR 0026 as needing independent verification
    before this feature is trusted for real identification decisions.
    """
    assert adduct_by_label(label).delta_mass == pytest.approx(expected_delta, abs=5e-4)


# ---------------------------------------------------------------------------
# matching + injection (pure logic)
# ---------------------------------------------------------------------------


_H_ADDUCT = adduct_by_label("[M+H]+")


def _features_df(rows: list[tuple[int, float]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["feature_id", "mz"])


def test_match_target_list_matches_existing_feature_within_match_ppm():
    glucose = TargetCompound(
        name="Glucose", formula="C6H12O6", inchikey=None,
        neutral_mass=180.0634, source_file="t.csv", row_number=1,
    )
    theoretical_mz = adduct_mz(glucose.neutral_mass, _H_ADDUCT)
    existing = _features_df([(1, theoretical_mz)])  # exact match

    result = match_target_list(
        [glucose], [_H_ADDUCT], existing, match_ppm=10.0, sample_names=["s1"],
    )

    assert len(result.matches) == 1
    m = result.matches[0]
    assert m.match_type == "existing"
    assert m.feature_id == 1
    assert not result.injected_features


def test_match_target_list_does_not_match_feature_just_outside_match_ppm():
    glucose = TargetCompound(
        name="Glucose", formula="C6H12O6", inchikey=None,
        neutral_mass=180.0634, source_file="t.csv", row_number=1,
    )
    theoretical_mz = adduct_mz(glucose.neutral_mass, _H_ADDUCT)
    far_mz = theoretical_mz * (1 + 50e-6)  # 50 ppm away
    existing = _features_df([(1, far_mz)])

    result = match_target_list(
        [glucose], [_H_ADDUCT], existing, match_ppm=10.0, sample_names=["s1"],
    )

    assert not result.matches or result.matches[0].match_type == "injected"
    assert result.matches[0].match_type == "injected"
    assert len(result.injected_features) == 1


def test_match_target_list_two_adducts_collapsing_onto_one_feature_produce_two_match_rows_same_feature_id():
    compound_a = TargetCompound(
        name="A", formula="C6H12O6", inchikey=None,
        neutral_mass=180.0634, source_file="t.csv", row_number=1,
    )
    compound_b = TargetCompound(
        name="B", formula="C6H12O6", inchikey=None,
        neutral_mass=180.0634, source_file="t.csv", row_number=2,
    )
    theoretical_mz = adduct_mz(compound_a.neutral_mass, _H_ADDUCT)
    existing = _features_df([(1, theoretical_mz)])

    result = match_target_list(
        [compound_a, compound_b], [_H_ADDUCT], existing,
        match_ppm=10.0, sample_names=["s1"],
    )

    assert len(result.matches) == 2
    assert all(m.feature_id == 1 for m in result.matches)
    assert all(m.match_type == "existing" for m in result.matches)


def test_match_target_list_injects_new_feature_when_no_existing_feature_within_tolerance():
    glucose = TargetCompound(
        name="Glucose", formula="C6H12O6", inchikey=None,
        neutral_mass=180.0634, source_file="t.csv", row_number=1,
    )
    existing = _features_df([])  # no features at all

    result = match_target_list(
        [glucose], [_H_ADDUCT], existing, match_ppm=10.0, sample_names=["s1", "s2"],
    )

    assert len(result.matches) == 1
    assert result.matches[0].match_type == "injected"
    assert result.matches[0].feature_id is None  # resolved later by the DB layer
    assert len(result.injected_features) == 1


def test_match_target_list_two_unmatched_theoretical_mz_within_match_ppm_of_each_other_share_one_injected_feature():

    # Two different formulas whose [M+H]+ land within 1 ppm of each other.
    compound_a = TargetCompound(
        name="A", formula="C6H12O6", inchikey=None,
        neutral_mass=180.0634, source_file="t.csv", row_number=1,
    )
    compound_b = TargetCompound(
        name="B", formula="C6H12O6", inchikey=None,
        neutral_mass=180.06345, source_file="t.csv", row_number=2,
    )
    existing = _features_df([])

    result = match_target_list(
        [compound_a, compound_b], [_H_ADDUCT], existing,
        match_ppm=10.0, sample_names=["s1"],
    )

    assert len(result.injected_features) == 1
    assert len(result.matches) == 2
    assert result.matches[0].feature_mz == result.matches[1].feature_mz


def test_match_target_list_far_apart_unmatched_mz_produce_separate_injected_features():
    compound_a = TargetCompound(
        name="A", formula="C6H12O6", inchikey=None,
        neutral_mass=180.0634, source_file="t.csv", row_number=1,
    )
    compound_b = TargetCompound(
        name="B", formula="C2H4O2", inchikey=None,  # acetic acid, far mass
        neutral_mass=60.0211, source_file="t.csv", row_number=2,
    )
    existing = _features_df([])

    result = match_target_list(
        [compound_a, compound_b], [_H_ADDUCT], existing,
        match_ppm=10.0, sample_names=["s1"],
    )

    assert len(result.injected_features) == 2


def test_match_target_list_injected_feature_members_json_is_all_null_for_every_sample():
    glucose = TargetCompound(
        name="Glucose", formula="C6H12O6", inchikey=None,
        neutral_mass=180.0634, source_file="t.csv", row_number=1,
    )
    existing = _features_df([])

    result = match_target_list(
        [glucose], [_H_ADDUCT], existing, match_ppm=10.0, sample_names=["s1", "s2"],
    )

    members = json.loads(result.injected_features[0].members_json)
    assert members == {"s1": None, "s2": None}


def test_match_target_list_empty_compounds_returns_no_matches():
    existing = _features_df([(1, 181.07)])
    result = match_target_list([], [_H_ADDUCT], existing, match_ppm=10.0, sample_names=[])
    assert result.matches == []
    assert result.injected_features == []


def test_match_target_list_no_existing_features_at_all_still_works():
    glucose = TargetCompound(
        name="Glucose", formula="C6H12O6", inchikey=None,
        neutral_mass=180.0634, source_file="t.csv", row_number=1,
    )
    existing = pd.DataFrame(columns=["feature_id", "mz"])  # truly empty df

    result = match_target_list(
        [glucose], [_H_ADDUCT], existing, match_ppm=10.0, sample_names=["s1"],
    )
    assert len(result.injected_features) == 1


# ---------------------------------------------------------------------------
# run_target_list_matching (DB-touching orchestration)
# ---------------------------------------------------------------------------


class _FakeTargetListConfig:
    def __init__(self, paths, polarity="positive", adducts=None, match_ppm=10.0):
        self.paths = paths
        self.polarity = polarity
        self.adducts = adducts
        self.match_ppm = match_ppm


def test_run_target_list_matching_persists_matches_and_injected_features(
    tmp_path: Path,
):
    targets = _write_csv(
        tmp_path / "targets.csv",
        [{"name": "Glucose", "formula": "C6H12O6", "inchikey": "WQZGKKKJIJFFOK-GASJEMHNSA-N"}],
    )
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    command_id = log_command(db, "match_target_list", {}, run_id="r1")
    config = _FakeTargetListConfig(paths=str(targets), adducts=["[M+H]+"])

    summary = run_target_list_matching(
        db, config, command_id=command_id, sample_names=["s1"],
    )

    assert summary.n_compounds == 1
    assert summary.n_adducts == 1
    assert summary.n_matches == 1
    assert summary.n_matched_existing == 0
    assert summary.n_injected_features == 1
    assert summary.n_distinct_features == 1

    with sqlite3.connect(db) as con:
        n_features = con.execute(
            "SELECT COUNT(*) FROM features WHERE origin = 'injected'"
        ).fetchone()[0]
        n_matches = con.execute("SELECT COUNT(*) FROM target_list_matches").fetchone()[0]
        feature_id = con.execute(
            "SELECT feature_id FROM target_list_matches"
        ).fetchone()[0]
    assert n_features == 1
    assert n_matches == 1
    assert feature_id is not None


def test_run_target_list_matching_matches_an_existing_feature_without_injecting(
    tmp_path: Path,
):

    theoretical_mz = adduct_mz(180.0634, _H_ADDUCT)
    targets = _write_csv(
        tmp_path / "targets.csv",
        [{"name": "Glucose", "formula": "C6H12O6", "inchikey": ""}],
    )
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    with sqlite3.connect(db) as con:
        con.execute(
            "INSERT INTO features (feature_id, mz, members_json) VALUES (1, ?, '{}')",
            (theoretical_mz,),
        )
        con.commit()
    command_id = log_command(db, "match_target_list", {}, run_id="r1")
    config = _FakeTargetListConfig(paths=str(targets), adducts=["[M+H]+"])

    summary = run_target_list_matching(
        db, config, command_id=command_id, sample_names=["s1"],
    )

    assert summary.n_matched_existing == 1
    assert summary.n_injected_features == 0

    features = load_feature_ids_and_mzs(db)
    assert len(features) == 1  # no new feature added