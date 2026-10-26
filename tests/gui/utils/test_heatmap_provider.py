from unittest.mock import patch

import anndata as ad
import numpy as np
import pandas as pd
from PySide6.QtGui import QImage
from scipy.sparse import csr_matrix

from msianalyzer.gui.utils.heatmap_provider import HeatmapImageProvider, load_light_sample


def _write_sample_h5ad(path, values=(0.0, 10.0, 5.0, 15.0), obs_columns=None):
    obs = pd.DataFrame(index=["a", "b", "c", "d"])
    if obs_columns:
        for name, column_values in obs_columns.items():
            obs[name] = column_values
    var = pd.DataFrame({"mz": [100.0]}, index=["mz_100.0000"])
    X = csr_matrix(np.array(values, dtype=np.float32).reshape(-1, 1))
    adata = ad.AnnData(X=X, obs=obs, var=var)
    adata.obsm["spatial"] = np.array(
        [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0)], dtype=float
    )
    adata.write_h5ad(path)


def test_request_image_renders_valid_heatmap(tmp_path):
    _write_sample_h5ad(tmp_path / "s1.h5ad")
    provider = HeatmapImageProvider()
    provider.setAnalysisDbPath(str(tmp_path / "analysis.db"))

    image = provider.requestImage("s1|100.0|raw|viridis|0|15", None, None)

    assert not image.isNull()
    assert image.width() == 2
    assert image.height() == 2
    assert image.format() == QImage.Format.Format_RGBA8888


def test_request_image_renders_colorbar_without_any_sample_or_analysis(tmp_path):
    # No setAnalysisDbPath call at all — the colorbar legend has no sample
    # data behind it (see render_colorbar), so it must not go through
    # _load_adata the way every other id shape does.
    provider = HeatmapImageProvider()

    image = provider.requestImage("colorbar|viridis", None, None)

    assert not image.isNull()
    assert image.width() == 256
    assert image.height() == 16
    assert image.format() == QImage.Format.Format_RGBA8888


def test_request_image_handles_percent_encoded_id(tmp_path):
    # QML's Image element treats `source` as a URL: assigning
    # "image://heatmap/name|mz|..." percent-encodes "|" (not valid raw in
    # a URL path) to "%7C" before this provider ever sees the id — every
    # real request arrives already encoded, which the "|"-only tests above
    # never exercise (they hand requestImage a pre-decoded string
    # directly). This is what actually reached production and broke every
    # single heatmap tile.
    _write_sample_h5ad(tmp_path / "s1.h5ad")
    provider = HeatmapImageProvider()
    provider.setAnalysisDbPath(str(tmp_path / "analysis.db"))

    encoded_id = "s1%7C100.0%7Craw%7Cviridis%7C0%7C15"
    image = provider.requestImage(encoded_id, None, None)

    assert not image.isNull()
    assert image.width() == 2
    assert image.height() == 2


def test_request_image_auto_scale_matches_render_feature_heatmap(tmp_path):
    _write_sample_h5ad(tmp_path / "s1.h5ad")
    provider = HeatmapImageProvider()
    provider.setAnalysisDbPath(str(tmp_path / "analysis.db"))

    image = provider.requestImage("s1|100.0|raw|viridis|auto|auto", None, None)

    from msianalyzer.core.plotting.heatmap import render_feature_heatmap

    adata = ad.read_h5ad(tmp_path / "s1.h5ad")
    expected = render_feature_heatmap(adata, 100.0, layer="raw", colormap="viridis")

    assert image.width() == expected.shape[1]
    assert image.height() == expected.shape[0]
    assert tuple(image.pixelColor(0, 0).getRgb()) == tuple(int(c) for c in expected[0, 0])


def test_request_image_null_without_analysis_db_path():
    provider = HeatmapImageProvider()
    image = provider.requestImage("s1|100.0|raw|viridis|auto|auto", None, None)
    assert image.isNull()


