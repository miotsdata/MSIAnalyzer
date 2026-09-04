"""
mzml_parser.py
Core mzML parsing module. No GUI dependencies, no pixel/spatial logic.

Classes:
    MzmlFile: Lightweight result object describing a completed parse.
    MzmlParser: Streams an mzML file and writes MS1 / MS2 SQLite databases.
"""

from __future__ import annotations

from datetime import datetime
import json
import re
import sqlite3
import zlib
from base64 import b64decode
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Optional, override

import numpy as np

from msianalyzer.version import __version__ as SOFTWARE_VERSION

import logging

from msianalyzer.core.utils.db import safe_execute, safe_executemany
from msianalyzer.core.utils.logging_utils import log_call

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public result object
# ---------------------------------------------------------------------------


@dataclass
class MzmlFile:
    """Describes the result of a completed mzML parse.

    Attributes:
        source_path: Source mzML file path.
        ms1_db_path: Path to the written MS1/MS2 SQLite database.
        n_ms1: Number of MS1 scans stored.
        n_ms2: Number of MS2 scans stored.
        rt_range: Retention-time range, in seconds, across stored scans.
        ms1_mz_range: m/z range across stored MS1 scans.
        ms2_precursor_mz_range: Range of MS2 precursor m/z values stored.
        instrument_info: Instrument metadata extracted from the header.
    """

    source_path: Path
    ms1_db_path: Path
    n_ms1: int = 0
    n_ms2: int = 0
    rt_range: tuple[float, float] = (0.0, 0.0)
    ms1_mz_range: tuple[float, float] = (0.0, 0.0)
    ms2_precursor_mz_range: tuple[float, float] = (0.0, 0.0)
    instrument_info: dict[str, str] = None

    def ms1_connection(self) -> sqlite3.Connection:
        """Return a new read-only SQLite connection to the MS1 database."""
        return sqlite3.connect(f"file:{self.ms1_db_path}?mode=ro", uri=True)

    @override
    def __repr__(self) -> str:
        return (
            f"MzmlFile(\n"
            f"  source   = '{self.source_path.name}',\n"
            f"  ms1      = {self.n_ms1} scans  →  {self.ms1_db_path.name}\n"
            f"  ms2      = {self.n_ms2} scans\n"
            f"  rt       = [{self.rt_range[0]:.2f}, {self.rt_range[1]:.2f}] s\n"
            f"  ms1 m/z  = [{self.ms1_mz_range[0]:.4f}, {self.ms1_mz_range[1]:.4f}]\n"
            f"  ms2 prec = [{self.ms2_precursor_mz_range[0]:.4f}, {self.ms2_precursor_mz_range[1]:.4f}]\n"
            f")"
        )


# ---------------------------------------------------------------------------
# Blob helpers (public — reused by downstream workers)
# ---------------------------------------------------------------------------


def array_to_blob(
    arr: np.ndarray, decimal_places: int = 4, compressed: bool = True
) -> bytes:
    """Compress a numpy array to a zlib-compressed float32 blob."""
    arr = np.round(arr, decimals=decimal_places)
    if compressed:
        return zlib.compress(arr.astype(np.float32).tobytes(), level=1)
    return arr.astype(np.float32).tobytes()


def blob_to_array(
    blob: bytes,
    decimal_places: int = 4,
    compressed: bool = True,
) -> np.ndarray:
    """Decompress a zlib blob back to a float32 numpy array."""
    if compressed:
        blob = zlib.decompress(blob)
    arr = np.frombuffer(blob, dtype=np.float32)
    return np.round(arr, decimals=decimal_places)


# ---------------------------------------------------------------------------
# Regex patterns — compiled once at module level
# ---------------------------------------------------------------------------

