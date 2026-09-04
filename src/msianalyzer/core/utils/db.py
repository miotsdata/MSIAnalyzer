"""SQLite write helpers that log the offending values on failure.

Every ``sqlite3.Error`` from a write is logged at ERROR — including the exact
parameters that triggered it — and then re-raised unchanged. For batched
writes the failing row is isolated by replaying the batch one row at a time.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Iterable, Sequence

__all__ = ["safe_execute", "safe_executemany"]

_MAX_PARAMS_REPR = 600


def _summarize_sql(sql: str) -> str:
    return " ".join(sql.split())


def _clip(text: str, limit: int = _MAX_PARAMS_REPR) -> str:
    return text if len(text) <= limit else text[:limit] + " …(truncated)"


def _params_repr(params: Sequence) -> str:
    params = tuple(params)
    if not params:
        return "(none — statement takes no bound parameters, e.g. INSERT … SELECT)"
    return _clip(repr(params))


def _extra(source: str | Path | None) -> dict | None:
    return {"source_file": str(source)} if source else None


def safe_execute(
    con: sqlite3.Connection,
    sql: str,
    params: Sequence = (),
    *,
    table: str,
    logger: logging.Logger,
    source: str | Path | None = None,
) -> sqlite3.Cursor:
    """``con.execute(sql, params)`` — logs params + re-raises on ``sqlite3.Error``.

    Args:
        source: Optional path (usually the DB file) put on the error
            record's ``source_file`` so the log's source column shows it.
    """
    try:
        return con.execute(sql, params)
    except sqlite3.Error as exc:
        logger.error(
            "DB write failed on %s: %s | sql=%s | params=%s",
            table,
            exc,
            _summarize_sql(sql),
            _params_repr(params),
            extra=_extra(source),
        )
        raise


def safe_executemany(
    con: sqlite3.Connection,
    sql: str,
    rows: Iterable[Sequence],
    *,
    table: str,
    logger: logging.Logger,
    source: str | Path | None = None,
) -> None:
    """``con.executemany(sql, rows)`` — on failure, pinpoint and log the bad row.

    The batch runs inside a ``SAVEPOINT`` so a failure leaves no partial
    rows behind; it is then replayed one row at a time to name the first
    offending row (and its index) in the log before the original exception
    is re-raised.

    Args:
        source: Optional path (usually the DB file) put on the error
            record's ``source_file``.
    """
    rows = list(rows)
    con.execute("SAVEPOINT _safe_em")
    try:
        con.executemany(sql, rows)
        con.execute("RELEASE _safe_em")
        return
    except sqlite3.Error as exc:
        con.execute("ROLLBACK TO _safe_em")

        offending_index = offending_row = None
        try:
            for i, row in enumerate(rows):
                try:
                    con.execute(sql, row)
                except sqlite3.Error:
                    offending_index, offending_row = i, row
                    break
        finally:
            con.execute("ROLLBACK TO _safe_em")
            con.execute("RELEASE _safe_em")

        if offending_row is not None:
            logger.error(
                "DB batch write failed on %s: %s | sql=%s | row %d of %d = %s",
                table,
                exc,
                _summarize_sql(sql),
                offending_index,
                len(rows),
                _clip(repr(tuple(offending_row))),
                extra=_extra(source),
            )
        else:
            logger.error(
                "DB batch write failed on %s: %s | sql=%s | %d row(s), "
                "offending row not isolated on replay",
                table,
                exc,
                _summarize_sql(sql),
                len(rows),
                extra=_extra(source),
            )
        raise
