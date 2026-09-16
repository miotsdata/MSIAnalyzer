import sqlite3

from PySide6.QtCore import Qt
from PySide6.QtQuick import QQuickItem


def test_header_shows_project_analysis_and_date(analysis_view, analysis_model):
    # The nav rail moved up next to the title ("in one row I have title,
    # the various sections that I can click, and back to project button")
    # so every section gets the page's full width instead of sharing it
    # with a fixed-width left-hand nav column.
    view = analysis_view(analysis_model)
    root = view.rootObject()

    title = root.findChild(QQuickItem, "analysisPageTitle")
    run_id_text = root.findChild(QQuickItem, "analysisPageRunId")
    date_text = root.findChild(QQuickItem, "analysisPageDate")

    assert title.property("text") == "Project: " + analysis_model.project.name
    assert run_id_text.property("text") == "Analysis: " + analysis_model.runId
    assert date_text.property("text") == "Date: " + analysis_model.startDateDisplay


def test_nav_rail_has_four_sections(analysis_view, analysis_model, find_visual_child):
    view = analysis_view(analysis_model)
    root = view.rootObject()

    expected = ["Summary", "MS1 Spectra", "Annotations", "Visual Inspection"]
    for i, label in enumerate(expected):
        button = find_visual_child(root, "navButton_" + str(i))
        assert button is not None
        assert button.property("text") == label


def test_nav_buttons_and_back_button_show_pointing_hand_on_hover(
    analysis_view, analysis_model, find_visual_child
):
    # "the buttons in analyses (MS1, Annotation etc)... should make the
    # user understand that they can be clickable (on hover, use the
    # classic 'hand' logo)" — Buttons don't get a pointing-hand cursor by
    # default in Qt Quick Controls. `HoverHandler` isn't a QQuickItem (not
    # reachable via find_visual_child's childItems() walk, nor via
    # findChild from `root` — it's parented directly to the button as a
    # plain QObject), so it's found via the button's own `.findChild`
    # once the button itself has been located.
    view = analysis_view(analysis_model)
    root = view.rootObject()

    for i in range(4):
        nav_button = find_visual_child(root, "navButton_" + str(i))
        hover_handler = nav_button.findChild(object, "navButtonHover_" + str(i))
        assert hover_handler is not None
        assert hover_handler.property("cursorShape") == Qt.PointingHandCursor

    back_button = root.findChild(QQuickItem, "backToProjectButton")
    back_hover = back_button.findChild(object, "backToProjectButtonHover")
    assert back_hover is not None
    assert back_hover.property("cursorShape") == Qt.PointingHandCursor


def test_back_to_project_button_navigates_to_project_home(
    analysis_view, analysis_model, application, qtbot
):
    view = analysis_view(analysis_model)
    root = view.rootObject()

    back_button = root.findChild(QQuickItem, "backToProjectButton")
    assert back_button is not None

    received = []
    application.router.showProjectHomeRequested.connect(received.append)

    center = back_button.mapToScene(back_button.boundingRect().center()).toPoint()
    qtbot.mouseClick(view, Qt.LeftButton, pos=center)
    qtbot.wait(50)

    assert len(received) == 1
    assert received[0].name == analysis_model.project.name


def test_summary_section_shown_by_default(analysis_view, analysis_model):
    view = analysis_view(analysis_model)
    root = view.rootObject()

    stack = root.findChild(QQuickItem, "sectionStack")
    assert stack.property("currentIndex") == 0


def test_clicking_nav_button_switches_section(
    analysis_view, analysis_model, find_visual_child, qtbot
):
    view = analysis_view(analysis_model)
    root = view.rootObject()
    stack = root.findChild(QQuickItem, "sectionStack")
    ms1_button = find_visual_child(root, "navButton_1")

    center = ms1_button.mapToScene(ms1_button.boundingRect().center()).toPoint()
    qtbot.mouseClick(view, Qt.LeftButton, pos=center)

    assert stack.property("currentIndex") == 1


def test_annotations_and_visual_sections_are_lazy(
    analysis_view, analysis_model, find_visual_child, qtbot
):
    # Opening the workspace used to eagerly construct all four sections'
    # full QML trees at once even though only Summary was visible —
    # Annotations/Visual Inspection now only construct once their own tab
    # is actually opened, matching MS1's existing (WebEngineView-driven)
    # lazy pattern. Cuts the one-time cost of opening the workspace down
    # to just Summary's.
    view = analysis_view(analysis_model)
    root = view.rootObject()

    annotations_loader = root.findChild(QQuickItem, "annotationsSectionLoader")
    visual_loader = root.findChild(QQuickItem, "visualSectionLoader")
    assert annotations_loader.property("active") is False
    assert visual_loader.property("active") is False

    annotations_button = find_visual_child(root, "navButton_2")
    center = annotations_button.mapToScene(annotations_button.boundingRect().center()).toPoint()
    qtbot.mouseClick(view, Qt.LeftButton, pos=center)
    qtbot.wait(50)

    assert annotations_loader.property("active") is True
    assert annotations_loader.property("item") is not None
    assert visual_loader.property("active") is False