def test_invalidate_forces_a_fresh_read_after_the_file_changes(tmp_path):
    path = tmp_path / "s1.h5ad"
    _write_sample_h5ad(path, values=(0.0, 10.0, 5.0, 15.0))
    provider = HeatmapImageProvider()
    provider.setAnalysisDbPath(str(tmp_path / "analysis.db"))

    # Populate the cache.
    provider.requestImage("s1|100.0|raw|viridis|0|15", None, None)
    assert str(path) in provider._cache

    # Mutate the file on disk directly (simulating a save_roi_to_sample-style
    # write) — without invalidation the cached AnnData would still be served.
    _write_sample_h5ad(path, values=(100.0, 100.0, 100.0, 100.0))

    provider.invalidate("s1")

    assert str(path) not in provider._cache
    image = provider.requestImage("s1|100.0|raw|viridis|0|100", None, None)
    # All-100 values at vmin=0/vmax=100 -> every pixel at the top of the
    # colormap, i.e. every pixel the same color (the old cached 0..15
    # spread would have produced a gradient instead).
    colors = {image.pixelColor(x, y).getRgb() for x in range(2) for y in range(2)}
    assert len(colors) == 1


def test_repeated_request_for_same_tile_only_renders_once(tmp_path):
    # ZoomableImage.qml sets `cache: false` on its own Image (Qt's own
    # image cache is off there), so re-showing an already-viewed tile
    # relies entirely on this provider's own render cache.
    from msianalyzer.core.plotting import heatmap as heatmap_module

    _write_sample_h5ad(tmp_path / "s1.h5ad")
    provider = HeatmapImageProvider()
    provider.setAnalysisDbPath(str(tmp_path / "analysis.db"))

    with patch(
        "msianalyzer.gui.utils.heatmap_provider.render_heatmap_by_target",
        wraps=heatmap_module.render_heatmap_by_target,
    ) as spy:
        provider.requestImage("s1|100.0|raw|viridis|0|15", None, None)
        provider.requestImage("s1|100.0|raw|viridis|0|15", None, None)
        assert spy.call_count == 1

        # A different id (different vmax) is a genuinely different tile —
        # still renders.
        provider.requestImage("s1|100.0|raw|viridis|0|100", None, None)
        assert spy.call_count == 2


def test_invalidate_clears_cached_renders_for_that_sample(tmp_path):
    from msianalyzer.core.plotting import heatmap as heatmap_module

    _write_sample_h5ad(tmp_path / "s1.h5ad")
    provider = HeatmapImageProvider()
    provider.setAnalysisDbPath(str(tmp_path / "analysis.db"))

    with patch(
        "msianalyzer.gui.utils.heatmap_provider.render_heatmap_by_target",
        wraps=heatmap_module.render_heatmap_by_target,
    ) as spy:
        provider.requestImage("s1|100.0|raw|viridis|0|15", None, None)
        assert spy.call_count == 1

        provider.invalidate("s1")

        provider.requestImage("s1|100.0|raw|viridis|0|15", None, None)
        assert spy.call_count == 2  # re-rendered, not served from the stale cache


def test_render_target_populates_cache_for_a_later_request_image_call(tmp_path):
    # What HEOverlayImageProvider relies on: a render through
    # _render_target is cached under the full request id, so a later
    # requestImage for the same id is a cache hit, not a fresh render.
    from msianalyzer.core.plotting import heatmap as heatmap_module

    _write_sample_h5ad(tmp_path / "s1.h5ad")
    provider = HeatmapImageProvider()
    provider.setAnalysisDbPath(str(tmp_path / "analysis.db"))

    parts = ["s1", "100.0", "raw", "viridis", "0", "15"]
    provider._render_target("s1", "100.0", parts)

    with patch(
        "msianalyzer.gui.utils.heatmap_provider.render_heatmap_by_target",
        wraps=heatmap_module.render_heatmap_by_target,
    ) as spy:
        image = provider.requestImage("s1|100.0|raw|viridis|0|15", None, None)
        assert spy.call_count == 0  # served straight from the prefetch's cache
        assert not image.isNull()


def test_invalidate_is_a_no_op_when_sample_never_cached(tmp_path):
    provider = HeatmapImageProvider()
    provider.setAnalysisDbPath(str(tmp_path / "analysis.db"))
    provider.invalidate("never_requested")  # must not raise


def test_invalidate_is_a_no_op_without_analysis_db_path():
    provider = HeatmapImageProvider()
    provider.invalidate("s1")  # must not raise


