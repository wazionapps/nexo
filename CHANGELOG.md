# Changelog

## [5.10.1] - 2026-04-17

### Fix: silent migration of legacy `reasoning_effort=max` to the resonance map

v5.9.0 introduced `preferences.default_resonance` (`maximo` / `alto` / `medio` / `bajo`) and v5.10.0 made that map prevail over the legacy `client_runtime_profiles.claude_code.reasoning_effort` hint. That removed a subtle double-writer bug, but it also meant any operator whose only recorded preference was the legacy `reasoning_effort="max"` silently fell back to `DEFAULT_RESONANCE="alto"` — effectively a one-tier downgrade on the first interactive call after the v5.10.0 update. The NEXO Desktop header on a fresh conversation would show `POTENCIA: ALTO` even when the operator had configured `max` long before.

v5.10.1 adds a one-shot, conservative, non-destructive migration inside `_run_runtime_post_sync()`:

- `_migrate_effort_to_resonance(dest)` runs exactly once per `nexo update`.
- It reads `<NEXO_HOME>/config/schedule.json → client_runtime_profiles.claude_code.reasoning_effort` and, if `<NEXO_HOME>/brain/calibration.json → preferences.default_resonance` is not already set, writes the equivalent tier:
  - `max → maximo`
  - `xhigh → alto`
  - `high → medio`
  - `medium → bajo`
- If either calibration or schedule already declares an explicit `default_resonance`, the migration is a no-op. Unknown/unsupported effort values are skipped. Corrupt `calibration.json` is rewritten safely.
- All errors are swallowed into an `actions.append("resonance-migration-warning:...")` line; the update path never raises.
- Idempotent by construction: a second run detects the already-present preference and does nothing.

**Tests**

`tests/test_auto_update_migrate_effort.py` — 10 cases covering each mapping, the two no-op paths (calibration pref set / schedule pref set), the "no hint" and "unknown hint" paths, idempotency on a second run, and the corrupt-JSON recovery.

**Test harness fix (unblocks CI)**

`tests/test_cron_recovery.py::test_catchup_script_runs_directly_from_runtime_root` (and two sibling tests) was copying a minimal `src/` subset into the simulated runtime root but missed modules introduced by v5.9.x / v5.10.0 (`model_defaults.py`, `model_defaults.json`, `resonance_map.py`, `db.py`, `enforcement_engine.py`, `bootstrap_docs.py`, `constants.py`). Consolidated the copy step into `_prime_catchup_runtime_root(repo_src, runtime_root)` and added the missing files. Pre-existing failure against `main`; now passes without touching production code.

Full suite: 1011 passed, 1 skipped.

## [5.10.0] - 2026-04-17

### Feature: extract-path bloat fix + `caller=` enforced + personal scripts on the map

v5.9.x introduced the resonance map and the `nexo preferences --resonance` selector but left three pieces of deuda tempered: deep-sleep extract still took ~57 minutes because each Claude CLI child reloaded an 11 KB `CLAUDE.md` bootstrap, callers without a `caller=` kept going via the legacy task-profile path, and the operators' personal scripts (email-monitor, followup-runner, github-monitor, post-x, orchestrator-v2) were not in the resonance map at all. v5.10.0 closes all three.

**Extract bootstrap bloat fix — `bare_mode` in run_automation_prompt**

Claude CLI 2.1.x auto-loads `~/.claude/CLAUDE.md`, runs hook/plugin/LSP sync, and probes the macOS keychain on every invocation. For a JSON-only extractor that only needs `Read,Grep,Bash`, that adds ~7 seconds per call — the reason Session 1 of deep-sleep took nearly an hour on some installs. The new `--bare` flag on Claude CLI opts out of all of it, but it also disables keychain auth, so callers must have an `ANTHROPIC_API_KEY` available.

v5.10.0 wires this up cleanly:

- New `bare_mode` kwarg on `run_automation_prompt` (default `None` = auto).
- New `BARE_MODE_SAFE_CALLERS` frozenset — `deep-sleep/extract` and `deep-sleep/synthesize` for now. Any caller here auto-enables `--bare` when an API key is resolvable.
- New `_resolve_anthropic_api_key()` helper looks at `ANTHROPIC_API_KEY` env, then `~/.claude/anthropic-api-key.txt`, then `~/.nexo/config/anthropic-api-key.txt`. Returns empty string instead of raising when nothing is available.
- If bare mode is requested but no key is available, the call falls back silently to the normal path. No hard failure.
- The `ANTHROPIC_API_KEY` gets injected into the child env only for the bare call; it does not leak into other subprocesses.

Measured impact on the reference install: a single `claude -p` call dropped from ~9.5s to ~2.2s — roughly 4.3× faster. Session 1 that used to take 57 min should now take under 5 min. The heavier per-session bottleneck for synthesize (which processes every session at once) is the model reasoning itself, which is why synthesize is pinned at `MAXIMO`.

**`caller=` is now mandatory**

`run_automation_prompt` raises `UnregisteredCallerError` when called without a `caller` argument or with a caller that is not registered in `src/resonance_map.py`. No silent fallback to `task_profile` or global defaults. Every automation subprocess is traceable to a named tier, by construction.

This is a breaking change for any script that was still calling without `caller=`. As part of this release the 13 callers updated in v5.9.0 were verified and the 5 personal scripts (`personal/email-monitor`, `personal/github-monitor`, `personal/post-x`, `personal/followup-runner`, `personal/orchestrator-v2`) were added to the map and patched to pass their `caller=`. Anyone else who ships an external script that calls into `run_automation_prompt` needs to register a new entry.

