from unittest.mock import MagicMock

from PySide6.QtCore import Qt
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


def test_library_path_field_is_read_only(new_analysis_view, project, find_visual_child):
    _, root, _ = _make_page(new_analysis_view, project)

    control = find_visual_child(root, "field_annotate_library_path")
    assert control is not None
    assert control.property("readOnly") is True


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
    assert config_dict["io"]["project_folder"] == model.folder
    assert config_dict["peak"]["filter_mad"] is True
    assert config_dict["annotate"]["library_path"] is None
