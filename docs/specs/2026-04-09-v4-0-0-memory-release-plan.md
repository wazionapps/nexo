# NEXO 4.0.0 Memory + Release Plan

**Date:** 2026-04-09
**Status:** In progress
**Release target:** `4.0.0`
**Execution mode:** Continuous implementation without avoidable pauses

## Goal

Ship a `4.0.0` release that materially upgrades NEXO's memory model and public product surface in one cohesive pass, not as a thin feature drop followed by an immediate cleanup patch.

This release must:

- implement the seven requested memory/system fronts
- include all merged but unreleased changes after `v3.2.0`
- pass repo tests and release-readiness checks
- update public release artifacts and website copy
- review and close related issues when the implementation really satisfies them

## Quality Bar

This release is explicitly **not** allowed to end with "left pending", "could be done later", or obvious related bugs that should have been handled while the relevant code was open.

Working rule for `4.0.0`:

- if implementation exposes an adjacent bug or obvious weak spot in the same surface, fix it before release
- prefer cohesive closure over artificial scope purity
- avoid shipping a `4.0.1` within 24 hours for predictable reasons

## Included Unreleased Changes

These commits are already merged to `main` and must be part of `4.0.0`:

- `f5ffe20` Protect live repo from automation writes (`#79`)
- `cdc7842` Enrich NEXO tool explanations (`#81`)
- `b332daf` Resolve Deep Sleep collect import path (`#82`)

## Release Surfaces Affected

- repo code under `src/`
- tests under `tests/`
- public release metadata:
  - `package.json`
  - `CHANGELOG.md`
  - release-facing integration artifacts synced by `scripts/sync_release_artifacts.py`
- public website worktree:
  - `../nexo-gh-pages`

## Seven Fronts

### 1. Multimodal Memory

Target:

- first-class reference layer for non-text artifacts
- images/audio/files can be stored as memory objects with metadata, links, and continuity value

Expected scope:

- lightweight media-reference memory layer first
- future-compatible with richer multimodal retrieval later

### 2. Auto-Flush Pre-Compaction

Target:

- before context compression, persist the critical session residue automatically
- reduce dependence on perfect diary discipline

Expected scope:

- structured pre-compaction capture
- inspectable flushed output
- safe behavior when optional subsystems are missing

### 3. Knowledge Wiki / Claims Layer

Target:

- turn existing claim graph pieces into a coherent public surface
- include provenance, verification state, freshness, contradiction handling, and reviewability

Expected scope:

- public tools
- linting/audit path
- dashboard/readable surface improvements where useful

### 4. Inspectability

Target:

- export readable memory snapshots for human audit
- make more of the memory system legible without raw DB inspection

Expected scope:

- markdown export
- include new memory layers where possible

### 5. Search Knobs

Target:

- expose more of the existing retrieval strategy deliberately
- allow operators/agents to choose narrow vs wide retrieval more explicitly

Expected scope:

- public API parameters over existing internals
- no regression to default search behavior

### 6. Interchangeable Backends

Target:

- formalize backend selection boundaries instead of leaving SQLite coupling implicit everywhere

Expected scope:

- introduce an explicit backend contract/registry
- preserve SQLite as default production backend
- avoid destabilizing existing runtime behavior

### 7. User Modeling + Multi-Agent Awareness

Target:

- upgrade from shallow sentiment to richer user-state inference
- make session/agent coordination more explicit and inspectable

Expected scope:

- richer user-state model from trust/corrections/context history
- keep the result inspectable, not opaque

## Issue Map

Directly related and expected to be closed if fully satisfied:

- `#78` Multimodal ingest/reference layer
- `#75` Stronger user-state model

Related and should be reviewed at the end:

- `#71` Richer episodic layer above diary/checkpoints/hot context
- `#76` More implicit KG relations from memory/change evidence
- `#77` Deep Sleep synthesis stability/explainability

If implementation does not fully satisfy an issue, do not close it prematurely.

## Constraints

- another active session may touch parts of core simultaneously
- avoid file collisions through NEXO coordination and tracked-file discipline
- do not rely on prompt memory for release integrity; keep checkpoint/state externalized
- keep public client capability claims honest

## Execution Phases

### Phase 0. Coordination

- detect active sessions and tracked files
- avoid overlap with concurrent core work
- proceed immediately on non-overlapping release surfaces

### Phase 1. New Memory Surfaces

- multimodal memory layer
- claims/wiki public surface
- inspectability/export
- user-state model

### Phase 2. Retrieval + Compaction

- public search knobs
- auto-flush pre-compaction
- validate that compaction preserves more useful context than before

### Phase 3. Backend Formalization

- introduce backend contract and default backend binding
- keep current behavior stable under SQLite

### Phase 4. Tests + Hardening

- add focused tests per new surface
- run targeted suites during development
- run broader suite before release
- fix adjacent regressions found during this phase

### Phase 5. Public Release Surfaces

- bump to `4.0.0`
- update changelog
- sync release-facing artifacts
- update website latest-version copy/changelog/blog/docs where needed

### Phase 6. Publish

- create branch
- commit cohesive release work
- push and open PR
- merge
- tag `v4.0.0`
- publish release workflow
- verify website/release/tag state

## Verification Requirements

Must pass before tag:

- targeted new tests for each new memory surface
- `pytest tests/ -v`
- `python3 scripts/verify_client_parity.py`
- `python3 scripts/sync_release_artifacts.py --check`
- `python3 scripts/verify_release_readiness.py`

Must verify after tag:

- GitHub release exists for `v4.0.0`
- website latest version shows `4.0.0`
- website changelog includes `v4.0.0`
- related issues closed only when the implementation truly covers them

## Release Narrative

`4.0.0` should read as a coherent memory-system upgrade:

- NEXO can now preserve more context before compression
- reason over claims more explicitly
- remember non-text artifacts
- expose richer retrieval/user-state controls
- export readable memory snapshots
- formalize backend boundaries

## Definition Of Done

- all seven fronts shipped in real runtime surfaces, not only in docs
- merged-but-unreleased work since `v3.2.0` included
- tests and release-readiness pass
- public release published
- website updated to `4.0.0`
- related issues reviewed and closed where appropriate
- no obvious "we already know this is broken/incomplete" leftovers in touched areas
