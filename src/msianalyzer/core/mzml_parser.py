"""
mzml_parser.py
Core mzML parsing module. No GUI dependencies, no pixel/spatial logic.

Classes
-------
MzmlFile
    Lightweight result object describing a completed parse.
MzmlParser
    Streams an mzML file and writes MS1 / MS2 SQLite databases.
"""

from __future__ import annotations

import re
import sqlite3
import zlib
from base64 import b64decode
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

import numpy as np


# ---------------------------------------------------------------------------
# Public result object
# ---------------------------------------------------------------------------

@dataclass
class MzmlFile:
    """
    Describes the result of a completed mzML parse.

    Produced by ``MzmlParser.parse()``.  Holds only lightweight metadata;
    the actual spectra live in the two SQLite databases.

    Attributes
    ----------
    source_path : Path
        Path to the original mzML file.
    ms1_db_path : Path
        Path to the SQLite database containing MS1 scans.
    ms2_db_path : Path
        Path to the SQLite database containing MS2 scans.
    n_ms1 : int
        Number of MS1 scans stored.
    n_ms2 : int
        Number of MS2 scans stored.
    rt_range : tuple[float, float]
        (min_rt, max_rt) in seconds across all stored scans.
    ms1_mz_range : tuple[float, float]
        Global m/z range across all MS1 scans (from per-scan min/max
        stored at parse time — no blob decoding needed).
    ms2_precursor_mz_range : tuple[float, float]
        Range of MS2 precursor m/z values stored.
    instrument_info : dict
        Key/value pairs extracted at parse time (best-effort).
    """

    source_path: Path
    ms1_db_path: Path
    ms2_db_path: Path
    n_ms1: int = 0
    n_ms2: int = 0
    rt_range: tuple[float, float] = (0.0, 0.0)
    ms1_mz_range: tuple[float, float] = (0.0, 0.0)
    ms2_precursor_mz_range: tuple[float, float] = (0.0, 0.0)
    instrument_info: dict = field(default_factory=dict)

    def ms1_connection(self) -> sqlite3.Connection:
        """Return a new read-only SQLite connection to the MS1 database."""
        return sqlite3.connect(f"file:{self.ms1_db_path}?mode=ro", uri=True)

    def ms2_connection(self) -> sqlite3.Connection:
        """Return a new read-only SQLite connection to the MS2 database."""
        return sqlite3.connect(f"file:{self.ms2_db_path}?mode=ro", uri=True)

    def __repr__(self) -> str:
        return (
            f"MzmlFile(\n"
            f"  source   = '{self.source_path.name}',\n"
            f"  ms1      = {self.n_ms1} scans  →  {self.ms1_db_path.name}\n"
            f"  ms2      = {self.n_ms2} scans  →  {self.ms2_db_path.name}\n"
            f"  rt       = [{self.rt_range[0]:.2f}, {self.rt_range[1]:.2f}] s\n"
            f"  ms1 m/z  = [{self.ms1_mz_range[0]:.4f}, {self.ms1_mz_range[1]:.4f}]\n"
            f"  ms2 prec = [{self.ms2_precursor_mz_range[0]:.4f}, {self.ms2_precursor_mz_range[1]:.4f}]\n"
            f")"
        )


# ---------------------------------------------------------------------------
# Blob helpers (public — reused by downstream workers)
# ---------------------------------------------------------------------------

def array_to_blob(arr: np.ndarray, decimal_places: int = 4, compressed: bool = True) -> bytes:
    """Compress a numpy array to a zlib-compressed float32 blob."""
    arr = np.round(arr, decimals=decimal_places)
    if compressed:
        return zlib.compress(arr.astype(np.float32).tobytes(), level=1)
    return arr.astype(np.float32).tobytes()


def blob_to_array(blob: bytes, decimal_places: int = 4, compressed: bool = True) -> np.ndarray:
    """Decompress a zlib blob back to a float32 numpy array."""
    if compressed:
        blob = zlib.decompress(blob)
    arr = np.frombuffer(blob, dtype=np.float32)
    return np.round(arr, decimals=decimal_places)


# ---------------------------------------------------------------------------
# Regex patterns — compiled once at module level
# ---------------------------------------------------------------------------


# RT pattern that matches the actual mzML attribute order
_RE_RT = re.compile(
    r'<cvParam[^>]*accession="MS:1000016"[^>]*value="([\d\.eE+\-]+)"[^>]*unitAccession="UO:(\d+)"'
    r'|<cvParam[^>]*value="([\d\.eE+\-]+)"[^>]*accession="MS:1000016"[^>]*unitAccession="UO:(\d+)"'
)

_RE_TIC = re.compile(r'<cvParam[^>]*accession="MS:1000285"[^>]*value="([\d\.eE+\-]+)"[^>]* />')
_RE_SCAN_POLARITY = re.compile(
    r'<cvParam\s+cvRef="MS"\s+accession="MS:1000130"[^>]*\bname="(\w+) scan"'
)