def test_loading_overlay_hides_once_summary_is_ready(analysis_view, analysis_model, qtbot):
    # "when the whole page is ready... it is displayed" — Summary
    # constructs synchronously/near-instantly, so the overlay shouldn't
    # still be covering it after a short settle.
    view = analysis_view(analysis_model)
    root = view.rootObject()
    qtbot.wait(50)

    overlay = root.findChild(QQuickItem, "sectionLoadingOverlay")
    assert overlay is not None
    assert overlay.property("visible") is False


def test_loading_overlay_covers_ms1_tab_until_plot_finishes_loading(
    analysis_view, analysis_model, find_visual_child, qtbot
):
    # "plots included" — the overlay should stay up for the MS1 tab until
    # the spectrum plot itself (not just the section's own QML) is ready.
    # Needs a real sample + saved spectrum, or there's nothing for
    # `plotLoading` to gate on (the empty-state case never counts as
    # "still loading" — see MS1SpectraSection.qml's `plotLoading`).
    import numpy as np

    from msianalyzer.core.analysis_db import log_command, register_sample
    from msianalyzer.core.spectra.average_spectra import save_aggregated_spectra

    db_path = analysis_model.analysisDbPath
    sample_id = register_sample(db_path, name="s1", raw_db_path="a.db")
    command_id = log_command(db_path, "filter_spectra", {}, run_id="test-run", sample_id=sample_id)
    save_aggregated_spectra(
        np.array([100.0, 200.0]), np.array([10.0, 20.0]),
        analysis_db_path=db_path, run_id="test-run", sample_id=sample_id, command_id=command_id,
    )

    view = analysis_view(analysis_model)
    root = view.rootObject()

    ms1_button = find_visual_child(root, "navButton_1")
    center = ms1_button.mapToScene(ms1_button.boundingRect().center()).toPoint()
    qtbot.mouseClick(view, Qt.LeftButton, pos=center)

    overlay = root.findChild(QQuickItem, "sectionLoadingOverlay")
    ms1_loader = root.findChild(QQuickItem, "ms1SectionLoader")

    qtbot.waitUntil(
        lambda: ms1_loader.property("item") is not None
                and not ms1_loader.property("item").property("plotLoading"),
        timeout=2000,
    )
    assert overlay.property("visible") is False


def test_nav_rail_and_back_button_disabled_while_section_still_loading(
    analysis_view, analysis_model, find_visual_child, qtbot
):
    # "is everything else disabled? So user cannot do more things while
    # loading... I don't want user to run back and forth while things are
    # loading" — while the MS1 tab's plot is still loading, the nav rail
    # and "Back to project" must be disabled (and visibly dimmed), then
    # re-enabled once the plot finishes.
    import numpy as np

    from msianalyzer.core.analysis_db import log_command, register_sample
    from msianalyzer.core.spectra.average_spectra import save_aggregated_spectra

    db_path = analysis_model.analysisDbPath
    sample_id = register_sample(db_path, name="s1", raw_db_path="a.db")
    command_id = log_command(db_path, "filter_spectra", {}, run_id="test-run", sample_id=sample_id)
    save_aggregated_spectra(
        np.array([100.0, 200.0]), np.array([10.0, 20.0]),
        analysis_db_path=db_path, run_id="test-run", sample_id=sample_id, command_id=command_id,
    )

    view = analysis_view(analysis_model)
    root = view.rootObject()

    nav_rail = root.findChild(QQuickItem, "navRail")
    back_button = root.findChild(QQuickItem, "backToProjectButton")
    overlay = root.findChild(QQuickItem, "sectionLoadingOverlay")
    ms1_loader = root.findChild(QQuickItem, "ms1SectionLoader")

    # Both are bound straight off `currentSectionReady` — the same
    # property the overlay's own `visible` is bound off — so every time
    # `visible` changes, `enabled`/`opacity` must already agree with it.
    # Recorded via the signal, deferred one event-loop tick (QML's
    # dependent bindings for the same source property don't all
    # re-evaluate in a guaranteed order *within* the same tick — reading
    # nav_rail synchronously inside overlay's own changed-signal handler
    # was observed to occasionally see a stale value) rather than a
    # plain before/after check, since MS1's plot can finish loading fast
    # enough in this test environment that a before/after check might
    # never actually catch the still-loading state.
    from PySide6.QtCore import QTimer

    history = []

    def record():
        history.append((
            overlay.property("visible"),
            nav_rail.property("enabled"),
            nav_rail.property("opacity"),
            back_button.property("enabled"),
            back_button.property("opacity"),
        ))

    overlay.visibleChanged.connect(lambda: QTimer.singleShot(0, record))
    record()

    ms1_button = find_visual_child(root, "navButton_1")
    center = ms1_button.mapToScene(ms1_button.boundingRect().center()).toPoint()
    qtbot.mouseClick(view, Qt.LeftButton, pos=center)
    qtbot.wait(10)

    qtbot.waitUntil(
        lambda: ms1_loader.property("item") is not None
                and not ms1_loader.property("item").property("plotLoading"),
        timeout=2000,
    )
    qtbot.wait(10)
    record()

    assert overlay.property("visible") is False
    assert len(history) >= 2
    for visible, nav_enabled, nav_opacity, back_enabled, back_opacity in history:
        ready = not visible
        assert nav_enabled is ready
        assert nav_opacity == (1.0 if ready else 0.5)
        assert back_enabled is ready
        assert back_opacity == (1.0 if ready else 0.5)
    assert any(visible for visible, *_ in history), (
        "test never observed the loading state — MS1's plot resolved too "
        "fast in this run to exercise the disabled/dimmed branch"
    )


