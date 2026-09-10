from pathlib import Path
import numpy as np
import pytest

from msianalyzer.core.spectra.average_spectra import (
    _estimate_baseline,
    _infer_decimal_places,
    _merge_peaks_ppm,
    detect_ms1_centroids,
    filter_intensities_mad,
    get_average_ms1_spectra,
    load_aggregated_spectra,
    save_aggregated_spectra,
)

MODULE_PATH = "msianalyzer.core.spectra.average_spectra"


# ==============================================================================
# SYNTHETIC DATA GENERATORS FOR COMPLEX SPECTRA
# ==============================================================================


def generate_complex_spectrum(
    min_mz: float = 100.0,
    max_mz: float = 500.0,
    n_points: int = 20000,
    seed: int = 42,
):
    """
    Generates a realistic mass spectrum containing:
    1. A curving background baseline.
    2. Random Gaussian noise.
    3. Multiple peaks: strong single peaks, weak peaks, and a resolvable doublet.
    """
    np.random.seed(seed)
    bin_centers = np.linspace(min_mz, max_mz, n_points)

    # Non-linear background
    baseline = (
        100.0
        + 300.0 * np.sin(np.pi * (bin_centers - min_mz) / (max_mz - min_mz))
        + 50.0 * np.exp(-(bin_centers - 200.0) ** 2 / 5000.0)
    )

    noise = np.random.normal(0, 5.0, size=n_points)
    intensities = np.clip(baseline + noise, a_min=0, a_max=None)

    # Defined peaks: (mz_center, amplitude, width_bins)
    peaks = [
        (120.0, 1500.0, 3),   # Strong isolated peak
        (220.0, 600.0, 3),    # First peak of doublet
        (220.25, 550.0, 3),   # Second peak of doublet (~1136 PPM apart)
        (350.0, 250.0, 3),    # Weak peak on high baseline
        (450.0, 2000.0, 4),   # Strong peak on low baseline
    ]

    for mz_c, amp, width in peaks:
        idx = np.argmin(np.abs(bin_centers - mz_c))
        gauss = amp * np.exp(-0.5 * ((np.arange(n_points) - idx) / width) ** 2)
        intensities += gauss

    return bin_centers, intensities, baseline


# ==============================================================================
# 1. DATABASE MOCK TESTS
# ==============================================================================


def test_get_average_ms1_spectra_multi_chunk(mocker):
    """Tests streaming bin accumulation across multiple DB data chunks."""
    mock_conn = mocker.MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_cursor = mocker.MagicMock()
    mocker.patch(f"{MODULE_PATH}.sqlite3.connect", return_value=mock_conn)
    mock_conn.cursor.return_value = mock_cursor

    mock_cursor.fetchone.return_value = [5]  # n_pixels = 5

    chunk1_mz = np.array([100.0, 200.0, 300.0])
    chunk1_int = np.array([50.0, 100.0, 150.0])

    chunk2_mz = np.array([100.0, 200.0, 400.0])
    chunk2_int = np.array([25.0, 75.0, 200.0])

    mock_cursor.fetchmany.side_effect = [
        [("blob1_mz", "blob1_int")],
        [("blob2_mz", "blob2_int")],
        [],
    ]

    mocker.patch(
        f"{MODULE_PATH}.blob_to_array",
        side_effect=[chunk1_mz, chunk1_int, chunk2_mz, chunk2_int],
    )

    bin_centers, mean_intensities = get_average_ms1_spectra(
        db_path="test_db.db",
        chunk_size=1,
        bin_width=1.0,
        min_mz=100.0,
        max_mz=500.0,
    )

    assert len(bin_centers) == 400
    assert mean_intensities[0] == pytest.approx(15.0)


def _norm(sql: str) -> str:
    return " ".join(sql.split())


