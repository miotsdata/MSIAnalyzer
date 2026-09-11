from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import plotly.graph_objects as go
import pytest

from msianalyzer.core.analysis_db import init_analysis_db
from msianalyzer.core.parser.mzml_parser import array_to_blob
from msianalyzer.core.plotting.plotter import Plotter


def _insert_annotation(
    db: Path,
    *,
    with_filtered_spectra: bool = True,
    with_precursor: bool = True,
) -> int:
    with sqlite3.connect(db) as con:
        con.execute(
            "INSERT INTO annotation_libraries (id, path, name) "
            "VALUES (1, 'lib.db', 'my_library')"
        )
        if with_precursor:
            con.execute(
                "INSERT INTO ms2_associations "
                "(sample_id, scan_id, match_key, precursor_mz, "
                "n_features_in_window, rt, n_peaks, polarity) "
                "VALUES (1, 42, 'k1', 150.1234, 1, 12.3, 5, 'positive')"
            )

        emp_mz = np.array([100.0, 150.0, 200.0], dtype=np.float32)
        emp_int = np.array([0.5, 1.0, 0.2], dtype=np.float32)
        lib_mz = np.array([100.01, 199.99], dtype=np.float32)
        lib_int = np.array([0.8, 1.0], dtype=np.float32)

        if with_filtered_spectra:
            emp_mz_blob = array_to_blob(emp_mz)
            emp_int_blob = array_to_blob(emp_int)
            lib_mz_blob = array_to_blob(lib_mz)
            lib_int_blob = array_to_blob(lib_int)
        else:
            emp_mz_blob = emp_int_blob = lib_mz_blob = lib_int_blob = None

        cur = con.execute(
            "INSERT INTO ms2_annotations "
            "(sample_id, scan_id, library_id, library_spectrum_id, "
            "compound_name, compound_formula, inchikey, score, "
            "dot_product_score, lib_coverage, emp_coverage, coverage_score, "
            "n_matched_peaks, n_lib_peaks, n_emp_peaks_raw, "
            "n_emp_peaks_filtered, emp_filtered_mz, emp_filtered_intensity, "
            "lib_filtered_mz, lib_filtered_intensity, rank_ms2) "
            "VALUES (1, 42, 1, 7, 'Caffeine', 'C8H10N4O2', "
            "'RYYVLZVUVIJVGH-UHFFFAOYSA-N', 0.87, 0.9, 0.8, 0.75, 0.77, "
            "2, 2, 10, 3, ?, ?, ?, ?, 1)",
            (emp_mz_blob, emp_int_blob, lib_mz_blob, lib_int_blob),
        )
        con.commit()
        return cur.lastrowid


def test_plot_ms2_annotation_builds_mirror_plot(tmp_path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    annotation_id = _insert_annotation(db)

    fig = Plotter().plot_ms2_annotation(db, annotation_id)

    assert isinstance(fig, go.Figure)
    assert len(fig.data) > 0
    assert "Caffeine" in fig.layout.title.text
    assert "score=0.8700" in fig.layout.title.text

    # metadata annotation box mentions scan, precursor, library, inchikey
    ann_text = fig.layout.annotations[0].text
    assert "scan 42" in ann_text
    assert "precursor m/z 150.1234" in ann_text
    assert "my_library" in ann_text
    assert "RYYVLZVUVIJVGH-UHFFFAOYSA-N" in ann_text


def test_plot_ms2_annotation_missing_precursor_omits_it_gracefully(tmp_path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    annotation_id = _insert_annotation(db, with_precursor=False)

    fig = Plotter().plot_ms2_annotation(db, annotation_id)

    ann_text = fig.layout.annotations[0].text
    assert "precursor m/z" not in ann_text
    assert "scan 42" in ann_text


def test_plot_ms2_annotation_raises_without_stored_spectra(tmp_path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    annotation_id = _insert_annotation(db, with_filtered_spectra=False)

    with pytest.raises(ValueError, match="no stored filtered spectra"):
        Plotter().plot_ms2_annotation(db, annotation_id)


def test_plot_ms2_annotation_raises_for_unknown_id(tmp_path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()

    with pytest.raises(ValueError, match="No annotation found"):
        Plotter().plot_ms2_annotation(db, 999)


def test_plot_ms2_annotation_custom_title_overrides_auto_title(tmp_path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    annotation_id = _insert_annotation(db)

    fig = Plotter().plot_ms2_annotation(db, annotation_id, title="My Title")

    assert fig.layout.title.text == "My Title"
