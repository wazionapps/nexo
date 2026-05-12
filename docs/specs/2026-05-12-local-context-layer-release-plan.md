# NEXO Local Context Layer - Brain + Desktop release plan

Created: 2026-05-12
Owner: Brain owns runtime truth; Desktop owns managed visibility/control.
Execution trigger: when Francisco says "adelante", re-read this file, inspect current repo state, then implement end-to-end.

## Objective

Ship one Brain release and one Desktop release that make NEXO automatically build and use a local memory/index of the user's machine.

The user should not need to know this exists. The system should work in the background after the required OS permissions are granted once. The Settings UI is only for observability and advanced control.

Product promise:

> NEXO remembers and connects what is on your computer so Nero can answer and act with local context, without uploading your life to the cloud.

## Target releases

Current versions at plan creation:

- Brain: `7.17.7`
- Desktop: `0.32.63`

Current versions observed during plan hardening:

- Brain: `7.17.8`
- Desktop: `0.32.64`

Default target if versions are still current when executed:

- Brain: `7.18.0`
- Desktop: `0.33.0`

If either repo has moved, pick the next minor/feature version at execution time and update this document only if the target version changes.

Release preflight must read live versions from Brain `package.json` and Desktop `package.json`. Do not implement or publish from stale version assumptions.

## Non-negotiables

- Automatic by default for normal users, businesses and teams.
- Runs even if NEXO Desktop is closed.
- Resumes after reboot.
- Uses checkpoints and incremental processing.
- Does not reprocess unchanged files.
- Does not block user work; backs off under load/battery/thermal pressure.
- Stores local context locally.
- Provides a Settings status panel, but does not require the user to manage it.
- Brain is the source of truth. Desktop consumes Brain APIs and shows product UI.
- Windows and macOS are release-blocking platforms. Do not treat either one as a later port.
- No broad "hide everything" policy. Prefer processing depth levels over binary exclusions.
- All user-facing Desktop wording must be non-technical.
- No partial core release: if a core phase in this document is not functional, do not publish the release as complete.
- Photo/video visual recognition is out of scope for this release. File/photo/video inventory and metadata are allowed; face/object/video understanding is not part of the first release contract.

## Closed first-release scope

This release is not a placeholder or "phase 1 now, real system later" release. If Francisco says "plan adelante", the implementation target is a complete end-to-end Local Context Layer for the supported asset types.

In scope and must work before release:

- resident background service
- restart/resume/checkpoints
- Phase 1 quick index
- Phase 2 light extraction for supported text/code/document/email metadata types
- Phase 3 smart extraction for selected text/email/code/document candidates
- Phase 4 graph relations
- Phase 5 local embeddings and retrieval
- pre-action context pack API
- Desktop Settings status/control surface
- Desktop bundled model artifact flow where required by Brain manifest

Out of scope for this release:

- face recognition in photos
- object recognition in photos
- video transcription
- video scene understanding
- "show every photo of person X" based only on face matching

Those visual capabilities can become a later explicit feature, but they must not be used to justify leaving the core index/extraction/graph/embedding system incomplete now.

## Correct mental model

This is not a file search product and not a user-facing question panel.

It is a local context substrate used before Nero answers or acts:

```text
User request / email reply / code task / automation
  -> pre-action context resolver
  -> local index + observations + graph + embeddings
  -> evidence-backed context pack
  -> Nero answers or acts with better context
```

The Settings panel exists so a curious or advanced user can see what is happening:

```text
Preferences -> Indexacion local
  -> explanation
  -> global progress
  -> per-disk status
  -> recent changes
  -> skipped/problem files
  -> force reindex / pause / resume
```

## Processing policy

Use depth levels instead of blunt exclusions.

| Depth | Meaning | Default examples |
|---|---|---|
| 0 | Do not touch | OS-protected paths, explicit user exclusions, permission denied |
| 1 | Inventory only | huge dependency/build/cache trees, system-like folders, unknown binaries |
| 2 | Light extraction | documents, emails, source files, markdown, spreadsheets, presentations, photos metadata |
| 3 | Smart extraction | candidate docs/emails/code/images selected by recency, salience, user requests, project relation |
| 4 | Embeddings + graph | useful chunks, summaries, entities, relations, project/email/document/photo links |

Important: `node_modules`, `vendor`, `dist`, `build`, cache folders and similar noisy trees are not invisible. They are normally inventory-only unless the user or resolver asks for deeper processing. That lets a programmer ask "what connects with X?" without turning dependency folders into semantic noise.

## Incremental change detection

The index is not a one-time scan. After the first full pass, Brain must keep the local context fresh with a hybrid strategy:

- OS file watchers when available and reliable for the selected roots.
- Periodic reconciliation scans for missed events, offline periods, external disks and watcher failures.
- Timestamp and size checks as the cheap first comparison.
- Content hashes only when needed to confirm meaningful changes.
- A durable job queue for new, modified, moved and deleted assets.

Required behavior:

- New file: create or update the asset row, enqueue the correct phases for its depth level, then link extracted chunks/entities/relations.
- Modified file: compare against the previous asset version; re-run only the affected extraction, graph and embedding work.
- Moved file: preserve identity when hash/version evidence is strong enough, update path metadata and avoid duplicate semantic records.
- Deleted file: mark a tombstone/deleted state instead of hard-deleting immediately, deactivate active relations and keep minimal evidence for historical context.
- Permission lost: mark the asset/root as inaccessible, keep the previous safe metadata, and retry according to bounded backoff.
- External disk unavailable: mark the root as offline, do not treat every file as deleted, and reconcile when the disk returns.

Desktop should show this as simple language: "updating changes", "waiting for disk", "needs permission" or "paused", not as internal queue terminology.

Watcher backend contract:

- macOS: use FSEvents or the safest available native watcher for broad roots.
- Windows: use `ReadDirectoryChangesW`, USN Journal or an equivalent native watcher where safe; fall back to polling when unavailable.
- Watcher overflow must mark the affected root as `needs_reconcile`.
- Duplicate events must be coalesced before creating expensive work.
- Renames and moves should preserve asset identity when file id/hash evidence is strong enough.
- Case-only renames and Unicode normalization differences must be handled explicitly.
- Symlink/junction/alias loops must be detected and stopped.
- Polling fallback must be bounded and incremental, not a full semantic reprocess.