def test_save_aggregated_spectra_targets_analysis_db(mocker):
    """save_aggregated_spectra inserts run_id, sample_id and command_id."""
    mock_conn = mocker.MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_cursor = mocker.MagicMock()
    mocker.patch(f"{MODULE_PATH}._analysis_connect", return_value=mock_conn)
    mock_conn.cursor.return_value = mock_cursor

    mocker.patch(
        f"{MODULE_PATH}.array_to_blob",
        side_effect=[b"mz_blob", b"int_blob"],
    )

    save_aggregated_spectra(
        mzs_array=np.array([100.0, 200.0]),
        intensities_array=np.array([10.0, 20.0]),
        analysis_db_path="analysis.db",
        run_id="run_abc123",
        sample_id=7,
        command_id=99,
    )

    # the INSERT runs through utils.db.safe_execute -> conn.execute; and the
    # function issues NO DDL (concurrent CREATE TABLE corrupts the shared DB)
    insert_calls = [
        c
        for c in mock_conn.execute.call_args_list
        if "INSERT" in _norm(c.args[0])
    ]
    assert len(insert_calls) == 1
    assert not any(
        "CREATE" in _norm(c.args[0]) for c in mock_conn.execute.call_args_list
    )
    sql, params = insert_calls[0].args
    assert _norm(sql) == _norm(
        "INSERT INTO aggregated_spectra "
        "(run_id, sample_id, command_id, mz_array, intensity_array) "
        "VALUES (?, ?, ?, ?, ?);"
    )
    assert params == ("run_abc123", 7, 99, b"mz_blob", b"int_blob")
    mock_conn.commit.assert_called_once()


def test_load_aggregated_spectra_filters_by_sample(mocker):
    """load_aggregated_spectra joins commands and filters on sample_id."""
    mock_conn = mocker.MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_cursor = mocker.MagicMock()
    mock_conn.execute.return_value = mock_cursor
    mocker.patch(f"{MODULE_PATH}._analysis_connect", return_value=mock_conn)

    mock_cursor.fetchone.return_value = (b"mz_blob", b"int_blob")
    expected_mzs = np.array([100.0, 200.0])
    expected_ints = np.array([500.0, 1000.0])

    mocker.patch(
        f"{MODULE_PATH}.blob_to_array",
        side_effect=[expected_mzs, expected_ints],
    )

    mzs, intensities = load_aggregated_spectra(
        analysis_db_path="analysis.db",
        run_id="run_abc123",
        command_name="detect_ms1_centroids",
        sample_id=7,
    )

    np.testing.assert_array_equal(mzs, expected_mzs)
    np.testing.assert_array_equal(intensities, expected_ints)

    sql, params = mock_conn.execute.call_args.args
    norm_sql = _norm(sql)
    assert "JOIN commands" in norm_sql
    assert "c.command_name = ? AND c.run_id = ? AND a.sample_id = ?" in norm_sql
    assert params == ("detect_ms1_centroids", "run_abc123", 7)


# ==============================================================================
# 2. PARAMETER SENSITIVITY TESTS ON COMPLEX SPECTRA
# ==============================================================================


@pytest.mark.parametrize("bin_width", [0.01, 0.001])
def test_get_average_ms1_spectra_bin_width_resolution(mocker, bin_width):
    """Verifies that smaller bin_width results in higher array resolution."""
    mock_conn = mocker.MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_cursor = mocker.MagicMock()
    mocker.patch(f"{MODULE_PATH}.sqlite3.connect", return_value=mock_conn)
    mock_conn.cursor.return_value = mock_cursor

    mock_cursor.fetchone.return_value = [1]
    mock_cursor.fetchmany.side_effect = [
        [("mz_blob", "int_blob")],
        [],
    ]

    mocker.patch(
        f"{MODULE_PATH}.blob_to_array",
        side_effect=[np.array([100.5, 101.5]), np.array([100.0, 200.0])],
    )

    bin_centers, _ = get_average_ms1_spectra(
        db_path="test.db",
        bin_width=bin_width,
        min_mz=100.0,
        max_mz=200.0,
    )

    expected_bins = int(np.ceil((200.0 - 100.0) / bin_width))
    assert len(bin_centers) == expected_bins


def test_detect_ms1_centroids_local_vs_global_baseline():
    """
    Demonstrates that 'local' baseline method adaptively tracks non-uniform background
    better than 'global' baseline on complex spectra.
    """
    bin_centers, intensities, _ = generate_complex_spectrum()

    mzs_local, _ = detect_ms1_centroids(
        bin_centers,
        intensities,
        baseline_method="local",
        baseline_factor=0.8,
        local_window=501,
        merge_ppm=10.0,
    )

    mzs_global, _ = detect_ms1_centroids(
        bin_centers,
        intensities,
        baseline_method="global",
        baseline_factor=0.8,
        merge_ppm=10.0,
    )

    assert len(mzs_local) != len(mzs_global)


