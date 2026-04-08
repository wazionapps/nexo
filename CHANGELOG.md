# Changelog

## [3.1.4] - 2026-04-08

### History Integrity Hotfix
- Closed the gap where public core scripts still mutated reminders/followups outside the new history model. `daily self-audit`, `deep sleep apply`, and `followup hygiene` now record history-aware followup/reminder mutations instead of silently bypassing the operational timeline.
- Followup creation now accepts priority at creation time through the public stack, which removes the last raw post-insert priority patch in the MCP server path.
- Hardened learning auto-capture so `self-audit` no longer depends on a fragile reread after metadata updates when creating repair learnings inline.
- Added regression coverage for history-aware self-audit followup creation/completion, deep-sleep duplicate consolidation notes, hygiene normalization events, and priority-aware followup creation.

## [3.1.3] - 2026-04-08

### History-Aware Reminders + Followups
- Reminders and followups now keep an append-only operational history (`created`, `updated`, `completed`, `deleted`, `restored`, `note`, and recurring archive/spawn events) so agents can reconstruct what happened instead of overwriting state blindly.
- Delete is now soft for both reminders and followups. Completed, deleted, and archived items remain queryable, which means NEXO can inspect old operational context instead of losing it permanently.
- Added history-aware `get`, `note`, and `restore` MCP tools plus read-token enforcement for update/delete/restore/note flows. Agents now have to read the item history first before mutating it through the public MCP surface.
- The dashboard now follows the same model: create/update actions log history, delete becomes soft delete, moved reminder/followup items preserve the source row as deleted, and per-item API detail endpoints expose history.

## [3.1.2] - 2026-04-08

### Evolution Load Balancing + Runtime Sync Fixes
- Evolution cron scheduling is no longer hard-pinned to Sunday 05:00 for every managed install. Core cron sync now derives a stable machine-staggered weekly slot so public evolution PRs are distributed across the week instead of bunching on one day.
- `nexo update` now includes top-level runtime modules such as `hook_guardrails.py`, `protocol_settings.py`, and `public_evolution_queue.py`, closing the gap where protocol-discipline and self-audit runtime behavior could drift locally even after a successful update.
- Learning creation now verifies persistence immediately after insert and returns an explicit failure if the new active learning cannot be read back from storage.

## [3.1.1] - 2026-04-08

### Update Reliability Hotfix
- Fixed personal script registry ID generation so `nexo update` and runtime reconciliation no longer fail when two distinct personal scripts share the same logical `name` but live at different paths, such as paired `.py` and `.sh` variants of the same workflow.
- This specifically restores safe runtime updates on installations that keep both shell and Python implementations for the same personal automation.

## [3.1.0] - 2026-04-08

### Self-Audit Goes Corrective
- The daily self-audit now resolves contradiction, formalization, and prevention findings inline instead of leaving behind orphan `NF-CONTRADICTION-*`, `NF-FORMALIZE-*`, and `NF-PREVENTION-*` followups. Conflicting learnings are superseded, recurring themes are formalized directly, and failure clusters produce canonical prevention learnings during the same run.
- Mechanical post-audit fixes now run automatically for managed bootstraps, mutable watchdog registry drift, golden snapshots, and syntactically broken personal plugins, shrinking the class of “audit found it but nobody fixed it” failures.
- When an inline self-audit fix touches public-core paths, NEXO now queues a durable public-port candidate so the public Evolution cycle can still surface that improvement upstream instead of losing it inside private maintenance.

### Stricter Client Discipline + Onboarding
- Added opt-in `protocol_strictness` modes (`lenient`, `strict`, `learning`) with pre-write hook enforcement and explanatory guidance for newer users who want protocol discipline to fail loudly instead of silently accumulating debt.
- Added first-party Gemini CLI adapter docs plus dedicated Cursor and Windsurf integration guides, and expanded the README client matrix so non-Claude users can bootstrap NEXO without reverse-engineering the Claude-specific setup.
- Added a repo-root `docker-compose.yml`, a persistent-volume Docker setup guide, and MCP health checks so containerized installs can come up with durable `NEXO_HOME` state out of the box.

### Workflow + Measurement Surfaces
- Added a workflow quickstart with practical `open`, `resume`, `replay`, and `handoff` examples so durable runs are easier to adopt without reading architecture docs first.
- Added a benchmark package that compares NEXO recall against a static `CLAUDE.md` baseline across decision recall, preference recall, repeat-error avoidance, interrupted-task resume, and related-context stitching, with reproducible scenarios and starter results.

