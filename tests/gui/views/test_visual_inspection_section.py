import sqlite3
from pathlib import Path

import anndata as ad
import numpy as np
import pandas as pd
import pytest
from PySide6.QtCore import Qt
from scipy.sparse import csr_matrix


def _open_visual_tab(view, root, find_visual_child, qtbot):
    nav_button = find_visual_child(root, "navButton_3")
    center = nav_button.mapToScene(nav_button.boundingRect().center()).toPoint()
    qtbot.mouseClick(view, Qt.LeftButton, pos=center)
    qtbot.wait(50)


def _scroll_controls_panel_to(root, find_visual_child, item):
    """Scrolls `controlsFlickable` so `item` (a descendant of
    `controlsPanel`) is on-screen — the panel can hold more controls than
    fit a typical window, same as any other scrollable list in this app;
    a real click needs the target actually visible, not just present in
    the (scrolled-off) content."""
    flickable = find_visual_child(root, "controlsFlickable")
    panel = find_visual_child(root, "controlsPanel")
    y_in_panel = item.mapToItem(panel, 0, 0).y()
    flickable.setProperty("contentY", max(0, y_in_panel - 20))


def test_visual_empty_state_without_features(
    analysis_view, analysis_model, find_visual_child, qtbot
):
    view = analysis_view(analysis_model)
    root = view.rootObject()
    _open_visual_tab(view, root, find_visual_child, qtbot)

    empty_label = find_visual_child(root, "visualEmptyStateLabel")
    assert empty_label is not None
    assert empty_label.property("visible") is True


def test_visual_shows_controls_and_feature_selector(
    analysis_view, visual_analysis_model, find_visual_child, qtbot
):
    view = analysis_view(visual_analysis_model)
    root = view.rootObject()
    _open_visual_tab(view, root, find_visual_child, qtbot)

    empty_label = find_visual_child(root, "visualEmptyStateLabel")
    assert empty_label.property("visible") is False

    feature_combo = find_visual_child(root, "featureCombo")
    assert feature_combo.property("count") == 1

    colormap_combo = find_visual_child(root, "colormapCombo")
    assert colormap_combo is not None

    tile_s1 = find_visual_child(root, "heatmapTile_s1")
    tile_s2 = find_visual_child(root, "heatmapTile_s2")
    assert tile_s1 is not None
    assert tile_s2 is not None


def test_visual_layer_button_click_updates_data_layer(
    analysis_view, visual_analysis_model, find_visual_child, qtbot
):
    view = analysis_view(visual_analysis_model)
    root = view.rootObject()
    _open_visual_tab(view, root, find_visual_child, qtbot)

    section = find_visual_child(root, "visualSection")
    assert section.property("dataLayer") == "TIC"

    raw_button = find_visual_child(root, "rawLayerButton")
    center = raw_button.mapToScene(raw_button.boundingRect().center()).toPoint()
    qtbot.mouseClick(view, Qt.LeftButton, pos=center)
    qtbot.wait(50)

    assert section.property("dataLayer") == "raw"


def test_visual_sample_visibility_toggle_removes_tile(
    analysis_view, visual_analysis_model, find_visual_child, qtbot
):
    view = analysis_view(visual_analysis_model)
    root = view.rootObject()
    _open_visual_tab(view, root, find_visual_child, qtbot)

    assert find_visual_child(root, "heatmapTile_s2") is not None

    checkbox = find_visual_child(root, "sampleVisibility_s2")
    _scroll_controls_panel_to(root, find_visual_child, checkbox)
    center = checkbox.mapToScene(checkbox.boundingRect().center()).toPoint()
    qtbot.mouseClick(view, Qt.LeftButton, pos=center)
    qtbot.wait(50)

    assert find_visual_child(root, "heatmapTile_s2") is None
    assert find_visual_child(root, "heatmapTile_s1") is not None


def _write_sample_h5ad(path, mz=150.0, values=(0.0, 10.0, 5.0, 15.0)):
    obs = pd.DataFrame(index=["a", "b", "c", "d"])
    var = pd.DataFrame({"mz": [mz]}, index=[f"mz_{mz:.4f}"])
    X = csr_matrix(np.array(values, dtype=np.float32).reshape(-1, 1))
    adata = ad.AnnData(X=X, obs=obs, var=var)
    adata.obsm["spatial"] = np.array(
        [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0)], dtype=float
    )
    adata.write_h5ad(path)


def test_visual_heatmap_tile_actually_loads_an_image(
    analysis_view, visual_analysis_model, find_visual_child, qtbot
):
    """End-to-end regression test for the id percent-encoding bug: QML's
    Image element percent-encodes "|" in `source` (not valid raw in a URL
    path) before HeatmapImageProvider.requestImage ever sees the id, which
    broke every single heatmap tile in production despite the provider's
    own unit tests passing (they call requestImage directly with an
    already-decoded id, never exercising the real Image/URL round trip)."""
    out_dir = Path(visual_analysis_model.outDir)
    _write_sample_h5ad(out_dir / "s1.h5ad")
    _write_sample_h5ad(out_dir / "s2.h5ad")

    view = analysis_view(visual_analysis_model)
    root = view.rootObject()
    _open_visual_tab(view, root, find_visual_child, qtbot)

    tile = find_visual_child(root, "heatmapImage_s1")
    assert tile is not None
    image = find_visual_child(tile, "zoomableImageContent")

    # `status` (an enum) isn't readable via .property() from Python — use
    # sourceSize instead: it's the loaded image's natural pixel size, zero
    # in both dimensions on Null/Error, non-zero only once genuinely loaded.
    qtbot.waitUntil(
        lambda: image.property("sourceSize").width() > 0, timeout=2000
    )
    assert image.property("sourceSize").width() == 2
    assert image.property("sourceSize").height() == 2


