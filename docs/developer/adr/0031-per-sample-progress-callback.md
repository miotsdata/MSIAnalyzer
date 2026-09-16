# 31 — Per-sample progress callback for `process_samples`

**Status:** Accepted

## Context

[ADR 11](0011-run-progress-callback.md) gave the GUI one `started`/
`completed`/`skipped`/`failed` event per pipeline stage — good enough for a
checklist, but `process_samples` (usually the longest-running stage, and the
one whose duration scales directly with how many samples/files the user
picked) gave no visibility beyond "still running" for however long every
sample takes. ADR 11's own "Consequences" flagged this gap directly: "a
finer-grained callback (e.g. per-sample during `process_samples`) is not
implemented and would be a separate, additive change if ever needed."
Reported as wanted: "progress bars for each step (the one on samples, with
number of samples)."

`run_core`'s sample loop collected results via `executor.map(worker, ...)`,
consumed as `list(executor.map(...))`. `map()` already submits every sample
to the pool up front (full parallelism from the start), but *yields results
back in submission order* — consuming it via `list(...)` blocks on sample 1
even if sample 3 actually finished first, and gives no hook to observe a
sample finishing at all, only the whole batch finishing.

## Decision

- `run_core`: replace `executor.map(...)` with per-sample
  `executor.submit(...)`, collected via `concurrent.futures.as_completed`
  instead — same parallelism (every sample still submitted to the same pool
  immediately), but results are now observed as each one *actually*
  finishes rather than in submission order. Each result is written back to
  its own original index (`results[futures[future]] = future.result()`),
  since downstream code (`out_db_paths`/`all_peaks_mzs`, used for m/z
  alignment and TIC normalization) depends on original sample order, not
  completion order.
- `Run.on_sample_progress: Callable[[int, int], None] | None`, alongside
  `Run.on_step` (same pattern: a constructor default of `None`, set via a
  new `Run.start(..., on_sample_progress=...)` parameter, invoked through a
  `self._emit_sample_progress(done, total)` helper mirroring `_emit_step`).
  Emits `(0, total)` before the pool starts, then `(n, total)` after each
  sample completes.
- `RunWorker.sampleProgress = Signal(int, int)` / `CoreBridge.runSampleProgress
  = Signal(int, int)`, wired the same way `on_step`/`stepChanged`/
  `runStepChanged` already are.
- `RunningAnalysisPage.qml`: every step row gets a `ProgressBar` while its
  own status is `"started"` — `indeterminate: true` for every step except
  `process_samples` (nothing finer than started/completed exists for those,
  an indeterminate bar reads better than static text), a real determinate
  one (`value`/`to` bound to `samplesDone`/`samplesTotal`) only for
  `process_samples`. The step's own status label also grows a `"(done/total)"`
  suffix while that step is `process_samples` and running.

## Alternatives considered

- **Indeterminate bar only for `process_samples` too**, not a real count.
  Rejected — explicitly asked for ("with number of samples"), and the
  `as_completed` switch to get it costs nothing (see below).
- **Keep `executor.map()`, poll `futures`/some shared counter from another
  thread instead.** Rejected — `as_completed` already exists for exactly
  this and needs no polling thread or shared-state synchronization of its
  own.

## Consequences

- No change in wall-clock time or actual parallelism for `process_samples` —
  confirmed directly (see [[gui-polish-batch-2026-09-16]] for the reasoning
  worked through with the user before implementing): `submit()` +
  `as_completed()` submits every sample to the pool immediately, identically
  to `map()`; only how results are *collected* changed.
- `on_sample_progress` is excluded from `Run.to_dict()`, same as `on_step` —
  not YAML-serialisable, not run state.
- CLI/API behavior unchanged: `on_sample_progress` defaults to `None`, every
  existing `Run.start()` call site unaffected.
- Test fixture note for anyone touching `run_core` tests: mocking the
  sample loop now means mocking `executor.submit(...)` (returning real,
  already-completed `concurrent.futures.Future` objects) rather than
  `executor.map(...).return_value` — see `_mock_pool_executor` in
  `tests/core/unit/test_run.py`. A *real* `Future` is required, not a
  further mock, since `run_core` calls the real (unmocked)
  `concurrent.futures.as_completed` against whatever `submit()` returns.
