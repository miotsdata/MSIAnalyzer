"""
target_list.py
Match a user-supplied list of target compounds against detected features by
theoretical m/z, independent of MS2 spectral annotation (Stage B,
:mod:`msianalyzer.core.annotation.annotate`).

A target compound (name, chemical formula, InChIKey) has no m/z of its own
— it must be searched for across configurable ion adducts (protonation,
sodiation, ...). A compound's theoretical m/z is either close enough to an
already-detected feature to attach to it, or it isn't, in which case a new
synthetic feature is created for it (``features.origin = 'injected'``) so
it still gets a real, independently-measured spatial heatmap (see
``core.run.run``'s ``target_mz_set`` wiring) and shows up in the
Annotations data — not silently absent because it happened not to survive
peak detection.

See `ADR 26 <../../adr/0026-target-list-annotation.html>`_ for the full
design rationale.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
from pyteomics import mass as pt_mass
from pyteomics.auxiliary.structures import PyteomicsError

from msianalyzer.core import analysis_db
from msianalyzer.core.utils import MSIAnalyzerError
from msianalyzer.core.utils.logging_utils import log_call

if TYPE_CHECKING:  # avoid importing the config package at module load
    from ..config.config import TargetListConfig

logger = logging.getLogger(__name__)


class TargetListError(MSIAnalyzerError):
    """Base for target-list parsing/matching errors."""


class InvalidFormulaError(TargetListError):
    """A target-list row's formula could not be parsed."""


# ---------------------------------------------------------------------------
# Adducts
# ---------------------------------------------------------------------------

#: physical constants (same values `libviz`'s own adduct model hardcodes —
#: cross-checked, not copied: this project derives its adduct deltas from
#: `pyteomics.mass.calculate_mass` on each fragment instead of hand-typed
#: floats, so they're auditable/re-derivable).
PROTON_MASS = 1.007276466812
ELECTRON_MASS = 0.000548579909065

_NA = pt_mass.calculate_mass(formula="Na")
_K = pt_mass.calculate_mass(formula="K")
_NH3 = pt_mass.calculate_mass(formula="NH3")
_H2O = pt_mass.calculate_mass(formula="H2O")
_CL = pt_mass.calculate_mass(formula="Cl")
_FA = pt_mass.calculate_mass(formula="CH2O2")  # formic acid, [M+FA-H]- a.k.a. [M+HCOO]-
_HAC = pt_mass.calculate_mass(formula="C2H4O2")  # acetic acid, [M+Hac-H]- a.k.a. [M+CH3COO]-
_CH3OH = pt_mass.calculate_mass(formula="CH4O")  # methanol


@dataclass(frozen=True)
class Adduct:
    """One ion adduct: how a neutral molecule becomes a detected ion m/z.

    Attributes:
        label: Display name, MS-DIAL-style bracket notation, e.g.
            ``"[M+H]+"``, ``"[2M+H]+"``.
        polarity: ``"positive"`` or ``"negative"``.
        charge: Signed ion charge, e.g. ``1``, ``-1``, ``2``, ``-2``.
        delta_mass: Net mass added/removed by the adduct itself — *not*
            multiplied by `multiplication_factor` — e.g. `+PROTON_MASS` for
            ``[M+H]+``, `-PROTON_MASS` for ``[M-H]-``.
        multiplication_factor: ``1`` for a monomer (``[M...]``), ``2`` for a
            dimer (``[2M...]``), etc.
    """

    label: str
    polarity: str
    charge: int
    delta_mass: float
    multiplication_factor: int = 1


#: standard positive-mode adducts, MS-DIAL style.
POSITIVE_ADDUCTS: list[Adduct] = [
    Adduct("[M+H]+", "positive", 1, PROTON_MASS),
    Adduct("[M+Na]+", "positive", 1, _NA - ELECTRON_MASS),
    Adduct("[M+K]+", "positive", 1, _K - ELECTRON_MASS),
    Adduct("[M+NH4]+", "positive", 1, _NH3 + PROTON_MASS),
    Adduct("[M+H-H2O]+", "positive", 1, PROTON_MASS - _H2O),
    Adduct("[M+CH3OH+H]+", "positive", 1, _CH3OH + PROTON_MASS),
    Adduct("[M+2H]2+", "positive", 2, 2 * PROTON_MASS),
    Adduct("[2M+H]+", "positive", 1, PROTON_MASS, multiplication_factor=2),
    Adduct("[2M+Na]+", "positive", 1, _NA - ELECTRON_MASS, multiplication_factor=2),
]