# --- Header / instrument ---
_RE_INSTRUMENT_MODEL = re.compile(
    r'<cvParam[^>]*accession="MS:1000031"[^>]*value="([^"]*)"'  # instrument model (generic)
    r'|<cvParam[^>]*accession="MS:1000483"[^>]*value="([^"]*)"'  # Thermo Fisher model
    r'|<cvParam[^>]*name="([^"]*instrument model[^"]*)"',
    re.IGNORECASE,
)
_RE_SERIAL_NUMBER = re.compile(
    r'<cvParam[^>]*accession="MS:1000529"[^>]*value="([^"]*)"'
)
_RE_IONIZATION = re.compile(
    r'<cvParam[^>]*accession="MS:1000073"[^>]*name="([^"]*)"'  # ESI
    r'|<cvParam[^>]*accession="MS:1000075"[^>]*name="([^"]*)"'  # MALDI
    r'|<cvParam[^>]*accession="MS:1000398"[^>]*name="([^"]*)"',  # DESI
)
_RE_ANALYZER = re.compile(
    r'<cvParam[^>]*accession="MS:1000484"[^>]*name="([^"]*)"'  # orbitrap
    r'|<cvParam[^>]*accession="MS:1000079"[^>]*name="([^"]*)"'  # FT-ICR
    r'|<cvParam[^>]*accession="MS:1000264"[^>]*name="([^"]*)"'  # ion trap
    r'|<cvParam[^>]*accession="MS:1000081"[^>]*name="([^"]*)"'  # quadrupole
    r'|<cvParam[^>]*accession="MS:1000084"[^>]*name="([^"]*)"',  # TOF
)
_RE_START_TIMESTAMP = re.compile(r'startTimeStamp="([^"]+)"')
_RE_SOURCE_FILE_NAME = re.compile(r'<sourceFile[^>]*name="([^"]*)"')
_RE_INSTRUMENT_CONFIG_END = re.compile(r"</instrumentConfigurationList>")

# --- Per-spectrum ---
_RE_RT = re.compile(
    r'<cvParam[^>]*accession="MS:1000016"[^>]*value="([\d\.eE+\-]+)"'
    r'[^>]*unitAccession="UO:(\d+)"'
    r'|<cvParam[^>]*value="([\d\.eE+\-]+)"[^>]*accession="MS:1000016"'
    r'[^>]*unitAccession="UO:(\d+)"'
)
_RE_MS_LEVEL = re.compile(r'accession="MS:1000511"[^>]*value="(\d+)"')
_RE_SCAN_ID = re.compile(r'\bid="([^"]+)"')
_RE_SCAN_NUM = re.compile(r"scan=(\d+)")

_RE_TIC = re.compile(r'<cvParam[^>]*accession="MS:1000285"[^>]*value="([\d\.eE+\-]+)"')

_RE_SCAN_POLARITY = re.compile(
    r'<cvParam[^>]*accession="MS:10001(?:29|30)"[^>]*\bname="(\w+)\s+scan"',
    re.IGNORECASE,
)

# Precursor
_RE_PRECURSOR_SPECREF = re.compile(r'<precursor\s[^>]*spectrumRef="([^"]*)"')
_RE_PRECURSOR_MZ = re.compile(
    r'<selectedIon>.*?accession="MS:1000744"[^>]*value="([\d\.eE+\-]+)"',
    re.DOTALL,
)
_RE_PRECURSOR_CHARGE = re.compile(
    r'<selectedIon>.*?accession="MS:1000041"[^>]*value="(\d+)"',
    re.DOTALL,
)
_RE_PRECURSOR_INTENSITY = re.compile(
    # MS:1000042 = peak intensity of selected ion (different from TIC)
    r'<selectedIon>.*?accession="MS:1000042"[^>]*value="([\d\.eE+\-]+)"',
    re.DOTALL,
)

_RE_FILTER_STRING = re.compile(
    r'<cvParam[^>]*accession="MS:1000512"[^>]*value="([^"]*)"'
)

