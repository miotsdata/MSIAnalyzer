import subprocess
import sys
import textwrap

from msianalyzer.gui.utils.crash_diagnostics import CrashDiagnostics

_CRASHING_CHILD = textwrap.dedent("""
    import ctypes, logging, sys
    from pathlib import Path
    from PySide6.QtCore import qWarning
    from msianalyzer.gui.utils.crash_diagnostics import CrashDiagnostics

    log_dir = Path(sys.argv[1])
    logging.basicConfig(filename=log_dir / "debug.log", level=logging.DEBUG)
    CrashDiagnostics(log_dir).start()
    qWarning("about to crash")
    logging.shutdown()
    ctypes.string_at(0)  # access violation, like a crash inside Qt
""")


def test_a_hard_crash_leaves_a_python_stack_and_qt_messages(tmp_path):
    result = subprocess.run(
        [sys.executable, "-c", _CRASHING_CHILD, str(tmp_path)], capture_output=True, timeout=60
    )

    assert result.returncode != 0
    (crash_file,) = tmp_path.glob("crash_*.txt")
    assert "Fatal Python error" in crash_file.read_text()
    assert "about to crash" in (tmp_path / "debug.log").read_text()


def test_a_clean_session_leaves_no_crash_file(tmp_path):
    diagnostics = CrashDiagnostics(tmp_path)
    diagnostics.start()
    diagnostics.stop()

    assert list(tmp_path.glob("crash_*.txt")) == []
