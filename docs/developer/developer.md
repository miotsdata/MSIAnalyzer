# Developer documentation

Design and internals of `msianalyzer.core`. If you use the pipeline rather than
change it, start with the [user guide](../user-guide/index.md).

## Sections

- **[Architecture](architecture/index.md)** — module map, data flow, the
  two-database model.
    - [Core modules](architecture/core-modules.md) — per-module responsibility
      and public surface.
    - [Database schemas](architecture/databases.md) — every table in the raw and
      analysis databases.
    - [Provenance & caching](architecture/provenance.md) — the `commands` table,
      `run_id` vs `analysis_id`, how steps are skipped.
- **[Decision records](adr/index.md)** — the *why* behind the structure.
- **[Testing](testing.md)** — TDD approach, the mock-data factory, test layering.
- **[Logging](logging.md)** — handler setup, INFO vs DEBUG, log format.
- **[API reference](api_reference/index.md)** — generated from docstrings.

## Ground rules

1. **Raw DB = objective facts; analysis DB = parameter-dependent results.**
   Nothing downstream writes to a raw database.
   ([ADR 1](adr/0001-raw-vs-analysis-db-split.md))
2. **One schema definition per database.** `create_raw_schema` and
   `create_analysis_schema` are the single sources of truth; tests build
   fixtures through them.
   ([ADR 6](adr/0006-schema-single-source-of-truth.md))
3. **Every step logs a `commands` row** with its full JSON arguments, so an
   analysis database fully describes how it was built.
4. **Pure core, thin IO.** Association/scoring logic operates on plain
   arrays/dicts and is unit-tested without a database; a thin layer reads raw
   DBs and writes the analysis DB.
5. **Google-style docstrings**, one `logging.getLogger(__name__)` per module,
   at least one log call per function.
