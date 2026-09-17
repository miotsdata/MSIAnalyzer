from unittest.mock import MagicMock

from PySide6.QtCore import QObject, Qt
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QSignalSpy

from msianalyzer.gui.models.project import ProjectModel
from msianalyzer.gui.utils.config_schema import build_config_schema


def _make_page(new_analysis_view, project):
    model = ProjectModel(project, "/tmp/proj")
    view = new_analysis_view(model)
    return view, view.rootObject(), model


def test_back_to_project_button_navigates_to_project_home(
    new_analysis_view, project, application, qtbot
):
    view, root, model = _make_page(new_analysis_view, project)

    back_button = root.findChild(QQuickItem, "backToProjectButton")
    assert back_button is not None

    received = []
    application.router.showProjectHomeRequested.connect(received.append)

    center = back_button.mapToScene(back_button.boundingRect().center()).toPoint()
    qtbot.mouseClick(view, Qt.LeftButton, pos=center)
    qtbot.wait(50)

    assert len(received) == 1
    assert received[0].name == model.name


def test_tab_bar_has_one_tab_per_group_plus_io(new_analysis_view, project, find_visual_child):
    _, root, _ = _make_page(new_analysis_view, project)

    assert find_visual_child(root, "tabButton_io") is not None
    for group in build_config_schema():
        assert find_visual_child(root, "tabButton_" + group["key"]) is not None


def test_run_button_disabled_by_default(new_analysis_view, project):
    _, root, _ = _make_page(new_analysis_view, project)

    run_button = root.findChild(QQuickItem, "runButton")
    assert run_button is not None
    assert run_button.property("enabled") is False


def test_out_dir_prefilled_from_project_folder(new_analysis_view, project):
    _, root, model = _make_page(new_analysis_view, project)

    out_dir_field = root.findChild(QQuickItem, "outDirField")
    assert out_dir_field.property("text") == model.folder + "/output"


def test_adding_and_filling_sample_row_enables_run_button(new_analysis_view, project):
    _, root, _ = _make_page(new_analysis_view, project)
    run_button = root.findChild(QQuickItem, "runButton")

    root.addSampleRow()
    assert run_button.property("enabled") is False  # mzml/xml still empty

    root.setSampleField(0, "mzml", "/data/sample1.mzML")
    assert run_button.property("enabled") is False  # xml still empty

    root.setSampleField(0, "xml", "/data/sample1.xml")
    assert run_button.property("enabled") is True


def test_bulk_add_mzml_creates_one_new_row_per_path(new_analysis_view, project):
    _, root, _ = _make_page(new_analysis_view, project)

    root.addMzmlPathsAsNewRows(["/data/s1.mzML", "/data/s2.mzML", "/data/s3.mzML"])

    rows = root.property("sampleRows").toVariant()
    assert len(rows) == 3
    assert [r["mzml"] for r in rows] == ["/data/s1.mzML", "/data/s2.mzML", "/data/s3.mzML"]
    # xml is left for the user to pair afterward.
    assert all(r["xml"] == "" for r in rows)


def test_bulk_add_mzml_with_no_paths_is_a_no_op(new_analysis_view, project):
    _, root, _ = _make_page(new_analysis_view, project)

    root.addMzmlPathsAsNewRows([])

    rows = root.property("sampleRows").toVariant()
    assert len(rows) == 0


def test_bulk_add_mzml_appends_after_existing_rows(new_analysis_view, project):
    _, root, _ = _make_page(new_analysis_view, project)

    root.addSampleRow()
    root.setSampleField(0, "mzml", "/data/existing.mzML")
    root.addMzmlPathsAsNewRows(["/data/new1.mzML", "/data/new2.mzML"])

    rows = root.property("sampleRows").toVariant()
    assert [r["mzml"] for r in rows] == [
        "/data/existing.mzML", "/data/new1.mzML", "/data/new2.mzML",
    ]


def test_bulk_add_xml_fills_existing_rows_in_order(new_analysis_view, project):
    _, root, _ = _make_page(new_analysis_view, project)

    root.addMzmlPathsAsNewRows(["/data/s1.mzML", "/data/s2.mzML"])
    root.addXmlPathsSequentially(["/data/s1.xml", "/data/s2.xml"])

    rows = root.property("sampleRows").toVariant()
    assert len(rows) == 2
    assert [r["xml"] for r in rows] == ["/data/s1.xml", "/data/s2.xml"]
    assert [r["mzml"] for r in rows] == ["/data/s1.mzML", "/data/s2.mzML"]