@pytest.mark.parametrize(
    "baseline_factor, min_expected_peaks",
    [
        (0.5, 3),   # Low factor: detects more peaks
        (10.0, 1),   # High factor: detects fewer peaks
    ],
)
def test_detect_ms1_centroids_baseline_factor_sensitivity(
    baseline_factor, min_expected_peaks
):
    """Verifies that increasing baseline_factor reduces peak detections."""
    bin_centers, intensities, _ = generate_complex_spectrum()

    mzs, _ = detect_ms1_centroids(
        bin_centers,
        intensities,
        baseline_method="local",
        baseline_factor=baseline_factor,
        local_window=501,
    )

    if baseline_factor == 0.5:
        assert len(mzs) >= min_expected_peaks
    else:
        assert len(mzs) <= min_expected_peaks


@pytest.mark.parametrize(
    "merge_ppm, expect_merged",
    [
        (10.0, False),    # 10 PPM keeps 220.0 and 220.25 (~1136 PPM apart) separate
        (2000.0, True),   # 2000 PPM merges 220.0 and 220.25 into a single peak
    ],
)
def test_detect_ms1_centroids_merge_ppm_sensitivity(merge_ppm, expect_merged):
    """Tests peak merging on doublets (220.0 and 220.25 m/z)."""
    bin_centers, intensities, _ = generate_complex_spectrum()

    mzs, _ = detect_ms1_centroids(
        bin_centers,
        intensities,
        baseline_method="local",
        baseline_factor=0.5,
        merge_ppm=merge_ppm,
    )

    mzs_near_220 = [mz for mz in mzs if 219.5 <= mz <= 220.5]

    if expect_merged:
        assert len(mzs_near_220) == 1
    else:
        assert len(mzs_near_220) == 2


def test_filter_intensities_mad_n_mads_sensitivity():
    """Verifies that higher n_mads produces strictly more conservative filtering."""
    np.random.seed(42)
    bg_ints = np.random.lognormal(mean=2.0, sigma=0.5, size=1000)
    signal_ints = np.array([500.0, 800.0, 1200.0, 2000.0, 5000.0])
    all_ints = np.concatenate([bg_ints, signal_ints])
    all_mzs = np.linspace(100.0, 500.0, len(all_ints))

    mzs_lax, _ = filter_intensities_mad(all_mzs, all_ints, log=True, n_mads=1.0)
    mzs_strict, _ = filter_intensities_mad(all_mzs, all_ints, log=True, n_mads=5.0)

    # Higher n_mads must yield fewer or equal peaks
    assert len(mzs_strict) < len(mzs_lax)


def test_filter_intensities_mad_log_vs_linear():
    """Compares log-transformed vs linear MAD filtering on exponentially skewed data."""
    np.random.seed(42)
    intensities = np.concatenate(
        [np.random.exponential(scale=10.0, size=500), np.array([1000.0, 2000.0])]
    )
    mzs = np.arange(len(intensities), dtype=float)

    mzs_log, _ = filter_intensities_mad(mzs, intensities, log=True, n_mads=2.0)
    mzs_lin, _ = filter_intensities_mad(mzs, intensities, log=False, n_mads=2.0)

    assert len(mzs_log) != len(mzs_lin)



@pytest.mark.parametrize(
    "bin_centers, expected_decimals",
    [
        (np.array([]), 4),  # Empty array fallback
        (np.array([100.0]), 4),  # Single element fallback
        (np.array([100.0, 101.0, 102.0]), 0),  # Integer spacing (1.0)
        (np.array([100.00, 100.01, 100.02]), 2),  # Spacing 0.01
        (np.array([100.000, 100.001, 100.002]), 3),  # Spacing 0.001
        (np.array([100.0000, 100.0001, 100.0002]), 4),  # Spacing 0.0001
    ],
)
def test_infer_decimal_places(bin_centers, expected_decimals):
    assert _infer_decimal_places(bin_centers) == expected_decimals