## [3.0.2] - 2026-04-07

### Public Evolution Hardening
- Public opt-in contribution now resumes immediately after a maintainer merges or closes the machine's Draft PR instead of entering a stale cooldown.
- Public contribution runners now preserve `active_pr_*` metadata correctly after creating a Draft PR, so the machine stays paused on its own PR and can resume cleanly afterward.
- Public diff sanitization no longer rejects valid Linux-facing changes just because a patch contains the generic literal `/home/`; it still blocks real absolute user-home paths.

### Reliability Sweep Across Runtime Surfaces
- Fixed the watchdog/self-audit contract so self-audit findings no longer look like cron crashes when the audit completed correctly.
- Fixed a maintenance/runtime timezone regression and hardened doctor orchestration so one bad tier/provider no longer takes down broader health reporting.
- Fixed Linux cron sync weekday mapping so `weekday=0` correctly means Sunday and the `weekday=7` Sunday alias works without indexing errors.
- Fixed catch-up locking so an early crash still releases the lock and overlapping recovery runs cannot be blocked by a stale in-process handle.
- Prevented file migration failures from causing permanent future skips.

### SQLite Lifecycle Cleanup
- Wrapped SQLite usage in `try/finally` across backup/restore, doctor providers, state watchers, evolution cycle data gathering, knowledge-graph somatic backfill, and embedding migration flows so connections are always closed on exceptions.

## [3.0.1] - 2026-04-06

### Python 3.10 Compatibility Hotfix
- Fixed the `datetime.UTC` regression introduced in `v3.0.0`, replacing Python 3.11-only timezone constants in live runtime surfaces with Python 3.10-safe `timezone.utc` handling.
- Added a Python `<3.11` fallback from `tomllib` to `tomli` across client/runtime modules and declared `tomli` in runtime requirements so fresh installs on Python 3.10 no longer need an accidental transitive dependency.
- This patch specifically restores repo-based `nexo chat` / startup flows, `doctor`, state watchers, session portability, and scorecard generation on Python 3.10 runtimes instead of failing during import.

### Boot-Tier Validation Hardening
- Boot doctor config parsing now validates all critical JSON config artifacts (`schedule.json`, `optionals.json`, and `crons/manifest.json`) instead of only checking `schedule.json`.
- Added regression coverage for broken manifest / optionals payloads and healthy multi-file config parsing, so boot tier catches silent cron-manifest corruption before runtime reliability degrades.

## [3.0.0] - 2026-04-06

### Protocol Discipline Runtime
- Shipped the first enforceable protocol-discipline runtime slice as one cohesive package instead of more advisory markdown: `nexo_task_open`, `nexo_task_close`, persistent `protocol_debt`, simplified managed bootstraps, and live protocol-compliance scoring in runtime doctor.
- `Cortex` now issues persistent `check_id` gates, so high-impact work can be opened under a durable reasoning contract instead of relying on the model to “remember” it should verify first.
- Conditioned-file learnings now behave like real guardrails: Claude hook guardrails create durable debt on conditioned-file reads/writes outside protocol, Codex transcript audits classify read/write/delete violations, and contradictory active file-scoped learnings are superseded instead of accumulating silently.
- Repair/correction work now routes through canonical learning capture before a debt/followup fallback, which closes the gap where the model knew it should write a learning but did not.

### Durable Execution + Executive Function
- Added the first durable workflow runtime: `nexo_workflow_open`, `nexo_workflow_update`, `nexo_workflow_resume`, `nexo_workflow_replay`, and `nexo_workflow_list`, backed by persistent workflow runs, steps, checkpoints, replay history, retry bookkeeping, and idempotent open keys.
- Added durable goals on top of that runtime with `nexo_goal_open`, `nexo_goal_update`, `nexo_goal_get`, and `nexo_goal_list`, so long-running work can stay active/blocked/abandoned/completed without collapsing into loose reminders.
- Shipped shared execution state, human approval gates, compensation/rollback metadata, attention management, and anticipatory warnings as first-class runtime primitives instead of leaving them to prompts.