def test_bulk_add_xml_appends_new_rows_when_more_xml_than_mzml(
    new_analysis_view, project
):
    _, root, _ = _make_page(new_analysis_view, project)

    root.addMzmlPathsAsNewRows(["/data/s1.mzML"])
    root.addXmlPathsSequentially(["/data/s1.xml", "/data/extra.xml"])

    rows = root.property("sampleRows").toVariant()
    assert len(rows) == 2
    assert rows[0]["mzml"] == "/data/s1.mzML"
    assert rows[0]["xml"] == "/data/s1.xml"
    assert rows[1]["mzml"] == ""
    assert rows[1]["xml"] == "/data/extra.xml"


def test_swap_field_exchanges_only_the_named_field_between_rows(
    new_analysis_view, project
):
    _, root, _ = _make_page(new_analysis_view, project)

    root.addMzmlPathsAsNewRows(["/data/s1.mzML", "/data/s2.mzML"])
    root.addXmlPathsSequentially(["/data/wrong_for_s1.xml", "/data/wrong_for_s2.xml"])

    root.swapField(0, 1, "xml")

    rows = root.property("sampleRows").toVariant()
    # mzml order untouched, only xml swapped between the two rows.
    assert [r["mzml"] for r in rows] == ["/data/s1.mzML", "/data/s2.mzML"]
    assert [r["xml"] for r in rows] == ["/data/wrong_for_s2.xml", "/data/wrong_for_s1.xml"]


def test_swap_field_out_of_bounds_is_a_no_op(new_analysis_view, project):
    _, root, _ = _make_page(new_analysis_view, project)

    root.addMzmlPathsAsNewRows(["/data/s1.mzML"])
    root.swapField(0, -1, "mzml")
    root.swapField(0, 5, "mzml")

    rows = root.property("sampleRows").toVariant()
    assert rows[0]["mzml"] == "/data/s1.mzML"


def test_remove_sample_row_disables_run_button_again(new_analysis_view, project):
    _, root, _ = _make_page(new_analysis_view, project)
    run_button = root.findChild(QQuickItem, "runButton")

    root.addSampleRow()
    root.setSampleField(0, "mzml", "/data/sample1.mzML")
    root.setSampleField(0, "xml", "/data/sample1.xml")
    assert run_button.property("enabled") is True

    root.removeSampleRow(0)
    assert run_button.property("enabled") is False


def test_add_db_paths_appends_entries(new_analysis_view, project):
    _, root, _ = _make_page(new_analysis_view, project)

    root.addDbPathsAsNewEntries(["/data/s1.db", "/data/s2.db"])

    paths = root.property("dbOnlyPaths").toVariant()
    assert paths == ["/data/s1.db", "/data/s2.db"]


def test_add_db_paths_with_no_paths_is_a_no_op(new_analysis_view, project):
    _, root, _ = _make_page(new_analysis_view, project)

    root.addDbPathsAsNewEntries([])

    assert root.property("dbOnlyPaths").toVariant() == []


def test_remove_db_only_path(new_analysis_view, project):
    _, root, _ = _make_page(new_analysis_view, project)

    root.addDbPathsAsNewEntries(["/data/s1.db", "/data/s2.db"])
    root.removeDbOnlyPath(0)

    assert root.property("dbOnlyPaths").toVariant() == ["/data/s2.db"]


def test_db_only_paths_alone_enable_run_button(new_analysis_view, project):
    _, root, _ = _make_page(new_analysis_view, project)
    run_button = root.findChild(QQuickItem, "runButton")

    root.addDbPathsAsNewEntries(["/data/s1.db"])

    assert run_button.property("enabled") is True


def test_bool_field_renders_as_checkbox_with_default(
    new_analysis_view, project, find_visual_child
):
    _, root, _ = _make_page(new_analysis_view, project)

    control = find_visual_child(root, "field_peak_filter_mad")
    assert control is not None
    assert control.property("checked") is True  # PeakConfig.filter_mad default True