def test_estimate_baseline_all_zeros():
    zeros = np.zeros(100)

    # Global mode on all zeros should return scalar 0.0 array
    b_global = _estimate_baseline(zeros, method="global")
    np.testing.assert_array_equal(b_global, np.array(0.0))

    # Local mode on all zeros should return zero array matching input shape
    b_local = _estimate_baseline(zeros, method="local")
    np.testing.assert_array_equal(b_local, np.zeros(100))


def test_estimate_baseline_global():
    data = np.array([0, 0, 10, 20, 30, 40, 50, 100], dtype=float)
    
    # Positive values: [10, 20, 30, 40, 50, 100]
    # 10th percentile of positives
    expected_p10 = np.percentile([10, 20, 30, 40, 50, 100], 10.0)

    res = _estimate_baseline(data, method="global", percentile=10.0)
    assert res.shape == ()
    assert pytest.approx(res.item()) == expected_p10


def test_estimate_baseline_local_smoothing_and_windows():
    # Array with a step change in baseline level
    intensities = np.concatenate([np.ones(500) * 10.0, np.ones(500) * 100.0])

    res = _estimate_baseline(
        intensities,
        method="local",
        percentile=10.0,
        local_window=51,
        smooth_sigma=5.0,
    )

    # Check output shape and type
    assert res.shape == intensities.shape
    assert isinstance(res, np.ndarray)

    # Local baseline should adaptively increase from left region to right region
    assert res[100] < res[900]
    assert res[100] == pytest.approx(10.0, abs=2.0)
    assert res[900] == pytest.approx(100.0, abs=2.0)


def test_estimate_baseline_local_handles_nans_and_sparse_data():
    # Mostly zeros with sparse non-zero entries
    intensities = np.zeros(100)
    intensities[50] = 100.0

    res = _estimate_baseline(
        intensities,
        method="local",
        percentile=10.0,
        local_window=201,  # Window larger than array size
    )

    # NaN-filling logic should ensure no NaNs/Infs remain in output
    assert not np.isnan(res).any()
    assert not np.isinf(res).any()
    assert res.shape == (100,)


def test_merge_peaks_ppm_no_merging_needed():
    # Well-separated peaks (> 1000 PPM apart)
    mzs = np.array([100.0, 200.0, 300.0])
    ints = np.array([10.0, 20.0, 30.0])

    out_mz, out_int = _merge_peaks_ppm(mzs, ints, ppm=5.0)

    np.testing.assert_array_equal(out_mz, mzs)
    np.testing.assert_array_equal(out_int, ints)


def test_merge_peaks_ppm_keeps_highest_intensity_peak():
    # 100.0000 and 100.0001 (1 PPM apart)
    mzs = np.array([100.0000, 100.0001])
    ints = np.array([50.0, 500.0])  # Second peak is 10x stronger

    out_mz, out_int = _merge_peaks_ppm(mzs, ints, ppm=5.0)

    assert len(out_mz) == 1
    assert out_mz[0] == 100.0001
    assert out_int[0] == 500.0


def test_merge_peaks_ppm_greedy_intensity_ordering():
    # Three peaks within 5 PPM of each other:
    # A: 100.0000 (int: 10)
    # B: 100.0001 (int: 100) -> Highest intensity, should be selected first
    # C: 100.0002 (int: 20)
    mzs = np.array([100.0000, 100.0001, 100.0002])
    ints = np.array([10.0, 100.0, 20.0])

    out_mz, out_int = _merge_peaks_ppm(mzs, ints, ppm=5.0)

    # B removes both A and C
    assert len(out_mz) == 1
    assert out_mz[0] == 100.0001
    assert out_int[0] == 100.0


def test_merge_peaks_ppm_output_sorted_by_mz():
    # Unsorted input peaks
    mzs = np.array([300.0, 100.0001, 100.0000, 200.0])
    ints = np.array([10.0, 500.0, 50.0, 20.0])

    out_mz, out_int = _merge_peaks_ppm(mzs, ints, ppm=5.0)

    # 100.0000 merged into 100.0001 (higher intensity)
    # Resulting array must be sorted ascending by m/z
    np.testing.assert_array_equal(out_mz, np.array([100.0001, 200.0, 300.0]))
    np.testing.assert_array_equal(out_int, np.array([500.0, 20.0, 10.0]))