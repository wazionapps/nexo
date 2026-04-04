# Changelog

## [2.6.3] - 2026-04-04

### Fixes
- Runtime cron sync now skips same-file copies when core scripts already live under `NEXO_HOME`, avoiding `SameFileError` during `nexo update` on synced runtimes.
- Core hook migration now normalizes legacy flat hook entries into Claude Code's required `matcher + hooks[]` format instead of re-emitting invalid `PostToolUse` entries.
- Plugin metadata version is aligned again with the published package version.

## [2.6.2] - 2026-04-04

### Startup Preflight & Recovery
- Startup preflight now runs before `nexo chat` and server startup, applying safe local migrations/backfills and deferring remote updates when the runtime is busy.
- Dev-linked runtime updates now use backup + rollback around source-pull + runtime sync instead of a blind copy.
- Personal managed schedules can now declare recovery contracts (`run_once_on_wake`, `catchup`, boot/wake flags, catchup window) and are included in catchup recovery.

### Power Policy
- Added persisted runtime power policy (`always_on` / `disabled` / `unset`) in `schedule.json`.
- Installer and interactive `nexo update` now prompt once for the optional prevent-sleep policy.
- `prevent-sleep` is now opt-in instead of being installed implicitly.

### Fixes
- Packaged/runtime installs now resolve their update root correctly and read the installed version from `version.json`, avoiding `Already up to date (vunknown)`.
- Catchup now accepts managed personal script paths directly, not just core runtime-relative scripts.

## [2.6.0] - 2026-04-03

### Personal Scripts — First-Class Citizen
- **Personal scripts registry**: Scripts in `NEXO_HOME/scripts/` are now tracked in SQLite with metadata, categories, and schedule associations. `nexo_personal_scripts_list`, `nexo_personal_script_create`, `nexo_personal_script_remove` MCP tools.
- **Lifecycle reconciliation**: `nexo_personal_scripts_reconcile` detects orphaned scripts, missing DB entries, and stale schedules. `nexo_personal_scripts_sync` syncs filesystem state into the registry.
- **Schedule lifecycle**: Personal scripts can be scheduled/unscheduled via `nexo_personal_script_schedules` and `nexo_personal_script_unschedule`. Full integration with LaunchAgent/systemd generation.
- **Script templates**: `templates/script-template.sh` and `templates/script-template.py` with inline metadata format and NEXO env injection.

### Orchestrator Removed from Core
- **Breaking**: The Day Orchestrator is no longer part of the core product. It was an opt-in personal automation that added complexity for all users. Existing orchestrator users can keep their personal setup in `NEXO_HOME/scripts/`.
- Orchestrator LaunchAgent and related code removed from manifest and sync.

### Claude Code Plugin Structure
- **Marketplace-ready**: Added `plugin.json`, entry point, and packaging structure for submission to the Claude Code plugin marketplace (Anthropic).
- Plugin metadata includes capabilities, required permissions, and installation instructions.

### Runtime CLI Enhancements
- **`nexo chat`**: Official command to launch Claude Code with NEXO as operator. Supports directory argument.
- **Runtime version surfacing**: `nexo -v` and CLI help now show the correct version from the installed runtime, not just the repo.
- **Self-sync prevention**: Runtime updates no longer trigger redundant self-sync cycles.

### Managed Evolution Hardening
- Evolution can now modify core behavior modules (not just config) when running in managed mode with rollback followups.
- Fixed false-positive watchdog tamper detection that was disabling Evolution after hash recovery.

### Cron & Runtime Reliability
- Hardened cron runtime recovery: TCC diagnostics, keepalive sync alignment, disabled optional cron respect.
- Catchup script handles personal schedules correctly during boot recovery.
- Runtime release rollout gaps closed — installed environments receive all files consistently.

### Fixes
- Prevent duplicate learning titles before insert (exact-title guard)
- Orchestrator prompt tuned: skip startup ceremony, direct email, 50-turn limit
- Runtime CLI version correctly surfaces in installed (non-repo) environments

## [2.5.1] - 2026-04-03

### Added
- Custom CLI help screen with auto-version from package.json, `nexo -v`
- Dashboard/Orchestrator control: `nexo dashboard on|off|status`, `nexo orchestrator on|off|status`
- Managed Evolution mode: auto-executes deterministic improvements with rollback + followups

### Fixed
- False-positive watchdog tamper detection disabling Evolution

## [2.5.0] - 2026-04-03

### Runtime CLI (`nexo`)
- New `nexo` command for operational tasks — separate from `nexo-brain` installer
- `nexo scripts list/run/doctor/call` — personal scripts framework with auto-discovery, inline metadata, forbidden-pattern validation
- `nexo doctor --tier boot|runtime|deep|all` — unified modular diagnostic system
- `nexo skills list/apply/sync/approve` — skills v2 with executable scripts
- `nexo update` — sync all repo files to NEXO_HOME in one command