def test_optional_field_defaults_to_blank_text(
    new_analysis_view, project, find_visual_child
):
    _, root, _ = _make_page(new_analysis_view, project)

    control = find_visual_child(root, "field_annotate_min_precursor_frac")
    assert control is not None
    assert control.property("text") == ""


def _open_annotate_tab(view, root, find_visual_child, qtbot):
    tab_button = find_visual_child(root, "tabButton_annotate")
    center = tab_button.mapToScene(tab_button.boundingRect().center()).toPoint()
    qtbot.mouseClick(view, Qt.LeftButton, pos=center)
    qtbot.wait(50)


def test_library_path_starts_empty_with_placeholder_label(
    new_analysis_view, project, find_visual_child, qtbot
):
    view, root, _ = _make_page(new_analysis_view, project)
    _open_annotate_tab(view, root, find_visual_child, qtbot)

    control = find_visual_child(root, "field_annotate_library_path")
    assert control is not None
    empty_label = find_visual_child(control, "libraryPathEmptyLabel")
    assert empty_label.property("visible") is True
    assert find_visual_child(control, "libraryPathRow_0") is None


def test_adding_library_paths_lists_them_with_remove_buttons(
    new_analysis_view, project, find_visual_child, qtbot
):
    view, root, _ = _make_page(new_analysis_view, project)
    _open_annotate_tab(view, root, find_visual_child, qtbot)

    root.addLibraryPaths(["/libs/a.db", "/libs/b.db"])
    qtbot.wait(50)

    control = find_visual_child(root, "field_annotate_library_path")
    assert find_visual_child(control, "libraryPathEmptyLabel").property("visible") is False
    row0 = find_visual_child(control, "libraryPathRow_0")
    row1 = find_visual_child(control, "libraryPathRow_1")
    assert row0 is not None and row1 is not None
    assert find_visual_child(control, "removeLibraryPathButton_0") is not None
    assert find_visual_child(control, "removeLibraryPathButton_1") is not None


def test_adding_the_same_library_path_twice_does_not_duplicate_it(
    new_analysis_view, project
):
    _, root, _ = _make_page(new_analysis_view, project)

    root.addLibraryPaths(["/libs/a.db"])
    root.addLibraryPaths(["/libs/a.db"])

    assert _as_list(root.property("libraryPaths")) == ["/libs/a.db"]


def test_removing_a_library_path_removes_only_that_one(new_analysis_view, project):
    _, root, _ = _make_page(new_analysis_view, project)

    root.addLibraryPaths(["/libs/a.db", "/libs/b.db"])
    root.removeLibraryPath(0)

    assert _as_list(root.property("libraryPaths")) == ["/libs/b.db"]


def test_group_tab_shows_a_description_sentence(new_analysis_view, project, find_visual_child):
    _, root, _ = _make_page(new_analysis_view, project)

    description = find_visual_child(root, "groupDescription_peak")
    assert description is not None
    assert description.property("text") == (
        "Drop low-intensity peaks left over after centroid detection."
    )


def test_field_label_is_prettified_not_the_raw_python_name(
    new_analysis_view, project, find_visual_child
):
    _, root, _ = _make_page(new_analysis_view, project)

    field_row = find_visual_child(root, "fieldRow_peak_peak_height_threshold")
    # The label Text has no objectName; scan its direct Text children.
    label_texts = [
        c.property("text") for c in field_row.childItems() if c.property("text") is not None
    ]
    assert "Peak height threshold" in label_texts


def test_field_help_button_tooltip_has_docstring_text(
    new_analysis_view, project, find_visual_child
):
    _, root, _ = _make_page(new_analysis_view, project)

    help_button = find_visual_child(root, "field_peak_filter_mad_help")
    assert help_button is not None
    assert help_button.property("text") == "?"
    assert "median-absolute-deviation" in help_button.property("helpText")


def _open_peak_tab(view, root, find_visual_child, qtbot):
    tab_button = find_visual_child(root, "tabButton_peak")
    center = tab_button.mapToScene(tab_button.boundingRect().center()).toPoint()
    qtbot.mouseClick(view, Qt.LeftButton, pos=center)
    qtbot.wait(50)


