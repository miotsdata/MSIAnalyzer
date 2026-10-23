# 56 — Per-run log file in the output folder, and an Output tab

**Status:** Accepted

## Context

A GUI run left no log next to its results. The GUI writes one DEBUG log per
app session in the user's log folder (`default_debug_log_dir()`), mixing
every run of the session with GUI activity. The CLI's `-v` / `-l` flags have
no GUI equivalent. Reviewing or sharing what a run did meant digging that
session file out.

## Decision

- **Each run writes `run_<YYYYmmdd_HHMMSS>.log` into its output folder**, a
  new file per run, at a level the user picks. `Run.start` attaches the
  handler and removes it when the run ends (also on failure), so GUI and CLI
  runs behave the same.
- **Only pipeline records** (`msianalyzer.core.*`) go to it, including those
  forwarded from worker processes by `worker_logging`. GUI records, such as
  heatmap rendering at DEBUG, stay out.
- **The first line names the run**: run id and level.
- **`analysis.log_level`**: `off`, `debug`, `info` (default), `warning` or
  `error`. `off` writes no file. It's saved with the run's config like any
  other setting. A new field with a default, so no config version bump
  (as in ADR 20/21). If the `msianalyzer.core` loggers would otherwise
  filter out the chosen level (e.g. a script that never configured
  logging), their level is lowered for the run and restored afterwards.
- **The config file format is unchanged.** `out_dir` stays in `io` and the
  database name and log level in `analysis`.
- **GUI: "input/output" becomes two tabs.** "input" keeps the project folder
  and the samples. "output" is a new bespoke tab, like the target-list tab:
  output folder, analysis database name, and the log level as a dropdown.
  The generic "analysis database" tab is gone, and a targeted analysis
  shows "output" too.
- The CLI's `-v` (console level) and `-l` (extra file) stay as they are; the
  session-wide DEBUG log too.

## Alternatives

- **One `run.log` appended on every re-run:** rejected by the user in favour
  of a new timestamped file per run.
- **Move `out_dir` and `db_name` into a new `output` config section:**
  matches the tabs, but breaks every saved config file for no functional
  gain.
- **Generic "fixed choices" support in the schema-driven form** for the
  dropdown: not needed once the Output tab is bespoke.

## Consequences

- Runs in the same output folder within the same second share a log file
  (appended).
- Analyses run before this ADR have no run log.
