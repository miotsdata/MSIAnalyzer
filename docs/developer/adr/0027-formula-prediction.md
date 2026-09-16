# 27 — On-demand molecular-formula prediction

**Status:** Accepted

## Context

Many detected features never get a name: no MS2 was ever collected for
them, or MS2 was collected but nothing in the spectral library
(`core/annotation/annotate.py`) scored well. Right now those features are
dead ends in the GUI — `load_feature_categories` labels them `"no_ms2"` or
`"non_annotated"` and nothing further is offered. Mass decomposition (find
every plausible CHNOPS(+halogen) formula whose theoretical adduct m/z
falls within tolerance of the feature's own m/z — the same technique
MS-DIAL/SIRIUS-style tools use) can at least narrow such a feature down to
a handful of candidate formulas, even without a real compound identity.

This is a smaller, more self-contained decision than target-list matching
(ADR 26), but two choices needed real investigation rather than being
obvious from the request as given: which package does the mass
decomposition, and where in the app this runs.

## Decision

**Package: `msbuddy`, not `find-mfs`.** The original candidate,
`find-mfs`, is GPL-3.0+ — a real problem, since MSIAnalyzer is MIT and
already ships compiled PyInstaller/AppImage binaries (ADR 22–25);
bundling GPL code into a distributed binary carries copyleft/source-
disclosure obligations the rest of the project doesn't have.
[`msbuddy`](https://github.com/Philipbear/msbuddy) is Apache-2.0
(confirmed via PyPI metadata) — permissive, no such obligation — and was
empirically verified before committing to it: a real `pdm add` into this
project's actual Python 3.13 environment succeeded (one transitive
dependency, `brain-isotopic-distribution`, has no `cp313` wheel yet and
compiled from source — worked here, not guaranteed on every machine), and
a real query — `Msbuddy.mz_to_formula(181.0707, adduct='[M+H]+', mz_tol=10,
ppm=True)` — correctly returned `C6H12O6` (glucose) as the top hit at
−0.19 ppm. Its adduct-label strings (`"[M+H]+"`, `"[M-H]-"`, ...) are
drop-in compatible with this project's own `target_list.Adduct.label`
notation, confirmed against 8 different adducts — no translation layer
needed, the adduct table and picker built for target-list matching
(ADR 26) work unmodified here too. `lightgbm` is a second, separate
required dependency: not declared by msbuddy's own PyPI metadata, and
never `import`ed by its source, but its bundled ranking model is a
joblib-pickled LightGBM `Booster`, so unpickling it needs `lightgbm`
importable — omitting it would silently break at first real use, not at
install time.

**On-demand, not a `run.py` pipeline step.** The original idea was
automatic — run mass decomposition on every unannotated feature right
after annotation, in parallel, on every run. Reconsidered after measuring
msbuddy's real cost: a ~420MB reference database, downloaded once and
cached, but loaded **eagerly and fully into memory** on every
`Msbuddy(...)` construction (~600–700MB resident, confirmed by measuring
`RSS` before/after). Forcing every run to pay that cost — a slow first-use
download plus a large permanent memory floor — regardless of whether the
user cares about formula prediction this particular time, is wasteful.
Instead: a new **"Predict formula…" button on the Annotations page**,
opening a window where the user picks *which* features to run it on (any
mix of unannotated and annotated-but-unconvincing ones — a score of any
value is fine, the user just picks the rows) and adjusts settings, then
runs synchronously (blocking the rest of the app, but on a background
`QThread` so Qt's event loop keeps painting rather than reading as a
frozen/unresponsive app). This single mechanism subsumes what was
originally proposed as two separate things — "predict for every
unannotated feature" and "optionally retry for a low-scoring annotated
one" — with no separate low-score-threshold concept needed.

This is the **first feature in this codebase where a GUI action writes
new rows into an already-finished analysis database.** Every other
DB-writing function is only ever called from the pipeline
(`core/run/run.py`) or the CLI — the GUI bridge layer
(`gui/utils/analysis_bridge.py`) has so far been read-only plus one
fire-and-forget rendering job (`MirrorPlotWorker`). `predicted_formulas`
rows are still logged through the ordinary `commands`/`log_command`
provenance mechanism (reusing the analysis's own `metadata.analysis_id`
as `run_id`, read back via the new `analysis_db.load_metadata_value`) —
so this establishes a pattern (bridge slot → `QThread` worker → core
function → `log_command` + save), not a one-off shortcut, for any future
"run one more thing against a finished analysis" feature.

