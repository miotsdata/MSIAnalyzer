"""Shared fixtures for the core unit tests.

The centrepiece is :func:`build_ms2_grouper_mock_data` (exposed as the
``ms2_grouper_mock_data`` factory fixture), which fabricates a realistic
batch of MS2 spectra plus a master m/z list and — on *every* call — plants
the edge cases named in ``test_ms2_grouper.py``:

* ``"single"``         -> ``test_ms2_associated_to_single_mz``
* ``"none"``           -> ``test_ms2_not_associated_to_any_mz``
* ``"chimeric"``       -> ``test_multiple_mz_in_same_ms2_isolation_window_add_flags_and_ppm_diff_for_each``
* ``"precursor_only"`` -> fragmentation did not occur (only the surviving precursor)
* ``"null_precursor"`` -> ``precursor_mz`` is null, fall back to ``isolation_window_target``

Each planted case comes with its expected answer (chosen feature, per-window
ppm differences, counts) so a test only has to run the association under
test and compare against ``mock.planted[<name>]``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

__all__ = [
    "PlantedCase",
    "Ms2GrouperMockData",
    "build_ms2_grouper_mock_data",
    "materialize_ms2_db",
    "build_mock_library",
]


# ---------------------------------------------------------------------------
# small numeric helpers
# ---------------------------------------------------------------------------


def ppm_between(mz: float, ref: float) -> float:
    """Signed ppm of ``mz`` relative to ``ref`` (positive => ``mz`` is higher)."""
    return (mz - ref) / ref * 1e6


def _mz_at_ppm(base: float, ppm: float) -> float:
    """m/z sitting exactly ``ppm`` away from ``base``."""
    return base * (1.0 + ppm / 1e6)


# ---------------------------------------------------------------------------
# result containers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlantedCase:
    """One deliberately constructed MS2 scan and its expected association.

    Attributes:
        name: Short key (``"single"`` / ``"none"`` / ``"chimeric"``).
        scan_id: ``scan_id`` of the planted scan inside ``mock.scans``.
        description: Human readable statement of what the scan exercises.
        precursor_mz: The scan's ``precursor_mz`` (``None`` only if a test
            asks for the null-precursor variant).
        isolation_window: ``(target - lower, target + upper)`` in m/z.
        features_in_window: Master m/z values lying inside ``isolation_window``,
            ascending.
        ppm_diff_per_window_feature: ``{master_mz: signed_ppm_vs_precursor}``
            for every entry of ``features_in_window`` — the per-feature ppm
            diff the grouper is expected to record.
        n_in_window: ``len(features_in_window)`` — the chimera / co-isolation
            count flag.
        expected_feature_mz: Best single match (smallest \\|ppm\\| to the
            precursor), or ``None`` when the scan must stay unassigned.
        expected_ppm_offset: Signed ppm of the precursor vs
            ``expected_feature_mz`` (``None`` when unassigned).
        nearest_other_feature_ppm: Signed ppm from the precursor to the
            closest master m/z that is *not* ``expected_feature_mz``
            (``None`` if there is no other feature).
        expected_match_key: Which scan field the grouper should match on —
            ``"precursor_mz"`` normally, ``"isolation_window_target"`` for
            the null-precursor fallback case.
        expected_low_fragmentation: Whether the grouper should compute a
            low `fragmentation_factor` for this scan (showing no real
            fragmentation) — a boolean statement of test intent, not the
            continuous value itself.
    """

    name: str
    scan_id: int
    description: str
    precursor_mz: float | None
    isolation_window: tuple[float, float]
    features_in_window: list[float]
    ppm_diff_per_window_feature: dict[float, float]
    n_in_window: int
    expected_feature_mz: float | None
    expected_ppm_offset: float | None
    nearest_other_feature_ppm: float | None
    expected_match_key: str = "precursor_mz"
    expected_low_fragmentation: bool = False


@dataclass(frozen=True)
class Ms2GrouperMockData:
    """Everything a grouper/association test needs.

    Attributes:
        scans: ``n`` MS2 scan dicts, shuffled. Keys mirror the parser's
            per-spectrum dict / the ``ms2_scans`` columns; ``mz_array`` and
            ``intensity_array`` are plain ``np.ndarray`` (not blobs).
        master_mz: Sorted unique master m/z list (the aligned features).
        features_df: The same list in ``align_mz_across_samples`` shape —
            indexed by ``mz`` with an ``Int64`` ``sample_0`` column.
        planted: ``{name: PlantedCase}`` for ``"single"``, ``"none"``,
            ``"chimeric"``, ``"precursor_only"`` and ``"null_precursor"`` —
            always all five.
        n_decimal_places / align_ppm / assoc_ppm / isolation_half_width /
        min_mz / max_mz: the parameters the batch was built with.
    """

    scans: list[dict]
    master_mz: np.ndarray
    features_df: pd.DataFrame
    planted: dict[str, PlantedCase]
    n_decimal_places: int
    align_ppm: float
    assoc_ppm: float
    isolation_half_width: float
    min_mz: float
    max_mz: float

    def scan_by_id(self, scan_id: int) -> dict:
        for s in self.scans:
            if s["scan_id"] == scan_id:
                return s
        raise KeyError(scan_id)

    def planted_scan(self, name: str) -> dict:
        """The scan dict backing planted case ``name``."""
        return self.scan_by_id(self.planted[name].scan_id)


# ---------------------------------------------------------------------------
# the builder
# ---------------------------------------------------------------------------


def build_ms2_grouper_mock_data(
    n: int = 1000,
    min_mz: float = 100.0,
    max_mz: float = 1000.0,
    n_decimal_places: int = 4,
    *,
    n_features: int | None = None,
    align_ppm: float = 5.0,
    assoc_ppm: float = 5.0,
    isolation_half_width: float = 0.5,
    polarity: str = "+",
    collision_energy: float = 25.0,
    seed: int = 0,
) -> Ms2GrouperMockData:
    """Fabricate ``n`` MS2 spectra + a master m/z list with all edge cases.

    The master list is a grid of well-separated features (gap >=
    ``4 * isolation_half_width``) so a *random* isolation window contains at
    most one feature. Five regions are then carved out and populated by
    hand:

    * ``"single"`` at 20 % of the m/z span — one isolated feature; the
      planted scan's ``precursor_mz`` sits ~3 ppm from it and the isolation
      window contains only that feature.
    * ``"precursor_only"`` at 35 % — one isolated feature; the scan matches
      it but its fragment spectrum is just the surviving precursor.
    * ``"none"`` at 50 % — a feature-free gap wider than any tolerance; the
      planted scan matches nothing.
    * ``"null_precursor"`` at 60 % — one isolated feature; the scan's
      ``precursor_mz`` is null so matching must fall back to
      ``isolation_window_target``.
    * ``"chimeric"`` at 75 % — three features 0.15 Da apart, all inside one
      isolation window; the planted scan's precursor is ~2 ppm from the
      middle one.

    The remaining ``n - 5`` filler scans each point at a random background
    feature with a small (< ``align_ppm``) precursor offset.

    Args:
        n: Number of MS2 spectra to create. Must be >= 1000.
        min_mz: Lower bound of the m/z range spanned by precursors/features.
        max_mz: Upper bound of that range.
        n_decimal_places: Rounding applied to every m/z (matches the
            parser's ``decimal_places``).
        n_features: Size of the background master list. Defaults to
            ``max(50, n // 10)``, clamped so features stay >= one window
            apart.
        align_ppm: Alignment tolerance the master list was built with; also
            the spread of filler precursors around their feature.
        assoc_ppm: Association tolerance the grouper under test will use —
            stored on the result, and the ``"none"`` gap is made far wider
            than this.
        isolation_half_width: Default ``lower``/``upper`` isolation offset
            (Da) put on every planted scan.
        polarity: ``polarity`` value on every scan.
        collision_energy: ``collision_energy`` value on every scan.
        seed: Seed for the filler RNG. The planted cases are deterministic
            regardless of ``seed``.

    Returns:
        An :class:`Ms2GrouperMockData`.

    Example:
        >>> mock = build_ms2_grouper_mock_data(n=1500)
        >>> case = mock.planted["chimeric"]
        >>> case.n_in_window
        3
        >>> sorted(case.ppm_diff_per_window_feature)
        [...]  # three master m/z, each with its signed ppm vs the precursor
    """
    if n < 1000:
        raise ValueError(f"n must be >= 1000 (got {n})")
    if max_mz - min_mz < 50.0:
        raise ValueError("need (max_mz - min_mz) >= 50")

    nd = n_decimal_places
    rng = np.random.default_rng(seed)
    span = max_mz - min_mz
    pad = span * 0.02
    hw = float(isolation_half_width)
    # half-band kept clear of background features around each planted anchor
    guard = max(3.0, 6.0 * hw)

    def r(x: float) -> float:
        return round(float(x), nd)

    # ---------------------------------------------------------------- anchors
    single_feat = r(min_mz + span * 0.20)
    po_feat = r(min_mz + span * 0.35)  # "precursor_only" case
    none_center = r(min_mz + span * 0.50)
    np_feat = r(min_mz + span * 0.60)  # "null_precursor" case
    chim_center = min_mz + span * 0.75
    f0, f1, f2 = r(chim_center - 0.15), r(chim_center), r(chim_center + 0.15)
    planted_feats = [single_feat, po_feat, np_feat, f0, f1, f2]
    anchors = [single_feat, po_feat, none_center, np_feat, chim_center]

    # ------------------------------------------------------------ background
    min_gap = max(2.0, 4.0 * hw)
    max_feats = max(10, int(span / min_gap))
    if n_features is None:
        n_features = max(50, n // 10)
    n_features = min(n_features, max_feats)

    grid = np.linspace(min_mz + pad, max_mz - pad, n_features)
    step = float(grid[1] - grid[0]) if n_features > 1 else span
    jittered = grid + rng.uniform(-step / 4.0, step / 4.0, size=grid.size)
    background = [r(x) for x in jittered if all(abs(x - a) > guard for a in anchors)]
    master_list = sorted(set(background) | set(planted_feats))
    master_mz = np.asarray(master_list, dtype=float)

    chimeric_trio = {f0, f1, f2}
    filler_feats = np.asarray(
        [m for m in master_list if m not in chimeric_trio], dtype=float
    )
    if filler_feats.size == 0:  # pragma: no cover - defensive
        filler_feats = np.asarray([single_feat], dtype=float)

    # ------------------------------------------------------------- factories
    def fragments(around_mz: float) -> tuple[np.ndarray, np.ndarray]:
        k = int(rng.integers(6, 60))
        hi = max(around_mz - 1.0, 60.0)
        mz = np.round(np.sort(rng.uniform(50.0, hi, size=k)), nd)
        inten = rng.uniform(1e2, 1e6, size=k).astype(float)
        return mz, inten

    def make_scan(
        scan_id: int,
        precursor_mz: float | None,
        target: float,
        lower: float,
        upper: float,
        *,
        mz_array: np.ndarray | None = None,
        int_array: np.ndarray | None = None,
    ) -> dict:
        if mz_array is None:
            mz_arr, int_arr = fragments(precursor_mz if precursor_mz else target)
        else:
            mz_arr = np.asarray(mz_array, dtype=float)
            int_arr = np.asarray(int_array, dtype=float)
        return {
            "scan_id": scan_id,
            "parent_scan_id": None,
            "polarity": polarity,
            "rt": r(60.0 + scan_id * 0.25),
            "filter_string": (
                f"FTMS {polarity} p NSI Full ms2 "
                f"{target:.4f}@hcd{collision_energy:.2f} "
                f"[50.0000-{max_mz:.4f}]"
            ),
            "precursor_mz": precursor_mz,
            "precursor_charge": 1,
            "precursor_intensity": r(rng.uniform(1e4, 1e7)),
            "isolation_window_target": r(target),
            "isolation_window_lower": r(lower),
            "isolation_window_upper": r(upper),
            "collision_energy": collision_energy,
            "n_peaks": int(mz_arr.size),
            "tic": r(float(int_arr.sum())),
            "mz_array": mz_arr,
            "intensity_array": int_arr,
        }

    def window_features(target: float, lower: float, upper: float) -> list[float]:
        lo, hi = target - lower, target + upper
        return [m for m in master_list if lo <= m <= hi]

    def nearest_other_ppm(precursor_mz: float | None, chosen: float | None):
        if precursor_mz is None:
            return None
        others = [m for m in master_list if m != chosen]
        if not others:
            return None
        closest = min(others, key=lambda m: abs(m - precursor_mz))
        return ppm_between(precursor_mz, closest)

    scans: list[dict] = []
    planted: dict[str, PlantedCase] = {}

    # -- case 1: associated to exactly one master m/z -----------------------
    p1 = r(_mz_at_ppm(single_feat, 3.0))
    scans.append(make_scan(1, p1, single_feat, hw, hw))
    win1 = window_features(single_feat, hw, hw)
    planted["single"] = PlantedCase(
        name="single",
        scan_id=1,
        description=(
            "precursor_mz ~3 ppm from exactly one master m/z; the isolation "
            "window contains only that feature."
        ),
        precursor_mz=p1,
        isolation_window=(r(single_feat - hw), r(single_feat + hw)),
        features_in_window=win1,
        ppm_diff_per_window_feature={m: ppm_between(p1, m) for m in win1},
        n_in_window=len(win1),
        expected_feature_mz=single_feat,
        expected_ppm_offset=ppm_between(p1, single_feat),
        nearest_other_feature_ppm=nearest_other_ppm(p1, single_feat),
    )

    # -- case 2: associated to nothing -------------------------------------
    p2 = none_center
    scans.append(make_scan(2, p2, none_center, hw, hw))
    win2 = window_features(none_center, hw, hw)
    planted["none"] = PlantedCase(
        name="none",
        scan_id=2,
        description=(
            "precursor_mz sits in a feature-free gap; no master m/z lies "
            "inside the isolation window or within assoc_ppm."
        ),
        precursor_mz=p2,
        isolation_window=(r(none_center - hw), r(none_center + hw)),
        features_in_window=win2,
        ppm_diff_per_window_feature={m: ppm_between(p2, m) for m in win2},
        n_in_window=len(win2),
        expected_feature_mz=None,
        expected_ppm_offset=None,
        nearest_other_feature_ppm=nearest_other_ppm(p2, None),
    )

    # -- case 3: several master m/z inside one isolation window ------------
    p3 = r(_mz_at_ppm(f1, 2.0))
    scans.append(make_scan(3, p3, f1, hw, hw))
    win3 = window_features(f1, hw, hw)
    ppm3 = {m: ppm_between(p3, m) for m in win3}
    best3 = min(win3, key=lambda m: abs(ppm3[m]))
    planted["chimeric"] = PlantedCase(
        name="chimeric",
        scan_id=3,
        description=(
            "three master m/z 0.15 Da apart fall inside one isolation "
            "window; the grouper must record a per-feature ppm diff + flag "
            "for each and pick the nearest (the middle one) as primary."
        ),
        precursor_mz=p3,
        isolation_window=(r(f1 - hw), r(f1 + hw)),
        features_in_window=win3,
        ppm_diff_per_window_feature=ppm3,
        n_in_window=len(win3),
        expected_feature_mz=best3,
        expected_ppm_offset=ppm3[best3],
        nearest_other_feature_ppm=nearest_other_ppm(p3, best3),
    )

    # -- case 4: fragmentation did not occur (precursor-only spectrum) -----
    p4 = r(_mz_at_ppm(po_feat, 3.0))
    # one dominant peak sitting on the precursor + two tiny noise peaks
    po_mz = np.round(
        np.array([min_mz + span * 0.11, min_mz + span * 0.19, p4]), nd
    )
    po_int = np.array([300.0, 450.0, 1.0e5])
    scans.append(make_scan(4, p4, po_feat, hw, hw, mz_array=po_mz, int_array=po_int))
    win4 = window_features(po_feat, hw, hw)
    planted["precursor_only"] = PlantedCase(
        name="precursor_only",
        scan_id=4,
        description=(
            "fragment spectrum is essentially just the surviving precursor "
            "(>98 % of the TIC within 2 Da of precursor_mz); the scan still "
            "associates to its feature but must get a low fragmentation_factor."
        ),
        precursor_mz=p4,
        isolation_window=(r(po_feat - hw), r(po_feat + hw)),
        features_in_window=win4,
        ppm_diff_per_window_feature={m: ppm_between(p4, m) for m in win4},
        n_in_window=len(win4),
        expected_feature_mz=po_feat,
        expected_ppm_offset=ppm_between(p4, po_feat),
        nearest_other_feature_ppm=nearest_other_ppm(p4, po_feat),
        expected_low_fragmentation=True,
    )

    # -- case 5: precursor_mz is null -> fall back to isolation target -----
    np_target = r(_mz_at_ppm(np_feat, 3.0))
    scans.append(make_scan(5, None, np_target, hw, hw))
    win5 = window_features(np_target, hw, hw)
    planted["null_precursor"] = PlantedCase(
        name="null_precursor",
        scan_id=5,
        description=(
            "precursor_mz is null; the grouper must fall back to "
            "isolation_window_target as the match key."
        ),
        precursor_mz=None,
        isolation_window=(r(np_target - hw), r(np_target + hw)),
        features_in_window=win5,
        ppm_diff_per_window_feature={m: ppm_between(np_target, m) for m in win5},
        n_in_window=len(win5),
        expected_feature_mz=np_feat,
        expected_ppm_offset=ppm_between(np_target, np_feat),
        nearest_other_feature_ppm=None,
        expected_match_key="isolation_window_target",
    )

    # planted invariants — fail loudly here rather than deep inside a test
    assert win1 == [single_feat], win1
    assert win2 == [], win2
    assert win3 == [f0, f1, f2], win3
    assert best3 == f1, best3
    assert win4 == [po_feat], win4
    assert win5 == [np_feat], win5

    # -- filler scans -----------------------------------------------------
    for scan_id in range(6, n + 1):
        feat = float(rng.choice(filler_feats))
        sign = 1.0 if rng.random() < 0.5 else -1.0
        off_ppm = float(rng.uniform(0.5, align_ppm * 0.8))
        precursor = r(_mz_at_ppm(feat, sign * off_ppm))
        target = r(feat + rng.uniform(-0.05, 0.05))
        scans.append(make_scan(scan_id, precursor, target, hw, hw))

    order = rng.permutation(len(scans))
    scans = [scans[i] for i in order]

    features_df = pd.DataFrame(
        {"sample_0": np.arange(master_mz.size)},
        index=pd.Index(np.round(master_mz, nd), name="mz"),
    )
    features_df["sample_0"] = features_df["sample_0"].astype("Int64")

    return Ms2GrouperMockData(
        scans=scans,
        master_mz=master_mz,
        features_df=features_df,
        planted=planted,
        n_decimal_places=nd,
        align_ppm=align_ppm,
        assoc_ppm=assoc_ppm,
        isolation_half_width=hw,
        min_mz=min_mz,
        max_mz=max_mz,
    )


# ---------------------------------------------------------------------------
# optional: materialise the scans into a real raw-schema SQLite ms2 DB
# ---------------------------------------------------------------------------


def materialize_ms2_db(db_path, mock: Ms2GrouperMockData, *, compressed: bool = True):
    """Write ``mock.scans`` into a SQLite DB using the canonical raw schema.

    Handy for tests that exercise the grouper end-to-end against a file
    rather than in-memory dicts. Reuses ``create_raw_schema`` /
    ``array_to_blob`` from the parser so the fixture can't drift from the
    production schema.

    Returns:
        ``db_path`` (as passed in).
    """
    from msianalyzer.core.parser.mzml_parser import array_to_blob, init_raw_db

    con = init_raw_db(db_path)
    try:
        con.executemany(
            """
            INSERT INTO ms2_scans
              (scan_id, parent_scan_id, polarity, rt, filter_string,
               precursor_mz, precursor_charge, precursor_intensity,
               isolation_window_target, isolation_window_lower,
               isolation_window_upper, collision_energy, n_peaks, tic,
               mz_array, intensity_array)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                (
                    s["scan_id"],
                    s["parent_scan_id"],
                    s["polarity"],
                    s["rt"],
                    s["filter_string"],
                    s["precursor_mz"],
                    s["precursor_charge"],
                    s["precursor_intensity"],
                    s["isolation_window_target"],
                    s["isolation_window_lower"],
                    s["isolation_window_upper"],
                    s["collision_energy"],
                    s["n_peaks"],
                    s["tic"],
                    array_to_blob(s["mz_array"], mock.n_decimal_places, compressed),
                    array_to_blob(
                        s["intensity_array"], mock.n_decimal_places, compressed
                    ),
                )
                for s in mock.scans
            ],
        )
        con.commit()
    finally:
        con.close()
    return db_path