### Operational Truth + Prevention
- Closed the silent-degradation paths that were still undermining trust: Deep Sleep collection now survives schema drift safely, `keep_alive` jobs like `wake-recovery` report alive/degraded/duplicated truthfully, and repeated warning storms no longer count as healthy just because a wrapper exited `0`.
- Runtime doctor now treats automation telemetry coverage as a real health signal, and the shared automation runner records backend usage/cost data across both Claude Code and Codex.
- Release readiness now resolves the active runtime home explicitly, so repo-side release validation checks the real live environment instead of drifting into the wrong `NEXO_HOME`.
- Historical Codex conditioned-file drift no longer poisons release status forever once it has aged out and no open protocol debt remains.

### Measurement + Product Surface
- Added a minimal public product surface for the new runtime: 5-minute quickstart, Python SDK, minimal API, reference verticals, protocol/dashboard explainability, session portability docs, and a measured compare scorecard in `compare/`.
- Public scorecard artifacts now include external LoCoMo baselines, NEXO ablation summaries, runtime telemetry coverage, and `cost_per_solved_task` when the collected telemetry is representative.
- Runtime doctor, client parity, and release-readiness checks now all defend the same public story instead of measuring different realities.

### Skills + Public Contribution
- Completed the skill lifecycle as a managed runtime surface with testing, promotion, retirement, and composition flows.
- Evolution public-core mode no longer idles when a machine already has its own Draft PR open: it can now peer-review other opt-in public-core PRs safely, leaving comments/approvals only and never merging.

## [2.7.0] - 2026-04-06

### Engineering Loop + Trust
- Weekly and monthly Deep Sleep summaries now grow from passive horizon artifacts into operational engineering reports: they include protocol compliance, loop output metrics, project pulse, and trend-vs-previous-period data instead of only top patterns and project weights.
- Deep Sleep summary markdown now renders those sections explicitly, so operators can review protocol drift, engineering followup output, and pressure by project without re-reading raw nightly JSON.
- The dashboard now exposes `/api/project-pulse` and `/api/engineering-loop`, plus new narrative cards for `What Matters Now`, `What Is Drifting`, and `What Is Improving`, driven directly from the latest periodic Deep Sleep summaries.

### Runtime Doctor + Release Discipline
- Runtime doctor now checks the latest weekly Deep Sleep `protocol_summary`, surfacing degraded or critical heartbeat / `guard_check` / `change_log` compliance instead of leaving protocol drift implicit.
- Runtime doctor now also audits release artifact sync: `package.json`, top `CHANGELOG.md` heading, and release-facing integration artifacts must stay aligned.
- Added `scripts/verify_release_readiness.py`, a public repo-side validator that enforces changelog/version alignment, synced release artifacts, client parity checks, website drift checks, and runtime doctor on local release runs.
- Tagged publish workflow now runs that release-readiness validator, so release discipline is enforced inside the repo instead of depending only on personal operator scripts or memory.

### Pending Fixes Included In This Release
- Included the unreleased Codex launcher fixes: better `nexo chat` client selection, corrected Codex launch mode handling, tracked last terminal choice, and aligned interactive launcher flags.
- This closes the gap between the engineering-loop release work and the pending terminal-client fixes that were already sitting after `v2.6.21`.

## [2.6.21] - 2026-04-05

### Deep Sleep: From Analyst to Engineer
- Deep Sleep now semantically deduplicates new followups against existing open followups before creating more nightly work, and it upgrades a matched followup in place when the overnight proposal is more concrete than the older wording.
- Deep Sleep now consolidates new learnings against existing learnings instead of blindly accumulating noise. Duplicate learnings are reaffirmed, reinforcing learnings strengthen the existing record, and contradictory learnings now create an explicit review followup instead of silently piling up conflicting advice.
- Overnight synthesis now asks for explicit concrete fix artifacts on recurring medium/high-severity patterns, and the synthesis layer backfills engineering followups automatically when a pattern exposes a fix but no actionable followup was emitted.
- This shifts Deep Sleep from passive diagnosis toward concrete engineering work: fewer duplicate followups, cleaner learning signal, and more recurring problems turned into scripts, hooks, checklists, and guardrails.

## [2.6.20] - 2026-04-05

### Claude Profile Defaults
- Claude Code now defaults explicitly to `claude-opus-4-6[1m]` instead of the looser `opus` alias, so fresh installs and normalized runtime schedules point at Opus 4.6 with 1M context deterministically.
- The installer now recommends `Opus 4.6 with 1M context` directly in the model picker instead of the older `Opus latest` wording.
- Claude interactive launches and Claude automation runs now both resolve legacy task hints like `model="opus"` and `model="sonnet"` through the configured Claude runtime profile, so the selected Claude model actually applies end-to-end instead of only affecting one surface.
- `nexo update` now carries that change safely into existing installs without tripping over a missing support module during runtime sync.

