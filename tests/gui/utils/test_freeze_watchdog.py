"""FreezeWatchdog (ADR 50, Phase 0): dumps every thread's stack when the GUI
thread stops processing events for longer than a threshold."""

from __future__ import annotations

import logging
import time

from msianalyzer.gui.utils.freeze_watchdog import FreezeWatchdog


def test_a_stall_longer_than_the_threshold_is_dumped_and_logged(tmp_path, qtbot, caplog):
    watchdog = FreezeWatchdog(tmp_path, threshold_s=0.3, interval_ms=50)
    watchdog.start()
    qtbot.wait(150)  # a few healthy ticks first

    with caplog.at_level(logging.WARNING, logger="msianalyzer.gui.utils.freeze_watchdog"):
        time.sleep(0.8)  # block the GUI thread: no event processing at all
        qtbot.wait(200)  # the next tick notices the stall and annotates it

    dump = watchdog.dump_path.read_text()
    assert "Timeout" in dump  # faulthandler's own header
    assert "test_freeze_watchdog.py" in dump  # the blocked GUI thread's stack
    assert "GUI thread stalled" in dump  # our annotation after the fact
    assert any("GUI thread stalled" in r.getMessage() for r in caplog.records)
    watchdog.stop()
    assert watchdog.dump_path.exists()  # kept: it has content


def test_short_stalls_are_logged_but_not_dumped(tmp_path, qtbot, caplog):
    watchdog = FreezeWatchdog(tmp_path, threshold_s=2.0, interval_ms=50, warn_s=0.2)
    watchdog.start()
    qtbot.wait(100)
    with caplog.at_level(logging.WARNING, logger="msianalyzer.gui.utils.freeze_watchdog"):
        time.sleep(0.5)
        qtbot.wait(150)

    assert watchdog.dump_path.read_text() == ""
    assert any("GUI thread stalled" in r.getMessage() for r in caplog.records)
    watchdog.stop()


def test_stop_removes_an_empty_dump_file(tmp_path, qtbot):
    watchdog = FreezeWatchdog(tmp_path, threshold_s=2.0, interval_ms=50)
    watchdog.start()
    qtbot.wait(150)
    path = watchdog.dump_path
    watchdog.stop()
    assert not path.exists()
    # and nothing fires after stop
    time.sleep(0.1)
    assert list(tmp_path.iterdir()) == []
