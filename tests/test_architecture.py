"""Architecture rules: layer direction and import cycles (ArchUnitPython)."""

from pathlib import Path

from archunitpython import CheckOptions, assert_passes, project_files, project_layers

SRC = str(Path(__file__).parents[1] / "src" / "msianalyzer")

# `if TYPE_CHECKING:` imports never execute, so they cannot form a runtime cycle.
OPTIONS = CheckOptions(ignore_type_checking_imports=True)

# Known cycles, as sets of file paths relative to SRC. Remove an entry once
# the cycle is broken; adding one should be a deliberate decision.
KNOWN_CYCLES = {
    # heatmap dispatches local-Moran renders to local_moran (function-local
    # import, keeps squidpy off the GUI startup path), and local_moran reuses
    # heatmap's grid helpers.
    frozenset({"core/plotting/heatmap.py", "core/spatial/local_moran.py"}),
}


def test_layers():
    # defined_by (full path), not defined_by_folder: "**/core/**" as a folder
    # pattern skips files directly in core/ (e.g. core/export.py).
    rule = (
        project_layers(SRC)
        .layer("core").defined_by("**/msianalyzer/core/**")
        .layer("cli").defined_by("**/msianalyzer/cli/**")
        .layer("gui").defined_by("**/msianalyzer/gui/**")
        .where_layer("core").may_not_depend_on_layers("cli", "gui")
        .where_layer("cli").may_not_depend_on_layers("gui")
    )
    assert_passes(rule, OPTIONS)


def test_no_new_cycles():
    violations = project_files(SRC).should().have_no_cycles().check(OPTIONS)
    cycles = {
        frozenset(str(Path(e.source_label).relative_to(SRC)) for e in v.cycle)
        for v in violations
    }
    assert cycles - KNOWN_CYCLES == set(), f"new import cycles: {cycles - KNOWN_CYCLES}"