## Windows and macOS release blockers

Windows and macOS must both work before this release is considered complete.

macOS requirements:

- User-level LaunchAgent starts at login and survives Desktop being closed.
- Full Disk Access state is detected and explained clearly when needed.
- FSEvents or equivalent watcher path is used when available, with periodic reconciliation as fallback.
- iCloud placeholders, external drives, symlinks, aliases, package folders and permission-denied folders do not break the scanner.
- Paths preserve Unicode, case behavior and user-visible names correctly.

Windows requirements:

- A per-user scheduled task or signed service starts at login and survives Desktop being closed.
- The implementation explicitly handles the current Brain-on-Windows runtime boundary instead of assuming POSIX paths automatically cover Windows user files.
- NTFS permissions, locked files, Defender/AV delays, OneDrive placeholders, junctions/reparse points, long paths, drive letters and external drives do not break the scanner.
- Windows paths shown in Desktop use normal user-facing paths such as `C:\Users\...`, not internal bridge paths.
- If WSL is involved, path translation, availability and performance are tested as first-class release gates.

Release cannot pass with only macOS green or only Windows green.

## Hard failure modes to handle

The implementation must explicitly handle these cases:

- app or machine restart during any phase
- DB locked or interrupted transaction
- partially written checkpoints
- duplicated watcher events
- missed watcher events
- file modified while being read
- file deleted while being queued or processed
- folder moved while children are queued
- symlink/junction loops
- huge files and huge folders
- unreadable, encrypted, corrupt or password-protected files
- files with no extension or misleading extension
- cloud placeholder files that should not be force-downloaded silently
- external disk removed mid-scan and later reattached
- low disk space for index/embeddings
- battery saver, thermal pressure and high CPU activity
- model unavailable, model hash mismatch or model warmup failure

Every failure must end in one of these states: retry later, skipped with reason, waiting for permission, waiting for disk, degraded but usable, or fatal setup issue with a clear repair action.

## Performance budgets

Performance must be measurable before release.

Required controls:

- configurable CPU, memory and IO budgets
- lower priority while the user is active
- battery saver / thermal backoff
- timeout per extractor
- maximum bytes/pages/rows per extraction pass
- batch size limits for embeddings and graph writes
- queue priority: current project and recent/user-referenced assets first, broad backfill later
- disk-space guard before writing chunks, embeddings or backups

Required scale tests:

- small fixture corpus for normal CI
- synthetic 100k file inventory test
- synthetic 1M file planning/estimation test, allowed as slower/manual if CI cost is too high
- large single-file tests for CSV/log/PDF/DOCX behavior

The UI should report "still working in the background" or "slowed down to avoid affecting your computer" instead of exposing raw scheduler terms.

## Privacy, consent and data boundaries

This feature handles private local data. Privacy is a release blocker, not a documentation afterthought.

First activation:

- The feature may be enabled automatically after install/update only if the user-facing product flow has already granted the required OS permission and explains what NEXO will do in plain language.
- Desktop must provide a clear first-run/permission explanation: what folders are considered, what kinds of data are read, what stays local, how to pause and how to clear the index.
- Businesses/teams must be able to disable or restrict local indexing through settings/policy without editing files manually.

Local/cloud boundary:

- Local context indexing, extraction, embeddings, graph, query, diagnostics and support logs must not require external APIs.
- By default, local chunks, emails, paths, summaries, entities, embeddings and context packs must not be sent to remote model/API providers.
- If a future feature needs remote processing, it must use an explicit policy mode: `local_only`, `redacted_metadata_only`, or `explicit_user_approved_content`.
- The first release should default to `local_only`.

Sensitive data policy:

- Secrets and credentials are depth 0 or inventory-only by default: `.env`, SSH keys, API tokens, cookies, browser profiles, password manager vaults, keychains, certificates and private keys.
- Potentially sensitive bulk data should be inventory-only by default unless explicitly selected or requested by resolver policy: database dumps, payroll, legal/health folders, tax records and contract archives.
- The system should store enough metadata to answer "this exists / permission needed / omitted for privacy", without extracting sensitive content blindly.

Storage protection:

- Brain runtime directories for index DB, vector store, queues, backups, WAL/temp files and diagnostics must use private per-user permissions.
- macOS should use Keychain-backed secrets where encryption keys are needed.
- Windows should use DPAPI or an equivalent per-user protected store where encryption keys are needed.
- Multi-user machines must keep indexes separated per OS user and per NEXO workspace/account where applicable.
- Never scan another OS user's profile from a service running with elevated privileges.
- Windows `LocalSystem` service mode is out of scope unless a signed helper and user isolation are explicitly implemented and tested.

Retention and purge:

- Deleted assets can keep tombstones for consistency, but users must also have a real purge path.
- `purge_asset` / clear-index flows must remove chunks, summaries, entities, relations, embeddings, vector records, query caches, previews, errors and derived backups where safely possible.
- `local_context_queries` and `current_context` payloads should not persist raw local content by default.
- Any persisted query/debug history must have TTL and a no-history mode.

Diagnostic privacy:

- Support bundles and live logs should contain counters, versions, state, sanitized paths, hashes, error codes and timings.
- Support diagnostics must not include extracted text, email bodies, document snippets, embedding vectors, secrets or raw private content.
- The user should be able to preview/export diagnostics locally before sharing.

## Brain release scope

Brain owns:

- persistent local index database schema
- resident background index service
- cross-platform service install/start/status commands
- scanner, checkpoints and job queue
- extractors and depth policy
- embeddings and local semantic search
- relation graph updates
- pre-action context API
- MCP/tool exports for agents and Desktop
- tests and release readiness gates

### Brain data model

Add migrations in Brain source, not runtime install. Suggested tables:

- `local_index_roots`
- `local_index_settings`
- `local_index_jobs`
- `local_index_checkpoints`
- `local_index_errors`
- `local_assets`
- `local_asset_versions`
- `local_chunks`
- `local_entities`
- `local_relations`
- `local_embeddings`
- `local_context_queries`