def test_request_image_null_for_missing_sample(tmp_path):
    provider = HeatmapImageProvider()
    provider.setAnalysisDbPath(str(tmp_path / "analysis.db"))

    image = provider.requestImage("does_not_exist|100.0|raw|viridis|auto|auto", None, None)

    assert image.isNull()


def test_request_image_null_for_malformed_id(tmp_path):
    provider = HeatmapImageProvider()
    provider.setAnalysisDbPath(str(tmp_path / "analysis.db"))

    assert provider.requestImage("not-enough-parts", None, None).isNull()


def test_request_image_reuses_cached_adata(tmp_path):
    _write_sample_h5ad(tmp_path / "s1.h5ad")
    provider = HeatmapImageProvider()
    provider.setAnalysisDbPath(str(tmp_path / "analysis.db"))

    with patch("msianalyzer.gui.utils.heatmap_provider.load_light_sample", wraps=load_light_sample) as spy:
        provider.requestImage("s1|100.0|raw|viridis|0|15", None, None)
        provider.requestImage("s1|100.0|raw|magma|0|15", None, None)
        assert spy.call_count == 1


def test_set_analysis_db_path_clears_cache_on_change(tmp_path):
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    _write_sample_h5ad(dir_a / "s1.h5ad")
    _write_sample_h5ad(dir_b / "s1.h5ad")

    provider = HeatmapImageProvider()
    provider.setAnalysisDbPath(str(dir_a / "analysis.db"))

    with patch("msianalyzer.gui.utils.heatmap_provider.load_light_sample", wraps=load_light_sample) as spy:
        provider.requestImage("s1|100.0|raw|viridis|0|15", None, None)
        assert spy.call_count == 1

        # Same path again -> cache hit, no re-read.
        provider.setAnalysisDbPath(str(dir_a / "analysis.db"))
        provider.requestImage("s1|100.0|raw|viridis|0|15", None, None)
        assert spy.call_count == 1

        # Different analysis -> cache cleared, same sample *name* re-reads
        # from the new location rather than returning dir_a's stale data.
        provider.setAnalysisDbPath(str(dir_b / "analysis.db"))
        image = provider.requestImage("s1|100.0|raw|viridis|0|15", None, None)
        assert spy.call_count == 2
        assert not image.isNull()


def test_get_feature_value_range_combines_multiple_samples(tmp_path):
    _write_sample_h5ad(tmp_path / "s1.h5ad", values=(0.0, 10.0, 5.0, 15.0))
    _write_sample_h5ad(tmp_path / "s2.h5ad", values=(20.0, 30.0, 25.0, 100.0))
    provider = HeatmapImageProvider()
    provider.setAnalysisDbPath(str(tmp_path / "analysis.db"))

    result = provider.getFeatureValueRange(["s1", "s2"], 100.0, "raw")

    # min of every sample's min, max of every sample's max.
    assert result == {"vmin": 0.0, "vmax": 100.0}


def test_get_feature_value_range_single_sample(tmp_path):
    _write_sample_h5ad(tmp_path / "s1.h5ad", values=(2.0, 4.0, 6.0, 8.0))
    provider = HeatmapImageProvider()
    provider.setAnalysisDbPath(str(tmp_path / "analysis.db"))

    result = provider.getFeatureValueRange(["s1"], 100.0, "raw")

    assert result == {"vmin": 2.0, "vmax": 8.0}


def test_get_feature_value_range_skips_missing_samples(tmp_path):
    _write_sample_h5ad(tmp_path / "s1.h5ad", values=(2.0, 4.0, 6.0, 8.0))
    provider = HeatmapImageProvider()
    provider.setAnalysisDbPath(str(tmp_path / "analysis.db"))

    result = provider.getFeatureValueRange(["s1", "does_not_exist"], 100.0, "raw")

    assert result == {"vmin": 2.0, "vmax": 8.0}


def test_get_feature_value_range_defaults_when_no_samples_resolve(tmp_path):
    provider = HeatmapImageProvider()
    provider.setAnalysisDbPath(str(tmp_path / "analysis.db"))

    result = provider.getFeatureValueRange(["does_not_exist"], 100.0, "raw")

    assert result == {"vmin": 0.0, "vmax": 1.0}


