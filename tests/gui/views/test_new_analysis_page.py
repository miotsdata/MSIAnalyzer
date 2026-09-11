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


def test_multi_select_mzml_fills_target_row_then_appends_new_rows(
    new_analysis_view, project
):
    _, root, _ = _make_page(new_analysis_view, project)

    root.addSampleRow()
    root.addSampleRowsFromMzmlPaths(0, ["/data/s1.mzML", "/data/s2.mzML", "/data/s3.mzML"])

    rows = root.property("sampleRows").toVariant()
    assert len(rows) == 3
    assert [r["mzml"] for r in rows] == ["/data/s1.mzML", "/data/s2.mzML", "/data/s3.mzML"]
    # xml is left for the user to pair per-row, same as a single-file pick.
    assert all(r["xml"] == "" for r in rows)


def test_multi_select_mzml_with_no_paths_is_a_no_op(new_analysis_view, project):
    _, root, _ = _make_page(new_analysis_view, project)

    root.addSampleRow()
    root.addSampleRowsFromMzmlPaths(0, [])

    rows = root.property("sampleRows").toVariant()
    assert len(rows) == 1
    assert rows[0]["mzml"] == ""


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

    control = find_visual_child(root, "field_purity_max_interpixel_gap_sec")
    assert control is not None
    assert control.property("text") == ""


def test_library_path_field_is_read_only(new_analysis_view, project, find_visual_child):
    _, root, _ = _make_page(new_analysis_view, project)

    control = find_visual_child(root, "field_annotate_library_path")
    assert control is not None
    assert control.property("readOnly") is True


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