_RE_MS_LEVEL = re.compile(r'accession="MS:1000511"[^>]*value="(\d+)"')
_RE_SCAN_ID  = re.compile(r'\bid="([^"]+)"')
_RE_SCAN_NUM = re.compile(r'scan=(\d+)')

# Precursor (inside <selectedIon> block)
_RE_PRECURSOR_MZ = re.compile(
    r'<selectedIon>.*?accession="MS:1000744"[^>]*value="([\d\.eE+\-]+)"',
    re.DOTALL,
)
_RE_PRECURSOR_CHARGE = re.compile(
    r'<selectedIon>.*?accession="MS:1000041"[^>]*value="(\d+)"',
    re.DOTALL,
)

# Binary data array blocks
_RE_BDA_BLOCK   = re.compile(r'<binaryDataArray[^>]*>(.*?)</binaryDataArray>', re.DOTALL)
_RE_BINARY_DATA = re.compile(r'<binary>(.*?)</binary>', re.DOTALL)

# Precision: MS:1000523 = 64-bit float, MS:1000521 = 32-bit float
_RE_64BIT = re.compile(r'accession="MS:1000523"')
_RE_32BIT = re.compile(r'accession="MS:1000521"')

# Compression: MS:1000574 = zlib, MS:1000576 = no compression
_RE_ZLIB = re.compile(r'accession="MS:1000574"')

# Array type: MS:1000514 = m/z, MS:1000515 = intensity
_RE_MZ_ARRAY  = re.compile(r'accession="MS:1000514"')
_RE_INT_ARRAY = re.compile(r'accession="MS:1000515"')


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------

class MzmlParser:
    """
    Streams an mzML file and writes two SQLite databases (MS1, MS2).

    No pixel / spatial logic here — that belongs in a separate module.

    Parameters
    ----------
    ms1_db_path : Path | str
        Destination path for the MS1 SQLite database.
    ms2_db_path : Path | str
        Destination path for the MS2 SQLite database.
    include_ms2 : bool
        Store MS2 scans (default True).
    progress_callback : callable | None
        Optional ``fn(n_ms1: int, n_ms2: int)`` called after each stored
        spectrum.  No Qt types — safe to call from any thread.
    """

    def __init__(
        self,
        ms1_db_path: Path | str,
        ms2_db_path: Path | str,
        include_ms2: bool = True,
        progress_callback=None,
        decimal_places: int = 4,
    ):
        self.ms1_db_path = Path(ms1_db_path)
        self.ms2_db_path = Path(ms2_db_path)
        self.include_ms2 = include_ms2
        self.progress_callback = progress_callback
        self.decimal_places = decimal_places

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def parse(self, mzml_path: Path | str) -> MzmlFile:
        """
        Stream-parse *mzml_path* and populate the two SQLite databases.

        Returns
        -------
        MzmlFile
            Lightweight result object with paths and summary metadata.
        """
        mzml_path = Path(mzml_path)

        ms1_con = self._init_ms1_db()
        ms2_con = self._init_ms2_db()

        n_ms1 = n_ms2 = 0
        all_rts: list[float]        = []
        ms1_mz_mins: list[float]    = []
        ms1_mz_maxs: list[float]    = []
        precursor_mzs: list[float]  = []

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
                    self._insert_ms2(ms2_con, sp)
                    n_ms2 += 1
                    all_rts.append(sp["rt"])
                    if sp.get("precursor_mz") is not None:
                        precursor_mzs.append(round(sp["precursor_mz"], self.decimal_places))

                if self.progress_callback:
                    self.progress_callback(n_ms1, n_ms2)

            ms1_con.commit()
            ms2_con.commit()

        finally:
            ms1_con.close()
            ms2_con.close()

        return MzmlFile(
            source_path=mzml_path,
            ms1_db_path=self.ms1_db_path,
            ms2_db_path=self.ms2_db_path,
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
        )

    # ------------------------------------------------------------------
    # Spectrum iterator
    # ------------------------------------------------------------------

    def _iter_spectra(self, path: Path) -> Iterator[dict]:
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

        sp: dict = {
            "scan_id":         _parse_scan_id(text),
            "rt":              rt,
            "ms_level":        _parse_ms_level(text),
            "n_peaks":         len(mz_arr) if mz_arr is not None else None,
            "mz_min":          float(mz_arr.min()) if mz_arr is not None and len(mz_arr) else None,
            "mz_max":          float(mz_arr.max()) if mz_arr is not None and len(mz_arr) else None,
            "mz_blob":         array_to_blob(mz_arr, decimal_places=self.decimal_places)  if mz_arr  is not None else b"",
            "intensity_blob":  array_to_blob(int_arr, decimal_places=self.decimal_places) if int_arr is not None else b"",
            "tic":              _parse_tic(text),
            "polarity":        _parse_scan_polarity(text)
        }

        if sp["ms_level"] == 2:
            sp["precursor_mz"]    = _parse_precursor_mz(text, decimal_places=self.decimal_places)
            sp["precursor_charge"] = _parse_precursor_charge(text)

        return sp

    # ------------------------------------------------------------------
    # SQLite — schema
    # ------------------------------------------------------------------

    def _init_ms1_db(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.ms1_db_path)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
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
        con.execute("CREATE INDEX IF NOT EXISTS idx_ms1_rt     ON ms1_scans(rt)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_ms1_mz_min ON ms1_scans(mz_min)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_ms1_mz_max ON ms1_scans(mz_max)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_ms1_tic ON ms1_scans(tic)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_ms1_polarity ON ms1_scans(polarity)")
        con.commit()
        return con

    def _init_ms2_db(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.ms2_db_path)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        con.execute("""
            CREATE TABLE IF NOT EXISTS ms2_scans (
                scan_id          INTEGER PRIMARY KEY,
                rt               REAL    NOT NULL,
                precursor_mz     REAL,
                precursor_charge INTEGER,
                n_peaks          INTEGER,
                tic              REAL,
                mz_array         BLOB    NOT NULL,
                intensity_array  BLOB    NOT NULL
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_ms2_rt           ON ms2_scans(rt)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_ms2_precursor_mz ON ms2_scans(precursor_mz)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_ms2_tic ON ms2_scans(tic)")
        con.commit()
        return con

    # ------------------------------------------------------------------
    # SQLite — inserts
    # ------------------------------------------------------------------

    @staticmethod
    def _insert_ms1(con: sqlite3.Connection, sp: dict) -> None:
        con.execute(
            """
            INSERT OR REPLACE INTO ms1_scans
              (scan_id, rt, n_peaks, mz_min, mz_max, tic, polarity, mz_array, intensity_array)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                sp["scan_id"], sp["rt"], sp["n_peaks"],
                sp["mz_min"],  sp["mz_max"],
                sp["tic"], sp["polarity"],
                sp["mz_blob"], sp["intensity_blob"],
            ),
        )

    @staticmethod
    def _insert_ms2(con: sqlite3.Connection, sp: dict) -> None:
        con.execute(
            """
            INSERT OR REPLACE INTO ms2_scans
              (scan_id, rt, precursor_mz, precursor_charge,
               n_peaks, tic, mz_array, intensity_array)
            VALUES (?,?,?,?,?,?,?, ?)
            """,
            (
                sp["scan_id"], sp["rt"],
                sp.get("precursor_mz"), sp.get("precursor_charge"),
                sp["n_peaks"], sp["tic"],
                sp["mz_blob"], sp["intensity_blob"],
            ),
        )


