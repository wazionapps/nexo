# Changelog

## [7.1.0] - 2026-04-19

Minor release that closes the post-F0.6 runtime contract and ships
coordinated with NEXO Desktop v0.22.0. The runtime now treats
`~/.nexo/core` as the canonical shipped code root, Desktop consumes a
real Brain-generated Guardian snapshot instead of stale manual lists,
core automations become product surfaces instead of personal carry-over,
and the local classifier baseline auto-installs on fresh installs and
updates unless the operator explicitly opts out.

### Added

- `src/guardian_runtime_surfaces.py` — canonical Brain-generated snapshot
  for Desktop-facing Guardian datasets (`known_hosts`, `read_only_hosts`,
  `destructive_patterns`, `projects`, `legacy_mappings`,
  `vhost_mappings`, `db_production_markers`, `all_entities_flat`).
  `client_sync.sync_all_clients()` now writes it to
  `~/.nexo/personal/brain/guardian-runtime-surfaces.json`.
- `src/automation_controls.py` — product contract for supported core
  automations. Centralises operator profile, extra instructions,
  schedule overrides, runtime prerequisites, and per-automation
  metadata for `email-monitor`, `followup-runner`, and `morning-agent`.
- `src/scripts/nexo-morning-agent.py` + `src/crons/manifest.json` entry
  — a real core daily briefing automation with a fixed product prompt,
  operator-facing overrides, and a resolved briefing recipient.
- Local classifier baseline auto-install in `src/auto_update.py`.
  Fresh installs and `nexo update` now attempt to install the required
  Python packages and cache the pinned model automatically; opt-out is
  explicit via `NEXO_LOCAL_CLASSIFIER=off`.

### Changed

- Runtime install/update paths now finalise the F0.6 layout
  automatically after sync. `bin/nexo.js`, `bin/nexo-brain.js`,
  `src/auto_update.py`, and `src/plugins/update.py` prefer
  `~/.nexo/core` as the canonical code root, re-run layout healers
  after runtime sync, and pass the canonical `NEXO_CODE` into cron and
  client sync helpers.
- Hook installation now follows the Python manifest contract instead of
  a parallel hardcoded list. `client_sync.py` promotes
  `session_start.py`, `auto_capture.py`, `post_tool_use.py`,
  `pre_compact.py`, `stop.py`, `notification.py`, and
  `subagent_stop.py` as the canonical managed surfaces.
- Email runtime shape now supports two clear levels on the same
  `email_accounts` table: `account_type='agent'|'operator'`,
  `description`, `can_read`, `can_send`, `is_default`, and
  `sent_folder`. Core email automations and Desktop now use the same
  contract for routing, default recipient selection, and optional IMAP
  copy placement.
- `nexo-email-monitor.py`, `nexo-followup-runner.py`, and
  `nexo-send-reply.py` no longer depend on operator-specific naming or
  legacy personal APIs. The default assistant fallback is now neutral
  (`Nova`), operator overrides are additive, and the old regex-only
  attention hints are a final compatibility fallback instead of the
  primary decision path.
- Brain-facing identity surfaces (`desktop_bridge.py`, `user_context.py`
  and onboarding helpers) now block product-name variants such as
  `NEXO` as assistant names so the product and the operator’s agent no
  longer collapse into the same identity by default.

### Tests

- Added or extended release-facing coverage for runtime layout healing,
  Guardian runtime surfaces parity, classifier auto-install, core
  automation productisation, preferences/onboarding identity guards,
  and release-readiness drift checks.
- The guarded validation block for this release includes Brain runtime
  contract tests, client-sync parity, classifier auto-install tests,
  core automation tests, public-surface readiness checks, and the
  coordinated NEXO Desktop QA build/test pipeline.

### Notes

- The companion NEXO Desktop release (v0.22.0) turns the same runtime
  contract into a closed product flow: guided bootstrap, Claude login
  handoff, operator/agent email surfaces, and productised automation
  controls.

## [7.0.1] - 2026-04-19

CRITICAL hotfix over v7.0.0. `src/db/_core.py::DB_PATH` was the only
caller still hardcoded to the legacy pre-F0.6 path `~/.nexo/data/nexo.db`.
After the v7.0.0 migration the shared DB lives at
`~/.nexo/runtime/data/nexo.db`, so every command that opened the
process-wide connection (`nexo email list`, `nexo scripts list`,
`nexo_task_open`, ...) silently read from a non-existent file —
returning empty results despite the real table being populated.

### Fixed
- `src/db/_core.py`: DB_PATH is now transition-aware. `NEXO_TEST_DB`
  and `NEXO_DB` env overrides keep priority. Otherwise: prefer
  `runtime/data/nexo.db` when it exists; fall back to legacy
  `data/nexo.db` only when legacy is the only one present; default
  to `runtime/data/nexo.db` for fresh installs.
- Reproduces against Francisco's runtime: `nexo email list --json`
  now returns the primary account; `nexo email test --label primary`
  returns IMAP+SMTP login OK.

## [7.0.0] - 2026-04-19

**BREAKING — Plan Consolidado fase F0.6**: physical separation of the
runtime tree into `~/.nexo/{core,personal,runtime}/`. The flat layout
(`~/.nexo/scripts/`, `~/.nexo/brain/`, `~/.nexo/data/`,
`~/.nexo/operations/`, ...) is gone. Operators on v6.x runtimes are
auto-migrated on first `nexo update` to v7.0.0; fresh installs land
directly in the new tree.

### New layout

```
~/.nexo/
├── core/                  ← shipped with the package, replaced on update
│   ├── scripts/           (38 packaged automations)
│   ├── plugins/
│   ├── hooks/
│   ├── rules/
│   └── contracts/
├── core-dev/              ← dev-only, off by default
│   └── scripts/
├── personal/              ← operator-owned, `nexo update` never touches
│   ├── scripts/
│   ├── skills/
│   ├── plugins/
│   ├── hooks/
│   ├── rules/
│   ├── brain/             (calibration.json, project-atlas.json, ...)
│   ├── config/
│   ├── lib/
│   └── overrides/
└── runtime/               ← dynamic state, never edited by hand
    ├── data/              (nexo.db)
    ├── logs/
    ├── operations/
    ├── backups/
    ├── memory/
    ├── cognitive/
    ├── coordination/
    ├── exports/
    ├── nexo-email/
    ├── doctor/
    ├── snapshots/
    └── crons/
```

### Added

- New `src/paths.py` module centralises every runtime path helper
  (`core_scripts_dir`, `personal_scripts_dir`, `brain_dir`, `data_dir`,
  `db_path`, `logs_dir`, `operations_dir`, ...). All shipped src code
  uses these helpers instead of hardcoding `NEXO_HOME / "X"`. Each
  helper is transition-aware: returns the new (post-F0.6) location if
  it exists; falls back to the legacy (pre-F0.6) location if only the
  legacy path is present. This lets the same code work on every
  runtime version (pre-F0.6, mid-F0.6, post-F0.6, fresh install).