_RE_ISOLATION_TARGET = re.compile(
    # MS:1000827 = isolation window target m/z — the DDA method target,
    # used for grouping repeated acquisitions of the same precursor.
    r'<isolationWindow>.*?accession="MS:1000827"[^>]*value="([\d\.eE+\-]+)"',
    re.DOTALL,
)
_RE_ISOLATION_LOWER = re.compile(
    # MS:1000828 = isolation window lower offset (Da)
    r'<isolationWindow>.*?accession="MS:1000828"[^>]*value="([\d\.eE+\-]+)"',
    re.DOTALL,
)
_RE_ISOLATION_UPPER = re.compile(
    # MS:1000829 = isolation window upper offset (Da)
    r'<isolationWindow>.*?accession="MS:1000829"[^>]*value="([\d\.eE+\-]+)"',
    re.DOTALL,
)
_RE_COLLISION_ENERGY = re.compile(
    # MS:1000045 = collision energy (eV)
    r'accession="MS:1000045"[^>]*value="([\d\.eE+\-]+)"',
)

# Binary arrays
_RE_BDA_BLOCK = re.compile(r"<binaryDataArray[^>]*>(.*?)</binaryDataArray>", re.DOTALL)
_RE_BINARY_DATA = re.compile(r"<binary>(.*?)</binary>", re.DOTALL)
_RE_64BIT = re.compile(r'accession="MS:1000523"')  # 64-bit float
_RE_ZLIB = re.compile(r'accession="MS:1000574"')  # zlib compression
_RE_MZ_ARRAY = re.compile(r'accession="MS:1000514"')  # m/z array
_RE_INT_ARRAY = re.compile(r'accession="MS:1000515"')  # intensity array


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------


