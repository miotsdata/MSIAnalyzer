"""Tests for :mod:`msianalyzer.core.annotation.formula_prediction`.

Never calls real `msbuddy` — that needs a ~420MB one-time download (see
ADR 27) and would make this suite slow/network-dependent. Every test here
uses a small fake engine exposing the same `mz_to_formula(mz, adduct,
mz_tol, ppm, halogen) -> list[object with .formula/.mass_error/
.mass_error_ppm]` shape `msbuddy.Msbuddy` does (verified against the real
package during design — see the formula-prediction ADR).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest

from msianalyzer.core.analysis_db import (
    init_analysis_db,
    load_predicted_formulas_for_feature,
    write_metadata,
)
from msianalyzer.core.annotation import formula_prediction as fp


# ---------------------------------------------------------------------------
# fake msbuddy engine
# ---------------------------------------------------------------------------


@dataclass
class _FakeFormulaResult:
    formula: str
    mass_error: float
    mass_error_ppm: float


class _FakeEngine:
    """Canned per-adduct results, already sorted by ascending abs(ppm error)
    (matching real `Msbuddy.mz_to_formula`'s own ordering) — keyed by adduct
    label so a test can hand each adduct a distinct candidate list."""

    def __init__(self, results_by_adduct: dict[str, list[_FakeFormulaResult]]):
        self.results_by_adduct = results_by_adduct
        self.calls: list[tuple] = []

    def mz_to_formula(self, mz, adduct, mz_tol, ppm, halogen=False):
        self.calls.append((mz, adduct, mz_tol, ppm, halogen))
        return self.results_by_adduct.get(adduct, [])


# ---------------------------------------------------------------------------
# FormulaPredictionSettings
# ---------------------------------------------------------------------------


def test_settings_rejects_empty_adducts():
    with pytest.raises(ValueError, match="adducts"):
        fp.FormulaPredictionSettings(adducts=[])


def test_settings_rejects_non_positive_top_n():
    with pytest.raises(ValueError, match="top_n"):
        fp.FormulaPredictionSettings(adducts=["[M+H]+"], top_n=0)


def test_settings_rejects_non_positive_error_ppm():
    with pytest.raises(ValueError, match="error_ppm"):
        fp.FormulaPredictionSettings(adducts=["[M+H]+"], error_ppm=0)


def test_settings_defaults():
    settings = fp.FormulaPredictionSettings(adducts=["[M+H]+"])
    assert settings.error_ppm == 10.0
    assert settings.top_n == 5
    assert settings.halogen is False


# ---------------------------------------------------------------------------
# predict_formulas_for_feature (pure logic, fake engine)
# ---------------------------------------------------------------------------


def test_predict_formulas_for_feature_assigns_rank_per_adduct():
    engine = _FakeEngine(
        {
            "[M+H]+": [
                _FakeFormulaResult("C6H12O6", 0.00003, 0.19),
                _FakeFormulaResult("C7H16OS2", 0.0008, 4.6),
            ],
            "[M+Na]+": [
                _FakeFormulaResult("C6H12O6", 0.00005, 0.3),
            ],
        }
    )
    settings = fp.FormulaPredictionSettings(adducts=["[M+H]+", "[M+Na]+"], top_n=5)

    rows = fp.predict_formulas_for_feature(engine, feature_id=1, mz=181.0707, settings=settings)

    assert len(rows) == 3
    by_adduct = {}
    for r in rows:
        by_adduct.setdefault(r.adduct, []).append(r)
    assert [r.rank for r in by_adduct["[M+H]+"]] == [1, 2]
    assert [r.formula for r in by_adduct["[M+H]+"]] == ["C6H12O6", "C7H16OS2"]
    assert [r.rank for r in by_adduct["[M+Na]+"]] == [1]
    assert all(r.feature_id == 1 for r in rows)


def test_predict_formulas_for_feature_truncates_to_top_n():
    engine = _FakeEngine(
        {
            "[M+H]+": [
                _FakeFormulaResult("A", 0.0, 0.1),
                _FakeFormulaResult("B", 0.0, 0.2),
                _FakeFormulaResult("C", 0.0, 0.3),
            ],
        }
    )
    settings = fp.FormulaPredictionSettings(adducts=["[M+H]+"], top_n=2)

    rows = fp.predict_formulas_for_feature(engine, feature_id=1, mz=100.0, settings=settings)

    assert [r.formula for r in rows] == ["A", "B"]


def test_predict_formulas_for_feature_empty_when_no_candidates():
    engine = _FakeEngine({})
    settings = fp.FormulaPredictionSettings(adducts=["[M+H]+"])

    rows = fp.predict_formulas_for_feature(engine, feature_id=1, mz=999.0, settings=settings)

    assert rows == []


def test_predict_formulas_for_feature_passes_settings_through_to_engine():
    engine = _FakeEngine({"[M+H]+": []})
    settings = fp.FormulaPredictionSettings(
        adducts=["[M+H]+"], error_ppm=15.0, halogen=True
    )

    fp.predict_formulas_for_feature(engine, feature_id=1, mz=200.0, settings=settings)

    assert engine.calls == [(200.0, "[M+H]+", 15.0, True, True)]


# ---------------------------------------------------------------------------
# run_formula_prediction (DB integration, engine construction mocked)
# ---------------------------------------------------------------------------


def test_run_formula_prediction_persists_and_returns_rows(tmp_path: Path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    with sqlite3.connect(db) as con:
        con.executemany(
            "INSERT INTO features (feature_id, mz, members_json) VALUES (?, ?, ?)",
            [(1, 181.0707, "{}"), (2, 100.0, "{}")],
        )
        con.commit()

    engine = _FakeEngine({"[M+H]+": [_FakeFormulaResult("C6H12O6", 0.00003, 0.19)]})
    settings = fp.FormulaPredictionSettings(adducts=["[M+H]+"])

    with patch.object(fp, "_build_engine", return_value=engine):
        rows = fp.run_formula_prediction(db, feature_ids=[1], settings=settings, n_threads=2)

    assert len(rows) == 1
    assert rows[0].feature_id == 1
    assert rows[0].formula == "C6H12O6"

    persisted = load_predicted_formulas_for_feature(db, 1)
    assert len(persisted) == 1
    assert persisted.iloc[0]["formula"] == "C6H12O6"
    # feature 2 wasn't in feature_ids -> untouched, no predictions
    assert load_predicted_formulas_for_feature(db, 2).empty


def test_run_formula_prediction_skips_unknown_feature_ids(tmp_path: Path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    with sqlite3.connect(db) as con:
        con.execute(
            "INSERT INTO features (feature_id, mz, members_json) VALUES (1, 100.0, '{}')"
        )
        con.commit()

    engine = _FakeEngine({"[M+H]+": [_FakeFormulaResult("A", 0.0, 1.0)]})
    settings = fp.FormulaPredictionSettings(adducts=["[M+H]+"])

    with patch.object(fp, "_build_engine", return_value=engine):
        rows = fp.run_formula_prediction(db, feature_ids=[1, 999], settings=settings)

    # 999 doesn't exist in `features` -> silently skipped, no error
    assert {r.feature_id for r in rows} == {1}


def test_run_formula_prediction_uses_analysis_id_metadata_as_run_id(tmp_path: Path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    write_metadata(db, {"analysis_id": "run-abc-123"})
    with sqlite3.connect(db) as con:
        con.execute(
            "INSERT INTO features (feature_id, mz, members_json) VALUES (1, 100.0, '{}')"
        )
        con.commit()

    engine = _FakeEngine({"[M+H]+": [_FakeFormulaResult("A", 0.0, 1.0)]})
    settings = fp.FormulaPredictionSettings(adducts=["[M+H]+"])

    with patch.object(fp, "_build_engine", return_value=engine):
        fp.run_formula_prediction(db, feature_ids=[1], settings=settings)

    with sqlite3.connect(db) as con:
        run_id = con.execute(
            "SELECT run_id FROM commands WHERE command_name = 'predict_formula'"
        ).fetchone()[0]
    assert run_id == "run-abc-123"


def test_run_formula_prediction_falls_back_to_unknown_run_id_without_metadata(
    tmp_path: Path,
):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    with sqlite3.connect(db) as con:
        con.execute(
            "INSERT INTO features (feature_id, mz, members_json) VALUES (1, 100.0, '{}')"
        )
        con.commit()

    engine = _FakeEngine({"[M+H]+": [_FakeFormulaResult("A", 0.0, 1.0)]})
    settings = fp.FormulaPredictionSettings(adducts=["[M+H]+"])

    with patch.object(fp, "_build_engine", return_value=engine):
        fp.run_formula_prediction(db, feature_ids=[1], settings=settings)

    with sqlite3.connect(db) as con:
        run_id = con.execute(
            "SELECT run_id FROM commands WHERE command_name = 'predict_formula'"
        ).fetchone()[0]
    assert run_id == "unknown"
