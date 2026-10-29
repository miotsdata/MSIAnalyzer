"""Annotation by precursor m/z alone (ADR 0065).

Every feature — with or without MS2 — is matched by m/z against every
library spectrum's precursor, within ``AnnotateConfig.precursor_ppm``.
All candidates are kept in ``precursor_annotations``, one row per
(feature, library, compound, adduct), ranked by absolute ppm error.

The reference m/z of a library spectrum is its stored precursor m/z when
that has at least 4 decimals; otherwise (often rounded in public
libraries) the theoretical m/z from the compound formula and the
spectrum's adduct, when both can be read; else the stored value anyway.

Isomers share a formula, so they tie on ppm: a mass alone can't tell them
apart, which is why a feature named from these hits shows the formula and
adduct with a candidate count, not one compound name.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
from pyteomics import mass as pt_mass
from pyteomics.auxiliary.structures import PyteomicsError

from ..analysis_db import create_analysis_schema
from ..parser.mzml_parser import blob_to_array
from ..spectra.mz_tools import weighted_mz_and_spread
from ..utils.logging_utils import log_call
from .ms2_cleaning import normalize_polarity
from .target_list import Adduct, TargetListError, adduct_by_label, adduct_mz

logger = logging.getLogger(__name__)

#: A stored precursor m/z with at least this many decimals is used as is.
MIN_DECIMALS = 4


def n_decimals(value: float) -> int:
    """Decimals of `value` as stored (``760.585`` -> 3: trailing zeros
    are lost in a float)."""
    text = repr(float(value))
    if "e" in text or "." not in text:
        return 0
    return len(text.split(".")[1].rstrip("0"))


@lru_cache(maxsize=None)
def _neutral_mass(formula: str | None) -> float | None:
    if not formula:
        return None
    try:
        return float(pt_mass.calculate_mass(formula=formula))
    except (PyteomicsError, ValueError, KeyError):
        return None


def reference_mz(
    library_mz: float,
    formula: str | None,
    adduct_label: str | None,
    adduct_charge: int | None = None,
    adduct_delta: float | None = None,
    adduct_mult: int | None = None,
) -> tuple[float, str]:
    """`(m/z, source)` to compare a feature against: the library's stored
    precursor m/z (`"library"`) when it has `MIN_DECIMALS` decimals, else
    formula + adduct (`"formula"`) when both can be read."""
    if n_decimals(library_mz) >= MIN_DECIMALS:
        return float(library_mz), "library"
    neutral = _neutral_mass(formula)
    if neutral is not None:
        adduct = None
        try:
            adduct = adduct_by_label(adduct_label or "")
        except TargetListError:
            if adduct_charge and adduct_delta is not None:
                adduct = Adduct(adduct_label or "?", "any", int(adduct_charge),
                                float(adduct_delta), int(adduct_mult or 1))
        if adduct is not None and adduct.charge:
            return adduct_mz(neutral, adduct), "formula"
    return float(library_mz), "library"


@dataclass
class _LibraryEntries:
    """One library's spectra, by reference m/z (sorted)."""

    library_id: int
    ref_mz: np.ndarray
    ref_source: list[str]
    polarity: list[str | None]
    rows: list[tuple]  # (spectrum_id, compound_id, name, formula, inchikey, adduct)


def _load_library(library_id: int, path: str) -> _LibraryEntries:
    """Every spectrum's precursor and compound — no peak arrays."""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        data = con.execute(
            "SELECT s.id, s.compound_id, c.name, c.formula, c.inchikey, a.formula, "
            "s.precursor_mz, s.polarity, a.charge, a.delta_mass, a.multiplication_factor "
            "FROM spectrum s JOIN compound c ON c.id = s.compound_id "
            "LEFT JOIN adduct_type a ON a.id = s.adduct_type_id"
        ).fetchall()
    finally:
        con.close()
    refs = [reference_mz(r[6], r[3], r[5], r[8], r[9], r[10]) for r in data]
    order = np.argsort([m for m, _ in refs], kind="stable")
    return _LibraryEntries(
        library_id=library_id,
        ref_mz=np.array([refs[i][0] for i in order], dtype=float),
        ref_source=[refs[i][1] for i in order],
        polarity=[normalize_polarity(data[i][7]) for i in order],
        rows=[data[i][:6] for i in order],
    )


@dataclass
class PrecursorCandidate:
    feature_id: int
    library_id: int
    library_spectrum_id: int
    compound_id: int | None
    compound_name: str | None
    compound_formula: str | None
    inchikey: str | None
    adduct: str | None
    ref_mz: float
    ref_source: str
    ppm_error: float
    rank: int = 0
    tie_rank: int = 0