**Personal scripts on the map (operators' own LaunchAgents)**

Five new entries under `SYSTEM_OWNED_CALLERS`:

- `personal/email-monitor` → `alto` (answering real user emails, quality matters)
- `personal/github-monitor` → `alto` (reasoning about issues/PRs, not mechanical)
- `personal/post-x` → `alto` (public-facing copy)
- `personal/followup-runner` → `alto` (executes due followups, output is user-visible)
- `personal/orchestrator-v2` → `maximo` (autonomous orchestration, critical reasoning)

All five use `mcp__nexo__*` tools, so none of them are bare-mode-safe. The `mcp__*` allow-list wiring in those scripts is otherwise unchanged.

**Resonance map tier bumps based on a per-caller read**

A pass through the map re-evaluated the gbp/* callers ("short marketing copy — could be `medio`") against the quality-first rule: output is public, a mediocre post embarrasses the brand. All five gbp callers are now `alto`. No other tiers moved in this release — deep-sleep, evolution, reflection, catchup, followup_runner, synthesis, self_audit, postmortem and the validator/checker callers stayed where they were, verified to match what each script actually does.

**Migration-friendly resonance_map lookup**

`_load_user_default_resonance()` (added in v5.9.1) still reads `brain/calibration.json` first and falls back to `config/schedule.json`. `resolve_tier_for_caller` now consults it automatically when `user_default` is not passed — which means interactive entry points like `nexo chat` and `launch_interactive_client` pick up whatever the Desktop preferences dialog wrote, without any CLI argument.

**Protocol debt bulk-resolve**

65 legacy protocol debts accumulated across 2026-04-13 → 2026-04-17 (missing_cortex_evaluation, unacknowledged_guard_blocking, release_channel_alignment_incomplete, codex_session_missing_startup, claimed_done_without_evidence) were resolved with a shared reference to the v5.10.0 audit. The patterns that generated them are now structurally closed by mandatory `caller=` + unified session log + bare_mode — keeping historical records open no longer drives behaviour.

**Tests**

10 new cases in `tests/test_agent_runner_bare_mode.py`:

- `BARE_MODE_SAFE_CALLERS` includes deep-sleep/extract + synthesize and excludes MCP-using callers (catchup, evolution, daily_self_audit).
- `_resolve_anthropic_api_key` picks env > `~/.claude/anthropic-api-key.txt` > `~/.nexo/config/anthropic-api-key.txt`, returns empty on no match.
- Explicit `bare_mode=True` plus a key adds `--bare` to the cmd, drops `--dangerously-skip-permissions`, and injects `ANTHROPIC_API_KEY` in the child env.
- `bare_mode=None` auto-enables for safe callers, stays off for unsafe ones.
- Missing key → silent fallback to the normal path (no raise).
- `bare_mode=False` overrides the safe-caller default.

Plus the `caller=` enforcement: `test_agent_runner.py` now declares `caller="test/harness"` (registered at MAXIMO for the duration of each test via an autouse fixture) and existing assertions were updated to reflect that the resonance map drives (model, effort) rather than `client_runtime_profiles` (unless the caller passes legacy model hints like `opus`, which still trigger the profile swap).

Full suite: 964 passed, 1 skipped (bandit not installed in the tmp env used by CI).

## [5.9.1] - 2026-04-17

### Feature: `default_resonance` reachable from NEXO Desktop's Preferences UI

v5.9.0 shipped `nexo preferences --resonance` as a CLI-only way to change the default tier for interactive sessions. The Desktop Preferences dialog had no matching control, so Desktop users either had to drop to a terminal or leave the default at `alto`. v5.9.1 closes that gap without requiring a Desktop release: NEXO Desktop already fetches its editable fields via `nexo schema --json`, so adding the field at the Brain end makes the selector appear the next time the user opens Preferences.

Changes:

- **`src/desktop_bridge.py`**: new field `preferences.default_resonance` (stored in `brain/calibration.json`) in the `preferences` group. Four labelled options (`Máximo` / `Alto (recomendado)` / `Medio` / `Bajo` in Spanish, `Maximum` / `High (recommended)` / `Medium` / `Low` in English) with an inline hint explaining that the preference only affects interactive sessions — crons and background processes (deep-sleep, evolution, …) stay pinned per caller in `resonance_map.py`. Desktop renders this automatically via its existing `buildFieldsFromBrainSchema()` path.
- **`src/resonance_map.py`**: new `_load_user_default_resonance()` helper. Reads `brain/calibration.json` first (`preferences.default_resonance`, where Desktop's UI writes) and falls back to `config/schedule.json` (where the v5.9.0 CLI wrote). `resolve_tier_for_caller` now consults that helper when the caller does not pass `user_default` explicitly — so `nexo chat` and `launch_interactive_client` pick up the Desktop-edited value without needing any extra wiring.
- **`src/cli.py`**: `nexo preferences --resonance` now writes to BOTH `calibration.json` (new canonical location matching the UI) and `schedule.json` (legacy location, kept so clients that read schedule.json keep working). `--show` reports which source provided the current value.
- **`tests/test_resonance_map.py`**: six new cases (20 total) covering calibration-first resolution, schedule.json fallback, invalid-tier rejection, empty-home fallback, `resolve_tier_for_caller` auto-discovery, and explicit `user_default` override winning over the file.

Out of scope (still deferred from 5.9.0):

- Requiring `caller=` at signature level.
- Onboarding simplification (the first-run flow still asks for `model`; adding the resonance knob as a pre-onboarding step is a bigger UX change).
- NEXO Desktop release that embeds MCP `nexo_session_log_create` / `nexo_session_log_close` calls around its direct `claude` spawns. The Brain side is ready; Desktop still needs to call them.
- Extract.py system-prompt bloat (the reason Session 1 of deep-sleep takes minutes on some installs). Separate investigation.

## [5.9.0] - 2026-04-17

### Feature: centralised resonance map + unified automation session log

Before v5.9.0 every script that called Claude or Codex picked its own model + reasoning effort — either explicitly or by falling back to the global defaults in `model_defaults.json`. That meant `nexo chat` (interactive, should burn reasoning on user requests) and a 4am daily GBP post (short marketing copy, should be cheap) ended up at the same tier, and changing a default shifted both at once. Interactive sessions (`nexo chat`, Desktop new conversation) also bypassed `automation_runs` entirely, so the Brain had no record of when the user actually talked to Claude.

**Resonance map (`src/resonance_map.py`)**

Four tiers (`MAXIMO` / `ALTO` / `MEDIO` / `BAJO`) mapped per backend to a concrete `(model, reasoning_effort)` pair. Every caller is registered in one of two dicts:

- `USER_FACING_CALLERS` = three entry points (`nexo_chat`, `desktop_new_session`, `nexo_update_interactive`) that honour the user's `default_resonance` preference.
- `SYSTEM_OWNED_CALLERS` = every cron / script / background task, locked at a fixed tier we pick per caller based on what the job actually needs. `deep-sleep/synthesize` runs `MAXIMO`; `deep-sleep/extract` runs `ALTO`; `evolution/run` and `reflection` run `MAXIMO`; short marketing copy (`gbp/*`) runs `MEDIO`. The user's preference never downgrades a system-owned job.

Unknown callers raise `UnregisteredCallerError` — no silent default. Every automation call is auditable back to a named, tiered caller.

**New `caller=` argument on `run_automation_prompt`**

`run_automation_prompt` accepts a `caller` kwarg and, when present, resolves `(model, effort)` via the resonance map instead of the global task profile. Explicit `model` / `reasoning_effort` still win for edge cases (e.g. the JSON-conversion follow-up inside `deep-sleep/extract.py` that calls with a shorter budget). 13 callers updated in this release: `deep-sleep/extract`, `deep-sleep/synthesize`, `catchup/morning`, `evolution/run`, `sleep/nightly`, `immune/scan`, `daily_self_audit`, `postmortem_consolidator`, `synthesis/daily`, `check_context`, `learning_validator`, `tools/drive_search`, `agent_run/generic`.

**`run_automation_interactive()` for chat + Desktop**

New sibling of `run_automation_prompt` that spawns an interactive Claude/Codex session with stdin/stdout inherited from the user terminal. Records a row in `automation_runs` at spawn (`ended_at IS NULL`) and updates it on exit. `launch_interactive_client` (used by `nexo chat`) now routes through this path, so every interactive session is in the unified log.

**MCP tools for Desktop (`tools_automation_sessions.py`)**

`nexo_session_log_create` and `nexo_session_log_close` expose the same start/end API over MCP so NEXO Desktop — which spawns `claude` from its TypeScript process, not via `agent_runner` — can participate in the unified log. Call create before spawning, store the returned `session_id`, call close when the conversation ends. Every Claude/Codex invocation on the machine now flows through one table.

**Migration #41 on `automation_runs`**

Adds six columns + three indexes: `caller`, `session_type` (`headless` / `interactive_chat` / `interactive_desktop`), `started_at`, `ended_at` (NULL = currently running), `pid`, `resonance_tier`. Idempotent — `_migrate_add_column` is a no-op on existing columns; pre-v5.9.0 rows just get empty values.

**`_record_automation_start` / `_record_automation_end` split**

The monolithic `_record_automation_run` is now a compatibility facade over two new helpers. Start inserts a row with `ended_at IS NULL` and returns a `row_id`; end UPDATEs by `row_id` (or falls back to a single-shot INSERT if start failed). This is what lets long-running jobs and interactive sessions show up in the log while they are still in flight.

**`nexo preferences --resonance` CLI**

New subcommand:

```
nexo preferences --resonance maximo|alto|medio|bajo   # set default
nexo preferences --show                               # read current
```

Writes `default_resonance` into `schedule.json`. The three user-facing callers read it at runtime through the resonance map.

**Tests**

20 new cases across `test_resonance_map.py` (14) and `test_automation_sessions_log.py` (6): tier/backend coverage, user-facing vs system-owned resolution, unknown caller rejection, empty-caller rejection, fallback when backend missing, register/unregister roundtrip, migration #41 schema, start/end persistence, end-without-start fallback, create/close MCP roundtrip. Full agent_runner + task classification suites stay green.

Out of scope for v5.9.0 (intentionally deferred to 5.9.1+):

- Requiring `caller=` at the signature level (enforces only at resolution time for now).
- Onboarding simplification (the interactive setup flow still asks for model; the new preferences knob is additive).
- Desktop UI for `default_resonance` (the CLI is enough until Desktop ships its next release).
- Personal scripts (email-monitor, etc.): will be revisited with Maria as a separate pass.

## [5.8.2] - 2026-04-17

### Fix: neutralize Brain core — remove Spanish-first NEXO-specific classification heuristic

v5.8.0 added `internal` and `owner` columns on `followups` and `reminders` with a regex-based auto-classifier (`classify_task`, `is_internal_id`, `classify_owner`) that fired whenever an agent left those fields blank. The heuristic was NEXO-specific in three ways: it matched `NF-PROTOCOL-*` / `NF-DS-*` / `NF-AUDIT-*` ID prefixes, it parsed Spanish user-verbs (`debes`, `revisar`, `firmar`, `llamar`), and it treated recurrence + agent keywords (`monitor`, `auditoría diaria`, `checkpoint`) as agent-owned. That was a reasonable bootstrap for NEXO's own DB but bled conventions into any third-party agent plugged into the shared Brain — deployments that did not follow NEXO's Spanish naming would see their user-facing tasks misclassified without ever touching the `internal`/`owner` API.

v5.8.2 removes the heuristic entirely. The Brain core no longer classifies tasks on behalf of agents: when `internal` is omitted it persists as `0`, and when `owner` is omitted it persists as `NULL`. Clients that want automatic classification compute it themselves (NEXO Desktop already does, via its `_legacyClassifyOwner` / `_legacyIsInternalTaskId` helpers) and pass the result to `nexo_followup_create` / `nexo_reminder_create` / their `_update` counterparts.

Changes:

- **`src/db/_classification.py`**: deleted `_INTERNAL_ID_PATTERNS`, `_USER_VERB_RX`, `_WAITING_RX`, `_AGENT_RX`, `is_internal_id()`, `classify_owner()`, `classify_task()`. Kept `VALID_OWNERS`, `normalise_owner()`, `normalise_internal()` — the pure normalisation helpers the DB layer uses to clamp agent input.
- **`src/db/_reminders.py`**: `create_reminder` / `create_followup` no longer call `classify_task`. When the caller omits `internal`, the stored value is `0`; when it omits `owner`, the stored value is `NULL`. `update_*` paths were already heuristic-free and stay unchanged.
- **`src/db/_schema.py`**: `_m40_classification_columns` no longer runs the one-shot backfill loop. The migration keeps the four `_migrate_add_column` calls and the four `_migrate_add_index` calls, and becomes a trivial idempotent schema change. Rows that were already backfilled by the v5.8.0 migration keep their values — `_migrate_add_column` is a no-op when the column exists and never touches row data.
- **`tests/test_task_classification.py`**: rewritten around the neutral contract. 12 cases cover column existence, the generic `VALID_OWNERS` set (explicitly asserting `"nexo"` is not a valid owner), `normalise_*` variant handling, explicit-override persistence, `NULL` defaults on create, invalid owner rejection on update, and migration idempotency.

Compatibility:

- Installs that ran the v5.8.0 migration keep their classified rows (Francisco's reference DB: 468 followups and 40 reminders classified, verified pre-release).
- NEXO Desktop v0.10.0 reads `owner` / `internal` from the DB when present and falls back to its client-side `_legacyClassifyOwner` / `_legacyIsInternalTaskId` on `NULL` — so rows created post-v5.8.2 without explicit classification render identically to pre-v5.8.0 rows from the user's point of view.
- Third-party agents that expected the Brain to classify for them now need to either pass `owner`/`internal` explicitly on create or implement their own client-side classifier.

## [5.8.1] - 2026-04-17

### Fix: deep-sleep Phase 2 could wedge on the first session of every batch

Between 2026-04-14 and 2026-04-17 the nightly deep-sleep on the reference
install stopped producing extractions / synthesis / applied artifacts. The
Phase 2 worker would start, get partway through Session 1 of N, and die with
`Automation backend error (exit 143)`. Every 30 minutes a new worker
started and hit the same fate, burning API credits on a never-advancing
loop.

Root cause chain:

- `scripts/nexo-cron-wrapper.sh` only wrote to `cron_runs` at the END of
  the job. Any wrapper killed mid-flight produced zero database records.
- `scripts/nexo-watchdog.sh`, running every 1800s, used `cron_runs` as its
  source of truth for "has this cron run recently?". With no row for
  deep-sleep it decided the cron was stuck and executed
  `launchctl kickstart -k "gui/<uid>/com.nexo.deep-sleep"` — the `-k`
  flag kills the running instance first, so the watchdog was actively
  killing its own worker.
- `scripts/deep-sleep/extract.py` cached the first failure (an Anthropic
  API `overloaded_error`) in a per-session checkpoint and reused it
  forever, so even when the kickstart loop was broken the same session
  would report 0 findings indefinitely.

What changed:

- **`scripts/nexo-cron-wrapper.sh`**: two-phase recording. INSERT a row
  with `ended_at=NULL` at start, UPDATE at end. Foreground command runs
  under a `wait $!` so `trap TERM/INT/HUP` fires immediately, forwards
  SIGTERM to the child, and closes the row with `exit_code=143` + an
  explicit `Killed by SIGTERM` error string. Wrappers killed by the
  watchdog, crash, or shutdown now show up as failed runs instead of
  vanishing.
- **`scripts/nexo-watchdog.sh`**: new in-flight detection. A cron_runs row
  with `started_at` set and `ended_at` empty is interpreted as "currently
  running" — never kickstart -k'd. Long-running in-flight rows (age >
  3×max_stale) only escalate if the worker process is provably dead
  (`proc_grep` check). Eliminates the kickstart loop.
- **`scripts/deep-sleep/extract.py`**: classified CLI failures into
  transient (`overloaded_error`, `rate_limit_error`, `api_error`,
  `timeout`, `signal`) vs deterministic (`json_parse`, `unknown`).
  Transient errors do not persist a poisoned checkpoint — the next run
  gets a clean retry. Deterministic errors increment `error_count` and
  are skipped once `error_count >= MAX_POISON_ATTEMPTS` (3). Shared
  context is now slimmed to 200 head lines + metadata so the Claude CLI
  subprocess does not stream 400+KB of DB dump on every per-session
  extraction.
- **`src/auto_update.py`**: new `_heal_deep_sleep_runtime()` runs on every
  post-sync. It purges poisoned checkpoints from any date directory,
  releases `sleep.lock` / `sleep-process.lock` / `synthesis.lock` older
  than 6h, closes dangling `cron_runs` rows older than 6h with an
  explicit "healed by auto_update (pre-5.8.1 wrapper left row open)"
  marker, and resets `.watchdog-fails` counters older than 24h. Existing
  installs get healed silently on their next `nexo update`.

Tests (`tests/test_cron_wrapper_contract.py`,
`tests/test_deep_sleep_extract.py`,
`tests/test_auto_update_heal_deep_sleep.py`,
`tests/test_watchdog_in_flight.py`): 22 new cases covering in-flight row
insertion, SIGTERM trap, CLI failure classification, poisoned checkpoint
skipping, transient non-poisoning, heal idempotency, and watchdog
in-flight detection.

## [5.8.0] - 2026-04-17

### Feature: first-class `internal` and `owner` columns on followups and reminders

Until now, the "who does this belong to?" classification lived client-side
in NEXO Desktop: spanish-only regex on description plus hardcoded ID-prefix
patterns (`NF-PROTOCOL-*`, `NF-DS-*`, `R-RELEASE-*`, …) tuned for NEXO's own
conventions. The result was a UX paradox — tasks marked "Para ti" could
disappear when the Desktop "Tareas internas" filter was unchecked because
the two classifications were decided independently — and a portability wall
for third-party agents plugging into the shared Brain, who would either see
everything as "Seguimiento" or, worse, have their user-facing tasks hidden.

Migration #40 makes the classification persistent and agent-owned:

- **`src/db/_schema.py`**: new migration `_m40_classification_columns` adds
  `internal INTEGER DEFAULT 0` and `owner TEXT DEFAULT NULL` to both
  `followups` and `reminders`, with indexes on each. A one-shot backfill at
  the end of the migration runs the legacy heuristic against every row where
  `owner IS NULL`, so existing installs keep their current Desktop rendering
  identically. The step is idempotent — `_migrate_add_column` is a no-op on
  the second run, and the backfill filters on `owner IS NULL` so agent-set
  values are never overwritten.
- **`src/db/_classification.py`** (new): single source of truth for the
  heuristic. Exposes `classify_task(id, description, category, recurrence)`
  returning `(internal, owner)`, plus `normalise_internal` / `normalise_owner`
  helpers that coerce agent-supplied strings and reject invalid values. The
  `owner` namespace is deliberately `'user' | 'waiting' | 'agent' | 'shared'`
  — `'agent'` is generic so non-NEXO deployments (Claude, Codex, hotel-assistant,
  etc.) do not inherit a NEXO-branded label in the stored data.
- **`src/db/_reminders.py`**: `create_reminder` / `create_followup` accept
  optional `internal=` and `owner=` kwargs. When omitted, `classify_task`
  applies the legacy rules so every pre-migration caller keeps working.
  `update_reminder` / `update_followup` extend their `allowed` field
  whitelists with the two columns and run them through the normaliser
  before persisting.
- **`src/tools_reminders_crud.py`**: `_format_reminder_payload` and
  `_format_followup_payload` surface the classification in the read output
  (`Owner:` + `Internal:` lines). `handle_reminder_create` /
  `handle_followup_create` / `handle_reminder_update` /
  `handle_followup_update` pass the overrides through.
- **`src/server.py`**: `nexo_reminder_create`, `nexo_reminder_update`,
  `nexo_followup_create`, `nexo_followup_update` gain `internal: str` and
  `owner: str` parameters, documented with the accepted values. Default is
  empty string, so agents that never touch the new knobs behave exactly as
  before.
- **`tests/test_task_classification.py`** (new, 17 cases): backfill
  coverage, heuristic fidelity against the legacy Desktop rules (user verbs,
  waiting triggers, agent-owned recurrences, NF-PROTOCOL-*/NF-DS-*/etc.
  internal IDs), override precedence, invalid-value rejection (`owner='nexo'`
  is intentionally rejected to force callers onto the generic taxonomy) and
  idempotency of the migration itself. Every existing migration + reminder
  history test continues to pass.