# ---------------------------------------------------------------------------
# Module-level parsing helpers (pure functions, easy to unit-test)
# ---------------------------------------------------------------------------

def _parse_rt(text: str) -> Optional[float]:
    m = _RE_RT.search(text)
    if not m:
        return None
    # Two capture groups depending on which branch matched
    val  = float(m.group(1) or m.group(3))
    unit = m.group(2) or m.group(4)
    if unit == "0000031":   # minutes → seconds
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


def _parse_precursor_mz(text: str, decimal_places: int = 4) -> Optional[float]:
    m = _RE_PRECURSOR_MZ.search(text)
    if not m:
        return None
    return round(float(m.group(1)), decimal_places)


def _parse_precursor_charge(text: str) -> Optional[int]:
    m = _RE_PRECURSOR_CHARGE.search(text)
    return int(m.group(1)) if m else None

def _parse_tic(text: str) -> Optional[float]:
    m = _RE_TIC.search(text)
    return float(m.group(1)) if m else None

def _parse_scan_polarity(text: str) -> Optional[str]:
    m = _RE_SCAN_POLARITY.search(text)
    return m.group(1) if m else None


def _parse_binary_arrays(
    text: str,
) -> tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    """
    Extract m/z and intensity arrays from ``<binaryDataArray>`` blocks.

    Precision detection
    -------------------
    MS:1000523 → 64-bit float (stored as float64, downcast to float32 by caller)
    MS:1000521 → 32-bit float
    Default    → 32-bit (conservative fallback)

    Compression detection
    ---------------------
    MS:1000574 → zlib
    MS:1000576 → no compression (default fallback)
    """
    mz_arr = int_arr = None

    for block in _RE_BDA_BLOCK.findall(text):
        bm = _RE_BINARY_DATA.search(block)
        if not bm:
            continue
        encoded = bm.group(1).strip()
        if not encoded:
            continue

        dtype       = np.float64 if _RE_64BIT.search(block) else np.float32
        compression = "zlib"     if _RE_ZLIB.search(block)  else "none"

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
