"""MS2 cleaning (ADR 0064): background fragments counted per precursor,
fragments above the precursor, and the stored cleaner."""

import sqlite3

import numpy as np

from msianalyzer.core.analysis_db import create_analysis_schema
from msianalyzer.core.annotation.ms2_cleaning import (
    Ms2Cleaner,
    clean_scans,
    count_background,
    load_cleaner,
    persist_cleaning,
    precursor_keys,
)
from msianalyzer.core.config.config import Ms2CleaningConfig


def test_counts_precursors_not_scans():
    # Precursor 1 has 10 scans with 150.0; precursor 2 one scan with 300.0
    # and 150.0. Per scan, 150.0 would be in 11/11; it's in 2/2 precursors,
    # and 300.0 in 1/2 — repeated scans don't add up.
    arrays = [np.array([150.0])] * 10 + [np.array([150.0005, 300.0])]
    keys = np.array([1] * 10 + [2])
    bg = count_background(arrays, keys, ppm=10.0)
    assert bg.total == 2
    counts = dict(zip(np.round(bg.mz), bg.n_precursors))
    assert counts == {150.0: 2, 300.0: 1}


def test_a_chain_of_noise_peaks_is_cut():
    # Peaks 5 ppm apart over 200 ppm chain into one gap-split cluster; it
    # must be cut so one precursor's far ends don't count as the same fragment.
    chain = 500.0 * (1 + np.arange(40) * 5e-6)
    bg = count_background([chain], np.array([1]), ppm=10.0)
    assert bg.mz.size > 5
    assert bg.n_precursors.max() == 1


def test_precursor_keys_prefer_the_nearest_feature():
    keys, ref = precursor_keys(
        np.array([200.0010, 200.0040, 900.0]),
        features=np.array([200.0, 200.004]), feature_ids=np.array([7, 8]),
        assoc_ppm=10.0,
    )
    assert list(keys[:2]) == [7, 8]
    assert keys[2] < 0  # no feature: a negative m/z bin
    assert ref[1] == 200.004 and ref[2] == 900.0


def test_above_precursor_skips_multiply_charged():
    cleaner = Ms2Cleaner(above_precursor_enabled=True, above_precursor_ppm=10.0)
    mz = np.array([100.0, 500.0, 500.004, 501.003])
    assert list(cleaner.keep_mask(mz, None, 500.0)) == [True, True, True, False]
    assert cleaner.keep_mask(mz, None, 500.0, charge=2).all()


def test_background_removal_only_when_enabled():
    removed = {"POSITIVE": np.array([184.0733])}
    mz = np.array([184.0735, 200.0])
    on = Ms2Cleaner(background_enabled=True, background_ppm=10.0, removed=removed)
    assert list(on.keep_mask(mz, "POSITIVE", None)) == [False, True]
    assert on.keep_mask(mz, "NEGATIVE", None).all()  # per polarity
    off = Ms2Cleaner(background_enabled=False, removed=removed)
    assert off.keep_mask(mz, "POSITIVE", None).all()


def _scans():
    # 4 precursors (features 1-4), each with 184.0733 (background) and its
    # own fragment, plus a peak above its precursor.
    scans = []
    for fid, prec in enumerate((300.0, 400.0, 500.0, 600.0), start=1):
        scans.append({
            "precursor_mz": prec, "polarity": "positive", "precursor_charge": 1,
            "mz_array": np.array([184.0733, prec - 100, prec + 50]),
            "intensity_array": np.array([1.0, 2.0, 3.0]),
            "n_peaks": 3,
        })
    return scans


def test_clean_scans_lists_the_table_and_cleans_in_place():
    scans = _scans()
    settings = Ms2CleaningConfig(
        background_enabled=True, background_min_percent=75.0,
        above_precursor_enabled=True,
    )
    cleaner, rows = clean_scans(
        scans, np.array([300.0, 400.0, 500.0, 600.0]), np.array([1, 2, 3, 4]),
        assoc_ppm=10.0, settings=settings,
    )
    removed = [r for r in rows if r[4]]
    assert len(removed) == 1 and round(removed[0][1], 4) == 184.0733
    assert removed[0][2] == 4 and removed[0][3] == 1.0
    # The per-precursor fragments (1 of 4 = 25%) are listed, not removed.
    assert sum(not r[4] for r in rows) == 8
    assert [list(s["mz_array"]) for s in scans][0] == [200.0]
    assert scans[0]["n_peaks"] == 1


def test_table_is_computed_even_with_removal_off():
    scans = _scans()
    _, rows = clean_scans(
        scans, np.array([300.0, 400.0, 500.0, 600.0]), np.array([1, 2, 3, 4]),
        assoc_ppm=10.0, settings=Ms2CleaningConfig(),
    )
    assert any(r[4] for r in rows)  # would be removed at 50%
    assert scans[0]["n_peaks"] == 3  # but nothing is


def test_stored_cleaner_round_trips(tmp_path):
    db = tmp_path / "a.db"
    settings = Ms2CleaningConfig(background_enabled=True, above_precursor_enabled=True,
                                 above_precursor_ppm=5.0)
    with sqlite3.connect(db) as con:
        create_analysis_schema(con)
        persist_cleaning(con, settings, [("POSITIVE", 184.07, 4, 1.0, True),
                                         ("POSITIVE", 200.0, 1, 0.25, False)])
    cleaner = load_cleaner(db)
    assert cleaner.background_enabled and cleaner.above_precursor_ppm == 5.0
    assert list(cleaner.removed["POSITIVE"]) == [184.07]


def test_no_cleaning_table_means_no_cleaning(tmp_path):
    db = tmp_path / "old.db"
    sqlite3.connect(db).close()
    assert not load_cleaner(db).active
