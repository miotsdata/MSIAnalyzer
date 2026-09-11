from os import name
import string
from hypothesis import given, settings, HealthCheck, strategies as st
from msianalyzer.gui.utils.application import Application
from msianalyzer.gui.utils.core_bridge import CoreBridge
from msianalyzer.core.run.run import Run
import os

VALID_CHARS = string.ascii_letters + string.digits + "_- "
INVALID_CHARS = "".join(
    c for c in (chr(i) for i in range(32, 127)) if c not in VALID_CHARS
)


def test_load_project_empy_path_emit_invalidProjectPath(application):
    received = []
    application.core_bridge.invalidProjectPath.connect(received.append)

    application.core_bridge.load_project("")

    assert len(received) == 1
    assert "no file provided" in received[0]


def test_load_project_wrong_path_emit_invalidProjectPath(application):
    received = []
    application.core_bridge.invalidProjectPath.connect(received.append)

    application.core_bridge.load_project("notexisting.yaml")

    assert len(received) == 1
    assert "does not exists" in received[0]


@settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(name=st.text(alphabet=VALID_CHARS, min_size=1, max_size=50))
def test_create_project_valid_emits_projectLoaded(name, tmp_path):
    application = Application()
    received = []
    application.core_bridge.projectLoaded.connect(received.append)

    application.core_bridge.create_project(name, tmp_path)

    assert len(received) == 1
    assert received[0].name == name


@settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    prefix=st.text(alphabet=VALID_CHARS, min_size=0, max_size=20),
    bad_char=st.sampled_from(INVALID_CHARS),
    suffix=st.text(alphabet=VALID_CHARS, min_size=0, max_size=20),
)
def test_create_project_invalid_name_emits_invalidCreateProjectName(
    prefix, bad_char, suffix, tmp_path
):
    name = prefix + bad_char + suffix
    application = Application()
    received = []
    application.core_bridge.invalidCreateProjectName.connect(received.append)

    application.core_bridge.create_project(name, tmp_path)

    assert len(received) == 1
    assert "only letters, numbers" in received[0]


@settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(name=st.text(alphabet=VALID_CHARS, min_size=1, max_size=50))
def test_create_project_folder_already_exists_emits_invalidCreateProjectPath(
    name, tmp_path
):
    application = Application()
    received = []
    application.core_bridge.invalidCreateProjectPath.connect(received.append)

    (tmp_path / name).mkdir()
    application.core_bridge.create_project(name, tmp_path)

    assert len(received) == 1
    assert "exists" in received[0]


@settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(name=st.text(alphabet=VALID_CHARS, min_size=1, max_size=50))
def test_create_project_folder_no_permissions_emits_invalidCreateProjectPath(
    name, tmp_path
):
    application = Application()
    received = []
    application.core_bridge.invalidCreateProjectPath.connect(received.append)

    os.chmod(tmp_path, 0o000)

    try:
        application.core_bridge.create_project(name, tmp_path)

        assert len(received) == 1
        assert "Permission" in received[0]
    finally:
        os.chmod(tmp_path, 0o700)


# ==============================================================================
# TESTS FOR run_analysis
# ==============================================================================


def test_run_analysis_invalid_config_emits_invalidConfig(tmp_path):
    bridge = CoreBridge()
    received = []
    bridge.invalidConfig.connect(received.append)

    bridge.run_analysis(
        {
            "io": {"mzml_paths": [], "xml_paths": []},
            "peak": {"this_field_does_not_exist": 1},
        },
        str(tmp_path),
    )

    assert len(received) == 1
    assert "Invalid" in received[0] or "invalid" in received[0]


def test_run_analysis_valid_config_writes_yaml_and_completes(tmp_path, mocker, qtbot):
    (tmp_path / "configs").mkdir()
    bridge = CoreBridge()

    mocker.patch.object(
        Run, "start", lambda self, **kwargs: kwargs["on_step"]("process_samples", "started")
    )

    config_dict = {
        "io": {
            "mzml_paths": [str(tmp_path / "a.mzML")],
            "xml_paths": [str(tmp_path / "a.xml")],
            "out_dir": str(tmp_path / "out"),
            "db_paths": [],
        }
    }

    with qtbot.waitSignal(bridge.runCompleted, timeout=2000):
        bridge.run_analysis(config_dict, str(tmp_path))

    yaml_files = list((tmp_path / "configs").glob("run_*.yaml"))
    assert len(yaml_files) == 1


def test_run_analysis_forwards_run_started_and_step_changed(tmp_path, mocker, qtbot):
    (tmp_path / "configs").mkdir()
    bridge = CoreBridge()

    mocker.patch.object(
        Run, "start", lambda self, **kwargs: kwargs["on_step"]("process_samples", "started")
    )

    config_dict = {
        "io": {
            "mzml_paths": [str(tmp_path / "a.mzML")],
            "xml_paths": [str(tmp_path / "a.xml")],
            "out_dir": str(tmp_path / "out"),
            "db_paths": [],
        }
    }

    with qtbot.waitSignal(bridge.runStarted, timeout=2000):
        with qtbot.waitSignal(bridge.runStepChanged, timeout=2000) as step_spy:
            bridge.run_analysis(config_dict, str(tmp_path))

    assert step_spy.args == ["process_samples", "started"]


def test_run_analysis_forces_project_folder_and_version(tmp_path, mocker, qtbot):
    (tmp_path / "configs").mkdir()
    bridge = CoreBridge()

    captured = {}

    def fake_start(self, **kwargs):
        captured["config"] = kwargs["config"]

    mocker.patch.object(Run, "start", fake_start)

    config_dict = {
        "io": {
            "mzml_paths": [str(tmp_path / "a.mzML")],
            "xml_paths": [str(tmp_path / "a.xml")],
            "out_dir": str(tmp_path / "out"),
            "db_paths": [],
            "project_folder": "/some/other/untrusted/path",
        }
    }

    with qtbot.waitSignal(bridge.runCompleted, timeout=2000):
        bridge.run_analysis(config_dict, str(tmp_path))

    assert str(captured["config"].io.project_folder) == str(tmp_path)