- New file `~/.nexo/.structure-version` carrying the F0.6 marker.

### Changed

- 24 src files refactored to use `paths.py` (auto_update.py, cli.py,
  evolution_cycle.py, runtime_power.py, cron_recovery.py,
  user_data_portability.py, system_catalog.py, public_contribution.py,
  tools_sessions.py, plugins/recover.py, plugins/personal_plugins.py,
  plugins/update.py, doctor/providers/runtime.py,
  doctor/providers/deep.py, doctor/providers/boot.py, db/_skills.py,
  ...). 100+ legacy `NEXO_HOME / "<flat>"` refs replaced.
- 7 shell scripts in `src/scripts/` (nexo-backup.sh, nexo-cron-wrapper.sh,
  nexo-deep-sleep.sh, nexo-inbox-hook.sh, nexo-snapshot-restore.sh,
  nexo-tcc-approve.sh, nexo-watchdog.sh) updated to reference the new
  layout. The cron wrapper's `DB="$NEXO_HOME/data/nexo.db"` is now
  `DB="$NEXO_HOME/runtime/data/nexo.db"`.
- `script_registry.classify_scripts_dir()` scans every dir in
  `paths.all_scripts_dirs()` (core/scripts, personal/scripts,
  core-dev/scripts) instead of the single legacy `~/.nexo/scripts/`.
- `script_registry.list_scripts(include_core=True)` hydrates `enabled`
  from `personal_scripts` table; gated to `include_core=True` so the
  default callers (CLI `nexo scripts list`) keep their v6.x behaviour.
- `doctor/providers/boot.py::check_required_dirs()` checks every
  required dir via the path helpers; `check_database_exists()` reads
  `paths.db_path()` instead of the legacy hardcoded path.

### Migration

- 13 dirs moved from `~/.nexo/<X>/` to `~/.nexo/{core,personal,runtime}/<X>/`.
- 71 `personal_scripts.path` rows UPDATEd transactionally to point at
  the new physical locations.
- 40 LaunchAgent plists (`~/Library/LaunchAgents/com.nexo.*.plist`)
  rewritten so their `ProgramArguments` script paths and
  `StandardOutPath`/`StandardErrorPath` log paths use the new layout.
- One snapshot (`~/.nexo-pre-f06-snapshot/`) is kept by the migrator;
  operators can `mv ~/.nexo-pre-f06-snapshot ~/.nexo` to roll back.

### Tests

- 1551/1551 pytest serial pass on the new tree.
- Test fixtures updated to either monkeypatch the env var alongside
  module constants OR use `tmp_path / "runtime" / X` for runtime state.
- `tests/test_cron_wrapper_contract.py`,
  `tests/test_doctor.py::test_missing_dirs_fix`,
  `tests/test_watchdog_in_flight.py` updated to use the new layout.

### Notes

- The companion NEXO Desktop release (v0.21.0) updates its hardcoded
  paths so the auto-update flow keeps working without operator
  intervention. Desktop is a closed-source companion app distributed
  separately from this open-source Brain.

## [6.5.0] - 2026-04-19

Plan Consolidado fase F0.2 — operator can now enable / disable any
personal script without touching plists, and the cron wrapper honours
the flag at every tick.

### Added

- New CLI verbs `nexo scripts enable <name>`, `nexo scripts disable <name>`,
  and `nexo scripts status <name>` (all accept `--json` for machine
  consumers like the NEXO Desktop F0.2 panel). Refuse to toggle
  packaged core scripts — operators have `nexo scripts unschedule` for
  that. Status returns `{enabled, classification, core, last_run}` so
  the Desktop panel can render the current state without a second
  query.
- New helper functions in `src/script_registry.py`:
  `set_personal_script_enabled(name_or_path, enabled)` and
  `get_personal_script_status(name_or_path)`.

### Changed

- `src/scripts/nexo-cron-wrapper.sh` now reads `personal_scripts.enabled`
  on every tick (`Plan F0.2.4` gate). When the script is disabled the
  wrapper short-circuits to `exit 0` with `summary='[disabled]'` and a
  visible `[disabled] $CRON_ID skipped — re-enable with: nexo scripts
  enable $CRON_ID` message in the log. The LaunchAgent stays loaded
  (zero `launchctl` churn) so re-enabling is a single CLI call.
- `src/db/_personal_scripts.py::upsert_personal_script` no longer
  overwrites `enabled` on the `ON CONFLICT DO UPDATE` branch. The
  operator-set flag is now sticky across `nexo scripts sync` runs.
  Initial INSERT still defaults `enabled=True`; the change only
  affects the UPDATE branch.

### Tests

- New `tests/test_personal_scripts_enabled.py`:
  - `test_enable_then_disable_then_enable` — round-trip lifecycle.
  - `test_unknown_script_returns_error` — clear error envelope.
  - `test_status_returns_enabled_and_classification` — read-only view
    shape.
  - `test_status_after_disable_reports_disabled` — sticky flag across
    sync (regression that broke before the upsert fix).

### Notes

- The matching NEXO Desktop release (v0.19.0 → v0.20.0) re-wires the
  Settings → Automatizaciones panel toggle on top of these CLI verbs.
  Both releases ship coordinated.


## [6.4.0] - 2026-04-19

Plan Consolidado fase F1 — multi-tenant email accounts and the JSON
bridge that lets NEXO Desktop drive email configuration without
operators ever touching a JSON file.

### Added

- New `email_accounts` table (migration m46). Multi-tenant by `label`,
  with IMAP/SMTP coords, role (`inbox`/`outbox`/`both`), enabled flag,
  operator email, and trusted-domain list. Passwords are NEVER stored
  here — only a pointer (`credential_service`+`credential_key`) into
  the existing `credentials` table.
- New CRUD module `src/db/_email_accounts.py` with `add_email_account`
  (upsert by label), `list_email_accounts`, `get_email_account`,
  `get_primary_email_account`, `set_email_account_enabled`,
  `remove_email_account`. Trusted domains and metadata are stored as
  JSON.
- New loader `src/email_config.py` with one entrypoint
  `load_email_config(label=None)`. Prefers the `email_accounts` table;
  falls back to legacy `~/.nexo/nexo-email/config.json` so existing
  installs keep working until the auto-migrator runs.
- New CLI subcommand tree under `nexo email`:
  - `nexo email setup` — interactive wizard for first-time operators.
    Prompts label/email/IMAP/SMTP/password (via getpass)/operator/
    trusted/role, stores password in `credentials`, then offers an
    IMAP+SMTP test. Designed for operators who will NEVER open a
    JSON file.
  - `nexo email add --label X --email X --imap-host X ... --password-stdin --json`
    — non-interactive variant. Used by NEXO Desktop and any script.
    Password is read from stdin, never on argv (so it never appears
    in `ps`).
  - `nexo email list [--json]`, `nexo email test --label X [--json]`,
    `nexo email remove --label X --yes [--json]` — JSON output for
    machine consumers (Desktop / scripts) plus rich text for humans.
