# Guardian Runtime Surfaces

`guardian-runtime-surfaces.json` is the canonical Desktop-facing snapshot for
Guardian datasets that Brain already knows at runtime.

## Purpose

Desktop's JS enforcement engine cannot query Brain's SQLite entity registry
directly. This snapshot lets Desktop consume the same live datasets Brain uses
for rules such as R15, R19, R21, R23, R23b, R23c, R23f, R23l, and R25 without
falling back to parallel manual lists when Brain already has the source of
truth.

## Path

Canonical path after F0.6:

`~/.nexo/personal/brain/guardian-runtime-surfaces.json`

Legacy-compatible read path:

`~/.nexo/brain/guardian-runtime-surfaces.json`

## Writer

Brain exports the snapshot from `src/guardian_runtime_surfaces.py`.

Current write point:

- `client_sync.sync_all_clients()`

That means any update / packaged flow that already runs shared client sync also
refreshes the snapshot.

## Resolution policy

Brain builds the snapshot from:

1. live `db.list_entities()` when available
2. `brain/presets/entities_universal.json` as a fallback when the DB is still empty

Desktop consumes the snapshot first. Only if the snapshot is absent or empty
does Desktop fall back to the preset / compatibility paths.

## Current fields

- `schema`
- `generated_at`
- `source`
- `entity_count`
- `entities`
- `known_hosts`
- `read_only_hosts`
- `destructive_patterns`
- `projects`
- `legacy_mappings`
- `vhost_mappings`
- `db_production_markers`
- `all_entities_flat`

## Intended consumers

- Desktop `enforcement-engine.js`
- future Desktop support/debug surfaces that need to explain why a Guardian rule fired
- future parity audits comparing Brain and Desktop rule datasets

Companion matrix:

- `docs/guardian-brain-desktop-parity.md`
- `docs/guardian-hooks-classification.md`

## Non-goals

- This is not a general public export API.
- This does not replace the DB as Brain's source of truth.
- This should not carry operator-private data beyond what is already present in
  the local runtime entity registry / merged local preset.
