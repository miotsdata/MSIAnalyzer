# 26 — Target-list compound annotation

**Status:** Accepted

## Context

Users want to check detected features against a known list of compounds
(name, chemical formula, InChIKey) by theoretical m/z — independent of,
and complementary to, the existing MS2 spectral-library annotation (Stage
B, `core/annotation/annotate.py`). A formula alone has no m/z; matching
requires searching across configurable ion adducts (protonation,
sodiation, etc.), MS-DIAL-style. A target compound may or may not land
near an already-detected feature; when it doesn't, it still needs a place
in the pipeline so the user can see it was searched for, rather than being
silently absent because it never happened to survive peak detection.

This touches five areas that already have established conventions worth
following rather than reinventing: chemistry/mass calculation, adduct
tolerances (ADR 5), the feature/`features` table model, the MS2
representative-selection machinery (ADR 21), and the config-form
generator's fixed set of recognized field shapes.

## Decision

**Chemistry**: `pyteomics.mass.calculate_mass` for formula → monoisotopic
mass. Already resolved in this project transitively (via `libviz`, which
uses the same function for the same purpose — spectral-library
construction); promoted to an explicit direct dependency rather than
relying on it silently through another package. No new dependency
introduced. Raises a clean, catchable error (`PyteomicsError`) on an
invalid formula, wrapped into this project's own `InvalidFormulaError`
with the offending file/row/compound name attached.

**Adducts**: a new, MSIAnalyzer-owned static table
(`core/annotation/target_list.Adduct`), MS-DIAL style, covering both
polarities (16 adducts total). `libviz`'s own adduct model
(`AdductType`) was considered and rejected — it's a SQLAlchemy ORM class
tied to libviz's own DB session (wrong shape to import), and only has 8
entries, too sparse. This project's deltas are derived from
`pyteomics.mass.calculate_mass` on each adduct's neutral fragment (±
electron mass for the resulting charge) rather than hand-typed constants,
so they're auditable and re-derivable — cross-checked against `libviz`'s
own hardcoded values and matched to 6 decimal places.

**File formats**: CSV and plain-delimited TXT only for v1 — no Excel
support (would need a new `openpyxl` dependency for no clear need yet).

**Polarity**: an explicit `TargetListConfig.polarity` field
(`"positive"`/`"negative"`, validated as a plain `str` in
`__post_init__`, not a `Literal` type — which would crash the GUI's
config-form generator, `gui/utils/config_schema.py`'s `_classify`, since
it only recognizes a fixed set of type shapes). Not auto-detected from
the mzML/raw data — the two adduct sets are physically incompatible with
each other, so validating them at config-parse time (before any parsing
has even happened) is both simpler and sufficient.

**Tolerance**: a fifth, independent `match_ppm` — per
[ADR 5](0005-three-ppm-tolerances.md)'s established precedent of one
tolerance per distinct physical comparison, not reusing
`align_ppm`/`assoc_ppm`/`candidate_ppm`/`integration_ppm`.

**Schema**: target-list matches get their own tables
(`target_list_compounds`, `target_list_matches`) — never merged into
`ms2_annotations`/`rank_feature`. That machinery (ADR 21) is entirely
MS2-spectral-score-driven (score, matched-peak counts, a scan to point
at); a target-list match has none of that, only a formula, an adduct, and
a ppm difference. `features` gains one column,
`origin TEXT NOT NULL DEFAULT 'detected' CHECK (origin IN ('detected',
'injected'))`. Analysis databases are created fresh per run (`CREATE
TABLE IF NOT EXISTS`, never migrated across versions), so this is a plain
schema addition plus a `Config.version` bump (15 → 16), not a migration.

**Injection**: a target compound/adduct whose theoretical m/z isn't
within `match_ppm` of any existing feature gets a new synthetic feature
(`features.origin = 'injected'`, `members_json` an all-`None` dict — no
real per-sample peak backs it). Multiple compounds/adducts landing on the
same unmatched mass are clustered together first (same greedy
sort-and-chain approach `align_mz_across_samples` already uses, minus its
one-per-sample constraint) so they collapse into one injected feature,
not several near-duplicates.

