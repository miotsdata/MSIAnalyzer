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


def test_summary_section_empty_state_for_empty_db(analysis_view, analysis_model, qtbot):
    view = analysis_view(analysis_model)
    root = view.rootObject()
    qtbot.wait(50)

    empty_label = root.findChild(QQuickItem, "summaryEmptyStateLabel")
    assert empty_label is not None
    assert empty_label.property("visible") is True


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
