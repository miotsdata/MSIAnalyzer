# 61 — Normalize to a feature after the run

**Status:** Accepted

## Context

The run can normalize to a reference m/z (`normalization.mode =
"reference_mz"`), but only when that is set in the config before the run.
A user who wants an untargeted analysis normalized to one feature (an
internal standard, a matrix peak) after the run had to re-run it.

## Decision

- **Analyses → Normalize to feature…** (`NormalizeFeatureDialog.qml`)
  uses the same name/m/z search as Visual Inspection's feature picker
  (`SearchQuery.js`, `requestFeatureList`). The user picks one feature and
  clicks **Normalize**.
- **Same math as the run** (`tic_normalization._normalize`, shared with
  `_add_normalized_layers`): `log1p(raw / (ref / median(ref)))`. The
  median is taken over every pixel of every sample, and a pixel with no
  reference signal is 0. `raw` is `.X`, which the pipeline never
  normalizes in place.
- **A new layer, nothing replaced:** `layers["feature_<id>"]` plus
  `obs["norm_scale_feature_<id>"]`, in every sample file and in
  `merged.h5ad` (when it exists). `.X`, `raw`, the run's normalized layer
  and `obs["norm_scale"]` are not touched. The layer is named by feature
  id rather than m/z: the id is unique and doesn't depend on rounding.
- **Element-level writes** (ADR 50): only `layers/<name>` and the one obs
  column are written, never the whole file. The samples are written in a
  process pool. The merged layer is stacked from the sample layers in
  `merged.obs["sample"]` row order.
- **Background:** `AnalysisBridge.normalizeToFeature` runs on the shared
  `DatasetTaskWorker`, so it can't overlap an import or a Moran's I run.
  Progress arrives through `datasetTaskProgress`. On success, the heatmap
  provider's caches are dropped and `featureLayersChanged(db)` fires.
- **Provenance:** a `commands` row `normalize_feature` with
  `{feature_id, mz, layer}`. The GUI lists layers from the file
  (`getFeatureLayers`), not from this row.
- **Running it again** for the same feature replaces that layer. The
  dialog then labels the button **Replace layer** and warns first.
- **Visual Inspection:** a "Normalized to feature…" combo next to the
  Raw / TIC / Local Moran's I buttons lists these layers. It is shown on
  the pipeline output only.
- The result message counts the pixels where the reference is 0, since
  those pixels are 0 in the new layer.

## Consequences

- No config version bump and no migration: old analyses just gain a layer.
- Not covered: imported datasets, the new layer as an Integration export
  option or as the Moran's I source layer.
- If the layer on screen is replaced, the visible tiles keep the old image
  until the layer or feature is changed: the tile URL is the same.

## Addendum: removing a layer

The dialog lists the existing `feature_<id>` layers. Each has a ✕ button
that asks for confirmation inline. **Remove**
(`tic_normalization.remove_feature_layer`, `AnalysisBridge.
removeFeatureLayer`) deletes `layers/feature_<id>` and
`obs/norm_scale_feature_<id>` from every sample file and `merged.h5ad`,
and logs a `remove_feature_layer` command.

- It runs synchronously, since deleting two elements per file is fast. It
  is refused while a dataset task is running, which could be writing the
  same files.
- If Visual Inspection was showing the removed layer, it falls back to
  the run's normalized layer.
- HDF5 doesn't reclaim the space of a deleted element, so the files keep
  their size until they are rewritten (for example with `h5repack`).