def match_features(
    features: list[tuple],
    libraries: list[_LibraryEntries],
    ppm: float,
    polarities: set[str] | None = None,
) -> list[PrecursorCandidate]:
    """Every candidate within `ppm` of each feature, the closest spectrum
    per (library, compound, adduct), ranked per feature by |ppm|.

    `features` holds ``(feature_id, mz)`` or ``(feature_id, mz, spread_ppm)``:
    candidates whose |ppm| is within `spread_ppm` of their tie group's
    first share its ``tie_rank``. `polarities` keeps spectra of those
    polarities (or unknown); `None` keeps all."""
    out: list[PrecursorCandidate] = []
    for feature_id, mz, *rest in features:
        spread = (rest[0] if rest else None) or 0.0
        best: dict[tuple, PrecursorCandidate] = {}
        for lib in libraries:
            lo = np.searchsorted(lib.ref_mz, mz * (1 - ppm * 1e-6), side="left")
            hi = np.searchsorted(lib.ref_mz, mz * (1 + ppm * 1e-6), side="right")
            for i in range(lo, hi):
                pol = lib.polarity[i]
                if polarities and pol is not None and pol not in polarities:
                    continue
                sid, cid, name, formula, inchikey, adduct = lib.rows[i]
                ref = float(lib.ref_mz[i])
                cand = PrecursorCandidate(
                    int(feature_id), lib.library_id, int(sid), cid, name, formula,
                    inchikey, adduct, ref, lib.ref_source[i], (mz - ref) / ref * 1e6,
                )
                key = (lib.library_id, cid if cid is not None else sid, adduct)
                if key not in best or abs(cand.ppm_error) < abs(best[key].ppm_error):
                    best[key] = cand
        ranked = sorted(best.values(), key=lambda c: (abs(c.ppm_error), c.compound_name or ""))
        group_start = None
        for rank, cand in enumerate(ranked, start=1):
            cand.rank = rank
            if group_start is None or abs(cand.ppm_error) - abs(group_start.ppm_error) > spread:
                group_start = cand
            cand.tie_rank = group_start.rank
        out.extend(ranked)
    return out


def update_feature_mz_precision(con: sqlite3.Connection) -> None:
    """Fill ``features.mz_weighted`` / ``mz_spread_ppm`` from each sample's
    filtered MS1 peaks, the alignment's input (`members_json` indexes
    them). A feature with no member peak (injected) keeps NULL.

    The stored peaks are already on the 0.0001 Da centroid grid, so this
    gains weighting and the spread, not sub-grid precision.
    """
    samples = con.execute("SELECT sample_id, name FROM samples ORDER BY sample_id").fetchall()
    peaks = []
    for sample_id, _ in samples:
        row = con.execute(
            "SELECT a.mz_array, a.intensity_array FROM aggregated_spectra a "
            "JOIN commands c ON c.id = a.command_id "
            "WHERE c.command_name = 'filter_spectra' AND a.sample_id = ? "
            "ORDER BY a.id DESC LIMIT 1",
            (sample_id,),
        ).fetchone()
        peaks.append((blob_to_array(row[0]), blob_to_array(row[1])) if row else None)
    names = [name for _, name in samples]
    updates = []
    for feature_id, members_json in con.execute("SELECT feature_id, members_json FROM features"):
        mzs, ints = [], []
        # Keyed by sample name; by position when the run renamed them
        # (`align.sample_names`) — both follow the samples' order.
        for pos, (key, idx) in enumerate(json.loads(members_json or "{}").items()):
            s = names.index(key) if key in names else pos
            if idx is None or s >= len(peaks) or peaks[s] is None or idx >= len(peaks[s][0]):
                continue
            mzs.append(peaks[s][0][idx])
            ints.append(peaks[s][1][idx])
        mz, spread = weighted_mz_and_spread(mzs, ints) if mzs else (None, None)
        updates.append((mz, spread, feature_id))
    con.executemany(
        "UPDATE features SET mz_weighted = ?, mz_spread_ppm = ? WHERE feature_id = ?", updates
    )
    con.commit()


_COLS = (
    "feature_id", "library_id", "library_spectrum_id", "compound_id", "compound_name",
    "compound_formula", "inchikey", "adduct", "ref_mz", "ref_source", "ppm_error",
    "rank", "tie_rank", "command_id",
)


@log_call(source="analysis_db_path")
def run_precursor_annotation(
    analysis_db_path: Path | str, config, *, command_id: int | None = None
) -> int:
    """Match every feature against `config.library_path`'s precursors and
    replace ``precursor_annotations``; return the number of features with
    at least one candidate."""
    from .annotate import _register_library, load_library, normalize_library_paths

    paths = normalize_library_paths(config.library_path)
    with sqlite3.connect(analysis_db_path) as con:
        create_analysis_schema(con)
        update_feature_mz_precision(con)
        features = con.execute(
            "SELECT feature_id, COALESCE(mz_weighted, mz), mz_spread_ppm FROM features"
        ).fetchall()
        polarities = {
            p for (raw,) in con.execute("SELECT polarity FROM samples")
            if (p := normalize_polarity(raw)) is not None
        }
    library_ids = [
        _register_library(analysis_db_path, load_library(p), command_id=command_id)
        for p in paths
    ]
    libraries = [_load_library(lid, p) for lid, p in zip(library_ids, paths)]
    candidates = match_features(features, libraries, config.precursor_ppm, polarities or None)

    with sqlite3.connect(analysis_db_path) as con:
        con.execute("DELETE FROM precursor_annotations")
        con.executemany(
            f"INSERT INTO precursor_annotations ({', '.join(_COLS)}) "
            f"VALUES ({', '.join('?' * len(_COLS))})",
            [
                (c.feature_id, c.library_id, c.library_spectrum_id, c.compound_id,
                 c.compound_name, c.compound_formula, c.inchikey, c.adduct, c.ref_mz,
                 c.ref_source, c.ppm_error, c.rank, c.tie_rank, command_id)
                for c in candidates
            ],
        )
        con.commit()
    n = len({c.feature_id for c in candidates})
    logger.info(
        "precursor annotation: %d candidate(s) for %d of %d feature(s) within %s ppm",
        len(candidates), n, len(features), config.precursor_ppm,
    )
    return n