class MzmlParser:
    """Streams an mzML file and writes two SQLite databases (MS1, MS2).

    Each database receives:
      - A ``metadata`` table with instrument info and file provenance.
      - A ``commands`` table recording processing steps (JSON arguments).
      - Scan data tables (``ms1_scans`` / ``ms2_scans``).

    Args:
        include_ms2: Store MS2 scans. Defaults to True.
        progress_callback: ``fn(n_ms1: int, n_ms2: int)`` — called after
            each stored spectrum. Defaults to None.
        decimal_places: Rounding precision for m/z and intensity values in
            blobs. Defaults to 4.
    """

    def __init__(
        self,
        include_ms2: bool = True,
        progress_callback: Callable | None = None,
        decimal_places: int = 4,
    ):
        self.include_ms2: bool = include_ms2
        self.progress_callback: Callable | None = progress_callback
        self.decimal_places: int = decimal_places

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    @log_call(source="mzml_path")
    def parse(self, mzml_path: Path | str, ms1_db_path: Path | str) -> MzmlFile:
        """Stream-parse ``mzml_path`` and populate the raw SQLite database.

        The database holds only ground-truth facts about the acquisition:
        MS1/MS2 scans plus instrument metadata and a parse ``commands``
        entry. Parameter-dependent derived data (averaged spectra,
        centroids, groupings, annotations) is never written here — it
        belongs to a per-analysis database.

        Args:
            mzml_path: Path to the mzML file to parse.
            ms1_db_path: Destination path for the raw SQLite database.

        Returns:
            An :class:`MzmlFile` describing the completed parse.
        """
        mzml_path = Path(mzml_path)
        ms1_db_path: Path = Path(ms1_db_path)

        logger.debug("%s: Starting parsing file.", mzml_path)

        instrument_info: dict[str, Any] = _parse_instrument_info(mzml_path)

        ms1_con = self._init_ms1_db(ms1_db_path)

        # Write instrument metadata + the parse command entry.
        parse_dt = datetime.now().astimezone().isoformat()
        _insert_metadata(ms1_con, mzml_path, instrument_info)
        _insert_command(
            ms1_con,
            command_name="parse",
            dt=parse_dt,
            arguments={
                "source_file": str(mzml_path),
                "decimal_places": self.decimal_places,
                "include_ms2": self.include_ms2,
                "msianalyzer_version": SOFTWARE_VERSION,
            },
            run_id="parse",
        )
        ms1_con.commit()

        logger.debug("%s: Written metadata", mzml_path)

        # --- Pass 2: spectrum streaming ---
        n_ms1 = n_ms2 = 0
        all_rts: list[float] = []
        ms1_mz_mins: list[float] = []
        ms1_mz_maxs: list[float] = []
        precursor_mzs: list[float] = []

        try:
            for sp in self._iter_spectra(mzml_path):
                level = sp["ms_level"]

                if level == 1:
                    self._insert_ms1(ms1_con, sp)
                    n_ms1 += 1
                    all_rts.append(sp["rt"])
                    if sp["mz_min"] is not None:
                        ms1_mz_mins.append(round(sp["mz_min"], self.decimal_places))
                        ms1_mz_maxs.append(round(sp["mz_max"], self.decimal_places))

                elif level == 2 and self.include_ms2:
                    self._insert_ms2(ms1_con, sp)
                    n_ms2 += 1
                    all_rts.append(sp["rt"])
                    if sp.get("precursor_mz") is not None:
                        precursor_mzs.append(
                            round(sp["precursor_mz"], self.decimal_places)
                        )

                if self.progress_callback:
                    self.progress_callback(n_ms1, n_ms2)

            ms1_con.commit()

        finally:
            ms1_con.close()

        logger.debug("%s: Finished writing spectra.", mzml_path)

        return MzmlFile(
            source_path=mzml_path,
            ms1_db_path=ms1_db_path,
            n_ms1=n_ms1,
            n_ms2=n_ms2,
            rt_range=(
                min(all_rts, default=0.0),
                max(all_rts, default=0.0),
            ),
            ms1_mz_range=(
                min(ms1_mz_mins, default=0.0),
                max(ms1_mz_maxs, default=0.0),
            ),
            ms2_precursor_mz_range=(
                min(precursor_mzs, default=0.0),
                max(precursor_mzs, default=0.0),
            ),
            instrument_info=instrument_info,
        )

    # ------------------------------------------------------------------
    # Spectrum iterator
    # ------------------------------------------------------------------

    def _iter_spectra(self, path: Path) -> Iterator[dict[str, str | float | int]]:
        """Yield one parsed dict per ``<spectrum>…</spectrum>`` block."""
        inside = False
        buf: list[str] = []

        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                if "<spectrum " in line:
                    inside = True
                    buf = [line]
                elif "</spectrum>" in line:
                    buf.append(line)
                    sp = self._parse_spectrum_block("".join(buf))
                    if sp is not None:
                        yield sp
                    inside = False
                    buf = []
                elif inside:
                    buf.append(line)

    # ------------------------------------------------------------------
    # Block parser
    # ------------------------------------------------------------------

    def _parse_spectrum_block(self, text: str) -> Optional[dict]:
        rt = _parse_rt(text)
        if rt is None:
            return None

        mz_arr, int_arr = _parse_binary_arrays(text)

        sp: dict[str, str | float | int | None | bytes] = {
            "scan_id": _parse_scan_id(text),
            "rt": rt,
            "ms_level": _parse_ms_level(text),
            "n_peaks": len(mz_arr) if mz_arr is not None else None,
            "mz_min": float(mz_arr.min())
            if mz_arr is not None and len(mz_arr)
            else None,
            "mz_max": float(mz_arr.max())
            if mz_arr is not None and len(mz_arr)
            else None,
            "mz_blob": array_to_blob(mz_arr, self.decimal_places)
            if mz_arr is not None
            else b"",
            "intensity_blob": array_to_blob(int_arr, self.decimal_places)
            if int_arr is not None
            else b"",
            "tic": _parse_tic(text),
            "polarity": _parse_scan_polarity(text),
        }

        if sp["ms_level"] == 2:
            sp["precursor_mz"] = _parse_precursor_mz(text, self.decimal_places)
            sp["precursor_charge"] = _parse_precursor_charge(text)
            sp["precursor_intensity"] = _parse_precursor_intensity(text)
            sp["parent_scan_id"] = _parse_parent_scan_id(text)
            sp["isolation_window_target"] = _parse_isolation_target(
                text, self.decimal_places
            )
            sp["isolation_window_lower"] = _parse_isolation_lower(text)
            sp["isolation_window_upper"] = _parse_isolation_upper(text)
            sp["collision_energy"] = _parse_collision_energy(text)
            sp["filter_string"] = _parse_filter_string(text)

        return sp

    # ------------------------------------------------------------------
    # SQLite — schema
    # ------------------------------------------------------------------

    def _init_ms1_db(self, ms1_db_path: Path | str) -> sqlite3.Connection:
        """Open ``ms1_db_path`` and ensure the raw schema exists.

        Thin wrapper around :func:`init_raw_db`, kept as a method for
        backwards compatibility with existing callers and tests.
        """
        return init_raw_db(ms1_db_path)

    # ------------------------------------------------------------------
    # SQLite — inserts
    # ------------------------------------------------------------------

    @staticmethod
    def _insert_ms1(con: sqlite3.Connection, sp: dict) -> None:
        safe_execute(
            con,
            """
            INSERT OR REPLACE INTO ms1_scans
              (scan_id, rt, n_peaks, mz_min, mz_max, tic, polarity,
               mz_array, intensity_array)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                sp["scan_id"],
                sp["rt"],
                sp["n_peaks"],
                sp["mz_min"],
                sp["mz_max"],
                sp["tic"],
                sp["polarity"],
                sp["mz_blob"],
                sp["intensity_blob"],
            ),
            table="ms1_scans",
            logger=logger,
        )

    @staticmethod
    def _insert_ms2(con: sqlite3.Connection, sp: dict) -> None:
        safe_execute(
            con,
            """
            INSERT OR REPLACE INTO ms2_scans
              (scan_id, parent_scan_id, polarity, rt, filter_string,
               precursor_mz, precursor_charge, precursor_intensity,
               isolation_window_target, isolation_window_lower, isolation_window_upper,
               collision_energy,
               n_peaks, tic,
               mz_array, intensity_array)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                sp["scan_id"],
                sp.get("parent_scan_id"),
                sp.get("polarity"),
                sp["rt"],
                sp.get("filter_string"),
                sp.get("precursor_mz"),
                sp.get("precursor_charge"),
                sp.get("precursor_intensity"),
                sp.get("isolation_window_target"),
                sp.get("isolation_window_lower"),
                sp.get("isolation_window_upper"),
                sp.get("collision_energy"),
                sp["n_peaks"],
                sp["tic"],
                sp["mz_blob"],
                sp["intensity_blob"],
            ),
            table="ms2_scans",
            logger=logger,
        )


