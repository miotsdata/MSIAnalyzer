"""Tests for :mod:`msianalyzer.core.annotation.target_list`."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd
import pytest
from pyteomics import mass as pt_mass

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


def test_parse_target_list_file_needs_a_formula_or_mz_column(tmp_path: Path):
    path = tmp_path / "targets.csv"
    path.write_text("name,inchikey\nGlucose,ABC\n")
    with pytest.raises(TargetListError, match="'formula' and/or 'mz'"):
        parse_target_list_file(path)


def test_parse_target_list_file_inchikey_column_is_optional(tmp_path: Path):
    path = tmp_path / "targets.csv"
    path.write_text("name,formula\nGlucose,C6H12O6\n")
    assert parse_target_list_file(path)[0].inchikey is None


def test_parse_target_list_file_mz_only_formula_only_and_both(tmp_path: Path):
    path = tmp_path / "targets.csv"
    path.write_text(
        "name,formula,m/z\n"
        "ByMz,,181.0707\n"
        "ByFormula,C6H12O6,\n"
        "Both,C6H12O6,203.0526\n"
    )
    by_mz, by_formula, both = parse_target_list_file(path)
    assert (by_mz.formula, by_mz.target_mz, by_mz.neutral_mass) == ("", 181.0707, 0.0)
    assert by_formula.target_mz is None
    assert by_formula.neutral_mass == pytest.approx(180.0634, abs=1e-3)
    # Both: the m/z is kept as the search value, the formula for display.
    assert both.target_mz == 203.0526 and both.formula == "C6H12O6"


@pytest.mark.parametrize("row, message", [
    ("Empty,,", "needs a formula or an m/z"),
    ("Bad,,abc", "not a positive number"),
    ("Negative,,-5", "not a positive number"),
])
def test_parse_target_list_file_rejects_bad_rows(tmp_path: Path, row, message):
    path = tmp_path / "targets.csv"
    path.write_text("name,formula,mz\n" + row + "\n")
    with pytest.raises(TargetListError, match=message) as exc_info:
        parse_target_list_file(path)
    assert "row 1" in str(exc_info.value)


def test_match_target_list_given_mz_wins_over_formula_and_adducts():
    # Formula would give glucose [M+H]+ / [M+Na]+, but the given m/z wins.
    compound = TargetCompound(
        name="Both", formula="C6H12O6", inchikey=None, neutral_mass=180.0634,
        source_file="t.csv", row_number=1, target_mz=500.0,
    )
    features = _features_df([(1, 181.0707), (2, 500.0001)])
    result = match_target_list(
        [compound], POSITIVE_ADDUCTS, features, match_ppm=10.0, sample_names=["s1"]
    )
    assert [(m.feature_id, m.adduct.label) for m in result.matches] == [(2, "m/z")]
    assert result.matches[0].theoretical_mz == 500.0


def test_match_target_list_unmatched_given_mz_is_injected_at_that_mz():
    compound = TargetCompound(
        name="ByMz", formula="", inchikey=None, neutral_mass=0.0,
        source_file="t.csv", row_number=1, target_mz=321.1234,
    )
    result = match_target_list(
        [compound], POSITIVE_ADDUCTS, _features_df([]), match_ppm=10.0, sample_names=["s1"]
    )
    assert [f.mz for f in result.injected_features] == [321.1234]


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

# ---------------------------------------------------------------------------
# After the run (ADR 60)
# ---------------------------------------------------------------------------


def _analysis_with_features(tmp_path: Path, mzs: list[float]) -> Path:
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    with sqlite3.connect(db) as con:
        for i, mz in enumerate(mzs, start=1):
            con.execute(
                "INSERT INTO features (feature_id, mz, members_json) VALUES (?, ?, '{}')",
                (i, mz),
            )
        con.commit()
    return db


def test_post_run_preview_lists_matches_and_undetected_without_writing(tmp_path: Path):
    from msianalyzer.core.annotation.target_list import preview_post_run_matches

    glucose_mz = adduct_mz(pt_mass.calculate_mass(formula="C6H12O6"), _H_ADDUCT)
    db = _analysis_with_features(tmp_path, [glucose_mz, glucose_mz + 0.00001])
    targets = _write_csv(tmp_path / "t.csv", [
        {"name": "Glucose", "formula": "C6H12O6", "inchikey": ""},
        {"name": "Caffeine", "formula": "C8H10N4O2", "inchikey": ""},
    ])

    rows = preview_post_run_matches(db, [str(targets)], "positive", ["[M+H]+"], 10.0)

    caffeine = [r for r in rows if r["name"] == "Caffeine"]
    assert len(caffeine) == 1 and caffeine[0]["detected"] is False
    glucose = [r for r in rows if r["name"] == "Glucose"]
    # Two features within ppm: the nearest one, suggested (it has no name).
    assert [(r["feature_id"], r["use"]) for r in glucose] == [(1, True)]
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM target_list_matches").fetchone()[0] == 0


def test_post_run_preview_suggests_one_name_per_feature_and_respects_existing_names(
    tmp_path: Path,
):
    from msianalyzer.core.annotation.target_list import preview_post_run_matches

    mz = adduct_mz(pt_mass.calculate_mass(formula="C6H12O6"), _H_ADDUCT)
    db = _analysis_with_features(tmp_path, [mz])
    targets = _write_csv(tmp_path / "t.csv", [
        {"name": "Glucose", "formula": "C6H12O6", "inchikey": ""},
        {"name": "Fructose", "formula": "C6H12O6", "inchikey": ""},
    ])
    rows = preview_post_run_matches(db, [str(targets)], "positive", ["[M+H]+"], 10.0)
    assert sum(r["use"] for r in rows) == 1  # same feature: one suggestion

    # Once named, a new match is not suggested over it.
    from msianalyzer.core.annotation.target_list import apply_post_run_matches

    apply_post_run_matches(db, "r1", [r for r in rows if r["name"] == "Glucose"], {})
    rows = preview_post_run_matches(db, [str(targets)], "positive", ["[M+H]+"], 10.0)
    assert not any(r["use"] for r in rows)
    assert {r["current_name"] for r in rows} == {"Glucose"}


def test_post_run_apply_names_the_feature_and_remove_undoes_only_it(tmp_path: Path):
    from msianalyzer.core.analysis_db import (
        count_command_target_list_matches,
        delete_command_target_list_rows,
        load_feature_list,
    )
    from msianalyzer.core.annotation.target_list import (
        POST_RUN_COMMAND,
        apply_post_run_matches,
        preview_post_run_matches,
    )

    mz = adduct_mz(pt_mass.calculate_mass(formula="C6H12O6"), _H_ADDUCT)
    db = _analysis_with_features(tmp_path, [mz, 500.0])
    # A pipeline-time target match on feature 2 must survive the undo.
    with sqlite3.connect(db) as con:
        con.execute(
            "INSERT INTO target_list_compounds (id, name, formula, neutral_mass, "
            "source_file, row_number) VALUES (99, 'Pipeline', 'C', 12.0, 'x', 1)"
        )
        con.execute(
            "INSERT INTO target_list_matches (target_compound_id, feature_id, adduct_label, "
            "adduct_charge, adduct_delta_mass, multiplication_factor, theoretical_mz, "
            "ppm_diff, match_type) VALUES (99, 2, '[M+H]+', 1, 1.0, 1, 500.0, 0, 'existing')"
        )
        con.commit()
    targets = _write_csv(tmp_path / "t.csv", [{"name": "Glucose", "formula": "C6H12O6", "inchikey": ""}])
    rows = preview_post_run_matches(db, [str(targets)], "positive", ["[M+H]+"], 10.0)

    assert apply_post_run_matches(db, "r1", rows, {"match_ppm": 10.0}) == 1
    names = dict(zip(load_feature_list(db)["feature_id"], load_feature_list(db)["compound_name"]))
    assert names[1] == "Glucose"
    assert count_command_target_list_matches(db, POST_RUN_COMMAND) == 1

    assert delete_command_target_list_rows(db, POST_RUN_COMMAND) == 1
    names = dict(zip(load_feature_list(db)["feature_id"], load_feature_list(db)["compound_name"]))
    assert pd.isna(names[1]) and names[2] == "Pipeline"


def test_post_run_preview_rejects_adduct_of_the_other_polarity(tmp_path: Path):
    from msianalyzer.core.annotation.target_list import preview_post_run_matches

    db = _analysis_with_features(tmp_path, [100.0])
    targets = _write_csv(tmp_path / "t.csv", [{"name": "G", "formula": "C6H12O6", "inchikey": ""}])
    with pytest.raises(TargetListError):
        preview_post_run_matches(db, [str(targets)], "negative", ["[M+H]+"], 10.0)


def test_post_run_mz_target_saves_with_target_mz_and_blank_formula_hidden(tmp_path: Path):
    from msianalyzer.core.analysis_db import load_feature_representative_annotations
    from msianalyzer.core.annotation.target_list import (
        apply_post_run_matches,
        preview_post_run_matches,
    )

    db = _analysis_with_features(tmp_path, [321.1234])
    targets = tmp_path / "t.csv"
    targets.write_text("name,mz\nMystery,321.1235\n")
    rows = preview_post_run_matches(db, [str(targets)], "positive", None, 10.0)
    assert [(r["adduct"], r["feature_id"], r["formula"]) for r in rows] == [("m/z", 1, "")]

    apply_post_run_matches(db, "r1", rows, {})
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT target_mz FROM target_list_compounds").fetchone()[0] == 321.1235
    rep = load_feature_representative_annotations(db).iloc[0]
    assert rep["compound_name"] == "Mystery" and rep["adduct"] == "m/z"
    assert rep["compound_formula"] is None
