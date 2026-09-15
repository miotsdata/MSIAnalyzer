# 22 — Distribution & CI strategy

**Status:** Accepted

## Context

The app had no distribution path beyond a source checkout, and no CI. Two
constraints shaped the discussion before any implementation:

- No Windows machine available to build or test a Windows installer on.
- Reluctance to rely on GitHub for **binary artifact storage** — release
  installers/AppImages for a PySide6 + QtWebEngine + numpy/scipy stack are
  legitimately large (hundreds of MB), and GitHub's free-tier storage and
  bandwidth are not a good fit for that, independent of whether the *code*
  stays on GitHub.

The second point matters because it's easy to conflate "avoid GitHub for
storage" with "avoid GitHub Actions for compute" — they're unrelated. CI
compute (running tests, building an AppImage) never touches the storage/
bandwidth concern; only where the *finished binaries* end up does.

## Decision

**Package as core + optional `[gui]` extra.** `pip install msianalyzer` →
core/CLI only; `pip install msianalyzer[gui]` → adds `pyside6`. Verified by
grepping actual imports (not assumed) that `plotly`/`matplotlib`/`anndata`/
`libviz`/`tomli-w`/`kaleido` are used by core plotting/report code too —
`pyside6` is the only genuinely GUI-exclusive dependency.

**Standalone builds: PyInstaller → onedir → AppImage, for Linux now,
Windows later.** See [ADR 23](0023-pyinstaller-appimage-packaging.md) for
the packaging approach itself. Windows is deferred to a locally-run Windows
VM (QEMU/VirtualBox) the user sets up — not Wine cross-compilation (too
fragile for this dependency stack to trust without being able to test on
real Windows) and not a paid cloud Windows runner (no recurring cost for an
infrequent, manual build step).

**CI runs on GitHub Actions, for compute only.** Tests and (advisory) lint
on every push/PR to `dev`/`main`. Nothing about this touches the storage
concern above — CI artifacts stay ephemeral (build logs, test results), no
release binaries are uploaded anywhere by CI.

**Artifact hosting is explicitly deferred, not designed around.** The app
is private/personal-use for now — there is no distribution audience yet
that needs a download link. When that changes, two options were identified
and neither was built: the user's own NAS, or a dedicated "releases-only"
GitHub repo (keeps large binaries out of the main repo's git history while
still using GitHub's free Release hosting for that separate repo). Building
storage infrastructure for a need that doesn't exist yet would be pure
speculation.

## Alternatives considered

- **Move off GitHub entirely** (GitLab, self-hosted Forgejo/Gitea).
  Rejected — the actual concern was storage/bandwidth for binaries, not
  GitHub as a platform; moving the whole repo would solve a problem that
  doesn't exist while creating real migration cost.
- **Wine-based cross-compilation for Windows**, to avoid needing a Windows
  machine at all. Rejected — PySide6 + QtWebEngine + numpy/scipy under Wine
  is a known-fragile combination, and any resulting bug would be
  undebuggable without real Windows to compare against.
- **Rent Windows CI compute** (GitHub Actions `windows-latest`, or a cloud
  spot VM) instead of a local VM. Left open as a later automation step once
  a local Windows build is proven to work — not rejected outright, just not
  the first move.

## Consequences

- Publishing to PyPI is not set up (would need a public index and a
  decision on version-tag discipline) — not needed while the app is
  private; a local/editable install or a wheel copied to the NAS covers
  current use.
- No Windows build exists yet. `packaging/pyinstaller/msianalyzer-gui.spec`
  is Linux-only in its current form (ELF-specific dependency handling —
  see [ADR 23](0023-pyinstaller-appimage-packaging.md)); a Windows spec is
  new work, not a port.
- CI has no artifact-upload step for the AppImage — building it is still a
  manual, local `packaging/appimage/build.sh` run. Wiring a short-retention
  CI build artifact (not a permanent Release) was the plan but not built
  in this pass.
- `[tool.pylint]` has no tuned config yet, so CI lint is advisory
  (`|| true`), not a merge gate — see
  [distribution & packaging](../distribution.md#linting) for why.