### Unified Doctor
- Modular check providers: boot (<100ms), runtime (<5s), deep (<60s)
- Report-only by default, deterministic `--fix` mode
- MCP tool `nexo_doctor` via plugin
- LaunchAgent schedule drift detection and reconciliation

### Skills v2 — Executable Skills
- Three modes: guide (text), execute (script), hybrid (both)
- Security levels: read-only, local, remote — with explicit approval
- Core vs personal vs community skill directories
- Deep Sleep integration for automatic skill evolution

### Day Orchestrator
- Autonomous NEXO cycles every 15 min (8:00-23:00)
- Launches Claude Code in headless mode with full MCP access
- Checks followups, emails, infrastructure — acts on what it can
- Emails user only when needed

### Dashboard Always-On
- Web dashboard at localhost:6174 as persistent LaunchAgent
- 23 modules with Jinja2 templating and dark theme (v3.0)

### Other
- Configurable operator name via UserContext singleton
- Watchdog schedule normalized to 30 min
- LaunchAgent drift reconciliation in doctor --fix

## [2.4.0] - 2026-04-02

### Skills System
- Skills store full procedural content (steps, gotchas, markdown)
- Deep Sleep correctly populates skills with step-by-step procedures
- Migration #18 adds content/steps/gotchas columns

### Security Fixes
- Credential redaction in tool logs (capture-tool-logs.sh)
- Sensitive data redaction in Deep Sleep transcripts
- Command injection fix in dashboard followup executor
- Path traversal protection in plugin loader

### Cron Scheduler
- Execution tracking (cron_runs table) + `nexo_schedule_status` MCP tool
- Deep Sleep: watermark collection, checkpointing, retry, JSON fix
- Preflight CI: 66 automated checks

### UX/Docs
- README accuracy pass: dashboard, alias, integration paths corrected
- Bash alias written to .bashrc for Linux users
- Dashboard shows real error messages instead of generic 'Failed'

## [2.3.0] - 2026-04-02

### Added
- Cron execution tracking (cron_runs table + nexo_schedule_status)
- Deep Sleep: watermark collection, checkpointing, retry, skill extraction, auto-calibration
- Linux systemd full support
- Preflight CI (64 checks)

### Fixed
- 3 broken scripts identified and fixed during audit
- Manifest as single source of truth for cron definitions
- README aligned with actual feature state

## [2.2.0] - 2026-04-01

### Trust Score v2 — Fair Scoring System
- **Deep Sleep Trust Calibration (Phase 7)**: overnight analysis scores the entire day 0-100, replacing volatile incremental adjustments with a holistic evaluation
- **Language-agnostic detection**: removed hardcoded Spanish/English keyword patterns — trust events are now emitted by the LLM via semantic instructions (works in ALL languages)
- **New positive events**: `task_completed` (+1 per followup completed), `session_productive` (+2), `clean_deploy` (+1) — fixes the downward spiral where the score could only decrease
- **Auto patterns for `proactive_action` and `paradigm_shift`**: previously defined but never detected
- **`explicit_thanks` default boosted to +5** (was +3)
- **Scoring guide in synthesis prompt**: 90-100 flawless, 70-89 good, 50-69 average, 30-49 below average, 0-29 bad day

### Fixes
- `nexo_followup_complete` emits `task_completed` trust event automatically
- Trust MCP instructions tell the LLM to detect intent, not keywords — a Chinese or Arabic user now gets the same trust tracking as a Spanish user

## [2.1.1] - 2026-04-01

### Fixes
- Harden all hooks against empty stdin and set -e failures

## [2.1.0] - 2026-04-01

### Deep Sleep v2 — Overnight Learning Pipeline
- 4-phase pipeline: Collect → Extract → Synthesize → Apply
- Collect: splits sessions into individual .txt files (one per session)
- Extract: Opus analyzes each session for 8 types of findings
- Synthesize: cross-session patterns, morning agenda, context packets
- Apply: auto-creates learnings, followups, morning briefing
- NEXO_HEADLESS env var: skips stop hook in CLI subprocesses

### Emotional Intelligence (Deep Sleep Increment 2)
- Emotional signal detection: frustration, flow, satisfaction, disengagement
- Daily mood arc with score (0.0-1.0) and recurring triggers
- Abandoned project detection (cross-referenced with existing followups)
- Productivity patterns: corrections, proactivity, tool efficiency
- Session tone generator: adapts next-day greeting based on mood + mistakes
- Calibration recommendations: auto-suggests personality adjustments
- mood_history saved in calibration.json (last 30 days)

