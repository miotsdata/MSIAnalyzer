# 60 — Target list after the run

**Status:** Accepted

## Context

Target-list matching (ADR 26) runs only inside the pipeline. A user who
forgot some compounds, or gets a new list, had to check features one by
one, or re-run the whole analysis.

## Decision

- **Analyses → Match target list…** (`TargetListMatchDialog.qml`) takes the
  same inputs as `target_list`: files, polarity, adducts and `match_ppm`.
  They are pre-filled from the analysis' own config
  (`AnalysisModel.targetListConfig`).
- **Preview, then apply.** `target_list.preview_post_run_matches` reuses
  `match_target_list` against `features` and writes nothing. It returns
  one row per (compound, adduct) on an existing feature, plus one "not
  detected" row per compound with no match. Each row has the feature's
  current representative name.
- **The user decides per row** with a "Use" checkbox:
  - It is pre-ticked only when the feature is unnamed or only has a
    predicted formula.
  - At most one row per feature is ticked. Ticking another unticks it,
    because the target CTE joins several names with commas.
- **No injection.** An undetected compound would need its m/z measured
  again from every raw DB into every `.h5ad`. That's too slow to do
  after the run (see the front-load-config preference), so these rows
  are only listed.
- **Apply** (`apply_post_run_matches`) saves the ticked rows into the
  existing `target_list_compounds` / `target_list_matches` tables
  (`match_type = 'existing'`). They are saved under one
  `commands` row named `match_target_list_post_run`, with the settings as
  arguments. The existing precedence (target list > MS2 > predicted) then
  shows the names everywhere. There's no schema change and no re-run.
  Unticked rows are not saved.
- **Undo:** `delete_command_target_list_rows` deletes the rows of those
  commands. The pipeline's own target-list rows are untouched.
- **Refresh:** the bridge drops its feature-list, summary and spatial
  caches and emits `targetListMatchesChanged(db)`. Annotations, the
  heatmap controls and Spatial Correlation reload on that signal.

## Consequences

- Names can be added to a finished analysis in seconds, and undone.
- A compound not detected during the run can still only be added by
  re-running with it in the config.

## Addendum: targets given by m/z

Target-list files can give an ion `mz` (or `m/z`) column instead of, or as
well as, `formula`. `inchikey` becomes optional. This applies to the
pipeline as well as after the run, since both use the same parser.

- **The m/z wins over the formula.** A row with an m/z is searched at that
  m/z only, through the pseudo-adduct `GIVEN_MZ` (label `"m/z"`, charge
  0), whatever the adducts and the formula say. The formula is kept for
  display, and a parser log line counts the rows that have both.
- In the pipeline, an unmatched m/z target is injected at that m/z like
  any other target.
- Storage: `target_list_compounds.target_mz REAL` is nullable, and is added
  to older analyses through `_ensure_column`. An m/z-only row stores
  `formula = ''` and `neutral_mass = 0`. The display CTE uses
  `NULLIF(formula, '')`, so a blank formula never shows.
