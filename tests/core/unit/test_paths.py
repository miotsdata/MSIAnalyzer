from pathlib import Path

from msianalyzer.core.utils.paths import relative_to_project, resolve_in_project


def test_relative_to_project_only_for_paths_inside_it(tmp_path):
    assert relative_to_project(tmp_path / "output" / "run1", tmp_path) == "output/run1"
    assert relative_to_project("/elsewhere/lib.db", tmp_path) == "/elsewhere/lib.db"
    assert relative_to_project("output/run1", tmp_path) == "output/run1"
    assert relative_to_project(None, tmp_path) is None


def test_resolve_relative_and_absolute_outside_project(tmp_path):
    assert resolve_in_project("output/run1", tmp_path) == tmp_path / "output" / "run1"
    assert resolve_in_project(str(tmp_path), "/nowhere") == tmp_path


def test_opened_project_wins_over_an_old_location_that_still_exists(tmp_path):
    # Local copy at old/P, same project opened from nas/P: show nas/P's results.
    (tmp_path / "old" / "P" / "output" / "run1").mkdir(parents=True)
    (tmp_path / "nas" / "P" / "output" / "run1").mkdir(parents=True)
    stored = str(tmp_path / "old" / "P" / "output" / "run1")
    assert resolve_in_project(stored, tmp_path / "nas" / "P") == (
        tmp_path / "nas" / "P" / "output" / "run1"
    )


def test_resolve_path_written_before_the_project_moved(tmp_path):
    # Project was at /data/Projects/P; now opened from the NAS (or Windows).
    (tmp_path / "output" / "run1").mkdir(parents=True)
    expected = tmp_path / "output" / "run1"
    assert resolve_in_project("/data/Projects/P/output/run1", tmp_path) == expected
    assert resolve_in_project(r"Z:\Projects\P\output\run1", tmp_path) == expected


def test_resolve_unknown_path_is_returned_unchanged(tmp_path):
    assert resolve_in_project("/data/Projects/P/output/gone", tmp_path) == Path(
        "/data/Projects/P/output/gone"
    )