### Cron Manifest System
- manifest.json defines 14 core crons with schedule/interval
- sync.py reconciles manifest with system LaunchAgents (macOS)
- nexo_update auto-syncs crons after pulling code
- Personal crons never touched by sync
- PYTHONUNBUFFERED=1 in all generated plists

### Fixes
- Stop hook stdout contamination: root cause of all Deep Sleep Phase 2 failures
- All CLI timeouts unified to 6h (21600s) across 25 scripts
- Synthesize fallback when Opus writes file directly via Write tool
- Removed --bare from health checks that blocked background scripts
- Dashboard shows correct session and followup counts
- Followup queries filter archived/blocked/waiting status

## [2.0.0] - 2026-03-31

### Breaking Changes
- Code and data separated: code in repo/NEXO_CODE, data in NEXO_HOME
- NEXO_HOME env var required (default ~/.nexo/)
- DB location: NEXO_HOME/data/nexo.db and cognitive.db
- Evolution config moved from cortex/ to brain/
- nexo-install.py deprecated (use npx nexo-brain)
- nexo-auto-update.py deprecated (auto-update built into server startup)

### Added
- Unified architecture: single source of code, personal data in NEXO_HOME
- Plugin loader: scans repo plugins/ then NEXO_HOME/plugins/ (personal override)
- Auto-update on startup: non-blocking (5s max), resilient, opt-out via schedule.json
- Auto-diary: 3-layer system (PostToolUse every 10 calls, PreCompact emergency, heartbeat DIARY_OVERDUE)
- CLAUDE.md version tracker: section markers for safe core updates without losing customizations
- schedule.json: customizable process schedules with timezone support
- All 15 processes auto-installed: watchdog, immune, synthesis, backup, catchup, cognitive-decay, postmortem, self-audit, sleep, deep-sleep, evolution, followup-hygiene, prevent-sleep, tcc-approve, auto-close-sessions
- All 7 hooks auto-installed: session-start, session-stop (postmortem), capture-tool-logs, inbox-hook, pre-compact, post-compact, session-timestamp
- prevent-sleep: cross-platform (caffeinate on macOS, systemd-inhibit on Linux)
- tcc-approve: auto-approve macOS permissions for Claude Code updates
- nexo_update MCP tool + nexo-update.sh standalone script
- Installer asks for data directory (NEXO_HOME) in 6 languages
- evolution-objective.json backfill for existing installs
- scripts/ backfill for existing installs
- Claude CLI calls hardened with --bare + real auth pre-check

### Fixed
- Lambda decay values were 24x too aggressive (STM: 7h→7d, LTM: 2.4d→60d)
- MCP instructions truncated (3458→1302 chars)
- Guard returned 35+ irrelevant blocking rules (now scoped to area, gated to high/critical)
- Recurring followup returned wrong ID and left FTS index inconsistent
- Server.py had side effects on import (now wrapped in _server_init + __main__)
- sqlite3 import missing in cognitive/_search.py
- Runtime-preflight, watchdog-smoke, self-audit referenced legacy cortex/ layout
- 12 rounds of external audit, ~60 findings resolved

### Changed
- All scripts use NEXO_HOME/NEXO_CODE env vars (auto-detect from repo location)
- All UI strings in English (NLP patterns retain bilingual keywords)
- README: honest credential storage description (was "secure")
- Single installer (nexo-brain.js), single update engine (auto_update.py)
- Dashboard: platform guard for osascript (returns 501 on Linux)

### Known Issues
- Credentials stored in plaintext SQLite (P0 for v2.1.0)
- Shell hooks use SQL interpolation (P0 for v2.1.0)
- Dashboard has no auth (localhost only, P0 for v2.1.0)
- Migrations are fail-open (P0 for v2.1.0)

## [1.8.0-beta.3] - 2026-03-31

### Fixed
- **core_rules table missing**: Added migration M15 creating `core_rules` and `core_rules_version` tables. Plugin crashed on fresh install because tables were never created. Seeds version row for update tracking.
- **core_rules plugin hardened**: `_seed_if_empty()` now handles missing table gracefully instead of crashing.
- **Personal data sanitized**: Removed "Francisco" reference in `tools_sessions.py` comment. Migration script patterns marked as legacy with generic alternatives added.
- **Anti-duplicate followup restored**: `create_followup()` now calls `find_similar_followups()` before inserting and warns on potential duplicates (non-blocking).
- **Auto-resolve reporting restored**: `handle_change_commit()` now reports which followup IDs were auto-resolved in its return message.

## [1.8.0-beta.2] - 2026-03-31

### Fixed
- **Instructions truncation**: MCP instructions reduced from 3458 to 1302 chars. Previous version was silently truncated by Claude Code's system reminder injection, causing the last rules (Diary, Cortex, Change Log) to be lost.
- **Heartbeat param naming**: Instructions now show explicit parameter names (`sid=SID, task='...'`) instead of ambiguous "SID + task" which caused LLM parameter guessing errors.
- **Guard noise reduction**: Universal rules now scoped to matching area + nexo-ops (was: all learnings with NUNCA/SIEMPRE keywords). Blocking rules gated to high/critical priority only. Output caps: learnings max 10 (was 15), universal max 5 (was 10).

