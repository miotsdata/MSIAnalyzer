import numpy as np
import pandas as pd
import pytest

from msianalyzer.core.spectra.mz_tools import align_mz_across_samples


# ==============================================================================
# 1. Edge Cases & Empty Inputs
# ==============================================================================


def test_empty_input_list():
    """Test behavior when passed an empty list of m/z arrays."""
    df = align_mz_across_samples([])

    assert isinstance(df, pd.DataFrame)
    assert df.empty
    assert len(df.columns) == 0


def test_list_of_empty_arrays():
    """Test behavior when passed arrays that contain no m/z values."""
    mz_arrays = [np.array([]), np.array([])]
    df = align_mz_across_samples(mz_arrays)

    assert isinstance(df, pd.DataFrame)
    assert df.empty
    assert list(df.columns) == ["sample_0", "sample_1"]


def test_single_sample_single_peak():
    """Test alignment with a single sample containing one m/z value."""
    mz_arrays = [np.array([100.12345])]
    df = align_mz_across_samples(mz_arrays, mz_decimals=4)

    assert len(df) == 1
    assert df.index[0] == pytest.approx(100.1235, abs=0.0002)
    assert df.loc[100.1234, "sample_0"] == 0


# ==============================================================================
# 2. Defaults & Naming Configurations
# ==============================================================================


def test_default_sample_names():
    """Verify default column names are auto-generated as sample_0, sample_1, etc."""
    mz_arrays = [np.array([100.0]), np.array([100.0]), np.array([100.0])]
    df = align_mz_across_samples(mz_arrays)

    assert list(df.columns) == ["sample_0", "sample_1", "sample_2"]


def test_custom_sample_names():
    """Verify custom sample names are applied correctly."""
    mz_arrays = [np.array([100.0]), np.array([100.0])]
    custom_names = ["Control", "Treatment"]
    df = align_mz_across_samples(mz_arrays, sample_names=custom_names)

    assert list(df.columns) == ["Control", "Treatment"]


# ==============================================================================
# 3. Core Alignment & PPM Tolerance
# ==============================================================================


def test_exact_matches_across_samples():
    """Test alignment when samples have identical m/z values."""
    mz_arrays = [
        np.array([100.0, 200.0]),
        np.array([100.0, 200.0]),
    ]
    df = align_mz_across_samples(mz_arrays)

    assert len(df) == 2
    assert list(df.index) == [100.0, 200.0]
    assert df.loc[100.0, "sample_0"] == 0
    assert df.loc[100.0, "sample_1"] == 0
    assert df.loc[200.0, "sample_0"] == 1
    assert df.loc[200.0, "sample_1"] == 1


def test_ppm_tolerance_within_window():
    """Peaks within the PPM window should be clustered together and averaged."""
    # At m/z 100.0, 10 PPM is 0.001 m/z
    # 100.0000 and 100.0005 are ~5 PPM apart -> should cluster
    mz_arrays = [
        np.array([100.0000]),
        np.array([100.0005]),
    ]
    df = align_mz_across_samples(mz_arrays, align_ppm=10.0, mz_decimals=4)

    assert len(df) == 1
    # Average of 100.0000 and 100.0005 = 100.00025 -> rounded to 4 decimals = 100.0002 or 100.0003 depending on rounding
    expected_mz = round((100.0000 + 100.0005) / 2, 4)
    assert df.index[0] == expected_mz
    assert df.iloc[0]["sample_0"] == 0
    assert df.iloc[0]["sample_1"] == 0


def test_ppm_tolerance_outside_window():
    """Peaks outside the PPM window should be placed in separate clusters."""
    # At m/z 100.0, 10 PPM is 0.001 m/z
    # 100.0000 and 100.0020 are 20 PPM apart -> should NOT cluster
    mz_arrays = [
        np.array([100.0000]),
        np.array([100.0020]),
    ]
    df = align_mz_across_samples(mz_arrays, align_ppm=10.0, mz_decimals=4)

    assert len(df) == 2
    assert df.index[0] == 100.0000
    assert df.index[1] == 100.0020
    assert df.loc[100.0000, "sample_0"] == 0
    assert pd.isna(df.loc[100.0000, "sample_1"])
    assert pd.isna(df.loc[100.0020, "sample_0"])
    assert df.loc[100.0020, "sample_1"] == 0


@pytest.mark.parametrize(
    "ppm, expected_clusters",
    [
        (5.0, 2),   # 15 PPM apart -> 2 clusters
        (20.0, 1),  # 15 PPM apart -> 1 cluster
    ],
)
def test_align_ppm_parameter(ppm, expected_clusters):
    """Test varying the align_ppm parameter."""
    # 100.0000 and 100.0015 are 15 PPM apart
    mz_arrays = [
        np.array([100.0000]),
        np.array([100.0015]),
    ]
    df = align_mz_across_samples(mz_arrays, align_ppm=ppm)

    assert len(df) == expected_clusters