#: standard negative-mode adducts, MS-DIAL style.
NEGATIVE_ADDUCTS: list[Adduct] = [
    Adduct("[M-H]-", "negative", -1, -PROTON_MASS),
    Adduct("[M+Cl]-", "negative", -1, _CL + ELECTRON_MASS),
    Adduct("[M+FA-H]-", "negative", -1, _FA - PROTON_MASS + ELECTRON_MASS),
    Adduct("[M+Hac-H]-", "negative", -1, _HAC - PROTON_MASS + ELECTRON_MASS),
    Adduct("[M-H2O-H]-", "negative", -1, -PROTON_MASS - _H2O),
    Adduct("[M-2H]2-", "negative", -2, -2 * PROTON_MASS),
    Adduct("[2M-H]-", "negative", -1, -PROTON_MASS, multiplication_factor=2),
]

ADDUCTS: list[Adduct] = POSITIVE_ADDUCTS + NEGATIVE_ADDUCTS
_ADDUCTS_BY_LABEL: dict[str, Adduct] = {a.label: a for a in ADDUCTS}


def adducts_for_polarity(polarity: str) -> list[Adduct]:
    """Every standard adduct for `polarity` (``"positive"``/``"negative"``)."""
    return [a for a in ADDUCTS if a.polarity == polarity]


def adduct_by_label(label: str) -> Adduct:
    """The `Adduct` named `label` (e.g. ``"[M+H]+"``).

    Raises:
        TargetListError: `label` isn't one of the standard adducts.
    """
    try:
        return _ADDUCTS_BY_LABEL[label]
    except KeyError:
        raise TargetListError(
            f"unknown adduct {label!r}; valid adducts: {sorted(_ADDUCTS_BY_LABEL)}"
        ) from None


def adduct_mz(neutral_mass: float, adduct: Adduct) -> float:
    """The ion m/z produced by applying `adduct` to a neutral `neutral_mass`.

    ``mz = (multiplication_factor * neutral_mass + delta_mass) / abs(charge)``.
    """
    return (adduct.multiplication_factor * neutral_mass + adduct.delta_mass) / abs(
        adduct.charge
    )


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

_REQUIRED_COLUMNS = ("name", "formula", "inchikey")


@dataclass
class TargetCompound:
    """One row of a target-list file, with its neutral mass pre-computed.

    Attributes:
        name: Compound name, as given in the file.
        formula: Chemical formula, as given in the file (e.g. ``"C6H12O6"``).
        inchikey: InChIKey, or `None` if the file left it blank.
        neutral_mass: Monoisotopic neutral mass of `formula`
            (`pyteomics.mass.calculate_mass`).
        source_file: The file this row came from.
        row_number: 1-based row number within `source_file` (header
            excluded) — for error messages.
    """

    name: str
    formula: str
    inchikey: str | None
    neutral_mass: float
    source_file: str
    row_number: int


def normalize_target_list_paths(paths: str | list[str] | None) -> list[str]:
    """Coerce `TargetListConfig.paths` to a de-duplicated list of path strings.

    Identical behavior to `annotate.normalize_library_paths`: `None`/``""``/
    an empty list -> ``[]`` (target-list matching disabled); a bare string
    -> a one-element list; a list/tuple is kept, order preserved, blanks
    dropped, duplicates removed.
    """
    if not paths:
        return []
    if isinstance(paths, (str, Path)):
        paths = [paths]
    seen: set[str] = set()
    out: list[str] = []
    for p in paths:
        if not p:
            continue
        s = str(p)
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


