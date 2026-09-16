"""
formula_prediction.py
On-demand molecular-formula prediction for features with no strong
identity — the user picks a feature subset (typically unannotated, or
annotated but unconvincing) from the GUI's "Predict formula" window;
independent of, and never overriding, a real MS2 annotation or
target-list match (see `analysis_db.load_feature_representative_
annotations`'s three-tier precedence: target_list > ms2 > predicted).

Uses `msbuddy <https://github.com/Philipbear/msbuddy>`_ (Apache-2.0) for
the actual mass-decomposition search. Deliberately **not** a `run.py`
`RUN_STEPS` pipeline stage — see `ADR 27
<../../adr/0027-formula-prediction.html>`_: msbuddy needs a ~420MB
one-time downloaded reference database and holds it ~600-700MB resident
in memory once loaded, a real cost not worth paying on every run
regardless of whether formula prediction is wanted this time. Instead
it's only invoked when the user explicitly asks for it, over a feature
subset they choose — which also means it's the first feature in this
codebase where a GUI action writes new rows into an *already-finished*
analysis database (see `analysis_db.save_predicted_formulas`).
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from msianalyzer.core import analysis_db
from msianalyzer.core.utils.logging_utils import log_call

logger = logging.getLogger(__name__)


@dataclass
class FormulaPredictionSettings:
    """Settings for one on-demand formula-prediction invocation.

    Not a `Config`/`GROUPS` member — this never touches a run's `Config`
    or `Config.version`; the settings used for one invocation are instead
    recorded in that invocation's `commands.arguments` (see
    `run_formula_prediction`), the same provenance convention every other
    step already uses.

    Attributes:
        adducts: Adduct labels to search (see
            `core.annotation.target_list.Adduct.label`, e.g. `"[M+H]+"`)
            — confirmed drop-in compatible with msbuddy's own adduct
            notation. Must be non-empty.
        error_ppm: Mass tolerance, in ppm, for both the search itself and
            the returned `mass_error_ppm` (`msbuddy.Msbuddy.mz_to_formula`'s
            `mz_tol`/`ppm=True`).
        top_n: Best N formula candidates to keep per (feature, adduct) —
            msbuddy's own results already come back sorted by ascending
            `abs(mass_error_ppm)`, so this is a plain head-N.
        halogen: Whether F/Cl/Br/I may appear in candidate formulas.
    """

    adducts: list[str]
    error_ppm: float = 10.0
    top_n: int = 5
    halogen: bool = False

    def __post_init__(self) -> None:
        if not self.adducts:
            raise ValueError("formula_prediction: adducts must be non-empty")
        if self.top_n < 1:
            raise ValueError(f"formula_prediction: top_n must be >= 1, got {self.top_n}")
        if self.error_ppm <= 0:
            raise ValueError(
                f"formula_prediction: error_ppm must be > 0, got {self.error_ppm}"
            )


@dataclass
class PredictedFormula:
    """One candidate formula for one (feature, adduct) pair.

    Attributes:
        feature_id: The feature this candidate was searched for.
        adduct: Adduct label used for this search (e.g. `"[M+H]+"`).
        formula: Candidate molecular formula, e.g. `"C6H12O6"`.
        mass_error: Absolute mass error, Da.
        mass_error_ppm: Mass error, ppm — candidates are ranked by
            ascending `abs(mass_error_ppm)`.
        rank: 1-based rank within this (feature, adduct) pair's
            candidates (1 == closest match).
    """

    feature_id: int
    adduct: str
    formula: str
    mass_error: float
    mass_error_ppm: float
    rank: int


def _build_engine(settings: FormulaPredictionSettings):
    """Construct one `msbuddy.Msbuddy` engine for reuse across every query
    in one invocation.

    Building this loads msbuddy's reference database into memory
    (downloading it first, on a machine's very first use — see this
    module's docstring) — a ~15s cold / ~0.07s warm cost, but ~600-700MB
    resident either way. Doing this once per invocation rather than once
    per feature is the entire reason `run_formula_prediction` shares one
    engine across a thread pool instead of, say, a `ProcessPoolExecutor`
    (which would duplicate that memory once per worker process).
    """
    from msbuddy import Msbuddy, MsbuddyConfig

    return Msbuddy(
        MsbuddyConfig(ms1_tol=settings.error_ppm, ppm=True, halogen=settings.halogen)
    )


def predict_formulas_for_feature(
    engine, feature_id: int, mz: float, settings: FormulaPredictionSettings
) -> list[PredictedFormula]:
    """Predict candidate formulas for one feature, across every adduct in
    `settings.adducts`.

    Pure enough to unit test with `engine` mocked (a stand-in exposing
    `mz_to_formula(mz, adduct, mz_tol, ppm, halogen) -> list[object with
    .formula/.mass_error/.mass_error_ppm]` — msbuddy's own
    `FormulaResult` shape) — never needs the real ~420MB database in
    tests.

    Args:
        engine: An `msbuddy.Msbuddy` instance (see `_build_engine`).
        feature_id: The feature being searched for.
        mz: The feature's observed m/z.
        settings: Adducts/tolerance/top_n/halogen for this search.

    Returns:
        Up to `settings.top_n` `PredictedFormula` rows per adduct, in
        `settings.adducts` order, each internally ranked by ascending
        `abs(mass_error_ppm)`.
    """
    out: list[PredictedFormula] = []
    for adduct in settings.adducts:
        results = engine.mz_to_formula(
            mz,
            adduct=adduct,
            mz_tol=settings.error_ppm,
            ppm=True,
            halogen=settings.halogen,
        )
        for rank, r in enumerate(results[: settings.top_n], start=1):
            out.append(
                PredictedFormula(
                    feature_id=feature_id,
                    adduct=adduct,
                    formula=r.formula,
                    mass_error=float(r.mass_error),
                    mass_error_ppm=float(r.mass_error_ppm),
                    rank=rank,
                )
            )
    return out


@log_call(source="analysis_db_path")
def run_formula_prediction(
    analysis_db_path: Path | str,
    feature_ids: list[int],
    settings: FormulaPredictionSettings,
    n_threads: int = 4,
) -> list[PredictedFormula]:
    """Predict formulas for `feature_ids`, persist the results, and return
    what was written.

    One shared `Msbuddy` engine for the whole call (see `_build_engine`),
    farmed out across a small `ThreadPoolExecutor` — threads share that
    one loaded engine/database rather than each duplicating its ~600-
    700MB memory footprint the way separate worker processes would.
    `mz_to_formula`'s inner loop is `numba`-jitted, which releases the
    GIL for its compiled sections, so this still gets real parallelism.

    Args:
        analysis_db_path: The analysis database (must already have its
            `features` table populated).
        feature_ids: Which features to predict for — a user-selected
            subset from the GUI's "Predict formula" window, never "every
            unannotated feature" automatically (this is never called as
            part of an ordinary pipeline run — see this module's
            docstring / ADR 27). Any id not present in the database's
            `features` table is silently skipped.
        settings: Adducts/tolerance/top_n/halogen for this invocation.
        n_threads: Worker threads for the per-(feature, adduct) queries.

    Returns:
        Every `PredictedFormula` written (already persisted — see
        `analysis_db.save_predicted_formulas`, which replaces exactly
        `feature_ids`' prior predictions, if any — every other feature's
        predictions are untouched).
    """
    analysis_db_path = Path(analysis_db_path)
    mzs = analysis_db.load_feature_ids_and_mzs(analysis_db_path)
    mz_by_id = dict(zip(mzs["feature_id"], mzs["mz"]))

    engine = _build_engine(settings)
    rows: list[PredictedFormula] = []
    with ThreadPoolExecutor(max_workers=n_threads) as pool:
        futures = [
            pool.submit(predict_formulas_for_feature, engine, fid, mz_by_id[fid], settings)
            for fid in feature_ids
            if fid in mz_by_id
        ]
        for future in futures:
            rows.extend(future.result())

    run_id = analysis_db.load_metadata_value(analysis_db_path, "analysis_id") or "unknown"
    command_id = analysis_db.log_command(
        analysis_db_path,
        "predict_formula",
        {
            "feature_ids": list(feature_ids),
            "adducts": settings.adducts,
            "error_ppm": settings.error_ppm,
            "top_n": settings.top_n,
            "halogen": settings.halogen,
        },
        run_id=run_id,
    )
    analysis_db.save_predicted_formulas(analysis_db_path, rows, command_id=command_id)

    logger.info(
        "formula prediction: %d feature(s) x %d adduct(s) -> %d candidate(s)",
        len(feature_ids),
        len(settings.adducts),
        len(rows),
        extra={"source_file": str(analysis_db_path)},
    )
    return rows
