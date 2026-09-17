"""Export menu backing functions (Annotation / Integration / Image).

One module for all three, not split across `core/annotation/` (where
only the Annotation export would conceptually belong) — Integration and
Image both operate over every feature/sample in the analysis, not
anything annotation-specific, so grouping the three together as "what the
Export menu does" reads more honestly than scattering them by superficial
topic.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path

from .analysis_db import load_feature_representative_annotations

logger = logging.getLogger(__name__)

#: Column order in the exported file, and the DataFrame column each comes
#: from (see `load_feature_representative_annotations`'s own docstring for
#: what's populated per tier). `feature_id`/`mz` are the only numeric
#: columns — every other field is free text, some of it library-supplied
#: and never guaranteed comma-free (a compound name, in particular).
_ANNOTATION_EXPORT_COLUMNS = [
    "feature_id",
    "mz",
    "compound_name",
    "compound_formula",
    "adduct",
    "inchikey",
    "cas",
    "hmdb",
    "library_name",
    "source",
]

_NUMERIC_ANNOTATION_EXPORT_COLUMNS = {"feature_id", "mz"}


def _csv_dialect_for(dest_path: Path) -> str:
    """`"\\t"` for `.txt`, `","` for anything else (`.csv` included) —
    the file extension the user picked in the save dialog is what decides
    the delimiter, not a separate format choice."""
    return "\t" if dest_path.suffix.lower() == ".txt" else ","


def export_annotation_table(db_path: str | Path, dest_path: str | Path) -> None:
    """Write one row per feature — its representative identity, across
    every tier, plus a row for a feature with no identity at all — to
    ``dest_path`` as CSV or tab-delimited text.

    Args:
        db_path: The analysis' SQLite database.
        dest_path: Where to write the export. ``.txt`` -> tab-delimited,
            anything else (``.csv`` included) -> comma-delimited.

    Every string field is quoted (``"..."``) regardless of whether it
    strictly needs to be — a compound name is free text from a library
    file and isn't guaranteed comma-free, so this isn't optional the way
    `csv`'s own default (quote only when the delimiter/quote character/a
    newline is actually present) would make it. `feature_id`/`mz` are the
    only fields written unquoted.
    """
    dest_path = Path(dest_path)
    df = load_feature_representative_annotations(db_path, include_unidentified=True)

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(dest_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(
            f, delimiter=_csv_dialect_for(dest_path), quoting=csv.QUOTE_NONNUMERIC
        )
        writer.writerow(_ANNOTATION_EXPORT_COLUMNS)
        for record in df.to_dict("records"):
            writer.writerow(
                [
                    _numeric_or_blank(record.get(col))
                    if col in _NUMERIC_ANNOTATION_EXPORT_COLUMNS
                    else _text_or_blank(record.get(col))
                    for col in _ANNOTATION_EXPORT_COLUMNS
                ]
            )
    logger.info("exported annotation table (%d features) to %s", len(df), dest_path)


def _numeric_or_blank(value):
    """`None`/`NaN` -> `0` (still numeric, so `QUOTE_NONNUMERIC` leaves it
    unquoted) — `feature_id`/`mz` are never actually missing in practice
    (every row comes from the `features` table itself), this is only a
    defensive fallback, not a real case."""
    if value is None or (isinstance(value, float) and value != value):  # NaN
        return 0
    return value


def _text_or_blank(value) -> str:
    """`None`/`NaN` -> `""`, everything else -> `str(value)` — so a
    missing field writes as an empty (still quoted) cell, not the literal
    text ``"None"``/``"nan"``."""
    if value is None or (isinstance(value, float) and value != value):  # NaN
        return ""
    return str(value)