def _write_3x3_sample_h5ad(path):
    # squidpy's spatial_neighbors_grid needs more pixels than the 2x2
    # fixture elsewhere in this file has (n_neighs=4 requires n_samples > 4).
    obs = pd.DataFrame(index=[f"px{i}" for i in range(9)])
    var = pd.DataFrame({"mz": [100.0]}, index=["mz_100.0000"])
    X = csr_matrix(np.arange(9, dtype=np.float32).reshape(-1, 1))
    adata = ad.AnnData(X=X, obs=obs, var=var)
    coords = [(x, y) for y in (0.0, 1.0, 2.0) for x in (0.0, 1.0, 2.0)]
    adata.obsm["spatial"] = np.array(coords, dtype=float)
    adata.write_h5ad(path)


def test_request_image_renders_local_moran_layer(tmp_path):
    # The "localMoran" dataLayer sentinel (HeatmapControlsPanel.qml) round-
    # trips through the same image://heatmap/... request-id shape as
    # raw/TIC — see render_heatmap_by_target's dispatch.
    _write_3x3_sample_h5ad(tmp_path / "s1.h5ad")
    provider = HeatmapImageProvider()
    provider.setAnalysisDbPath(str(tmp_path / "analysis.db"))

    image = provider.requestImage("s1|100.0|localMoran|coolwarm|auto|auto", None, None)

    assert not image.isNull()
    assert image.width() == 3
    assert image.height() == 3
    assert image.format() == QImage.Format.Format_RGBA8888


def test_get_obs_columns_reads_from_first_resolvable_sample(tmp_path):
    _write_sample_h5ad(
        tmp_path / "s1.h5ad",
        obs_columns={
            "tic": [1.0, 2.0, 3.0, 4.0],
            "region": ["a", "a", "b", "b"],
        },
    )
    provider = HeatmapImageProvider()
    provider.setAnalysisDbPath(str(tmp_path / "analysis.db"))

    columns = {c["name"]: c["numeric"] for c in provider.getObsColumns(["does_not_exist", "s1"])}

    assert columns["tic"] is True
    assert columns["region"] is False


def test_get_obs_columns_excludes_scan_id_and_polarity(tmp_path):
    _write_sample_h5ad(
        tmp_path / "s1.h5ad",
        obs_columns={
            "scan_id": ["1", "2", "3", "4"],
            "polarity": ["positive"] * 4,
            "tic": [1.0, 2.0, 3.0, 4.0],
        },
    )
    provider = HeatmapImageProvider()
    provider.setAnalysisDbPath(str(tmp_path / "analysis.db"))

    names = [c["name"] for c in provider.getObsColumns(["s1"])]

    assert "scan_id" not in names
    assert "polarity" not in names
    assert "tic" in names


def test_get_obs_columns_empty_when_no_samples_resolve(tmp_path):
    provider = HeatmapImageProvider()
    provider.setAnalysisDbPath(str(tmp_path / "analysis.db"))

    assert provider.getObsColumns(["does_not_exist"]) == []


def test_get_obs_value_range_combines_multiple_samples(tmp_path):
    _write_sample_h5ad(tmp_path / "s1.h5ad", obs_columns={"tic": [0.0, 10.0, 5.0, 15.0]})
    _write_sample_h5ad(tmp_path / "s2.h5ad", obs_columns={"tic": [20.0, 30.0, 25.0, 100.0]})
    provider = HeatmapImageProvider()
    provider.setAnalysisDbPath(str(tmp_path / "analysis.db"))

    result = provider.getObsValueRange(["s1", "s2"], "tic")

    assert result == {"vmin": 0.0, "vmax": 100.0}


def test_get_obs_value_range_defaults_when_no_samples_resolve(tmp_path):
    provider = HeatmapImageProvider()
    provider.setAnalysisDbPath(str(tmp_path / "analysis.db"))

    result = provider.getObsValueRange(["does_not_exist"], "tic")

    assert result == {"vmin": 0.0, "vmax": 1.0}