Consumers (NEXO Desktop, dashboard, future clients) can now trust
`followups.owner` / `followups.internal` as the persistent classification.
A follow-up Desktop release removes the mirror client-side logic and wires
UI chips/counters to the stored values.

## [5.7.0] - 2026-04-17

### Feature: `nexo update` auto-updates Claude Code + Codex CLIs

`nexo update` now keeps your terminal CLIs in lockstep with NEXO Brain itself.
When the global `@anthropic-ai/claude-code` or `@openai/codex` packages are
installed, the updater checks the npm registry for a newer version and runs
`npm install -g <pkg>@latest` in-line before the post-update verify step.

Motivation: we kept seeing installs where the `model` setting in
`~/.claude/settings.json` was already on Opus 4.7 but the terminal still
booted on Opus 4 because the locally-installed Claude Code was too old to
recognise the new model id. Bundling the CLI bump into `nexo update` closes
that gap in one command.

- **`src/auto_update.py`**: new `_update_external_clis()` detects the
  installed global version, looks up the latest from the npm registry, and
  runs `npm install -g <pkg>@latest` only when a bump is available.
  Packages that are not installed globally are skipped silently — NEXO
  does not push third-party CLIs onto operators who never opted in.
  `TimeoutExpired` and `FileNotFoundError` (no `npm` on `PATH`) are handled
  explicitly per learning #294. A companion `_format_external_clis_results()`
  emits a visible warning per bumped CLI ("`reinicia terminal para activar`"),
  a warning per failure, and a single informational line when everything
  was already on the latest version.
- **`src/plugins/update.py`**: `handle_update()` and `_handle_packaged_update()`
  gained an `include_clis: bool = True` keyword. The CLI bump runs after
  migrations and runtime-dependency sync but before the shared client-config
  sync, so the post-`nexo update` client sync benefits from the freshest CLI.
  Failures of the third-party install never trigger the NEXO rollback path —
  the git/npm package rollback only covers NEXO itself.
- **`src/cli.py`**: new `nexo update --no-clis` short-circuits the external
  CLI bump for operators who want to pin their terminal CLIs manually. The
  flag is wired through both `manual_sync_update` (dev-linked runtimes) and
  `handle_update` (packaged installs).
- **`tests/test_external_clis_update.py`**: 11 new unit tests covering
  not-installed, already-latest, successful bump, npm failure, timeout,
  missing `npm` binary, each formatter branch, and the
  `include_clis=False` short-circuit.

## [5.6.1] - 2026-04-17

### Fix: `nexo update` hardening — 0-byte DB orphans + Claude Code `settings.json` model sync

Two small-but-sharp fixes in the update path. Both follow up on v5.6.0 (the
Opus 4.6 → 4.7 default model upgrade) and unblock users who hit interrupted
installs or noticed their Claude Code boot model was not actually changing.

- **`src/auto_update.py`**: new `_purge_zero_byte_db_files()` scans
  `NEXO_HOME` and `NEXO_HOME/data/` for 0-byte `.db` files and deletes
  them before the pre-update backup runs. These are leftover shells from
  interrupted installs or aborted `sqlite3.connect` calls. They were
  breaking backup validation by masking the real DB during
  `_find_primary_db_path` selection and by being copied into the backup
  as empty DBs that later confused `_restore_dbs` on rollback. The
  helper is called at the top of `_backup_dbs()`, never touches
  `SRC_DIR` or `backups/`, and swallows errors so backup never aborts
  on orphan cleanup.
- **`src/client_sync.py` + `src/auto_update.py`**: new
  `sync_claude_code_model()` helper keeps `~/.claude/settings.json` in
  sync with the NEXO-recommended model. Claude Code reads its default
  model from that file, **not** from `client_runtime_profiles`, so a
  v5.6.0 heal was updating NEXO's internal profile while Claude Code
  kept booting on the old model. The helper is called right after
  `heal_runtime_profiles()` migrates the `claude_code` profile, and is
  deliberately conservative: if `settings.json` is missing or has no
  top-level `"model"` field it is a no-op (never seeds a field the user
  never opted in to). Supersedes learning #391.

## [5.6.0] - 2026-04-16

### Feature: Default model upgrade — Opus 4.6 → 4.7

NEXO Brain now ships with **Claude Opus 4.7** as the recommended model for
Claude Code users. The 1M context window remains active (same beta header).

- **`src/model_defaults.json`**: `claude_code.model` updated to
  `claude-opus-4-7[1m]` with `reasoning_effort: "max"`. The new `max` tier
  is the highest reasoning level available on Opus 4.7 (verified empirically
  against `/v1/messages`). `recommendation_version` bumped to 2.
- **Auto-migration on `nexo update`**: `heal_runtime_profiles()` now
  detects users whose model starts with `claude-opus-4-6` and silently
  migrates to `claude-opus-4-7` preserving any suffix (e.g. `[1m]`).
  Reasoning effort is bumped to `max` if the user had an empty value,
  `xhigh`, or the legacy `enabled` format.
- **Codex untouched**: GPT-5.4 / xhigh remains the Codex default. No
  migration applies to Codex profiles.
- **Interactive prompt preserved**: users on an older NEXO default who have
  not yet run `nexo update` will also be offered the upgrade interactively
  via `detect_outdated_recommendations`.

### Breaking API change in Opus 4.7 (informational)

Opus 4.7 changed the thinking/reasoning API from `thinking.enabled` +
`thinking.budget_tokens` to `thinking.type: "adaptive"` + `output_config.effort`.
NEXO Brain does not call the Anthropic API directly (Claude Code handles it),
but this is documented here for operators building custom integrations.
Valid effort values: `low`, `medium`, `high`, `xhigh`, `max`.

## [5.5.6] - 2026-04-16

### Hotfix: rate-limit the backup/restore/export tools

Same-day follow-up to v5.5.5. The v5.5.5 release neutralised the *consequences*
of the 2026-04-16 incident (validated backups, pre-flight guard, post-migration
gate, startup self-heal, `nexo recover`). v5.5.6 closes the *cause*: a runaway
MCP client (Claude Code tool-use loop, a buggy Desktop handler, etc.) can no
longer hammer `sqlite3.Connection.backup()` from the tool boundary.

- **`plugins/backup.py`**: `handle_backup_now` rate-limited to one call every
  `NEXO_BACKUP_MIN_INTERVAL_SECS` (default 30 s). `handle_backup_restore`
  rate-limited to one call every `NEXO_BACKUP_RESTORE_MIN_INTERVAL_SECS`
  (default 60 s). `handle_backup_list` is read-only and never rate-limited.
- **`user_data_portability.export_user_bundle`**: rate-limited to one call
  every `NEXO_EXPORT_MIN_INTERVAL_SECS` (default 120 s). Returns
  `{"ok": False, "rate_limited": True, "error": "..."}` so callers can react.
