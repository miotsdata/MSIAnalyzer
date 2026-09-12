from __future__ import annotations

import json
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
    with_emp_raw: bool = True,
    with_lib_raw: bool = True,
    with_precursor: bool = True,
    sample_raw_db_path: str | None = None,
    library_path: str = "lib.db",
    library_spectrum_id: int = 7,
    fragment_ppm_command: float | None = None,
    noise_threshold_command: float | None = None,
    stored_lib_raw_mz: np.ndarray | None = None,
    stored_lib_raw_intensity: np.ndarray | None = None,
) -> int:
    """Insert one `ms2_annotations` row for the mirror-plot tests.

    `with_emp_raw`/`with_lib_raw` control whether the untouched
    `emp_raw_*`/`lib_raw_*` blobs are stored at all (the ADR 0018 "was
    `store_raw_spectra` on for this run" gate). The default raw arrays
    below survive `normalize_and_filter_spectrum` unchanged at the class's
    default `noise_threshold` (0.01) — nothing in them is small enough to
    drop — so they double as what "filtered" reconstructs to. Pass
    `stored_lib_raw_mz`/`stored_lib_raw_intensity` to store a *different*
    raw library spectrum than the default (e.g. to prove a stored copy,
    not a live re-read, is what gets used).
    """
    with sqlite3.connect(db) as con:
        if sample_raw_db_path is not None:
            con.execute(
                "INSERT INTO samples (sample_id, name, raw_db_path, polarity) "
                "VALUES (1, 's1', ?, 'positive')",
                (str(sample_raw_db_path),),
            )
        con.execute(
            "INSERT INTO annotation_libraries (id, path, name) "
            "VALUES (1, ?, 'my_library')",
            (str(library_path),),
        )
        if with_precursor:
            con.execute(
                "INSERT INTO ms2_associations "
                "(sample_id, scan_id, match_key, precursor_mz, "
                "rt, n_peaks, polarity) "
                "VALUES (1, 42, 'k1', 150.1234, 12.3, 5, 'positive')"
            )

        args = {}
        if fragment_ppm_command is not None:
            args["fragment_ppm"] = fragment_ppm_command
        if noise_threshold_command is not None:
            args["noise_threshold"] = noise_threshold_command
        command_id = None
        if args:
            command_id = con.execute(
                "INSERT INTO commands (run_id, command_name, datetime, arguments) "
                "VALUES ('run-1', 'annotate_ms2', '2026-01-01', ?)",
                (json.dumps(args),),
            ).lastrowid

        emp_mz = np.array([100.0, 150.0, 200.0], dtype=np.float32)
        emp_int = np.array([0.5, 1.0, 0.2], dtype=np.float32)
        lib_mz = np.array([100.01, 199.99], dtype=np.float32)
        lib_int = np.array([0.8, 1.0], dtype=np.float32)

        emp_mz_blob = array_to_blob(emp_mz) if with_emp_raw else None
        emp_int_blob = array_to_blob(emp_int) if with_emp_raw else None

        if stored_lib_raw_mz is not None:
            lib_mz_blob = array_to_blob(np.asarray(stored_lib_raw_mz, dtype=np.float32))
            lib_int_blob = array_to_blob(np.asarray(stored_lib_raw_intensity, dtype=np.float32))
        elif with_lib_raw:
            lib_mz_blob = array_to_blob(lib_mz)
            lib_int_blob = array_to_blob(lib_int)
        else:
            lib_mz_blob = lib_int_blob = None

        cur = con.execute(
            "INSERT INTO ms2_annotations "
            "(sample_id, scan_id, library_id, library_spectrum_id, "
            "compound_name, compound_formula, inchikey, score, "
            "dot_product_score, lib_coverage, emp_coverage, coverage_score, "
            "n_matched_peaks, n_lib_peaks, n_emp_peaks_raw, "
            "n_emp_peaks_filtered, emp_raw_mz, emp_raw_intensity, "
            "lib_raw_mz, lib_raw_intensity, rank_ms2, command_id) "
            "VALUES (1, 42, 1, ?, 'Caffeine', 'C8H10N4O2', "
            "'RYYVLZVUVIJVGH-UHFFFAOYSA-N', 0.87, 0.9, 0.8, 0.75, 0.77, "
            "2, 2, 10, 3, ?, ?, ?, ?, 1, ?)",
            (library_spectrum_id, emp_mz_blob, emp_int_blob, lib_mz_blob, lib_int_blob,
             command_id),
        )
        con.commit()
        return cur.lastrowid