def test_unchecking_filter_mad_fades_dependents_and_enables_height_threshold(
    new_analysis_view, project, find_visual_child, qtbot
):
    view, root, _ = _make_page(new_analysis_view, project)
    _open_peak_tab(view, root, find_visual_child, qtbot)

    filter_mad_row = find_visual_child(root, "fieldRow_peak_filter_mad_log")
    height_row = find_visual_child(root, "fieldRow_peak_peak_height_threshold")
    assert filter_mad_row.property("enabled") is True
    assert height_row.property("enabled") is False

    filter_mad = find_visual_child(root, "field_peak_filter_mad")
    center = filter_mad.mapToScene(filter_mad.boundingRect().center()).toPoint()
    qtbot.mouseClick(view, Qt.LeftButton, pos=center)
    qtbot.wait(50)

    assert filter_mad.property("checked") is False
    assert filter_mad_row.property("enabled") is False
    assert height_row.property("enabled") is True


def test_run_click_emits_router_signal_with_nested_config(
    new_analysis_view, project, application, qtbot
):
    view, root, model = _make_page(new_analysis_view, project)
    run_button = root.findChild(QQuickItem, "runButton")

    root.addSampleRow()
    root.setSampleField(0, "mzml", "/data/sample1.mzML")
    root.setSampleField(0, "xml", "/data/sample1.xml")

    # Application really is wired to core_bridge.run_analysis — stub it out
    # so clicking Run in this QML-focused test doesn't kick off a real
    # pipeline run against fake sample paths.
    application.core_bridge.run_analysis = MagicMock()

    spy = QSignalSpy(application.router.runAnalysisRequested)
    button_center = run_button.mapToScene(run_button.boundingRect().center()).toPoint()
    qtbot.mouseClick(view, Qt.LeftButton, pos=button_center)

    qtbot.waitUntil(lambda: spy.count() == 1, timeout=2000)
    emitted_project, config_dict = spy.at(0)

    assert emitted_project.name == model.name
    assert config_dict["io"]["mzml_paths"] == ["/data/sample1.mzML"]
    assert config_dict["io"]["xml_paths"] == ["/data/sample1.xml"]
    assert config_dict["io"]["db_paths"] == [None]
    assert config_dict["io"]["project_folder"] == model.folder
    assert config_dict["peak"]["filter_mad"] is True
    assert config_dict["annotate"]["library_path"] is None


def test_run_click_emits_a_single_library_path_as_a_bare_string(
    new_analysis_view, project, application, qtbot
):
    view, root, model = _make_page(new_analysis_view, project)
    run_button = root.findChild(QQuickItem, "runButton")

    root.addSampleRow()
    root.setSampleField(0, "mzml", "/data/sample1.mzML")
    root.setSampleField(0, "xml", "/data/sample1.xml")
    root.addLibraryPaths(["/libs/a.db"])

    application.core_bridge.run_analysis = MagicMock()
    spy = QSignalSpy(application.router.runAnalysisRequested)
    button_center = run_button.mapToScene(run_button.boundingRect().center()).toPoint()
    qtbot.mouseClick(view, Qt.LeftButton, pos=button_center)

    qtbot.waitUntil(lambda: spy.count() == 1, timeout=2000)
    _, config_dict = spy.at(0)

    assert config_dict["annotate"]["library_path"] == "/libs/a.db"


def test_run_click_emits_multiple_library_paths_as_a_list(
    new_analysis_view, project, application, qtbot
):
    view, root, model = _make_page(new_analysis_view, project)
    run_button = root.findChild(QQuickItem, "runButton")

    root.addSampleRow()
    root.setSampleField(0, "mzml", "/data/sample1.mzML")
    root.setSampleField(0, "xml", "/data/sample1.xml")
    root.addLibraryPaths(["/libs/a.db", "/libs/b.db"])

    application.core_bridge.run_analysis = MagicMock()
    spy = QSignalSpy(application.router.runAnalysisRequested)
    button_center = run_button.mapToScene(run_button.boundingRect().center()).toPoint()
    qtbot.mouseClick(view, Qt.LeftButton, pos=button_center)

    qtbot.waitUntil(lambda: spy.count() == 1, timeout=2000)
    _, config_dict = spy.at(0)

    assert config_dict["annotate"]["library_path"] == ["/libs/a.db", "/libs/b.db"]


