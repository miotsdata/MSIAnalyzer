# 28 — GUI launch-directory independence

**Status:** Accepted

## Context

`msianalyzer-gui run` failed outright when the GUI was launched from a
directory outside the project's folder tree — reported from real-app use:
"when I run msianalyzer-gui from a folder, and run an analysis, it doesn't
run if I run msianalyzer-gui outside the project folder."

Root-caused directly, not guessed: `Run.start()` (`core/run/run.py`)
calls `self.project = Project.load()` with no `start_path`. `Project.load()`
walks up from `Path.cwd()` — the CLI's original design assumption, "run
`msianalyzer` from inside the project, like `git`" (`get_project_folder`'s
own docstring: "similar to how Git finds `.git`") — to find `.msianalyzer.yml`.
For the GUI, `Path.cwd()` is just wherever the process happened to be
launched from (a desktop app has no shell cwd the user deliberately set),
which has nothing to do with which project is open. `Run.start()` already
does `os.chdir(self.config.io.project_folder)` two lines later — clearly a
recognition that the pipeline needs to run from inside the project folder —
but that chdir comes *after* the `Project.load()` call it needed to protect,
so it never helped this specific failure at all.

## Decision

Two independent fixes, addressing two different points where "cwd" leaked
in as an implicit, GUI-inappropriate assumption:

1. **`Run.start()`**: pass the already-known project folder explicitly —
   `Project.load(self.config.io.project_folder)` — instead of relying on
   `Project.load()`'s cwd-relative default. This is the actual fix for the
   reported crash; it makes `Run.start()` correct regardless of the calling
   process's cwd, GUI or CLI.
2. **`CoreBridge.load_project()` / `create_project()`** (`gui/utils/core_bridge.py`):
   `os.chdir()` to the project's folder once it's successfully opened/created,
   before `projectLoaded` is emitted. This matches the user's own stated
   mental model ("when I open a project, the cwd become[s] that project
   folder") and is deliberately kept as a second, independent fix — not a
   substitute for (1) — as insurance against any other not-yet-found place
   in the pipeline that assumes "cwd is inside the project" the way the CLI
   always could.

## Alternatives considered

- **Only fix `Project.load()`'s call site, skip the GUI-side chdir.** Would
  have resolved the reported crash on its own. Rejected as incomplete — the
  CLI's whole design (`get_project_folder`'s git-style upward walk) treats
  "cwd is inside the project" as a standing assumption that other code paths
  could just as easily lean on later; the GUI has no shell-set cwd to rely
  on in the first place, so making "open a project" establish one is the
  more durable fix for the GUI specifically.
- **Only add the GUI-side chdir, skip the `Project.load()` fix.** Rejected —
  chdir happens once, when a project is *opened*; `Run.start()`'s own
  `Project.load()` call would still be silently relying on that side effect
  rather than being correct on its own terms (e.g. for any future non-GUI
  caller, or a test that constructs a `Run` without going through
  `CoreBridge` first).

## Consequences

- `msianalyzer-gui` can now be launched from anywhere; opening or creating a
  project makes that project's folder the process's cwd for as long as it
  stays open, matching how the CLI has always expected to be invoked.
- `IOConfig.resolve_paths()` (anchors `out_dir`/`mzml_paths`/`xml_paths`/
  `db_paths` to `project_folder` when relative) was found, while
  investigating this, to never actually be called anywhere in the runtime
  path — only referenced in its own definition and a unit test. Not touched
  here (out of scope for this fix — every path the GUI submits today is
  already absolute), but worth a deliberate look: either wire it in where
  `Config` is built/loaded, or remove it if genuinely dead.