### Changed
- **Instructions format**: Dense bullets with explicit tool signatures instead of H3 prose sections. Same 11 rules, 62% fewer chars.

## [1.8.0-beta.1] - 2026-03-31

### Added
- **Hybrid Architecture**: Tool-coupled behavioral rules moved from CLAUDE.md to the MCP server `instructions` field. Rules are now protocol-level, injected at the same priority as CLAUDE.md.
- **Migration script** (`migrate-v1.7-to-v1.8.py`): Automatically slims existing CLAUDE.md files by removing sections that are now MCP-owned. Idempotent with backup.
- **Expanded MCP instructions**: Heartbeat, Guard, Delegation, Reminders, Memory, Trust Score, Dissonance, Change Log, Session Diary, and Cortex rules now ship with the server.

### Changed
- **CLAUDE.md template**: 130 → 50 lines. Now contains only bootstrap (identity, profile, format, autonomy, project atlas, hooks). All tool-coupled rules removed.
- **Context token savings**: ~3K fewer tokens consumed per session by eliminating rule duplication between CLAUDE.md and MCP instructions.


## [1.7.0] - 2026-03-31

### Added
- **Linux support**: systemd user timers (preferred) or crontab fallback for automated cognitive processes. Same 4 scheduled tasks as macOS LaunchAgents.
- **Auto-resolve followups**: Change log entries automatically cross-reference and complete matching open followups via file overlap, keyword similarity, and ID reference.
- **Find similar followups**: Duplicate detection before creating new followups using asymmetric keyword overlap scoring.
- **Free-form learning categories**: Removed hardcoded category validation. Users can now use any category name (e.g., 'backend', 'frontend', 'devops').

### Changed
- **Full internationalization**: All UI strings, error messages, labels, and DB status values translated to English. Status values: `PENDING`, `COMPLETED`, `DELETED` (previously Spanish).
- **CLAUDE.md template rewrite**: 494→127 lines. Compact, procedural format. Same capabilities, zero prose.
- **Complete sanitization**: Removed all personal data, hardcoded paths, and project-specific references from the entire codebase. Every file uses `NEXO_HOME` env var.
- **Deleted 48 macOS Finder duplicate files** ("file 2.ext" pattern).

### Fixed
- Syntax error in `nexo-evolution-run.py` from automated NEXO_HOME insertion.
- Hardcoded locale (Europe/Madrid, es_ES) in menu date formatting.
- Personal directory path in FTS code indexing configuration.

## [1.7.0-beta.1] - 2026-03-30

### Added
- **Migration system** (`nexo-migrate.py`): Automatic, idempotent upgrades between versions with backup-before-migrate and a versioned migration registry.
- **Install script** (`nexo-install.py`): First-time setup that creates `~/.nexo/` structure, initializes databases, copies repo files, and sets `NEXO_HOME` in shell profile.
- **Diary brief mode**: `nexo_session_diary_read` now accepts `brief=True` to return only the last entry's summary + mental_state + context_next (~1K chars) for fast startup.

### Changed
- **Heartbeat optimization** (8 ops down to 3): Removed sentiment detection, trust auto-detect, adaptive mode computation, RAG retrieval, and auto-prime from the heartbeat hot path. These cognitive features remain available on-demand via dedicated tools (`nexo_cognitive_sentiment`, `nexo_cognitive_trust`, `nexo_cognitive_retrieve`, `nexo_context_packet`).
- **Path standardization**: All files now use `NEXO_HOME` env var with `~/.nexo` fallback instead of hardcoded `~/claude` paths. Affected: `_fts.py`, `auto_close_sessions.py`, `nexo-watchdog.sh`, `nexo-deep-sleep.sh`, `nexo-postmortem-consolidator.py`, `dashboard/app.py`.

### Fixed
- Dashboard watchdog endpoint used `~/claude` as fallback instead of `~/.nexo`.

## [1.6.0] - 2026-03-29

### Added
- Artifact registry for tracking services, dashboards, scripts, and APIs.
- Session checkpoints for intelligent auto-compaction.
- Claude session ID linking for inter-terminal coordination.
- Learnings priority, weight, and guard usage tracking.
- Deep sleep analysis system with catch-up for missed days.
- Watchdog comprehensive health monitor (15+ services).
- Postmortem consolidator v2 with Claude CLI intelligence.

### Changed
- Episodic memory plugin expanded with diary archive (permanent subconscious memory).
- FTS5 unified search with cross-language synonyms and code file indexing.
