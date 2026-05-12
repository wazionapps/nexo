# NEXO Local Context Layer - Release Execution Progress

Started: 2026-05-12
Operator: Francisco
Executor: Nero

Source plan:

- `/Users/franciscoc/Documents/_PhpstormProjects/nexo/docs/specs/2026-05-12-local-context-layer-release-plan.md`

## Execution Rule

This release is not considered complete until Brain and Desktop both pass their gates and the release is actually published and verified.

Do not report "published", "released" or "done" from pre-release verification alone.

## Current Versions

- Brain current: `7.17.8`
- Desktop current: `0.32.64`
- Planned next Brain feature version: `7.18.0`
- Planned next Desktop feature version: `0.33.0`

## Initial Repo State

Brain:

- Worktree has the local context plan as an untracked spec file.
- No other Brain dirty files observed during this preflight.

Desktop:

- Worktree clean during this preflight.

## Decision

Chosen execution strategy:

- Implement in controlled steps with tracking and gates.
- Publish beta/stable only if the release gates pass.
- If a Windows/macOS/privacy/logging/release gate cannot be verified, block publication and record the exact blocker here.

## Live Status Log

### 2026-05-12 - Preflight

- Read project atlas before touching NEXO repos.
- Re-read the Local Context Layer release plan.
- Confirmed Brain `7.17.8` and Desktop `0.32.64`.
- Confirmed Desktop worktree clean.
- Confirmed Brain worktree has `docs/specs/2026-05-12-local-context-layer-release-plan.md` untracked.
- NEXO task tooling required use through CLI because the embedded MCP process is still on the previous runtime fingerprint.
- Guard learning acknowledged: distinguish pre-release verification from effective publication.

### 2026-05-12 - Brain Core Pass 1

- Added Brain schema migration `63` for Local Context Layer tables: roots, exclusions, jobs, checkpoints, logs, errors, assets, versions, chunks, entities, relations, embeddings and query audit rows.
- Added `src/local_context/` package with local-only scanner, lightweight extractors, deterministic local embeddings, graph relations, context query resolver, purge, diagnostics tail and status payload.
- Added pause/resume state and checkpointed partial scans so background cycles do not reprocess only the first files forever.
- Added `nexo_local_*` MCP tools in Brain server for Desktop/Brain integration. CLI wiring remains pending because `src/cli.py` is currently tracked by another active release session.
- Added `src/scripts/nexo-local-index.py` cooperative background cycle with file log `local-index.log` and DB diagnostic events.
- Registered `local-index` in `src/crons/manifest.json` at 60-second intervals with run-on-boot and run-on-wake.
- Expanded service config metadata for macOS LaunchAgent and Windows Scheduled Task, including start/status/stop/uninstall commands and support log path.
- Added Local Context model status/warmup contract: deterministic local fallback always available, pinned packaged models reported without forcing downloads.
- Integrated Local Context evidence into Brain pre-action context so agents can retrieve local evidence before answering/acting.
- Verified Brain local context tests: `pytest -q tests/test_local_context.py` -> `9 passed`.
- Verified pre-action integration tests: `pytest -q tests/test_local_context.py tests/test_local_context_pre_action.py tests/test_tool_compat_aliases.py::test_pre_action_context_accepts_intent_and_area_aliases` -> `11 passed`.
- Verified compile gate: `python3 -m compileall -q src/local_context src/scripts/nexo-local-index.py src/server.py src/db/_schema.py tests/test_local_context.py`.
- Verified service cycle dry run with default roots disabled: exited `0` and wrote a JSON result.
- Verified MCP exports by importing `server` and listing `nexo_local_*` functions.

### 2026-05-12 - Desktop Local Memory UI Pass 1

- Added Desktop IPC bridge methods for Local Context Layer via `nexo scripts call` so Desktop can use Brain MCP tools without waiting for `src/cli.py` edits.
- Exposed preload APIs: status, control, roots, exclusions, diagnostics tail, service config, models and purge.
- Added Preferences tab `Local memory` / `Memoria local` under Product for normal users.
- Added global progress, file counts, phase, background service state, pause/resume, force pass, clear index, included folders, excluded folders, disks, local models and skipped problems.
- Added support-mode-only live diagnostic log. When `app.support_mode` is off, the log is hidden.
- Added English/Spanish i18n keys and React settings contracts.
- Verified Desktop syntax: `npm run smoke:syntax`.
- Verified Desktop React build: `npm run build:react`.
- Verified Desktop local memory/settings contracts: `node --test tests/local-memory-settings.test.js tests/react-settings-contract.test.js tests/settings-support-surface.test.js` -> `25 passed`.