## [2.6.18] - 2026-04-05

### Codex & Client Parity Hardening
- Codex client sync now persists a managed `mcp_servers.nexo` entry inside `~/.codex/config.toml`, so the shared brain survives drift in ad-hoc Codex MCP state instead of depending only on a one-time `codex mcp add`.
- If the Codex CLI MCP command fails but managed startup/config sync is still possible, NEXO now falls back cleanly to a managed-config path instead of leaving the install in a half-synced state.
- Runtime doctor now audits recent Codex sessions for actual startup discipline (`nexo_startup`, heartbeat usage, bootstrap markers) and reports Claude Desktop shared-brain metadata explicitly instead of treating both as invisible best-effort wiring.
- Added regression auditing against new Claude-only assumptions so future runtime changes cannot quietly drift back toward `.claude/projects`-only or Claude-specific session conventions.

### Deep Sleep Horizon & Reliability
- Deep Sleep long-horizon collection now carries weighted project-priority signals built from diaries, learnings, followups, and decision outcomes so overnight synthesis can rank what matters by leverage, not just recency.
- Deep Sleep now writes reusable weekly and monthly summary artifacts alongside the daily morning briefing, giving the overnight system higher-horizon memory instead of rediscovering the same patterns from scratch every day.
- Deep Sleep synthesis now accepts the nested output path produced by the current headless model flow, preventing false failed runs when the JSON payload was already written successfully.

### Retrieval Precision & Explainability
- Cognitive retrieval explanations now surface result confidence and the automatic retrieval strategy that fired (`semantic`, `associative`, or both) so the system is more honest about why a memory surfaced.
- Associative expansion now trims low-signal neighbors more aggressively and re-slices back to `top_k`, which keeps exact lookups cleaner while preserving the benefits of shallow spreading on concept-heavy queries.

## [2.6.17] - 2026-04-05

### Bootstrap Sync Hotfix
- Existing installs that already had NEXO wired into Codex now backfill `interactive_clients.codex` conservatively when managed Codex artifacts are present, so `nexo update` and `nexo clients sync` no longer skip the new global Codex bootstrap sync by mistake.
- Managed bootstrap rendering now falls back cleanly to `NEXO` when `operator_name` is blank or missing, instead of generating broken headings like `#  — Cognitive Co-Operator`.
- Runtime update flows now persist the normalized client preference backfill before client sync, so the repaired Codex state survives future updates instead of only existing in memory for one command.

## [2.6.16] - 2026-04-05

### Codex Runtime Parity
- Codex client sync now manages `~/.codex/config.toml` as part of the shared-brain contract, including bootstrap injection via `initial_messages` plus the configured Codex model/reasoning profile.
- `nexo_startup` and session registration now support generic external session tokens and client identifiers instead of assuming every interactive session is Claude-shaped.
- Dashboard followup execution now launches the configured NEXO terminal client instead of hardcoding Claude Code.

### Retrieval & Memory Personalization
- Cognitive retrieval now defaults to an automatic mode for HyDE query expansion and shallow spreading activation: conceptual queries get richer recall, while exact lookups stay conservative.
- STM/LTM rows now track per-memory `stability` and `difficulty`, and rehearsal updates those profiles over time instead of relying only on global decay constants.
- Cognitive stats and retrieval explanations now surface the new personalization/auto-mode behavior.

### Long-Horizon Deep Sleep
- Deep Sleep collection now builds a 60-day blended context (70% recent, 30% older) across diaries, learnings, stale followups, and transcript metadata.
- Overnight synthesis prompts now explicitly ask for multi-week recurring themes, older/current cross-domain links, and topics repeatedly mentioned but never formalized.

### Guardrails & Audits
- Added regression audit coverage for shared-runner usage, transcript-source parity, Codex bootstrap guidance, and client-aware dashboard followups so future changes do not silently drift back into Claude-only assumptions.

## [2.6.15] - 2026-04-05

### Bootstrap Runtime Hotfix
- Fixed installed-runtime bootstrap template resolution so `bootstrap_docs.py` now finds `templates/` correctly in both source-tree and packaged/runtime layouts.
- This restores real migration/sync of `~/.claude/CLAUDE.md` and `~/.codex/AGENTS.md` in existing installations instead of failing silently after the parity release.
- Added regression coverage for runtime-layout template resolution and isolated startup-preflight tests so local test runs no longer contaminate real user bootstrap files.