Minimum fields:

- asset id, path, volume id, parent path, file type, extension
- size, created/modified/accessed times, inode/file id when available
- hash strategy: quick fingerprint first, full hash when needed
- depth level, phase, status, last indexed, last error
- source type: file, email, attachment, conversation, project, photo, code
- privacy class and permission state
- evidence refs for every context pack result

DB and concurrency contract:

- Use a single-writer design for local index writes. Either keep local context in a dedicated DB/vector store with a controlled writer, or enforce leases around shared SQLite writes.
- Durable jobs must include `status`, `priority`, `claimed_by`, `lease_expires_at`, `attempt_count`, `next_attempt_at` and `last_error_code`.
- Readers from Brain/MCP/Desktop must not block long-running indexing writes.
- Use WAL mode and planned checkpointing where SQLite is used.
- Crash recovery must reclaim expired leases and continue safely.
- Startup health must include a quick DB/index consistency check.
- If corruption is detected, stop writes, preserve evidence, restore from backup when possible and report a repair action.
- Tests must simulate concurrent Desktop status polling, Brain context queries and service writes.

### Brain modules to add

Create a package such as:

```text
src/local_context/
  __init__.py
  config.py
  schema.py
  service.py
  scheduler.py
  scanner.py
  checkpoint.py
  depth_policy.py
  extractors.py
  chunker.py
  entities.py
  graph.py
  embeddings.py
  resolver.py
  status.py
  cli.py
```

Use existing Brain patterns for paths, migrations, MCP exports, tests and local model pins. Do not create a parallel runtime database outside supported paths unless the product contract explicitly calls for it.

### Brain phases

Implement phases explicitly and report them in status.

#### Phase 1 - quick index

Goal: build the file/disc map fast.

Must collect:

- path
- name
- extension
- file type
- size
- created/modified timestamps
- volume/disk
- parent relation
- quick fingerprint
- permission state
- depth level

This phase should cover broad user-accessible areas and attached volumes. It should skip only truly blocked/protected/system paths or explicit exclusions.

#### Phase 2 - light extraction

Goal: extract cheap useful content.

Minimum supported in first release:

- text files
- markdown
- common source files
- JSON/YAML/TOML/config files
- CSV/TSV
- HTML/CSS
- existing NEXO conversations/diaries/observations
- email metadata and bodies where NEXO already has configured access
- image metadata/EXIF

Stretch if dependencies are already present or safe to add:

- PDF text
- DOCX
- PPTX
- XLSX

If a dependency is heavy or risky, degrade gracefully and record the extractor as unavailable in health/status.

Extractor safety:

- Run risky/heavy extractors in bounded worker subprocesses where practical.
- Sniff file type instead of trusting extension alone.
- Enforce limits for bytes, pages, rows, archive expansion and execution time.
- Skip password-protected or encrypted documents with a clear reason.
- Never execute local code or macros while extracting.
- Record normalized error codes, not raw stack traces, for user-facing status.

#### Phase 3 - smart extraction

Goal: understand only selected content, not the whole machine blindly.

Use deterministic heuristics first:

- recency
- file type
- folder/project relation
- email sender/recipient/thread
- attachment relation
- user/assistant active topic
- asset referenced by a current request

Then generate:

- short summary
- entity candidates
- relation candidates
- topic tags
- project hints
- action relevance

Do not use the tiny local presence model as the main summarizer for large documents. Use it only for lightweight routing/classification if quality is acceptable. Prefer embeddings + graph + deterministic extraction for first-release reliability.

#### Phase 4 - graph

Goal: connect local assets and NEXO memory.

Required relation types:

- `file_in_folder`
- `file_on_volume`
- `email_has_attachment`
- `email_from`
- `email_to`
- `asset_mentions_entity`
- `asset_related_to_project`
- `conversation_mentions_asset`
- `code_imports_or_references`
- `code_defines_endpoint`
- `asset_similar_to_asset`
- `asset_recently_used_with_topic`

Use existing knowledge graph patterns where possible. Every inferred edge needs confidence and evidence/source.

#### Phase 5 - embeddings

Goal: local semantic retrieval.

Use the pinned Brain embedding stack already documented in `docs/local-embedding-model-notes.md` unless a deliberate model upgrade is included in the same Brain release.

Embed:

- chunks
- summaries
- entity contexts
- relation captions

Do not embed every dependency/cache/build file by default. Inventory remains broad; embeddings are selective.

Incremental invalidation:

- Derived records must link back to `asset_version`.
- Chunks need deterministic IDs where possible so small edits do not invalidate unrelated chunks.
- Summaries, entities, relations and embeddings must be superseded or deleted when their source version changes.
- Embedding rows must store model id, revision, dimension and chunk version.
- Changing one paragraph should re-extract/re-embed only affected chunks when possible.
- Model upgrades must create a controlled re-embedding job, not corrupt old vectors silently.

### Local Model Manager and Desktop model migration

Brain must own the local model contract: profiles, model ids, pinned revisions, required files, sha256 hashes, compatibility policy, warmup API and runtime cache layout.

Required release profiles:

- `minimal`: required embeddings only, safe for low-resource machines.
- `local-context`: embeddings plus optional small local classification/summarization if the machine and manifest support it.
- `full`: all local context models allowed by policy and bundled artifacts.

Each profile must declare required/optional artifacts per platform, maximum expected disk size, engine, license/redistribution status, `trust_remote_code=false` where applicable and cleanup rules for orphaned revisions.

Desktop must keep its offline-first packaging advantage. It may continue to fetch and bundle model artifacts inside the installer so first install does not wait on HuggingFace or other model downloads. The distinction is:

- Brain owns what model is valid and how it is verified.
- Desktop/installer may own physical distribution for fast install.
- Runtime materialization copies/adopts only artifacts that match Brain's manifest.

Do not regress the current Desktop behavior where bundled local models reduce first-install time. Desktop's existing `resources/llm-models/` + `scripts/fetch-llm-models.sh` / `npm run prebuild:bundles` pattern should evolve to read Brain's manifest, not disappear.

