# 59 — Portable project paths

**Status:** Accepted

## Context

Paths in a config are meant to be relative to the project root (see
`IOConfig`, and the user guide's "Relative paths ... are resolved against
`project_folder`"). The GUI didn't follow that: the New Analysis form
fills `out_dir` with an absolute path, and `Config.to_dict` /
`Run.to_dict` stored every path as given. A project created at
`/data/Projects/P` and copied to a NAS kept pointing at `/data/Projects/P`:

- On Linux, opening the NAS copy silently showed the **local** copy's
  results.
- On Windows, the same NAS folder (a drive letter) found nothing. Every tab
  was empty and MS1 crashed.

`samples.raw_db_path` in the analysis DB is absolute too (used by H&E
coregistration).

## Decision

- **Store paths inside the project relative to it.** `Config.to_dict`
  rewrites `io.out_dir`, `mzml_paths`, `xml_paths` and `db_paths` that
  lie inside `project_folder` as relative POSIX paths, and `Run.to_dict`
  does the same for `config_path`. This covers both the project file and
  `configs/run_*.yaml`. The in-memory `Config` stays absolute.
  `Run.start` already `chdir`s to the project, so a reloaded relative
  config runs as before. Paths outside the project (raw data on another
  share, spectral libraries) stay absolute.
- **Resolve on read against the project that was opened**
  (`core/utils/paths.resolve_in_project`). Relative paths are joined to
  the project folder. For an absolute path, the longest trailing part
  that exists under the opened project wins, *even if the old location
  still exists*: you see the results of the folder you opened.
  `/data/Projects/P/output/run1` opened as `Z:\P` resolves to
  `Z:\P\output\run1`. `PureWindowsPath` parses both separators, so paths
  written on either OS resolve on both. This is used by:
  - `AnalysisModel.outDir` (and so every analysis DB and `.h5ad` read),
  - `ProjectModel.runsList`,
  - `Project.delete_run`,
  - `analysis_db.get_sample_raw_db_path`, which finds the project by
    walking up from the analysis DB.
  Existing projects work without migration or re-run.
- **Missing output fails clearly.** `AnalysisModel.outDirMissing` makes
  the Analysis page show "Output folder not found: <path>" and load no
  section, instead of empty tabs and a crash.

## Consequences

- A project folder can be moved, or shared between Linux and Windows,
  with its analyses intact.
- Re-running from another OS still needs the raw data and libraries
  reachable at their (absolute, outside-project) paths.
- `samples.raw_db_path` is still written absolute; resolution on read
  covers it.