def test_run_click_emits_config_with_mixed_mzml_and_db_only_samples(
    new_analysis_view, project, application, qtbot
):
    view, root, model = _make_page(new_analysis_view, project)
    run_button = root.findChild(QQuickItem, "runButton")

    root.addSampleRow()
    root.setSampleField(0, "mzml", "/data/sample1.mzML")
    root.setSampleField(0, "xml", "/data/sample1.xml")
    root.addDbPathsAsNewEntries(["/data/already_parsed.db"])

    application.core_bridge.run_analysis = MagicMock()

    spy = QSignalSpy(application.router.runAnalysisRequested)
    button_center = run_button.mapToScene(run_button.boundingRect().center()).toPoint()
    qtbot.mouseClick(view, Qt.LeftButton, pos=button_center)

    qtbot.waitUntil(lambda: spy.count() == 1, timeout=2000)
    _, config_dict = spy.at(0)

    assert config_dict["io"]["mzml_paths"] == ["/data/sample1.mzML", None]
    assert config_dict["io"]["xml_paths"] == ["/data/sample1.xml", None]
    assert config_dict["io"]["db_paths"] == [None, "/data/already_parsed.db"]


# ---------------------------------------------------------------------- #
# Target list matching tab (bespoke — polarity-filtered adducts multiselect)
# ---------------------------------------------------------------------- #


def _open_target_list_tab(view, root, find_visual_child, qtbot):
    tab_button = find_visual_child(root, "tabButton_target_list")
    center = tab_button.mapToScene(tab_button.boundingRect().center()).toPoint()
    qtbot.mouseClick(view, Qt.LeftButton, pos=center)
    qtbot.wait(50)


def test_target_list_tab_button_exists(new_analysis_view, project, find_visual_child):
    _, root, _ = _make_page(new_analysis_view, project)
    assert find_visual_child(root, "tabButton_target_list") is not None


def test_target_list_tab_defaults_to_positive_polarity_and_adducts(
    new_analysis_view, project, find_visual_child, qtbot
):
    view, root, _ = _make_page(new_analysis_view, project)
    _open_target_list_tab(view, root, find_visual_child, qtbot)

    polarity_combo = find_visual_child(root, "field_target_list_polarity")
    assert polarity_combo is not None
    assert polarity_combo.property("currentText") == "positive"

    positive_checkbox = find_visual_child(root, "field_target_list_adduct_[M+H]+")
    assert positive_checkbox is not None
    assert positive_checkbox.property("checked") is False
    # Negative-mode adducts aren't offered while polarity is positive.
    assert find_visual_child(root, "field_target_list_adduct_[M-H]-") is None


def test_target_list_switching_polarity_swaps_adduct_options_and_clears_selection(
    new_analysis_view, project, find_visual_child, qtbot
):
    view, root, _ = _make_page(new_analysis_view, project)
    _open_target_list_tab(view, root, find_visual_child, qtbot)

    positive_checkbox = find_visual_child(root, "field_target_list_adduct_[M+H]+")
    center = positive_checkbox.mapToScene(positive_checkbox.boundingRect().center()).toPoint()
    qtbot.mouseClick(view, Qt.LeftButton, pos=center)
    qtbot.wait(50)
    assert positive_checkbox.property("checked") is True

    polarity_combo = find_visual_child(root, "field_target_list_polarity")
    polarity_combo.setProperty("currentIndex", 1)
    qtbot.wait(50)

    assert polarity_combo.property("currentText") == "negative"
    # The old (positive) checkbox is gone entirely, and the new selection
    # was cleared rather than carrying over a now-invalid adduct label.
    assert find_visual_child(root, "field_target_list_adduct_[M+H]+") is None
    negative_checkbox = find_visual_child(root, "field_target_list_adduct_[M-H]-")
    assert negative_checkbox is not None
    assert negative_checkbox.property("checked") is False


def test_target_list_paths_field_is_read_only(
    new_analysis_view, project, find_visual_child, qtbot
):
    view, root, _ = _make_page(new_analysis_view, project)
    _open_target_list_tab(view, root, find_visual_child, qtbot)

    control = find_visual_child(root, "field_target_list_paths")
    assert control is not None
    assert control.property("readOnly") is True