def test_plot_spectra_default_single_uncategorized_trace():
    mz = np.array([100.0, 200.0, 300.0])
    intensity = np.array([1.0, 2.0, 3.0])

    fig = Plotter.plot_spectra(mz, intensity)

    # One stick trace + its ghost-marker trace, neither with a legend name
    # (the pre-categories behavior) — and only the stick trace opts into
    # the legend.
    assert len(fig.data) == 2
    line_trace, marker_trace = fig.data
    assert line_trace.mode == "lines"
    assert line_trace.showlegend is True
    assert line_trace.name is None
    assert marker_trace.mode == "markers"
    assert marker_trace.showlegend is False


def test_plot_spectra_categorized_splits_into_three_traces_with_one_legend_entry_each():
    mz = np.array([100.0, 200.0, 300.0, 400.0])
    intensity = np.array([1.0, 2.0, 3.0, 4.0])
    categories = np.array(["no_ms2", "annotated", "non_annotated", "annotated"])

    fig = Plotter.plot_spectra(mz, intensity, categories=categories)

    # 3 categories present -> 3 stick traces + 3 marker traces = 6.
    assert len(fig.data) == 6

    line_traces = [t for t in fig.data if t.mode == "lines"]
    marker_traces = [t for t in fig.data if t.mode == "markers"]
    assert len(line_traces) == 3
    assert len(marker_traces) == 3

    # Every line trace has a legend entry; no marker trace does — "I don't
    # want both the stick and the dot as legend, I want just 3 color lines".
    assert all(t.showlegend for t in line_traces)
    assert not any(t.showlegend for t in marker_traces)
    assert {t.name for t in line_traces} == {"No MS2", "Annotated", "Non-annotated"}

    colors = {t.name: t.line.color for t in line_traces}
    assert colors["No MS2"] == "#999999"
    assert colors["Annotated"] == "#1f77b4"
    assert colors["Non-annotated"] == "#000000"

    # The 2 "annotated" peaks (200, 400) land in the same trace.
    annotated_trace = next(t for t in line_traces if t.name == "Annotated")
    assert list(annotated_trace.x) == [200.0, 200.0, None, 400.0, 400.0, None]


def test_plot_spectra_categorized_omits_empty_categories():
    mz = np.array([100.0, 200.0])
    intensity = np.array([1.0, 2.0])
    categories = np.array(["annotated", "annotated"])

    fig = Plotter.plot_spectra(mz, intensity, categories=categories)

    line_traces = [t for t in fig.data if t.mode == "lines"]
    assert len(line_traces) == 1
    assert line_traces[0].name == "Annotated"


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
    annotation_id = _insert_annotation(db, with_emp_raw=False, with_lib_raw=False)

    with pytest.raises(ValueError, match="raw empirical spectrum not found"):
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


def test_get_annotation_spectra_returns_arrays_and_metadata(tmp_path):
    # The shared data both plot_ms2_annotation and the GUI's fast raster
    # mirror plot (Plotter().get_annotation_spectra ->
    # mirror_plot_raster.render_mirror_plot_png) draw from.
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    annotation_id = _insert_annotation(db)

    data = Plotter().get_annotation_spectra(db, annotation_id)

    assert list(data["empirical_mz"]) == [100.0, 150.0, 200.0]
    # library_mz round-trips through float32 storage then a float64 cast in
    # normalize_and_filter_spectrum, so it's only float32-precision exact.
    np.testing.assert_allclose(data["library_mz"], [100.01, 199.99], rtol=1e-6)
    assert data["compound_name"] == "Caffeine"
    assert data["fragment_ppm_tolerance"] == 10.0  # class default, no commands row