def test_get_obs_categories_combines_and_orders_across_samples(tmp_path):
    _write_sample_h5ad(
        tmp_path / "s1.h5ad", obs_columns={"polarity": ["positive"] * 4}
    )
    _write_sample_h5ad(
        tmp_path / "s2.h5ad", obs_columns={"polarity": ["negative"] * 4}
    )
    provider = HeatmapImageProvider()
    provider.setAnalysisDbPath(str(tmp_path / "analysis.db"))

    result = provider.getObsCategories(["s1", "s2"], "polarity")

    assert [c["category"] for c in result] == ["negative", "positive"]
    assert result[0]["color"] != result[1]["color"]


def test_get_obs_categories_applies_custom_colors(tmp_path):
    from msianalyzer.core.analysis_db import init_analysis_db, save_obs_category_color

    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    save_obs_category_color(db, "polarity", "positive", "#ff0000")
    _write_sample_h5ad(
        tmp_path / "s1.h5ad", obs_columns={"polarity": ["negative", "positive"] * 2}
    )
    provider = HeatmapImageProvider()
    provider.setAnalysisDbPath(str(db))

    result = provider.getObsCategories(["s1"], "polarity")

    assert result[0]["custom"] is False
    assert result[1] == {"category": "positive", "color": "#ff0000", "custom": True}


def test_request_image_renders_numeric_obs_column(tmp_path):
    _write_sample_h5ad(tmp_path / "s1.h5ad", obs_columns={"tic": [0.0, 10.0, 5.0, 15.0]})
    provider = HeatmapImageProvider()
    provider.setAnalysisDbPath(str(tmp_path / "analysis.db"))

    image = provider.requestImage("s1|obs:tic|viridis|0|15", None, None)

    assert not image.isNull()
    assert image.width() == 2
    assert image.height() == 2


def test_request_image_renders_categorical_obs_column(tmp_path):
    _write_sample_h5ad(
        tmp_path / "s1.h5ad",
        obs_columns={"polarity": ["negative", "positive", "negative", "positive"]},
    )
    provider = HeatmapImageProvider()
    provider.setAnalysisDbPath(str(tmp_path / "analysis.db"))

    image = provider.requestImage("s1|obs:polarity|negative,positive", None, None)

    assert not image.isNull()
    assert image.width() == 2
    assert image.height() == 2


def test_request_image_handles_percent_encoded_obs_id(tmp_path):
    _write_sample_h5ad(tmp_path / "s1.h5ad", obs_columns={"tic": [0.0, 10.0, 5.0, 15.0]})
    provider = HeatmapImageProvider()
    provider.setAnalysisDbPath(str(tmp_path / "analysis.db"))

    encoded_id = "s1%7Cobs%3Atic%7Cviridis%7C0%7C15"
    image = provider.requestImage(encoded_id, None, None)

    assert not image.isNull()


# ---------------------------------------------------------------------------
# ADR 50: asynchronous, serial rendering + budgeted light-sample cache
# ---------------------------------------------------------------------------


def test_request_image_response_renders_off_the_calling_thread(tmp_path, qtbot):
    import threading

    _write_sample_h5ad(tmp_path / "s1.h5ad")
    provider = HeatmapImageProvider()
    provider.setAnalysisDbPath(str(tmp_path / "analysis.db"))
    render_threads = []
    real = provider.requestImage

    def spy(*args):
        render_threads.append(threading.current_thread())
        return real(*args)

    provider.requestImage = spy
    response = provider.requestImageResponse("s1|100.0|raw|viridis|0|15", None)
    qtbot.waitUntil(lambda: response.done, timeout=5000)
    assert render_threads and render_threads[0] is not threading.main_thread()


def test_request_image_response_texture_is_the_rendered_image(tmp_path, qtbot):
    _write_sample_h5ad(tmp_path / "s1.h5ad")
    provider = HeatmapImageProvider()
    provider.setAnalysisDbPath(str(tmp_path / "analysis.db"))
    response = provider.requestImageResponse("s1|100.0|raw|viridis|0|15", None)
    qtbot.waitUntil(lambda: not response._image.isNull(), timeout=5000)
    assert response._image.width() == 2 and response._image.height() == 2