The current Desktop local model is treated as one model profile, not as the general NEXO local intelligence model:

- profile: `local_presence_llm`
- current model: `qwen3-0.6b-q4-local-presence`
- purpose: wake/presence/lightweight local signals
- required: `false`
- not suitable as the main summarizer or document understanding engine

#### Model profiles

Brain should define model profiles in `src/local_model_manifest.json` and expose them through status APIs.

Required profiles for this release:

| Profile | Required | Purpose | Selection rule |
|---|---:|---|---|
| `embedding_default` | yes, when local context is enabled | semantic retrieval for chunks/summaries/entities | use current pinned multilingual embedding stack unless intentionally upgraded in same release |
| `reranker_default` | optional | improve retrieval ordering | install if already available or machine profile allows it |
| `local_presence_llm` | optional | Desktop voice/presence/light routing | migrate existing Desktop model into Brain manifest ownership |
| `local_context_llm_small` | optional | classify/summarize selected candidates | install only if machine profile and disk budget allow it |
| `local_vision_small` | optional/future | local photo/image understanding | not in first release unless implemented and tested explicitly |

Do not make `local_context_llm_small` mandatory for all users. The system must still work with extraction + embeddings + graph when no local LLM is installed.

#### How Brain chooses what to install

Add a Brain policy resolver such as `src/local_context/model_policy.py`.

Inputs:

- OS and architecture
- RAM
- free disk space under NEXO runtime model cache
- CPU/GPU/MPS availability if detectable safely
- battery/power state where available
- user/admin setting: automatic, conservative, performance, off
- feature need: presence, embeddings, context understanding, vision

Default automatic policy:

1. Always prepare `embedding_default` for Local Context Layer when indexing is enabled.
2. Keep `local_presence_llm` installed only if Desktop voice/presence features need it.
3. Install `local_context_llm_small` only on machines that satisfy a conservative resource threshold.
4. If thresholds are not met, skip the LLM and mark smart extraction as `limited_local_llm_unavailable`.
5. Prefer bundled/pre-cached model artifacts when Desktop ships them and their hashes match Brain's manifest.
6. Never download multi-GB models silently in the background without a product-level policy and visible status.

First-release conservative threshold suggestion:

- RAM: at least 16 GB for `local_context_llm_small`
- free disk: at least 6 GB after download/cache
- laptop on battery: defer heavy model download and heavy smart extraction
- low-end machines: embeddings + graph only

The exact thresholds must be codified in tests. Desktop should display a plain status such as "Comprension local avanzada pendiente por recursos del equipo", not raw model errors.

#### Desktop packaged model bundle

Desktop currently ships local model artifacts to avoid long first installs. Preserve that product property.

Current Desktop pattern to preserve/evolve:

- bundle directory: `resources/llm-models/`
- fetch script: `scripts/fetch-llm-models.sh`
- full release prebuild: `npm run prebuild:bundles`
- fast iteration prebuild: `npm run prebuild:bundles:fast` can skip heavy model refresh
- verification scripts: local LLM bundle/runtime bundle checks

New contract:

1. Brain's `src/local_model_manifest.json` remains the canonical manifest.
2. Desktop's model fetch script reads that manifest and downloads exactly the required files.
3. Desktop release verification fails if bundled model files do not match Brain manifest size/hash.
4. During install/update, Desktop offers the bundled model directory to Brain as a trusted candidate cache.
5. Brain verifies hashes and materializes/adopts the files into `NEXO_HOME/runtime/models/...`.
6. If a bundled optional model is absent because a fast/dev build skipped heavy bundles, Brain degrades gracefully or downloads later only when policy allows.
7. Public release builds must not rely on slow first-run model downloads for required profiles.

In other words: Brain is the authority; Desktop is still allowed to be the delivery vehicle.

#### Candidate `local_context_llm_small`

Do not pick the final model from memory during implementation. Evaluate and pin one model in the Brain manifest with immutable revision and sha256 before release.

Candidate families:

- Qwen 2.5/3 small instruct GGUF
- Llama 3.2 small instruct GGUF
- other compact local instruct models that load reliably on CPU/MPS

Selection criteria:

- Spanish/English quality
- summarization quality on short extracted chunks
- entity extraction quality
- predictable JSON output
- RAM and disk footprint
- offline reproducibility
- pinned source revision + file sha256

If no candidate passes, ship without `local_context_llm_small` and leave smart extraction as deterministic + embeddings + graph in this release.

#### Desktop -> Brain migration

Migration goal: no duplicated model contract, without losing Desktop's bundled/offline model delivery.

Steps:

1. Brain adds `local_presence_llm` to the canonical model manifest if not already present.
2. Brain exposes model status:
   - installed
   - missing
   - downloading
   - verifying
   - unavailable
   - model id/revision/hash
3. Desktop replaces any hardcoded local model assumptions with calls to Brain model status/warmup APIs.
4. Desktop keeps `scripts/fetch-llm-models.sh` / bundled model resources, but points them at Brain's manifest and verifies against Brain hashes.
5. If Desktop has a bundled or previously downloaded presence model, it should either:
   - ask Brain to verify/adopt it if path/hash match the Brain manifest, or
   - let Brain download/materialize the verified copy into the Brain runtime cache.
6. Desktop keeps a temporary runtime fallback only for one release cycle if needed, but the fallback must compare against Brain's manifest and log that ownership is migrating.
7. After one release cycle, remove Desktop-owned model decision logic, not Desktop's build-time bundling pipeline.

The migration should not force all Brain standalone users to download the presence LLM. It only becomes available through Brain's model manager; it remains optional and feature-driven.

#### Required model APIs

Add Brain APIs/CLI:

```bash
nexo local-models status --json
nexo local-models warmup --profile embedding_default
nexo local-models warmup --profile local_presence_llm
nexo local-models warmup --profile local_context_llm_small
nexo local-models policy --json
```

Expose equivalent MCP/tool or Desktop bridge surfaces:

- `nexo_local_model_status`
- `nexo_local_model_warmup`
- `nexo_local_model_policy`

Desktop should consume these APIs for:

- Settings -> Indexacion local status
- voice/presence readiness
- explaining why advanced local understanding is active, pending, or unavailable

### Brain CLI/API

Add CLI commands:

```bash
nexo local-index status --json
nexo local-index start
nexo local-index stop
nexo local-index pause
nexo local-index resume
nexo local-index reindex --scope all|changed|path
nexo local-index roots list
nexo local-index roots add PATH
nexo local-index roots remove PATH
nexo local-index exclusions list
nexo local-index exclusions add PATH
nexo local-index exclusions remove PATH
nexo local-index service install
nexo local-index service uninstall
nexo local-index logs --tail --json
nexo local-index purge --asset ASSET_ID
nexo local-index purge --root PATH
nexo local-context query "Maria" --json
```

Add MCP/tool exports:

- `nexo_local_index_status`
- `nexo_local_index_control`
- `nexo_local_index_roots`
- `nexo_local_index_exclusions`
- `nexo_local_context`
- `nexo_local_asset_get`
- `nexo_local_asset_neighbors`
- `nexo_local_index_diagnostics_tail`
- `nexo_local_index_purge`

Required status payload shape:

```json
{
  "ok": true,
  "service": {
    "installed": true,
    "running": true,
    "state": "active|paused|indexing|idle|waiting_permission|waiting_disk|failed|not_installed",
    "platform": "macos|windows|linux",
    "started_at": "...",
    "last_heartbeat_at": "..."
  },
  "global": {
    "phase": "quick_index|light_extraction|smart_extraction|graph|embeddings|idle",
    "percent": 0,
    "files_found": 0,
    "files_processed": 0,
    "changes_pending": 0,
    "elapsed_seconds": 0,
    "eta_seconds": null
  },
  "volumes": [],
  "roots": [],
  "exclusions": [],
  "problems": [],
  "permissions": [],
  "models": [],
  "support_log_available": false
}
```

Problem rows must include:

- `user_message`
- `recommended_action`
- `technical_detail`
- `support_code`
- `severity`
- `retryable`
- `asset_ref` when safe

Normal Desktop UI shows only `user_message` and `recommended_action`. Support mode may reveal `technical_detail` after redaction.

### Pre-action integration

Add one Brain resolver path that agents and automations can call before action:

```json
{
  "query": "que sabes sobre maria",
  "intent": "answer|email_reply|code_change|file_lookup|photo_lookup",
  "current_context": "...",
  "evidence_required": true,
  "limit": 12
}
```

Return a context pack:

```json
{
  "ok": true,
  "query": "...",
  "intent": "...",
  "confidence": 0.0,
  "summary": "...",
  "assets": [],
  "entities": [],
  "relations": [],
  "chunks": [],
  "warnings": [],
  "evidence_refs": []
}
```

Integrate this resolver into:

- interactive startup/pre-action context when relevant
- email reply path before drafting
- code task path before edits
- photo/file lookup path
- followup/email automations where local context can improve result

Do not force a local-context call on every trivial message. Use intent/routing to avoid latency.

Mandatory Brain usage contract:

- Before answering a knowledge question about a person, company, project, document, purchase, contract or local file, Brain should ask `nexo_local_context` unless the answer is clearly unrelated to local context.
- Before drafting or replying to an email, Brain should request local context for sender, thread, attachments, mentioned entities and related documents.
- Before code edits, Brain should request related project files, dependency/config hints, known references and graph neighbors for the target files.
- Before file/photo lookup, Brain should combine semantic results, graph neighbors and asset metadata instead of relying on filename search alone.
- Every returned context pack must include evidence refs or warnings explaining why evidence is unavailable.
- Nero must treat the context pack as evidence to inspect and cite internally, not as an unverified final answer.
- Wire this through the existing pre-action/context path used by Brain/MCP/router, not as an isolated CLI demo.
- Add integration tests proving email reply, code change and document/person questions request local context when relevant and degrade cleanly when it is unavailable.

### Resident service

Implement a user-level resident service:

- macOS: LaunchAgent preferred for user files; LaunchDaemon only if a signed/admin helper later requires it.
- Windows: per-user scheduled task at logon as baseline; Windows Service only if installer privileges and signed helper support it safely.
- Linux: systemd user service.

Service command contract per OS:

- install
- start
- stop
- status
- pause
- resume
- uninstall
- repair
- logs/status path discovery

Service requirements:

- starts at login/reboot
- keeps running when Desktop is closed
- writes heartbeats/status
- resumes checkpoints
- exposes `installed|running|paused|failed|not_installed` state
- records the executable/path that actually needs OS permissions
- backs off under CPU/battery/user activity pressure
- handles corrupt/slow files with timeout and error rows
- bounded retries
- no infinite loops
- safe shutdown
- clean uninstall disables service and leaves user files untouched
- service logs are rotated and redacted

### Brain tests

Add tests for:

- schema migrations are idempotent
- scanner detects files and does not reindex unchanged files
- depth policy keeps noisy folders inventory-only by default
- checkpoint resumes mid-job
- corrupt/slow file records error and continues
- status payload shape
- CLI commands
- MCP exports
- context pack evidence refs
- graph relations
- embeddings store/selective policy
- service config rendering for macOS/Windows/Linux without actually installing system services in CI
- DB concurrency with service writer + Brain/Desktop readers
- watcher coalescing, overflow and reconciliation
- platform path handling for Windows drive letters and macOS Unicode paths
- pre-action integration in email/code/document flows
- purge cascade
- sensitive fixture policy and egress-zero behavior
- extractor worker timeout/kill behavior

Brain verification before release:

```bash
python3 -m pytest tests/test_local_context*.py tests/test_migrations.py tests/test_server_protocol_exports.py -q
python3 -m ruff check src/local_context src/server.py src/db
scripts/pre-release-verify.sh --release vX.Y.Z
```

## Desktop release scope

Desktop owns:

- managed enablement during install/update
- OS permission prompts/surfaces
- Settings UI
- bridge calls to Brain status/control APIs
- non-technical health wording
- release package with updated Brain dependency/artifacts

### Desktop UI

Add a Settings section:

- Spanish user-facing label: `Memoria local`
- English user-facing label: `Local memory`
- Technical/internal label may remain `local indexing`.

Position: Settings/Preferences as a product section, not hidden in developer-only screens.

Primary explanation:

> NEXO organiza y conecta la informacion de este ordenador para que Nero pueda trabajar con mas contexto. Funciona en segundo plano; no tienes que hacer nada.

Normal users should understand the page without knowing what an index, embedding, graph, daemon, watcher or checkpoint is. Use plain states such as:

- `Preparando memoria local`
- `Actualizando cambios`
- `Todo esta al dia`
- `Pausado`
- `Esperando permiso`
- `Esperando a que vuelva el disco`
- `Algunos archivos se omitieron`

Global status:

- progress bar
- percentage
- total files found
- files processed
- current phase
- elapsed time
- estimated remaining time
- last check
- service state: active, paused, waiting for permission, indexing, idle
- recent changes count
- skipped/problem files count

Per disk/volume:

```text
Disco principal
Ultima comprobacion: 2026-05-12 08:42
Archivos detectados: 1.000.000
Estado: Fase 1 - indice rapido
Progreso: 57%
Tiempo procesado: 34m
Tiempo estimado: 1h 12m
```

Buttons:

- Pause
- Resume
- Force reindex
- Clear index, with confirmation and plain explanation
- Add folder
- Exclude folder
- Remove exclusion
- Show included folders
- Show excluded folders
- Show details/problems

Folder controls:

- Add folder uses native OS picker and stores a root in Brain.
- Exclude folder uses native OS picker and stores an exclusion in Brain.
- Exclusions must be visible as a list with remove buttons.
- Excluded folders must apply on the next cycle without requiring a reboot.
- Excluding a folder must not delete user files; wording must say it only stops NEXO from reading that folder.
- Default noisy folders can be shown as "managed automatically" but should not be confused with user exclusions.

Advanced wording:

- Avoid scary raw errors.
- Show "Algunos archivos no se pudieron leer" instead of stack traces.
- Keep detailed errors behind an expandable advanced view.
- Never show extracted document text in normal Settings UI.

Support and diagnostics mode behavior:

- Desktop already has `app.support_mode` under Preferences -> Support as "Support and diagnostics mode". Reuse that flag.
- When support mode is off, the Local memory page must stay simple and hide raw logs.
- When support mode is on, Desktop may show a support-only diagnostic log tab or panel for Local memory.
- Prefer a separate support/debug surface such as `LocalIndexLogTab` if that fits the existing Settings architecture better than putting raw logs inside the normal page.
- The live log should stream or poll a bounded Brain ring buffer, not read unbounded log files into the renderer.
- The live log may show phase, status, counters, root, file type, sanitized path, error code and retry reason.
- The live log must not show extracted text, email body, document content, embedding vectors or secrets.
- Add actions for "Copy diagnostics" and "Export support bundle" if the existing Desktop support flow can provide them safely.
- If Brain is offline, show "NEXO no puede leer el estado ahora" plus a retry action, not fake progress.

Permissions integration:

- Local memory must appear in the existing Permissions surface as its own capability.
- macOS copy should explain Full Disk Access in user language and deep-link to the correct System Settings page.
- Windows copy should explain folder/account access in Windows language and must not mention macOS-only concepts.
- If permission is missing, normal UI should show the next user action, not a technical failure.

### Desktop bridge

Add IPC/bridge calls:

- `localIndexStatus`
- `localIndexControl`
- `localIndexAddRoot`
- `localIndexRemoveRoot`
- `localIndexAddExclusion`
- `localIndexRemoveExclusion`
- `localIndexOpenPrivacySettings`
- `localIndexDiagnosticsTail` only available/rendered when support mode is active
- `localIndexExportDiagnostics` if support bundle integration is implemented

Likely files to inspect/edit:

- `main.js`
- `preload.js`
- `lib/brain-bridge-ipc.js`
- `lib/settings-schema.js`
- `lib/settings-controller.js`
- `lib/settings-runtime-surfaces.js`
- `renderer/react/panels/Settings/components/SettingsNav.jsx`
- `renderer/react/panels/Settings/SettingsShell.jsx`
- `renderer/react/panels/Settings/tabs/LocalMemoryTab.jsx` or equivalent new component
- `renderer/i18n/es.json`
- `renderer/i18n/en.json`
- `renderer/react/panels/Settings/*`
- `tests/settings-*.test.js`
- `tests/brain-bridge*.test.js`

### Desktop automation

Desktop must:

- install/enable the Brain local-index service during setup/update when safe
- detect permission state
- show one clear permission prompt if needed
- keep service status visible
- not require Desktop to stay open
- not duplicate indexing logic in JavaScript
- keep Windows and macOS service enablement paths separate and tested
- preserve existing support-mode behavior: support-only diagnostics stay hidden until `app.support_mode` is enabled

Desktop should call Brain CLI/API; Brain remains the source of truth.

### Desktop tests

Add tests for:

- Settings schema exposes Local indexing tab
- Settings navigation labels it as Local memory/Memoria local for users
- renderer shows global progress and per-volume rows
- buttons call bridge actions
- add folder, exclude folder and remove exclusion call the correct bridge methods
- status polling handles offline Brain
- permission-needed state is non-technical
- offline disk state is non-technical
- problem files are hidden behind details
- raw logs are hidden when support mode is off
- live diagnostic log is visible when support mode is on
- diagnostic log redacts content and does not render extracted text
- Desktop does not fake status if Brain is unavailable
- service remains described as background/resident
- bundled model resources are verified against Brain's manifest
- a dev/fast build can skip heavy optional model bundles, but release builds cannot skip required model bundles
- Windows build/test path covers drive-letter paths and Brain path translation
- macOS build/test path covers Full Disk Access messaging and LaunchAgent status

Desktop verification before release:

```bash
npm run check
npm run prebuild:bundles
npm run verify:llm-bundle
npm run verify:llm-runtime-bundle
npm test -- --runInBand tests/settings-*.test.js tests/brain-bridge*.test.js
npm run dist:qa
npm run manifest -- --notes "Local indexing status and NEXO Local Context Layer"
```

