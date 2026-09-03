# Testing

Tests live under `tests/core/unit/` and run with `pytest`. The suite is fast
(no real instrument data) and green on every commit.

```bash
source .venv/bin/activate
python -m pytest tests/core -q      # core
python -m pytest tests -q           # everything (cli + gui too)
```

## Approach: design-first, then TDD

New features start from test *names* that state the edge cases, then a mock-data
builder, then the assertions, then the implementation. The MS2 grouper was built
this way — see `tests/core/unit/test_ms2_grouper.py`.

## Test layering

| layer | example | mock data |
|---|---|---|
| pure logic | `associate_scan`, `align_mz_across_samples`, `detect_ms1_centroids` | plain floats / arrays, no factory |
| DB integration | `persist_grouping`, `save_features`, `run_grouper` | temp SQLite built through the real schema builders |
| orchestration | `run_core`, `_process_one_sample` | collaborators patched at their import site (`mocker.patch("msianalyzer.core.run.run.<name>")`) |

Only the middle layer needs a factory. Fixtures build their databases through
`create_raw_schema` / `create_analysis_schema`
([ADR 6](adr/0006-schema-single-source-of-truth.md)) so they cannot drift.

## The grouper mock-data factory

`tests/core/unit/conftest.py` exposes `build_ms2_grouper_mock_data(...)` (as the
factory fixture `ms2_grouper_mock_data`). One call builds ≥ 1000 MS2 scan dicts
plus a master m/z list, and **plants five edge cases** with their expected
answers in `.planted`:

| key | exercises |
|---|---|
| `single` | precursor ~3 ppm from exactly one feature; window holds only it |
| `none` | precursor in a feature-free gap; matches nothing |
| `chimeric` | three features 0.15 Da apart in one isolation window |
| `precursor_only` | fragment spectrum is just the surviving precursor |
| `null_precursor` | `precursor_mz` is null → falls back to the isolation target |

Each `PlantedCase` carries `expected_feature_mz`, `expected_ppm_offset`,
`expected_match_key`, `expected_precursor_only`,
`ppm_diff_per_window_feature`, `n_in_window`, etc., so a test just runs the
grouper and compares. Background features are spaced wider than any isolation
window, so a random scan is unambiguous and the planted cases are the only
interesting ones. Planted cases are deterministic regardless of `seed`.

Companion helpers: `materialize_ms2_db(path, mock)` / the `make_ms2_db` fixture
write a mock batch into a real raw-schema SQLite DB, for testing `run_grouper`
end-to-end.

## Conventions

- Patch collaborators where they are *used*, not where they are defined.
- Assert on behaviour and row counts, not on log output.
- A DB integration test creates its database through `init_*` / the schema
  builder, never with hand-written DDL.
