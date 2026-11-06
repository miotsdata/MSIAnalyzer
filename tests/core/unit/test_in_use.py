import datetime
import json
import socket
import sqlite3

import pytest

from msianalyzer.core.utils import in_use
from msianalyzer.core.utils.db import set_journal_mode


def _write_marker(db, host, minutes_ago=0):
    beat = datetime.datetime.now().astimezone() - datetime.timedelta(minutes=minutes_ago)
    in_use.marker_path(db).write_text(json.dumps({
        "host": host, "user": "u", "pid": 1, "token": "x",
        "since": beat.isoformat(), "heartbeat": beat.isoformat(),
    }))


def test_claim_writes_marker_and_release_removes_it(tmp_path):
    db = tmp_path / "analysis_x.db"
    claim = in_use.Claim(db).acquire()
    info = in_use.read_marker(db)
    assert info["host"] == socket.gethostname() and info["token"] == claim.token
    # This computer never counts as "elsewhere".
    assert in_use.held_elsewhere(db) is None
    claim.release()
    assert not in_use.marker_path(db).exists()


def test_fresh_marker_from_another_computer_blocks_stale_one_does_not(tmp_path):
    db = tmp_path / "analysis_x.db"
    _write_marker(db, "other-pc")
    assert in_use.held_elsewhere(db)["host"] == "other-pc"
    with pytest.raises(in_use.InUseError):
        in_use.Claim(db).acquire()

    _write_marker(db, "other-pc", minutes_ago=in_use.STALE_S / 60 + 1)
    assert in_use.held_elsewhere(db) is None
    in_use.Claim(db).acquire().release()


def test_release_leaves_a_marker_taken_over_by_someone_else(tmp_path):
    db = tmp_path / "analysis_x.db"
    claim = in_use.Claim(db).acquire()
    in_use.Claim(db).acquire()  # same computer takes over (e.g. GUI during a run)
    claim.release()
    assert in_use.marker_path(db).exists()


def test_set_journal_mode_round_trip(tmp_path):
    db = tmp_path / "a.db"
    sqlite3.connect(db).close()
    assert set_journal_mode(db, "wal") == "wal"
    assert set_journal_mode(db, "delete") == "delete"
    assert not (tmp_path / "a.db-wal").exists()


def test_h5ad_writes_into_a_read_only_analysis_are_refused(tmp_path):
    import h5py

    from msianalyzer.core.utils import h5ad_io

    db = tmp_path / "analysis_x.db"
    h5 = tmp_path / "s1.h5ad"
    h5py.File(h5, "w").close()
    in_use.set_read_only(db, True)
    try:
        with pytest.raises(in_use.ReadOnlyError):
            with h5ad_io.open_for_write(h5):
                pass
    finally:
        in_use.set_read_only(db, False)
    with h5ad_io.open_for_write(h5):
        pass