## [2.6.14] - 2026-04-05

### Bootstrap Parity
- Claude Code and Codex now have managed bootstrap documents with an explicit `CORE` / `USER` contract: NEXO updates can refresh product rules in `CORE` without touching operator-specific instructions in `USER`.
- Added a new managed Codex bootstrap at `~/.codex/AGENTS.md`, while keeping `~/.claude/CLAUDE.md` on the same migration contract.
- `nexo chat` and Codex headless automation now inject the current Codex bootstrap explicitly, so Codex starts as NEXO even when plain global Codex startup does not honor global instructions consistently.
- Startup preflight, `nexo update`, and `nexo clients sync` now keep Claude/Codex bootstrap files aligned automatically.

### Deep Sleep Parity
- Deep Sleep transcript collection now reads both Claude Code raw transcripts and Codex durable session files, merging them into one overnight analysis input set with per-session client/source metadata.
- Added session manifest and stable per-session file mapping so Deep Sleep extraction can resume safely across mixed Claude/Codex transcript sources.
- Runtime doctor now checks transcript-source parity and managed client bootstrap parity, instead of assuming a Claude-only world.

### Runtime Hardening
- Runtime sync/install/update now ship the new bootstrap management module, so existing installations can migrate without missing-file breakage.
- Evolution, self-audit, and watchdog safety prompts now protect `AGENTS.md` alongside `CLAUDE.md`.
- Session startup guidance now surfaces Evolution state directly in the session briefing and treats external session tokens as multi-client, not Claude-only.

## [2.6.13] - 2026-04-04

### Personal KeepAlive Schedules
- Personal scripts can now declare official daemon-style schedules via `schedule_required=true` + `recovery_policy=restart_daemon`, which reconciles to a managed `KeepAlive` service instead of an unmanaged manual LaunchAgent.
- `nexo doctor --tier runtime --fix`, `nexo update`, and personal script reconciliation now adopt and repair matching legacy `KeepAlive` daemons instead of leaving them as orphan criticals.
- Added a targeted legacy backfill for the historical `nexo-wake-recovery.sh` helper so existing installs migrate to the managed schedule model automatically.

## [2.6.12] - 2026-04-04

### Clients & Installer
- Installer now persists explicit client preferences in `schedule.json`: connected interactive clients, default terminal client, background automation enabled/disabled, and selected automation backend.
- Installer also persists per-client runtime profiles, so the chosen terminal/backend carries an explicit model + reasoning configuration instead of relying on provider defaults.
- `nexo chat` no longer assumes Claude Code. It now opens the configured default terminal client and supports `nexo chat --client claude_code|codex`.
- Install now detects Claude Code, Codex, and Claude Desktop up front and can offer installation of the required terminal client/backend when missing.
- Shell alias generation now targets `nexo chat .` instead of hardcoding a Claude Code launch command.

### Automation Backend
- Added a shared `agent_runner` abstraction so core background jobs can run through the configured automation backend instead of calling Claude Code directly.
- Core agentic jobs now route through that runner, preserving the existing hardened Claude Code flags when `claude_code` remains the backend.
- Legacy task hints such as `opus` / `sonnet` now resolve against the selected backend profile instead of silently falling back to an implicit provider default.
- Added a small `nexo-agent-run.py` wrapper so shell-based flows such as watchdog repair can use the configured backend while keeping the old Claude fallback for partial/older runtimes.
- Added persisted client/backend defaults to runtime schedule handling and cron/doctor logic so older installs keep automation enabled unless the user explicitly turns it off.

### Personal Script Templates
- Python personal-script and plugin templates now ship a `run_automation_text(...)` helper, so newly generated private scripts use the configured NEXO backend/model instead of hardcoding `claude -p` or provider-specific model names.

### Runtime Health
- Doctor runtime checks now surface mismatches between the configured default terminal client / automation backend and what is actually installed on the machine.
- Auto-update/runtime sync now backfill the new client/backend support modules so existing installs can migrate without manual repair.

## [2.6.11] - 2026-04-04