### 2026-05-12 - Desktop Enforcer Overlap Race Fix

- Added `lib/enforcer-turn-lifecycle.js` to separate silent enforcer routing from visible user turns that arrive while an enforcer reminder is still in flight.
- Updated `main.js` so `sendMessage()` marks the overlap before stream routing, assistant text after the overlap is not tagged as `_enforcerResponse`, and the silent enforcer completion does not send `claude-idle` for the visible turn.
- Updated permission feedback injection to clear stale overlap markers when it starts an internal enforcer-style turn.
- Added regression coverage in `tests/enforcer-silent-overlap.test.js` for the reported case: silent enforcer turn + overlapping user message must keep the visible answer routable.
- Added `lib/enforcer-turn-lifecycle.js` to Desktop `smoke:syntax`.
- Verified package JSON: `python3 -m json.tool package.json`.
- Verified syntax: `node --check main.js`, `node --check lib/enforcer-turn-lifecycle.js`, `node --check lib/permission-feedback-runtime.js`, and `npm run smoke:syntax`.
- Verified stream/enforcer regression suite: `node --test tests/enforcer-silent-overlap.test.js tests/enforcer-silent-contract.test.js tests/claude-stream-router.test.js tests/permission-feedback-runtime.test.js` -> `21 passed`.

### 2026-05-12 - Desktop Folder Permissions Pass

- Added native folder picker IPC/preload bridge for Local Memory include/exclude actions on macOS and Windows.
- Added Local Memory permission card that reads the existing system permissions registry and opens Full Disk Access when macOS protected folders may be skipped.
- Verified Desktop syntax: `node --check lib/brain-bridge-ipc.js && node --check main.js && node --check preload.js`.
- Verified i18n JSON: `python3 -m json.tool renderer/i18n/en.json` and `renderer/i18n/es.json`.
- Verified Local Memory/settings contracts: `node --test tests/local-memory-settings.test.js tests/react-settings-contract.test.js tests/settings-support-surface.test.js` -> `25 passed`.
- Verified React build: `npm run build:react`.

### 2026-05-12 - Brain CLI Exports Pass

- Added `nexo local-context ...` CLI commands for status, run-once, pause/resume, roots, exclusions, query, diagnostics, service config, model status/warmup and asset inspection/purge.
- Added CLI regression coverage in `tests/test_local_context_cli.py`.
- Verified compile gate: `python3 -m py_compile src/cli.py tests/test_local_context_cli.py`.
- Verified CLI/index/pre-action suite: `pytest -q tests/test_local_context_cli.py tests/test_local_context.py tests/test_local_context_pre_action.py` -> `12 passed`.
- Verified CLI service metadata command: `python3 src/cli.py local-context service-config --platform macos --json`.

### 2026-05-12 - Brain Release Surface Pass

- Bumped Brain package metadata to `7.18.0`.
- Added the `7.18.0` CHANGELOG entry and public blog page for Local Context Layer.
- Updated homepage, blog index, changelog page and sitemap markers so release-readiness can verify `v7.18.0`.
- Updated the changelog hero to point at `#v7180` and describe Local Context Layer as the current release.

### 2026-05-12 - Brain Release Verification and Publication

- Added all `nexo_local_*` tools to `tool-enforcement-map.json` and verified map sync: `python3 scripts/verify_tool_map.py` -> `297 tools`.
- Verified Brain release readiness: `python3 scripts/verify_release_readiness.py --ci` -> `216 passed`.
- Verified full Brain pre-release gate: `scripts/pre-release-verify.sh --release v7.18.0` -> `2531 passed, 2 skipped, 1 xfailed, 4 xpassed`; final summary `5 passed, 0 failed, 0 skipped`.
- Verified package content: `npm pack --dry-run` -> `nexo-brain-7.18.0.tgz`, package size `1.5 MB`, unpacked size `6.1 MB`, `488` files.
- Published Brain to npm: `npm publish --access public` -> `+ nexo-brain@7.18.0`.
- Verified public registry: `npm view nexo-brain version` -> `7.18.0`.
- Verified public tarball: `https://registry.npmjs.org/nexo-brain/-/nexo-brain-7.18.0.tgz`, integrity `sha512-HrCdBcMuCFiB4c9Yp/fQf7YHde3nnbObkDvm0DIzwx/XNVkL8aAiQShx9uMKbbtfmDKG5smwcfQMIl5jts4Rxg==`.

### 2026-05-12 - Desktop Wake Word Release Fix

