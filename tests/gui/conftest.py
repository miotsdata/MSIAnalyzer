# tests/gui/conftest.py
import subprocess
import pytest
from msianalyzer.gui.main import build_engine


@pytest.fixture(scope="session", autouse=True)
def compile_qml_resources():
    subprocess.run(
        [
            "pyside6-rcc",
            "src/msianalyzer/gui/qml/resources.qrc",
            "-o",
            "src/msianalyzer/gui/resources_rc.py",
        ],
        check=True,
    )
    print("Compiled qml resources.")


@pytest.fixture
def engine(qapp):
    eng = build_engine(qapp)
    yield eng
    eng.deleteLater()