def test_target_list_paths_dialog_is_independent_from_library_path_dialog(
    new_analysis_view, project
):
    # A shared file dialog hardcoded to one field would silently overwrite
    # it when reused for a second path_list-shaped field (the bug the
    # bespoke target_list tab was built to avoid) — assert the two dialogs
    # are genuinely distinct objects.
    _, root, _ = _make_page(new_analysis_view, project)

    target_list_dialog = root.findChild(QObject, "targetListPathsDialog")
    library_dialog = root.findChild(QObject, "libraryPathDialog")
    assert target_list_dialog is not None
    assert library_dialog is not None
    assert target_list_dialog is not library_dialog


def test_run_click_emits_target_list_config_with_defaults(
    new_analysis_view, project, application, qtbot
):
    view, root, model = _make_page(new_analysis_view, project)
    run_button = root.findChild(QQuickItem, "runButton")

    root.addSampleRow()
    root.setSampleField(0, "mzml", "/data/sample1.mzML")
    root.setSampleField(0, "xml", "/data/sample1.xml")

    application.core_bridge.run_analysis = MagicMock()
    spy = QSignalSpy(application.router.runAnalysisRequested)
    button_center = run_button.mapToScene(run_button.boundingRect().center()).toPoint()
    qtbot.mouseClick(view, Qt.LeftButton, pos=button_center)

    qtbot.waitUntil(lambda: spy.count() == 1, timeout=2000)
    _, config_dict = spy.at(0)

    assert config_dict["target_list"]["paths"] is None
    assert config_dict["target_list"]["polarity"] == "positive"
    assert config_dict["target_list"]["adducts"] is None
    assert config_dict["target_list"]["match_ppm"] == 10.0


def test_run_click_emits_target_list_config_with_selected_adducts_and_path(
    new_analysis_view, project, application, qtbot, find_visual_child
):
    view, root, model = _make_page(new_analysis_view, project)
    run_button = root.findChild(QQuickItem, "runButton")

    root.addSampleRow()
    root.setSampleField(0, "mzml", "/data/sample1.mzML")
    root.setSampleField(0, "xml", "/data/sample1.xml")

    _open_target_list_tab(view, root, find_visual_child, qtbot)

    path_field = find_visual_child(root, "field_target_list_paths")
    path_field.setProperty("text", "/data/targets.csv")

    checkbox = find_visual_child(root, "field_target_list_adduct_[M+H]+")
    center = checkbox.mapToScene(checkbox.boundingRect().center()).toPoint()
    qtbot.mouseClick(view, Qt.LeftButton, pos=center)
    qtbot.wait(50)

    application.core_bridge.run_analysis = MagicMock()
    spy = QSignalSpy(application.router.runAnalysisRequested)
    button_center = run_button.mapToScene(run_button.boundingRect().center()).toPoint()
    qtbot.mouseClick(view, Qt.LeftButton, pos=button_center)

    qtbot.waitUntil(lambda: spy.count() == 1, timeout=2000)
    _, config_dict = spy.at(0)

    assert config_dict["target_list"]["paths"] == "/data/targets.csv"
    assert config_dict["target_list"]["polarity"] == "positive"
    assert config_dict["target_list"]["adducts"] == ["[M+H]+"]
    assert config_dict["target_list"]["match_ppm"] == 10.0


# ---------------------------------------------------------------------- #
# Load config from a previous analysis (template picker)
# ---------------------------------------------------------------------- #


def _as_list(value):
    """A QML `property var` list read back via `.property()`: an array of
    plain strings comes back as a native Python list already, but an
    array of objects (dicts) stays wrapped in a QJSValue needing
    `.toVariant()` — same helper as test_roi_design_window.py's
    identical need."""
    return value.toVariant() if hasattr(value, "toVariant") else value


