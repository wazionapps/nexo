# NEXO Brain — Your AI Gets a Brain

[![npm](https://img.shields.io/npm/v/nexo-brain?label=npm&color=purple)](https://www.npmjs.com/package/nexo-brain)
[![F1 0.588 on LoCoMo](https://img.shields.io/badge/LoCoMo_F1-0.588-brightgreen)](https://github.com/wazionapps/nexo/blob/main/benchmarks/locomo/results/)
[![+55% vs GPT-4](https://img.shields.io/badge/vs_GPT--4-%2B55%25-blue)](https://github.com/snap-research/locomo/issues/33)
[![GitHub stars](https://img.shields.io/github/stars/wazionapps/nexo?style=social)](https://github.com/wazionapps/nexo/stargazers)
[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

> Local cognitive runtime with a shared brain across Claude Code, Codex, Claude Desktop, and other MCP clients. Persistent memory, durable workflow runs, selectable terminal and automation backends, overnight learning, self-healing background jobs, startup preflight, and doctor diagnostics. 150+ MCP tools. Benchmarked on LoCoMo (F1 0.588, +55% vs GPT-4).

**NEXO Brain transforms any MCP-compatible AI agent from a stateless assistant into a cognitive partner that remembers, learns, forgets, adapts, and builds a relationship with you over time.**

<p align="center">
  <a href="https://nexo-brain.com/watch/">
    <img src="assets/nexo-brain-infographic-v5.png" alt="NEXO Brain Architecture" width="700">
  </a>
</p>

[Watch the overview video](https://nexo-brain.com/watch/) · [Watch on YouTube](https://www.youtube.com/watch?v=i2lkGhKyVqI) · [Open the infographic](https://nexo-brain.com/assets/nexo-brain-infographic-v5.png)

Version `7.31.10` is the current packaged-runtime line. Patch release over v7.31.9 - Local Memory search now downranks boilerplate emails when stronger documents match the same query.

Previously in `7.31.9`: patch release over v7.31.8 - UI release closeout now has to prove the original reported symptom was reopened with observable evidence before claiming the release is ready.

Previously in `7.31.8`: patch release over v7.31.7 - email monitor debt scans no longer escalate intentionally waiting threads as unresolved commitments when recent resolution or hot-context state proves the thread is waiting on the user or a third party. Real unresolved commitments still surface.

Previously in `7.31.7`: patch release over v7.31.6 - stateful answers now require evidence before claiming release, commit, branch, server, ticket, deployment, sent/uploaded, installed, verified, or closed status. Guardian defaults promote identity coherence to hard/core and add a hard/core pre-answer evidence gate, while closeout and Local Context telemetry now leave stronger proof trails.

Previously in `7.31.6`: patch release over v7.31.5 - headless/email-monitor notifications now respect the Desktop UI language for static ES/EN templates before falling back to profile/calibration language and English.

Previously in `7.31.5`: patch release over v7.31.4 - the breaker resume notice exists: when a notified engine pause recovers, the operator gets exactly one resume email in their language, signed by their agent.

Previously in `7.31.4`: patch release over v7.31.3 - memory recall honours absolute time ranges (ISO dates, start..end ranges, datetimes, epochs) and enforces the window in SQL, so asking about a specific past day returns that day.

Previously in `7.30.33`: patch release over v7.30.32 - personal agent/script status now keeps the newest real run between manual executions and cron history, so a successful manual agent run cannot be hidden behind an older scheduled failure.

Previously in `7.30.32`: patch release over v7.30.31 - packaged update/doctor now repair npm/npx wrapper drift, archive stale personal script backups, validate observable automation health contracts, and block legacy Claude/Codex project memory writes.

Previously in `7.30.31`: patch release over v7.30.30 - Core Rules now reach agents both through a compact managed bootstrap summary and task-specific `cortex/task_open` injection from the protected `core_rules` registry.

Previously in `7.30.30`: product-managed Core Rules now sync from `src/rules/core-rules.json` into protected DB rows for bootstrap and product behavior, with provenance, hashes, severity, and install/update synchronization.

Previously in `7.30.29`: runtime disk guards now bound hourly database backups and pause Local Memory indexing before disk pressure becomes unsafe.

Previously in `7.30.27`: patch release over v7.30.26 - post-update repair now recovers core scripts archived by older F0.6 shim reconciliation and refreshes `core/current` from `core`, so same-version snapshots cannot keep stale watchdog code.

Previously in `7.30.26`: patch release over v7.30.25 - `nexo update` now copies packaged core scripts through the F0.6 `scripts -> core/scripts` shim, so watchdog fixes reach the active LaunchAgent path.

Previously in `7.30.25`: patch release over v7.30.24 - Desktop-facing maintenance diagnostics now self-heal or stay informational when the runtime is healthy, and watchdog uses the canonical cognitive DB layout.

Previously in `7.30.24`: patch release over v7.30.23 - client sync now stages and verifies managed MCP providers before writing Brain-owned default MCP entries into Claude, Codex, or Desktop configs.

Previously in `7.30.23`: patch release over v7.30.22 - Brain ships Opportunity Orchestrator Phase 1 for evidence-backed proactive preparation, read-only dry-run reports, bounded proposal queues, and feedback/suppression controls.

Previously in `7.30.20`: patch release over v7.30.19 - packaged installs now copy the `product_knowledge` package into the installed runtime so `nexo update` can import the new product knowledge tools.

Previously in `7.30.19`: patch release over v7.30.18 - product capabilities now live in a structured, validated catalog with read-only discovery tools and preserved legacy system-catalog names.

Previously in `7.30.16`: patch release over v7.30.14 - Desktop diagnostics can read embedding migration status without warming models, and the coordinated Desktop update path is covered for bundled model verification and obsolete managed model cleanup.

Previously in `7.30.15`: patch release over v7.30.14 - Email NEXO closes affirmative actionable replies correctly and avoids reopening processed threads after a sent reply is already recorded.

Previously in `7.30.14`: patch release over v7.30.13 - support tickets, provider capability discovery, task-close rearming, internal audit followups, and the memory-observation watchdog are aligned for Desktop-managed agents.

Previously in `7.30.13`: patch release over v7.30.12 - Email NEXO monitor prompts now require a full IMAP body fetch before replying, and managed email defaults use the MXroute `Sent` folder instead of `INBOX.Sent`.

Previously in `7.30.12`: patch release over v7.30.11 - Desktop-managed email credentials now resolve through the shared credential helper, so Email NEXO migrations and CLI reads use keychain-backed markers before legacy SQLite fallback.

Previously in `7.30.11`: patch release over v7.30.10 - the installer and npm postinstall path now stamp the verified repair baseline too, so the first update from older packaged installs is covered without a manual second run.

Previously in `7.30.10`: patch release over v7.30.9 - packaged `nexo update` now stamps the verified repair baseline after import verification, including same-version maintenance runs.

Previously in `7.30.9`: patch release over v7.30.8 - post-update self-heal now stamps a verified repair baseline, and doctor release gates distinguish current installation failures from historical operator/session drift.

Previously in `7.30.8`: patch release over v7.30.7 - Deep Sleep now folds parallel Codex sub-agents into their parent thread and Local Context stops the `entity_facts` cartesian blow-up that created runaway sidecar databases.

Previously in `7.30.7`: patch release over v7.30.6 - the Deep Sleep retention update is republished with the required release smoke contract so final closeout, npm, GitHub, and runtime verification stay aligned.

Previously in `7.30.6`: patch release over v7.30.5 - Deep Sleep now rotates its operational artifacts and logs automatically, keeping historical installs bounded without touching local-context memory.

Previously in `7.30.4`: patch release over v7.30.3 - local runtime update post-sync now gives bounded Memory Fabric repair enough time to finish, and headless automations now treat `nexo_stop` as a terminal close so followup/deep-sleep runners do not reopen no-op protocol loops.

Previously in `7.30.3`: patch release over v7.30.2 - release closeout now protects the freshly written runtime backup from technical pruning and validates protocol evidence against the canonical `runtime/data/nexo.db` layout.

Previously in `7.30.2`: patch release over v7.30.1 - Deep Sleep now fails closed on partial nightly runs, carries complete learning context into synthesis, writes an explicit agent start packet, and creates governed followups with verification, priority, owner, and date fields.

Previously in `7.30.1`: patch release over v7.30.0 - morning briefings now behave more like a start-of-day assistant, explain news/weather as verified optional sources, and keep user type inference inside NEXO instead of asking the user to choose a role manually.

Previously in `7.30.0`: minor release over v7.29.0 - morning briefing preferences now support weather/news, selectable weekdays, safer SMTP certificates, Opus 4.8 defaults, and an agent-facing preference catalog so NEXO can explain or change supported settings by chat.

Previously in `7.29.0`: minor release over v7.28.0 - the morning briefing now has structured content preferences, Desktop-facing briefing presentation state, and per-account email signature support.

Previously in `7.28.0`: minor release over v7.27.6 - the Brain memory architecture now links action authorship, reasons, operational state, entity profiles, failure prevention, semantic layers, and release-gated runtime memory benchmarks.

Previously in `7.27.6`: patch release over v7.27.5 - operational memory continuity now persists promises as commitments, routes pre-answer questions through evidence-backed memory, and exposes observation-queue convergence in health checks.

Previously in `7.27.5`: patch release over v7.27.4 - Desktop onboarding asks for the user's name directly in Spanish with `¿Cómo te llamas?`, matching Desktop fallback copy.

Previously in `7.27.1`: patch release over v7.27.0 - lifecycle stop calls skip external provider session UUIDs safely, and provider runtime selection keeps chat plus automation aligned to the same Anthropic/OpenAI account.

Previously in `7.27.0`: minor release over v7.26.0 - Codex-side defaults move to verified `gpt-5.5` resonance tiers, managed config healing follows that model family, and Local Memory file-type actions tolerate transient SQLite locks.

Previously in `7.26.0`: minor release over v7.25.6 - provider runtime parity lets Desktop-managed Brain choose Anthropic or OpenAI, keep provider metadata on sessions/automation/crons, and provision the managed OpenAI runtime from bundled Desktop resources.

Previously in `7.25.6`: patch release over v7.25.5 - existing Local Memory sidecar databases repair legacy root/exclusion columns before source-dependent indexes are created, and core background crons prefer the NEXO-managed Python runtime.

Previously in `7.25.4`: patch release over v7.25.3 - Local Memory starts from safe user-content and email roots, adds configurable included/excluded file types, and cleans legacy whole-disk index state with backup or archive-rebuild safety.

Previously in `7.25.3`: patch release over v7.25.2 - Desktop-managed Brain installs require the same Python ABI as the bundled wheels and repair incompatible managed virtual environments before reuse.

Previously in `7.25.0`: minor release over v7.24.0 - Memory Fabric links transcript lookup, historical backup diary recovery, unified search and knowledge graph evidence so memories are not available only inside expiring snapshots.

Previously in `7.24.0`: minor release over v7.23.13 - Home Agents, cognitive quality controls, English operational copy, and non-blocking task-open context are integrated into main.

Previously in `7.23.13`: patch over v7.23.12 - release guardrails now audit publish workflows for masked failures and add minimal-delta coverage for punctual UI edits.

Previously in `7.23.12`: patch over v7.23.11 - protected database recovery now repairs degraded Brain tables from backup without rolling back newer rows.

Previously in `7.23.11`: patch over v7.23.10 - older installed runtimes can update safely even when `cognitive_paths.py` has not been synced yet.

Previously in `7.23.6`: patch over v7.23.5 - `nexo update` clears safe legacy `cognitive.db` shadows and keeps superseded archives under runtime backup retention.

Previously in `7.23.3`: patch over v7.23.2 - Followup runner skips DONE terminal statuses so already-finished followups do not re-enter executable batches.

Previously in `7.23.2`: patch over v7.23.1 - Desktop-facing version checks refresh stale latest-version cache entries so a just-published Brain update can still appear inside NEXO Desktop.

Previously in `7.23.1`: express patch over v7.23.0 - headless automations no longer hang on silent Claude children, synthetic followup prompts no longer trigger session-end loops, and runtime backups self-prune under a hard cap before creating new large artifacts.

Previously in `7.23.0`: minor release over v7.22.0 - pre-answer routing now consults continuity evidence before visible replies, Memory Observations queue processing converges through a bounded processor, and audits expose saved-but-not-used stores, automation drift, MCP live/catalog gaps, artifact location and transcript coverage.

Previously in `7.22.0`: minor release over v7.21.0 - heartbeat stays fast in Desktop-managed sessions, MCP writes can be accepted through a durable file-backed queue before SQLite commit, Brain exposes compliance state for Desktop gates, and Local Context adds Entity Dossier for open-domain local evidence aggregation.

Previously in `7.21.0`: minor release over v7.20.25 - MCP now starts through a thin compatibility adapter backed by one resident local Runtime Service, reducing duplicate Brain processes and SQLite contention across Claude Code, Codex, Claude Desktop, and NEXO Desktop.

Previously in `7.20.25`: patch release over v7.20.24 — Local Context now uses the pinned local BGE embedding model when available, automatically refreshes old hash embeddings, prioritizes known documents before lower-value files, and treats the Desktop-owned Qwen local-presence model as optional in standalone Brain installs.

Previously in `7.20.24`: patch release over v7.20.23 — Local Memory performance profile writes now tolerate active indexing, retry transient SQLite busy states, and shorten indexer write locks between processed files.

Previously in `7.20.23`: patch release over v7.20.22 — Local Memory status reads the real split sidecar database read-only, reports retryable keyed failures without false zeroes, and keeps Desktop Spanish/English copy localized.

Previously in `7.20.22`: patch release over v7.20.19 — Local Memory moved out of the main Brain database, MCP readiness verifies required tools, and split-aware Desktop backups validate the main DB and Local Memory sidecar separately.

Previously in `7.20.18`: patch release over v7.20.17 — Desktop-managed setup now preserves a completed onboarding flag when Brain is later invoked with the non-interactive `--skip` bootstrap path.

Previously in `7.20.17`: patch release over v7.20.16 — validated DB backups now tolerate tiny live-write growth from the Local Memory indexer while still rejecting real protected-table loss.

Previously in `7.20.16`: patch release over v7.20.15 — packaged updates keep the `local_context` runtime shim importable and rollback code-tree snapshots safely when compatibility directories are symlinks.

Previously in `7.20.15`: patch release over v7.20.14 — Brain update/recovery paths now fail closed when the DB guard is missing or stale, and backup validation rejects any replacement that loses Local Memory tables.

Previously in `7.20.14`: patch release over v7.20.13 — Brain protects Local Memory during update/recovery paths, rotates runtime backup families to the latest 5 entries, keeps first-indexing status stable, and exposes bounded indexing speed profiles for Desktop.

Previously in `7.20.13`: patch release over v7.20.12 — Brain recovery now pauses all known DB writers before restoring `nexo.db`, and Doctor can repair the zero-byte/locked database state that made Desktop Local Memory show zero files.

Previously in `7.20.12`: patch release over v7.20.11 — Local Context now keeps the first index pass separate from live change tracking, persists the current indexing start time, caps compact context payloads for agents, and installs the Windows host scheduler needed to keep WSL indexing alive after reboots.

Previously in `7.20.11`: patch release over v7.20.10 — Local Context now starts from real system volume roots plus mounted/removable/network volumes, filters system/cache/app/product artifacts, and injects relevant local evidence automatically into heartbeat, task-open and pre-action context.

Previously in `7.20.10`: patch release over v7.20.9 — Local Context manual refreshes now reconcile automatic roots every time, so newly mounted disks and upgraded default roots are picked up immediately from Desktop's "comprobar cambios" path.

Previously in `7.20.9`: patch release over v7.20.8 — Local Context scans automatic roots at full operational depth, falls back to crontab when Linux/WSL systemd user timers fail, passes Windows AppData email roots into WSL, and blocks Google API keys before HTML cleaning.

Previously in `7.20.8`: patch release over v7.20.7 — Local Context recognises Windows Mail package roots and Outlook Mac profile roots as bounded local-email sources instead of rejecting them as generic AppData / Group Containers.

Previously in `7.20.7`: patch release over v7.20.6 — Local Context email-root bootstrap is deterministic across CI, WSL and migrated profiles while preserving macOS Mail.app, Windows Outlook, Thunderbird and NEXO email coverage.

Previously in `7.20.6`: patch release over v7.20.5 — Local Context ranks entity matches at chunk level, keeps old entity-matched assets eligible, adds safe local email roots for macOS/Windows/Linux, extracts `.eml`, `.emlx`, `.msg` and NEXO email DB continuity, and exposes local graph relations in pre-action context.

Previously in `7.20.5`: patch release over v7.20.4 — Local Context status reports elapsed indexing time and a defensive ETA while background jobs remain pending.

Previously in `7.20.4`: patch release over v7.20.3 — Local Context now blocks private dotfiles, hidden project folders and secret-bearing content before chunks, embeddings, graph relations or agent context are created.

Previously in `7.20.3`: patch release over v7.20.2 — installer DMG volumes are no longer added as local-memory roots, removed roots purge stale payloads, and doctor can repair removed-root residue.

Previously in `7.20.2`: patch release over v7.20.1 — Local Context now requeues stalled work, reports real macOS/Windows background-service health, records scan errors and preserves Windows drive roots.

Previously in `7.20.1`: patch release over v7.20.0 — the Local Context service now recovers from orphaned locks and mixed-version cycle failures instead of leaving the background index stuck.

Previously in `7.20.0`: minor release over v7.19.0 — the Local Context index now reconciles known files and folders on every service cycle, so created, modified, deleted and newly excluded local files are reflected automatically between full scans.

Previously in `7.19.0`: minor release over v7.18.1 - bundle-managed installations (NEXO Desktop `brain-bundle/`) can now pin Brain to the host application release cycle via `NEXO_BRAIN_AUTO_UPDATE=false`, and the server auto-exits with code 75 on fingerprint mismatch so MCP clients respawn the server with the new code instead of leaving stale `server.py` processes alive.

Previously in `7.18.1`: patch release over v7.18.0 - packaged Brain runtimes now include the `local_context` package, so Desktop Local Memory and `nexo local-context` do not get stuck behind `ModuleNotFoundError` or zero-file status; the local-index service also keeps detecting newly mounted volumes automatically.

Previously in `7.18.0`: minor release over v7.17.8 - Brain adds the Local Context Layer: a local-only background memory index with checkpoints, extraction, graph links, embeddings, MCP/CLI controls, and pre-action evidence for NEXO agents.

Previously in `7.17.8`: patch release over v7.17.7 - standalone `nexo chat` now surfaces macOS Full Disk Access guidance, and Brain clears stale permission state after a live access probe succeeds.

Previously in `7.17.7`: patch release over v7.17.6 - macOS TCC privacy denials now become a guided Full Disk Access permission state instead of an unexplained cron failure, with Desktop-ready status written for user action.

Previously in `7.17.6`: patch release over v7.17.5 - cron health diagnostics are clearer for macOS TCC approval, and catch-up fallback executions now stay visible in `cron_runs` even on legacy or partially migrated runtimes.

Previously in `7.17.5`: patch release over v7.17.4 - `nexo --version --json` now returns machine-readable update status so NEXO Desktop can populate the Updates panel without scraping slower human output.

Previously in `7.17.4`: corrective patch over v7.17.3 - automation runners now keep full NEXO discipline for real background agents while strict JSON children stay clean, and runtime doctor/metrics expose caller coverage and Guardian injection telemetry instead of hiding blind spots.

Previously in `7.17.3`: corrective patch over v7.17.2 - standalone Brain install/update no longer aborts when the Desktop-only `qwen3-0.6b-q4-local-presence` model is not bundled or already cached locally. Required Brain warmups stay strict; only the optional local-presence GGUF now degrades cleanly.

Previously in `7.17.2`: patch release over v7.17.1 - email-monitor now guards its `/tmp/nexo-*` draft buffers before writing, morning-agent closes interrupted/stale briefing claims deterministically, and Codex managed config migrates from the legacy `codex_hooks` flag to `[features].hooks`.

Previously in `7.17.1`: patch release over v7.17.0 - the headless Claude CLI 2.1+ direct-JSON response shape is now handled: when the wrapper `{"result": ...}` is absent and the agent's answer is returned directly, `_extract_claude_telemetry` surfaces the full payload to the caller instead of an empty string. Fixes the daily morning-agent failure with "Morning agent returned invalid JSON output".

Previously in `7.17.0`: minor release over v7.16.3 - the headless runner pre-emptive guard becomes advisory: it surfaces learnings/schemas to the agent and logs to `guard_checks`, but never returns `blocked=True`. The PreToolUse hook is the authoritative gate at write time.

Previously in `7.16.3`: patch release over v7.16.2 - the headless runner guard opts out of the runtime-core blocking rule because actual writes on those paths are already blocked at the PreToolUse layer.

Previously in `7.16.0`: minor release over v7.15.2 - Brain adds Memory Observations v2: evidence-backed event capture, derived observations, update-safe backfill, MCP retrieval, dashboard visibility, and safer refusal when memory lacks evidence.

Previously in `7.15.2`: patch release over v7.15.1 - Brain treats normal Codex startup context reads of calibration and project atlas files as healthy bootstrap activity instead of conditioned-file drift.

Previously in `7.15.1`: patch release over v7.15.0 - Brain drains larger self-audit clusters, bounds hook history with update-time cleanup, filters normal Codex bootstrap reads, routes email-monitor effort by message complexity, and locks morning briefings by local date and recipient.

Previously in `7.15.0`: minor release — Brain unifies sent-email continuity across send paths, moves cognitive recall to multilingual embeddings, forces tagged learnings into context, hardens email loop guards and headless runners, exposes learning creation dates, and adds AUTO-N burst postmortems.

Previously in `7.14.0`: minor release — Brain closes the install/reliability loop with update-path venv recovery, platform-gated wheels, WSL Desktop-managed flag preservation, startup memory authority warnings, legacy MEMORY write blocking, post-action real-world verification, and stale followup triage.

Previously in `7.13.9`: patch release — Brain moves aside an existing managed `.venv` when it was created with unsupported Python <3.10, then recreates it with the supported interpreter prepared by Desktop.

Previously in `7.13.8`: patch release — Brain rejects Python <3.10 during Desktop-managed fresh installs, honors the Python interpreter prepared by Desktop, and fails clearly before dependency resolution if an unsupported Apple Python 3.9 reaches the installer.

Previously in `7.13.7`: patch release — Brain adds an authenticated official protocol-card client (`nexo_card_catalog`, `nexo_card_get`, `nexo_card_match`) so agents can ask the NEXO Desktop backend for the right task protocol at runtime. The protocol corpus stays private on the server; this open-source package ships only the client, tool map, and agent guidance.

Previously in `7.13.6`: patch release — Codex hook sync now renders the managed `PreToolUse` shell/exec_command guard with native Windows `cmd.exe` syntax while preserving the existing POSIX command on macOS/Linux. Result: coordinated Desktop bundles can ship the fixed Brain without changing the Mac/Windows installation contract.

Previously in `7.13.3`: unified release — doctor now repairs orphan personal script metadata and ignores historical `versions/**` snapshots, `nexo update` prunes runtime snapshots older than two back, protocol compliance self-heals missing task-open/change-log/stale-session gaps, headless automation uses bounded timeouts, Guardian false positives are tightened, and Codex CLI config/default checks are release-gated. Result: coordinated Desktop bundles can ship the new Brain without changing the Mac/Windows installation contract.

Previously in `7.12.15`: patch release — same-version packaged updates now still run the safe maintenance path, Deep Sleep clears process locks on shutdown, sent replies are recorded in durable continuity, and personal script schedule-marker drift is surfaced during reconcile. Result: coordinated Desktop bundles can refresh Brain safely without breaking install/update parity on macOS, Windows via WSL, or Linux.

Previously in `7.12.0`: minor release — adds `nexo support-snapshot` for generic local runtime diagnostics and completes the silent-reminder hardening on the live Protocol Enforcer path. The support collector emits one JSON bundle with version/platform metadata, runtime path presence, health-check output, and recent event/operation tails, while map-driven reminders (`nexo_startup`, `nexo_smart_startup`, `nexo_heartbeat`, `nexo_reminders`, `nexo_session_diary_*`, `nexo_stop`, `nexo_task_close`, compaction checkpoint prompts) now say explicitly that silence owns the entire reminder turn.

Previously in `7.11.5`: patch release — Desktop-managed installs now block the standalone dashboard at the same product-mode layer as evolution, so `installation_live`, cron sync, and watchdog no longer disagree about whether `com.nexo.dashboard` should exist. Validation: `125` targeted tests across product-mode, cron sync, and doctor, plus a full pre-release wrapper (`2321 passed, 2 skipped, 1 xfailed, 4 xpassed`).

Previously in `7.11.4`: patch release — packaged runtimes now receive root JSON contracts such as `local_model_manifest.json`, install/update paths sync core crons from `src/crons/manifest.json` instead of depending on a stale JS list, `runner-health-check` is wired into cron/doctor/dashboard instead of writing an unread file, and the watchdog retries failed crons immediately while treating `run_once_on_wake` as catchup-style recovery. Validation: `117` targeted tests across packaged update, cron sync/recovery, dashboard, local models, and runtime update contracts.

Previously in `7.11.3`: patch release — root-cause fix for the `mcp_restart_required` lockup that v7.11.2 only masked at the enforcer layer. `_FINGERPRINT_EXCLUDE_DIRS` in `src/runtime_versioning.py` was missing `"versions"`, so `compute_mcp_runtime_fingerprint()` walked into `core/versions/<old>/**.py` whenever it was called against the live runtime root. `installed_runtime_fingerprint()` (which resolves through `active_runtime_root()` → `core/versions/<active>/`) returned a clean per-snapshot hash, while `prime_process_fingerprint()` (which starts from `Path(__file__).resolve().parent` → live `core/`) accumulated every retained snapshot. The two never matched after the second-ever `nexo update` on a host. Every update wrote `mcp-restart-required.json` and the marker could never be cleared by `_ack_current_client_if_restarted()` because the `installed_fp != process_fp` test always returned `True`. Every non-allowlisted MCP tool (`nexo_reminders`, `nexo_smart_startup`, `nexo_guard_check`, `nexo_task_open`, …) returned `{"error": "mcp_restart_required", "reason": "fingerprint_mismatch"}` indefinitely, even after the operator restarted the client. Adding `"versions"` to `_FINGERPRINT_EXCLUDE_DIRS` restores parity; 21 runtime-fingerprint tests stayed green.

Previously in `7.11.2`: patch release — two reliability fixes in the same family ("components ignoring signals they should respect"): (1) `STUCK CRON REAPER` added to `nexo-watchdog.sh` and (2) the Guardian/Enforcer now honors the `mcp-restart-required` marker. The watchdog reaper closes the v5.8.1 in-flight gap: truly hung wrappers (e.g. headless `claude --bare` blocked on an MCP that flagged `mcp_restart_required`) used to hold their slot for days. The reaper sweeps `cron_runs` rows with `ended_at IS NULL` past `stuck_after_seconds` (per-cron from `manifest.json`, fallback 12h global), SIGTERMs the wrapper (trap closes row at `exit 143`), grace 10s, SIGKILL on survivors. Generous defaults (deep-sleep 8h, sleep/evolution 4h) prevent any v5.8.1 regression. The enforcer gate skips `nexo_*`-mentioning reminders when the marker file is present (cached per-instance, 30s TTL); reminders that don't reference `nexo_*` still fire. 12 new tests; 3 existing watchdog tests + 52 existing enforcer tests stay green.

Previously in `7.11.1`: patch release — caches the runtime fingerprint by `(file_count, size_total, max_mtime)` signature so MCP startup and the per-tool-call `resolve_restart_required` skip the 263-file rehash when nothing on disk changed. ~11× speedup warm path (~40ms → ~3.7ms locally), ~10-20s/day saved across Claude Code / Codex / headless / deep-sleep / cron startups. Cache miss is always safe (falls through to full hash and self-repairs). Default `use_cache=False` keeps `plugins/update.py` on the ground-truth path around `git pull` / `npm update`. Builds on the v7.11.0 runtime fingerprint that gates `mcp-restart-required.json`. Full write-up in [`docs/runtime-fingerprint.md`](docs/runtime-fingerprint.md).

Previously in `7.10.0`: minor release — **removes the LLM proxy override path that 7.9.28 → 7.9.34 introduced**. Background: 7.9.28 added two opt-in files at `~/.nexo/config/llm_endpoint.json` and `~/.nexo/config/auth_provider.json` that let a third-party orchestrator (NEXO Desktop) redirect every Anthropic SDK call from Brain to a custom proxy and resolve the bearer via a local helper, with concrete model names translated to wire aliases (`nexo-max`, `nexo-high`, `nexo-medium`, `nexo-low`, `nexo-mini`) and an `Idempotency-Key` per request. NEXO Desktop's commercial model has changed: Desktop is now a wrapper over the user's own Claude Code subscription (Max / Pro), with a separate Desktop licence. Brain calls go directly to `api.anthropic.com` using the user's existing OAuth (the one stored under `~/.claude/` and consumed by Claude Code spawns) or a plain `ANTHROPIC_API_KEY`. There is no NEXO bearer, no NEXO proxy, no NEXO credit accounting in this codebase. Every proxy symbol is gone from `call_model_raw.py` and `agent_runner.py`; the proxy-specific tests and `docs/api/override-files.md` are removed; any pre-existing override files on disk are simply ignored from this release forward.

Previously in `7.9.34`: two fixes — the email monitor's header parser was dropping any email whose RFC822 headers came back as `email.header.Header` instances (Q-encoded utf-8 / quoted-printable). Every `msg.get(...)` now goes through `_decode_header`, and the failure log is lifted from DEBUG to WARNING. The PreToolUse Guardian gate hardens hard blocks with stderr + exit 2 enforcement so terminal Claude cannot ignore the deny channel mid-tool-loop.

Previously in `7.9.33`: adds ``usedforsecurity=False`` to the SHA-1 call that derives a filesystem-safe checkpoint filename from the email's Message-ID, so Bandit's B324 audit no longer fails the publish workflow on a non-security usage. The ``v7.9.32`` git tag is preserved for traceability but no npm release ever shipped for it; ``nexo-brain@7.9.33`` is the first release that carries the 7.9.32 email-recovery checkpoints.

Previously in `7.9.32`: hardens the email monitor's recovery so emails that fall between Brain releases never end up in a permanent limbo. The periodic ``_recover_unreplied_processed`` sweep now looks back 7 days (was 24h), and every failed worker run persists a per-email checkpoint at ``~/.nexo/nexo-email/checkpoints/`` capturing files touched, last assistant narration, and error. Retry attempts inject that context into the next prompt so a long task (drafting a presentation, multi-step analysis) continues from where the previous attempt died instead of restarting from scratch. Stale checkpoints are pruned automatically after 7 days. 15 new unit tests cover the helpers.

Previously in `7.9.31`: fixes a wire-level bug where ``call_model_raw`` was sending ``stop_sequences=["\n", ".", " "]`` by default, which the current Anthropic Messages API rejects with HTTP 400 ``each stop sequence must contain non-whitespace``. The default is now ``None`` (no ``stop_sequences`` field sent) since ``max_tokens=3`` already caps the yes/no classifier output. A local guard rejects whitespace-only caller values up front so the error shows where the caller is, not as a remote 400. Also removes an internal design document that did not belong in the open-source distribution.

Previously in `7.9.30`: hotfix for a missing ``import sys`` in ``src/agent_runner.py`` that ruff F821 caught in CI and blocked the 7.9.29 publish workflow before any npm artifact shipped. ``nexo-brain@7.9.30`` is the first npm release that carries the 7.9.29 override-path hardening.

Previously in `7.9.29`: hardening pass on the optional LLM endpoint and auth provider override path. The bearer is now passed to the Anthropic SDK via `auth_token` so it lands in the standard `Authorization: Bearer` header (7.9.28 sent it as `X-Api-Key` and any compatible proxy rejected every request with 401). The Brain config directory is resolved on each call instead of cached at import, so LaunchAgent crons that export `NEXO_HOME` via a wrapper now reach the right `~/.nexo/config/`. The `Idempotency-Key` header accepts a caller-provided value so application-level retries reuse the same dedup key. Override mode is strict about its bearer source: if `auth_provider.json` is missing or the helper fails, the call raises `ClassifierUnavailableError` instead of falling back to the operator's real `ANTHROPIC_API_KEY`, which would otherwise leak to the custom proxy as a second header. A new end-to-end test suite drives the real SDK against a local `http.server` and asserts on captured wire headers and body, complementing the SDK-mock unit tests.

Previously in `7.9.28`: optional override files at `~/.nexo/config/llm_endpoint.json` and `~/.nexo/config/auth_provider.json` let third-party orchestrators redirect Brain's Anthropic SDK calls and delegate bearer token resolution to a local command (analogous to git's `credential.helper`). The same redirection is propagated to every CLI child Brain spawns (deep-sleep, evolution, followup-runner, morning-agent, email-monitor, `nexo chat`) by injecting `ANTHROPIC_BASE_URL` and `ANTHROPIC_API_KEY` into the spawned environment, so headless crons reach the proxy too. An `Idempotency-Key` (UUID4 hex) is attached per request for proxy-side dedup of transparent retries within 24h. Brain libre standalone (no override files) hits `api.anthropic.com` directly with `ANTHROPIC_API_KEY` exactly as before.

Previously in `7.9.27`: server startup no longer hangs the MCP `initialize` handshake when legacy followups/reminders still need owner backfill — the synchronous startup migration now runs `--rules-only` and skips the multi-minute `LocalZeroShotClassifier` load, keeping handshake under a few seconds.

Previously in `7.9.26`: headless automation prompts now receive the operator-language contract centrally, so reports, diaries, syntheses, followups, escalations, and Deep Sleep-generated memory text follow calibration even when the underlying template is English.

Previously in `7.9.23`: Desktop lifecycle fallback diaries now enrich sparse lifecycle events from continuity snapshots, so app-exit fallback evidence preserves recent turn context even when the live agent does not answer the injected diary prompt before shutdown.

Previously in `7.9.22`: Desktop lifecycle shutdowns gained an emergency Brain-side fallback diary path, so close/archive/app-exit can preserve title, goal, session ids, and transcript tail even when the live agent does not answer the injected diary prompt before shutdown.

Previously in `7.9.21`: LaunchAgent reload/repair now handles macOS already-loaded races by booting out jobs with modern launchctl forms, falling back to legacy load, and treating an already-loaded job as healthy only when it points at the expected plist.

Previously in `7.9.20`: packaged update/doctor repair now finds `runtime/crons/sync.py`, LaunchAgent PATH includes the managed Claude runtime installed under `~/.nexo/runtime/bootstrap/npm-global/bin`, root runtime backfill includes `claude_cli.py`, and Immune no longer treats the legacy optional `~/.claude-mem/claude-mem.db` as a required database.

Previously in `7.9.19`: runtime doctor now distinguishes real install breakage from tracked in-progress work, interactive Desktop sessions no longer poison automation telemetry scoring, stale filesystem skill rows are pruned during sync, stale protocol debt draining marks rows resolved, and watchdog treats LaunchAgent SIGTERM reloads as supervisor interruptions instead of failures.

Previously in `7.9.18`: packaged client-sync imports now work when `NEXO_HOME` is unset, so `nexo clients sync`, `nexo update`, and runtime doctor bootstrap checks no longer hit the `_user_home` import-order crash.

Previously in `7.9.17`: continuity snapshot idempotency marks its SHA-1 digest as non-security usage, keeping the high-severity Bandit gate green while preserving stable idempotency keys.

Previously in `7.9.5`: patch release that fixes canonical diary confirmation for Desktop: Brain resolves the Desktop/Claude session UUID through NEXO SID aliases before checking `session_diary`, so archive/delete/app-exit can confirm diaries written by `nexo_session_diary_write` under the active `nexo-...` SID. Verification: `pytest tests/test_lifecycle_events.py` (28 passing) plus coordinated Desktop v0.28.6 shutdown/archive/delete/app-exit checks.

Previously in `7.9.4`: patch release that blocks the Brain 7.9.3 + Desktop 0.28.2 diary regression: canonical lifecycle plans now require real `session_diary` evidence (`wait_for_diary_write`) before `stop_session`, and canonical completion is rejected/retryable without that diary row. It also fixes npm CLI onboarding so `nexo-brain --version` and subcommands never launch the wizard when legacy/v2 calibration is already valid, commits setup calibration atomically only after the wizard completes, and adds `nexo-brain warmup-models` so install/update paths predownload the local mDeBERTa/BGE/reranker models. Verification: full Brain pytest (`2189 passed, 3 skipped, 1 xfailed, 5 xpassed`), release-readiness, npm pack dry-run, and coordinated Desktop v0.28.3 checks.

Previously in `7.9.3`: patch release that hardens Brain's canonical lifecycle plan for Desktop close/archive/delete/app-exit diary guarantees: `canonical_actions` now publish the v2 canonical shape (`type` plus `payload.prompt`) while keeping one-release compatibility mirrors (`kind` plus top-level `prompt`) for older Desktop clients. This lets Desktop execute resume → diary prompt → stop with one exact owner per lifecycle event and preserve Brain-side dedupe by event id. Targeted verification: `pytest tests/test_lifecycle_events.py` (25 passing) plus release-readiness after artifact sync.

Previously in `7.9.2`: patch release that completes the Brain semantic-router site migration: the remaining decision callers now route through `semantic_router.route(...)` with named `decision_kind` policies (`r20_constant_change`, `r34_identity_coherence`, `t4_r15`, `t4_r23e`, `t4_r23f`, `t4_r23h`, `followup_operator_attention`, `drive_signal_type`, `drive_area`, `reply_event_type`, `query_intent`, and `sentiment_intent`). Brain now owns model choice, thresholds, and fallback behaviour centrally instead of each caller carrying its own classifier policy. The patch also fixes packaged headless Guardian map loading: `enforcement_engine` and `agent_runner` now check the installed core directory (`~/.nexo/core/tool-enforcement-map.json`) so followup-runner, morning-agent, digest, and email-monitor load the map instead of falling back to unguarded subprocess execution. Targeted verification: 100 semantic/router/enforcer tests, 125 Drive/cognitive/productization tests, and release-readiness passing.

Previously in `7.9.0`: minor release that ships the foundation of the semantic stack (router + reasoner + CLI) under the ONEPASS LLM Coverage plan, plus two product-bug fixes observed in the wild on 2026-04-23. New `src/semantic_router.py` exposes 18 named `decision_kinds` (13 textual + 5 code-aware) with a per-kind policy table and the layer chain `fast_local → semantic_reasoner → remote_fallback`. New `src/semantic_reasoner.py` adds Mode A (`multipass_local`: reuses the mDeBERTa pin with three prompt-perturbed passes + majority vote + 0.75 floor) and Mode B (`cached_llm`: wrapper over `call_model_raw` with a pid+uuid atomic-write 24h-TTL disk cache at `~/.nexo/runtime/operations/semantic-reasoner-cache.json`, SHA-256 keyed by `decision_kind` + normalized input, LRU-bounded at 2000 entries, corrupt entries dropped on read). New `scripts/semantic-classify.py` JSON-in JSON-out CLI lets external MCP clients (including the closed-source NEXO Desktop companion) query Brain as the single semantic authority. New `NEXO_SEMANTIC_REASONER` kill switch (`0`/`off`/`false`/`no`/`disable`/`disabled`) honours the plan mandate for a runtime opt-out separate from `NEXO_LOCAL_CLASSIFIER`. Bug fixes: `bin/nexo-brain.js` upgrade flow now copies `templates/` root the same way fresh install and same-version refresh already did (Maria iMac 7.1.10→7.8.1 upgrade had lost 27 core-prompts templates and broken post-update import verification); and `tool-enforcement-map.json` `nexo_startup.enforcement.inject_prompt` now instructs the model to preload the 13 `mcp__nexo__*` protocol tools via `ToolSearch` before calling `nexo_startup` when the host MCP client defers tool schemas (Claude Code with many MCPs installed). Audit-driven hardening: router/reasoner defensively use `getattr` over the `call_model_raw` module and add a trailing `except Exception` so provider errors degrade with `remote_error` instead of propagating; cache writes use pid+uuid tmp + `fsync` + `os.replace` to survive concurrent writers; `NEXO_SEMANTIC_REASONER_TTL` parse tolerates malformed values. Tests: +50 (22 router, 20 reasoner, 8 CLI). Per-site migration of existing callers (`session_end_intent`, `r14`, `r16`, `r17`, `r20`, `r34`, T4 gates, `tools_drive`, `nexo-followup-runner`) is explicitly deferred to follow-up patch releases and tracked as `NF-SEMANTIC-ROUTER-SITE-MIGRATION`; nothing in this release changes the behaviour of the existing callers. Companion coordinated release: NEXO Desktop v0.28.0.

Previously in `7.8.2`: patch release that fixes the compact-hook observability gap Francisco flagged after v7.8.1: `hook_runs.session_id` was empty for 7 out of 8 recent compaction rows (and when populated it stored the raw Claude Code token instead of the NEXO sid), so per-session queries over `hook_runs` for compact events could not be joined back to the NEXO session that actually compacted. v7.8.2 adds `src/hooks/compact_session_resolver.py` with `resolve_nexo_sid(claude_session_id)`, which walks the same rails the shell already uses: `sessions.claude_session_id` match, then `session_claude_aliases.claude_session_id` (most recent `last_seen` wins), then the per-conversation sidecar under `runtime/data/compacting/<safe-claude-id>.txt`, then the legacy global sidecar for single-conversation setups. `src/hooks/pre_compact.py` and `src/hooks/post_compact.py` now call the resolver and store the real NEXO sid in `hook_runs.session_id`; both wrappers also stash `{claude_session_id, sid_source}` in `hook_runs.metadata` so "why is this row still empty?" has a one-query answer. Nine new tests in `tests/test_hook_runs_compact_sid_resolution.py` pin the five resolver rails (sessions / alias / sidecar / legacy / none), malformed-sidecar rejection, the pre- and post-compact wrapper end-to-end paths, and the empty-state wrapper rail so a clean audit trail is written even when nothing resolves. No Desktop bump.

Previously in `7.8.1`: patch release that closed the last compaction-continuity gap Francisco flagged after v7.8.0: `pre-compact.sh` Layer 2 emergency auto-diary and Layer 3 `compaction_memory.record_auto_flush` now use the exact `TARGET_SID` resolved from `CLAUDE_SESSION_ID` instead of falling back to `ORDER BY last_update_epoch DESC LIMIT 1` ("latest active session"). In multi-conversation Desktop that fallback routinely wrote the emergency diary against the wrong conversation even though the main restore path was already exact-SID in v7.8.0. `last_diary_ts` is also scoped by `session_id` now. Fail-closed when no `CLAUDE_SESSION_ID` resolves. New behavioural tests drive the real shell script with two sessions in the DB to pin the invariant. Fixed a latent bash-escape bug in `pre-compact.sh` where a double-quoted string inside a Python comment silently closed the `python3 -c "..."` argument early — caught by adding the behavioural tests. Pytest 2092 passing (+2 new behavioural). No Desktop bump.

Previously in `7.8.0`: minor release that closed the PostCompact continuity work Francisco requested after v7.7: `src/hooks/post_compact.py` is a real registered hook (part of the canonical 9-hook set, was 8), `pre-compact.sh` resolves the exact NEXO SID from `CLAUDE_SESSION_ID` instead of falling back to "latest active session" (that was actively wrong in multi-conversation Desktop), the sidecar moves from `/tmp` to `$NEXO_HOME/runtime/data/compacting-sid.txt` so two concurrent compactions on two conversations cannot race on `/tmp`, `post-compact.sh` removes its "latest checkpoint" fallback (fail-closed to a diagnostic systemMessage instead of restoring the wrong conversation), and the hook cross-checks the sidecar SID against the env-resolved one so a "SID mismatch" is logged as such. Pre- and post-compact now emit NDJSON events the engine drains on every periodic tick via `_consume_pending_hook_events()`; the queue file is truncated after read so an event never fires twice. A new contract test (`tests/test_v78_compaction_continuity.py`) pins 11 invariants across ten rails including the hook registration, the exact-SID resolution path, fail-closed behaviour, and that `compaction_count` only increments on real restore. Pytest 2086 passing (+16 vs v7.7). No Desktop bump — v0.27.0 continues to ship.

Previously in `7.7.0`: minor release that closed the six gaps left partial after v7.6.0's constructor-guardian-90 pass 1 (autonomous detector for `multi_step_task_detected`, R16 vocabulary expansion, R_CATALOG extended to plain Edit/Write, new `R_PRIMITIVE_CHOICE` rule, `R11_plugin_load_pre_inventory` hardened, 12 new contract tests). Post-review hotfix on the same release wired `task_open` rearm properly (discarded from `tools_called` + per-instance pin cleared on `task_close`), added live `on_event` triggers in R14 and R16, and called `on_tool_call_before` before `on_tool_call` in `run_with_enforcement` so before_tool rules fire in Brain the same way Desktop fires `onBeforeToolCall`.

Previously in `7.6.0`: minor release that closed the drift between `tool-enforcement-map.json` v2.2 and the two enforcement engines (Brain Python + Desktop JS), added per-instance `after_tool` satisfaction, tightened `learning_add` grace to 0 and `task_open` threshold to 4/must, hardened R15/R17/R22/R_CATALOG from soft to hard, and raised R34 from shadow to soft.

Previously in `7.5.0`: minor release that promoted `nexo_lifecycle_event` from ledger + reconciliation authority to **canonical authority of session-end**. Brain now owns the prompt, the sequence, and the timing of diary+stop; Desktop v0.25.0 (closed-source companion) is the conduit that executes Brain's plan against the live Claude process. The new 2-call contract — `nexo_lifecycle_event` returns a versioned `canonical_plan` (resume_session → inject_prompt → stop_session, with stable ids and per-action timeouts) and `nexo_lifecycle_complete_canonical` confirms execution with a per-action results array — replaces polling with explicit acknowledgement. `canonical_plan_id` is deterministic: `sha256(event_id + "|v" + plan_version)[:24]`, so retries reuse the same id. Migration m52 extends `lifecycle_events` with six `canonical_*` columns plus an index; pre-v7.5 rows simply carry NULL. `session_diary` is the dedupe key on re-delivery: if Desktop crashes between executing the inject and sending the complete call, the next `nexo_lifecycle_event` for the same `event_id` checks for a diary written after `canonical_dispatched_at`; if one exists, Brain short-circuits to `already_processed` and refuses to re-dispatch. The seven explicit `delivery_status` values (`accepted`, `processed`, `canonical_pending`, `canonical_done`, `already_processed`, `retryable_error`, `rejected`) give the pipeline a diffable state machine. `switch` and `window-close` stay observational (no plan ever issued, even with a live `session_id`). `nexo lifecycle record` now returns exit code 0 for `canonical_pending`; older wrappers that treated it as an error are incompatible with v7.5. MCP tool count: 262 → 263.

Previously in `7.4.1`: patch release correcting the over-promise in v7.4.0's release notes and locking in the exact role of `nexo_lifecycle_event` as a ledger + reconciliation authority — NOT the canonical executor of diary+stop, which lived in Desktop. That responsibility moved to Brain in v7.5.

Previously in `7.2.0`: minor release consolidating three parallel workstreams into a single Guardian-active-by-default train. Block K roadmap closure (G1 enforcer active, G3 SSH remote-write detector, `src/guardian_runtime_config.py` resolver, `_persist_guardian_hard_defaults` during `nexo update`). F0.6 hardening wave (`nexo rollback f06` CLI, `src/scripts/prune_runtime_backups.py` promoted to core, `docs/f06-layout-contract.md`, three new doctor boot-tier checks, `scripts/nexo-migrate-nora.sh` + `scripts/f0-safe-apply-remote.sh` idempotent migration). Adaptive weights flipped from "14-day calendar wait" to "14 days OR (≥200 samples AND ≥2 days)" with auto-promotion during `nexo update`. Small-fixes batch: R34 `bool("unknown")==True` fix, `classify_scripts_dir` dedup, B10 module-level path constants lazy-evaluated, schedule override audit log, `scripts/pre-release-verify.sh` + `docs/release-discipline.md`, pre-commit hook that blocks commits when `tool-enforcement-map.json` drifts from `src/plugins/`.

Previously in `7.1.10`: follow-up over v7.1.8 that shipped two rescue batches of WIP stashed aside during the v7.1.8 release window. First rescue: `src/autonomy_mandate.py` expanded the Spanish-language mandate-detection phrase set (do everything / do not stop / you are in charge / I leave you in charge / keep going / do the full plan), added three honest flags on `MandateState` (`execute_until_blocker`, `suppress_mid_task_menus`, `revalidate_after_compaction`) with session filtering, wired post/pre-compact hooks that read those flags, surfaced them through protocol/workflow handlers and session payload, and introduced the new `src/checkpoint_policy.py` module with tests. Second rescue: `scripts/verify_release_readiness.py` gained a smoke-artifact contract pass that validates `release-contracts/smoke/v<version>.json` before any tag push, the release-final audit skill references the new contract, `src/hook_guardrails.py` + `src/hooks/post_tool_use.py` refine the post-tool protocol reminder path with a new contract test, and a couple of core prompts (task-close evidence, r14 correction learning) got wording polish.

Previously in `7.1.8`: batch release over v7.1.7 consolidating the Block K Guardian/Enforcer roadmap (auto-drain of stale `protocol_debt` rows, destructive-command pre-tool gate, `guard_check`-required gate, inline guard ack on `nexo_task_open`, Guardian Health in the morning briefing) with Block D hardcode cleanup (classifier-backed `backfill_task_owner`, migration v50 supersedes the duplicate NEXO-product learning pair, new semantic-hardcodes audit) and Block E product guards (LaunchAgent plist protection, agent-name fallbacks no longer leak the product identity, `francisco_emails` removed from the email-config dict export, `runner-health-check.py` + `nexo_personal_automation.py` promoted from personal to core).

Previously in `7.0.1`: hotfix over v7.0.0 (db._core.DB_PATH was only caller still hardcoded to legacy ~/.nexo/data/nexo.db; every shared-DB command silently returned empty results post-migration). Previously in `7.0.0`: **BREAKING — Consolidated Plan Phase F0.6**: physical separation of the runtime tree into `~/.nexo/{core,personal,runtime}/`. The flat layout (`~/.nexo/scripts/`, `brain/`, `data/`, `operations/`, ...) is gone. Operators on v6.x are auto-migrated on first `nexo update`; fresh installs land directly in the new tree. New `paths.py` helpers are transition-aware.

Previously in `6.5.0`: Consolidated Plan Phase F0.2: operators can now `nexo scripts enable|disable|status <name>` any personal automation. The cron wrapper honours the flag at every tick (`exit 0` with `summary='[disabled]'` while the LaunchAgent stays loaded). The companion NEXO Desktop client (a closed-source product, distributed separately) wires the same toggle into its Automations panel. See [CHANGELOG](CHANGELOG.md) for the full diff.

> **About NEXO Desktop.** NEXO Desktop is a separate closed-source companion app distributed at [nexo-desktop.com](https://nexo-desktop.com/) — its source does not live in this repo. When release notes mention Desktop they describe a coordinated client release that consumes the Brain's CLI / MCP contract; the Brain itself is fully usable on its own (terminal, Codex, Claude Code, or any MCP client). If you want the product edition rather than the open-source Brain alone, contact `info@wazion.com` and ask about NEXO Desktop.


Previously in `6.4.0`: Consolidated Plan Phase F1 — multi-tenant email accounts (`email_accounts` table, `nexo email setup` interactive wizard, `nexo email add --password-stdin --json` for machine consumers, idempotent migrator from legacy `~/.nexo/nexo-email/config.json`). On post-F0.6 installs that legacy-looking path is only a compatibility alias/shim into `~/.nexo/runtime/nexo-email/config.json`; it should never be treated as a second source of truth.

Previously in `6.3.1`: privacy hotfix over v6.3.0. The nightly auditor caught that `src/presets/entities_universal.json` in v6.3.0 shipped operator-specific `vhost_mapping` entries (private IPs, hostnames, tenant names). v6.3.1 pulls those out into `src/presets/entities_local.sample.json` (template) + `.gitignore`'d `~/.nexo/brain/presets/entities_local.json` (operator copy), and the installer drops the sample at `nexo init`. No behaviour change on the Guardian side.

Previously in `6.3.0` — Consolidated Plan wave 2, coordinated with NEXO Desktop v0.18.0. Closes the remaining Guardian roadmap items that do not require an invasive structure migration: extended `cognitive_sentiment` shape (is_correction/valence/intent), extended `entities` schema, 21 labelled rule fixtures with R13 spike gates, Phase F telemetry loops + Deep Sleep phase, pinned local zero-shot classifier skeleton (mDeBERTa), hook respects `NEXO_MIGRATING=1`, `origin` column on `personal_scripts`, and the T4 LLM gate wrapping R15/R23e/R23f/R23h (byte-parity Py ↔ JS). Two pre-release auditors flagged a CRITICAL in the first JS wire (method-name + async mismatch) and a HIGH (classifier bool conflated "no" with "unparseable"); both corrected with regression tests before merge.

Previously in `6.1.1`: small fix to `nexo --help` so the `Latest: vX` line reliably appears when NEXO Desktop invokes the CLI via subprocess — unblocks the Desktop Brain auto-update banner that previously couldn't parse the version delta. No behaviour change for interactive terminal users; the 6-hour registry cache still rate-limits network calls. Bundles all v6.1.0 Protocol Enforcer Phase 2 + multi-claude-sid hotfix content.

Previously in `6.0.2`: adds the reserved caller prefix `personal/*` so scripts living in `~/.nexo/scripts/` can invoke the automation backend with their own caller id without editing `src/resonance_map.py`. New kwarg `tier` (`"maximo"` / `"alto"` / `"medio"` / `"bajo"`) on `run_automation_prompt`, `run_automation_interactive`, `nexo_helper.run_automation_text`, `nexo_helper.run_automation_json`, and `nexo-agent-run.py --tier`. Precedence for `personal/*` callers: explicit `tier=` → explicit `reasoning_effort=` → `calibration.preferences.default_resonance` → `DEFAULT_RESONANCE` (`alto`). Registered callers keep their behaviour unchanged. New guide: [`docs/personal-scripts-guide.md`](docs/personal-scripts-guide.md).

Previously in `6.0.1`: hotfix on top of the 6.0.0 release. `protocol_settings.py` now treats the process as interactive when **either** stdin+stdout are TTYs **or** `NEXO_INTERACTIVE=1` is exported — closes the gap where NEXO Desktop 0.12.0 spawned `claude` through pipes and Brain fell back to `lenient` even with a human in the loop. The `PostToolUse` hook also gains an inbox autodetect stage: when the session has unread `nexo_send` messages and has gone 60s+ without a heartbeat, it emits a `systemMessage` asking the agent to run `nexo_heartbeat` and consume them. Rate-limited to one reminder per minute per SID (new `hook_inbox_reminders` table, migration m42). Added `sessions.last_heartbeat_ts`, stamped by every successful heartbeat. `NEXO_INTERACTIVE` is an internal Brain↔Electron contract — not user-facing, not a resurrection of the removed `NEXO_PROTOCOL_STRICTNESS`.

Previously in `6.0.0`: **BREAKING** tier-only setup. Onboarding asks for one resonance tier (`maximo`/`alto`/`medio`/`bajo`) and that choice drives every backend via `src/resonance_tiers.json`; the per-backend model/effort prompts are gone and the legacy `client_runtime_profiles.{claude_code,codex}.{model,reasoning_effort}` are silently purged from `schedule.json` on upgrade. Protocol strictness is no longer configurable — interactive TTY sessions run `strict`, non-TTY (crons, pipes, tests) run `lenient`; `NEXO_PROTOCOL_STRICTNESS` env, `preferences.protocol_strictness`, and the `default/normal/off/warn/soft` aliases are all removed. `preferences.show_pending_at_start` moves to NEXO Desktop's electron-store. The seven core hooks are now unified behind `src/hooks/manifest.json` (plugin and npm modes read the same file), two new hooks ship (`Notification` for live-session activity and `SubagentStop` for auto-closing stale `protocol_tasks`), and `auto_capture.py` is wired to both `UserPromptSubmit` and `PostToolUse` with a persistent 1h dedup table plus an automatic `nexo_learning_add` on correction matches. `~/.nexo/hooks_status.json` is published after every `registerAllCoreHooks()` so NEXO Desktop ≥0.12.0 can render Hooks activos X/Y. New `nexo-brain --skip` flag aliases `--yes`/`--defaults`. Full suite 1057 passed, 1 skipped.

Previously in `5.10.2`: auto-bootstraps `brain/profile.json` from `brain/calibration.json` on `nexo update` when the profile file is missing, empty, or corrupt AND calibration carries at least one of `meta.role`, `meta.technical_level`, `name`, `language`. NEXO Desktop's *Preferencias → Avanzado* tab used to render an empty `{}` for that block when the onboarding flow had been interrupted; now it either shows the seeded profile or a friendly explanation of what each file is for, paired with Desktop `v0.11.2` which adds header descriptions to both JSON blocks. Never overwrites a populated profile, never raises, idempotent. Also fixes a latent host-filesystem leak in `test_user_facing_caller_with_no_user_default_uses_alto` exposed by the v5.10.1 migration.

Previously in `5.10.1`: silent, one-shot migration that recovers legacy `reasoning_effort="max"` (written by `nexo preferences --reasoning-effort max` before v5.9.0) into the new `preferences.default_resonance` map — any user who had configured `max` before v5.9.0 and never touched the new selector was silently falling back to `DEFAULT_RESONANCE="alto"` on interactive calls since the v5.10.0 update. `_run_runtime_post_sync()` runs `_migrate_effort_to_resonance()` exactly once: `max→maximo`, `xhigh→alto`, `high→medio`, `medium→bajo`. No-op when calibration or schedule already declares an explicit `default_resonance`; idempotent; conservative; never raises.

Previously in `5.10.0`: fixes the deep-sleep extract bloat that made Session 1 take ~57 minutes on some installs (new `bare_mode` on `run_automation_prompt` wires `claude --bare` for JSON-only extractor callers — ~4.3× faster per child, sourced from `ANTHROPIC_API_KEY` env or `~/.claude/anthropic-api-key.txt`). `caller=` is now **mandatory** on `run_automation_prompt` — no silent fallback; every automation subprocess traces back to a registered caller with a deliberate tier. Five personal scripts (`personal/email-monitor`, `personal/github-monitor`, `personal/post-x`, `personal/followup-runner`, `personal/orchestrator-v2`) joined the resonance map with tiers picked per caller based on what each one does. gbp/* marketing posts bumped from `medio` to `alto` (public-facing copy, quality first over speed). 65 legacy protocol debts bulk-resolved as part of the audit — the patterns that generated them are structurally closed by mandatory `caller=` + unified session log + bare_mode.

Previously in `5.9.1`: adds `default_resonance` to `brain/calibration.json` via the Desktop-facing schema (`nexo schema --json`), so NEXO Desktop's Preferences dialog renders a localized resonance selector automatically — no Desktop release needed. `resolve_tier_for_caller` reads calibration first and falls back to the legacy `schedule.json` location. `nexo preferences --resonance` writes both. The UI control only affects interactive sessions (`nexo chat`, Desktop new conversation, interactive `nexo update`); crons and background processes stay pinned per caller in `resonance_map.py`.

Previously in `5.9.0`: every Claude/Codex invocation now flows through a central **resonance map** and a **unified session log**. Four tiers (`MAXIMO` / `ALTO` / `MEDIO` / `BAJO`) each resolve to a concrete `(model, reasoning_effort)` pair per backend. User-facing callers (`nexo chat`, Desktop new conversation, interactive `nexo update`) honour the user's `default_resonance` preference; system-owned callers (deep-sleep, evolution, catchup, GBP posts, …) run at a fixed tier chosen per caller in `src/resonance_map.py` — the user's preference never downgrades a cron we decided needs `MAXIMO`. Unknown callers raise `UnregisteredCallerError`. Migration #41 adds `caller`, `session_type`, `started_at`, `ended_at`, `pid`, `resonance_tier` to `automation_runs`; interactive sessions record a row at spawn (with `ended_at=NULL`) and update it on close, so the Brain now has a single source of truth for every Claude/Codex call regardless of origin. New `nexo preferences --resonance` CLI. New MCP tools `nexo_session_log_create` / `nexo_session_log_close` let NEXO Desktop (which spawns `claude` directly from its TypeScript process) feed the same log.

Previously in `5.8.2`: the Brain core no longer auto-classifies `followups` and `reminders` on behalf of agents. v5.8.0's `classify_task()` heuristic (NEXO-specific ID prefixes `NF-PROTOCOL-*` / `NF-DS-*` / `NF-AUDIT-*`, Spanish user-verb patterns, and agent keywords such as monitor or daily audit) was fine for NEXO's own DB but bled convention into every third-party agent plugged into the shared Brain. The core now persists `internal=0` and `owner=NULL` when the caller omits them, and clients that want automatic classification (NEXO Desktop does, via its `_legacyClassifyOwner` helpers) compute it themselves and pass the result. Migration #40 keeps the columns + indexes; rows already backfilled by v5.8.0 keep their values. `normalise_owner` still explicitly rejects the string `"nexo"` so legacy hardcoding cannot sneak back in.

Previously in `5.8.1`: closes a self-reinforcing `launchctl kickstart -k` loop in the watchdog that wedged deep-sleep Phase 2 between 2026-04-14 and 2026-04-17. The cron wrapper now INSERTs an in-flight row (`ended_at=NULL`) at start and traps SIGTERM/INT/HUP to close it with `exit_code=143` instead of vanishing from `cron_runs`. The watchdog interprets in-flight rows as "currently running" and only re-executes after verifying the worker process is dead. `extract.py` classifies CLI failures into transient (`overloaded_error`, rate-limit, timeout, signal — retried next run) and deterministic (skipped after `MAX_POISON_ATTEMPTS`), and passes a slim shared-context (200 head lines + metadata) instead of the full 400+ KB dump. A new `auto_update._heal_deep_sleep_runtime()` repairs existing installs silently on the next `nexo update`: poisoned checkpoints, stale locks, dangling `cron_runs` rows, and bloated `.watchdog-fails` counters.

Previously in `5.8.0`: first-class `internal` and `owner` columns on `followups` and `reminders`. Migration #40 adds both fields with an idempotent one-shot backfill, so the "who does this task belong to?" classification moves from client-side regex (Desktop) to persistent storage every MCP client shares. Taxonomy is intentionally generic — `owner in {user, waiting, agent, shared}` — so third-party agents plugging into the shared Brain can render whatever assistant label they carry without inheriting NEXO branding. `nexo_reminder_create`, `nexo_reminder_update`, `nexo_followup_create`, and `nexo_followup_update` gain optional `internal` and `owner` parameters that win over the default heuristic.

Previously in `5.7.0`: `nexo update` now keeps Claude Code and Codex CLIs in lockstep with NEXO Brain itself. When the global `@anthropic-ai/claude-code` or `@openai/codex` packages are installed, the updater checks the npm registry and runs `npm install -g <pkg>@latest` in-line — so the terminal boot model stays aligned with the settings NEXO already wrote to `~/.claude/settings.json`. Packages the operator never installed are skipped silently. Pass `nexo update --no-clis` to keep the terminal CLIs pinned.

Previously in `5.6.1`: update-path hardening — 0-byte `.db` orphans from interrupted installs are now purged from `~/.nexo/` and `~/.nexo/data/` before the pre-update backup, and `sync_claude_code_model()` propagates the NEXO-recommended model into `~/.claude/settings.json` whenever `heal_runtime_profiles()` migrates the `claude_code` default.

Previously in `5.5.5`: data-loss guardrails + automatic self-heal. The updater now refuses to capture an already-wiped `nexo.db` into a `pre-update-*` snapshot (validated `sqlite3.backup` + pre-flight wipe guard + post-migration row-count gate), and an auto-heal restores `data/nexo.db` from the newest hourly backup on the next server boot when a wipe is detected. New `nexo recover` CLI + `nexo_recover` MCP tool.

Previously in `5.5.4`: Deep Sleep no longer blocks on unparseable sessions — reduced retries, added a JSON escape hatch, and unified the automation subprocess timeout to 3h across all scripts via a single shared constant.

Previously in `5.5.3`: CLAUDE.md CORE teaches the model to trust the Protocol Enforcer, so aligned backends stop rejecting heartbeat, diary, and checkpoint injections as suspected prompt injection.

Start here:
- [5-minute quickstart](docs/quickstart-5-minutes.md)
- [Workflow quickstart](docs/workflows-quickstart.md)
- [Runtime templates](docs/runtime-templates.md)
- [Recent memory fallbacks + live system catalog](docs/recent-memory-fallbacks-and-system-catalog.md)
- [Supported client guides](docs/integrations/cursor.md)
- [Docker setup](docs/docker-setup.md)
- [Architecture visuals](docs/architecture-visuals.md)
- [Memory classes](docs/memory-classes.md)
- [Session portability](docs/session-portability.md)
- [Python SDK](docs/sdk-python.md)
- [Reference verticals](docs/reference-verticals.md)
- [Measured compare scorecard](compare/README.md)
- [Memory benchmark harness](benchmarks/README.md)
- [Public contribution guide](docs/public-contribution.md)

Every time you close a session, everything is lost. Your agent doesn't remember yesterday's decisions, repeats the same mistakes, and starts from zero. NEXO Brain fixes this with a cognitive architecture modeled after how human memory actually works.

## Shared Brain Across Clients

Shared brain is now the baseline:

- **Claude Code** remains the recommended path because it still has the deepest hook integration and the most battle-tested headless automation surface.
- **Codex** is supported both as an interactive terminal client and as the background automation backend.
- **Claude Desktop** can point at the same local brain through MCP.

That means NEXO now manages not only the shared runtime and MCP wiring, but also the startup layer around it:

- `nexo chat` opens the configured client instead of assuming Claude Code forever.
- Claude Code and Codex both get managed bootstrap files:
  - `~/.claude/CLAUDE.md`
  - `~/.codex/AGENTS.md`
- Those files now use an explicit **`CORE` / `USER`** contract, so NEXO can update product rules in `CORE` while preserving operator-specific instructions in `USER`.
- For Codex specifically, `nexo chat` and Codex headless automation inject the current bootstrap explicitly, so Codex starts as NEXO even when plain global Codex startup is inconsistent about global instructions.
- Deep Sleep now reads both Claude Code and Codex transcript stores, so overnight analysis still works even when the user spends the day in Codex.

Versions `2.6.14` through `2.7.0` established the practical shared-brain baseline: managed Claude/Codex bootstrap, Codex config sync, transcript-aware Deep Sleep, 60-day long-horizon analysis, weekly/monthly summary artifacts, retrieval auto-mode, and the first measured engineering loop.

Versions `3.0.0` and `3.0.1` close the next execution gap:

- protocol discipline is now a runtime contract, not just instructions:
  - `nexo_task_open`
  - `nexo_task_close`
  - persistent `protocol_debt`
  - enforceable `Cortex` gates
- durable execution is now first-class:
  - resumable workflow runs
  - checkpoints
  - replay
  - retries
  - durable goals
- conditioned learnings on critical files are now real guardrails across Claude hooks, Codex transcript audits, and headless automation prompts
- repair/correction work now routes through canonical learning capture instead of depending on the model to remember to document after the fact
- runtime truth is stricter:
  - no more healthy-looking warning storms
  - no more silent Deep Sleep schema drift
  - keep-alive jobs report alive/degraded/duplicated honestly
- public proof is stronger:
  - measured compare scorecard
  - external and internal ablations
  - `cost_per_solved_task`
  - SDK/API/quickstart surface

Versions `3.1.7` through `3.2.0` close the recent-memory gap:

- recent operational continuity is now first-class through `hot context` and `recent events`
- the runtime can build a reusable pre-action bundle instead of reconstructing the last few hours from diaries and durable recall only
- when even that misses, NEXO now exposes raw transcript fallback tools for Claude Code and Codex session stores
- NEXO can now inspect itself through a live system catalog derived from canonical sources instead of relying only on stale docs or operator memory

Version `5.3.11` hardens protocol and Cortex contracts: malformed `outcome`, `task_type`, and `impact_level` values now fail explicitly instead of being coerced into other valid states, so persisted task history, debt, hot context, and decision telemetry stay faithful to what the caller actually asked for. Version `5.3.10` tightened the packaged-runtime truth layer again: installs and updates now keep `~/.nexo/package.json` aligned with the published npm package so runtime metadata and doctor evidence no longer drift to an old version, `nexo doctor --tier deep` treats a missing `self-audit-summary.json` as a pending bootstrap artifact when the runtime was just installed or updated instead of reporting a false degradation, weekly Evolution now asks for explicit `dimension_scores` / `score_evidence` so telemetry can persist instead of staying blank, and daily synthesis only ingests `update-last-summary.json` when it carries actionable runtime signals. Version `5.3.9` is the packaged core-artifact manifest heal for `5.3.8`: packaged updates now rebuild `runtime-core-artifacts.json` from the canonical npm package `src/` tree instead of scanning the live `~/.nexo/scripts` directory, script classification prefers that canonical packaged source when available, and runtime doctor syncs personal scripts before LaunchAgent inventory so personal automations recover cleanly instead of being mistaken for unknown core drift. Version `5.3.8` was the immediate packaged-migration hotfix for `5.3.7`: the installer/runtime migrator now discovers all top-level runtime Python modules from `src/` dynamically instead of relying on a manual allowlist, so new product surfaces like `nexo export` / `nexo import` actually arrive in `~/.nexo` after update instead of being present only in the published npm tarball. Version `5.3.7` closed the remaining packaged-runtime happy-path gap and finally exposed portable user-data migration commands: packaged `nexo update` now self-heals cron definitions and LaunchAgents after a successful npm bump, new `nexo export` / `nexo import` commands move operator data as a safe bundle instead of leaving that flow implicit, and runtime doctor now distinguishes tracked historical Codex drift from an actually broken runtime so cleaned installs stop staying red for stale transcript debt alone. Version `5.3.6` hardened the Claude Code bootstrap path and related runtime hygiene: managed client sync now writes the NEXO MCP server where current Claude Code actually reads it (`~/.claude.json`), script classification is stricter about core-vs-personal runtime artifacts, schedule status distinguishes genuinely running jobs from broken ones, and retroactive learnings stop opening keyword-only false positives outside their declared `applies_to` scope. Version `5.3.5` already keeps CLI version visibility honest right after `nexo update`: if the cached npm version lags behind the runtime you just installed, `nexo` / `nexo chat` now clamp `Latest` to the installed version and refresh the cache instead of showing a stale older release. Version `5.3.4` already cleaned up legacy core alias leakage and added the version-status banner. Version `5.3.3` closed the remaining packaged-runtime doctor mismatch: the built-in hourly backup helper is now inventoried as a core LaunchAgent, so clean installs no longer get a false unknown-LaunchAgent warning. Version `5.3.2` already hardened the runtime boundary by persisting which runtime scripts/hooks are core product artifacts, keeping `nexo scripts` from mixing those into the personal bucket, and migrating the legacy Claude Code heartbeat wrappers into managed core hooks.

Version `5.3.1` normalizes packaged npm installs so they behave like packaged npm installs: `nexo update` now keeps the runtime anchored to `~/.nexo`, refreshes packaged bootstrap/client artifacts after upgrade, avoids repo-only release-artifact drift in installed runtimes, and keeps personal scripts on the canonical packaged path.

Version `5.3.0` adds `nexo uninstall` — a CLI command that cleanly separates runtime from user data. It stops all crons, removes the MCP server config, and preserves databases, learnings, and personal scripts for safe reinstall.

Version `5.2.1` fixes the Deep Sleep datetime regression and closes the decision-to-outcome feedback gap:

- `_parse_any_datetime` in `apply_findings.py` now strips timezone info before comparison, fixing the offset-aware/offset-naive crash that was breaking Deep Sleep verification work.
- `cortex_decide()` now auto-creates a `decision_outcome` when none is linked yet, so the outcome-checker cron can verify real decisions instead of leaving the loop open.

Version `5.2.0` closes two focused gaps in the Cortex layer that were left open by the v5.1 audit — the high-stakes response-contract detector was English-only, and the `nexo-cortex-cycle` cron was writing a quality snapshot that no reader ever consumed:

- `HIGH_STAKES_KEYWORDS_ES` adds ~45 Spanish-language keywords to the high-stakes detector with accented and unaccented variants, so a goal written in Spanish trips the same gate as its English twin.
- `NEGATION_PATTERNS` suppresses false positives when the user explicitly disclaims touching the sensitive area, including both Spanish and English boundary statements. The raw keyword being present is no longer enough to flag the task.
- `evaluate_response_confidence` accepts two new optional kwargs, `pre_action_context_hits` (+up to 10) and `area_has_atlas_entry` (+5), so the score can finally reward tasks that loaded real context instead of only punishing unprepared ones. Both signals are capped and cannot override a real risk penalty.
- A monotonic numeric safeguard layers on top of the boolean decision tree: `answer` downgrades to `verify` when `final_score < 50`, and `verify` downgrades to `defer` when `high_stakes` and `final_score < 30`. The safeguard can only make response discipline stricter, never looser.
- `handle_cortex_quality` in `src/plugins/cortex.py` now reads `$NEXO_HOME/operations/cortex-quality-latest.json` when the requested window (7 or 1 days) is fresh (<6h 30m) and the schema matches — silent fallback to the live SQL computation on any failure. The handler's JSON response now includes `"source": "cache" | "live"` for observability.

Version `5.1.0` lands the full NEXO-AUDIT-2026-04-11 roadmap as a single minor bump — every open evolution / adaptive / cognitive / skills loop now closes under itself, the knowledge graph exports cleanly, OpenTelemetry spans can be turned on without a hard dependency, and every PR has to clear lint, security, coverage, and release-readiness gates before it can merge:

- Evolution cycle now auto-applies user-approved proposals on the next run (backed by the new idempotent migration `m38`), adaptive learned-weight rollbacks surface as visible followups, outcome patterns auto-promote to draft skills, and a Voyager-style detector exposes co-occurring skill pairs as composite-skill candidates via `nexo_skill_compose_candidates`.
- `cognitive._search.search()` now accepts `dream_weight` and reranks dream-insights through it, somatic markers fold into the same reranking path (max +0.10 boost), state watchers open and auto-resolve deterministic `NF-WATCHER-{id}` followups, and correction fatigue opens a visible followup instead of only decaying memory.
- A new Cortex quality cron (every 6h) watches accept rate / linked-success / override gap and opens `NF-CORTEX-QUALITY-DROP` idempotently when the decision engine starts drifting between cycles.
- Adding a new learning now walks recent decisions through `retroactive_learnings.apply_learning_retroactively()` and opens deterministic `NF-RETRO-L<id>-D<id>` followups for every decision the learning would have changed (exposed via `nexo_learning_apply_retroactively`).
- Hook lifecycle observability: new `hook_runs` table (migration `m39`) + `nexo_hook_runs` tool expose recent hook runs, failure streaks, and a health summary. Hook drops are no longer invisible.
- Knowledge graph bitemporal export: `nexo_kg_export` emits JSON-LD (with an `nexo:*` vocabulary) or GraphML, and accepts an `as_of` ISO timestamp that replays the historical snapshot through `kg_edges.valid_from / valid_until` for igraph, Gephi, NetworkX, and Cytoscape.
- OpenTelemetry integration: new `src/observability.py` soft-imports `opentelemetry` and only activates when `OTEL_EXPORTER_OTLP_ENDPOINT` or `OTEL_SERVICE_NAME` is set. `tool_span()` becomes a real span when enabled and stays a no-op context manager when disabled.
- CI gates on every PR: new workflows enforce ruff (`E9 / F63 / F7 / F82 / F821`), bandit at high severity / high confidence, coverage baselines, and `verify_release_readiness.py --ci`. A PR that breaks the release contract fails loudly instead of waiting until tag push.
- Safer update path: `auto_update` is guarded by a POSIX `flock` with stale-steal at 10 minutes, and on macOS it now `launchctl unload`s and reloads every `com.nexo.*.plist` after a version bump so long-lived crons pick up the new codebase immediately.

Version `5.0.4` tightens the local runtime bridge and trims false-positive doctor noise:

- vendorable `nexo_helper.py` now resolves `NEXO_HOME` and the `nexo` CLI path robustly, so personal scripts and subprocess flows stop depending on a lucky PATH
- doctor no longer degrades because of advisory-only self-audit warnings or a single missing usage-telemetry row
- managed Claude Code and Codex bootstraps now force an immediate first answer after simple email/diary/reminder/followup reads instead of feeling hung while chaining extra lookups

Version `5.0.3` closes the next post-5.0 runtime gap:

- `nexo chat` now boots Claude Code and Codex with an explicit NEXO startup prompt instead of opening cold or leaking the target path as a fake prompt
- terminal launches now use the requested working directory as real `cwd`, so the selected project path stops behaving like chat text
- the vendorable `nexo_helper.py` bridge now bounds helper calls with a timeout instead of letting personal-script subprocess flows wait forever
- the doctor hardening from `5.0.2` remains validated on a real upgraded runtime after sync

Version `5.0.2` closes the small post-5.0.1 doctor drift:

- deep doctor now reads the live `learnings` schema correctly whether the install uses `status` or the older `archived` flag
- a real upgraded runtime was revalidated with `nexo update`, `nexo doctor --tier deep`, `nexo doctor --tier all`, and a fresh Claude Code startup smoke

Version `5.0.1` hardens the live 5.0 upgrade path:

- managed Claude Code hooks are now cleaned up when an older release left obsolete core-managed entries behind
- upgrades no longer preserve the stale `heartbeat-guard.sh` path that could create warning storms and fake "hung" symptoms after `nexo update`
- the corrected path has been revalidated on a real install with `nexo clients sync`, Codex/Claude Code headless runtime access, email-monitor recovery, and a full `nexo update`

Version `5.0.0` closes the loop between memory, decisions, outcomes, and reusable behavior:

- goal profiles are now explicit and auditable instead of living as hidden heuristics
- the Cortex can rank alternatives with goals, outcomes, overrides, and structured penalties
- repeated outcome patterns can become durable learnings that influence later decisions
- outcome-backed evidence can seed, promote, demote, or retire reusable skills
- the runtime benchmark pack now shows the operator/runtime advantage with checked-in artifacts instead of relying only on prose
- personal-script/core runtime paths, protocol debt maintenance, and release doctoring are now strong enough that the live install path can be audited honestly before release

### Client Capability Matrix

| Capability | Claude Code | Codex | Claude Desktop |
|------------|-------------|-------|----------------|
| Shared brain / MCP runtime | Yes | Yes | Yes |
| Managed bootstrap document | `~/.claude/CLAUDE.md` | `~/.codex/AGENTS.md` | Not applicable |
| Global startup bootstrap sync | Native via hooks + bootstrap | Managed via bootstrap + Codex config `mcp_servers.nexo` | Managed MCP-only shared-brain metadata |
| `nexo chat` terminal client | Yes | Yes | No |
| Background automation backend | Recommended | Supported | No |
| Raw transcript source for Deep Sleep | Yes | Yes | No |
| Native hook depth | Deepest | Partial, compensated | None |
| Runtime doctor parity audit | Yes | Yes | Shared-brain only |
| Recommended today | Yes | Supported | Shared-brain companion |

### Supported Clients

| Client | Status | Integration style | Notes |
|--------|--------|-------------------|-------|
| Claude Code | First-class | Managed install + hooks + bootstrap | Deepest NEXO parity today |
| Codex | First-class | Managed install + bootstrap + transcript parity | Best non-Claude terminal path |
| Claude Desktop | Companion | MCP-only shared brain | Useful as read/chat companion |
| Cursor | Documented companion | MCP + `.cursor/rules` | Good editor pairing; no Deep Sleep transcript parity yet |
| Windsurf | Documented companion | MCP + `.windsurf/rules` or repo `AGENTS.md` | Native MCP support, manual companion mode |
| Gemini CLI | Adapter included | MCP + `GEMINI.md` | Best when you want Gemini as a shared-brain companion, not the primary NEXO runtime |

## The Problem

AI coding agents are powerful but amnesic:
- **No memory** — closes a session, forgets everything
- **Repeats mistakes** — makes the same error you corrected yesterday
- **No context** — can't connect today's work with last week's decisions
- **Reactive** — waits for instructions instead of anticipating needs
- **No learning** — doesn't improve from experience
- **No safety** — stores anything it's told, including poisoned or redundant data

## The Solution: A Cognitive Architecture

NEXO Brain implements the **Atkinson-Shiffrin memory model** from cognitive psychology (1968) — the same model that explains how human memory works:

```
What you say and do
    |
    +---> Sensory Register (raw capture, 48h)
    |       |
    |       +---> Attention filter: "Is this worth remembering?"
    |               |
    |               v
    +---> Short-Term Memory (7-day half-life)
    |       |
    |       +---> Used often? --> Consolidate to Long-Term Memory
    |       +---> Not accessed? --> Gradually forgotten
    |
    +---> Long-Term Memory (60-day half-life)
            |
            +---> Active: instantly searchable by meaning
            +---> Dormant: faded but recoverable ("oh right, I remember now!")
            +---> Near-duplicates auto-merged to prevent clutter
```

This isn't a metaphor. NEXO Brain literally implements Ebbinghaus forgetting curves, rehearsal-based reinforcement, and memory consolidation during automated "sleep" processes.

## What Makes NEXO Brain Different

| Without NEXO Brain | With NEXO Brain |
|---------------------|-----------------|
| Memory gone after each session | Persistent across sessions with natural decay and reinforcement |
| Repeats the same mistakes | Checks "have I made this mistake before?" before every action |
| Keyword search only | Finds memories by **meaning**, not just words |
| Starts cold every time | Resumes from the mental state of the last session |
| Same behavior regardless of context | Adapts tone and approach based on your mood |
| No relationship | Trust score that evolves — makes fewer redundant checks as alignment grows |
| Stores everything blindly | Prediction error gating rejects redundant information at write time |
| Vulnerable to memory poisoning | 4-layer security pipeline scans every memory before storage |
| No proactive behavior | Context-triggered reminders fire when topics match, not just by date |

## How the Brain Works

### Memory That Forgets (And That's a Feature)

NEXO Brain uses **Ebbinghaus forgetting curves** — memories naturally fade over time unless reinforced by use. This isn't a bug, it's how useful memory works:

- A lesson learned yesterday is strong. If you never encounter it again, it fades — because it probably wasn't important.
- A lesson accessed 5 times in 2 weeks gets promoted to long-term memory — because repeated use proves it matters.
- A dormant memory can be reactivated if something similar comes up — the "oh wait, I remember this" moment.

On top of that baseline, NEXO now keeps a lightweight **per-memory profile**:

- **stability** slows decay for memories that keep surviving retrieval and reinforcement
- **difficulty** speeds decay slightly for memories that tend to be weak, noisy, or harder to reuse correctly

That keeps the core Ebbinghaus model, but makes decay more individual and less purely global.

### Semantic Search (Finding by Meaning)

NEXO Brain doesn't search by keywords. It searches by **meaning** using multilingual vector embeddings (fastembed, 384 dimensions).

Example: If you search for "deploy problems", NEXO Brain will find a memory about "SSH connection timeout on production server" — even though they share zero words. This is how human associative memory works.

Retrieval is now also smarter by default:

- **HyDE auto mode** expands conceptual or ambiguous queries when that improves recall
- **Spreading activation auto mode** adds a shallow associative boost for concept-heavy searches
- **Exact lookup heuristics** keep both off for literal file paths, IDs, stack traces, and other precision-sensitive queries

### Metacognition (Thinking About Thinking)

Before every code change, NEXO Brain asks itself: **"Have I made a mistake like this before?"**

It searches its memory for related errors, warnings, and lessons learned. If it finds something relevant, it surfaces the warning BEFORE acting — not after you've already broken production.

### Cognitive Dissonance

When you give an instruction that contradicts established knowledge, NEXO Brain doesn't silently obey or silently resist. It **verbalizes the conflict**:

> "My memory says you prefer Tailwind over plain CSS, but you're asking me to write inline styles. Is this a permanent change or a one-time exception?"

You decide: **paradigm shift** (permanent change), **exception** (one-time), or **override** (old memory was wrong).

### Sibling Memories

Some memories look identical but apply to different contexts. "How to deploy" for Project A is different from Project B. NEXO Brain detects discriminating entities (different OS, platform, language) and links them as **siblings** instead of merging them:

> "Applying the Linux deploy procedure. Note: there's a sibling for macOS that uses a different port."

### Trust Score (0-100)

NEXO Brain tracks alignment with you through a trust score:

- **You say thanks** --> score goes up --> reduces redundant verification checks
- **Makes a mistake you already taught it** --> score drops --> becomes more careful, checks more thoroughly
- **The score doesn't control permissions** — you're always in control. It's a mirror that helps calibrate rigor.

### Sentiment Detection

NEXO Brain reads your tone (keywords, message length, urgency signals) and adapts:

- **Frustrated?** --> Ultra-concise mode. Zero explanations. Just solve the problem.
- **In flow?** --> Good moment to suggest that backlog item from last Tuesday.
- **Urgent?** --> Immediate action, no preamble.

### Sleep Cycle

Like a human brain, NEXO Brain has automated processes that run while you're not using it:

| Time | Process | Human Analogy |
|------|---------|---------------|
| 03:00 | Decay + memory consolidation + merge duplicates + dreaming | Deep sleep consolidation |
| 04:00 | Clean expired data, prune redundant memories | Synaptic pruning |
| 07:00 | Self-audit, health checks, metrics | Waking up + orientation |
| 23:30 | Process day's events, extract patterns | Pre-sleep reflection |
| Boot | Catch-up: run anything missed while computer was off | -- |

If your Mac was asleep during any scheduled process, NEXO Brain catches up in order when it wakes.

Deep Sleep now also mixes **recent context with older context across a 60-day horizon**. Instead of only looking at the immediate past, it can surface:

- recurring multi-week themes
- cross-domain links between older learnings and current failures
- stale followups and topics that keep being mentioned but never formalized
- weighted project pressure based on diary activity, followups, learnings, and decision outcomes

It now also writes **weekly and monthly Deep Sleep summaries** so the overnight system can reuse higher-horizon signals instead of rediscovering everything from scratch every day.

## Cognitive Cortex

The Cortex is a middleware cognitive layer that makes the agent **think before acting**. It implements architectural inhibitory control — the agent cannot bypass reasoning.

```
User message → Fast Path check → Simple chat? → Respond directly
                                → Action needed? → Cortex activates
                                                    ↓
                                              Generate cognitive state
                                              (goal, plan, unknowns, evidence)
                                                    ↓
                                              Middleware validates
                                              ├─ Unknowns? → ASK mode (tools blocked)
                                              ├─ No plan? → PROPOSE mode (read-only)
                                              └─ Plan + evidence → ACT mode (full access)
```

| Feature | What It Does |
|---------|-------------|
| **Inhibitory Control** | Physically restricts tools based on reasoning quality. Unknowns → can only ask. No plan → can only propose. Evidence + verification → can act. |
| **Event-Driven Activation** | Only activates on tool intent, ambiguity, destructive actions, or retries. Simple chat has zero overhead. |
| **Trust-Gated Escalation** | Low trust score → requires more evidence before allowing "act" mode. Trust builds through successful execution. |
| **Core Rules Injection** | Automatically surfaces relevant behavioral rules based on task type. |
| **Activation Metrics** | Tracks modes, inhibition rates, and task types for continuous improvement. |

The Cortex was designed through a 3-way AI debate (Claude Opus 4.6 + GPT-5.4 + Gemini 3.1 Pro) and validated against 6 months of real production failures.

## Durable Workflow Runtime

Memory and guardrails are not enough if long work still restarts from zero.

NEXO now ships a durable workflow runtime for multi-step and cross-session execution:

- `nexo_workflow_open` creates a persistent run with step metadata, idempotency key, priority, and shared state
- `nexo_workflow_update` records replayable checkpoints, retry metadata, approval gates, and the current actionable state
- `nexo_workflow_resume` tells the agent what to do next without guessing
- `nexo_workflow_replay` reconstructs the recent execution history honestly instead of pretending the run is still in memory
- `nexo_workflow_list` keeps active and blocked work visible so it does not disappear into reminders or prose notes

This is the bridge between "good memory" and "reliable execution": tasks can now preserve state, retries, approval gates, and next action across interruptions.

## Context Continuity (Auto-Compaction)

NEXO Brain automatically preserves session context when Claude Code compacts conversations. Using PreCompact and PostCompact hooks:

- **PreCompact**: Saves a complete session checkpoint to SQLite (task, files, decisions, errors, reasoning thread, next step)
- **PostCompact**: Re-injects a structured Core Memory Block into the conversation, so the session continues seamlessly

This means long sessions (8+ hours) feel like one continuous conversation instead of restarting after each compaction.

**How it works:**
1. Configure the hooks in your Claude Code `settings.json`
2. NEXO Brain's heartbeat automatically maintains the checkpoint
3. When compaction happens, the PreCompact hook reads the checkpoint and injects a recovery block
4. The session continues from exactly where it left off

**Setup:**
```json
{
  "hooks": {
    "PreCompact": [{
      "matcher": "*",
      "hooks": [{"type": "command", "command": "bash $NEXO_HOME/hooks/pre-compact.sh", "timeout": 10}]
    }],
    "PostCompact": [{
      "matcher": "*",
      "hooks": [{"type": "command", "command": "bash $NEXO_HOME/hooks/post-compact.sh", "timeout": 10}]
    }]
  }
}
```

2 new MCP tools: `nexo_checkpoint_save` (manual or hook-triggered checkpoint), `nexo_checkpoint_read` (retrieves the latest checkpoint for context injection).

## Cognitive Features

NEXO Brain provides **150+ MCP tools** across 23 categories. These features implement cognitive science concepts that go beyond basic memory:

### Input Pipeline

| Feature | What It Does |
|---------|-------------|
| **Prediction Error Gating** | Only novel information is stored. Redundant content that matches existing memories is rejected at write time, keeping your memory clean without manual curation. |
| **Security Pipeline** | 4-layer defense against memory poisoning: injection detection, encoding analysis, behavioral anomaly scoring, and credential scanning. Every memory passes through all four layers before storage. |
| **Quarantine Queue** | New facts enter quarantine status and must pass a promotion policy before becoming trusted knowledge. Prevents unverified information from influencing decisions. Automated nightly processing promotes, rejects, or expires items. |
| **Secret Redaction** | Auto-detects and redacts API keys, tokens, passwords, and other sensitive data before storage. Secrets never reach the vector database. |

### Memory Management

| Feature | What It Does |
|---------|-------------|
| **Pin / Snooze / Archive** | Granular lifecycle states for memories. Pin = never decays (critical knowledge). Snooze = temporarily hidden (revisit later). Archive = cold storage (searchable but inactive). |
| **Intelligent Chunking** | Adaptive chunking that respects sentence and paragraph boundaries. Produces semantically coherent chunks instead of arbitrary token splits, reducing retrieval noise. |
| **Adaptive Decay** | Decay rate still follows Ebbinghaus as the base model, but now also adapts per memory using `stability` and `difficulty` profiles. Frequently reinforced memories become stickier; fragile memories fade sooner. |
| **Auto-Migration** | Formal schema migration system (schema_migrations table) tracks all database changes. Safe, reversible schema evolution for production systems — upgrades never lose data. |
| **Auto-Merge Duplicates** | Batch cosine deduplication during the 03:00 sleep cycle. Respects sibling discrimination — similar memories about different contexts are kept separate. |
| **Memory Dreaming** | Discovers hidden connections between recent memories during the 03:00 sleep cycle and now feeds a 60-day long-horizon Deep Sleep blend, so older patterns can reappear when they become relevant again. |

### Operational Continuity

| Feature | What It Does |
|---------|-------------|
| **Hot Context 24h** | Keeps active topics, blockers, and waiting states fresh across sessions, clients, cron ticks, and channel changes. This is the shared recent-memory substrate for operational continuity. |
| **Pre-Action Context Bundle** | Loads recent contexts, recent events, related reminders, and related followups before acting, so continuity is explicit instead of prompt-only. |
| **Transcript Fallback** | When recent-memory capture is thin or missing, NEXO can now search and read recent Claude Code / Codex transcripts directly through MCP instead of pretending the conversation is lost. |
| **Live System Catalog** | NEXO can now inspect its own current surface — core tools, plugin tools, skills, scripts, crons, projects, and artifacts — through a live catalog derived from canonical sources at read time. |

### Retrieval

| Feature | What It Does |
|---------|-------------|
| **HyDE Query Expansion** | Generates hypothetical answer embeddings for richer semantic search. NEXO now auto-enables HyDE for conceptual or ambiguous queries while keeping literal lookups conservative. |
| **Hybrid Search (FTS5+BM25+RRF)** | Combines dense vector search with BM25 keyword search via Reciprocal Rank Fusion. Outperforms pure semantic search on precise terminology and code identifiers. |
| **Cross-Encoder Reranking** | After initial vector retrieval, a cross-encoder model rescores candidates for precision. The top-k results are reordered by true semantic relevance before being returned to the agent. |
| **Multi-Query Decomposition** | Complex questions are automatically split into sub-queries. Each component is retrieved independently, then fused for a higher-quality answer — improves recall on multi-faceted prompts. |
| **Temporal Indexing** | Memories are indexed by time in addition to semantics. Time-sensitive queries ("what did we decide last Tuesday?") use temporal proximity scoring alongside semantic similarity. |
| **Spreading Activation** | Graph-based co-activation network. NEXO now auto-enables a shallow spreading pass for concept-heavy queries, improving contextual recall without turning every exact lookup into a fuzzy search. |
| **Recall Explanations** | Transparent score breakdown for every retrieval result. Shows exactly why a memory was returned: semantic similarity, recency, access frequency, and co-activation bonuses. |

### Proactive

| Feature | What It Does |
|---------|-------------|
| **Prospective Memory** | Context-triggered reminders that fire when conversation topics match, not just by date. "Remind me about X when we discuss Y" works naturally. |
| **Hook Auto-capture** | Extracts decisions, corrections, and factual statements from conversations automatically. You don't need to explicitly say "remember this" — the system detects what's worth storing. |
| **Session Summaries** | Automatic end-of-session summarization that distills key decisions, errors, and follow-ups into a compact diary entry. The next session starts with full context — not a cold slate. |
| **Smart Startup** | Pre-loads relevant cognitive memories at session boot by composing a query from pending followups, due reminders, and last session's topics. Every session starts with the right context — not a cold search. |
| **Context Packets** | Bundles all area knowledge (learnings, recent changes, active followups, preferences, cognitive memories) into a single injectable packet for subagent delegation. Subagents never start blind again. |
| **Auto-Prime by Topic** | Heartbeat detects project/area keywords in conversation and automatically surfaces the most relevant learnings. No explicit memory query needed — context arrives proactively. |

## Benchmark: LoCoMo (ACL 2024)

NEXO Brain was evaluated on [LoCoMo](https://github.com/snap-research/locomo) (ACL 2024), a long-term conversation memory benchmark with 1,986 questions across 10 multi-session conversations.

| System | F1 | Adversarial | Hardware |
|---|---|---|---|
| **NEXO Brain v0.5.0** | **0.588** | **93.3%** | **CPU only** |
| GPT-4 (128K full context) | 0.379 | — | GPU cloud |
| Gemini Pro 1.0 | 0.313 | — | GPU cloud |
| LLaMA-3 70B | 0.295 | — | A100 GPU |
| GPT-3.5 + Contriever RAG | 0.283 | — | GPU |

**+55% vs GPT-4. Running entirely on CPU.**

**Key findings:**
- Outperforms GPT-4 (128K full context) by 55% on F1 score
- 93.3% adversarial rejection rate — reliably says "I don't know" when information isn't available
- 74.9% recall across 1,986 questions
- Open-domain F1: 0.637 | Multi-hop F1: 0.333 | Temporal F1: 0.326
- Runs on CPU with local multilingual embeddings — no GPU required
- First MCP memory server benchmarked on a peer-reviewed dataset

Full results in [`benchmarks/locomo/results/`](benchmarks/locomo/results/).

## Nervous System (v2.0.0)

NEXO Brain doesn't just respond — it runs 13 core recovery-aware background jobs plus optional helpers, like a biological nervous system. They handle maintenance, health monitoring, and self-improvement without any user interaction:

| Script | Schedule | What It Does |
|--------|----------|-------------|
| **cognitive-decay** | 03:00 daily | Ebbinghaus decay + memory consolidation + duplicate merging + dreaming |
| **sleep** | 04:00 daily | Synaptic pruning, expired data cleanup |
| **deep-sleep** | 04:30 daily | 4-phase overnight pipeline: Collect→Extract→Synthesize→Apply. Analyzes all sessions, detects emotional patterns, abandoned projects, productivity issues, and auto-creates learnings |
| **self-audit** | 07:00 daily | Health checks, guard stats, trust score review, metrics |
| **postmortem** | 23:30 daily | Session consolidation, extract patterns from day's events |
| **catchup** | On boot | Runs any missed scheduled processes (Mac was off/asleep) |
| **tcc-approve** | On boot (macOS) | Auto-approve macOS permissions for Claude Code updates |
| **prevent-sleep** | Always (daemon) | Keeps machine awake for nocturnal processes (caffeinate/systemd-inhibit) |
| **evolution** | Weekly (Sun) | Self-improvement proposals — NEXO suggests and applies enhancements |
| **followup-hygiene** | Weekly (Sun) | Normalizes statuses, flags stale followups, cleans orphans |
| **learning-housekeep** | 03:15 daily | Dedup learnings, adjust weights by usage, process overdue reviews, reconcile decision outcomes |
| **immune** | Every 30 min | Quarantine processing, memory promotion/rejection, synaptic pruning |
| **impact-scorer** | 05:45 daily | Scores active followups so queues can prioritize by expected impact |
| **synthesis** | 06:00 daily | Memory synthesis — discovers cross-memory patterns |
| **outcome-checker** | 08:00 daily | Verifies tracked outcomes and marks them met, pending, or missed |
| **watchdog** | Every 30 min | Monitors services, LaunchAgents, and infrastructure health |
| **auto-close-sessions** | Every 5 min | Cleans stale sessions |

Core processes are defined in `src/crons/manifest.json` and auto-synced to your system by `nexo_update`. On macOS they run via LaunchAgents; on Linux via systemd user timers. `tcc-approve`, `prevent-sleep`, and `backup` are platform/personal helpers — not in the manifest but listed above for completeness. Personal crons (your own scripts) are never touched by the sync. If your Mac was asleep during a scheduled process, the catch-up script re-runs everything in order when it wakes.

## Deep Sleep v2 — Overnight Learning (v2.1.0)

Deep Sleep is a 4-phase pipeline that runs at 4:30 AM and makes NEXO smarter while you sleep:

```
Phase 1: COLLECT (Python)
├── Reads all session transcripts from the day
├── Splits each session into individual .txt files
└── Gathers DB state (followups, learnings, trust)

Phase 2: EXTRACT (Opus, one call per session)
├── 8 types of findings per session:
│   ├── Uncaptured corrections (user corrected agent, no learning saved)
│   ├── Self-corrected errors (knowledge gaps to fix)
│   ├── Unformalised ideas (mentioned but never tracked)
│   ├── Missed commitments (promised but no followup)
│   ├── Protocol violations (guard_check, heartbeat, change_log)
│   ├── Emotional signals (frustration, flow, satisfaction)
│   ├── Abandoned projects (started but not finished)
│   └── Productivity patterns (corrections, proactivity, tool efficiency)
└── Outputs per-session JSON with findings + emotional timeline

Phase 3: SYNTHESIZE (Opus, one call)
├── Cross-session patterns (same error in 5 sessions = systemic)
├── Daily mood arc with score (0.0 = terrible day, 1.0 = great day)
├── Recurring triggers (what causes frustration vs flow)
├── Productivity analysis (corrections, tool efficiency)
├── Abandoned project detection
├── Morning agenda (prioritized)
└── Calibration recommendations

Phase 4: APPLY (Python)
├── Auto-creates learnings from high-confidence findings
├── Creates followups for unfinished work
├── Updates mood_history in calibration.json (30-day rolling)
├── Generates session-tone.json (emotional guidance for next session)
└── Writes morning-briefing.md
```

### Session Tone — Emotional Intelligence

Deep Sleep generates a `session-tone.json` that tells NEXO how to behave next morning:

- **Agent made many mistakes yesterday** → Acknowledge them, show what was learned, demonstrate improvement
- **User had a bad day (mood < 40%)** → Supportive approach, lighter start, avoid known frustration triggers
- **User had a great day (mood > 70%)** → Reinforce momentum, reference wins, push ambitious goals
- **Agent was too reactive** → Be proactive today, don't wait for instructions

This is read by `nexo_smart_startup` and injected into every session's context. NEXO adapts its personality based on real behavioral data, not just configuration.

## Cron Manifest & Scheduler (v2.4.0)

All core crons are defined in `src/crons/manifest.json`. When you run `nexo_update`, the sync script:
- **Installs** new crons from the manifest
- **Updates** changed schedules/intervals
- **Removes** crons no longer in the manifest (only core ones)
- **Never touches** personal crons you created yourself

Every cron execution is tracked in the `cron_runs` table via a universal wrapper. Use `nexo_schedule_status` to see what ran overnight:

```
✅ deep-sleep: 1/1 OK, 4523s avg — 37 sessions, 259 findings
✅ immune: 48/48 OK, 2s avg
❌ evolution: 0/1 OK — CLI timeout
```

Add personal crons from conversation with `nexo_schedule_add` — generates LaunchAgent (macOS) or systemd timer (Linux) automatically.

## Skill Auto-Creation (v2.4.0)

Deep Sleep automatically extracts reusable procedures from successful multi-step tasks and stores them as skills with full procedural content (steps, gotchas, markdown).

Pipeline: `trace → draft → published → archived`. Trust rises with successful use, decays without it. No human approval gates.

7 MCP tools: `nexo_skill_create`, `nexo_skill_match`, `nexo_skill_get`, `nexo_skill_result`, `nexo_skill_list`, `nexo_skill_merge`, `nexo_skill_stats`.

## Dashboard (v1.6.0)

A web interface at `localhost:6174` with 6 interactive pages for visual insight into your brain's state:

| Page | What It Shows |
|------|-------------|
| **Overview** | System health at a glance — memory counts, trust score, active sessions, recent changes |
| **Graph** | Interactive D3.js visualization of the knowledge graph (nodes, edges, clusters) |
| **Memory** | Browse and search all memory stores (STM, LTM, sensory, archived) |
| **Somatic** | Pain map per file/area — see which parts of your codebase cause the most errors |
| **Adaptive** | Personality signals, learned weights, and current mode |
| **Sessions** | Active and historical sessions with timeline and diary entries |

Built with FastAPI backend and D3.js frontend. Dashboard files are installed to `NEXO_HOME/dashboard/` but must be started manually:

```bash
python3 ~/.nexo/dashboard/app.py
```

This opens `localhost:6174` in your browser. Add `--port 8080` to change the port or `--no-browser` to skip auto-opening.

## Full Orchestration System

Memory alone doesn't make a co-operator. What makes the difference is the **behavioral loop** — the automated discipline that ensures every session starts informed, runs with guardrails, and ends with self-reflection.

### Automated Hooks

7 hooks fire automatically at key moments in every Claude Code session:

| Hook | When | What It Does |
|------|------|-------------|
| **SessionStart (timestamp)** | Session opens | Writes session timestamp for staleness detection |
| **SessionStart (briefing)** | Session opens | Generates briefing from SQLite: overdue reminders, today's tasks, pending followups, active sessions. Cleans up post-mortem flags. |
| **Stop** | Session ends | Mandatory post-mortem: self-critique (5 questions), session buffer entry, followup creation, proactive seeds for next session |
| **PostToolUse (capture)** | After each tool call | Captures meaningful mutations to the Sensory Register + auto-diary every 10 tool calls |
| **PostToolUse (inbox)** | After each tool call | Inter-terminal inbox delivery between parallel sessions |
| **PreCompact** | Before context compression | Saves full session checkpoint to SQLite — task, files, decisions, errors, reasoning thread + emergency diary |
| **PostCompact** | After context compression | Re-injects Core Memory Block so the session continues seamlessly from where it left off |

### The Session Lifecycle

```
Session starts
    ↓
SessionStart hook generates briefing
    ↓
Operator reads diary, reminders, followups
    ↓
Heartbeat on every interaction (sentiment, context shifts)
    ↓
Guard check before every code edit
    ↓
PreCompact hook saves full checkpoint if conversation is compressed
    ↓
PostCompact hook re-injects Core Memory Block → session continues seamlessly
    ↓
Stop hook refreshes the diary draft and approves immediately:
  - Latest changes and decisions stay attached to the active session
  - Session buffer keeps structured tool activity for downstream processing
  - Followups and closing synthesis happen inline when the agent detects real closing intent
  - No mid-conversation blocking from the hook itself
    ↓
Nocturnal post-mortem consolidator processes the buffer mechanically
    ↓
Nocturnal processes: decay, consolidation, self-audit, dreaming
```

### Reflection Engine

NEXO still ships `nexo-reflection.py` as a standalone analyzer for `session_buffer.jsonl`.
It is not currently auto-triggered by the stop hook:
- Extracts recurring tasks, error patterns, mood trends
- Updates `user_model.json` with observed behavior
- No LLM required — runs as pure Python

### Auto-Migration

Existing users upgrading from any previous version:
```bash
npx nexo-brain  # detects current version, migrates automatically
```
- Updates hooks, core files, plugins, scripts, and LaunchAgent templates
- Runs database schema migrations automatically
- **Never touches your data** (memories, learnings, preferences)
- Saves updated CLAUDE.md as reference (doesn't overwrite customizations)

## Runtime CLI (v2.6.0)

NEXO Brain includes a local CLI that runs independently of any single terminal client:

- `nexo chat` — launch a NEXO terminal client; if both Claude Code and Codex are available, it asks every time which one to open and puts the last-used client first
- `nexo update` — sync runtime from source, run migrations, reconcile schedules
- `nexo doctor --tier runtime` — boot/runtime/deep diagnostics with `--fix` mode
- `nexo scripts list` — list all personal scripts and their status
- `nexo scripts reconcile` — align declared schedules with actual LaunchAgents/systemd
- `nexo -v` — show installed runtime version

The CLI lives at `NEXO_HOME/bin/nexo` and is added to your PATH during install.

## Personal Scripts Registry (v2.6.0)

Scripts in `NEXO_HOME/scripts/` are first-class managed entities:

- Tracked in SQLite with metadata, categories, and schedule associations
- Inline metadata in scripts declares name, runtime, schedule, and recovery policy
- `nexo scripts create NAME` scaffolds a new script with the correct template
- `nexo scripts reconcile` creates/repairs LaunchAgents from declared metadata
- `nexo scripts sync` discovers filesystem state and updates the registry
- `nexo doctor --tier runtime` detects orphaned schedules, missing plists, and drift

Personal scripts are completely separate from core NEXO processes. The `crons/manifest.json` defines core; everything in `NEXO_HOME/scripts/` is personal.

If you need to decide between a personal script, skill, plugin, or schedule, use [docs/personal-artifacts-manual.md](docs/personal-artifacts-manual.md). That is the canonical operational guide.

## Recovery-Aware Background Jobs (v2.6.2)

Core and personal jobs now declare explicit recovery contracts in `crons/manifest.json`:

| Field | Purpose |
|-------|---------|
| `recovery_policy` | `catchup`, `restart`, `restart_daemon`, or `skip` |
| `run_on_boot` | Re-run when the machine starts |
| `run_on_wake` | Re-run after sleep/resume |
| `idempotent` | Safe to re-run without side effects |
| `max_catchup_age` | Maximum age of a missed window to still catch up |

If the Mac was asleep during a scheduled window, `catchup` detects the gap from `cron_runs` (not a state file) and re-executes eligible jobs once. Interval-based personal scripts get a single recovery run, not repeated ticks.

For personal daemon-style helpers, `recovery_policy=restart_daemon` plus `schedule_required=true` declares an official `KeepAlive` schedule. NEXO can now reconcile and repair those daemons instead of treating them as unmanaged legacy LaunchAgents.

## Startup Preflight (v2.6.2)

Before `nexo chat` or MCP server start, NEXO runs a preflight check:

1. Apply power policy (caffeinate on macOS, systemd-inhibit on Linux)
2. Run safe local migrations and backfills
3. Sync personal scripts registry
4. For dev-linked runtimes: check if source repo is behind, pull if safe, sync to runtime

This replaces the old "blind startup" where NEXO entered without verifying runtime health.

## Knowledge Graph (v0.8)

A bi-temporal entity-relationship graph with 988 nodes and 896 edges. Entities and relationships carry both valid-time (when the fact was true) and system-time (when it was recorded), enabling temporal queries like "what did we know about X last Tuesday?". BFS traversal discovers multi-hop connections between concepts. Event-sourced edges with smart dedup (ADD/UPDATE/NOOP) prevent redundant writes while preserving full history.

4 MCP tools: `nexo_kg_query` (SPARQL-like queries), `nexo_kg_path` (shortest path between entities), `nexo_kg_neighbors` (direct connections), `nexo_kg_stats` (graph metrics).

### Cross-Platform Support
Full Linux support and Windows via WSL. The installer detects the platform and configures the appropriate process manager (LaunchAgents on macOS, catch-up on startup for Linux). PEP 668 compliance (venv on Ubuntu 24.04+). Session keepalive prevents phantom sessions during long tasks. Opportunistic maintenance runs cognitive processes when resources are available.

> **Windows users:** NEXO Brain requires [WSL (Windows Subsystem for Linux)](https://learn.microsoft.com/en-us/windows/wsl/install). Install WSL first, then run `npx nexo-brain` inside the Ubuntu/WSL terminal.

### Storage Router
A new abstraction layer routes storage operations through a unified interface, making the system multi-tenant ready. Each operator's data is isolated while sharing the same cognitive engine.

## Learned Weights & Somatic Markers (v0.7.0)

### Adaptive Learned Weights
Signal weights learn from real user feedback via Ridge regression. A 2-week shadow mode observes before activating. Weight momentum (85/15 blend) prevents personality whiplash. Automatic rollback if correction rate doubles.

### Somatic Markers (Pain Memory)
Files and areas that cause repeated errors accumulate a risk score (0.0–1.0). The guard system warns on HIGH RISK (>0.5) and CRITICAL RISK (>0.8), lowering thresholds for more paranoid checking. Clean guard checks reduce risk multiplicatively (×0.7). Nightly decay (×0.95) ensures old pain fades.

### Adaptive Personality v2
6 weighted signals: vibe, corrections, brevity, topic, tool errors, git diff. Emergency keywords bypass hysteresis. Severity-weighted decay. Manual override via `nexo_adaptive_override`.

## Quick Start

### Claude Code (Primary)

```bash
npx nexo-brain
```

The installer handles everything and syncs the same `nexo` MCP brain into Claude Code, Claude Desktop, and Codex when those clients are present:

```
  How should I call myself? (default: Nova) > Atlas

  Can I explore your workspace to learn about your projects? (y/n) > y

  Keep Mac awake so my cognitive processes run on schedule? (y/n) > y

  Installing cognitive engine dependencies...
  Setting up NEXO home...
  Scanning workspace...
    - 3 git repositories
    - Node.js project detected
  Configuring MCP server...
  Setting up nervous system...
    15 core recovery-aware jobs configured.
    Dashboard configured at localhost:6174.
  Caffeinate enabled.
  Generating operator instructions...

  +----------------------------------------------------------+
  |  Atlas is ready. Type 'atlas' to start.                  |
  +----------------------------------------------------------+
```

### Docker Compose

NEXO now ships a root-level [`docker-compose.yml`](docs/docker-setup.md) for a persistent containerized runtime. It does two things at once:

- keeps `NEXO_HOME` on a named volume
- exposes a remote MCP endpoint at `http://localhost:8000/mcp` for IDEs that support HTTP/SSE MCP

Start it with:

```bash
docker compose up -d
```

For Claude Code and Codex, keep using stdio and point the MCP command at the running container:

```bash
docker compose exec -T nexo python src/server.py
```

That gives you the same persistent brain in the container while keeping terminal clients on their native stdio transport. The full step-by-step flow, health checks, and config examples live in [docs/docker-setup.md](docs/docker-setup.md).

### Starting a Session

After install, use the runtime CLI:

```bash
nexo chat          # Launch a NEXO terminal client (asks if both Claude Code and Codex are available)
nexo doctor        # Check runtime health
nexo update        # Pull latest version and sync
nexo clients sync  # Re-sync Claude Code/Desktop/Codex to the same brain
nexo scripts list  # See your personal scripts
```

During install, NEXO now asks which interactive clients you want to connect, which one `nexo chat` should suggest first when multiple terminal clients are available, whether to enable background automation, which backend should run that automation, and which model profile each active terminal/backend should use. Shared brain stays on in every mode.

Public entry points for the mental model now stay intentionally small:
- `nexo_remember`
- `nexo_memory_recall`
- `nexo_consolidate`
- `nexo_run_workflow`
- `nexo_pre_action_context`
- `nexo_transcript_search`
- `nexo_system_catalog`

If you want the shell or Python wrappers instead of raw MCP tools:
- [docs/quickstart-5-minutes.md](docs/quickstart-5-minutes.md)
- [docs/memory-classes.md](docs/memory-classes.md)
- [docs/recent-memory-fallbacks-and-system-catalog.md](docs/recent-memory-fallbacks-and-system-catalog.md)
- [docs/sdk-python.md](docs/sdk-python.md)
- [docs/reference-verticals.md](docs/reference-verticals.md)
- [compare/README.md](compare/README.md)

The model you pick during install is used everywhere — interactive sessions, automation scripts, and all task profiles.  Change it once in your preferences and every part of the system follows.  Default: `Opus 4.7 with 1M context`.

Or use the shell alias created during install (e.g. `atlas`), which now runs `nexo chat .` so it opens the terminal client you pick for that session, with the last-used option shown first.

Your operator will greet you immediately — adapted to the time of day, resuming from where you left off. No cold starts.

### Contributing

NEXO is being hardened in public, and the best contributions now are not only code changes but also real workflow feedback:

- Open issues when a client flow feels asymmetric across Claude Code, Codex, Claude Desktop, OpenClaw, or other MCP environments.
- Send PRs for docs, install UX, tests, compatibility checks, and public-facing copy.
- If you use NEXO in production-like daily work, include exact runtime symptoms and commands in bug reports. This project improves fastest when the operational reality is concrete.

The project still recommends Claude Code as the primary path, but contributions that improve Codex, client parity, installer clarity, and ecosystem integrations are especially valuable.

Maintainers and contributors touching startup, bootstrap, Deep Sleep, or shared-brain behavior should also use the client parity checklist:
- [docs/client-parity-checklist.md](docs/client-parity-checklist.md)
- `python3 scripts/verify_release_readiness.py`

### What Gets Installed

| Component | What | Where |
|-----------|------|-------|
| Cognitive engine | Python: fastembed, numpy, vector search | pip packages |
| MCP server | 150+ tools for memory, cognition, learning, guard | NEXO_HOME/ |
| Claude Code Plugin | Marketplace-ready (packaging verified) | `.claude-plugin/` |
| Plugins | Guard, episodic memory, cognitive memory, entities, preferences, update, etc. | Code: src/plugins/, Personal: NEXO_HOME/plugins/ |
| Hooks (7) | SessionStart, Stop, PostToolUse, PreCompact, PostCompact | NEXO_HOME/hooks/ |
| Nervous system | 13 core recovery-aware jobs + optional helpers (dashboard, prevent-sleep) | NEXO_HOME/scripts/ |
| Dashboard | Web UI at localhost:6174 (23 modules, dark theme) — opt-in, always-on | NEXO_HOME/dashboard/ |
| Runtime CLI | `nexo` command: scripts, doctor, skills, update | NEXO_HOME/bin/ |
| Doctor | Unified diagnostics: boot/runtime/deep tiers, `--fix` mode | src/doctor/ |
| Skills v2 | Executable skills with guide/execute/hybrid modes, approval levels | NEXO_HOME/skills/ |
| Startup Preflight | Health checks before every `nexo chat` or server start | Built into CLI |
| CLAUDE.md | Complete operator instructions (Codex, hooks, guard, trust, memory) | ~/.claude/CLAUDE.md |
| Schedule config | schedule.json with customizable process times and timezone | NEXO_HOME/config/ |
| Auto-update | Non-blocking startup check (5s max), opt-out via schedule.json | Built into server startup |
| CLAUDE.md tracker | Version-tracked core sections with safe updates preserving customizations | Built into auto-update |
| Shared client sync | Same `nexo` MCP entry wired into Claude Code, Claude Desktop, and Codex | User config dirs |
| Client/backend preferences | Selected interactive clients, default terminal client, automation backend, and model/reasoning profiles per client | `NEXO_HOME/config/schedule.json` |
| Auto-diary | 3-layer system: PostToolUse every 10 calls, PreCompact emergency, heartbeat DIARY_OVERDUE | Built into hooks |
| Claude Code config | MCP server + 7 hooks + 15 managed processes registered | ~/.claude/settings.json |

### Runtime CLI

After installation or auto-update, NEXO adds `NEXO_HOME/bin` to your shell `PATH`. Open a new terminal and the `nexo` command provides operational tools:

```bash
# Personal Scripts
nexo scripts list              # List your personal scripts
nexo scripts run my-script     # Run a script with injected NEXO env
nexo scripts doctor            # Validate all personal scripts
nexo scripts call nexo_learning_search --input '{"query":"cron"}' # Call any MCP tool

# Skills v2
nexo skills sync               # Sync filesystem skill definitions into SQLite
nexo skills list               # List published/stable skills
nexo skills get SK-...         # Inspect a skill definition
nexo skills apply SK-... --dry-run --json  # Resolve guide/execute/hybrid without running it
nexo skills approve SK-... --execution-level local --approved-by Francisco  # Optional metadata override
nexo skills evolution          # Show text→script and improvement candidates

# Unified Doctor
nexo doctor                    # Quick boot diagnostics
nexo doctor --tier all         # Full system check (boot + runtime + deep)
nexo doctor --tier runtime --json  # Machine-readable health report
nexo doctor --fix              # Apply deterministic repairs
```

Personal scripts live in `NEXO_HOME/scripts/` with inline metadata. Their Python templates now include `run_automation_text(...)`, which routes work through the configured NEXO automation backend instead of hardcoding `claude -p` or provider-specific model names. `nexo-agent-run.py` now also supports task profiles (`fast`, `balanced`, `deep`) plus safe backend fallback, so automations can prefer cheaper/faster Codex paths or deeper Claude paths without hardcoding one provider forever. See `docs/writing-scripts.md` for details and `docs/personal-artifacts-manual.md` for the canonical artifact decision guide.

Skills v2 combine procedural guides with optional executable scripts. Personal skills live in `NEXO_HOME/skills/`, packaged core skills live in `NEXO_CODE/skills/` during development and `NEXO_HOME/skills-core/` in installed environments, and staged runtime copies live in `NEXO_HOME/skills-runtime/`. Execution is fully autonomous: Deep Sleep can evolve mature guide skills into executable drafts automatically, and runtime execution no longer waits for manual approval. See `docs/skills-v2.md` for the full model and `docs/personal-artifacts-manual.md` for the boundary between skills, scripts, plugins, and schedules.

The Doctor system reads existing health artifacts (immune, watchdog, self-audit) without triggering repairs in default mode.

### Requirements

- **macOS or Linux** (Windows via [WSL](https://learn.microsoft.com/en-us/windows/wsl/install))
- **Node.js 18+** (for the installer)
- **Claude Code is the primary recommended client.** It remains the most mature NEXO path: native hooks, the most battle-tested automation contract, and the clearest parity with historical production behavior.
- **Model:** You pick your model during install and every component uses it.  Default is `Opus 4.7 with 1M context`.  Scripts and automation profiles read from a single preference — no hardcoded model strings.
- Python 3, Homebrew, and the selected required client/backend can be installed automatically when NEXO has a supported installer path for that dependency.

## Architecture

### Unified Code/Data Separation (v2.0.0)

NEXO Brain separates **code** (immutable, in the repo or npm package) from **data** (personal, in `NEXO_HOME`):

| Path | Contents |
|------|----------|
| `src/` (or npm package) | Server, plugins, hooks, scripts — never modified at runtime |
| `NEXO_HOME/` (default `~/.nexo/`) | Database, config, personal plugins, schedule, backups |
| `NEXO_HOME/config/schedule.json` | Customizable process schedules, timezone, auto_update flag |
| `NEXO_HOME/plugins/` | Personal plugins that override or extend repo plugins |
| `NEXO_HOME/data/` | SQLite databases (nexo.db, cognitive.db), migration state |

The plugin loader scans `src/plugins/` first (base), then `NEXO_HOME/plugins/` (personal override by filename). This dual-directory approach lets you extend NEXO without forking the repo.
The client sync layer points Claude Code, Claude Desktop, and Codex at the same runtime and `NEXO_HOME`, so all three clients share one brain instead of drifting into separate local memories.

### 150+ MCP Tools across 23 Categories

| Category | Count | Tools | Purpose |
|----------|-------|-------|---------|
| Cognitive | 8 | retrieve, stats, inspect, metrics, dissonance, resolve, sentiment, trust | The brain — memory, RAG, trust, mood |
| Cognitive Input | 5 | prediction_gate, security_scan, quarantine, promote, redact | Input pipeline — gating, security, quarantine |
| Cognitive Advanced | 8 | hyde_search, spread_activate, explain_recall, dream, prospect, hook_capture, pin, archive | Advanced retrieval, proactive, lifecycle |
| Guard | 3 | check, stats, log_repetition | Metacognitive error prevention |
| Episodic | 10 | change_log/search/commit, decision_log/outcome/search, review_queue, diary_write/read, recall | What happened and why |
| Sessions | 4 | startup, heartbeat, stop, status | Session lifecycle + context shift detection + inter-terminal auto-inbox |
| Coordination | 7 | track, untrack, files, send, ask, answer, check_answer | Multi-session file coordination + messaging |
| Reminders | 5 | list, create, update, complete, delete | User's tasks and deadlines |
| Followups | 4 | create, update, complete, delete | System's autonomous verification tasks |
| Learnings | 5 | add, search, update, delete, list | Error patterns and prevention rules |
| Credentials | 5 | create, get, update, delete, list | Local credential storage (plaintext SQLite — protect with filesystem permissions) |
| Task History | 3 | log, list, frequency | Execution tracking and overdue alerts |
| Menu | 1 | menu | Operations center with box-drawing UI |
| Entities | 5 | search, create, update, delete, list | People, services, URLs |
| Preferences | 4 | get, set, list, delete | Observed user preferences |
| Agents | 5 | get, create, update, delete, list | Agent delegation registry |
| Backup | 3 | now, list, restore | SQLite data safety |
| Evolution | 5 | propose, approve, reject, status, history | Self-improvement proposals |
| Adaptive & Somatic | 4 | adaptive_weights, adaptive_override, somatic_check, somatic_stats | Learned signal weights + pain memory per file |
| Knowledge Graph | 4 | kg_query, kg_path, kg_neighbors, kg_stats | Bi-temporal entity-relationship graph |
| Context Continuity | 2 | checkpoint_save, checkpoint_read | Auto-compaction session preservation |
| Personal Scripts | 9 | sync, list, create, remove, schedules, unschedule, reconcile, classify, ensure_schedules | Script lifecycle management |
| Skills | 12 | match, create, get, list, apply, approve, result, stats, evolution_candidates, merge, sync, featured | Reusable procedure library |
| Schedule | 2 | add, status | Personal cron scheduling |
| Doctor | 1 | doctor | Runtime diagnostics with --fix |
| Update | 1 | update | Pull latest code, backup, migrate, verify (with rollback) |

### Plugin System

NEXO Brain supports hot-loadable plugins with a dual-directory loader. Base plugins live in `src/plugins/` (repo). Personal plugins go in `NEXO_HOME/plugins/` and can override base plugins by filename. Drop a `.py` file in `NEXO_HOME/plugins/`:

```python
# my_plugin.py
def handle_my_tool(query: str) -> str:
    """My custom tool description."""
    return f"Result for {query}"

TOOLS = [
    (handle_my_tool, "nexo_my_tool", "Short description"),
]
```

Reload without restarting: `nexo_plugin_load("my_plugin.py")`

Use a personal plugin only when you need a new MCP tool in the runtime surface. If the real need is autonomous execution or scheduling, use a personal script plus managed schedule instead. The canonical decision guide is [docs/personal-artifacts-manual.md](docs/personal-artifacts-manual.md).

### Data Privacy

- **Everything stays local.** All data in `~/.nexo/`, never uploaded anywhere.
- **No telemetry.** No analytics. No phone-home.
- **No cloud dependencies.** Vector search runs on CPU (fastembed), not an API.
- **Auto-update is resilient.** NEXO checks for updates on startup. If an update fails, it continues with the current version and notifies you. Local migrations (database schema, configuration) always run. Network updates (git pull) can be disabled by setting `auto_update: false` in `NEXO_HOME/config/schedule.json`.
- **Secret redaction.** API keys and tokens are stripped before they ever reach memory storage.

## The Psychology Behind NEXO Brain

NEXO Brain isn't just engineering — it's applied cognitive psychology:

| Psychological Concept | How NEXO Brain Implements It |
|----------------------|----------------------|
| Atkinson-Shiffrin (1968) | Three memory stores: sensory register --> STM --> LTM |
| Ebbinghaus Forgetting Curve (1885) | Exponential decay: `strength = strength * e^(-lambda * time)` |
| Rehearsal Effect | Accessing a memory resets its strength to 1.0 |
| Memory Consolidation | Nightly process promotes frequently-used STM to LTM |
| Prediction Error | Only surprising (novel) information gets stored — redundant input is gated |
| Spreading Activation (Collins & Loftus, 1975) | Retrieving a memory co-activates related memories through an associative graph |
| HyDE (Gao et al., 2022) | Hypothetical document embeddings improve semantic recall |
| Prospective Memory (Einstein & McDaniel, 1990) | Context-triggered intentions fire when cue conditions match |
| Metacognition | Guard system checks past errors before acting |
| Cognitive Dissonance (Festinger, 1957) | Detects and verbalizes conflicts between old and new knowledge |
| Theory of Mind | Models user behavior, preferences, and mood |
| Synaptic Pruning | Automated cleanup of weak, unused memories |
| Associative Memory | Semantic search finds related concepts, not just matching words |
| Memory Reconsolidation | Dreaming process discovers hidden connections during sleep |

## Integrations

### Claude Code (Primary)

NEXO Brain is designed as an MCP server. Claude Code remains the primary recommended client and the most complete integration path:

```bash
npx nexo-brain
```

All 150+ tools are available immediately after installation. The installer configures Claude Code's `~/.claude/settings.json` automatically. The recommended Claude profile is `Opus 4.7 with 1M context`.

### Claude Desktop

When Claude Desktop is installed, `nexo-brain`, `nexo update`, and `nexo clients sync` keep `claude_desktop_config.json` pointed at the same local NEXO runtime and `NEXO_HOME`.

### Codex

When Codex CLI is available, `nexo-brain`, `nexo update`, and `nexo clients sync` register the same `nexo` MCP server via `codex mcp add`, so Codex uses the same local memory store as Claude Code and Claude Desktop. If selected during install, `nexo chat` can open Codex directly and background automation can also run through Codex. Interactive `nexo chat` launches use Codex's aggressive no-confirmation mode so the session does not stall on repetitive approval prompts. Codex uses the same model you configured during install — no separate model override is needed. Runtime Doctor also audits recent Codex sessions for NEXO startup markers and conditioned-file protocol discipline so parity drift does not hide behind the lack of native Claude-style hooks.

### Cursor

Cursor works well as a documented companion client. Point Cursor at the same local `nexo` MCP server and add a project rule that forces `nexo_startup`, `nexo_heartbeat`, and the protocol path on real work. See [docs/integrations/cursor.md](docs/integrations/cursor.md).

### Windsurf

Windsurf/Cascade supports MCP plus durable repo rules. Use the same local `nexo` server and add NEXO startup/protocol instructions in `.windsurf/rules/` or your repo `AGENTS.md`. See [docs/integrations/windsurf.md](docs/integrations/windsurf.md).

### Gemini CLI

Gemini CLI can share the same local NEXO brain through `mcpServers` in `~/.gemini/settings.json` plus a repo `GEMINI.md`. NEXO now ships a starter adapter in [adapters/gemini/README.md](adapters/gemini/README.md).

### OpenClaw

NEXO Brain also works as a cognitive memory backend for [OpenClaw](https://github.com/openclaw/openclaw):

#### MCP Bridge (Zero Code)

Add NEXO Brain to your OpenClaw config at `~/.openclaw/openclaw.json`:

```json
{
  "mcp": {
    "servers": {
      "nexo-brain": {
        "command": "python3",
        "args": ["~/.nexo/server.py"],
        "env": {
          "NEXO_HOME": "~/.nexo"
        }
      }
    }
  }
}
```

Or via CLI:

```bash
openclaw mcp set nexo-brain '{"command":"python3","args":["~/.nexo/server.py"],"env":{"NEXO_HOME":"~/.nexo"}}'
openclaw gateway restart
```

#### ClawHub Skill

```bash
npx clawhub@latest install nexo-brain
```

#### Native Memory Plugin

```bash
npm install @wazionapps/openclaw-memory-nexo-brain
```

```json
{
  "plugins": {
    "slots": {
      "memory": "memory-nexo-brain"
    }
  }
}
```

This replaces OpenClaw's default memory system with NEXO Brain's full cognitive architecture.

### Any MCP Client

NEXO Brain works with any application that supports the MCP protocol. Configure it as an MCP server pointing to `server.py` inside `NEXO_HOME` (default `~/.nexo/server.py`), with the `NEXO_HOME` env var set to the same directory.

## Listed On

| Directory | Type | Link |
|-----------|------|------|
| npm | Package | [nexo-brain](https://www.npmjs.com/package/nexo-brain) |
| Glama | MCP Directory | [glama.ai](https://glama.ai/mcp/servers/@wazionapps/nexo) |
| mcp.so | MCP Directory | [mcp.so](https://mcp.so/server/nexo/wazionapps) |
| mcpservers.org | MCP Directory | [mcpservers.org](https://mcpservers.org) |
| OpenClaw | Native Plugin | [openclaw.com](https://openclaw.ai) |
| dev.to | Technical Article | [How I Applied Cognitive Psychology to AI Agents](https://dev.to/wazionapps/how-i-applied-cognitive-psychology-to-give-ai-agents-real-memory-2oce) |
| Claude Code | Plugin (marketplace-ready) | Packaging verified, included in npm tarball |
| nexo-brain.com | Official Website | [nexo-brain.com](https://nexo-brain.com) |

## Support the Project

If NEXO Brain is useful to you, consider:

- **Star this repo** — it helps others discover the project and motivates continued development
- **[Sponsor on GitHub](https://github.com/sponsors/wazionapps)** — support ongoing development directly
- **Share your experience** — tell others how you're using cognitive memory in your AI workflows
- **Contribute** — see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines. Issues and PRs welcome
- **Client parity / shared-brain maintenance** — see [docs/client-parity-checklist.md](docs/client-parity-checklist.md)
- **Writing a personal script that calls the automation backend** — see [docs/personal-scripts-guide.md](docs/personal-scripts-guide.md)

[![Star History Chart](https://api.star-history.com/svg?repos=wazionapps/nexo&type=Date)](https://star-history.com/#wazionapps/nexo&Date)

## Memory Benchmark Snapshot

The full harness is in [benchmarks/README.md](benchmarks/README.md). The first checked-in micro-benchmark compares the NEXO runtime against a static `CLAUDE.md`-only baseline on five recall-heavy scenarios:

| Scenario | NEXO full stack | Static `CLAUDE.md` | No memory |
|----------|-----------------|--------------------|-----------|
| Decision rationale recall | Pass | Partial | Fail |
| User preference recall | Pass | Partial | Fail |
| Repeat-error avoidance | Pass | Partial | Fail |
| Resume interrupted task | Pass | Partial | Fail |
| Related-context stitching | Pass | Fail | Fail |

See [benchmarks/results/memory-recall-vs-static.md](benchmarks/results/memory-recall-vs-static.md) for the rubric, prompt shape, and first-run notes.

## Changelog

### v3.0.1 — Python 3.10 Compatibility Patch (2026-04-06)
- Restored Python 3.10 compatibility by replacing Python 3.11-only `datetime.UTC` with `timezone.utc`.
- Added `tomllib` → `tomli` fallback plus declared runtime dependency for Python < 3.11.
- Boot doctor now validates all critical JSON config artifacts: `schedule.json`, `optionals.json`, `crons/manifest.json`.

### v3.0.0 — Protocol Discipline, Durable Execution, Measured Runtime (2026-04-06)
- **Protocol discipline runtime**: Enforceable `nexo_task_open`/`nexo_task_close`, persistent `protocol_debt`, `Cortex` gates with durable `check_id`, conditioned-file guardrails across Claude hooks and Codex transcript audits.
- **Durable workflow runtime**: `nexo_workflow_open`/`update`/`resume`/`replay`/`list` with persistent runs, steps, checkpoints, replay history, retry bookkeeping, and idempotent open keys.
- **Durable goals**: `nexo_goal_open`/`update`/`get`/`list` for long-running work that stays active/blocked/abandoned/completed.
- **Operational truth**: Deep Sleep survives schema drift, `keep_alive` reports alive/degraded/duplicated honestly, warning storms no longer count as healthy.
- **Measured product surface**: 5-minute quickstart, Python SDK, reference verticals, measured compare scorecard with LoCoMo baselines and `cost_per_solved_task`.
- **Skill lifecycle**: Testing, promotion, retirement, and composition flows. Evolution public-core peer-review for opt-in PRs.

### v2.7.0 — Shared Brain Baseline (2026-04-06)
- Managed Claude Code + Codex bootstrap with explicit `CORE`/`USER` contract.
- Codex config sync and transcript-aware Deep Sleep across both clients.
- 60-day long-horizon analysis, weekly/monthly summary artifacts.
- Retrieval auto-mode and first measured engineering loop.
- `nexo chat` opens the configured client instead of assuming Claude Code.

### v2.6.9 — Integration Sync, CI/CD Pipeline (2026-04-04)
- **Release artifact sync**: Automated version synchronization across Claude Code plugin, OpenClaw package, and ClawHub skill before every publish.
- **CI/CD pipeline**: Full GitHub Actions workflow for publish + verification of all integration channels.
- **OpenClaw plugin hardened**: Contract tests, correct runtime path, synchronized version. Published as @wazionapps/openclaw-memory-nexo-brain@2.6.9.
- **ClawHub skill hardened**: Version-synced metadata, correct server path, post-publish smoke verification.
- **Claude Code plugin packaging**: Verified plugin.json, .mcp.json, hooks included in npm tarball. Marketplace-ready.

### v2.6.5 — Power Helper Hardening, Recovery Contracts (2026-04-04)
- Power helper semantics explicit and safer: `always_on` = platform helper for best-effort background availability.
- Catch-up recovery suppresses duplicate relaunches for in-flight `cron_runs`.
- Runtime update/startup reconciles declared personal schedules automatically.

### v2.6.3 — Cron Sync Fix, Hook Migration (2026-04-04)
- Runtime cron sync skips same-file copies, avoiding `SameFileError` on synced runtimes.
- Core hook migration normalizes legacy flat entries into Claude Code's required `matcher + hooks[]` format.

### v2.6.2 — Startup Preflight, Personal Recovery, Power Policy (2026-04-04)
- Startup preflight before `nexo chat` and server — safe local migrations, deferred remote updates.
- Personal managed schedules can declare recovery contracts (wake/boot/catchup).
- Persisted runtime power policy (`always_on`/`disabled`/`unset`). Installer and `nexo update` prompt once.
- Packaged installs resolve update root correctly (fixes `vunknown`).

### v2.6.0 — Personal Scripts Registry, Plugin Marketplace, Managed Evolution (2026-04-03)
- **Personal scripts registry**: Scripts in `NEXO_HOME/scripts/` tracked in SQLite with metadata, categories, schedules. Full lifecycle: create, sync, reconcile, schedule, unschedule, remove.
- **Orchestrator removed from core** (breaking): Was opt-in personal automation adding complexity for all users. Existing users keep their setup in `NEXO_HOME/scripts/`.
- **Claude Code plugin structure**: `plugin.json`, entry point, packaging for marketplace submission.
- **`nexo chat`**: Official command to launch a NEXO terminal client, asking when multiple supported terminal clients are available.
- **Managed Evolution hardening**: Can modify core behavior modules with rollback followups.
- Cron recovery hardened: TCC diagnostics, keepalive sync, personal schedule catchup.

### v2.5.0 — Runtime CLI, Doctor, Skills v2, Day Orchestrator (2026-04-03)
- **Runtime CLI** (`nexo`): New operational CLI separate from installer. `nexo scripts list/run/doctor/call` for personal scripts, `nexo doctor` for diagnostics, `nexo skills apply` for executable skills, `nexo update` for one-step sync.
- **Unified Doctor**: Modular diagnostic system with boot/runtime/deep tiers. Report-only by default, deterministic `--fix` mode. MCP tool `nexo_doctor`. LaunchAgent schedule drift detection and reconciliation.
- **Skills v2**: Executable skills with guide/execute/hybrid modes. Security levels (read-only/local/remote) with explicit approval. Core vs personal vs community directories. Deep Sleep auto-evolution integration.
- **Day Orchestrator**: Autonomous NEXO cycles every 15 min (8:00-23:00). Launches Claude Code headless with full MCP. Checks followups, emails, infra — acts autonomously, emails user only when needed. Opt-in.
- **Dashboard always-on**: Web UI at localhost:6174 as persistent LaunchAgent. 23 modules, Jinja2 templating, dark theme. Opt-in.
- **Personal Scripts Framework**: Auto-discovery in NEXO_HOME/scripts/, inline metadata, runtime detection, forbidden-pattern validation, vendorable helper, template.
- Configurable operator name (UserContext singleton), watchdog normalized to 30 min, LaunchAgent drift fix.

### v2.4.0 — Skills, Cron Scheduler, Security, Full Audit (2026-04-03)
- **Skill Auto-Creation**: Deep Sleep extracts reusable procedures from sessions. Content stored as markdown with steps and gotchas. Trust pipeline with autonomous quality control.
- **Cron Scheduler**: execution tracking (`cron_runs` table), `nexo_schedule_status` and `nexo_schedule_add` MCP tools, universal cron wrapper for all processes.
- **Deep Sleep v2.4**: watermark-based collection (late-night sessions included), per-session checkpointing (crash-safe), retry x3, JSON parsing fix, auto-calibration of personality settings.
- **Security**: credential redaction in tool logs, transcript sanitization, command injection fix in dashboard, path traversal protection in plugin loader.
- **Diary filter**: startup only shows human sessions, auto-closed cron sessions filtered out. Email sessions preserved as real interactions.
- **Preflight CI**: 66 automated checks (py_compile, bash -n, manifest consistency, npm artifact, forbidden markers).
- **Python 3.9 compat**: `from __future__ import annotations` across 18 files.
- **Linux**: full systemd timer support, .bashrc alias for interactive shells.
- Passed 5-phase automated audit: Product, Failure, Security, Packaging, UX.

### v2.2.0 — Trust Score v2 (2026-04-01)
- **Trust Score**: fair daily calibration from Deep Sleep analysis. Score 0-100 based on corrections, autonomy, proactivity.
- **Cognitive Quarantine**: new memories go through quarantine before promotion to LTM.

### v2.0.0 — Unified Architecture (2026-03-31)
- **Code/data separation**: Code in repo (`src/`), personal data in `NEXO_HOME` (default `~/.nexo/`). `NEXO_HOME` env var required.
- **Plugin loader dual-directory**: Scans `src/plugins/` (base) then `NEXO_HOME/plugins/` (personal override by filename).
- **Auto-update on startup**: Non-blocking (5s max), resilient, opt-out via `schedule.json`. Separate from manual `nexo_update` tool.
- **Auto-diary**: 3-layer system — PostToolUse every 10 calls, PreCompact emergency save, heartbeat DIARY_OVERDUE signal.
- **CLAUDE.md version tracker**: Section markers enable safe core updates without losing user customizations.
- **schedule.json**: Customizable process schedules with timezone support and `auto_update` flag.
- **15 autonomous processes**: Added auto-close-sessions, synthesis, backup, tcc-approve, prevent-sleep (cross-platform).
- **7 hooks**: SessionStart (timestamp + briefing), Stop, PostToolUse (capture + inbox), PreCompact, PostCompact.
- **150+ MCP tools**: Added `nexo_update` tool for manual updates with rollback.
- **Lambda fix**: Decay values were 24x too aggressive (STM: 7h to 7d, LTM: 2.4d to 60d).
- **Guard scoping**: Was returning 35+ irrelevant blocking rules; now scoped to area and gated to high/critical.
- **12 rounds of external audit**: ~60 findings resolved.

### v1.7.0 — Full Internationalization + Linux Support (2026-03-31)
- **Full i18n**: All UI strings, error messages, DB status values in English. NLP detection patterns retain bilingual keywords (Spanish + English) for multilingual user support.
- **Linux support**: systemd user timers (preferred) or crontab fallback for all automated cognitive processes.
- **Auto-resolve followups**: Change log entries automatically cross-reference and complete matching open followups.
- **Free-form learning categories**: No more hardcoded category validation — use any category name.
- **CLAUDE.md template rewrite**: 494 to 127 lines, compact procedural format with full heartbeat signal reactions.
- **Complete sanitization**: All hardcoded paths use `NEXO_HOME` env var. No credentials or personal data in the distributed package. Migration scripts and maintainer tooling use configurable paths.

### v1.6.0 — Nervous System + Dashboard v2 (2026-03-30)
- **Nervous System**: 11 autonomous scripts (decay, deep sleep, self-audit, catchup, evolution, followup hygiene, immune, watchdog, github monitor, learning validator)
- **Dashboard v2**: 6 interactive pages at localhost:6174 (Overview, Graph, Memory, Somatic, Adaptive, Sessions)
- **LaunchAgent Templates**: macOS automation templates included in the package for scheduling the nervous system
- **Hooks**: 7 total — SessionStart, Stop, PostToolUse, PreCompact, PostCompact
- **Installer**: Now configures dashboard LaunchAgent, nervous system scripts, and all templates automatically

### v1.5.2 — Deep Sleep (2026-03-29)
- **Deep Sleep**: Reads full session transcripts (not just diary) — finds uncaptured corrections, protocol violations, missed commitments
- Uses Claude CLI in `--bare` mode (no hooks, no CLAUDE.md interference)
- Catch-up system re-runs yesterday if the Mac was off

### v1.5.0 — Modular Core + Knowledge Graph Search (2026-03-29)
- **Architecture**: `db.py` refactored into `db/` package (11 modules); `cognitive.py` into `cognitive/` package (6 modules)
- **KG Boost**: Knowledge Graph connection count influences search result ranking
- **HNSW Vector Index**: Optional approximate nearest neighbor acceleration (auto-activates above 10,000 memories)
- **Claim Graph**: Decomposes blob memories into atomic verifiable facts with provenance and contradiction detection
- **Inter-terminal Auto-inbox (D+)**: `nexo_startup` accepts `claude_session_id` for automatic inbox delivery between parallel terminals
- **Tests**: 156 pytest tests across 3 suites (cognitive, knowledge graph, migrations)

### v1.4.1 — Multi-AI Code Review (2026-03-29)
- **Fix**: 3 bugs found by GPT-5.4 (Codex CLI) + Gemini 2.5 (Gemini CLI) reviewing full codebase
- **Security**: Memory sanitization prevents prompt injection via stored content
- **Migration #13**: Normalizes legacy status values on upgrade

### v1.4.0 — The Brain Dreams (2026-03-29)
- **Major**: All 9 nightly scripts migrated from Python word-overlap to CLI wrapper pattern
- **Stop Hook v8**: Session-scoped tool counting, buffer fallback removed
- **Guard**: Behavioral rules section surfaces most-violated rules at session start

### v1.3.0 — Evolution System (2026-03-28)
- **New**: Self-improvement cycle — NEXO proposes and applies improvements weekly
- Dual-mode: auto (low-risk) and review (owner approval required)
- Circuit breaker, snapshot/rollback, immutable file protection

### v1.2.3 — AGPL-3.0 License (2026-03-27)
- License changed from MIT to AGPL-3.0

### v1.2.1 — Stop Hook Hotfix (2026-03-27)
- **Fix**: v1.2.0 deleted the flag on approve, causing infinite block loops if session didn't close immediately
- **Fix**: Removed TTL on flag — it persists until SessionStart cleans it up next session
- **New**: Trivial sessions (<5 meaningful tool calls) skip post-mortem entirely and approve immediately
- SessionStart hook now cleans up `.postmortem-complete` flag on session start

### v1.2.0 — Blocking Stop Hook (2026-03-27)
- **Fix**: Stop hook now uses `"decision": "block"` instead of `"approve"` to enforce post-mortem execution
- Previous behavior: hook injected `systemMessage` but AI had already responded — instructions were never processed
- New behavior: session close is blocked until AI completes self-critique, session diary, buffer entry, and followups
- Flag-based mechanism (`.postmortem-complete`) allows second close attempt to succeed
- Works for all NEXO users, not just specific setups

### v1.1.1 — Multi-terminal fix (2026-03-27)
- **Fix**: PostCompact now reads the correct session's checkpoint in multi-terminal setups
- Changelog section added to README

### v1.1.0 — Context Continuity (2026-03-27)
- **Context Continuity**: PreCompact/PostCompact hooks preserve session state across compaction events
- New `session_checkpoints` SQLite table + migration #12
- New tools: `nexo_checkpoint_save`, `nexo_checkpoint_read`
- Heartbeat automatically maintains checkpoint every interaction
- Core Memory Block re-injected post-compaction with task, files, decisions, reasoning thread
- 115+ total tools at the time, 20 categories

### v1.0.0 — Cognitive Cortex + Stable Release (2026-03-26)
- **Cognitive Cortex**: architectural inhibitory control (ASK/PROPOSE/ACT modes)
- 30 Core Rules as immutable DNA in SQLite
- Designed via 3-way AI debate (Claude Opus + GPT-5.4 + Gemini 3.1 Pro)
- Artifact Registry for operational facts
- Full benchmark suite (LoCoMo F1: 0.588)

### v0.10.0 — Smart Context (2026-03-22)
- Smart Startup: pre-loads memories from pending followups + diary
- Context Packet: structured injection for subagents
- Auto-Prime: keyword-triggered area learnings in heartbeat
- Diary Archive: permanent subconscious memory (180d+ auto-archived)

### v0.9.0 — Cognitive Memory (2026-03-15)
- Atkinson-Shiffrin memory model (STM → LTM promotion)
- Semantic RAG with pinned local multilingual fastembed models
- Trust scoring, sentiment detection, adaptive personality modes
- Ebbinghaus decay, sister detection, quarantine system

## License

AGPL-3.0 -- see [LICENSE](LICENSE)

---

Created by **Francisco Cerdà Puigserver** & **NEXO** (Claude Opus) · Built by [WAzion](https://www.wazion.com)