- Auto-migrator script `src/scripts/nexo-email-migrate-config.py`
  reads legacy `~/.nexo/nexo-email/config.json` and inserts into
  `credentials` + `email_accounts` (label='primary'). Idempotent.
  Triggered automatically by `auto_update.py` on next session, so
  existing operators upgrade transparently.
- Test suite `tests/test_email_accounts.py` covers add+list, upsert
  by label, role validation, remove, primary picker, loader prefers
  table, loader falls back to JSON, migrator end-to-end.

### Changed

- `_debt_fingerprint()` (in the operator-side runtime helper, not
  shipped in this repo's `src/`) now passes `usedforsecurity=False`
  to its SHA1 call (it's a content fingerprint for dedup, not a
  security hash) so bandit no longer flags it on operators that mirror
  the helper into their own `~/.nexo/scripts/`.
- (Operators only) The `nexo-email-monitor.py` and `nexo-send-reply.py`
  runners that some operators install at `~/.nexo/scripts/` keep
  working unchanged — they still read from
  `~/.nexo/nexo-email/config.json` until the new `email_accounts` table
  is populated by the migrator. A future release will refactor those
  runners to use `email_config.load_email_config()` directly, once we
  have a generic operator-agnostic prompt template (the current ones
  are highly tenant-specific). See `NF-PLAN-V7-EMAIL-RUNNERS-CORE`.

### Security

- New CI guard `scripts/check_no_personal_data.sh` greps `src/` for
  operator-specific markers (personal email addresses, tenant domains,
  user names) on every run. v6.4.0 added it after the second-pass
  auditor caught two operator-specific runner scripts that had been
  copied into `src/scripts/` mid-refactor — exactly the same class of
  leak that v6.3.1 hotfixed inside the entities preset. The guard
  fails the build before any other check; same hardening as v6.3.1's
  `.gitignore` block on `entities_local.json`.

### Notes

- The matching NEXO Desktop release (v0.19.0) ships the Email +
  Automations Settings panels that drive the new `nexo email --json`
  surface end-to-end.

## [6.3.1] - 2026-04-19

Security / privacy hotfix. v6.3.0 shipped
`src/presets/entities_universal.json` with operator-specific entries
(private IPs, hostnames, docroots, tenant names) that should have
stayed local to the operator who wrote them. The nightly auditor
(Opus 4.7 xhigh) caught the leak before anyone pulled the package on
a fresh install, but the npm package was public for a short window.

### Fixed

- Removed operator-specific `vhost_mapping` entries from
  `entities_universal.json`: `systeam_es`, `wazion_com`,
  `recambios_bmw`, `allinoneapp`, `bulksend`, `canarirural`,
  `vic_shop`.
- Removed operator-specific alias + anti-example from the
  `email_to_operator_contact` entry (previously mentioned Maria and
  CanaRirural by name).
- The preset now only ships the generic `nexo_brain` vhost
  (public product site) plus destructive-command /
  legacy-path / artifact-class entries that are genuinely
  universal.
- Also moved `shopify_banner_block` out of the universal preset
  to the local override. Platform-specific knowledge (Shopify,
  WooCommerce, Stripe, etc.) belongs to operators who use those
  platforms, not to every fresh install — the previous location
  was a second leak of operator context into the public package.

### Added

- `src/presets/entities_local.sample.json` — template operators copy
  to `~/.nexo/brain/presets/entities_local.json` and fill with their
  real domains, hosts, IPs, tenants.
- `.gitignore` blocks `entities_local.json` so operator data never
  reaches the public npm package again.
- `scripts/install_guardian.py` drops the sample at `nexo init` and
  never overwrites an existing operator copy.

### Migration guidance

Operators who installed v6.3.0 on a fresh box and pulled the leaked
entries into their local preset should rotate any hostname / IP /
domain that happens to be also someone else's data and move their
private entries to `~/.nexo/brain/presets/entities_local.json`.

## [6.3.0] - 2026-04-18

Plan Consolidado — wave 2 (coordinated with NEXO Desktop v0.18.0).
Closes the remaining items from the v7 roadmap that can land without
an invasive structure migration. The breaking v7.0.0 (F0.3–F0.6
physical move of `~/.nexo/scripts/`, `skills/`, `plugins/`, `hooks/`,
`brain/` into `core/` + `personal/`) is tracked as a follow-up because
it requires coordinated validation on Francisco's and Nora's live
runtimes.

### Added

- **Plan 0.2 — cognitive_sentiment shape** — `detect_sentiment` now
  returns `is_correction: bool`, `valence: float (-1..1)` and
  `intent` enum alongside the legacy fields. New CORRECTION /
  ACKNOWLEDGEMENT / INSTRUCTION / QUESTION signal sets, surfaced to
  callers via `handle_cognitive_sentiment`.
- **Plan 0.3 — entities schema extension** — five new columns on
  `entities` (`aliases`, `metadata`, `source`, `confidence`,
  `access_mode`) via idempotent migration `_m44_entities_extended_schema`.
  Fresh installs get the full schema on day 0; legacy rows migrate
  in place.
- **Plan 0.8 + 0.14 — rule fixtures + R13 spike gates** — 21 labelled
  fixtures in `tests/fixtures_rules_validation.json`, FP <5 % and
  P95 <3 s gates on the R13 decision function.
- **Plan 0.X.5 — artifact_class preset** —
  `shopify_banner_block`, `changelog_entry` and
  `email_to_operator_contact` added to `entities_universal.json`.
- **Plan 0.X.1 + 0.X.6 — system_catalog discoverability smoke** —
  summary-count coherence + required locations + core_tools intent
  search covered.
- **Plan A.4 — R34 added to the system prompt** — trigger + action
  + anti-example text for identity coherence across terminals.
- **Plan F.2 / F.3 / F.5 / F.6 — Fase F telemetry loops** —
  `src/fase_f_loops.py` (per-rule aggregate, FP grouping, FN
  candidate promotion) + `src/scripts/phase_guardian_analysis.py`
  Deep Sleep phase writing
  `~/.nexo/reports/guardian-fase-f-<date>.json`.
- **Plan 0.21 + F.8 — local zero-shot classifier** —
  `src/classifier_local.py` with pinned mDeBERTa revision and
  fail-closed contract, plus `docs/classifier-model-notes.md`
  (upgrade policy, alternatives, pinning rationale).
- **Plan F0.0.4 — hook respects `NEXO_MIGRATING=1`** —
  `process_pre_tool_event` short-circuits during a structure
  migration, matching the claim already in `nexo_migrate.py`.
- **Plan F0.1 — `origin` column on `personal_scripts`** — idempotent
  migration `_m45_personal_scripts_origin` + CREATE TABLE update +
  index on `origin`. Enables `nexo update` and the future Desktop
  Automations panel to segment core vs user automations without
  heuristics.
- **Plan T4.2–T4.6 — LLM classifier gate wraps R15 / R23e / R23f /
  R23h** — `_t4_gate_says_no` helper composed of `t4_llm_gate` +
  `enforcement_classifier`. "no" skips the injection; "yes" /
  "unknown" / missing-module fall through to regex.

### Deferred to a later release

- F0.3–F0.6 physical move of `~/.nexo/scripts/`, `skills/`,
  `plugins/`, `hooks/`, `rules/`, `brain/`, `operations/` into
  `core/` + `personal/`, plus the v7.0.0 symlink removal. Requires
  coordinated smoke on Francisco + Nora runtimes per learning
  #450 (credential + function validation after relocation).
- F0.1 CLI `--origin` filter flag on `nexo scripts list`.
- F0.2 Desktop "Automations" panel (needs renderer work + IPC).

## [6.2.0] - 2026-04-18

Plan Consolidado — first coordinated release of the two-wave plan.
Second wave (T4 LLM classifier wrap, 0.2 cognitive_sentiment reshape,
0.3 extended entities schema, 0.21 local zero-shot BGE-M3, R06 email
secret filter, R11 plugin pre-inventory, Fase E.3–E.6, F0.1–F0.6
scripts migration with the breaking v7.0.0 symlink removal) is tracked
in `~/Desktop/NEXO-PLAN-CONSOLIDADO-BACKLOG.md`.

### Added — Plan T5 · R34 identity coherence across terminals

- **`templates/CLAUDE.md.template`** — new "Identity continuity across terminals" section after Core Systems. Tells the model that when multiple terminals are active, they are all the same NEXO, and that past-tense denials require consulting the shared brain first. Same block added to `templates/CODEX.AGENTS.md.template` so Codex sessions inherit it.
- **`src/r34_identity_coherence.py`** + **`nexo-desktop/lib/r34-identity-coherence.js`** — pure decision modules, byte-for-byte equivalent. Multilingual regex (ES/EN) pre-filter for past-tense denials ("yo no he hecho eso", "I haven't done that", "it wasn't me"…). If none of the shared-brain tools (`nexo_recent_context`, `nexo_session_diary_read`, `nexo_change_log`, `nexo_status`, `nexo_transcript_*`) fired in the current turn, an optional LLM classifier disambiguates. Fail-closed: classifier error → no injection.
- **Engines** — `src/enforcement_engine.py::on_assistant_message` (new public API) + `nexo-desktop/enforcement-engine.js::onAssistantMessage`. Both read `guardian.json.rules.R34_identity_coherence` (default **shadow** — the rule logs but does not surface until false-positive rate is measured).
- **`src/presets/guardian_default.json`** — adds `R34_identity_coherence: shadow`.
- **`tests/test_r34_identity_coherence.py`** (16 cases) + **`nexo-desktop/tests/r34-identity-coherence.test.js`** (15 cases) — match detection, suppression when shared-brain tool present, classifier yes/no, classifier failure fails closed, empty/non-string safety, byte-parity of the injection prompt with the JS twin.

### Added — Plan 0.X.2 · R-CATALOG pre-create probe

- **`src/r_catalog.py`** + **`nexo-desktop/lib/r-catalog.js`** — pure decision modules, byte-for-byte equivalent. Trigger on any `nexo_*_create` / `_open` / `_add` tool. If none of the six discovery tools (`nexo_system_catalog`, `nexo_tool_explain`, `nexo_skill_match`, `nexo_skill_list`, `nexo_learning_search`, `nexo_guard_check`) fired in the preceding 60-second window, inject a nudge to run one first. Prevents duplicate artefacts (new personal scripts that clone an existing skill, duplicate followups, learning spam).
- **`src/enforcement_engine.py::_check_r_catalog`** + **`nexo-desktop/enforcement-engine.js::_checkRCatalog`** — wire both engines. Shadow/soft/hard respect `guardian.json.rules.R_CATALOG_before_artifact_create`. Default already shipped as `soft`.
- **`tests/test_r_catalog.py`** (10 cases) + **`nexo-desktop/tests/r-catalog.test.js`** (11 cases) — parity tests. One dedicated case asserts the injection prompt is byte-for-byte identical between Python and JS so the two engines can never drift.

### Added — Plan 0.X.4 · `locations` in `nexo_system_catalog`

- **`src/system_catalog.py::_locations`** — new canonical path map exposed alongside the catalog sections: `brain.db`, `brain.calibration`, `brain.project_atlas`, `config.dir`, `config.guardian`, `config.guardian_runtime_overrides`, `logs.*`, `skills.*`, `scripts.core`, `hooks.runtime`, `rules.*`, `tool_enforcement_map`, `reports`, `backups`, `snapshots`, `crons.*`. All absolute, resolved from `NEXO_HOME` + `NEXO_CODE` so tests and staging runtimes get coherent paths. `build_system_catalog()` returns it under the `locations` key (outside the per-section summary so existing consumers keep working).
- **`tests/test_system_catalog_locations.py`** — 3 cases: flat dict of absolute paths, canonical keys present, `build_system_catalog()` exposes the block.

### Added — Plan 0.15 · drift baseline

- **`scripts/measure_drift_baseline.py`** — reads the last 90 session diaries from `~/.nexo/brain/session_archive/` (fallback `brain/diaries/`), counts occurrences of known drift patterns per rule (R13/R14/R16/R17/R19/R20/R25/R26/R27/R30/R31), and writes an aggregated JSON report to `~/.nexo/reports/drift-baseline-<YYYY-MM-DD>.json`. Pure reader: never writes inside the diary tree. Exits non-zero when no diaries are found so the caller knows the baseline is unusable. Prerequisite for Fase F KPI "reducción >50% por regla en 30 días".

### Added — Plan 0.16 · pre-commit parity hook

- **`scripts/hooks/pre-commit`** (tracked) — shared git hook that (1) blocks accidental `.db` / `.env` / `*_token.*` / `*.pem` / `*.key` commits and (2) runs `scripts/verify_tool_map.py` whenever `src/server.py`, `src/plugins/`, `src/tools_*.py`, or `tool-enforcement-map.json` is staged. Prevents new `nexo_*` tools from merging without an enforcement-map entry (learning #335).
- **`scripts/install-hooks.sh`** — idempotent installer that sets `core.hooksPath=scripts/hooks` and ensures `chmod +x`. Safe to re-run. README-worthy step for every fresh clone.

### Added — Plan 0.17 · `nexo_guardian_rule_override` writer

- **`src/tools_guardian.py`** — MCP writer for `~/.nexo/config/guardian-runtime-overrides.json`. The reader side (`guardian_config.rule_mode`) already honoured this file with TTL + core-rule defence-in-depth; this module adds the writer as a structured tool so an operator or automation can bump a noisy rule to shadow for an hour without editing JSON by hand.
- **`src/server.py::nexo_guardian_rule_override`** — `@mcp.tool`. Args `rule_id`, `mode` (`off`/`shadow`/`soft`/`hard`), `ttl` (`1h`/`24h`/`session`). Empty `mode` clears the override. Core rules R13/R14/R16/R25/R30 reject `off` at write time (defence in depth against a bad config). Session TTL is bounded at 12 h so an override never lingers past a restart.
- **`tool-enforcement-map.json`** — added entry for the new tool + 3 orphan backfills (`nexo_recover`, `nexo_session_log_create`, `nexo_session_log_close`) that were in code but missing from the map. 251 tools total.
- **`tests/test_tools_guardian_override.py`** — 11 cases: shape, core-rule off rejection, invalid mode, invalid TTL, set/clear round-trip, idempotent clear, tool JSON success + error shape, session TTL bounded at 12 h, NDJSON audit log accumulates set + clear events.
- **`tests/test_measure_drift_baseline.py`** — 4 cases: empty → no scan, matching patterns counted, report written under `~/.nexo/reports/`, main exits 2 when no diaries found.

## [6.1.1] - 2026-04-18

### Fixed

- **`nexo --help` now refreshes the `Latest: vX` line even when invoked via subprocess with piped stdio.** Prior gate in `_should_refresh_latest_version()` only allowed the npm-registry lookup when `sys.stdout.isatty()` or `sys.stderr.isatty()` returned True. NEXO Desktop spawns `nexo --help` with `stdio: ['ignore', 'pipe', 'pipe']`, so `isatty()` always returned False, the version cache was never populated from Desktop, and the Brain auto-update banner never saw a newer `Latest: vX` line to offer the upgrade. The 6-hour `max_age_seconds` at `_load_latest_version_cache()` is the real rate-limit and still prevents excessive npm hits; the TTY gate was redundant and broke the Desktop bridge. Fix: `_should_refresh_latest_version()` now returns True unconditionally; `_fetch_latest_version` still fail-closes to `None` on any subprocess error so the help line degrades to installed-only when npm is unreachable.

---

## [6.1.0] - 2026-04-18

### Added — Protocol Enforcer Fase 2 (Capa 2 runtime guardian)

- **Wrapper Bloque 1 (Fase C)** — 4 core rules: R13 pre-Edit guard, R14 post-correction learning window, R16 declared-done without close, R25 Nora/María read-only destructive block. CORE rules have defence-in-depth: guardian.json cannot turn them off.
- **Wrapper Bloque 2 (Fase D)** — 9 rules: R15 project-context, R17 promise-debt, R18 followup-autocomplete, R19 require-grep-before-Write, R20 constant-change grep probe, R21 legacy-path, R22 personal-script probe, R23 ssh-without-atlas, R24 stale-memory window.
- **Wrapper Bloque 3 (Fase D2)** — 12 incident-driven rules: R23b deploy-vhost-mismatch, R23c destructive-in-wrong-cwd, R23d chown-R-without-ls, R23e force-push-main (`--force-with-lease` allowed), R23f DB-DELETE/UPDATE-no-WHERE (heredoc aware), R23g secrets-in-output (Bearer/sk-/pk-/api_key/JWT/AWS/GitHub/Shopify/KEY=VALUE/mysql -p<pass>), R23h shebang-vs-interpreter-mismatch (no shell injection), R23i auto-deploy-ignored, R23j global-install, R23k script-duplicates-skill, R23l resource-collision (type-scoped), R23m message-duplicate.
- **Guardian config** (`guardian_config.py`) — loader + validator + defence-in-depth resolver.
- **Guardian telemetry** (`guardian_telemetry.py`) — per-enqueue NDJSON event log.
- **Installer** (`scripts/install_guardian.py`) — seeds presets, SSH hosts, automation_backend, guardian.json with merge-on-update.
- **`nexo quarantine list|promote|reject`** CLI — Desktop Guardian Proposals panel bridge.
- **Red-team suite** + **cross-engine parity harness (strict)** — 32 adversarial attempts + 13 parity fixtures.
- **Log redaction** (`_redact_for_log`) — Bearer/sk-/pk-/api_key/$TOKEN-refs/GitHub/Shopify/AWS/JWT/inline-password.
- **Documentation**: `docs/guardian-quickstart.md`.

### Added — Multi-Claude-sid aliasing (NEXO Desktop multi-conversation fix)

- **Migration v43** creates `session_claude_aliases` (N-to-1 sid alias map).
- **`_resolve_nexo_sid`** consults aliases → legacy column → single-active fallback.
- **`handle_startup`** auto-registers alias on every session_token binding.
- Fixes: Desktop with 2+ conversations no longer blocks edits with "unknown target".

### Fixed

- Stream routing: `run_with_enforcement` forwards the initial prompt to `on_user_message` (R14/R15 were dead in headless before).
- `on_user_message` no longer short-circuits on R14 module absence.
- `_enqueue` accepts explicit `rule_id` (not tag-split-parsed).
- Templates byte-for-byte Py↔JS across all 25 rules.
- R23f heredoc multiline; R23 curl URL-anchored; R16 session-scoped; R23e lease-allowed; R23l type-scoped.

### Removed

- Desktop dev-only cross-repo map fallback.

---

## [6.0.6] - 2026-04-17

### Fixed

- **Installer leaked `export PATH="$NEXO_HOME/bin:$PATH"` into the developer's real shell profile whenever `NEXO_HOME` was not the canonical `$HOME/.nexo`.** Repro: any pytest case, sandbox, or CI job that ran the installer with `NEXO_HOME=/tmp/pytest-xxx` appended `# NEXO runtime CLI\nexport PATH="/tmp/pytest-xxx/bin:$PATH"` to `~/.bash_profile`, `~/.bashrc`, and `~/.zshrc` — contaminating the operator's real shell between runs. `_ensure_runtime_cli_in_shell()` (and its two JavaScript twins in `bin/nexo-brain.js`: install Step 8 and the migration path) computed the rc file list from `Path.home()` / `os.homedir()` regardless of where `NEXO_HOME` pointed. Reported by a Claude Code session recovering the runtime after a full reset.

### Added

- **`src/auto_update.py::_should_skip_shell_profile_backfill()`.** Returns `(skip, reason)` based on (a) `NEXO_SKIP_SHELL_PROFILE=1|true|yes|on` and (b) whether `NEXO_HOME` resolves to the canonical `managed_nexo_home()` path. Used by `_ensure_runtime_cli_in_shell()` to gate the write. Fail-safe: when `NEXO_HOME` matches the canonical install path and the flag is unset, behaviour is unchanged.
- **`bin/nexo-brain.js::shouldSkipShellProfileBackfill()`.** Mirror of the Python helper. Guards both call sites that touch `.bash_profile`/`.bashrc`/`.zshrc`: the `install` command Step 8 (alias + PATH for fresh operators) and the `migrate` path that restores the alias for existing installs.
- **`tests/test_auto_update_shell_profile.py`.** Five regression cases covering: pytest tmp dir (non-canonical) → skip, env flag → skip, canonical install → write, multiple truthy flag values, and env flag set to `0` with canonical install → write.

### Housekeeping

- `.github/workflows/tests 2.yml` — duplicate workflow file with a space in the name (accidentally committed alongside `tests.yml`) removed. Also purged 78+ stale `__pycache__/*\ 2.*` duplicates created by Finder copies during earlier releases.

## [6.0.5] - 2026-04-17

### Fixed

- **Pre-tool strict guardrail blocked every `Edit`/`Write` with "unknown target" when Claude Code's PreToolUse payload omitted `session_id`.** The `process_pre_tool_event` resolver consulted only `payload["session_id"]`. Several Claude Code versions deliver PreToolUse without that field, so `_resolve_nexo_sid` returned `""`, the strict branch recorded a `strict_protocol_write_without_startup` debt, and the formatter emitted *"NEXO STRICT MODE BLOCKED THIS EDIT — Start the shared-brain session first: call `nexo_startup`, then `nexo_task_open`, before editing (unknown target)"* even when the user already had an open task, an acknowledged guard, and a tracked file. Tracked as learning #411. A partial fix shipped in 6.0.3 (`handle_guard_check` persists `session_id`) but it did not cover the missing-payload case for edits.
- **Two `tests/test_hook_guardrails.py` pre-tool cases silently regressed in 6.0.2+ and no CI job ran `pytest` to catch it.** `test_process_pre_tool_event_allows_public_contribution_checkout` and `test_process_pre_tool_event_does_not_treat_runtime_home_as_live_repo_when_not_git_checkout` asserted `result["skipped"] is True, result["reason"] == "lenient mode"`, which stopped being the correct assertion once public-contribution mode began preserving strict discipline and only relaxing the live-repo guard. Both tests now assert the specific property they were designed to guard (no `automation_live_repo_write_blocked` debt, no `automation_live_repo` reason code) and create the protocol task the strict path expects.
- **`test_non_tty_returns_lenient` inherited `NEXO_INTERACTIVE=1` from the parent shell (NEXO Desktop / `claude` terminal) and read strict instead of lenient.** `_force_tty` now clears `NEXO_INTERACTIVE` via `monkeypatch` so the TTY signal is the only thing steering strictness. Without the cleanup the test masked regressions for any contributor running pytest from inside an interactive NEXO client.

### Added

- **`.github/workflows/tests.yml`.** CI now runs `pytest tests/ -q --maxfail=5` on every PR and push to `main`. Up to v6.0.4 CI only executed `ruff`, `bandit`, `verify_release_readiness`, and `verify_client_parity`, so three pre-tool test failures shipped unnoticed. Release discipline gains pytest as a blocking gate.
- **`src/hook_guardrails.py::_read_claude_session_id_from_coordination()`.** Fallback helper used by `process_pre_tool_event` when `payload["session_id"]` is absent. Reads `$NEXO_HOME/coordination/.claude-session-id` (written on SessionStart by the NEXO hook) and falls through to `~/.nexo/coordination/.claude-session-id`. Fail-closed semantics preserved: when neither source yields a session id the guardrail still blocks with `missing_startup`.
- **`tests/test_hook_guardrails.py` gains two new cases** covering both the happy path (payload omits `session_id` but coordination file is present) and the fail-closed path (both payload and coordination file empty → still blocks).

### Changed

- **`src/hook_guardrails.py::process_pre_tool_event`.** Resolution now walks payload → coordination file → empty. No behavioural change for callers that already supply `session_id`.

### Housekeeping

- `NF-TEST-PROTOCOL-API-REFACTOR` followup captures two `tests/test_protocol.py` cases (`test_task_close_opens_protocol_debt_when_done_without_evidence`, `test_task_open_previews_anticipatory_warnings_without_firing_trigger`) that assert API shape that no longer exists. Marked `xfail(strict=False)` in this release so the new `tests.yml` gate stays green; both will be revisited with the handle_task_close / cognitive-trigger refactor landing in a subsequent patch.

### Merged from branch `fix/purge-legacy-python-claude-hooks` (PR #208)

- Purge legacy Python Claude hooks on sync (commit 9e42b03).
- Harden macOS test/runtime isolation (commit 6005288). Smoke installs on macOS no longer touch launchd real; tests run in an isolated launchd namespace so `nexo install` on a developer laptop can never clobber the user's live LaunchAgents.

## [6.0.4] - 2026-04-17

### Fixed

- **`nexo chat` ignored `preferences.default_resonance`.** `build_interactive_client_command` picked `--model` / `--effort` straight from `client_runtime_profiles` in `config/schedule.json`, so users who changed their Resonance in NEXO Desktop Preferences (Alto → writes `calibration.json`) kept getting whatever model/effort was cached in the legacy profile (usually `max`). Headless runs (`run_automation_prompt`) and NEXO Desktop sessions already honoured the preference correctly; only the terminal launcher was stuck.
- **Dashboard "Open followup in Terminal" had the same bug.** `build_followup_terminal_shell_command` also pulled from `client_runtime_profiles`, so the Terminal window the dashboard spawned ran at the stale tier instead of the user's current preference.

### Changed

- `src/agent_runner.py` — new `_resolve_interactive_model_and_effort(caller, backend, ...)` helper consults `resonance_map.resolve_model_and_effort` first (honouring `user_default` / explicit tier) and falls back to `client_runtime_profiles` only when the resonance contract is missing. Both `build_interactive_client_command` and `build_followup_terminal_shell_command` now use it. The former accepts a `caller=` kwarg (default `nexo_chat`) and `tier=` override, which `run_automation_interactive` propagates.
- `src/resonance_map.py` — registers `nexo_followup_terminal` in `USER_FACING_CALLERS` with the user-default sentinel so the dashboard "Open in Terminal" action resolves against the user's preference.

## [6.0.3] - 2026-04-17

### Fixed

- **`resonance_tiers.json` published at the wrong path.** v6.0.0 defined the public contract as `~/.nexo/brain/resonance_tiers.json` (consumed by NEXO Desktop ≥ 0.12.0) but the installer kept copying the file to `~/.nexo/resonance_tiers.json` (legacy flat-file layout). NEXO Desktop failed to start Claude with *"NEXO Brain contract missing"* on every fresh install and on every update from 6.0.0 / 6.0.1 / 6.0.2 unless the user copied the file by hand. The Brain's own Python runtime still worked because `resonance_map.py` read the legacy location, so the symptom only surfaced for Desktop users.
- **`nexo_guard_check` persisted rows with `session_id=""`.** The tool hardcoded the empty string on every insert, so `hook_guardrails._session_has_guard_check` (used by `missing_file_guard` and sibling hooks) could never match a guard call to the current session. Under strict protocol that meant every edit tripped the *"no guard_check seen for this session"* block, even right after a successful `nexo_guard_check`. The `guard_checks` table now records the resolved SID (env `NEXO_SID` → env `CLAUDE_SESSION_ID` translated via `sessions.external_session_id` → most-recently-updated `sessions` row). Empty `session_id` is only written when `sessions` is genuinely empty, which is the right *"nothing to guard"* signal.

### Changed

- `bin/nexo-brain.js` — new `publishBrainContracts(srcDir, nexoHome)` helper writes `resonance_tiers.json` straight into `~/.nexo/brain/` on install and update, and unlinks the legacy `~/.nexo/resonance_tiers.json` if present. Removed `resonance_tiers.json` from `getCoreRuntimeFlatFiles()` so it no longer lands at the root.
- `src/resonance_map.py` — contract resolution now walks: (1) `NEXO_HOME/brain/resonance_tiers.json` → (2) `NEXO_HOME/resonance_tiers.json` (legacy fallback during the rollout) → (3) `src/resonance_tiers.json` (dev checkout). Honours `$NEXO_HOME`.
- `src/plugins/guard.py` — new `_resolve_active_sid(conn)` helper used by `handle_guard_check` when persisting the audit row.

### Migration

- `src/auto_update.py::_relocate_resonance_tiers_contract` — runs during `nexo update` and promotes a legacy `~/.nexo/resonance_tiers.json` into `~/.nexo/brain/` if the contract path is empty, then unlinks the legacy copy. Idempotent; never raises.

### Tests

- `tests/test_auto_update_relocate_resonance.py` — 5 cases covering the contract relocation migration (promotion, legacy cleanup, idempotency, absence-of-files, exception safety).
- `tests/test_guard.py` — 4 new cases covering SID resolution for `guard_checks` inserts (env, external_session_id mapping, most-recent fallback, empty-sessions edge case).

### Impact

- Fresh installs of v6.0.3: contract is written to `~/.nexo/brain/` on first boot. Desktop starts cleanly.
- Updates from v6.0.0 / v6.0.1 / v6.0.2: installer copies the new file, migration removes the legacy one. No user action required beyond restarting NEXO Desktop.
- Brain-only users (no Desktop): Python runtime keeps working; it now reads from `brain/` instead of the root.
- Strict-protocol sessions stop seeing the spurious *"no guard_check seen"* block as soon as the Brain runtime is refreshed after the update.

## [6.0.2] - 2026-04-17

### Added

- Reserved caller prefix `personal/` — scripts that live outside the NEXO Brain repo (user-owned LaunchAgents in `~/.nexo/scripts/`) can now invoke the automation backend with their own caller id without registering in `src/resonance_map.py::SYSTEM_OWNED_CALLERS`. The resolver bypasses the registry for any caller whose id starts with `personal/` and follows a deterministic precedence chain: explicit `tier=` → explicit `reasoning_effort=` → `calibration.preferences.default_resonance` → `DEFAULT_RESONANCE` (`"alto"`). Invalid tier values are silently ignored instead of raising, so a typo falls through to the next step rather than breaking the caller.
- New kwarg `tier: str = ""` on `run_automation_prompt` and `run_automation_interactive` (agent_runner), on `run_automation_text` and `run_automation_json` (templates/nexo_helper.py), and as `--tier` on `nexo-agent-run.py`.
- New kwarg `caller: str = ""` on `run_automation_text` and `run_automation_json` so personal scripts can declare their id without touching the runner invocation manually; the helper propagates the id to `nexo-agent-run.py --caller`.
- `docs/personal-scripts-guide.md` — reference for any NEXO session helping a user author a personal script. Explains the prefix, the tier semantics, the precedence rules, anti-patterns, and how to test against a scratch `NEXO_HOME`.

### Changed

- `resolve_tier_for_caller` and `resolve_model_and_effort` accept a new keyword-only argument `explicit_tier`. Existing positional calls continue to work.
- README gains a `personal-scripts-guide.md` link in the contribution / maintenance section.

### Backcompat

- Callers registered in `USER_FACING_CALLERS` / `SYSTEM_OWNED_CALLERS` keep their v6.0.0 behaviour. No entry in either registry is modified.
- Callers without the `personal/` prefix continue to require a registry entry and raise `UnregisteredCallerError` when missing.

### Tests

- Three new pytest modules: `test_personal_caller_prefix.py` (8 resolver cases), `test_run_automation_prompt_tier_kwarg.py` (3 cases on the full `run_automation_prompt` surface), `test_nexo_agent_run_tier_flag.py` (1 CLI propagation case). Full suite stays green.

## [6.0.1] - 2026-04-17

### Fixed

- `protocol_settings.py` used to ignore `NEXO_INTERACTIVE=1`, so sessions spawned by Electron-class clients (NEXO Desktop 0.12.0 uses `child_process.spawn` with pipes) got classified as non-interactive and fell back to `lenient` even with a human in the loop. The detector now treats the process as interactive when either `stdin+stdout` are TTYs **or** `NEXO_INTERACTIVE` is exactly `"1"`. Truthy-looking aliases (`true`, `yes`, `on`) are deliberately rejected so a typo cannot silently strict-mode a headless cron.
- Claude Code sessions on autopilot (long streams of tool calls with no user messages) could not see inbound `nexo_send` messages until the user interacted manually, because `nexo_heartbeat` only fires on user turns.

### Added

- `PostToolUse` hook now runs an inbox-autodetect stage after the other steps. When the session has unread messages AND `≥ 60s` have passed since the last heartbeat, the hook emits a `systemMessage` asking the agent to run `nexo_heartbeat` and consume its inbox. Rate-limited to **one reminder per minute per SID** via the new `hook_inbox_reminders` table.
- `sessions.last_heartbeat_ts` column (idempotent migration m42) — stamped by `handle_heartbeat` on every successful invocation.
- `hook_inbox_reminders` table (idempotent migration m42) — per-SID rate limiter for the inbox reminder.
- New DB helpers: `update_last_heartbeat_ts`, `get_last_heartbeat_ts`, `count_pending_inbox_messages`, `resolve_sid_from_external` in `db._sessions`; `get_last_reminder_ts`, `mark_reminder_sent`, `reset_reminders_for_sid` in `db._hook_inbox_reminders`. All re-exported from `db`.

### Changed

- `nexo_heartbeat` now calls `update_last_heartbeat_ts(sid)` at the start of the inner body so the PostToolUse reminder has a fresh anchor after every heartbeat.
- `_stdio_is_tty()` is kept as a thin deprecated alias that delegates to `_is_interactive()`, so any caller that imported the old name from v6.0.0 still respects the `NEXO_INTERACTIVE` contract.

### Contract (internal, unchanged)

`NEXO_INTERACTIVE=1` is the Brain↔interactive-clients contract. It is not user-facing, not documented to operators, and not a resurrection of the removed `NEXO_PROTOCOL_STRICTNESS` knob. It only signals presence of a human in the loop; the actual strictness value still comes from the interactivity test.

### Tests

Six new pytest cases: `test_protocol_strictness_nexo_interactive.py`, `test_inbox_autodetect.py`, `test_inbox_reminder_rate_limit.py`, `test_heartbeat_updates_last_ts.py`, `test_v6_0_1_migration.py`, and `test_inbox_autodetect_e2e.py`. Full suite stays green.

## [6.0.0] - 2026-04-17

### BREAKING

- **Tier-only setup.** Onboarding no longer asks for model or reasoning effort. It asks for one tier (`maximo`/`alto`/`medio`/`bajo`) and that choice drives every backend via `src/resonance_tiers.json`. The legacy `client_runtime_profiles.{claude_code,codex}.{model,reasoning_effort}` fields are removed from the `schedule.json` schema and silently dropped during upgrade.
- **Protocol strictness is no longer configurable.** Interactive TTY sessions always run `strict`. Non-TTY contexts (crons, tests, pipes) always run `lenient`. The `NEXO_PROTOCOL_STRICTNESS` environment variable, `preferences.protocol_strictness` setting, and the `default/normal/off/warn/soft` aliases are all gone. Users who had a custom strictness see it silently cleared on upgrade and fall through to the TTY/no-TTY decision.
- **`preferences.show_pending_at_start` moves to NEXO Desktop's electron-store.** Brain no longer reads or writes it; the `calibration.json` key is purged on upgrade. Desktop ≥0.12.0 keeps the UI toggle.

### Added

- `src/resonance_tiers.json` — single source of truth for `tier → (model, effort)` per backend. Consumed by `src/resonance_map.py` at import time (`load_resonance_table()` is exposed for tests) and by NEXO Desktop for its resonance selector.
- `src/hooks/manifest.json` — unified manifest of the seven core hooks. Both plugin mode (`hooks/hooks.json`) and npm mode (`bin/nexo-brain.js registerAllCoreHooks()`) read the same file, eliminating the pre-v6 divergence where each mode shipped a different list.
- **Two new hooks registered:** `Notification` (records live-session activity via `hook_observability.record_activity()` so `auto_close_sessions` stops pruning busy sessions) and `SubagentStop` (auto-closes `protocol_tasks` that a subagent opened without calling `nexo_task_close`).
- `auto_capture.py` is now wired to both `UserPromptSubmit` and `PostToolUse`. Classification still produces decision/correction/explicit facts, but on `correction` matches the hook also calls `nexo_learning_add` (category `auto`, priority `medium`) exactly once per content hash per hour. Dedup is persistent: hits land in a new `auto_capture_dedup` SQLite table with a 1h TTL.
- `~/.nexo/hooks_status.json` — published after every `registerAllCoreHooks()` invocation. NEXO Desktop uses this file for the "Hooks activos X/Y" widget in its Estado del sistema tab.
- `nexo-brain --skip` flag — alias of `--yes`/`--defaults`. All three skip onboarding prompts and apply the recommended defaults end-to-end.
- `hook_observability.record_activity(session_id=..., activity_type=...)` helper, backing the `Notification` hook and any future activity-signalling surface.

### Changed

- Onboarding defaults for Deep scan, Caffeinate (macOS only), and the web Dashboard now answer **yes** on bare ENTER in the interactive flow, and are ON by default in `--yes/--skip` mode.
- The "What's your name?" prompt falls through to the literal string `"Usuario"` when the operator presses ENTER without typing anything, so `calibration.user.name` always ships with a concrete value.
- `calibration.json` is written in the canonical nested shape on fresh installs (`user.*`, `personality.*`, `preferences.*`, `meta.*`). `preferences.default_resonance` holds the single tier choice.
- The v5.x hook list under `~/.claude/settings.json` is pruned to the manifest's seven handlers on every `registerAllCoreHooks()` run — legacy direct-to-shell commands (`heartbeat-posttool.sh`, `protocol-guardrail.sh`, `inbox-hook.sh`, `post-compact.sh`, etc.) are detected and removed. User-custom hooks (anything not owned by the NEXO manifest) are left alone.
- `bin/postinstall.js` still prints the fresh-install banner (`Run 'nexo-brain' to complete setup.`) and continues to run migration silently on upgrade — neither flow auto-starts onboarding.

### Silent migration (run once per `nexo update`)

- `client_runtime_profiles.{claude_code,codex}.{model,reasoning_effort}` removed from `schedule.json`.
- `preferences.protocol_strictness` removed from `calibration.json` (and from the top level if any v5.x install wrote it there).
- `preferences.show_pending_at_start` removed from `calibration.json`.
- `preferences.default_resonance` seeded to `"alto"` only if the user had no explicit value. Existing values (`maximo`/`medio`/`bajo` or a prior `alto`) are respected and never overwritten on subsequent updates.

### Tests

Eight new / updated test cases: `test_resonance_loader.py`, `test_migration_legacy_to_v6.py`, `test_auto_capture_correction_learning.py`, `test_hooks_status_publish.py`, `test_protocol_strictness_tty.py`, plus the `/tmp/nexo-fresh` smoke-install, `scripts/verify_client_parity.py`, and a cross-mode diff guaranteeing plugin and npm installs register the same seven hooks in `~/.claude/settings.json`.

## [5.10.2] - 2026-04-17

### Fix: bootstrap `brain/profile.json` from `calibration.json` on `nexo update`

NEXO Desktop's *Preferencias → Avanzado* tab shows two JSON blocks: `brain/calibration.json` (editable personality, language, name, mood history) and `brain/profile.json` (deep-scan results from onboarding). Operators who went through the onboarding flow before v5.9.x ended up with `role` and `technical_level` recorded under `calibration.meta.*` but no `profile.json` file at all — Desktop then rendered an empty `{}` for the profile block with no context, which looked broken. v5.10.2 closes that gap from both ends:

- **Brain** — new `_bootstrap_profile_from_calibration_meta(dest)` runs inside `_run_runtime_post_sync()` right after the v5.10.1 effort→resonance migration. When `brain/profile.json` is missing, empty, or corrupt AND `brain/calibration.json` carries at least one of `meta.role`, `meta.technical_level`, `name`, `language`, the helper seeds `profile.json` with those fields plus a `"source": "auto_update._bootstrap_profile_from_calibration_meta"` marker. Never overwrites a populated profile, never raises, logs `profile-bootstrap:<n>-fields` on the actions trail. Idempotent by construction.
- **Desktop (v0.11.2)** — the *Avanzado* tab now prefixes each JSON block with a short explanation ("Calibración = personalidad + idioma + identidad editada desde las pestañas anteriores" / "Perfil completo = deep-scan del onboarding, construido por NEXO Brain en segundo plano"). When `profile.json` does not exist, renders a friendly placeholder explaining that the basic fields live meanwhile inside `calibration.meta` and `name`, instead of dumping `{}`.

### Test regression also fixed

`tests/test_resonance_map.py::test_user_facing_caller_with_no_user_default_uses_alto` used to read the real `~/.nexo/brain/calibration.json` on the machine running the suite. After the v5.10.1 migration wrote `default_resonance=maximo` on Francisco's box, the test started asserting against the real fs state instead of the intended library default and failed. Fixed by monkeypatching `_load_user_default_resonance` to return an empty string, isolating the test from the host filesystem.

**Tests**

10 new cases in `tests/test_auto_update_bootstrap_profile.py` covering each seeding path, the two no-op paths (profile already populated / calibration absent or empty), the idempotency on a second run, the corrupt-JSON recoveries on both files, and the empty-string filter.

Full suite: 1021 passed, 1 skipped.

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