# ---------------------------------------------------------------------------
# optional: a tiny libviz spectral library that "knows" one planted case
# ---------------------------------------------------------------------------


def build_mock_library(
    db_path,
    mock: Ms2GrouperMockData,
    *,
    case: str = "single",
    n_decoys: int = 3,
    jitter_ppm: float = 1.5,
    seed: int = 0,
):
    """Write a small libviz library that contains the ``case`` scan's spectrum.

    The "true" compound's fragment list is the planted scan's own
    ``mz_array`` / ``intensity_array`` shifted by ``jitter_ppm`` and its
    ``precursor_mz`` is the feature the grouper will snap that scan to
    (``planted[case].expected_feature_mz``). ``n_decoys`` random spectra are
    added at nearby precursor m/z so candidate gathering has something to
    rank the true hit against.

    Returns:
        ``db_path`` (as passed in).
    """
    from libviz.core.library import Library

    rng = np.random.default_rng(seed)
    planted = mock.planted[case]
    scan = mock.scan_by_id(planted.scan_id)
    true_mz = np.asarray(scan["mz_array"], dtype=float) * (1.0 + jitter_ppm / 1e6)
    true_int = np.asarray(scan["intensity_array"], dtype=float)
    precursor = float(planted.expected_feature_mz)

    lib = Library.create(Path(db_path), name=f"mock-{case}")
    adduct_id = next(a["id"] for a in lib.get_adducts() if a["charge"] > 0)

    lib.add_spectra(
        compound_name="TrueCompound",
        compound_formula="C10H15N5O10P2",
        inchikey="AAAAAAAAAAAAAA-BBBBBBBBBB-N",
        spectra_info={
            "precursor_mz": precursor,
            "polarity": "POSITIVE",
            "collision_energy": float(scan["collision_energy"]),
            "mz": true_mz,
            "intensity": true_int,
        },
        adduct_id=adduct_id,
    )

    for k in range(n_decoys):
        off_ppm = float(rng.uniform(-4.0, 4.0))
        n_pk = int(rng.integers(8, 40))
        d_mz = np.sort(rng.uniform(50.0, max(precursor - 1.0, 60.0), size=n_pk))
        d_int = rng.uniform(1e2, 1e6, size=n_pk)
        lib.add_spectra(
            compound_name=f"Decoy{k}",
            compound_formula="C6H12O6",
            inchikey=f"DECOY{k:08d}AAAAA-CCCCCCCCCC-N",
            spectra_info={
                "precursor_mz": precursor * (1.0 + off_ppm / 1e6),
                "polarity": "POSITIVE",
                "collision_energy": float(scan["collision_energy"]),
                "mz": d_mz,
                "intensity": d_int,
            },
            adduct_id=adduct_id,
        )

    return db_path


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ms2_grouper_mock_data():
    """Factory fixture -> :func:`build_ms2_grouper_mock_data`.

    Call it in a test to build a fresh batch; every call plants the
    ``"single"``, ``"none"`` and ``"chimeric"`` cases::

        def test_ms2_associated_to_single_mz(ms2_grouper_mock_data):
            mock = ms2_grouper_mock_data(n=1000)
            case = mock.planted["single"]
            scan = mock.planted_scan("single")
            ...  # run the grouper, assert it picks case.expected_feature_mz
    """
    return build_ms2_grouper_mock_data


@pytest.fixture
def make_ms2_db(tmp_path):
    """Factory fixture -> writes a mock batch to a temp raw-schema SQLite DB.

    def test_x(ms2_grouper_mock_data, make_ms2_db):
        mock = ms2_grouper_mock_data()
        db = make_ms2_db(mock)          # -> Path to <tmp>/mock_ms2.db
    """

    def _make(mock: Ms2GrouperMockData, name: str = "mock_ms2.db"):
        return materialize_ms2_db(tmp_path / name, mock)

    return _make


@pytest.fixture
def make_library_db(tmp_path):
    """Factory fixture -> writes a mock libviz library under ``tmp_path``.

    def test_x(ms2_grouper_mock_data, make_library_db):
        mock = ms2_grouper_mock_data()
        lib = make_library_db(mock, case="single")   # -> Path to <tmp>/mock_lib.db
    """

    def _make(mock: Ms2GrouperMockData, name: str = "mock_lib.db", **kwargs):
        return build_mock_library(tmp_path / name, mock, **kwargs)

    return _make
