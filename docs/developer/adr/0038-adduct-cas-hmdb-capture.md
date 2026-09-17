# 38 — Capture adduct/CAS/HMDB for MS2-annotated hits

**Status:** Accepted

## Context

The planned Export menu's Annotation export needs adduct (required) and
CAS/HMDB registry numbers (optional, if the library has them) for every
identified feature. Checked directly, not assumed: none of these are
stored anywhere for an MS2-annotated (library-matched) hit today —
`ms2_annotations` only ever captured `compound_name`/`compound_formula`/
`inchikey` from the matched library candidate. The other two tiers
already had what they need: `target_list_matches.adduct_label` (a target
compound is searched for by a specific adduct) and
`predicted_formulas.adduct` (ADR 27) — this gap was ms2-tier only.

`libviz` (the separate package spectral libraries are built with) *does*
have this data — `Spectrum.adduct_type_id` links to a real adduct, and a
generic `spectrum_metadata` key/value table can hold whatever else a
source `.msp` file provided — but `Library.get_spectra_in_mz_range`
(the one method `annotate.py`'s candidate-gathering already calls) never
joins either. Extending that method would need a new `libviz` release,
and `libviz`'s release is currently blocked on the maintainer's YubiKey
(see the `libviz-release-pending-yubikey` memory) — a hard blocker this
feature would otherwise have inherited for no good reason, since
`Library` already exposes `self.engine`/`self.session_scope()` as plain
public attributes, the same surface `Library` uses internally for
everything else.

## Decision

- `annotate.py`'s `_gather_candidates` runs one additional batched query
  per candidate batch (not N+1) against the library's own
  `session_scope()`, joining `Spectrum.adduct_type_id → AdductType.formula`
  for `adduct`, and a separate `spectrum_metadata` lookup for `cas`/`hmdb` —
  entirely within MSIAnalyzer's own code, no `libviz` changes.
- CAS/HMDB are explicitly best-effort: `libviz`'s own `.msp` importer
  dumps every non-standard field into `spectrum_metadata` verbatim under
  whatever key the *source* file used — there's no standard key across
  libraries. `_gather_spectrum_extras` checks a handful of common
  case-insensitive variants (`cas`/`cas#`/`casno`/`cas_number`/`casrn`,
  `hmdb`/`hmdbid`/`hmdb_id`) and leaves the field `None` when nothing
  matches, rather than trying to be exhaustive.
- `Candidate`/`AnnotationRow` gain `adduct`/`cas`/`hmdb` fields (trailing,
  defaulted `None` — no existing construction call site needs updating).
  `ms2_annotations` gains matching nullable columns.
- `load_feature_representative_annotations` (the GUI Annotations table's
  and the new export's shared reader) now also selects these for the ms2
  tier, and `target_list_matches.adduct_label` for the target-list tier
  (already stored, just not previously surfaced here) — `cas`/`hmdb` stay
  `None` for target-list and predicted tiers, which never had real
  compound-registry identity to begin with (same reasoning ADR 27 already
  used for why a predicted row has no score/coverage/peak-count).
- New `include_unidentified: bool = False` parameter on that same
  function — the Export menu wants a row for *every* feature, including
  ones with no identity at all; the GUI Annotations table (the existing,
  default behavior) still only wants features that have something to
  show. One shared query, not a duplicate.
- **A genuinely new limitation for `_ensure_column`** (the helper behind
  `AnalysisBridge.ensureSchemaCurrent`, ADR 37): `CREATE TABLE IF NOT
  EXISTS` is a no-op on a table that already exists — it never adds a
  *column* a newer schema introduced, unlike `CREATE INDEX IF NOT EXISTS`
  (a separate object). `create_analysis_schema` now explicitly
  `PRAGMA table_info` + `ALTER TABLE ADD COLUMN`s these three columns
  after the (no-op-on-an-existing-table) `CREATE TABLE`, so an
  already-run analysis's `ms2_annotations` gets the new *columns*
  retroactively via `ensureSchemaCurrent` too — though, unlike the ADR 33
  index, the *data* in them still requires a re-run (this is new
  information captured at annotation time, not derivable from what's
  already stored).

## Alternatives considered

- **Read adduct/CAS/HMDB live from the library file at export time**,
  instead of capturing them into `ms2_annotations` at annotation time.
  Considered and explicitly rejected by the user — more fragile (blank if
  the library file has since moved or been deleted) in exchange for
  avoiding a re-run, and this project already accepts "re-run needed for
  a schema change" as the standing convention (ADR 10, 19, 33).
- **Modify `libviz`'s `get_spectra_in_mz_range`** to join adduct/metadata
  itself. Rejected — ties this feature to a blocked external release for
  no benefit, when the same tables are already reachable without it.

## Consequences

- An already-run analysis needs a re-run for `adduct`/`cas`/`hmdb` to
  actually populate on its existing ms2-tier rows — the columns
  themselves arrive for free via `ensureSchemaCurrent` (ADR 37), the data
  does not.
- Any future column addition to an existing table needs the same
  `_ensure_column` treatment as this one, not just the CREATE TABLE text —
  worth remembering as this project's schema keeps evolving, since it's
  an easy step to forget (a fresh analysis would still work fine, masking
  the gap until someone opens an *old* one).