# ---------------------------------------------------------------------------
# Raw-database schema (single source of truth — reused by tests)
# ---------------------------------------------------------------------------


def create_raw_schema(con: sqlite3.Connection) -> None:
    """Create every table and index of the raw database on ``con``.

    The raw database describes only ground-truth facts about the
    acquisition: MS1/MS2 scans, instrument ``metadata`` and a ``commands``
    log of the parse (and, later, pixel mapping). It carries no
    parameter-dependent derived data.

    Idempotent — every statement uses ``IF NOT EXISTS``.
    """
    con.execute("""
        CREATE TABLE IF NOT EXISTS metadata (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS commands (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id       UUID    NOT NULL,
            command_name TEXT    NOT NULL,
            datetime     TEXT    NOT NULL,
            arguments    TEXT    NOT NULL
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_command_run_id ON commands(run_id)")
    con.execute("""
        CREATE TABLE IF NOT EXISTS ms1_scans (
            scan_id         INTEGER PRIMARY KEY,
            rt              REAL    NOT NULL,
            n_peaks         INTEGER,
            mz_min          REAL,
            mz_max          REAL,
            tic             REAL,
            polarity        TEXT,
            mz_array        BLOB    NOT NULL,
            intensity_array BLOB    NOT NULL
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_ms1_rt      ON ms1_scans(rt)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_ms1_mz_min  ON ms1_scans(mz_min)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_ms1_mz_max  ON ms1_scans(mz_max)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_ms1_tic     ON ms1_scans(tic)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_ms1_pol     ON ms1_scans(polarity)")
    con.execute("""
        CREATE TABLE IF NOT EXISTS ms2_scans (
            scan_id                  INTEGER PRIMARY KEY,
            parent_scan_id           INTEGER,
            polarity                 TEXT,
            rt                       REAL    NOT NULL,
            filter_string            TEXT    NOT NULL,
            precursor_mz             REAL,
            precursor_charge         INTEGER,
            precursor_intensity      REAL,
            isolation_window_target  REAL,
            isolation_window_lower   REAL,
            isolation_window_upper   REAL,
            collision_energy         REAL,
            n_peaks                  INTEGER,
            tic                      REAL,
            mz_array                 BLOB    NOT NULL,
            intensity_array          BLOB    NOT NULL,
            CONSTRAINT fk_ms1_scan
            FOREIGN KEY (parent_scan_id)
            REFERENCES ms1_scans(scan_id)
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_ms2_rt             ON ms2_scans(rt)")
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_ms2_precursor_mz   ON ms2_scans(precursor_mz)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_ms2_isolation_tgt  ON ms2_scans(isolation_window_target)"
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_ms2_tic            ON ms2_scans(tic)")
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_ms2_parent         ON ms2_scans(parent_scan_id)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_filter_string      ON ms2_scans(filter_string)"
    )


def init_raw_db(db_path: Path | str) -> sqlite3.Connection:
    """Open (creating if needed) the raw database and apply its schema.

    Args:
        db_path: Destination path for the raw SQLite database.

    Returns:
        An open connection with WAL journalling and foreign keys enabled.
    """
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA foreign_keys = ON")
    create_raw_schema(con)
    con.commit()
    return con


# ---------------------------------------------------------------------------
# Header pre-pass — instrument metadata
# ---------------------------------------------------------------------------


def _parse_instrument_info(path: Path) -> dict:
    """
    Fast pre-pass: read the mzML header up to and including
    </instrumentConfigurationList>, extract instrument metadata.

    Stops reading as soon as the instrument block ends — does not load
    the full file into memory.
    """
    info: dict = {
        "instrument_model": None,
        "serial_number": None,
        "ionization": None,
        "analyzer": None,
        "acquisition_start": None,
        "source_file": None,
    }

    header_buf: list[str] = []
    found_end = False

    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            header_buf.append(line)
            if _RE_INSTRUMENT_CONFIG_END.search(line):
                found_end = True
            # startTimeStamp is on <run>, which follows instrumentConfigurationList
            # so we continue a little further until <spectrumList starts
            if found_end and "<spectrumList" in line:
                break
            # Safety: stop after 500 lines regardless
            if len(header_buf) > 500:
                break

    header = "".join(header_buf)

    m = _RE_INSTRUMENT_MODEL.search(header)
    if m:
        info["instrument_model"] = next((g for g in m.groups() if g is not None), None)

    m = _RE_SERIAL_NUMBER.search(header)
    if m:
        info["serial_number"] = m.group(1)

    m = _RE_IONIZATION.search(header)
    if m:
        info["ionization"] = next((g for g in m.groups() if g is not None), None)

    m = _RE_ANALYZER.search(header)
    if m:
        info["analyzer"] = next((g for g in m.groups() if g is not None), None)

    m = _RE_START_TIMESTAMP.search(header)
    if m:
        info["acquisition_start"] = m.group(1)

    m = _RE_SOURCE_FILE_NAME.search(header)
    if m:
        info["source_file"] = m.group(1)

    return info


# ---------------------------------------------------------------------------
# Module-level parsing helpers
# ---------------------------------------------------------------------------


def _parse_rt(text: str) -> Optional[float]:
    m = _RE_RT.search(text)
    if not m:
        return None
    val = float(m.group(1) or m.group(3))
    unit = m.group(2) or m.group(4)
    if unit == "0000031":  # minutes → seconds
        val *= 60.0
    return val


def _parse_ms_level(text: str) -> int:
    m = _RE_MS_LEVEL.search(text)
    return int(m.group(1)) if m else 1


def _parse_scan_id(text: str) -> int | str:
    m = _RE_SCAN_ID.search(text)
    if not m:
        return -1
    id_str = m.group(1)
    m2 = _RE_SCAN_NUM.search(id_str)
    return int(m2.group(1)) if m2 else id_str


def _parse_filter_string(text: str) -> int:
    m = _RE_FILTER_STRING.search(text)
    return m.group(1) if m else 1


def _parse_precursor_mz(text: str, decimal_places: int = 4) -> Optional[float]:
    m = _RE_PRECURSOR_MZ.search(text)
    return round(float(m.group(1)), decimal_places) if m else None


def _parse_precursor_charge(text: str) -> Optional[int]:
    m = _RE_PRECURSOR_CHARGE.search(text)
    return int(m.group(1)) if m else None


def _parse_precursor_intensity(text: str) -> Optional[float]:
    """
    MS:1000042 = peak intensity of the selected ion in the MS1 survey scan.
    This is NOT the TIC of the MS2 scan — it is the intensity of the
    precursor peak as measured before fragmentation. Useful as a proxy
    for precursor abundance and quantification confidence.
    """
    m = _RE_PRECURSOR_INTENSITY.search(text)
    return float(m.group(1)) if m else None


def _parse_parent_scan_id(text: str) -> Optional[int]:
    """
    Extract the parent MS1 scan ID from the ``spectrumRef`` attribute
    of the ``<precursor>`` element.

    Example spectrumRef value:
        "controllerType=0 controllerNumber=1 scan=42"
    Returns 42.
    """
    m = _RE_PRECURSOR_SPECREF.search(text)
    if not m:
        return None
    ref = m.group(1)
    m2 = _RE_SCAN_NUM.search(ref)
    return int(m2.group(1)) if m2 else None


def _parse_tic(text: str) -> Optional[float]:
    m = _RE_TIC.search(text)
    return float(m.group(1)) if m else None


def _parse_scan_polarity(text: str) -> Optional[str]:
    m = _RE_SCAN_POLARITY.search(text)
    return m.group(1).upper() if m else None


def _parse_isolation_target(text: str, decimal_places: int = 4) -> Optional[float]:
    """
    MS:1000827 — isolation window target m/z.
    This is what the DDA method targeted for fragmentation — used for
    grouping repeated acquisitions of the same precursor. Distinct from
    precursor_mz (MS:1000744) which is the selected ion actually found.
    """
    m = _RE_ISOLATION_TARGET.search(text)
    return round(float(m.group(1)), decimal_places) if m else None


def _parse_isolation_lower(text: str) -> Optional[float]:
    """MS:1000828 — isolation window lower offset in Da."""
    m = _RE_ISOLATION_LOWER.search(text)
    return float(m.group(1)) if m else None


def _parse_isolation_upper(text: str) -> Optional[float]:
    """MS:1000829 — isolation window upper offset in Da."""
    m = _RE_ISOLATION_UPPER.search(text)
    return float(m.group(1)) if m else None


def _parse_collision_energy(text: str) -> Optional[float]:
    """MS:1000045 — collision energy in eV."""
    m = _RE_COLLISION_ENERGY.search(text)
    return float(m.group(1)) if m else None


def _parse_binary_arrays(
    text: str,
) -> tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    """
    Extract m/z and intensity arrays from ``<binaryDataArray>`` blocks.

    Precision: MS:1000523 = 64-bit, MS:1000521 = 32-bit (default).
    Compression: MS:1000574 = zlib (default fallback = none).
    """
    mz_arr = int_arr = None

    for block in _RE_BDA_BLOCK.findall(text):
        bm = _RE_BINARY_DATA.search(block)
        if not bm:
            continue
        encoded = bm.group(1).strip()
        if not encoded:
            continue

        dtype = np.float64 if _RE_64BIT.search(block) else np.float32
        compression = "zlib" if _RE_ZLIB.search(block) else "none"

        try:
            raw = b64decode(encoded)
            if compression == "zlib":
                raw = zlib.decompress(raw)
            arr = np.frombuffer(raw, dtype=dtype)
        except Exception:
            continue

        if _RE_MZ_ARRAY.search(block):
            mz_arr = arr
        elif _RE_INT_ARRAY.search(block):
            int_arr = arr

    return mz_arr, int_arr


# ---------------------------------------------------------------------------
# Metadata / commands persistence helpers
# ---------------------------------------------------------------------------


def _insert_metadata(
    con: sqlite3.Connection, mzml_path: Path, instrument_info: dict
) -> None:
    """Write all metadata key/value pairs into the metadata table."""
    rows = [
        ("source_file", str(mzml_path.name)),
        ("source_path", str(mzml_path.resolve())),
        ("instrument_model", instrument_info.get("instrument_model") or ""),
        ("serial_number", instrument_info.get("serial_number") or ""),
        ("ionization", instrument_info.get("ionization") or ""),
        ("analyzer", instrument_info.get("analyzer") or ""),
        ("acquisition_start", instrument_info.get("acquisition_start") or ""),
        ("original_source", instrument_info.get("source_file") or ""),
        ("db_creation_date", datetime.now().astimezone().isoformat()),
        ("msianalyzer_version", SOFTWARE_VERSION),
    ]
    safe_executemany(
        con,
        "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
        rows,
        table="metadata",
        logger=logger,
    )


def _insert_command(
    con: sqlite3.Connection,
    command_name: str,
    dt: str,
    arguments: dict,
    run_id: str | None,
) -> None:
    """Append one row to the commands table."""
    safe_execute(
        con,
        "INSERT INTO commands (command_name, datetime, arguments, run_id) VALUES (?,?,?,?)",
        (command_name, dt, json.dumps(arguments), run_id),
        table="commands",
        logger=logger,
    )


# ---------------------------------------------------------------------------
# Public helper: add a command entry to an existing DB
# (used by downstream modules: grouper, denoiser, spatial associator)
# ---------------------------------------------------------------------------


@log_call(source="db_path")
def log_command(
    db_path: Path | str, command_name: str, arguments: dict, run_id: str
) -> int | None:
    """Append a command record to an existing DB's ``commands`` table.

    Intended for use by any downstream module that modifies a DB
    (grouper, denoiser, pixel associator, quantifier).

    Args:
        db_path: Path to any msianalyzer SQLite database (ms1, ms2,
            groups…).
        command_name: Short identifier, e.g. ``"denoise_ms2"``,
            ``"assign_pixels"``.
        arguments: Any JSON-serialisable key/value pairs describing the
            command.
        run_id: Identifier of the run the command belongs to.
    """
    con = sqlite3.connect(Path(db_path))
    try:
        cur = safe_execute(
            con,
            "INSERT INTO commands (command_name, datetime, arguments, run_id) VALUES (?,?,?,?)",
            (
                command_name,
                datetime.now().astimezone().isoformat(),
                json.dumps(arguments),
                run_id,
            ),
            table="commands",
            logger=logger,
        )
        con.commit()
        command_id = cur.lastrowid
        return command_id
    finally:
        con.close()