def test_visual_grid_is_fixed_to_one_column(
    analysis_view, visual_analysis_model, find_visual_child, qtbot
):
    # Rows/cols picking is disabled for now — one tile per row, fixed
    # size, the surrounding Flickable scrolls for more samples.
    view = analysis_view(visual_analysis_model)
    root = view.rootObject()
    _open_visual_tab(view, root, find_visual_child, qtbot)

    assert find_visual_child(root, "rowsSpinBox") is None
    assert find_visual_child(root, "colsSpinBox") is None

    grid = find_visual_child(root, "heatmapGrid")
    assert grid.property("columns") == 1

    flickable = find_visual_child(root, "heatmapGridFlickable")
    assert flickable is not None


@pytest.fixture
def sortable_features_model(visual_analysis_model):
    """`visual_analysis_model` (feature 1, mz=150.0, unannotated), plus
    two annotated features out of alphabetical order relative to their
    mz: feature 2 (mz=100.0, "Zebra compound") and feature 3 (mz=200.0,
    "Apple compound") — so a name-sort and an mz-sort disagree, and the
    already-existing unannotated feature must land last either way."""
    db_path = visual_analysis_model.analysisDbPath
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO features (feature_id, mz, members_json) "
            "VALUES (2, 100.0, '{\"s1\": 0, \"s2\": 0}')"
        )
        con.execute(
            "INSERT INTO features (feature_id, mz, members_json) "
            "VALUES (3, 200.0, '{\"s1\": 0, \"s2\": 0}')"
        )
        con.execute(
            "INSERT INTO annotation_libraries (id, path, name) "
            "VALUES (1, 'lib.db', 'my_library')"
        )
        for feature_id, scan_id, mz, name in [
            (2, 42, 100.0, "Zebra compound"),
            (3, 43, 200.0, "Apple compound"),
        ]:
            con.execute(
                "INSERT INTO ms2_associations "
                "(sample_id, scan_id, match_key, precursor_mz, "
                "n_features_in_window, rt, n_peaks, polarity) "
                "VALUES (1, ?, ?, ?, 1, 12.3, 5, 'positive')",
                (scan_id, f"k{scan_id}", mz),
            )
            con.execute(
                "INSERT INTO ms2_annotations "
                "(feature_id, sample_id, scan_id, library_id, "
                "library_spectrum_id, compound_name, compound_formula, inchikey, "
                "score, dot_product_score, lib_coverage, emp_coverage, "
                "coverage_score, n_matched_peaks, n_lib_peaks, n_emp_peaks_raw, "
                "n_emp_peaks_filtered, rank_ms2, rank_feature) "
                "VALUES (?, 1, ?, 1, ?, ?, 'C1H1', ?, "
                "0.9, 0.9, 0.8, 0.8, 0.8, 2, 2, 10, 3, 1, 1)",
                (feature_id, scan_id, scan_id, name, f"KEY{scan_id}"),
            )
        con.commit()
    return visual_analysis_model


def test_feature_labels_default_sorted_by_mz(
    analysis_view, sortable_features_model, find_visual_child, qtbot
):
    view = analysis_view(sortable_features_model)
    root = view.rootObject()
    _open_visual_tab(view, root, find_visual_child, qtbot)

    section = find_visual_child(root, "visualSection")
    labels = section.property("featureLabels").toVariant()
    assert labels == [
        "100.0000: Zebra compound",
        "m/z 150.0000",
        "200.0000: Apple compound",
    ]


def test_sort_by_name_puts_annotated_alphabetically_then_unannotated_last(
    analysis_view, sortable_features_model, find_visual_child, qtbot
):
    # Set sortMode directly rather than driving the ComboBox's native
    # popup — this codebase's tests don't simulate clicks inside a
    # ComboBox's popup list for any control (see featureCombo/
    # colormapCombo elsewhere: only ever asserted on, never clicked
    # through). The onActivated -> sortMode wiring itself is a one-line
    # ternary, low risk without its own test.
    view = analysis_view(sortable_features_model)
    root = view.rootObject()
    _open_visual_tab(view, root, find_visual_child, qtbot)

    section = find_visual_child(root, "visualSection")
    section.setProperty("sortMode", "name")

    labels = section.property("featureLabels").toVariant()
    assert labels == [
        "200.0000: Apple compound",
        "100.0000: Zebra compound",
        "m/z 150.0000",
    ]

    sort_combo = find_visual_child(root, "sortModeCombo")
    assert sort_combo.property("currentIndex") == 1


def test_sort_mode_combo_reflects_default_mz_sort(
    analysis_view, sortable_features_model, find_visual_child, qtbot
):
    view = analysis_view(sortable_features_model)
    root = view.rootObject()
    _open_visual_tab(view, root, find_visual_child, qtbot)

    sort_combo = find_visual_child(root, "sortModeCombo")
    assert sort_combo.property("currentIndex") == 0
