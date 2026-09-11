from PySide6.QtCore import Qt, QObject


def _open_annotations_tab(view, root, find_visual_child, qtbot):
    nav_button = find_visual_child(root, "navButton_2")
    center = nav_button.mapToScene(nav_button.boundingRect().center()).toPoint()
    qtbot.mouseClick(view, Qt.LeftButton, pos=center)
    qtbot.wait(50)


def _open_candidates_popup(view, root, find_visual_child, qtbot, feature_id=7):
    row = find_visual_child(root, "annotationRow_" + str(feature_id))
    center = row.mapToScene(row.boundingRect().center()).toPoint()
    qtbot.mouseClick(view, Qt.LeftButton, pos=center)
    qtbot.wait(100)
    return root.findChild(QObject, "candidatesPopup")


def _candidates_panel(popup, find_visual_child):
    """The candidate-list side of the popup, scoped away from the
    WebEngineView sibling — searching *past* a WebEngineView while looking
    for something else recurses into its internal item tree, which is
    unsafe to touch via childItems()."""
    popup_content = popup.property("contentItem")
    return find_visual_child(popup_content, "candidatesPanel")


def test_annotations_empty_state_for_db_without_annotations(
    analysis_view, analysis_model, find_visual_child, qtbot
):
    view = analysis_view(analysis_model)
    root = view.rootObject()
    _open_annotations_tab(view, root, find_visual_child, qtbot)

    empty_label = find_visual_child(root, "annotationsEmptyStateLabel")
    assert empty_label is not None
    assert empty_label.property("visible") is True


def test_annotations_table_shows_one_row_per_feature(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    _open_annotations_tab(view, root, find_visual_child, qtbot)

    row = find_visual_child(root, "annotationRow_7")
    assert row is not None

    compound_text = find_visual_child(root, "annotationRowCompound_7")
    score_text = find_visual_child(root, "annotationRowScore_7")
    assert compound_text.property("text") == "Caffeine"
    assert score_text.property("text") == "score 0.870"


def test_clicking_row_opens_candidates_popup_with_one_candidate(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    _open_annotations_tab(view, root, find_visual_child, qtbot)

    popup = _open_candidates_popup(view, root, find_visual_child, qtbot)
    assert popup is not None
    assert popup.property("visible") is True
    assert popup.property("featureId") == 7

    panel = _candidates_panel(popup, find_visual_child)
    candidate_row = find_visual_child(panel, "candidateRow_1")
    assert candidate_row is not None

    label = find_visual_child(panel, "candidateRowLabel_1")
    assert "Caffeine" in label.property("text")
    meta = find_visual_child(panel, "candidateRowMeta_1")
    assert meta.property("text") == "s1  ·  my_library"


def test_selecting_candidate_triggers_mirror_plot_load(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()
    _open_annotations_tab(view, root, find_visual_child, qtbot)

    popup = _open_candidates_popup(view, root, find_visual_child, qtbot)
    panel = _candidates_panel(popup, find_visual_child)
    candidate_row = find_visual_child(panel, "candidateRow_1")
    center = candidate_row.mapToScene(candidate_row.boundingRect().center()).toPoint()
    qtbot.mouseClick(view, Qt.LeftButton, pos=center)
    qtbot.wait(50)

    selected = popup.property("selectedCandidate")
    assert selected is not None
    assert selected["id"] == 1
    # The WebEngineView itself is not asserted on here — searching *past* it
    # (or into it) while it's loading touches Chromium's own async
    # compositor state and isn't safe; getMirrorPlotUrl's actual HTML
    # output is covered directly in test_analysis_bridge.py.