### Reliability
- Declared personal schedules now self-heal during startup preflight, `nexo update`, and catch-up recovery, so personal services like the email monitor do not silently stop after schedule drift.
- `nexo update` now surfaces when it repaired personal schedules, making runtime recovery visible instead of silent.
- Installed runtimes can now recover safely even when newly introduced support modules such as `public_contribution.py` were missing from older synced installs.
- Runtime sync/install file coverage was tightened again so shared-brain support files and contributor/runtime helpers arrive consistently in existing `NEXO_HOME` installs.

## [2.6.10] - 2026-04-04

### Shared Brain
- Added shared client config sync so Claude Code, Claude Desktop, and Codex can point at the same local `nexo` MCP runtime and `NEXO_HOME`.
- Added `nexo clients sync` plus install/update hooks to re-apply that wiring automatically after runtime changes.

## [2.6.9] - 2026-04-04

### Release Workflow Fix
- Fixed the integration release workflow YAML so the automated post-publish ClawHub verification runs correctly during tagged releases.

## [2.6.8] - 2026-04-04

### Integration Release Integrity
- Release automation now synchronizes the public integration artifacts before publishing, so the Claude Code plugin packaging, ClawHub skill metadata, and OpenClaw plugin package stay aligned with the tagged version.
- Added hard validation for Claude Code packaging, ClawHub skill metadata, and OpenClaw plugin contract/packaging both in CI and in the release workflow.
- Release automation now smoke-verifies the published ClawHub listing after publish instead of assuming it updated correctly.

### OpenClaw & ClawHub
- The native OpenClaw plugin now targets the packaged `~/.nexo/server.py` entrypoint instead of the obsolete `~/.nexo/src/server.py` path.
- The OpenClaw bridge now reports a client version synchronized with the published release version.
- The repository ClawHub skill definition is now version-aligned and ready to be republished automatically during release.

## [2.6.7] - 2026-04-04

### Public Contributor Evolution
- Added an opt-in public contribution mode for install/update on GitHub-authenticated machines.
- Public contribution runs now work from an isolated checkout, use a dedicated `public_core` evolution policy, and can open a single Draft PR against the public repository before pausing that machine.
- Added contributor lifecycle controls via `nexo contributor status|on|off`, persisted contributor state in `schedule.json`, and guardrails that prevent personal runtime data, local prompts, secrets, or personal scripts from reaching public proposals.

### Personal MCP Scaffolding
- Added `nexo_personal_plugin_create`, a core tool that scaffolds persistent personal MCP plugins in `NEXO_HOME/plugins` with an optional companion script in `NEXO_HOME/scripts`.
- Added a reusable personal plugin template so user-specific capabilities can survive updates without being promoted to core.

### Operator Experience & Memory Continuity
- Install/update now emit clearer progress messages while copying files, pulling changes, running migrations, reconciling schedules, and verifying the runtime.
- `nexo_session_diary_read(last_day=true)` now returns a recent continuity window (~36h) instead of truncating to the latest calendar day.
- Auto-close diary promotion now uses checkpoint/tool-log context so reconstructed diaries keep more of the session goal, next step, and reasoning thread.

## [2.6.6] - 2026-04-04

### macOS Permissions
- Install and interactive `nexo update` now detect when macOS Full Disk Access is actually relevant instead of treating it as a blanket requirement.
- When relevant, NEXO opens the correct System Settings pane, explains exactly what to add, and persists a best-effort state (`unset`, `granted`, `declined`, `later`) in `schedule.json`.
- Startup preflight and runtime update now carry the persisted Full Disk Access state alongside the power policy without blocking background entrypoints.
- Doctor output now makes the TCC/FDA boundary explicit: NEXO can guide and verify best effort, but macOS permission approval remains manual.

## [2.6.5] - 2026-04-04

### Reliability
- Power helper semantics are now explicit and safer: `always_on` means "enable the platform power helper for best-effort background availability", not guaranteed closed-lid operation on every laptop.
- macOS installs now use the native `caffeinate` helper more robustly, while preserving wake recovery and catch-up as part of the contract.
- Catch-up recovery now suppresses duplicate relaunches for cron windows that already have an in-flight `cron_runs` entry.
- Runtime update/startup post-sync now reconciles declared personal schedules directly, so normal users do not need to run `nexo scripts reconcile` after `nexo update`.

### Product Surface
- README, website copy, FAQ, `llms.txt`, and public changelog were aligned with the current product: Claude Code-first runtime, 150+ MCP tools, 13 core recovery-aware jobs, optional 23-module dashboard, and the current install/update flow.

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