def _rich_template_config():
    """A config dict shaped like Config.to_dict(), with `io`, one generic
    group, and `target_list` all set away from their schema defaults —
    so a test can tell "loaded" apart from "coincidentally matches the
    default" for every tab applyConfig touches."""
    return {
        "version": 16,
        "io": {
            "project_folder": "/tmp/proj",
            "mzml_paths": ["/data/s1.mzML", None],
            "xml_paths": ["/data/s1.xml", None],
            "db_paths": [None, "/data/already_parsed.db"],
            "out_dir": "/tmp/proj/custom_output",
        },
        "peak": {"filter_mad": False},  # default is True
        "target_list": {
            "paths": "/data/targets.csv",
            "polarity": "negative",
            "adducts": ["[M-H]-"],
            "match_ppm": 15.0,
        },
        # Two paths, deliberately — this exact shape (Config.to_dict()'s
        # own list-of-str for a multi-library run) is what reproduced the
        # reported "library path shows as '//'" bug: the value crosses the
        # Python/QML boundary as an array-*like* object that fails
        # Array.isArray(), so the old code fell through to stringifying it
        # directly instead of joining it.
        "annotate": {"library_path": ["/libs/a.db", "/libs/b.db"]},
    }


def _project_with_template_run():
    from msianalyzer.core.project.project import Project

    p = Project(name="template_test_proj")
    p.runs["run-1"] = {
        "id": "run-1",
        "start_date": "2026-01-01 12:00:00",
        "end_date": "2026-01-01 12:30:00",
        "status": "COMPLETED",
        "config_path": "/tmp/proj/configs/run_1.yaml",
        "config": _rich_template_config(),
    }
    return p


def test_apply_config_prefills_io_generic_and_target_list_tabs(
    new_analysis_view, find_visual_child
):
    _, root, _ = _make_page(new_analysis_view, _project_with_template_run())

    root.applyConfig(_rich_template_config())

    rows = _as_list(root.property("sampleRows"))
    assert rows == [{"mzml": "/data/s1.mzML", "xml": "/data/s1.xml"}]
    assert _as_list(root.property("dbOnlyPaths")) == ["/data/already_parsed.db"]

    out_dir_field = find_visual_child(root, "outDirField")
    assert out_dir_field.property("text") == "/tmp/proj/custom_output"

    filter_mad = find_visual_child(root, "field_peak_filter_mad")
    assert filter_mad.property("checked") is False

    paths_field = find_visual_child(root, "field_target_list_paths")
    assert paths_field.property("text") == "/data/targets.csv"
    polarity_combo = find_visual_child(root, "field_target_list_polarity")
    assert polarity_combo.property("currentText") == "negative"
    assert _as_list(root.property("targetListSelectedAdducts")) == ["[M-H]-"]
    ppm_field = find_visual_child(root, "field_target_list_match_ppm")
    assert ppm_field.property("text") == "15"

    assert _as_list(root.property("libraryPaths")) == ["/libs/a.db", "/libs/b.db"]


def test_reset_to_blank_restores_defaults_after_a_load(
    new_analysis_view, find_visual_child
):
    _, root, _ = _make_page(new_analysis_view, _project_with_template_run())
    root.applyConfig(_rich_template_config())

    root.resetToBlank()

    assert _as_list(root.property("sampleRows")) == []
    assert _as_list(root.property("dbOnlyPaths")) == []

    out_dir_field = find_visual_child(root, "outDirField")
    assert out_dir_field.property("text").endswith("/output")

    filter_mad = find_visual_child(root, "field_peak_filter_mad")
    assert filter_mad.property("checked") is True  # back to schema default

    polarity_combo = find_visual_child(root, "field_target_list_polarity")
    assert polarity_combo.property("currentText") == "positive"
    assert _as_list(root.property("targetListSelectedAdducts")) == []
    paths_field = find_visual_child(root, "field_target_list_paths")
    assert paths_field.property("text") == ""
    assert _as_list(root.property("libraryPaths")) == []


def test_template_combo_lists_start_blank_and_previous_runs(
    new_analysis_view, find_visual_child
):
    _, root, _ = _make_page(new_analysis_view, _project_with_template_run())

    combo = find_visual_child(root, "templateCombo")
    assert combo is not None
    options = combo.property("model")
    labels = [o["label"] for o in options]
    assert labels[0] == "Start blank"
    assert any("COMPLETED" in label for label in labels[1:])


def test_template_combo_hidden_when_project_has_no_previous_runs(
    new_analysis_view, project, find_visual_child
):
    _, root, _ = _make_page(new_analysis_view, project)

    row = find_visual_child(root, "templateRow")
    assert row is not None
    assert row.property("visible") is False