def test_get_annotation_spectra_raises_for_invalid_source(tmp_path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    annotation_id = _insert_annotation(db)

    with pytest.raises(ValueError, match="emp_source"):
        Plotter().get_annotation_spectra(db, annotation_id, emp_source="nope")


def _line_traces(fig: go.Figure) -> list:
    return [t for t in fig.data if t.mode == "lines"]


def test_plot_ms2_annotation_matched_black_unmatched_gray_one_legend_entry_each(tmp_path):
    # "Matching fragments... should be black, while the unmatched should be
    # gray" — for both empirical and library, one shared "Matched"/
    # "Unmatched" legend pair rather than a separate color/entry per side.
    # A wide explicit tolerance guarantees the 100.0/100.01 and
    # 200.0/199.99 pairs match (the fixture's peaks are ~50-100 ppm apart,
    # wider than any real default) while 150.0 (empirical only) stays
    # unmatched.
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    annotation_id = _insert_annotation(db)

    fig = Plotter().plot_ms2_annotation(db, annotation_id, fragment_ppm_tolerance=200.0)

    line_traces = _line_traces(fig)
    matched = [t for t in line_traces if t.name == "Matched"]
    unmatched = [t for t in line_traces if t.name == "Unmatched"]
    assert len(matched) == 2  # empirical + library sides both have a match
    # Only empirical has an unmatched peak (150.0, no library counterpart)
    # — library's own 2 peaks both matched, so it contributes no
    # "Unmatched" trace at all.
    assert len(unmatched) == 1
    assert all(t.line.color == "#000000" for t in matched)
    assert all(t.line.color == "#b0b0b0" for t in unmatched)
    # Only the empirical (first) call shows in the legend — "Matched"/
    # "Unmatched" appear once each, not once per side.
    assert sum(t.showlegend for t in matched) == 1
    assert sum(t.showlegend for t in unmatched) == 1


def test_plot_ms2_annotation_fragment_ppm_tolerance_resolved_from_command_arguments(tmp_path):
    # "same mz +- ppm used for matching in core" — the tolerance used for
    # coloring should be the run's own `fragment_ppm`, not a hardcoded
    # guess. 200 ppm (stored on the `annotate_ms2` command) matches the
    # 100.0/100.01 and 200.0/199.99 pairs; the class default of 10 ppm
    # would not.
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    annotation_id = _insert_annotation(db, fragment_ppm_command=200.0)

    fig = Plotter().plot_ms2_annotation(db, annotation_id)

    matched = [t for t in _line_traces(fig) if t.name == "Matched"]
    assert len(matched) == 2


def test_plot_ms2_annotation_fragment_ppm_tolerance_falls_back_without_command(tmp_path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    # No fragment_ppm_command -> no `commands` row/command_id at all.
    annotation_id = _insert_annotation(db)

    fig = Plotter().plot_ms2_annotation(db, annotation_id)

    # Falls back to the 10 ppm default, too narrow for this fixture's
    # ~50-100 ppm gaps -> nothing matches.
    assert [t for t in _line_traces(fig) if t.name == "Matched"] == []


def test_plot_ms2_annotation_raw_empirical_source_reads_from_sample_raw_db(tmp_path):
    from msianalyzer.core.parser.mzml_parser import init_raw_db

    raw_db = tmp_path / "sample.db"
    con = init_raw_db(raw_db)
    raw_mz = np.array([111.0, 222.0, 333.0], dtype=np.float32)
    raw_int = np.array([5.0, 10.0, 2.0], dtype=np.float32)
    con.execute(
        "INSERT INTO ms2_scans (scan_id, rt, filter_string, mz_array, intensity_array) "
        "VALUES (42, 1.0, 'f', ?, ?)",
        (array_to_blob(raw_mz), array_to_blob(raw_int)),
    )
    con.commit()
    con.close()

    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    # No stored emp_raw_* on this row -> forces the live re-read path.
    annotation_id = _insert_annotation(db, sample_raw_db_path=raw_db, with_emp_raw=False)

    fig = Plotter().plot_ms2_annotation(db, annotation_id, emp_source="raw")

    # The raw scan's own m/z values appear (not the default emp_raw_*
    # fixture values, 100.0/150.0/200.0, which weren't stored here).
    all_x = {x for t in fig.data for x in (t.x if t.x is not None else []) if x is not None}
    assert {111.0, 222.0, 333.0} <= all_x
    assert 100.0 not in all_x and 150.0 not in all_x


def test_plot_ms2_annotation_raw_empirical_source_missing_raises(tmp_path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    # No stored emp_raw_*, and no sample_raw_db_path -> `samples` has no
    # row for sample_id=1 either, so the live fallback also fails.
    annotation_id = _insert_annotation(db, with_emp_raw=False)

    with pytest.raises(ValueError, match="raw empirical spectrum not found"):
        Plotter().plot_ms2_annotation(db, annotation_id, emp_source="raw")


def test_plot_ms2_annotation_raw_library_source_reads_from_library_file(tmp_path):
    from libviz.core.library import Library

    lib_path = tmp_path / "library.db"
    lib = Library.create(lib_path, name="test-lib")
    adduct_id = next(a["id"] for a in lib.get_adducts() if a["charge"] > 0)
    raw_lib_mz = np.array([444.0, 555.0], dtype=np.float32)
    raw_lib_int = np.array([1.0, 3.0], dtype=np.float32)
    lib.add_spectra(
        compound_name="Caffeine",
        compound_formula="C8H10N4O2",
        inchikey="RYYVLZVUVIJVGH-UHFFFAOYSA-N",
        spectra_info={
            "precursor_mz": 195.0,
            "polarity": "POSITIVE",
            "collision_energy": 20.0,
            "mz": raw_lib_mz,
            "intensity": raw_lib_int,
        },
        adduct_id=adduct_id,
    )
    with lib.session_scope() as session:
        from libviz.core.db.models import Spectrum

        spectrum_id = session.query(Spectrum).one().id

    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    # No stored lib_raw_* on this row -> forces the live library-file
    # re-read path.
    annotation_id = _insert_annotation(
        db, library_path=lib_path, library_spectrum_id=spectrum_id, with_lib_raw=False
    )

    fig = Plotter().plot_ms2_annotation(db, annotation_id, lib_source="raw")

    all_x = {x for t in fig.data for x in (t.x if t.x is not None else []) if x is not None}
    assert {444.0, 555.0} <= all_x
    assert 100.01 not in all_x and 199.99 not in all_x


def test_plot_ms2_annotation_raw_library_source_missing_raises(tmp_path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    # Default library_path ("lib.db") doesn't exist on disk, and no
    # stored lib_raw_* either -> both resolution paths fail.
    annotation_id = _insert_annotation(db, with_lib_raw=False)

    with pytest.raises(ValueError, match="raw library spectrum not found"):
        Plotter().plot_ms2_annotation(db, annotation_id, lib_source="raw")


def test_plot_ms2_annotation_prefers_stored_raw_library_spectrum(tmp_path):
    # ADR 0017: the untouched library spectrum is persisted at annotation
    # time (annotate.persist_annotations) so this never needs to re-open
    # the library file live. library_path here points at a file that
    # doesn't exist at all — proving the stored copy is what's actually
    # used, not a live re-read (which would raise, per the test above).
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    stored_mz = np.array([444.0, 555.0], dtype=np.float32)
    stored_int = np.array([1.0, 3.0], dtype=np.float32)
    annotation_id = _insert_annotation(
        db,
        library_path=str(tmp_path / "does_not_exist.db"),
        stored_lib_raw_mz=stored_mz,
        stored_lib_raw_intensity=stored_int,
    )

    fig = Plotter().plot_ms2_annotation(db, annotation_id, lib_source="raw")

    all_x = {x for t in fig.data for x in (t.x if t.x is not None else []) if x is not None}
    assert {444.0, 555.0} <= all_x
    assert 100.01 not in all_x and 199.99 not in all_x


def test_get_annotation_spectra_falls_back_to_live_read_without_stored_raw(tmp_path):
    # A row written before the lib_raw_* columns existed (or with
    # store_raw_spectra off) has them NULL — falls back to the live
    # library-file re-read, same as before this feature existed.
    from libviz.core.library import Library

    lib_path = tmp_path / "library.db"
    lib = Library.create(lib_path, name="test-lib")
    adduct_id = next(a["id"] for a in lib.get_adducts() if a["charge"] > 0)
    lib.add_spectra(
        compound_name="Caffeine",
        compound_formula="C8H10N4O2",
        inchikey="RYYVLZVUVIJVGH-UHFFFAOYSA-N",
        spectra_info={
            "precursor_mz": 195.0,
            "polarity": "POSITIVE",
            "collision_energy": 20.0,
            "mz": np.array([444.0, 555.0], dtype=np.float32),
            "intensity": np.array([1.0, 3.0], dtype=np.float32),
        },
        adduct_id=adduct_id,
    )
    with lib.session_scope() as session:
        from libviz.core.db.models import Spectrum

        spectrum_id = session.query(Spectrum).one().id

    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    annotation_id = _insert_annotation(
        db, library_path=lib_path, library_spectrum_id=spectrum_id, with_lib_raw=False
    )  # no stored lib_raw_mz/intensity -> NULL on the row

    data = Plotter().get_annotation_spectra(db, annotation_id, lib_source="raw")

    np.testing.assert_allclose(sorted(data["library_mz"]), [444.0, 555.0])


def test_plot_ms2_annotation_title_notes_non_default_sources(tmp_path):
    from msianalyzer.core.parser.mzml_parser import init_raw_db

    raw_db = tmp_path / "sample.db"
    con = init_raw_db(raw_db)
    con.execute(
        "INSERT INTO ms2_scans (scan_id, rt, filter_string, mz_array, intensity_array) "
        "VALUES (42, 1.0, 'f', ?, ?)",
        (array_to_blob(np.array([111.0], dtype=np.float32)),
         array_to_blob(np.array([5.0], dtype=np.float32))),
    )
    con.commit()
    con.close()

    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    annotation_id = _insert_annotation(db, sample_raw_db_path=raw_db)

    default_fig = Plotter().plot_ms2_annotation(db, annotation_id)
    assert "empirical:" not in default_fig.layout.title.text

    raw_fig = Plotter().plot_ms2_annotation(db, annotation_id, emp_source="raw")
    assert "empirical: raw, library: filtered" in raw_fig.layout.title.text


def test_plot_ms2_annotation_rejects_invalid_source(tmp_path):
    db = tmp_path / "analysis.db"
    init_analysis_db(db).close()
    annotation_id = _insert_annotation(db)

    with pytest.raises(ValueError, match="emp_source"):
        Plotter().plot_ms2_annotation(db, annotation_id, emp_source="nope")
    with pytest.raises(ValueError, match="lib_source"):
        Plotter().plot_ms2_annotation(db, annotation_id, lib_source="nope")