- Changed packaged wake-word model URLs from `file://` to `nexo-voice://voice-runtime/vosk-model-small-es-0.42.tar.gz` while keeping `file://` for development.
- Verified the `nexo-voice://` privileged scheme maps packaged resources from `process.resourcesPath/voice-runtime/*` without disabling Electron `webSecurity`.
- Added persistent `voice-engine.log` under Desktop user data logs and exposed listener errors through runtime status.
- Added a non-technical Desktop Health warning when wake word is enabled/calibrated but the listener does not become ready.
- Verified focused wake-word/health suites: `node --test tests/voice-engine-runtime.test.js tests/voice-runtime.test.js tests/voice-preload-contract.test.js tests/product-health.test.js tests/product-health-ipc.test.js` -> `74 passed`.

### 2026-05-12 - Desktop Release Verification and Publication

- Bumped Desktop to `0.33.0` and bundled Brain `7.18.0`.
- Verified Brain bundle source: `npm run verify:brain-bundle-source` -> `Desktop 0.33.0 -> Brain 7.18.0`.
- Verified full Desktop check gate: `npm run check` -> lint completed with existing warnings, tests `1713 passed, 2 skipped`, syntax smoke OK, bundled hashes OK.
- Verified focused release suites: Local Memory UI, silent enforcer overlap, wake-word runtime, voice protocol and product health -> `106 passed`.
- Built unsigned release artifacts with `ALLOW_UNSIGNED=1 npm run dist:release` -> exit `0`.
- Verified macOS unsigned artifacts: `node scripts/verify-release-artifacts.js --dist dist/release --allow-unsigned true` -> unsigned QA verification passed for `2` DMG artifacts.
- Verified Windows x64 artifacts: `node scripts/verify-release-artifacts.js --platform win --dist dist/release-win/desktop --allow-unsigned true` -> validated `2` Windows EXE artifacts and `1` unpacked bundle.
- Published Desktop beta unsigned: `ALLOW_UNSIGNED=1 CHANNEL=beta npm run release:upload` -> `done - version=0.33.0 channel=beta`.
- Published Desktop stable unsigned: `ALLOW_UNSIGNED=1 CHANNEL=stable npm run release:upload` -> `done - version=0.33.0 channel=stable`.
- Verified stable public release matrix: `npm run verify:release:matrix:public` -> `ok: true`, version `0.33.0`.
- Verified beta public manifests: `update-beta-mac-arm64.json`, `update-beta-arm64.json`, `update-beta-mac-x64.json`, `update-beta-x64.json`, `update-beta.json`, `update-beta-win-x64.json`, `update-beta-win.json` -> all version `0.33.0`.
- Verified stable public manifests: `update-mac-arm64.json`, `update-arm64.json`, `update-mac-x64.json`, `update-x64.json`, `update.json`, `update-win-x64.json`, `update-win.json` -> all version `0.33.0`.
- Verified public binary redirects and sizes: Mac arm64 `2916803748`, Mac x64 `2932737995`, Windows setup `1745395897`, Windows portable `1706805970`, all HTTP `200` after redirect to GCS.
- Verified rollback inventory after cleanup: bucket preserves versions `0.31.4`, `0.32.63`, `0.32.64` and `0.33.0`.

## Implementation Checklist

- [x] Brain schema and local context package
- [x] Brain DB concurrency / durable queue
- [x] Brain scanner, watcher and reconciliation
- [x] Brain safe extractors and depth policy
- [x] Brain graph, embeddings and incremental invalidation
- [x] Brain local model profiles/status/warmup
- [x] Brain CLI/MCP exports
- [x] Brain service install/start/status/stop/uninstall for macOS and Windows
- [x] Brain pre-action/router integration
- [x] Brain fixtures and tests
- [x] Desktop bridge APIs
- [x] Desktop Settings tab `Memoria local` / `Local memory`
- [x] Desktop folder include/exclude controls
- [x] Desktop support-mode-only diagnostic log
- [x] Desktop permissions integration
- [x] Desktop silent enforcer overlap race fix
- [x] Desktop tests
- [x] Brain public release surface
- [x] Brain release verification
- [x] Desktop release verification
- [x] macOS artifact smoke
- [x] Windows x64 artifact smoke
- [x] beta manifests
- [x] stable manifests
- [x] rollback verification

## Blockers

- No publication blocker remains for Brain `7.18.0` or Desktop `0.33.0`.
- Manual installed-app smoke on a physical Windows machine remains the only check that cannot be executed from this macOS host.

## Resolved Release Blockers