@log_call(source="path")
def parse_target_list_file(path: str | Path) -> list[TargetCompound]:
    """Parse and validate one target-list CSV/TXT file.

    Required columns (case-insensitive, whitespace-stripped): ``name``,
    ``formula``, ``inchikey``. ``inchikey`` may be blank per row. Every
    formula is parsed immediately — fail-fast on the first bad one, not
    skip-and-continue — so a broken file is caught before any of it is used
    (the GUI relies on this to validate a file the moment it's selected).

    Args:
        path: A ``.csv`` or ``.txt`` file (both read as comma-delimited).

    Returns:
        One `TargetCompound` per data row, in file order.

    Raises:
        TargetListError: A required column is missing, or the file has no
            data rows.
        InvalidFormulaError: A row's ``formula`` isn't a valid chemical
            formula.
    """
    path = Path(path)
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df.columns = [str(c).strip().lower() for c in df.columns]

    missing = [c for c in _REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise TargetListError(
            f"target list {path}: missing required column(s) {missing} "
            f"(found {list(df.columns)})"
        )
    if df.empty:
        raise TargetListError(f"target list {path}: no data rows")

    compounds: list[TargetCompound] = []
    for i, row in enumerate(df.itertuples(index=False), start=1):
        name = str(getattr(row, "name")).strip()
        formula = str(getattr(row, "formula")).strip()
        inchikey = str(getattr(row, "inchikey")).strip() or None
        try:
            neutral_mass = float(pt_mass.calculate_mass(formula=formula))
        except PyteomicsError as exc:
            raise InvalidFormulaError(
                f"target list {path} row {i} ({name!r}): invalid formula "
                f"{formula!r}: {exc}"
            ) from exc
        compounds.append(
            TargetCompound(
                name=name,
                formula=formula,
                inchikey=inchikey,
                neutral_mass=neutral_mass,
                source_file=str(path),
                row_number=i,
            )
        )
    return compounds


def parse_target_list_files(paths: str | list[str] | None) -> list[TargetCompound]:
    """`parse_target_list_file` over every path in `paths`, concatenated in order.

    No cross-file de-duplication — the same compound listed in two files
    becomes two `TargetCompound` rows (and, if both match the same feature,
    two match rows — consistent with "list every match, don't collapse").
    """
    compounds: list[TargetCompound] = []
    for p in normalize_target_list_paths(paths):
        compounds.extend(parse_target_list_file(p))
    return compounds


# ---------------------------------------------------------------------------
# Matching + injection (pure logic, no DB access)
# ---------------------------------------------------------------------------


@dataclass
class TargetMatch:
    """One (target compound, adduct) pair matched to a feature.

    Attributes:
        compound: The matched `TargetCompound`.
        adduct: The `Adduct` used.
        theoretical_mz: `adduct_mz(compound.neutral_mass, adduct)`.
        feature_id: The matched feature's id. `None` for a `"injected"`
            match until the caller (`run_target_list_matching`) has
            actually inserted the new feature and can resolve it.
        feature_mz: The feature's own consensus (or injected) m/z.
        ppm_diff: ``(feature_mz - theoretical_mz) / theoretical_mz * 1e6``.
        match_type: ``"existing"`` (within `match_ppm` of an
            already-detected feature) or ``"injected"`` (no existing
            feature close enough — a new synthetic feature was created).
    """

    compound: TargetCompound
    adduct: Adduct
    theoretical_mz: float
    feature_id: int | None
    feature_mz: float
    ppm_diff: float
    match_type: str


@dataclass
class InjectedFeature:
    """One new synthetic feature — no existing feature was within
    `match_ppm` of any target compound/adduct's theoretical m/z.

    `members_json` is an all-`None` dict (the same shape
    `analysis_db.load_features` already expects) — no real per-sample peak
    backs it; it's a pure mass-search hit.
    """

    mz: float
    members_json: str


@dataclass
class TargetListMatchResult:
    """The full output of :func:`match_target_list`."""

    matches: list[TargetMatch]
    injected_features: list[InjectedFeature]


def match_target_list(
    compounds: list[TargetCompound],
    adducts: list[Adduct],
    existing_features: pd.DataFrame,
    match_ppm: float,
    sample_names: list[str],
    mz_decimals: int = 4,
) -> TargetListMatchResult:
    """Match every (compound, adduct) pair against `existing_features` by m/z.

    Pure logic, no DB access.

    Args:
        compounds: Parsed target compounds (see `parse_target_list_files`).
        adducts: Adducts to search (see `adducts_for_polarity`).
        existing_features: A DataFrame with `feature_id` and `mz` columns
            (see `analysis_db.load_feature_ids_and_mzs`) — only these two
            columns are used.
        match_ppm: How close (ppm) a theoretical m/z must be to an existing
            feature's `mz` to match it, rather than being injected as a new
            feature.
        sample_names: Every sample name, for the all-`None` `members_json`
            of an injected feature.
        mz_decimals: Decimal places an injected feature's `mz` is rounded
            to (matches `AlignMzSamples.mz_decimals`'s convention).

    Returns:
        Every `TargetMatch` (both `match_type`\\ s) and every
        `InjectedFeature` that needs to be created — the caller
        (`run_target_list_matching`) inserts them and back-fills
        `TargetMatch.feature_id` for the `"injected"` ones.
    """
    if len(existing_features):
        order = np.argsort(existing_features["mz"].to_numpy(dtype=float))
        sorted_mzs = existing_features["mz"].to_numpy(dtype=float)[order]
        sorted_ids = existing_features["feature_id"].to_numpy(dtype=int)[order]
    else:
        sorted_mzs = np.array([], dtype=float)
        sorted_ids = np.array([], dtype=int)

    matches: list[TargetMatch] = []
    unmatched: list[tuple[TargetCompound, Adduct, float]] = []

    for compound in compounds:
        for adduct in adducts:
            theoretical_mz = adduct_mz(compound.neutral_mass, adduct)
            feature_id, feature_mz, ppm_diff = _nearest_within_ppm(
                sorted_mzs, sorted_ids, theoretical_mz, match_ppm
            )
            if feature_id is not None:
                matches.append(
                    TargetMatch(
                        compound=compound,
                        adduct=adduct,
                        theoretical_mz=theoretical_mz,
                        feature_id=feature_id,
                        feature_mz=feature_mz,
                        ppm_diff=ppm_diff,
                        match_type="existing",
                    )
                )
            else:
                unmatched.append((compound, adduct, theoretical_mz))

    injected_features, injected_matches = _cluster_and_inject(
        unmatched, match_ppm, sample_names, mz_decimals
    )
    matches.extend(injected_matches)
    return TargetListMatchResult(matches=matches, injected_features=injected_features)


def _nearest_within_ppm(
    sorted_mzs: np.ndarray, sorted_ids: np.ndarray, mz: float, ppm: float
) -> tuple[int | None, float | None, float | None]:
    """The nearest `(feature_id, mz)` pair to `mz`, if within `ppm` — else
    `(None, None, None)`. Explicit tolerance check (unlike
    `heatmap.py`'s `_feature_column_index`, which has none)."""
    if sorted_mzs.size == 0:
        return None, None, None
    idx = np.searchsorted(sorted_mzs, mz)
    candidates = [i for i in (idx - 1, idx) if 0 <= i < sorted_mzs.size]
    if not candidates:
        return None, None, None
    nearest = min(candidates, key=lambda i: abs(sorted_mzs[i] - mz))
    nearest_mz = float(sorted_mzs[nearest])
    ppm_diff = (nearest_mz - mz) / mz * 1e6
    if abs(ppm_diff) <= ppm:
        return int(sorted_ids[nearest]), nearest_mz, ppm_diff
    return None, None, None


def _cluster_and_inject(
    unmatched: list[tuple[TargetCompound, Adduct, float]],
    match_ppm: float,
    sample_names: list[str],
    mz_decimals: int,
) -> tuple[list[InjectedFeature], list[TargetMatch]]:
    """Cluster unmatched theoretical m/z among themselves at `match_ppm`
    (same greedy sort-and-chain approach as
    `spectra.mz_tools.align_mz_across_samples`, minus the one-per-sample
    constraint) so multiple compounds/adducts landing on the same mass
    collapse into one injected feature, not several near-duplicates.
    """
    if not unmatched:
        return [], []

    ordered = sorted(unmatched, key=lambda t: t[2])
    clusters: list[list[tuple[TargetCompound, Adduct, float]]] = []
    current = [ordered[0]]
    for item in ordered[1:]:
        cluster_mean = float(np.mean([c[2] for c in current]))
        delta_ppm = abs(item[2] - cluster_mean) / cluster_mean * 1e6
        if delta_ppm <= match_ppm:
            current.append(item)
        else:
            clusters.append(current)
            current = [item]
    clusters.append(current)

    injected_features: list[InjectedFeature] = []
    injected_matches: list[TargetMatch] = []
    members_json = json.dumps({name: None for name in sample_names})
    for cluster in clusters:
        cluster_mz = round(float(np.mean([c[2] for c in cluster])), mz_decimals)
        injected_features.append(InjectedFeature(mz=cluster_mz, members_json=members_json))
        for compound, adduct, theoretical_mz in cluster:
            ppm_diff = (cluster_mz - theoretical_mz) / theoretical_mz * 1e6
            injected_matches.append(
                TargetMatch(
                    compound=compound,
                    adduct=adduct,
                    theoretical_mz=theoretical_mz,
                    feature_id=None,
                    feature_mz=cluster_mz,
                    ppm_diff=ppm_diff,
                    match_type="injected",
                )
            )
    return injected_features, injected_matches


# ---------------------------------------------------------------------------
# Orchestration entry point (DB-touching)
# ---------------------------------------------------------------------------


@dataclass
class TargetListRunSummary:
    """Counts summarizing one :func:`run_target_list_matching` call."""

    n_files: int
    n_compounds: int
    n_adducts: int
    n_matches: int
    n_matched_existing: int
    n_injected_features: int
    n_distinct_features: int


@log_call(source="analysis_db_path")
def run_target_list_matching(
    analysis_db_path: Path | str,
    config: "TargetListConfig",
    command_id: int,
    sample_names: list[str],
) -> TargetListRunSummary:
    """Parse `config.paths`, match against `features`, persist the results.

    Injected features are appended to `features` (never deleting existing
    rows — `analysis_db.append_injected_features`), then every `TargetMatch`
    is saved with its real `feature_id` resolved.

    Args:
        analysis_db_path: The analysis database (must already have its
            `features` table populated by `align_mz`).
        config: The run's `TargetListConfig`.
        command_id: This step's `commands.id` (see `analysis_db.log_command`).
        sample_names: Every sample name in the analysis, for an injected
            feature's `members_json`.

    Returns:
        Counts summarizing what happened — also logged at INFO.
    """
    analysis_db_path = Path(analysis_db_path)

    compounds = parse_target_list_files(config.paths)
    adducts = (
        [adduct_by_label(label) for label in config.adducts]
        if config.adducts
        else adducts_for_polarity(config.polarity)
    )

    existing = analysis_db.load_feature_ids_and_mzs(analysis_db_path)
    result = match_target_list(
        compounds,
        adducts,
        existing,
        match_ppm=config.match_ppm,
        sample_names=sample_names,
    )

    injected_ids = analysis_db.append_injected_features(
        analysis_db_path, result.injected_features, command_id=command_id
    )
    mz_to_injected_id = dict(
        zip((f.mz for f in result.injected_features), injected_ids)
    )
    for m in result.matches:
        if m.match_type == "injected":
            m.feature_id = mz_to_injected_id[m.feature_mz]

    compound_ids = analysis_db.save_target_list_compounds(
        analysis_db_path, compounds, command_id=command_id
    )
    compound_id_of = {id(c): cid for c, cid in zip(compounds, compound_ids)}
    analysis_db.save_target_list_matches(
        analysis_db_path,
        [(m, compound_id_of[id(m.compound)]) for m in result.matches],
        command_id=command_id,
    )

    summary = TargetListRunSummary(
        n_files=len(normalize_target_list_paths(config.paths)),
        n_compounds=len(compounds),
        n_adducts=len(adducts),
        n_matches=len(result.matches),
        n_matched_existing=sum(1 for m in result.matches if m.match_type == "existing"),
        n_injected_features=len(result.injected_features),
        n_distinct_features=len({m.feature_id for m in result.matches}),
    )
    logger.info(
        "target list: %d file(s), %d compound(s) x %d adduct(s) -> %d match(es) "
        "(%d to existing features, %d injected)",
        summary.n_files, summary.n_compounds, summary.n_adducts, summary.n_matches,
        summary.n_matched_existing, summary.n_injected_features,
        extra={"source_file": str(analysis_db_path)},
    )
    return summary
