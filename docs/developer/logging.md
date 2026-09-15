# Logging

Logging in MSIAnalyzer is how a run is made legible after the fact — what ran,
in what order, how long each step took, and exactly what went wrong.

## Principles

[`configure_logging`][msianalyzer.core.utils.logging_utils.configure_logging]
sets up the handlers; every module then makes its own logger with
`logger = logging.getLogger(__name__)`.

Every public pipeline function is wrapped with the
[`@log_call`][msianalyzer.core.utils.logging_utils.log_call] decorator, which
emits a `start <Class.func>` record on entry and an `end <Class.func> (N ms)`
record on exit (both DEBUG), and on an exception logs `fail <func>` with the
traceback before re-raising. Pass `@log_call(source="db_path")` to copy a path
argument onto the record's `source_file` field so it shows in the log's source
column. Hot per-item helpers (`associate_scan`, `reverse_dot_product`, …) are
**not** decorated — the trace would drown the log.

The human-readable narrative on top of that trace is a handful of `logger.info`
lines at the stage boundaries in `run.py` and the `run_*` orchestrators.

## Configure Logging

Three handlers:

- **console** — `level` and above (set by `run -v`), concise format.
- **user file** — only when `run -l PATH` is given; same `level` as the console.
- **debug file** — always DEBUG, verbose format. Written to
  `<project_folder>/logs/debug_<run-id>.log` for a `run`; other commands only get
  the console.

`configure_logging` is called once at import in `cli/main.py` (console only), and
again by `run_command` once the project folder and run id are known.

The GUI (`gui/main.py`'s `main()`) calls `configure_logging(level=logging.DEBUG)`
with no `debug_log_dir` — so it hits the default,
`Path(__file__).resolve().parents[2] / "log"`, i.e. **inside the installed
package itself** (`src/msianalyzer/log/` in a source checkout), not a
project-relative `logs/` directory like a CLI `run` gets. This is a known
wart, not a deliberate design: it's why every GUI debug run leaves a file
there, silently, forever (never gitignored or git-tracked), and why
`pdm build`'s packaging has to explicitly exclude that directory — see
[distribution & packaging](distribution.md#packaging-excludes). Worth
giving the GUI a real project-relative (or user-cache-directory) log
destination at some point; not changed yet.

## Verbosity — `msianalyzer run -v` / `-l`

`-v {debug,info,warning,error,critical}` (default `info`) sets the level of the
console **and** the `-l` file. `-l PATH` writes that run's log to `PATH`; omitted,
there is no user log file. The always-on DEBUG project file is unaffected by
`-v`.

```
msianalyzer run -c cfg.yaml -l results/run.log -v debug
```

## Multiprocessing

Worker processes have their own logging state, so
[`worker_logging`][msianalyzer.core.utils.logging_utils.worker_logging] runs a
`QueueListener` over the parent's handlers for the lifetime of a
`ProcessPoolExecutor` block, and the pool `initializer` points each worker's root
logger at that queue. Used by the per-sample pool in `run_core`, the annotation
pool, and `create_spatial_adata`, so `-l` and the debug file capture parallel
stages with the parent's formatting.

## Database errors

`core/utils/db.py` wraps every DB write: `safe_execute` / `safe_executemany` log
an **ERROR** naming the table, the statement, and the exact offending
parameters (for a batch, the failing row is isolated by replaying it row by row
inside a `SAVEPOINT`, so nothing partial is left behind), then re-raise the
original `sqlite3.Error` unchanged.

## INFO vs DEBUG

*Should the user know about this, or is it only useful to me?* "Only me" → DEBUG,
otherwise INFO. The `@log_call` trace is always DEBUG.