# ==============================================================================
# 4. Same-Sample Duplicate / Close Peaks
# ==============================================================================


def test_same_sample_close_peaks_cannot_cluster_together():
    """
    A cluster cannot accept more than one peak from the same sample,
    even if they fall within the PPM window.
    """
    # Sample 0 has two peaks very close to each other (1 PPM apart)
    mz_arrays = [
        np.array([100.0000, 100.0001]),
        np.array([100.0000]),
    ]
    df = align_mz_across_samples(mz_arrays, align_ppm=10.0)

    # Should create 2 distinct clusters because sample 0 can't contribute twice to 1 cluster
    assert len(df) == 2


# ==============================================================================
# 5. Indexing, Ordering, & Formatting
# ==============================================================================


def test_unsorted_input_arrays():
    """Verify algorithm handles unsorted input arrays while tracking original indices."""
    # Unsorted m/z arrays
    mz_arrays = [
        np.array([300.0, 100.0, 200.0]),  # 100.0 is at orig_idx 1, 200.0 at 2, 300.0 at 0
        np.array([200.0, 100.0]),        # 100.0 is at orig_idx 1, 200.0 at 0
    ]
    df = align_mz_across_samples(mz_arrays)

    # Output DataFrame index should be sorted ascending by m/z
    assert list(df.index) == [100.0, 200.0, 300.0]

    # Check that original array indices were preserved correctly
    assert df.loc[100.0, "sample_0"] == 1
    assert df.loc[100.0, "sample_1"] == 1
    assert df.loc[200.0, "sample_0"] == 2
    assert df.loc[200.0, "sample_1"] == 0
    assert df.loc[300.0, "sample_0"] == 0
    assert pd.isna(df.loc[300.0, "sample_1"])


@pytest.mark.parametrize("decimals", [2, 4, 6])
def test_mz_decimals_parameter(decimals):
    """Verify that cluster m/z values are rounded to the specified precision."""
    mz_arrays = [
        np.array([100.1234567]),
        np.array([100.1234569]),
    ]
    df = align_mz_across_samples(mz_arrays, mz_decimals=decimals)

    expected_mz = round((100.1234567 + 100.1234569) / 2, decimals)
    assert df.index[0] == expected_mz


def test_dataframe_column_dtypes_are_nullable_int64():
    """Verify that columns are cast to pandas 'Int64' (nullable integer)."""
    mz_arrays = [
        np.array([100.0, 200.0]),
        np.array([100.0]),  # Missing 200.0
    ]
    df = align_mz_across_samples(mz_arrays)

    assert df["sample_0"].dtype == "Int64"
    assert df["sample_1"].dtype == "Int64"

    # Missing value should be represented as pd.NA / NaN seamlessly without converting column to float64
    assert df.loc[100.0, "sample_1"] == 0
    assert pd.isna(df.loc[200.0, "sample_1"])


# ==============================================================================
# 6. Complex Multi-Sample Scenario
# ==============================================================================


def test_complex_multi_sample_alignment():
    """Test alignment across 3 samples with missing peaks and close peaks."""
    sample_a = np.array([100.0000, 200.0000, 500.0000])
    sample_b = np.array([100.0005, 300.0000, 500.0002])  # 100.0005 is ~5 ppm from 100.0
    sample_c = np.array([200.0010, 300.0000])            # 200.0010 is 5 ppm from 200.0

    df = align_mz_across_samples(
        mz_arrays=[sample_a, sample_b, sample_c],
        sample_names=["A", "B", "C"],
        align_ppm=10.0,
        mz_decimals=4,
    )

    # Expecting 4 distinct m/z alignment groups: ~100, ~200, ~300, ~500
    assert len(df) == 4
    assert list(df.columns) == ["A", "B", "C"]

    # Row 1: ~100.0003 (A=0, B=0, C=NA)
    row_100 = df.iloc[0]
    assert row_100["A"] == 0
    assert row_100["B"] == 0
    assert pd.isna(row_100["C"])

    # Row 2: ~200.0005 (A=1, B=NA, C=0)
    row_200 = df.iloc[1]
    assert row_200["A"] == 1
    assert pd.isna(row_200["B"])
    assert row_200["C"] == 0

    # Row 3: 300.0 (A=NA, B=1, C=1)
    row_300 = df.iloc[2]
    assert pd.isna(row_300["A"])
    assert row_300["B"] == 1
    assert row_300["C"] == 1

    # Row 4: ~500.0001 (A=2, B=2, C=NA)
    row_500 = df.iloc[3]
    assert row_500["A"] == 2
    assert row_500["B"] == 2
    assert pd.isna(row_500["C"])