**Config surface stays deliberately minimal**, and lives outside
`Config`/`GROUPS` entirely. `FormulaPredictionSettings`
(`core/annotation/formula_prediction.py`) is a plain dataclass —
`adducts`, `error_ppm`, `top_n`, `halogen` — used only by the new core
function and the new GUI window. It is **not** a `Config` field: no
`Config.version` bump, no `NewAnalysisPage.qml` change, nothing to
configure before a run even starts. The settings actually used for one
invocation are recorded in that invocation's `commands.arguments` JSON —
the same provenance convention every pipeline step already uses — rather
than needing a place in the run's own `Config`. `msbuddy.mz_to_formula`
also exposes `dbe_cutoff`, `integer_dbe`, and full per-element count
ranges; none of that is user-configurable in v1, left at msbuddy's own
defaults, to keep the picker window's surface small. Isotope-envelope
matching (`msbuddy` can optionally cross-check a candidate formula
against an *observed* isotope pattern) isn't used at all — this
pipeline's peak-picked MS1 features don't currently retain a usable
per-feature isotope envelope to hand it.

**Parallelism: one shared engine, `ThreadPoolExecutor`, not
`ProcessPoolExecutor`.** Every other CPU-bound step in this codebase
(`annotate.py`, `precursor_purity.py`, `create_adata.py`) parallelizes
with worker *processes* (`n_workers`, default `os.cpu_count()`) — the
obvious pattern to reach for. Here it would be actively wrong: each
worker process would need its own `Msbuddy` instance, multiplying the
~600–700MB memory cost by the worker count. Since `mz_to_formula`'s inner
loop is `numba`-jitted (which releases the GIL for its compiled
sections), a single shared engine plus a small `ThreadPoolExecutor` gets
real parallelism across a user-picked feature subset without duplicating
that memory at all.

**Representative-annotation precedence gains a third, weakest tier**:
`target_list` (ADR 26) > `ms2` (`rank_feature = 1`) > `predicted` (the
feature's single best predicted candidate — closest `abs(mass_error_ppm)`
across every adduct searched — shown as `"<formula> + <adduct>"`, e.g.
`"C6H12O6 + [M+H]+"`, in place of a compound name). Implemented as a
third CTE (`_PREDICTED_REP_CTE`, `analysis_db.py`) folded into
`load_feature_representative_annotations`, `load_feature_list`, and
`load_feature_categories` (which gains a fifth category, `"predicted"`)
— exactly the shared-precedence-machinery pattern ADR 26 established, not
a new one. None of this touches `ms2_annotations`/`target_list_matches`/
`rank_feature` themselves, only which identity gets *displayed* — a
predicted formula never overrides a real MS2 hit or target-list match,
whether the annotated feature's score is high or low.

`predicted_formulas` gets its own table (`feature_id`, `adduct`,
`formula`, `mass_error`, `mass_error_ppm`, `rank`, `command_id`) — not
merged into `ms2_annotations`, same reasoning as target-list matching
(ADR 26): a predicted formula has no scan, no spectral score, nothing
that machinery's ranking is built around. Unlike target-list matches
(never deleted, only appended) or MS2 annotations (the whole table
replaced on a re-run), a re-prediction for a given feature **replaces
only that feature's own prior rows** (`save_predicted_formulas`) — since
the user re-running prediction with different settings on one feature
expects an update, not accumulation, but every other feature's
predictions must survive untouched (they weren't part of this
invocation).

## Alternatives considered

- **`find-mfs`** — rejected for its GPL-3.0+ license; see above.
- **Pipeline-step integration** (`RUN_STEPS`, like target-list matching)
  — rejected; see "on-demand, not a pipeline step" above.
- **A "low MS2 score" threshold config**, gating an automatic recheck —
  rejected; no such threshold exists anywhere in this codebase today
  (`AnnotateConfig` has no score floor, only `min_matched_peaks`), and the
  user-picked feature list in the new window already lets the user decide
  per feature, without inventing a new numeric cutoff nobody has an
  informed opinion on yet.
- **`ProcessPoolExecutor`** for the per-feature queries — rejected; see
  "parallelism" above.

## Consequences

- Two new required dependencies: `msbuddy`, `lightgbm` (both
  Apache-2.0/MIT-safe). A machine without a `cp313` wheel for
  `brain-isotopic-distribution` needs a working C toolchain to install
  MSIAnalyzer at all going forward — worth a real check on whatever CI
  runners matter, not just assumed from this session's one successful
  install.
- First real use on any given machine downloads ~420MB and costs
  ~600–700MB of resident memory for the lifetime of one "Predict
  formula…" invocation — a real, user-visible cost the GUI must surface
  honestly (a real progress indicator, not a silent multi-second hang) —
  see the deferred GUI work below.
- This establishes the codebase's first "GUI writes into an
  already-finished analysis DB" pattern — a template for any future
  similar feature, not a one-off.
- Deferred, not built in this round: the top-hits list merge (a `kind`
  discriminator across `ms2`/`target_list`/`predicted`, one small
  tag/badge component — the same still-open item ADR 26 already flagged
  for target-list's own top-hits surfacing, now extended to cover
  `predicted` too rather than building the merge machinery twice), the
  `MirrorPlotDetailWindow` no-MS2 placeholder branching by `kind`, and
  reflecting the new `"predicted"` category in the MS1 spectrum's colors.