Use `dist:release` only if signing/notarization is ready. Otherwise publish QA/pre-sales according to current release policy.

## End-to-end acceptance criteria

The feature is acceptable only when all of these are true:

- Release preflight recalculates Brain/Desktop versions from live repos and target versions are updated.
- Brain release is published/tagged first and Desktop bundles exactly that Brain version.
- Brain and Desktop worktrees are clean except intentional release changes.
- Fresh install or updated install creates local index tables.
- DB concurrency gate passes with service writer plus Brain/MCP/Desktop readers.
- Service can start without Desktop open.
- Service resumes after restart.
- macOS LaunchAgent install/start/status/stop/uninstall/reboot/resume passes on Apple Silicon and Intel, or the release explicitly blocks the unsupported arch.
- Windows scheduled task/service install/start/status/stop/uninstall/reboot/resume passes on Windows 10/11 in the supported runtime mode.
- Permission state is correct for the actual scanning executable/helper on each OS.
- Phase 1 indexes user-accessible files broadly.
- Phase 1 writes durable checkpoints and can resume after interruption.
- Watchers and periodic reconciliation handle new, modified, moved and deleted files.
- External/offline disks are not mistaken for mass deletion.
- Noisy dependency/build/cache folders appear as inventory-only, not silently invisible.
- Phase 2 extracts useful text/metadata from first-release supported types.
- Phase 3 produces summaries/entities/tags for selected candidates without scanning the whole machine blindly.
- Phase 4 writes graph relations with confidence and evidence/source metadata.
- Phase 5 stores/searches local embeddings for eligible chunks/summaries/entities.
- Sensitive files are depth 0 or inventory-only by default.
- Clear index and purge flows remove derived chunks, summaries, graph records and embeddings.
- Pre-action context pack returns evidence refs and is callable by Nero/automations.
- Existing Brain pre-action/router paths actually call Local Context for relevant email/code/document/person tasks.
- Context API can answer "Maria" with evidence from indexed local data when such data exists.
- Email reply path can request local context for sender/thread/attachments before drafting.
- Code task path can retrieve related project files before edits.
- Settings shows global progress and per-disk progress.
- Force reindex works through Desktop -> Brain.
- Pause/resume works through Desktop -> Brain.
- Permission missing state is understandable for non-technical users.
- No external API is required for indexing/embedding local content.
- No external API receives local chunks, emails, paths, summaries, entities, embeddings or context packs by default.
- Support diagnostics/logs are redacted and do not include raw private content.
- Brain owns local model selection through model profiles and manifest verification.
- Desktop consumes Brain model status/warmup APIs instead of owning local model decisions.
- Desktop's installer still bundles/pre-caches required model artifacts so normal first install is not slowed by model downloads.
- The existing Desktop presence model remains optional and does not become mandatory for Brain standalone installs.
- Release docs/changelog mention the feature without overclaiming face recognition or full visual understanding.
- Photo/video visual recognition remains explicitly out of scope and is not required for release acceptance.

## QA fixture corpus

Create a reproducible local fixture suite before implementation is considered complete.

Minimum fixture coverage:

- new, modified, moved and deleted files
- symlinks, aliases, junctions and loop prevention
- Unicode paths, spaces, long paths and case-only renames
- permission denied folders
- corrupt PDF/DOCX/XLSX/PPTX/email files
- password-protected documents
- `.env`, SSH key, API token and cookie-like files
- email with attachment
- PDF invoice/contract sample
- source-code project with config, tests and dependency folders
- database dump sample
- image with EXIF GPS metadata, without face/object recognition
- iCloud/OneDrive placeholder-like files
- external/offline volume simulation

Required tests:

- scanner/extractor/graph/embedding behavior against fixtures
- egress-zero test during scan/query
- purge cascade test
- support bundle redaction test
- runtime file permission test on macOS and Windows
- watcher reconciliation test for missed/duplicated events

## Cross-platform smoke matrix

Minimum smoke matrix before release:

- macOS Apple Silicon fresh install
- macOS Apple Silicon update install
- macOS Intel if still supported by Desktop release policy
- Windows 11 fresh install with WSL present
- Windows 11 fresh install without WSL
- Windows 10 if still supported by Desktop release policy
- standard user where possible
- admin/elevated install path where required by packaging
- Desktop closed while service runs
- reboot/resume
- permission denied then granted
- external disk offline then online
- fixture indexing and `local-context query` with evidence pack

Each smoke must verify service status, Desktop UI status, support-mode log visibility, redaction, pause/resume, exclusion list and force reindex.

## Release coordination, rollback and no-publish gates

Release coordination:

- Brain version, tag, changelog and package metadata must match before Desktop starts release packaging.
- Desktop must verify the bundled Brain source/version, model manifest and required artifacts.
- Desktop must not tag or publish with a dirty Brain checkout or mismatched Brain bundle.
- Public manifests, legacy aliases, download URLs, SHA/size, blockmap and update channels must be verified after upload.

Migration:

- Fresh install, update from previous Brain/Desktop, postinstall migration and Desktop bridge compatibility must all pass.
- SQLite/index migrations must be idempotent and backup before destructive changes.
- Model adoption from Desktop bundle into Brain runtime cache must be verified by hash.
- One-release compatibility shims are allowed only if documented and tested.

Rollback:

- Define how to disable/uninstall the service without deleting user files.
- Define how to restore previous Desktop stable/beta manifests.
- Define how to restore or ignore a failed local index migration safely.
- Verify `nexo --version`, Desktop update check and `local-index status` after rollback.

Desktop blocker added during implementation:

- Bug: race condition where a Protocol Enforcer silent turn overlaps a new user message and Desktop drops the visible assistant reply.
- Repro:
- 1. Conversation open in NEXO Desktop 0.32.x.
- 2. Backend has just returned a normal assistant response.
- 3. A silent `system-reminder`/Protocol Enforcer turn arrives immediately after, for example `nexo_smart_startup` or `nexo_heartbeat` with "Do not produce visible text for this reminder".
- 4. While that silent tool-only turn is still executing, the user sends a new chat message.
- 5. Runtime delivers that message as an interruption while the model is working.
- Observed behavior:
- The chat briefly shows "thinking", then spinner disappears and status returns to ready.
- The user message remains visible but no assistant answer is painted in the UI.
- Backend did generate the answer, visible in transcript/recent context.
- Hypothesis:
- The frontend closes the visible turn when the silent enforcer tool-call completes.
- The later assistant prose for the interrupted user message is discarded or associated with an already closed turn.
- Areas to inspect:
- Desktop renderer streaming and turn lifecycle.
- `enforcerSilent` handling in header/sidebar and spinner state.
- Stop-turn abort path: abort must not cancel/drain visible prose incorrectly.
- Known family:
- `NF-DS-1A05E8AA`, Desktop 0.32.62 deferred fixes around `enforcerSilent` header/sidebar and full stop-turn abort.
- Evidence:
- Affected SID: `nexo-1778577772-52043`.
- Conversation: `aa73e681-8e4a-4e89-8e7d-032c978a7ba4`.
- User message lost in UI: "tu entiendes cual es la idea de esto?", timestamp 2026-05-12 11:26 local.
- Backend answer started with: "Sí, Francisco. La idea de fondo es esta: NEXO se convierte en el operador comercial único...".
- Acceptance:
- Add a test that simulates silent enforcer tool-call plus overlapping `user_message`.
- The assistant response to the user message must reach the DOM even if the turn started with a silent tool call.
- "Thinking" must remain active while visible prose is still pending, even if the silent tool call already finished.
- Add the case to the 0.32.62 smoke coverage before release.

No publish if any of these is true:

- Any core phase in scope is incomplete.
- The silent-enforcer plus overlapping user-message race can still drop a backend-generated visible assistant reply in Desktop.
- macOS or Windows release smoke fails.
- Service cannot survive reboot/Desktop closed on either OS.
- Required model artifacts are missing or hash mismatched.
- Local private content can leak into logs/support bundle/remote APIs by default.
- Clear index/purge leaves derived private content behind.
- Desktop UI shows raw stack traces or technical logs to normal users.
- Support-mode log is visible without `app.support_mode`.
- Brain/Desktop versions, tags, manifests or bundled artifacts do not match.
- Public update manifest verification fails.
- Rollback path is untested.

## Visual media scope for this release

Do not claim photo face recognition, object recognition, video transcription or video scene understanding in this release.

For "muestrame todas las fotos de mi mama", first-release behavior can support:

- filenames
- folders/albums
- EXIF dates/locations
- existing captions/metadata
- previous conversations linking the person to files/events
- manually or automatically inferred relations with confidence

Full face recognition, object recognition and video understanding are later explicit features. They are not hidden stretch goals in this release.

Do not claim the current tiny local presence model can understand the whole computer. It can help with lightweight local classification if quality is good, but embeddings, extraction and graph are the reliable core of this release.

## Implementation order when Francisco says "adelante"

1. Re-read this document.
2. Check project atlas and current repo state.
3. Confirm current Brain/Desktop versions from live `package.json` files and choose target release numbers.
4. Inspect dirty files and do not include unrelated user/other-session changes.
5. Build the QA fixture corpus and platform smoke plan before product code claims are made.
6. Brain: implement schema, DB concurrency model and local_context package.
7. Brain: implement scanner, watcher backends, reconciliation and durable queue.
8. Brain: implement safe extractors, depth policy, chunk invalidation, graph and embeddings.
9. Brain: implement Local Model Manager profiles/policy/status and migrate Desktop presence model ownership into Brain manifest.
10. Brain: implement CLI + MCP exports.
11. Brain: implement service runner and platform service install/start/status/stop/uninstall for macOS and Windows.
12. Brain: implement pre-action/router integrations.
13. Brain: add tests and run targeted suite, including fixtures, concurrency, egress-zero, purge and platform service rendering.
14. Brain: update docs/changelog/version.
15. Brain: run full pre-release verification.
16. Brain: commit/tag/publish release if gates pass.
17. Desktop: update Brain dependency/artifact sync to that exact Brain release.
18. Desktop: replace Desktop-owned local model assumptions with Brain model status/warmup calls.
19. Desktop: update model bundling scripts so `prebuild:bundles` fetches/verifies Brain-manifest model artifacts.
20. Desktop: implement bridge + Settings UI (`Memoria local` / `Local memory`).
21. Desktop: implement service enable/status calls and Permissions integration.
22. Desktop: implement support-mode-only Local memory diagnostic log.
23. Desktop: add tests and run `npm run check`.
24. Desktop: run `npm run prebuild:bundles` and model bundle verification for release build.
25. Desktop: build QA/release artifact.
26. Run cross-platform smoke matrix for macOS and Windows.
27. Publish update manifests only after Brain/Desktop/version/artifact/no-publish gates pass.
28. Verify public manifests, aliases, download URLs, SHA/size, blockmap and rollback path.
29. Verify installed Desktop sees the new menu, reports Brain local index status and hides support logs unless support mode is enabled.

## Release notes draft

Brain:

```text
Added NEXO Local Context Layer: a local, background indexing service that builds a private context layer from user files, documents, emails, projects and NEXO memory. Includes incremental scanning, checkpoints, local embeddings, graph relations and context-pack APIs for Nero before answering or acting.
```

Desktop:

```text
Added Preferences -> Local memory: a simple status and control surface for NEXO's background local context service. Users can see progress by disk, pause/resume, force reindex and manage folders while the system continues to work automatically in the background. Technical logs remain hidden unless Support and diagnostics mode is enabled.
```

## Final execution rule

When executing this plan, do not stop after Brain or after Desktop alone. The release is complete only when:

- Brain release exists and passes verification.
- Desktop release includes the visible Settings menu.
- Desktop calls the Brain local index APIs.
- Public update manifests point to the new Desktop version.
- A real local status call proves the feature is active or correctly waiting for OS permission.
- Windows and macOS smoke verification is green.
- Support-mode diagnostic logs are gated and redacted.
- All in-scope core phases pass their acceptance tests. If any core phase is incomplete, fix it before publishing or explicitly stop and report the blocker; do not ship it as "done".
