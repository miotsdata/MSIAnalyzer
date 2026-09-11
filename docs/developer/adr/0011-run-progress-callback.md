# 11 — `Run` step-progress callback for GUI consumption

**Status:** Accepted

## Context

The GUI needs a page that shows each pipeline stage and marks it completed as
the run progresses. `run_core` already knows exactly when each stage starts,
finishes, is skipped (disabled by config) or fails — but that only reaches
`logger` calls today. The GUI runs `Run.start()` on a background `QThread`
and needs a way to observe stage transitions from the main/GUI thread without
polling the log or the output folder, and without re-implementing
`run_core`'s orchestration (caching checks, parallel sample processing, path
setup) in the GUI layer just to get progress events.

## Decision

Add an optional `on_step: Callable[[str, str], None]` parameter to
`Run.start()`, stored as `self.on_step` and invoked via a small
`self._emit_step(step, status)` helper at each stage boundary already present
in `run_core`. `status` is one of `"started"`, `"completed"`, `"skipped"`
(the stage's own enable/config check short-circuited it — `purity.enabled`,
no `annotate.library_path`, `consensus.enabled`, `report.enabled`) or
`"failed"` (only wired for `process_samples`, the one stage with existing
explicit exception handling; a failure elsewhere propagates out of
`run_core`/`start()` unmarked, which is enough for a v1 "the run failed"
signal). A stage that is *cached* (its own internal
`is_command_already_run`/file-exists check hits) still reports `"completed"`,
not `"skipped"` — the distinction is config-disabled vs. already-done, both
of which leave the stage's output present.

A new module-level `RUN_STEPS` tuple (`process_samples`, `align_mz`,
`group_ms2`, `precursor_purity`, `annotate_ms2`, `ms2_consensus`,
`assemble_adata`, `summary_report`) gives consumers the canonical order and
full set of stages up front, so a checklist can be rendered before any event
arrives.

`Run.start()` also gained `config_path`, recorded on `self.config_path` (a
plain string) for provenance — which config file produced this run, shown in
the GUI's project page alongside the run's date and output folder. Defaults
to `config_file` when that was given and `config_path` wasn't.

## Consequences

- CLI/API behavior is unchanged: `on_step` defaults to `None`, and every
  existing `Run.start()` call site is unaffected.
- `on_step` is excluded from `Run.to_dict()` (alongside the existing
  `project` back-reference) — a callable isn't YAML-serialisable, and it
  isn't run state.
- Step granularity is coarse — one event pair per pipeline stage, not per
  sample or per row. Good enough for a checklist-style progress page; a
  finer-grained callback (e.g. per-sample during `process_samples`) is not
  implemented and would be a separate, additive change if ever needed.