**Injected features get a real heatmap, not a fake one.**
`create_spatial_adata` doesn't read `features` directly — it takes an
arbitrary `target_mz_set` and independently re-quantifies each m/z
against raw per-pixel MS1. Unioning an injected feature's exact m/z into
`target_mz_set` before `assemble_adata` runs (`core/run/run.py`) means it
gets a genuine, directly-measured per-sample value — not an interpolated
or synthetic one. This union is done unconditionally alongside every
`match_target_list` run (and re-read from the DB, not carried in memory,
so it's correct whether that step just ran or was already-done and
skipped) — it must never be split from the injection step, because
`core/plotting/heatmap.py`'s `_feature_column_index` does unconditional
nearest-neighbor m/z matching with **no tolerance check** (a pre-existing,
unrelated gap): a feature added to `features` without its m/z also
reaching every sample's `.h5ad` would silently show an unrelated
feature's heatmap, with no error.

**Representative-label precedence**: a feature with a target-list match
displays that identity unconditionally over an MS2 `rank_feature = 1`
pick, when both exist — it was explicitly searched for by name, unlike an
automatically-scored MS2 hit. This is a read-layer rule (one shared SQL
fragment reused by `load_feature_representative_annotations`,
`load_feature_list`, `load_feature_categories`), not a change to how
`rank_feature` itself is computed. `load_feature_categories` gains a
fourth MS1-coloring category, `"target_list"`, so an injected/no-MS2
feature isn't misleadingly bucketed as plain `"no_ms2"`.

## Alternatives considered

- **A precomputed adduct-mass column on `target_list_compounds` per
  adduct, computed once at parse time.** Rejected in favor of computing
  `theoretical_mz` on the fly during matching — the compound×adduct
  cross-product is small (tens of compounds × ~16 adducts, not
  per-scan-scale), so precomputing buys nothing and would need its own
  cache-invalidation story if the adduct table ever changes.
- **Reusing `libviz`'s adduct infrastructure directly** (its DB models,
  or exporting its `COMMON_ADDUCTS` list). Rejected — wrong shape (ORM
  class vs. plain data) and insufficient coverage (8 adducts vs. the
  ~16-entry MS-DIAL-style set wanted here).
- **Auto-detecting polarity from `samples.polarity`** (already a column,
  populated from the mzML) instead of an explicit config field. Considered
  — would remove one thing the user has to state explicitly — but rejected
  for v1: it couples config validation to parsing having already happened
  (today config validates standalone, before any file is touched), and a
  project can in principle mix per-sample polarities in ways a single
  config-level choice can't cleanly resolve anyway. An explicit field is
  simpler and sufficient; cross-checking it against `samples.polarity` as
  a non-blocking warning is a reasonable future addition, not built here.

## Consequences

- `Config.version` bumps 15 → 16 — existing analyses need a re-run to gain
  target-list data, same category of break as every prior schema-changing
  ADR (most recently ADR 19).
- Every `create_spatial_adata` call site must always receive
  `target_mz_set = aligned_features ∪ injected_features`, never just the
  aligned set alone — `core/run/run.py`'s wiring computes both from the
  same source in the same code path specifically so this can't be
  forgotten in the future.
- GUI integration (top-hits list merge showing a target-list hit
  alongside MS2 hits, the mirror-plot placeholder for a hit with no real
  spectrum, immediate file-selection validation) is deliberately scoped as
  separate follow-up work, not part of this change — the core
  pipeline/schema/report/config piece is independently useful and
  testable on its own (a target-list-configured run already produces
  correct `features`/`target_list_matches` rows and a real report section
  without any GUI changes).
- The adduct list itself, while cross-checked against `libviz`'s own
  values where overlapping, was otherwise assembled from published
  MS-DIAL conventions rather than copied from a single canonical source —
  worth an independent literature-value review before this feature is
  used to make real identification decisions.