- Desktop race condition fixed: silent Protocol Enforcer turn plus overlapping user message no longer routes the subsequent visible assistant response as `_enforcerResponse`.
- Regression coverage added for the overlap lifecycle and stream routing contract. Full DOM smoke remains part of Desktop release verification.
- Wake-word packaged asset loading fixed by serving Vosk model assets through `nexo-voice://` instead of `file://` in packaged builds.
- Desktop beta and stable are intentionally unsigned for this release flow; upload was performed with `ALLOW_UNSIGNED=1`.

## Active Continuation Checkpoint - 2026-05-12 19:40 Europe/Madrid

This section is the compact-safe handoff for the current continuation. If the chat compacts, resume from here before doing any release work.

Current public baseline before this continuation:

- Brain published baseline: `7.19.0`.
- Desktop published baseline: `0.33.2`.
- Both repos were clean at the start of this continuation.
- Target now: one coordinated Brain + Desktop release, beta and stable, with the live local memory fixes and the Desktop UI corrections below.

User-critical requirements for this continuation:

- The local memory processor must be live: created, modified and deleted files must be detected and reflected automatically, not only after a full scan finishes.
- Must work on macOS and Windows.
- UI must remain simple: excluded folders, current state, file counts/progress and basic controls only.
- Desktop must keep shipping the local LLM/model bundle so install/update does not require users to download large models during setup.
- `Limpiar índice` must require confirmation before deleting local index progress.
- `Forzar pasada` must be renamed/explained as a normal-user action, not technical wording.
- Preferences loading surface should use `Cargando...` as title and `Leyendo estado de memoria local...` as subtitle.
- `nexo chat` must not keep asking for Full Disk Access when it is already verified.

Brain changes currently implemented but not released:

- Added migration `64` with `local_index_dirs` to track known folders and detect live directory changes.
- Added live reconciliation before each index cycle:
  - known active files are checked for deletion/modification;
  - known active folders are checked for directory mtime changes;
  - changed folders are scanned in bounded batches to discover new files and prune deleted children;
  - exclusions now tombstone already-indexed files under excluded folders;
  - offline roots are not treated as mass deletion.
- `run_once()` now returns `live`, `scan`, and `jobs`.
- `src/scripts/nexo-local-index.py` now runs live reconciliation limits before scan/process.
- Added `nexo local-context reconcile --json`.
- `context_query()` now returns graph relations for matched assets, not only chunks/assets/entities.
- Fixed Full Disk Access prompt loop: if the probe verifies granted access, `ensure_full_disk_access_choice()` clears stale reasons/state and returns no visible message.

Brain verification already passed in this continuation:

- `python3 -m compileall -q src/local_context src/db/_schema.py src/cli.py src/runtime_power.py src/scripts/nexo-local-index.py` -> passed.
- `python3 -m pytest tests/test_local_context.py tests/test_local_context_cli.py tests/test_runtime_power.py -q` -> `39 passed`.
- `python3 src/cli.py local-context service-config --platform windows --json` -> rendered Windows Scheduled Task metadata.
- `python3 src/cli.py local-context service-config --platform macos --json` -> rendered macOS LaunchAgent metadata.

Desktop changes currently implemented but not released:

- Fixed React i18n interpolation to support both `{time}` and `{{time}}`, so "Última comprobación {{time}}" no longer renders braces.
- Local Memory tab now separates fast status polling from heavier auxiliary calls:
  - initial/full refresh loads status, service config, permissions, exclusions and support log;
  - recurring refresh polls only status and support log;
  - this avoids a slow auxiliary call making the UI look frozen.
- Loading for Local Memory now uses title `Cargando...` and subtitle `Leyendo estado de memoria local...`.
- `Limpiar índice` opens the existing React `ConfirmDialog` before calling `clear_index`.
- `Forzar pasada` fallback text changed to `Check changes now`; update i18n keys next so Spanish reads `Comprobar cambios ahora`.
- Manual `Actualizar` fallback changed to `Refresh status`; update i18n keys next so Spanish reads `Actualizar estado`.

Desktop changes still pending before release:

- Investigate any remaining Updates tab version correctness issue during installed-app smoke.
- Run focused Desktop tests/build:
  - `node --test tests/local-memory-settings.test.js tests/i18n-tn-plural.test.js`
  - `npm run build:react`
  - any update-status focused tests if Updates tab is modified.

Desktop verification after UI continuation:

- Updated Local Memory i18n:
  - `force`: `Comprobar cambios ahora` / `Check changes now`.
  - `refresh`: `Actualizar estado` / `Refresh status`.
  - added clear-index confirmation title/body/hint/ok keys.