- Each rate-limit message is explicit about loop detection ("If you see this
  repeatedly, a client may be stuck in a tool-use loop…") so transcript
  evidence surfaces the next time a client misbehaves.
- **Test coverage**: 10 new tests in `tests/test_backup_rate_limit.py`. Full
  suite remains 880/880 green.

### Why not in v5.5.5

v5.5.5 was the minimal-surface data-loss hotfix. Rate-limits at the tool
boundary change tool semantics (well-behaved callers occasionally hit the
"try again in N s" response) and deserved a dedicated release so operators
read the limit numbers, not just the recovery flow.

## [5.5.5] - 2026-04-16

### Hotfix: Data-loss guardrails and automatic self-heal

**Incident** (2026-04-16, Europe/Madrid): one user's `~/.nexo/data/nexo.db`
was reset to a 4 KB empty-schema file between the 15:02 hourly backup
(38 MB, 643 `protocol_tasks`, 442 `followups`, 381 `learnings`) and the
first manual `nexo update` at 15:09. Three consecutive update attempts
over the next 11 minutes each captured the already-empty DB into a new
`pre-update-*` snapshot, masking the wipe and destroying the window to
inspect what had caused it externally.

Root cause for the data loss itself remained inconclusive (the update
flow did not write zeros; the existing hourly backup taken at 15:02 was
intact). v5.5.5 therefore focuses on preventing the update flow from
ever masking an external wipe again, plus delivering an unattended
recovery path so the same incident cannot silently persist on another
install.

- **New `db_guard` module** (`src/db_guard.py`): single source of truth
  for critical-table row counts, wipe detection, hourly-backup discovery,
  and validated `sqlite3.backup` copies. Zero-dependency (stdlib only)
  so it is safe to import from installer, auto-update, and CLI paths.
- **Pre-flight wipe guard** in `plugins.update.handle_update` and the
  packaged-install path: if `data/nexo.db` already looks wiped AND a
  hourly backup within 48 h still contains real data, the update now
  ABORTS with a message pointing at `nexo recover`. Overridable with
  `NEXO_SKIP_WIPE_GUARD=1` for deliberate reinstalls.
- **Validated backups**: `plugins.update._backup_databases` now rejects
  a `pre-update-*` snapshot whose critical-table row counts do not
  match the source. The v5.5.4 rollback could have restored an empty
  backup on top of a partially recovered DB; this closes that path.
- **Post-migration wipe gate**: after migrations run, the updater
  compares pre- and post-migration row counts across `CRITICAL_TABLES`
  and rolls back if two or more tables regressed by ≥80 % or the
  overall row count dropped by ≥80 %.
- **Self-heal at server startup** (`auto_update._self_heal_if_wiped`):
  when NEXO starts and detects a wiped primary DB while a recent
  hourly backup (≥50 critical rows) is available, it kills any live
  MCP servers, snapshots the current state to `backups/pre-heal-*`,
  restores the backup via `sqlite3.backup`, and validates row counts.
  Capped by a 6 h cooldown and gated by `NEXO_DISABLE_AUTO_HEAL=1`
  so it cannot loop. This is the mechanism that will automatically
  repair any user who experienced the same wipe before upgrading.
- **New `nexo recover` CLI + `nexo_recover` MCP tool**
  (`src/plugins/recover.py`): list available backups, restore from the
  newest usable one (or an explicit `--from` path), with mandatory kill-
  MCP, pre-recover snapshot, and post-restore validation. Refuses to
  overwrite a healthy DB unless `--force` is passed.
- **Installer update**: `bin/nexo-brain.js` now copies `db_guard.py`
  to `NEXO_HOME` alongside `auto_update.py`. `plugins/update.py` keeps
  a defensive fallback so an in-flight upgrade from v5.5.4 still
  completes even before `db_guard.py` lands on disk.
- **Test coverage**: 36 new tests across `test_db_guard.py`,
  `test_update_wipe_guard.py`, `test_recover.py`, and
  `test_auto_update_selfheal.py`. Full suite remains 870/870 green.

### Recovery instructions for affected users

If a user is on v5.5.4 or earlier with the symptoms from the incident
(`~/.nexo/data/nexo.db` ≈4 KB, `protocol_tasks` / `followups` /
`learnings` at 0 rows) the v5.5.5 auto-update will self-heal from the
hourly backup on the next server start — no action required. Manual
recovery is also available: `nexo recover --list` to inspect backups,
`nexo recover --dry-run` to preview, `nexo recover --yes` to apply.

## [5.5.4] - 2026-04-16

### Fix: Deep Sleep no longer blocks on unparseable sessions

- Root cause: Phase 2 (`extract.py`) retried the same session 3 times with
  identical prompt and context when Claude returned non-JSON output, so a
  single semantic parse failure could stall the entire night's run behind a
  6-hour per-attempt safety net. In the worst observed case, one session
  could block the pipeline for up to ~18h before the skip logic kicked in.
- Reduced `MAX_RETRIES` in `src/scripts/deep-sleep/extract.py` from 3 to 2:
  two attempts is enough to cover transient failures; a second failure is
  almost always deterministic (parse, schema, prompt mismatch) and further
  retries just burn time. Failed sessions are still checkpointed with
  `error: "cannot_comply"` and the run continues.
- Added a JSON escape hatch to the `JSON_SYSTEM_PROMPT`: if the model cannot
  comply with the extraction schema for any reason, it is now instructed to
  return a structured `{session_id, findings:[], error:"cannot_comply",
  reason:"..."}` object instead of plain text. Guarantees parseable output
  even on degenerate inputs and removes the most common cause of retry
  loops.

### Fix: Unified automation subprocess timeout to 3h across core

- Discovered historical inconsistency: 10 scripts (`extract.py`,
  `synthesize.py`, `nexo-evolution-run.py`, `nexo-agent-run.py`,
  `nexo-synthesis.py`, `nexo-sleep.py`, `nexo-catchup.py`, `nexo-immune.py`,
  `nexo-daily-self-audit.py`, `nexo-postmortem-consolidator.py`) all carried
  the comment `# 3h safety net` while the actual value was `21600` seconds
  (6h). The CHANGELOG entry that introduced the unification claimed 6h,
  but the per-file comments had drifted toward 3h, leaving the repo in a
  "comment says 3h, code says 6h" state that masked the real ceiling.
- Added `src/constants.py` with `AUTOMATION_SUBPROCESS_TIMEOUT = 10800`
  (3 hours) as single source of truth.
- Rewired all 10 scripts to import and use the shared constant, so future
  tuning is one-file-one-edit instead of ten.

### Verification

- Compiles: `py_compile` green across all edited files.
- Runtime parity: `~/.nexo/scripts/*.py` re-synced from `src/` and validated
  importing `constants.AUTOMATION_SUBPROCESS_TIMEOUT`.

## [5.5.3] - 2026-04-16

### Feat: NEXO Protocol Enforcer section in CLAUDE.md CORE

- Added `<!-- nexo:start:enforcer --> ## NEXO Protocol Enforcer` block in
  `templates/CLAUDE.md.template`, placed between Core Systems and Autonomy.
- Explains to the model that `<system-reminder>` messages prefixed with
  `[NEXO Protocol Enforcer]` are legitimate protocol instructions, not prompt
  injections, and should be executed silently without questioning.
- Resolves a recurring failure mode where aligned models (Opus 4.6, safety-
  tuned variants) rejected enforcer injections as suspected prompt injection,
  breaking heartbeat, diary, and on-session-end checkpoints.
- Paired with matching Desktop-side change that wraps every enforcer prompt in
  `<system-reminder>` with the `[NEXO Protocol Enforcer]` prefix.
- Bumps internal `nexo-claude-md-version` marker 2.1.4 → 2.1.5.

## [5.5.2] - 2026-04-15

### Fix: auto-repair unloaded LaunchAgents

- `ensure_personal_schedules` now verifies launchctl loaded state for schedules
  marked "already_present" and auto-reloads via `launchctl bootstrap` if missing.
- `_check_launchagents` in startup auto-repairs instead of only warning.
- Migrated all `launchctl load/unload` calls to modern `bootstrap/bootout` API.
- Added return code verification for all repair operations.

### Fix: headless automation scripts defer model resolution to the configured runtime

- Core automation scripts no longer hardcode legacy `"opus"` / `"sonnet"`
  fallback strings when `_USER_MODEL` is empty.
- Passing an empty `model` now lets `run_automation_prompt()` resolve the
  active backend profile, so Codex and Claude headless runs stay aligned with
  the configured runtime defaults.
- Added regression coverage for empty-model Codex resolution in
  `tests/test_agent_runner.py`.

## [5.5.1] - 2026-04-15

### Fix: headless enforcement import + logging

- Fixed sys.path resolution for enforcement_engine import in agent_runner.py.
  Previously failed silently when cwd != NEXO_HOME (email monitor, orchestrator).
- Added comprehensive logging to enforcement_engine.py (enforcer-headless.log).
- Added dedup logic: skips injection if tool was called < 60s ago.
- Added session summary with tool counts and injection stats.

## [5.5.0] - 2026-04-15

### Feat: real-time headless Protocol Enforcer

- New enforcement_engine.py: Python equivalent of Desktop enforcement-engine.js.
- run_automation_prompt now uses stream-json mode with Popen instead of one-shot -p.
- Real-time monitoring of tool calls in headless sessions (Deep Sleep, email, etc.).
- Injects enforcement prompts via stdin when protocol rules are violated.
- End-of-session enforcement: forces diary_write + stop before process exits.
- Falls back to simple subprocess.run if enforcement_engine is unavailable.

## [5.4.9] - 2026-04-15

### Feat: headless Protocol Enforcer in run_automation_prompt

- All headless sessions (Deep Sleep, email-monitor, followup-runner, catchup,
  synthesis, evolution, etc.) now receive enforcement rules via append_system_prompt.
- Reads tool-enforcement-map.json and injects must/should rules automatically.
- Single integration point: covers ~15 scripts without modifying any of them.

## [5.4.8] - 2026-04-15

### Feat: tool-enforcement-map v2.0 — multi-dimensional enforcement

- Complete rewrite of `tool-enforcement-map.json` with multi-dimensional
  enforcement rules based on actual source code analysis of all 247 tools.
- New enforcement levels: `must` (inject), `should` (remind), `may` (track),
  `none` (on-demand). Previous v1 only had binary enforce/null.
- Each tool now declares: dependencies (`requires`), chain triggers
  (`triggers_after`), internal sub-calls (`internally_calls`), internal
  checks (`internally_checks`), and conditional rules.
- 11 must + 10 should + 1 may + 225 none = 247 tools mapped.
- Map structure designed for dynamic consumption by Desktop and
  `run_automation_prompt()` headless enforcement.

## [5.4.7] - 2026-04-15

### Feat: tool-enforcement-map.json for Protocol Enforcer

- Canonical map of all 247 NEXO Brain MCP tools with enforcement metadata.
- 16 tools have enforcement rules (periodic, event-based, session lifecycle).
- NEXO Desktop and headless session-guard read this map to mechanically
  enforce protocol compliance without depending on model self-discipline.
- New `scripts/verify_tool_map.py` catches drift between code and map.
- Learning #335 guards future tool additions/removals.

## [5.4.6] - 2026-04-14

### Feat: runtime dependency management in nexo update + daily auto-update cron

- `nexo update` now manages external runtime dependencies declared in
  `package.json` `runtimeDependencies` array. First dependency:
  `@anthropic-ai/claude-code` — checks version, installs if missing,
  updates if outdated. Best-effort: never aborts the update on failure.
- New daily auto-update cron (`auto-update`, 02:00) runs the full
  `nexo update --json` flow automatically via LaunchAgent.
- Declarative system: adding future dependencies is a single line in
  package.json `runtimeDependencies`.

## [5.4.5] - 2026-04-14

### Fix: increase CI test timeout for nexo update

- Increased `test_update_uses_recorded_source_repo` subprocess timeout
  from 10s to 30s. GitHub Actions runners are too slow for the full
  `nexo update --json` flow within 10 seconds even with a fake venv.

## [5.4.4] - 2026-04-14

### Fix: test isolation for tree_hygiene module + venv timeout in CI

- Fixed 2 test failures from v5.4.2: `tree_hygiene.py` now copied into
  isolated runtime directories used by `TestRuntimeUpdate`.
- Fixed CI timeout in `test_update_uses_recorded_source_repo`: tests now
  pre-create a fake `.venv/bin/python3` so `_ensure_runtime_venv` skips
  the slow venv creation that exceeds the 10-second timeout on GitHub
  Actions runners.

## [5.4.3] - 2026-04-14

### Fix: test isolation for tree_hygiene module

- Fixed 2 test failures in `TestRuntimeUpdate` that broke the v5.4.2 publish
  workflow. Both `test_installed_runtime_update_repairs_missing_public_contribution_module`
  and `test_packaged_update_reads_runtime_version_from_version_json` set up
  isolated runtime directories but did not copy `tree_hygiene.py`, causing
  `ModuleNotFoundError` when `auto_update.py` and `plugins/update.py` imported it.

## [5.4.2] - 2026-04-14

### Fix: traceability truth + Sensory Register buffer close-loop

This release closes two low-level integrity gaps around the Sensory Register
without changing the product boundary or removing any Claude/Opus/Codex-assisted
path.

- `src/plugins/episodic_memory.py` now distinguishes repo-tracked changes from
  local/runtime/server-side operations when warning about missing
  `commit_ref`. The diary warning no longer inflates every operational edit into
  "repo debt", and `handle_change_log` now tells callers to use a real git hash
  only for repo files while allowing markers such as `server-direct` or
  `local-uncommitted` for local-side changes.
- `src/scripts/nexo-postmortem-consolidator.py` now treats
  `session_buffer.jsonl` as a real pending queue: it renders useful hook/tool
  activity into the Sensory Register, processes all pending entries instead of
  only "today", and prunes only the lines that were actually ingested.
- The postmortem consumer now rewrites `session_buffer.jsonl` atomically, so a
  partial write cannot leave the pending-event queue truncated.
- Public and internal docs are aligned again: the README and
  `nexo-reflection.py` no longer describe the stop hook as if it auto-triggered
  the standalone reflection engine.
- Added regression coverage for repo-vs-local `commit_ref` classification and
  for pending-buffer ingestion/pruning in the postmortem consolidator.

No feature removals. No model-path downgrade. Claude/Opus-assisted
consolidation stays intact; this patch only hardens the mechanical loop around
it.

## [5.4.1] - 2026-04-14

### Fix: PostToolUse capture-session hook was always writing "unknown"

Forensic finding: `src/hooks/capture-session.sh` has been reading
`$CLAUDE_TOOL_NAME` — an environment variable Claude Code has never
set — since the hook was introduced on 2026-04-12. Claude Code passes
the tool name in a JSON payload over stdin. Result: 100% of entries
written to `session_buffer.jsonl` in the last 48 hours have
`"tool":"unknown"`, which silently blinded the Sensory Register.

This release:

- Parses `tool_name` from stdin JSON with python3 (same pattern as
  `capture-tool-logs.sh`) and falls back to an empty string when the
  payload is malformed. An empty name now exits silently instead of
  polluting the buffer with "unknown" noise.
- Keeps `Bash`, `Write`, `Edit`, `MultiEdit`, `Task`, and MCP tools in
  the stream — these are where real state change happens. The old
  filter skipped `Bash`, which was the other half of the bug.
- Removes the contaminating duplicate `~/.nexo/hooks/capture-session 2.sh`
  that had survived the v5.3.29 hygiene gates because it lived in the
  runtime bucket, not in the repo.
- On upgrade, a one-time purge strips pre-existing `"tool":"unknown"`
  lines from `session_buffer.jsonl` (with a `.pre-v5.4.1.bak` backup).

No feature changes. No API changes. Hook hygiene only.

## [5.4.0] - 2026-04-14

### Add: calibration migration + runtime events bus + notify/health/logs

Second iteration of the NEXO Desktop integration plan. External UIs can
now react to live Brain state, not just read a static schema.

- `src/calibration_migration.py` — detects flat `calibration.json` from
  older installs and migrates to nested (user/personality/preferences/meta)
  with a pre-migrate backup. Unknown keys go to `legacy_unmapped`. Reverts
  automatically on write failure.
- `src/user_context.py` — loader accepts both flat and nested shapes so
  no upgrade race breaks existing users.
- `nexo doctor --migrate-calibration [--calibration-dry-run]` — explicit
  knob for the migration. Also runs implicitly with `nexo doctor --fix`.
- `nexo update` — migrates once per user after the code sync. Silent no-op
  if already nested.

- `src/events_bus.py` — append-only NDJSON stream at
  `~/.nexo/runtime/events.ndjson` with monotonic `id`, locked writes,
  5 MB rotation, and a stable envelope
  `{id, ts, type, priority, text, reason, source, extra}`.
  Event types: `attention_required`, `proactive_message`, `followup_alert`,
  `health_alert`, `info`. Priorities: `low|normal|high|urgent`.
- `nexo notify <type> [--text] [--reason] [--priority] [--source] [--json]`
  — one-shot emitter. Lets Brain internals (recovery, followup runner,
  health watchers) wake up a UI without polling.
- `src/health_check.py` + `nexo health --json` — snapshot of runtime,
  database integrity, crons, MCP wiring, recent errors, and events.
  Top-level `status` rolls up to `ok|degraded|error`.
- `nexo logs --tail [--lines N] [--source all|events|operations|<file>]
  [--json]` — single entry point to tail the event bus or
  `~/.nexo/operations/*.log` without opening a terminal.

No existing commands changed behavior. Pure additive surface plus a safe,
idempotent calibration migration that runs in the background of `update`.

## [5.3.30] - 2026-04-14

### Add: Desktop bridge — read-only commands for external UIs

Four new CLI commands so NEXO Desktop (and any other UI) can auto-adapt
to NEXO Brain without hardcoding field lists or identity rules:

- `nexo schema --json` — editable-field schema (groups + multilang labels + options)
  for Preferences UIs. Carries `schema_version` for forward compatibility.
- `nexo identity --json` — canonical `{name, source, writable_source}` so callers
  know where the assistant name currently comes from and where to persist changes.
- `nexo onboard --json` — stepwise onboarding wizard (prompt, type, writes, default,
  validate) so clients render a wizard instead of hardcoding questions.
- `nexo scan-profile` — idempotent profile builder. Default is preview;
  `--apply` writes `profile.json`, `--force` overrides an existing file.

No behavior changes to existing commands. Pure additive surface.

## [5.3.29] - 2026-04-14

### Fix: runtime hygiene, fail-closed startup, and honest release surfaces

- Duplicate `* 2` artifacts are now treated as contamination instead of tolerated noise: `.gitignore` no longer hides them, runtime/plugin/update loaders skip them, and preflight/release checks fail if they return.
- `src/scripts/nexo-update.sh` no longer carries a parallel shell update path; it delegates to the canonical Python update handler so packaged/runtime updates stop diverging.
- Older installed runtimes that do not yet have `tree_hygiene.py` can still import the update path long enough to finish the upgrade; duplicate filtering falls back to a safe no-op until the new module lands.
- Server startup now runs preflight synchronously, and corrupt SQLite state no longer respawns a fresh empty brain by default. Fresh-DB recovery requires explicit `NEXO_ALLOW_FRESH_DB_ON_CORRUPTION=1`.
- Cron execution logging now writes a complete row after command exit and spools JSON under `~/.nexo/operations/cron-spool` when SQLite is unavailable, so runs stop disappearing silently.
- `scripts/verify_release_readiness.py` now also checks repo-facing public surfaces (`README.md`, `llms.txt`, `index.html`, `blog/index.html`, `changelog/index.html`, `sitemap.xml`) so code, docs, and public web copy cannot drift apart quietly before tag publish.

## [5.3.28] - 2026-04-14

### Feature: guardrail requires `guard_check` per-file, not per-session

- `process_pre_tool_event` now verifies that `nexo_guard_check` was
  invoked specifically for the file being edited, not merely once
  somewhere in the session. Opens a `guard_unacknowledged` protocol
  debt otherwise. Closes the loophole where a single early guard_check
  satisfied the gate for every subsequent file in the session.

## [5.3.27] - 2026-04-14

### Feature: heartbeat exposes authoritative NOW_UTC

- `nexo_heartbeat` output now begins with a `NOW_UTC: <ISO-8601>` line so
  clients always have an authoritative wall-clock time on every user turn.
  Prevents date/day-of-week drift in long sessions (e.g. emails or diaries
  saying "ayer domingo" when yesterday was actually Monday).
- Neutral UTC, no locale/timezone baked into core — clients format per
  operator preferences in runtime personal.

## [5.3.26] - 2026-04-14

### Fix: sync model_defaults.json into NEXO_HOME

- The npm installer's runtime-file sync only copied `.py` files from
  `src/` into `~/.nexo/`, so `src/model_defaults.json` (introduced in
  v5.3.24) never reached the runtime. Python then fell back to hardcoded
  defaults inside `model_defaults.py`, meaning future
  `recommendation_version` bumps in the JSON would not propagate until
  the fallback was also edited. Installer now also copies
  `*_defaults.json` files, and `model_defaults.json` is added to the
  static file list explicitly.

## [5.3.25] - 2026-04-14

### Fix headless Claude Code automation actually running (add --dangerously-skip-permissions)

- `agent_runner.run_automation_prompt` now passes
  `--dangerously-skip-permissions` to every headless `claude -p`
  invocation. Without it, Claude Code ignored `permissions.allow` from
  `settings.json` for MCP tool calls in non-interactive mode and
  stalled waiting for approval that never arrived. This killed
  followup-runner, email-monitor, deep-sleep, and every other NEXO
  cron after v5.3.22 added the allowlist. (Codex already used the
  equivalent `--dangerously-bypass-approvals-and-sandbox` so Codex
  automation was never affected.)
- Interactive sessions (`nexo chat`) never route through this path and
  keep their normal approval prompts unchanged.
- Documentation: public blog + changelog page + site navigation updated
  to feature v5.3.24 (single-source model defaults + headless-safe
  update) as the latest release.

## [5.3.24] - 2026-04-14

### Fix false-positive recommendation prompt + heal on packaged update path

- `detect_outdated_recommendations` now classifies each client into
  `pending` (needs interactive prompt) vs `auto_ack` (silent
  acknowledge). If the user's model already matches the current
  recommendation (regardless of reasoning_effort), their preferences are
  auto-acknowledged silently without prompting. Fixes spurious
  "Model recommendation available" noise on fresh installs whose
  defaults already match the recommendation (e.g. Nora on v5.3.23).
- Customized models (not a previously recommended NEXO default) also
  auto-acknowledge silently — respects the user's choice without
  repeating the stderr hint on every `nexo update`.
- Model-profile heal is now applied on the npm packaged-install update
  path (`plugins/update.py`), not just the legacy sync flow. Fixes
  stale `schedule.json` keeping `claude-opus-*` in the Codex profile
  after v5.3.23 update, which caused `nexo chat` → Codex to pass the
  Claude model via `--model` and fail with "model not supported".

## [5.3.23] - 2026-04-14

### Fix Codex broken with Claude model default + centralize model recommendations

- `DEFAULT_CODEX_MODEL` was aliased to the Claude default, causing Codex to
  write `model = "claude-opus-4-6[1m]"` into `~/.codex/config.toml` and fail
  with "model not supported when using Codex with a ChatGPT account" on first
  run. Codex default is now `gpt-5.4` / `xhigh` (matching the onboarding
  installer).
- **Single source of truth for model defaults:** new `src/model_defaults.json`
  read by both the Python runtime (`src/model_defaults.py`) and the JS
  installer (`bin/nexo-brain.js`). Editing the JSON updates install defaults
  for new users and — when `recommendation_version` is bumped — triggers a
  one-time upgrade prompt for existing users on their next interactive
  `nexo update`.
- **Recommendation prompt:** during interactive `nexo update`, if the JSON
  recommends a newer model than the user's current profile AND the user's
  model is a prior NEXO default (not a customization), they are offered to
  migrate with `[y/N/later]`. Customized models are respected silently.
  Non-TTY (cron/headless) updates only log a hint and apply nothing.
- **Self-heal on update:** Claude-family models written into the Codex
  runtime profile by previous buggy versions are automatically reset to the
  current Codex default before client sync, so `~/.codex/config.toml` is
  regenerated clean.
- Client sync refuses to write Claude-family models into Codex config
  (defense in depth against future regressions).

## [5.3.22] - 2026-04-14

### Fix headless crons stalling on permission approval

- Claude Code: installer/updater now populates `permissions.allow` in
  `~/.claude/settings.json` with the minimum entries required for NEXO
  headless automation (followup-runner, email-monitor, deep-sleep, etc.)
  including `mcp__*` wildcard. Idempotent: preserves user customizations.
- Codex: installer/updater now sets `approval_policy = "never"` and
  `sandbox_mode = "danger-full-access"` as defaults in
  `~/.codex/config.toml` when unset. Existing user values are preserved.
- Fixes zombie crons on fresh installs that never had an interactive
  session populate the allowlist manually.

## [5.3.21] - 2026-04-14

### Fix update crash on slow source repos

- Catch `subprocess.TimeoutExpired` in `_git_in_repo()` so `nexo update`
  no longer crashes when the source repo has heavy untracked directories.
- Add `.venv/` to `.gitignore`.

## [5.3.20] - 2026-04-13

### Fix operator alias lost after update

- Migration now restores the custom operator shell alias (e.g. `nora`) in
  `.zshrc`/`.bash_profile` if it was lost during a previous update.
- Skip alias creation when operator name is "nexo" to avoid shadowing the
  CLI binary.

## [5.3.19] - 2026-04-13

### Deep scan: know the user better than they know themselves

- Email detection now uses 4 fallback methods (sandboxed container, legacy
  plist, Internet Accounts, Mail directory scan) — works on all macOS versions.
- Added Notes detection (macOS Notes.app SQLite + Linux Obsidian vaults).
- Added Reminders detection (macOS Reminders.app + Linux todo files).
- Added Photos library count (macOS Photos.app SQLite).
- Profile summary now shows life data (email, notes, reminders, contacts,
  photos, documents) alongside dev data.
- Linux equivalents for Notes (Obsidian) and Reminders (todo.txt).

## [5.3.18] - 2026-04-13

### Fix REPO_DIR resolution for npm installs

- `auto_update.py` now resolves templates, migrations, and version metadata
  from NEXO_HOME when running inside an npm-installed runtime (where
  `SRC_DIR.parent` points to the user home, not a repo root).
- Fixes `FileNotFoundError: CLAUDE.md.template` that blocked bootstrap sync,
  cron regeneration, and LaunchAgent updates on all npm-based installs.

## [5.3.17] - 2026-04-13

### Template copy fix in auto_update (git-based updates)

- Removed last hardcoded template file lists from `auto_update.py`.
  Both the backfill path and the packaged-update path now scan the full
  templates directory including subdirectories (`launchagents/`).
- Fixes `startup_preflight` FileNotFoundError for `CLAUDE.md.template`
  that blocked bootstrap sync and cron regeneration on npm installs.

## [5.3.16] - 2026-04-13

### Packaged installer fixes: client detection, template copy, doctor nvm

- `detect_installed_clients()` now searches nvm and `~/.nexo/bin` for `claude`
  — fixes `nexo doctor` reporting "claude_code not installed" on nvm setups.
- `npx nexo-brain init` and `nexo update` (npm path) now copy **all** template
  files including `CLAUDE.md.template`, `CODEX.AGENTS.md.template`, and the
  `launchagents/` directory — fixes `client_bootstrap_parity` crash.
- Removed hardcoded template file lists in the installer; uses directory scan.

## [5.3.15] - 2026-04-13

### Installer nvm PATH + Keychain unlock for headless

- `nexo-brain init` installer now uses `resolveLaunchAgentPath()` to auto-detect
  nvm node paths in generated LaunchAgent plists (was hardcoded to Homebrew paths).
- New Keychain setup step during install: stores macOS login password (chmod 600)
  so `nexo-cron-wrapper.sh` can `security unlock-keychain` before headless runs.
- `nexo-cron-wrapper.sh` now unlocks the login Keychain before executing commands,
  fixing "Not logged in" errors in headless Claude Code sessions.

## [5.3.14] - 2026-04-13

### LaunchAgent PATH detection + tomli dependency fix

- `resolve_launchagent_path()` now auto-detects nvm node paths so headless
  automation (email-monitor, followup-runner, catchup) finds `claude` even
  when node is installed via nvm instead of Homebrew.
- `tomli` moved from optional Dashboard section to Core in `requirements.txt`
  — fixes `ModuleNotFoundError` on Python < 3.11 installations.
- All LaunchAgent generators (`crons/sync.py`, `plugins/schedule.py`,
  `runtime_power.py`) use the dynamic PATH helper.

## [5.3.13] - 2026-04-13

### Core scripts use centralized model

- All core automation scripts (`nexo-catchup`, `nexo-sleep`, `nexo-immune`,
  `nexo-evolution-run`, `nexo-daily-self-audit`, `nexo-synthesis`,
  `nexo-postmortem-consolidator`, `deep-sleep/extract`, `deep-sleep/synthesize`,
  and others) now call `resolve_user_model()` instead of hardcoding `"opus"` or
  `"sonnet"`.  The user's model choice is respected everywhere.

## [5.3.12] - 2026-04-13

### Centralized model selection

- All automation profiles, task backends, and Codex defaults now inherit the
  user's configured model instead of hardcoding third-party model strings.
  The `fast` profile no longer defaults to Codex — it uses the user's backend
  and model.
- Added `resolve_user_model()` to `client_preferences` so scripts can query
  the single source of truth instead of hardcoding a model name.
- README updated to reflect the one-model-everywhere design.

## [5.3.11] - 2026-04-13

### Protocol + Cortex contract hardening

- `nexo_task_close` no longer coerces malformed `outcome` values into
  `failed`. Invalid close outcomes now return an explicit error and leave the
  protocol task untouched, so hot context, debt, and task history stop
  recording false failures.
- `nexo_task_open`, `nexo_confidence_check`, `nexo_cortex_check`, and
  `nexo_cortex_decide` now reject invalid `task_type` values instead of
  silently degrading them to a different valid type. The runtime says the
  contract is wrong rather than inventing a new meaning.
- `nexo_cortex_decide` now rejects invalid `impact_level` values instead of
  silently treating them as `high`, so Cortex evaluations stop inheriting a
  stronger urgency class than the caller actually supplied.
- The DB helpers behind protocol and Cortex now enforce the same task-type,
  close-outcome, and impact-level validation, so malformed internal calls can
  no longer bypass the public-tool hardening and contaminate persisted rows.
- Added regression coverage for invalid close outcomes, invalid task types,
  invalid impact levels, and the “do not mutate state on malformed close”
  contract across protocol and Cortex.

## [5.3.10] - 2026-04-13

### Packaged runtime truth + evolution telemetry + synthesis loop closure

- Packaged installs and updates now refresh `~/.nexo/package.json` from the
  published npm package during fresh install, migration, and same-version
  refreshes, so runtime metadata and doctor evidence stop carrying stale
  package versions after a successful update.
- `nexo doctor --tier deep` no longer marks a fresh packaged runtime as
  degraded just because `self-audit-summary.json` does not exist yet. When the
  daily self-audit is configured but the install/update is still fresh, doctor
  reports that the summary is pending instead of implying breakage.
- Weekly Evolution now asks the automation backend for explicit
  `dimension_scores` and `score_evidence`, and `nexo_evolution_status` falls
  back to the objective file when persisted metrics are still missing, so the
  status surface stops going blank after a real cycle.
- Daily synthesis now ingests `update-last-summary.json` only when it contains
  actionable runtime events such as deferred syncs, bootstrap changes, healed
  personal schedules, or update errors; routine cooldown/no-op summaries stay
  out of the briefing.
- Added regression coverage for the packaged installer metadata sync, the
  deep-doctor bootstrap contract, the Evolution telemetry contract, and the
  new synthesis/update-summary ingestion path.

## [5.3.9] - 2026-04-13

### Packaged core-artifact manifest heal for personal-script recovery

- Packaged `nexo update` no longer rebuilds `runtime-core-artifacts.json`
  from the live `~/.nexo/scripts` directory. It now uses the canonical `src/`
  tree from the installed npm package, so personal scripts stop being
  reclassified as core during update.
- Packaged runtimes now self-heal personal-script ownership even after a bad
  `5.3.8` update. Script classification prefers the canonical npm package
  source when available, and runtime doctor syncs personal scripts before
  LaunchAgent inventory checks so personal automations stop appearing as
  unknown core drift.

## [5.3.8] - 2026-04-12

### Packaged migration hotfix for new root runtime modules

- Packaged auto-migration now discovers and copies all top-level runtime Python
  modules from `src/` into `~/.nexo` instead of depending on a manual allowlist.
  That closes the real 5.3.7 regression where `nexo export` / `nexo import`
  could be published and documented correctly but still fail on upgraded
  packaged runtimes because `user_data_portability.py` never reached the live
  runtime tree.
- Added a regression contract test so the packaged installer keeps discovering
  new root runtime modules instead of silently omitting them in future releases.

## [5.3.7] - 2026-04-12

### Packaged update self-heal + portable user-data export/import

- `nexo update` on packaged installs now syncs cron definitions, skips
  same-file hook copy noise, and reloads managed macOS LaunchAgents after a
  real version bump so the normal happy path no longer depends on immediately
  running `nexo doctor --tier runtime --fix`.
- `nexo doctor` now separates active runtime breakage from tracked historical
  Codex drift more honestly: conditioned-file transcript drift no longer keeps
  packaged runtimes red once no conditioned protocol debt remains open, while
  the evidence still stays visible for auditability.
- Added `nexo export` and `nexo import` for portable user-data bundles covering
  the active DB, brain state, coordination artifacts, selected config, and
  personal scripts, with an automatic safety backup before import restore.
- Added regression coverage for the new export/import CLI flow and for packaged
  update cron/LaunchAgent self-heal behavior, plus the new doctor severity
  contract for tracked conditioned-file drift.

## [5.3.6] - 2026-04-12

### Claude MCP bootstrap + runtime hygiene hardening

- `nexo clients sync` / managed Claude Code sync now writes the NEXO MCP
  server to `~/.claude.json` as well as `~/.claude/settings.json`, matching
  current Claude Code user-scoped MCP resolution instead of leaving `nexo chat`
  and `claude mcp` out of sync.
- `nexo scripts` now classifies core runtime artifacts more robustly across
  packaged installs, runtime roots, hook directories, and legacy alias names so
  operator-facing script inventory stays clean.
- `nexo schedule status` now distinguishes active/open runs from failures and
  shows run age for still-running jobs instead of collapsing missing exit codes
  into false negatives.
- Retroactive learnings now ignore keyword-only matches when a learning defines
  `applies_to` but the scoped blast radius does not match, cutting false review
  followups outside the intended target.
- The final release-audit skill resolves repo roots more reliably from cwd,
  runtime metadata, and Project Atlas instead of assuming one fixed checkout
  layout.
- Added a published core skill for running NEXO audit phases with empirical
  verification discipline and autonomous execution defaults.
- Added regression coverage for Claude root MCP sync, schedule open-run status,
  retroactive-learning gating, and packaged/runtime release validation paths.

## [5.3.5] - 2026-04-12

### Version banner cache correction

- `nexo` and `nexo chat` no longer show a cached `Latest` version that is
  older than the runtime you just installed.
- When the cached npm version lags behind the installed runtime version, the
  CLI now treats the installed runtime as the floor and refreshes the cache
  accordingly.
- Added regression coverage for both help and chat banner paths so post-update
  version visibility stays honest.

## [5.3.4] - 2026-04-12

### Core/personal runtime boundary cleanup + version visibility

- `nexo scripts` now keeps legacy hook aliases (`nexo-postcompact.sh`,
  `nexo-memory-precompact.sh`, `nexo-memory-stop.sh`,
  `nexo-session-briefing.sh`) out of the personal bucket on packaged
  installs.
- `nexo update` now removes those retired aliases from `NEXO_HOME/scripts/`
  when the canonical hook already exists in `NEXO_HOME/hooks/`.
- `nexo` and `nexo chat` now show a lightweight version status line with the
  installed runtime version and the latest published npm version.
- Added regression coverage for alias cleanup and the CLI version-status
  banner.

## [5.3.3] - 2026-04-12

### Doctor inventory alignment

- `nexo doctor` now recognizes `com.nexo.backup` as a core auxiliary LaunchAgent, matching the packaged installer/runtime inventory.
- Eliminates the false "Unknown com.nexo LaunchAgents" warning on clean packaged installs that still use the built-in hourly DB backup helper.

## [5.3.2] - 2026-04-12

### Runtime boundary hardening — core vs personal scripts

- **fix(personal-scripts):** packaged installs now persist a runtime
  core-artifacts manifest, and the personal script registry uses it to
  classify packaged core scripts and hook shims as core instead of mixing
  them into the personal bucket.
- **fix(heartbeat-hooks):** Claude Code heartbeat hooks are now shipped as
  core hooks, not ad-hoc personal runtime scripts. `nexo update` rewrites
  managed client configs to the core hook paths and removes retired legacy
  heartbeat files from `NEXO_HOME/scripts/`.
- **fix(update):** packaged and source-based update flows now refresh the
  runtime core-artifacts manifest during sync, so future updates keep the
  core/personal boundary stable instead of relying on filename guesses.
- **fix(templates):** removed the stale `com.nexo.github-monitor.plist`
  template that referenced a non-packaged script, avoiding another false
  signal that operator-specific maintenance automation was core product
  surface.
- **tests:** added regression coverage for runtime core-artifacts manifests,
  packaged-update cleanup of retired heartbeat files, and the new
  classification rules.

## [5.3.1] - 2026-04-12

### Packaged runtime normalization — clean `nexo update` path

- **fix(runtime-home):** packaged installs now resolve the canonical runtime
  home from `~/.nexo` instead of drifting back to legacy `~/claude` or a
  source checkout. This closes the gap where a normal npm user could end up
  with wrappers, hooks, or helper scripts still pointing at non-packaged paths.
- **fix(update):** `nexo update` now refreshes packaged client/bootstrap
  artifacts after upgrade and preserves the runtime/data split expected by
  normal npm installs. Existing users can move forward without needing a repo
  checkout on disk.
- **fix(doctor):** packaged runtimes no longer fail repo-only release-artifact
  checks that make sense in source trees but not in installed user runtimes.
- **fix(personal-scripts):** script registry and helper/runtime path resolution
  now consistently use the canonical packaged home, so personal scripts,
  startup preflight, and managed clients keep working after update.
- **tests:** added packaged-runtime coverage for runtime-home resolution,
  startup preflight, client sync, doctor behavior, update flow, and personal
  script registry migration handling.

## [5.3.0] - 2026-04-12

### `nexo uninstall` — clean separation of runtime and user data

- **feat(cli):** `nexo uninstall` stops all LaunchAgents/systemd timers,
  removes MCP server and hooks from Claude Code settings, removes
  runtime files (server.py, plugins/, hooks/, etc.), and preserves all
  user data (databases, brain, personal scripts, operations, logs).
  Supports `--dry-run` to preview and `--delete-data` for full wipe.
  Writes `.uninstalled` marker so reinstall detects existing data.
- **docs:** Updated llms.txt and README for v5.2.1 changes.

## [5.2.1] - 2026-04-12

### Bug fixes & cortex outcome feedback loop

- **fix(deep-sleep):** `_parse_any_datetime` in `apply_findings.py` now
  explicitly strips timezone info, fixing TypeError when comparing
  offset-naive and offset-aware datetimes (caused 7/8 Phase 4 failures).
- **feat(cortex):** `cortex_decide()` auto-creates a `decision_outcome`
  when no existing outcome is linked, closing the decision → verification
  feedback loop. The daily `outcome-checker` cron verifies these
  automatically.

## [5.2.0] - 2026-04-12

### Response contract i18n & scoring — cortex-quality snapshot reader

A focused minor release that closes two real gaps in the Cortex layer
identified during the audit of the response-contract behaviour: the
`HIGH_STAKES_KEYWORDS` detector was English-only and had no way to
reward tasks with meaningful prior context, and the `nexo-cortex-cycle`
cron was writing a quality snapshot that no reader ever consumed.

#### Protocol — response contract Fase 1

- **Bilingual high-stakes detection.** `HIGH_STAKES_KEYWORDS_ES` adds ~45
  Spanish keywords (`crítico`, `producción`, `facturación`, `clientes`,
  `despliegue`, `credencial`, `privacidad`, `reembolso`, accented and
  unaccented variants). A task written in Spanish now trips the same
  high-stakes gate as its English twin — previously a goal like
  *"migrar la base de datos de producción"* silently skipped the
  high-stakes penalty because none of its words matched the English set.
- **Negation-aware detection.** `NEGATION_PATTERNS` suppresses the
  high-stakes flag when the text explicitly disclaims touching the
  sensitive area (`sin afectar producción`, `no tocar prod`,
  `without touching production`, `don't modify`, etc.). Before this
  release these boundary statements caused false positives because the
  raw keyword was physically present in the string. `_detect_high_stakes`
  now runs negation suppression before keyword matching.
- **Positive signals on the confidence score.** `evaluate_response_confidence`
  accepts two new optional kwargs:
  - `pre_action_context_hits: int` — adds `+min(10, hits*2)` when the
    pre-action context lookup returned relevant prior context
  - `area_has_atlas_entry: bool` — adds `+5` when the task's area is a
    known entry in `project-atlas.json`
  Both are capped so they can never override a real risk signal. Before
  this release the score was purely a penalty accumulator; there was no
  mechanism to reward a task that *did* load the right context, which
  meant the final score drifted downward even when the agent was well
  prepared.
- **Numeric safeguard over the boolean decision tree.** After the
  existing `high_stakes/unknowns/evidence/verification` rules pick a
  mode, `evaluate_response_confidence` now applies a monotonic
  safeguard: `answer` with `final_score < 50` is downgraded to `verify`,
  and `verify` with `high_stakes=true` and `final_score < 30` is
  downgraded to `defer`. The safeguard can only make response
  discipline *stricter*, never looser. This catches edge cases where
  accumulated soft penalties didn't trip any single boolean rule but
  the confidence was objectively low.

#### Cortex — quality snapshot reader

- **`handle_cortex_quality` now reads the cron snapshot.** The
  `nexo-cortex-cycle` cron (every 6h, `src/scripts/nexo-cortex-cycle.py`)
  has been writing `$NEXO_HOME/operations/cortex-quality-latest.json`
  since v5.1.0, with an explicit promise in its own docstring that
  *"dashboards / morning briefings can read fresh metrics without
  re-running the SQL"*. That reader never existed —
  `handle_cortex_quality` recomputed the summary from the DB on every
  call. This release closes the loop: the handler now serves the cached
  snapshot when `days in {1, 7}`, the file is fresh (< 6h 30m old), and
  `schema == 1`. Any failure (missing file, corrupt JSON, stale
  timestamp, unknown window, schema mismatch) falls back silently to
  the live `cortex_evaluation_summary` computation. The cache is a
  performance optimisation, never a correctness dependency.
- **Observable source.** The handler's JSON response now includes
  `"source": "cache" | "live"` so callers (dashboards, morning
  briefings, agents) can tell which path was taken without extra
  tooling.

#### Tests

- `tests/test_protocol.py` — 9 new tests covering:
  - Spanish keyword detection (accented and unaccented)
  - Negation suppression (bilingual)
  - Positive signal boosts (capped)
  - Numeric safeguard transitions
  - Score bounds
- `tests/test_cortex_quality_cache.py` — 7 new tests covering:
  - Fresh cache hit serves 7d / 1d windows without touching the DB
  - Stale cache (>6h 30m) falls back to live
  - Corrupt schema falls back to live
  - Invalid JSON falls back to live
  - Missing file falls back to live
  - Non-cached windows (e.g. 30d) always use live

All pre-existing cortex + protocol tests continue to pass — the new
positive-signal kwargs are defaulted so no existing caller is broken,
and the numeric safeguard is monotonic over the existing boolean tree.

No breaking changes, no bootstrap / startup / Deep Sleep / client
parity surfaces touched.

## [5.1.1] - 2026-04-12

### Release trace hygiene — runtime + self-audit + diary

A focused patch that closes the gap where audit-phase workflow traces and
self-audit placeholder goals silently accumulated in the runtime. No breaking
changes, no bootstrap / startup / Deep Sleep / client-parity surfaces touched.

- **New runtime doctor check `runtime.release_trace_hygiene`** flags stale
  `audit-phase` `workflow_runs` (>6h open) and stale active `WG-AUDIT-*` /
  `NEXO-AUDIT-*` `workflow_goals` with no open runs, so drifted release traces
  surface as a visible `degraded` check instead of quietly accumulating.
- **Daily self-audit auto-retires stale `WG-AUDIT-*` placeholder goals** via
  `_retire_stale_audit_goals_inline()`. Goals owned by `system:self-audit`
  with the placeholder `next_action`, no open runs, and no activity for
  >36h are marked `abandoned` with an explicit `blocker_reason`. The
  self-audit recreates them only if the underlying pattern reappears.
- **`episodic_memory.handle_session_diary_write` splits commit_ref warnings**
  into recent (last 7 days) vs historical buckets, so diary warnings
  distinguish live drift from dormant debt instead of lumping them together.

Tests:
- `tests/test_doctor.py::test_release_trace_hygiene_flags_stale_audit_artifacts`
- `tests/test_self_audit.py::test_retire_stale_audit_goals_inline_abandons_old_placeholders`
- `tests/test_episodic_memory.py::test_session_diary_write_distinguishes_recent_and_historical_commit_ref_gaps`

All three pass locally and in CI (Lint / Security / Release readiness /
Verify integrations / Verify client parity all green on PR #127).

## [5.1.0] - 2026-04-11

### NEXO-AUDIT-2026-04-11 — Phases 2-5 delivered end-to-end

This release lands the entire NEXO-AUDIT-2026-04-11 roadmap (Phases 2 through 5
plus the pre-release Bloques A-D) as a single coordinated version bump. Every
item was empirically verified before touching code — about 46% of the audit's
originally-flagged gaps turned out to be false positives, which is why this
changelog focuses on what actually changed rather than on the audit list
itself.

### Phase 2 — open evolution / adaptive / skills / cortex loops now close under themselves
- Evolution cycle now auto-applies user-approved proposals on the next run
  via `_apply_accepted_proposals()` in `scripts/nexo-evolution-run.py`, backed
  by the new `evolution_log.proposal_payload` column (migration m38). Accepted
  proposals can no longer linger in `accepted` state indefinitely.
- `skills_runtime.auto_promote_outcome_patterns_to_skills()` now materializes
  recurring outcome patterns into draft skills without manual curation, and
  `detect_skill_coactivation_candidates()` exposes a Voyager-style detector
  that groups `skill_usage` by session and surfaces co-occurring pairs as
  composite-skill candidates via `nexo_skill_compose_candidates`.
- New `retroactive_learnings.apply_learning_retroactively()` walks recent
  decisions, scores them against a newly-added learning, and opens
  deterministic `NF-RETRO-L<id>-D<id>` followups when the learning would have
  changed the decision. Exposed via `nexo_learning_apply_retroactively`.
- Adaptive learned-weight rollback now surfaces as a visible followup on the
  next heartbeat so the operator sees the runtime has backed off instead of
  the signal staying inside `adaptive_log`.
- New Cortex quality cron (`scripts/nexo-cortex-cycle.py`, every 6h via
  `src/crons/manifest.json`) watches accept_rate / linked_success /
  override_gap thresholds and opens `NF-CORTEX-QUALITY-DROP` idempotently
  when Cortex quality degrades between cycles.
- `nexo_heartbeat` surfaces open `protocol_debt` rows for the active task so
  the agent cannot drift past a dropped discipline rule silently.
- Deep-sleep `code_change` actions now stage their findings into
  `evolution_log` with proposal payloads so the evolution cycle can apply
  them, closing the end-to-end loop from observation → synthesis → apply.

### Phase 3 — cognitive subsystems close their own loops with user-visible evidence
- `cognitive._search.search()` now accepts `dream_weight: float` and reranks
  dream-insight memories through that weight when set. A new
  `_somatic_boost_results()` step (max +0.10) folds somatic markers into the
  same reranking path, so emotional salience and dream salience are both
  first-class signals instead of dead columns.
- State watchers now open and auto-resolve deterministic `NF-WATCHER-{id}`
  followups through `_open_watcher_followup` /
  `_resolve_watcher_followup`, so a watcher firing is always externally
  observable rather than buried in runtime logs.
- Cognitive-decay now surfaces correction fatigue as a visible followup when
  the fatigue signal crosses its threshold, instead of only adjusting
  memory weights invisibly.
- Hook lifecycle observability: new `src/hook_observability.py` +
  `src/scripts/nexo-hook-record.py` shim record hook runs into a `hook_runs`
  table (migration m39) with 3 indexes. `nexo_hook_runs` tool exposes recent
  runs + a health summary so hook failures surface instead of silently
  dropping work.
- `auto_update` is now guarded by POSIX `fcntl.flock` with stale-steal at 10
  minutes, fixing a race where two concurrent `nexo update` invocations could
  stomp each other mid-sync.

### Phase 4 — automated lint / security / coverage / release gates on every PR
- New `.github/workflows/lint.yml` enforces ruff `E9 / F63 / F7 / F82 / F821`
  on every PR and push to main. Baseline pass fixed 5 latent F821 bugs in
  `cognitive/_memory.py`, `cognitive/_ingest.py`, `tools_menu.py`.
- New `.github/workflows/security.yml` runs `bandit -r src/` at
  `high severity + high confidence`. Baseline pass fixed 10 weak-hash flags
  (`usedforsecurity=False`) across `plugins/protocol.py`, `plugins/simple_api.py`,
  `scripts/check-context.py`, `scripts/deep-sleep/apply_findings.py`,
  `scripts/deep-sleep/synthesize.py`, and `scripts/nexo-daily-self-audit.py`.
- Coverage baseline tests (`test_decay_baseline.py`, `test_trust_baseline.py`,
  `test_plugin_loader_baseline.py`, `test_fase4_lint_baseline.py`,
  `test_security_baseline.py`, `test_release_readiness_baseline.py`) pin the
  contract surface area of the cognitive / plugin loading / security / release
  stack so a refactor cannot silently delete it.
- `.github/workflows/release-readiness.yml` now runs
  `verify_release_readiness.py --ci` on **every PR** instead of only on tag
  push, which means a PR that breaks the release contract fails loudly
  instead of waiting until release time to surface.
- `requirements-dev.txt` pins `ruff>=0.6.0`, `pytest-cov>=4.0`, and
  `bandit[toml]>=1.7`. `pyproject.toml` carries the ruff / bandit / pytest
  configuration so local dev matches CI exactly.

### Phase 5 — shippable differentiators vs existing memory frameworks
- Bitemporal Knowledge Graph export: `knowledge_graph.export_to_jsonld()` and
  `knowledge_graph.export_to_graphml()` emit the full graph in JSON-LD (with
  `nexo:*` vocabulary) or GraphML (for igraph / Gephi / NetworkX / Cytoscape).
  Both accept an `as_of` ISO timestamp that replays the historical snapshot
  through `kg_edges.valid_from / valid_until`. Exposed via `nexo_kg_export`.
- OpenTelemetry integration: new `src/observability.py` soft-imports
  `opentelemetry` and only activates when `OTEL_EXPORTER_OTLP_ENDPOINT` or
  `OTEL_SERVICE_NAME` is set. `tool_span()` is a no-op context manager when
  OTEL is disabled and a real span when enabled, so NEXO can be traced with
  `ai.tool.*` semantic conventions without a hard dependency.
- `benchmarks/results/comparison-vs-competition-2026-04.md` documents an
  honest feature matrix vs Letta, Mem0, Zep, Graphiti, Cognee, and DSPy so
  the differentiators (bitemporal KG, metacognitive guard, trust scoring,
  Atkinson-Shiffrin decay, native MCP surface) are defensible with receipts.
- Voyager-style skill co-activation detector (see Phase 2) ships as the first
  evidence of automated skill composition from live usage.

### Audit followups (NEXO-AUDIT-2026-04-11) — closed under evidence
- `nexo_heartbeat` now auto-fires `compute_mode` every heartbeat so
  `adaptive_log` actually gets populated from live signals instead of staying
  empty.
- Server FastMCP instructions now tell the agent to register outcomes
  proactively, closing the gap where tools existed but the agent didn't know
  it was supposed to call them.
- Every other Phase 2-5 followup was either marked `resolved` with evidence
  or left as an explicit tracked followup with a clear next action.

### Release safety — v5.0.x → v5.1.0 update path
- `auto_update._reload_launch_agents_after_bump()` now `launchctl unload`s
  and re-`load`s every `com.nexo.*.plist` after a version bump on macOS, so
  long-lived crons pick up the new codebase automatically instead of running
  the pre-bump version until the next reboot.
- Migrations m38 (`evolution_log.proposal_payload`) and m39 (`hook_runs` +
  3 indexes) are idempotent `ALTER TABLE` / `CREATE TABLE IF NOT EXISTS`
  statements safe to re-run across every v5.0.x baseline.
- `tests/test_update_path_and_reload.py` pins the hot-reload + migration
  contract. `tests/test_auto_update_lock.py` pins the concurrent-run
  protection so a regression here fails CI instead of corrupting a real
  install.

### Validation
- `python3 -m pytest tests/ -q` — all tests passing.
- `python3 scripts/verify_release_readiness.py --ci --contract release-contracts/v5.1.0.json --require-contract-complete` passes locally and in CI.
- ruff + bandit + release-readiness workflows all green on main.
- Live runtime `nexo doctor --tier all` returns `HEALTHY` after sync.

## [5.0.4] - 2026-04-11

### Runtime Bridge + Doctor Signal Cleanup
- Hardened the vendorable `templates/nexo_helper.py` bridge so personal scripts and subprocess flows resolve `NEXO_HOME` and the `nexo` CLI robustly instead of depending on PATH luck or a single home layout.
- Added structured JSON automation helpers to the vendorable bridge, giving personal-script callers a canonical path for automation jobs that must parse machine-readable output cleanly.
- Refined doctor scoring so advisory-only self-audit warnings remain healthy and a single missing usage-telemetry row does not degrade the full runtime. That keeps the live signal honest without punishing one-off backend telemetry gaps.
- Tightened the managed Claude Code and Codex bootstraps for single-artifact reads (`email`, `diary`, `reminders`, `followups`): after the first relevant read, NEXO should answer immediately instead of silently chaining more lookups and looking hung.

### Validation
- Added regression coverage for the single-missing-usage telemetry path on the runtime doctor suite.
- Re-ran `python3 -m pytest tests/test_doctor.py -q` (`80 passed`) and revalidated a live runtime with `nexo doctor --tier all` returning `HEALTHY`.

## [5.0.3] - 2026-04-11

### Terminal Bootstrap + Runtime Hardening
- Fixed `nexo chat` so Claude Code no longer receives the selected path as a fake prompt. The terminal client now launches in the requested working directory and starts from an explicit NEXO bootstrap prompt instead of the stray `.` / cold-open path.
- Codex interactive launch now gets the same explicit startup prompt, so both managed terminal clients begin by acting as NEXO immediately instead of waiting for the operator to force bootstrap manually.
- Added explicit response-pacing rules to the managed Claude Code and Codex bootstraps. After the first relevant tool/artifact result, NEXO now answers visibly before chaining deeper investigation, which removes the "looks hung" behavior on single-email / single-fact asks.
- Hardened Drive signal detection in `heartbeat`: the hot path now passes `allow_llm` explicitly, defaults to `NEXO_DRIVE_LLM_IN_HEARTBEAT=0`, and keeps LLM classification out of interactive startup/heartbeat unless it is deliberately re-enabled.
- Added a bounded timeout to the vendorable `templates/nexo_helper.py` CLI bridge so personal scripts using `nexo scripts call` cannot wait forever on a stuck subprocess path.
- Revalidated the doctor hotfix path on the live runtime after sync: the progress banner appears immediately, `nexo doctor --tier all` stays healthy, and the protocol/automation false positives remain closed.

### Public Surface Refresh
- Replaced the old external video dependency on the public site with a self-hosted overview video page (`/watch/`) and updated home/features embeds plus README/watch links to point at the current v5 asset set.
- Refreshed the public infographic and social-preview assets so README, docs/blog surfaces, and the main site all show the current v5 graphic instead of drifting across older versions.

### Validation
- Added regression tests covering the new interactive startup prompt flow for Claude Code and Codex, explicit `cwd` handoff for terminal launches, and the new heartbeat drive flag default/override path.
- Re-ran the focused runtime regression suites (`drive`, `hot_context`, `agent_runner`, `client_sync`, `cli_scripts`, `doctor`) and revalidated the live runtime with `nexo update`, `nexo doctor --tier all`, Codex/Claude launch smokes, client bootstrap sync, diary retrieval, and real email/tool flows.

## [5.0.2] - 2026-04-10

### Doctor Schema Drift Hotfix
- Fixed the deep-doctor learning-count check so it reads the live `learnings` schema correctly on both current installs (`status`) and older installs (`archived`) instead of reporting a misleading skipped check on healthy runtimes.
- Revalidated the corrected path on a real upgraded install: `nexo update`, `nexo doctor --tier deep`, and `nexo doctor --tier all` all pass cleanly after the sync.
- Re-ran a real Claude Code startup smoke after the runtime sync so the patch ships with fresh evidence that the corrected install path still boots cleanly end to end.

### Validation
- Added regression coverage for both schema variants in the deep doctor suite, so future releases cannot silently drift back to the stale `archived`-only assumption.

## [5.0.1] - 2026-04-10

### Upgrade Path + Client Sync Hardening
- Fixed `client_sync` so managed Claude Code hooks from older releases are purged when they no longer belong to the current core hook set, instead of surviving forever as stale managed entries.
- Eliminated the legacy `heartbeat-guard.sh` drift path that could leave upgraded installs showing noisy PostToolUse errors and an apparent "NEXO is hanging" symptom even though the runtime itself was still healthy.
- Kept custom operator hooks intact while removing only obsolete managed identities, so hook cleanup does not regress local customizations.
- Revalidated the live upgrade path on a real install after the fix: client sync, Codex/Claude Code headless runtime access, inbox processing, email monitor health, and `nexo update` all pass again on the corrected runtime.

### Validation
- Added a dedicated regression test proving that sync removes obsolete managed Claude Code hooks while preserving custom hooks.
- Refreshed the v5 smoke artifact and release contract so the shipped evidence reflects the real post-5.0 upgrade path instead of only the original feature-line release.

## [5.0.0] - 2026-04-10

### Goal-Driven Decisions + Outcome Learning
- Added Goal Engine v1 with explicit, auditable `goal_profiles`, runtime tools to inspect and manage them, and decision traces that show which objective weights were active for a recommendation.
- Extended the existing Cortex into a real Decision Cortex v2 path: high-impact work can now rank alternatives with goal weights, outcome history, override history, structured penalties, and persisted evaluation summaries instead of treating "context" as the only intelligence layer.
- Added the first structured-learning bridge from repeated outcomes into future decisions. Strong repeated patterns can now be captured as durable outcome-pattern learnings, and later decisions can read that structured signal back as an explicit score adjustment.
- Outcome-backed skill evolution is now real: strong patterns can seed reusable skills, outcome evidence can influence promotion and retirement, and product-facing reuse surfaces such as featured skills now change because of that evidence instead of only trust counters.

### Proof + Runtime Integrity
- Rebuilt the runtime benchmark pack around a broader matrix with checked-in results, generated summaries, and compare artifacts that make the operator/runtime advantage inspectable instead of anecdotal.
- Replaced Drive's primary hardcoded-regex detection path with semantic classification as the authoritative signal path, leaving regex only as a narrow fallback.
- Hardened the CLI-to-core runtime path so personal scripts and cron/subprocess flows can call core tools without depending on the interactive Claude Code environment to have already loaded the right Python runtime.
- Added an official path to inspect and resolve historical `protocol_debt`, and updated runtime doctor scoring so it distinguishes open live debt from already-audited historical drift and from decision-eval rollout warmup.
- Audited the live update path on a real installation, fixed a real `cron-sync` bug that could remove official personal LaunchAgents, and revalidated the runtime with `nexo update` plus `nexo doctor --tier all`.

### Validation
- Added or refreshed smoke/contract artifacts for both v4.5 and v5.0 release lines, including `scripts/run_v5_0_smoke.py` and `release-contracts/v5.0.0.json`.
- Confirmed the release-critical regression suites across protocol, doctor, cron sync, outcomes, cortex decisions, skills, and public scorecard generation.

## [4.1.0] - 2026-04-09

### Drive/Curiosity — Autonomous Investigation Signals
- Added a first-class Drive/Curiosity layer that accumulates tension-based signals during normal work (heartbeat, task close) and investigates autonomously when signals mature. Five signal types: anomaly, pattern, connection, gap, and opportunity.
- New MCP tools `nexo_drive_signals`, `nexo_drive_reinforce`, `nexo_drive_act`, and `nexo_drive_dismiss` expose the drive surface publicly while internal detection runs passively from heartbeat and task close context hints.
- Signals follow a lifecycle: latent (noise filtering) → rising (reinforced 2+ times) → ready (investigated silently) → acted/dismissed. Ready signals do not decay. Latent and rising signals decay daily, enforced by the maintenance scheduler.
- Deep Sleep synthesis now includes a Drive phase that investigates ready signals, promotes rising signals with cross-area connections, and dismisses stale signals overnight. The apply phase executes drive synthesis findings automatically.
- Heartbeat now surfaces mature drive signals in its response when relevant to the current work area, so the agent is aware of accumulated curiosity without blocking the operator.
- Detection uses deterministic heuristics (regex patterns for anomalies, recurring patterns, knowledge gaps, and opportunities) to avoid adding latency or model calls to the heartbeat path.
- Maximum 30 active signals enforced, with weakest latent signals dropped when the cap is reached.

### Validation
- Added comprehensive test coverage for drive signals: creation, reinforcement, tension promotion, decay, status transitions, similarity deduplication, max cap enforcement, detection heuristics, and MCP handler integration. 38 new tests, 444 total suite green.

## [4.0.1] - 2026-04-09

### Release Alignment + Protocol Reminder
- Published the post-`v4.0.0` mainline fix as a real patch release so git installs, npm installs, GitHub Releases, and the public website converge on the same shipped state instead of leaving `main` ahead of the public release tag.
- Added a correction-aware heartbeat reminder in `tools_sessions`: when the operator clearly corrects the agent and no recent learning was captured, NEXO now emits a `LEARNING REMINDER` instead of relying on model discipline alone.
- Finished the `datetime.UTC` cleanup in the trust-history and user-state paths, so the shipped runtime now matches the Python 3.14 warning cleanup already claimed by the 4.0 release notes.
- Kept the broader `4.0.0` memory-surface package intact while making the public release channels honest again about what is actually shipped.

## [4.0.0] - 2026-04-09

### Memory Surfaces Become Product Surfaces
- Added a first-class multimodal reference layer for non-text artifacts with new MCP tools `nexo_media_memory_add`, `nexo_media_memory_search`, `nexo_media_memory_get`, and `nexo_media_memory_stats`. Screenshots, PDFs, audio, video, and other non-text artifacts can now live in NEXO as structured memory objects instead of being reduced to ad-hoc notes.
- Added structured pre-compaction auto-flush so session context is no longer left to discipline alone. The pre-compact hook now persists actionable summaries and next steps into a dedicated `session_auto_flush` layer, feeds recent-context continuity, and exposes audit tools `nexo_auto_flush_recent` and `nexo_auto_flush_stats`.
- Promoted the claim graph into a public knowledge-wiki surface with provenance, evidence, verification state, freshness scoring, linting, and linking through new MCP tools `nexo_claim_add`, `nexo_claim_search`, `nexo_claim_get`, `nexo_claim_link`, `nexo_claim_verify`, `nexo_claim_lint`, and `nexo_claim_stats`.
- Added readable memory export via `nexo_memory_export`, producing an auditable markdown bundle for learnings, decisions, claims, media memories, auto-flush records, user-state snapshots, and cognitive stats instead of forcing operators to trust only hidden database state.
- Added a stronger inspectable user-state model through `nexo_user_state`, `nexo_user_state_history`, and `nexo_user_state_stats`, combining trust, sentiment, correction fatigue, diary signals, and hot-context pressure into one explicit adaptive surface.
- Exposed more retrieval controls publicly through `nexo_cognitive_retrieve`, including `hybrid_alpha`, `decompose`, `exclude_dreams`, and `exclude_dormant`, so operators can tune retrieval behavior without touching internal code paths.
- Added an explicit memory-backend contract and status surface through `nexo_memory_backend_status`, formalizing how newer memory layers declare capabilities while SQLite + FTS5 remains the default production backend.

### Included Since v3.2.0
- Included the unreleased protection for live-repo automation writes, so managed automation stops relying on weak path conventions around mutable runtime copies.
- Included the public tool-explanation enrichment pass, making `nexo_tool_explain` more useful as a runtime self-knowledge surface.
- Included the Deep Sleep import-path fix that restores stable collection startup by resolving the shared transcript parser correctly in both source and installed runtime layouts.
- Included the core-vs-personal ownership hardening from the updater/doctor path, so git-based installs preserve personal script collisions, auxiliary core LaunchAgents are inventoried explicitly, and runtime audits stop treating that boundary as soft convention.

### Validation
- Added targeted regression coverage for claims/wiki, multimodal memory, user-state snapshots, pre-compaction auto-flush, readable memory export, public retrieval knobs, and backend contract status.
- Cleaned newly introduced UTC handling to avoid Python 3.14 deprecation warnings in the richer user-state path and trust-history lookups.

## [3.2.0] - 2026-04-08

### Recent Memory Fallbacks + Live System Catalog
- Added public transcript fallback MCP tools: `nexo_transcript_recent`, `nexo_transcript_search`, and `nexo_transcript_read`. When hot context, recall, or diaries are not enough, agents can now search and read recent Claude Code / Codex transcripts directly instead of claiming the conversation is lost.
- Extracted transcript parsing into a shared `transcript_utils.py` module and wired Deep Sleep to use the same parser as the public MCP surface. This removes parser drift between overnight analysis and operator-visible transcript fallback tools.
- Added a live NEXO system catalog / ontology built from canonical sources at read time, not from a stale copied registry. New public tools `nexo_system_catalog` and `nexo_tool_explain` now expose the current map of core tools, plugin tools, skills, scripts, crons, projects, and artifacts.
- Updated docs, quickstart, script guidance, and public release surfaces so the new recent-memory ladder is explicit: `hot context -> transcript fallback -> live system catalog for NEXO self-knowledge`.

## [3.1.9] - 2026-04-08

### Runtime Update Bootstrap Fix For Hot Context
- Hardened `nexo update` so it no longer depends on a hand-maintained root-module list when new top-level runtime modules are introduced. The updater now discovers and copies all top-level `.py` runtime modules dynamically.
- This specifically fixes the bootstrap gap where an installed runtime could copy the new `server.py` from the hot-context release but fail before importing because the old updater did not know it also had to copy `tools_hot_context.py`.
- Added regression coverage for runtime updates copying the new `tools_hot_context.py` module into installed runtimes, so future public releases can add root runtime modules without silently breaking the upgrade path.

## [3.1.8] - 2026-04-08

### Hot Context Release Stabilization
- Promoted the hot-context memory release as `v3.1.8` after the first `v3.1.7` tag exposed CI-only regressions before public publication. The shipped release now matches the green test suite and release artifacts instead of leaving an orphan tag as the public truth.
- Hardened the modular `db` package reload path so runtime/test DB switches no longer leak stale submodule state. `reload(db)` now refreshes the concrete submodules, and package-level core access resolves against the current live module state instead of captured references.
- Made hot-context capture additive against partial/minimal schemas. If `hot_context` / `recent_events` tables are unavailable, reminder/followup creation and self-audit flows now degrade safely instead of crashing.
- Updated learning and hot-context DB helpers to resolve `db._core` dynamically, eliminating connection drift that only appeared under the full suite and release CI order.

## [3.1.7] - 2026-04-08

### Hot Context Memory + Dashboard History Discipline
- Added a first-class `hot context` / `recent events` layer for 24-hour operational continuity across sessions, clients, and channels. Core now persists active recent topics, recent timeline events, and a reusable pre-action bundle instead of relying only on long-term recall and diaries.
- Added new MCP tools for this layer: `nexo_recent_context_capture`, `nexo_recent_context`, `nexo_pre_action_context`, `nexo_recent_context_resolve`, and `nexo_hot_context_list`.
- Wired hot context into core runtime surfaces: `heartbeat`, `task_open`, `task_close`, reminders, and followups now all feed the same shared recent-memory substrate.
- Added dashboard observability for `Hot Context 24h` and a public `/api/recent-context` endpoint so recent operational memory is visible and testable instead of being hidden in prompts.
- Documented the separation between reminder/followup history and recent operational memory, including the expected script pattern for pre-action loading, capture, and resolution.
- Closed a dashboard loophole: reminder/followup mutations and dashboard moves now require a fresh `READ_TOKEN`, so the official UI can no longer bypass the history-first discipline enforced by the MCP tools.

## [3.1.6] - 2026-04-08

### Deep Sleep Abandoned Followups Hotfix
- Deep Sleep no longer turns abandoned-project discoveries into live `PENDING` followups. New `[Abandoned]` items are created directly as archived historical context, which keeps the trail visible without polluting active work queues.
- The archived creation path now records an explicit followup-history note so operators and agents can see that the item was intentionally stored as history instead of silently disappearing.
- Added regression coverage so abandoned followups stay archived and non-actionable across future releases.

## [3.1.5] - 2026-04-08

### Dashboard + Proactive History Hygiene
- The dashboard operations view now treats completed, deleted, and full-history reminder/followup states explicitly instead of collapsing everything into a vague “all” bucket. Agents and operators can inspect soft-deleted past work without losing the default open-work view.
- Reminder and followup API list filters now normalize status families consistently, so `completed`, `deleted`, `history`, and `all` mean the same thing across dashboard screens and backend endpoints.
- The proactive dashboard no longer surfaces deleted, waiting, cancelled, archived, blocked, or completed reminders/followups as live overdue work, which removes another source of “zombie state” from the public operational surface.
- Added regression coverage for the new dashboard filters and for proactive scans ignoring inactive reminder/followup states.

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