def test_loading_overlay_mouse_area_blocks_all_clicks(analysis_view, analysis_model, qtbot):
    # Covering the not-yet-ready content visually isn't enough — a click
    # could still land on it underneath. The overlay's MouseArea must
    # accept every mouse button so nothing passes through while visible.
    view = analysis_view(analysis_model)
    root = view.rootObject()

    overlay = root.findChild(QQuickItem, "sectionLoadingOverlay")
    assert overlay is not None
    mouse_area = overlay.findChild(QQuickItem, "loadingOverlayMouseArea")
    assert mouse_area is not None
    assert mouse_area.property("acceptedButtons") == Qt.AllButtons


def test_summary_section_empty_state_for_empty_db(analysis_view, analysis_model, qtbot):
    view = analysis_view(analysis_model)
    root = view.rootObject()
    qtbot.wait(50)

    empty_label = root.findChild(QQuickItem, "summaryEmptyStateLabel")
    assert empty_label is not None
    assert empty_label.property("visible") is True


def test_inspect_visually_switches_to_visual_tab_with_feature_selected(
    analysis_view, annotated_analysis_model, find_visual_child, qtbot
):
    # End-to-end: right-click an annotated row in Annotations, "Inspect
    # visually", land on Visual Inspection already showing that same
    # feature — the cross-tab handoff (AnalysisPage.pendingInspectMz)
    # between AnnotationsSection and VisualInspectionSection.
    view = analysis_view(annotated_analysis_model)
    root = view.rootObject()

    annotations_button = find_visual_child(root, "navButton_2")
    center = annotations_button.mapToScene(
        annotations_button.boundingRect().center()
    ).toPoint()
    qtbot.mouseClick(view, Qt.LeftButton, pos=center)
    qtbot.wait(50)
    # rows is fetched on a background thread now (see ADR 33) — wait for
    # it before looking for the row.
    annotations_section = find_visual_child(root, "annotationsSection")
    qtbot.waitUntil(
        lambda: not annotations_section.property("rowsLoading"), timeout=2000
    )

    row = find_visual_child(root, "annotationRow_7")
    assert row is not None
    menu_item = row.findChild(object, "inspectVisuallyMenuItem_7")
    assert menu_item is not None
    menu_item.click()
    qtbot.wait(50)

    stack = root.findChild(QQuickItem, "sectionStack")
    assert stack.property("currentIndex") == 3

    controls = find_visual_child(root, "controlsFlickable")
    # features is fetched on a background thread too — applyPendingInspect
    # (VisualInspectionSection.qml) deliberately waits for featuresLoading
    # to clear before matching, see that function's own comment.
    qtbot.waitUntil(lambda: not controls.property("featuresLoading"), timeout=2000)
    assert controls.property("inspectionMode") == "feature"
    assert controls.property("selectedFeature")["mz"] == 123.4567

    # One-shot: the handoff is cleared once applied, not left dangling on
    # AnalysisPage for a later plain tab switch to re-apply.
    import math

    assert math.isnan(root.property("pendingInspectMz"))


def test_summary_section_shows_seeded_counts(
    analysis_view, analysis_model, find_visual_child, qtbot
):
    db_path = analysis_model.analysisDbPath
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO samples (sample_id, name, raw_db_path, polarity) "
            "VALUES (1, 's1', 'a.db', 'positive')"
        )
        con.execute(
            "INSERT INTO samples (sample_id, name, raw_db_path, polarity) "
            "VALUES (2, 's2', 'b.db', 'positive')"
        )
        con.executemany(
            "INSERT INTO features (feature_id, mz, members_json) VALUES (?, ?, '{}')",
            [(1, 100.0), (2, 200.0), (3, 300.0)],
        )
        con.commit()

    view = analysis_view(analysis_model)
    root = view.rootObject()
    qtbot.wait(50)

    samples_value = find_visual_child(root, "statValue_n_samples")
    features_value = find_visual_child(root, "statValue_n_features")
    annotated_value = find_visual_child(root, "statValue_n_annotated_features")

    assert samples_value.property("text") == "2"
    assert features_value.property("text") == "3"
    # no library ran -> placeholder, not "0"
    assert annotated_value.property("text") == "—"
