# Decision records

Architecture Decision Records capture a choice, the context that forced it, and
the consequences we accepted. They are immutable once accepted: a later reversal
gets its own record that supersedes the old one.

Format: **Status · Context · Decision · Consequences** (plus *Alternatives* where
useful).

| # | Title | Status |
|---|---|---|
| [1](0001-raw-vs-analysis-db-split.md) | Raw vs analysis database split | Accepted |
| [2](0002-ms2-feature-association-design.md) | MS2→feature association design | Accepted |
| [3](0003-no-min-peaks-filter-flag-instead.md) | No min-peaks filter for MS2 — flag instead | Accepted (supersedes an earlier draft) |
| [4](0004-two-table-association-storage.md) | Two-table storage for MS2 associations | Accepted |
| [5](0005-three-ppm-tolerances.md) | Three separate ppm tolerances | Accepted |
| [6](0006-schema-single-source-of-truth.md) | One schema builder per database | Accepted |
| [7](0007-library-annotation-design.md) | Library annotation design (Stage B) | Accepted |
| [8](0008-precursor-ion-purity.md) | Precursor ion purity (Stage A′) | Accepted |
| [9](0009-consume-purity-and-consensus.md) | Consume purity: annotation filter + per-feature consensus | Accepted |
| [10](0010-score-weights-and-flat-fragmentation.md) | Configurable score weights + flat-fragmentation flag | Accepted |
