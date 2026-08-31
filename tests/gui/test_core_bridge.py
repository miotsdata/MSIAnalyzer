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


def test_load_project_valid_path_emit_projectLoaded():
    pass
