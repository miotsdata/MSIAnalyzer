import uuid

from msianalyzer.gui.utils import single_instance


def _unique_name() -> str:
    return f"test-msianalyzer-{uuid.uuid4().hex}"


def test_first_acquire_returns_a_listening_server(qapp):
    name = _unique_name()
    server = single_instance.acquire(name)
    try:
        assert server is not None
        assert server.isListening()
    finally:
        server.close()


def test_second_acquire_while_first_is_alive_returns_none(qapp):
    name = _unique_name()
    first = single_instance.acquire(name)
    try:
        second = single_instance.acquire(name)
        assert second is None
    finally:
        first.close()


def test_acquire_after_the_first_closes_succeeds_again(qapp):
    # Clean shutdown (not a crash) — the name must be free again.
    name = _unique_name()
    first = single_instance.acquire(name)
    first.close()

    second = single_instance.acquire(name)
    try:
        assert second is not None
        assert second.isListening()
    finally:
        second.close()


def test_second_launch_activates_the_first_instance(qapp, qtbot):
    name = _unique_name()
    server = single_instance.acquire(name)
    activated = []
    single_instance.connect_activation(server, lambda: activated.append(True))
    try:
        second = single_instance.acquire(name)
        assert second is None
        qtbot.waitUntil(lambda: len(activated) == 1, timeout=2000)
    finally:
        server.close()
