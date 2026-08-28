# tests/gui/conftest.py
import subprocess
import pytest
from msianalyzer.core.project.project import Project
from msianalyzer.gui.main import build_engine
from msianalyzer.gui.utils import Router
from msianalyzer.gui.utils.application import Application

import os


@pytest.fixture(scope="session", autouse=True)
def compile_qml_resources():
    rc_path = "src/msianalyzer/gui/resources_rc.py"
    if os.path.exists(rc_path):
        os.remove(rc_path)
    subprocess.run(
        ["pyside6-rcc", "src/msianalyzer/gui/qml/resources.qrc", "-o", rc_path],
        check=True,
    )
    print("Compiled qml resources.")


@pytest.fixture
def router():
    return Router()


@pytest.fixture
def engine(qapp, application):
    eng = build_engine(qapp, application)
    yield eng
    eng.deleteLater()


@pytest.fixture
def application():
    return Application()


@pytest.fixture
def project():
    p = Project(name="test_proj")
    return p