def test_renders_never_run_concurrently(tmp_path, qtbot):
    import threading
    import time as _time

    for name in ("s1", "s2", "s3"):
        _write_sample_h5ad(tmp_path / f"{name}.h5ad")
    provider = HeatmapImageProvider()
    provider.setAnalysisDbPath(str(tmp_path / "analysis.db"))
    active = [0]
    peak = [0]
    guard = threading.Lock()
    real = provider.requestImage

    def slow(*args):
        with guard:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        _time.sleep(0.02)
        try:
            return real(*args)
        finally:
            with guard:
                active[0] -= 1

    provider.requestImage = slow
    responses = [
        provider.requestImageResponse(f"{n}|100.0|raw|viridis|0|{v}", None)
        for n in ("s1", "s2", "s3") for v in (10, 15, 20)
    ]
    qtbot.waitUntil(lambda: all(not r._image.isNull() for r in responses), timeout=10000)
    assert peak[0] == 1


def test_cancelled_response_skips_the_render_but_still_finishes(tmp_path, qtbot):
    _write_sample_h5ad(tmp_path / "s1.h5ad")
    provider = HeatmapImageProvider()
    provider.setAnalysisDbPath(str(tmp_path / "analysis.db"))
    # Hold the single render thread busy so the second request is still
    # queued when it gets cancelled.
    import threading
    release = threading.Event()
    calls = []
    real = provider.requestImage

    def gated(id_, *rest):
        calls.append(id_)
        if id_.endswith("|0|15"):
            release.wait(5)
        return real(id_, *rest)

    provider.requestImage = gated
    first = provider.requestImageResponse("s1|100.0|raw|viridis|0|15", None)
    second = provider.requestImageResponse("s1|100.0|raw|viridis|0|99", None)
    second.cancel()
    release.set()
    qtbot.waitUntil(lambda: second.done, timeout=5000)
    assert calls == ["s1|100.0|raw|viridis|0|15"]
    assert second._image.isNull()


def test_warm_loads_samples_so_tiles_do_not_read_files(tmp_path):
    for name in ("s1", "s2"):
        _write_sample_h5ad(tmp_path / f"{name}.h5ad")
    provider = HeatmapImageProvider()
    provider.setAnalysisDbPath(str(tmp_path / "analysis.db"))
    provider.warm(["s1", "s2"], "raw")
    with patch("msianalyzer.gui.utils.heatmap_provider.load_light_sample",
               wraps=load_light_sample) as spy:
        assert not provider.requestImage("s1|100.0|raw|viridis|0|15", None, None).isNull()
        assert not provider.requestImage("s2|100.0|raw|viridis|0|15", None, None).isNull()
        assert spy.call_count == 0


def test_cache_budget_evicts_least_recently_used_samples(tmp_path):
    for name in ("s1", "s2", "s3"):
        _write_sample_h5ad(tmp_path / f"{name}.h5ad")
    provider = HeatmapImageProvider(cache_budget_bytes=1)  # smaller than any sample
    provider.setAnalysisDbPath(str(tmp_path / "analysis.db"))
    provider.warm(["s1", "s2", "s3"], "raw")
    assert list(provider._cache) == [str(tmp_path / "s3.h5ad")]  # most recent kept


def test_cache_holds_many_samples_under_a_normal_budget(tmp_path):
    names = [f"s{i}" for i in range(16)]
    for name in names:
        _write_sample_h5ad(tmp_path / f"{name}.h5ad")
    provider = HeatmapImageProvider()
    provider.setAnalysisDbPath(str(tmp_path / "analysis.db"))
    provider.warm(names, "raw")
    assert len(provider._cache) == 16  # the old fixed 4-entry cache thrashed here


def test_missing_sample_response_reports_an_error_not_an_empty_image(tmp_path, qtbot):
    # A Ready-but-0x0 image blocks all input in ZoomableImage; a missing
    # sample must reach QML as Image.Error, exactly like the old
    # synchronous provider's null QImage did.
    provider = HeatmapImageProvider()
    provider.setAnalysisDbPath(str(tmp_path / "analysis.db"))
    response = provider.requestImageResponse("nope|100.0|raw|viridis|0|15", None)
    qtbot.waitUntil(lambda: response.done, timeout=5000)
    assert response.errorString() == "no image"