- Added/adjusted Desktop tests for:
  - `ConfirmDialog` guarding `clear_index`;
  - fast status polling vs full auxiliary refresh;
  - i18n interpolation for both `{time}` and `{{time}}`;
  - Updates tab non-blocking local-first load.
- Changed Updates tab to render local versions first and run remote Brain/Desktop update checks in the background.
- Verified Desktop i18n JSON:
  - `python3 -m json.tool renderer/i18n/es.json >/dev/null`
  - `python3 -m json.tool renderer/i18n/en.json >/dev/null`
- Verified focused Desktop suite:
  - `node --test tests/local-memory-settings.test.js tests/i18n-tn-plural.test.js tests/react-settings-contract.test.js tests/update-orchestrator-brain.test.js` -> `28 passed`.
- Verified React build:
  - `npm run build:react` -> passed.

Release still pending:

- Brain version bump for this continuation, likely `7.20.0` if treated as feature-level live reconciliation, or `7.19.1` if treated as hardening. Prefer `7.20.0` because DB migration `64` changes behavior and CLI surface.
- Desktop version bump after Brain publish/bundle, likely `0.33.3`.
- Desktop must bundle the new Brain version and preserve packaged local model artifacts.
- Publish both beta and stable unsigned as requested by Francisco.
- Verify public manifests for beta/stable and verify Desktop points to the new Brain bundle.

## 2026-05-12 20:46 CEST - Release closure evidence

Brain release:

- Version bumped to `7.20.0`.
- `npm publish --access public` completed successfully.
- Public npm verification:
  - `npm view nexo-brain version` -> `7.20.0`.
  - `npm view nexo-brain dist-tags --json` -> `{"beta":"0.10.0-beta.8","latest":"7.20.0"}`.

Brain verification:

- `python3 -m compileall -q src/local_context src/db/_schema.py src/cli.py src/runtime_power.py src/scripts/nexo-local-index.py` passed.
- `python3 -m pytest tests/test_local_context.py tests/test_local_context_cli.py tests/test_runtime_power.py -q` -> `39 passed`.
- Full pre-release pytest run before public-surface fixes -> `2546 passed, 2 skipped, 1 xfailed, 4 xpassed`.
- `python3 scripts/sync_release_artifacts.py --release-version 7.20.0 && python3 scripts/verify_release_readiness.py --ci` -> `216 passed`.
- `scripts/pre-release-verify.sh --release v7.20.0 --skip pytest` -> `4 passed, 0 failed, 1 skipped`.

Desktop release:

- Version bumped to `0.33.3`.
- `npm run verify:brain-bundle-source` -> `ok Desktop 0.33.3 -> Brain 7.20.0`.
- `ALLOW_UNSIGNED=1 npm run dist:release` completed successfully.
- Generated release artifacts:
  - `dist/release/NEXO Desktop-0.33.3-arm64.dmg`.
  - `dist/release/NEXO Desktop-0.33.3-x64.dmg`.
  - `dist/release-win/desktop/NEXO Desktop Setup 0.33.3.exe`.
  - `dist/release-win/desktop/NEXO Desktop 0.33.3.exe`.
- `ALLOW_UNSIGNED=1 CHANNEL=beta npm run release:upload` completed successfully.
- `ALLOW_UNSIGNED=1 CHANNEL=stable npm run release:upload` completed successfully.

Desktop verification:

- `npm run check` -> `1716 pass / 2 skipped / 0 fail` plus lint warnings only.
- `npm run verify:llm-bundle` -> `ok (4 models, 16 files, 801 MB)`.
- `npm run verify:llm-runtime-bundle` -> `ok (3 platforms, 72 native libs)`.
- `npm run verify:voice-bundle` -> `ok (1 models, 38 MB)`.
- `node scripts/verify-release-artifacts.js --allow-unsigned true --dist dist/release` passed for macOS arm64/x64 DMGs.
- `node scripts/verify-release-artifacts.js --platform win --allow-unsigned true --dist dist/release-win/desktop` passed for Windows x64 installer/portable.
- `npm run verify:release:matrix:public` passed for stable public manifests.
- Direct public manifest checks returned `0.33.3` for:
  - `update-beta-mac-arm64.json`.
  - `update-beta-mac-x64.json`.
  - `update-beta-win-x64.json`.
  - `update-mac-arm64.json`.
  - `update-mac-x64.json`.
  - `update-win-x64.json`.

Remaining operational note:

- Commit/tag/push still need to be completed so repositories match the already-published npm/Desktop artifacts.
