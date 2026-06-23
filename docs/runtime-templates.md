# Runtime Templates

This is the canonical index of reusable templates shipped with NEXO Brain.

Use it when scaffolding new personal artifacts, reviewing operator-generated files, or teaching the agent what the supported starting points are. If a new template becomes part of the supported workflow, add it here in the same change.

## Core Templates

| Template | Path | Use |
|---|---|---|
| Python script | `templates/script-template.py` | New personal automation or helper script |
| Shell script | `templates/script-template.sh` | Lightweight shell-based automation |
| Plugin | `templates/plugin-template.py` | New personal MCP/runtime plugin |
| Skill guide | `templates/skill-template.md` | New skill definition guide |
| Skill-backed script | `templates/skill-script-template.py` | Executable helper for a skill |
| Email draft | `templates/email-template.md` | Human-facing outbound/reply email drafts and reviewable automation emails |
| Claude bootstrap | `templates/CLAUDE.md.template` | Managed Claude Code bootstrap skeleton |
| Codex bootstrap | `templates/CODEX.AGENTS.md.template` | Managed Codex bootstrap skeleton |
| Helper module | `templates/nexo_helper.py` | Stable helper API for personal scripts |
| Real-data smoke test | `docs/templates/smoke-test-real-data.md` | Mandatory checklist before closing critical engine execute tasks using booking, payment, voice routing, or availability |

## Prompt Catalog Templates

These are product-controlled prompt bodies, not personal scaffolds:

| Template | Path |
|---|---|
| Email monitor | `templates/core-prompts/email-monitor.md` |
| Followup runner | `templates/core-prompts/followup-runner.md`, `followup-runner-operator-attention-*.md` |
| Morning agent | `templates/core-prompts/morning-agent.md` |
| Daily synthesis | `templates/core-prompts/daily-synthesis.md` |
| Postmortem consolidator | `templates/core-prompts/postmortem-consolidator.md` |
| Sleep | `templates/core-prompts/sleep.md` |
| Enforcement classifier (strict / retry) | `templates/core-prompts/enforcement-classifier-*.md` |
| Deep Sleep extract conversion fallback | `templates/core-prompts/deep-sleep-extract-json-conversion.md` |
| Rule prompts R14 / R15 / R16 / R17 / R18 / R19 / R20 / R21 / R22 / R23* / R24 / R25 | `templates/core-prompts/r14-*.md`, `r15-*.md`, `r16-*.md`, `r17-*.md`, `r18-*.md`, `r19-*.md`, `r20-*.md`, `r21-*.md`, `r22-*.md`, `r23*.md`, `r24-*.md`, `r25-*.md` |
| Rule prompt R13 pre-edit guard | `templates/core-prompts/r13-pre-edit-guard-injection.md` |
| T4 LLM gate prompts | `templates/core-prompts/t4-r15-*.md`, `t4-r23e-*.md`, `t4-r23f-*.md`, `t4-r23h-*.md` |
| R-CATALOG probe | `templates/core-prompts/r-catalog.md` |
| R34 identity coherence | `templates/core-prompts/r34-identity-coherence-*.md` |
| Interactive startup | `templates/core-prompts/interactive-startup.md` |
| Codex protocol contract | `templates/core-prompts/codex-protocol-contract.md` |
| MCP server instructions | `templates/core-prompts/server-mcp-instructions.md` |
| Post-tool inbox reminder | `templates/core-prompts/post-tool-inbox-reminder.md` |
| Watchdog L2 repair | `templates/core-prompts/watchdog-repair.md` |

## Rules

- Prefer these templates over ad-hoc copies.
- Keep personal artifacts under `NEXO_HOME`, not inside repo `src/`, unless the behavior should ship to all users.
- If a template no longer reflects the runtime contract, update the template and the consuming docs/tests together.
- Do not create parallel template trees under `personal/` that shadow the product contract without a specific reason.

## Desktop Release Gates

Any NEXO Desktop release that touches chat, renderer, or lifecycle behavior must run the installed-app live chat soak before tagging or publishing installers:

```bash
node scripts/live-chat-soak.js --app /Applications/NEXO\ Desktop.app/Contents/MacOS/NEXO\ Desktop
```

Required evidence:

- stdout contains `LIVE_SOAK_OK`
- `artifacts/report.json` has `turns.length >= 15`
- every `turn.result.ok` is `true`
- one successful turn label includes `sub-agent-task`
- `archiveRestore.ok` is `true`
- `05-after-restore.result.ok` is `true`
- screenshots exist for each send/final/archive/restore step

If the soak returns `LIVE_SOAK_FAIL`, the release is blocked until the failing UI state is fixed and the gate is rerun.

## Worked Starting Points

Use these as the canonical first move instead of cloning an old operator file.

### New personal automation script

Use:

- `templates/script-template.py`
- `templates/nexo_helper.py`

Then:

1. fill header metadata
2. implement the real job
3. run `nexo scripts doctor NAME`
4. run `nexo scripts reconcile`

### New script with only shell glue

Use:

- `templates/script-template.sh`

Then:

1. keep it thin
2. push real business logic into stable helpers when possible
3. declare schedule metadata only if the shell script itself is the canonical runner

### New personal plugin/tool

Use:

- `templates/plugin-template.py`

Then:

1. give tools explicit names
2. keep runtime side effects narrow
3. verify with `nexo_plugin_load(...)` + `nexo_plugin_list()`

### New reusable skill

Use:

- `templates/skill-template.md`
- `templates/skill-script-template.py` only if execution is really needed

Then:

1. define the procedure clearly in `guide.md`
2. only add executable code if text instructions are not enough
3. sync and dry-run before relying on the skill

### New reviewable email draft

Use:

- `templates/email-template.md`

Then:

1. keep the message human-facing
2. keep product/internal reasoning out of the final text
3. use it for operator-visible drafts and automation copy that should stay reviewable

### New managed bootstrap prompt/config

Use:

- `templates/CLAUDE.md.template`
- `templates/CODEX.AGENTS.md.template`

Rule:

- treat these as managed bootstrap surfaces, not as free-form scratchpads
- if the credential rule or source-of-truth order changes, update the template, not just one generated copy
- Claude and Codex bootstraps must both include `User-Facing Agent Contract` with equivalent identity, continuity, autonomy, and safety wording
- generated sync may rewrite `CORE`, but it must preserve the operator-managed `USER` block
- Desktop conversation bootstrap should carry a short equivalent reminder without duplicating the full technical protocol

## Navigating templates from `~/.nexo/personal/`

Templates live under `~/.nexo/core/templates/`. They are shipped with every
release, read-only from the operator's perspective (same rule as the rest
of `core/`: never edit in place). The personal filesystem (`~/.nexo/personal/`)
is where new artifacts go. Use this flow when the operator wants to start a
new personal script or skill from a supported starting point.

### Copying a template into a personal script

```
cp ~/.nexo/core/templates/script-template.py \
   ~/.nexo/personal/scripts/<logical-name>.py
```

Then edit the new file. The `# nexo: name=...` header is the one the
registry reads; match the filename (`<logical-name>.py`) so
`nexo scripts reconcile` keeps the metadata coherent.

`nexo scripts create <logical-name>` also materializes this template
automatically and calls `sync_personal_scripts` for you — prefer the CLI
when the operator is scaffolding interactively.

### Copying a skill template into a personal skill

```
mkdir -p ~/.nexo/personal/skills/sk-<slug>
cp ~/.nexo/core/templates/skill-template.md \
   ~/.nexo/personal/skills/sk-<slug>/guide.md
cp ~/.nexo/core/templates/skill-script-template.py \
   ~/.nexo/personal/skills/sk-<slug>/script.py   # only if execution is needed
```

Then create `skill.json` with `{id, name, description, level, mode, content,
trigger_patterns}`. Skills that are guide-only do not need `skill.json` to
declare `executable_entry` / `command_template` — skip those fields.

### Why NOT copy templates as `*.template` into personal/

A prior version of this runbook suggested mirroring the entire `templates/`
tree into `~/.nexo/personal/scripts/` with a `.template` suffix for
navigability. That approach creates a parallel source of truth: the
mirror falls out of sync on every release, operators edit the wrong copy,
and `sync_personal_scripts` has to ignore them by extension. The current
contract is simpler:

- Browse templates at `~/.nexo/core/templates/` (read-only; treat like any
  `core/` content).
- Create your personal artifact in `~/.nexo/personal/scripts/` or
  `~/.nexo/personal/skills/` by copying ONE template into the new
  artifact's real filename.
- Do not leave `.template` copies sitting next to real scripts — that
  noise confuses both the registry scan and the operator.

If a new starting point should be shipped to everyone, add it to
`templates/` in the repo and list it in this document, same as any other
contract change.

## Backup Retention Policy

NEXO manages its own disk usage. The product must not alert the user about
space consumed by NEXO technical backups: first it silently prunes NEXO-owned
backup artifacts, and only if free space remains below
`NEXO_BACKUP_MIN_FREE_BYTES` should it surface a user-visible low-disk warning
attributable to the user's own files.

### Environment Variables

| Variable | Default | Use |
|---|---:|---|
| `NEXO_BACKUP_MAX_BYTES` | `50G` | Configured upper bound for technical backups. |
| `NEXO_BACKUP_MIN_FREE_BYTES` | `5G` | Free-space floor before blocking or alerting. |
| `NEXO_BACKUP_TMP_TTL_MINUTES` | `30` | Minimum age before orphan temporary files are removed. |
| `NEXO_LOCAL_CONTEXT_BACKUP_KEEP_LAST` | `1` | `local-context-*.db` backups kept under the cap. |

The effective default cap is adaptive:
`min(NEXO_BACKUP_MAX_BYTES, 5% of total disk size)`, floored at `10G` and capped
at `50G`. Emergency prune steps may use lower caps (`10G`, `5G`, `0`) to
recover space.

Before any hourly or weekly `nexo-*.db` backup can be deleted by retention,
`scripts/nexo-backup.sh` must run Memory Fabric reconciliation against
`runtime/backups/`. Recoverable diary rows that are no longer present in the
active DB are copied into `historical_diary_index`, added to unified search as
`historical_diary`, and linked into the knowledge graph. Backups may rotate;
the semantic memory they contain must not be available only inside an expiring
snapshot.

### Universal Backup Wrapper

Every technical snapshot creator under `runtime/backups/` must use:

- `paths.create_backup_dir(prefix)` for directories.
- `paths.create_backup_path(prefix, suffix)` for files.
- `paths.finalize_backup_snapshot(path)` after writing the snapshot, or
  `with paths.create_backup_dir(prefix) as backup_dir:` so post-prune runs when
  the context exits.

Do not build direct paths with `paths.backups_dir() / f"prefix-..."`. If a new
technical prefix is added, register it in `TECHNICAL_PREFIXES` in
`scripts/prune_runtime_backups.py`.

### Escalating Self-Prune

When free space falls below the floor, `paths.backup_space_error()` and
`doctor.providers.boot.check_disk_space()` run:

1. normal prune with the adaptive cap;
2. prune with `--max-bytes 10G`;
3. prune with `--max-bytes 5G`;
4. emergency `--delete-all-technical`.

Emergency mode removes only technical classes (`pre-*`, `code-tree-*`,
`runtime-tree-*`, temporary files). It never touches `shopify-backups/`,
`weekly/`, hourly `nexo-*.db`, root DBs, or unknown entries. Each escalation
step records anonymous telemetry in
`runtime/operations/backup-retention-events.jsonl`.

### Cross-Platform Recovery Sweep

When a low-disk to OK-disk transition is detected,
`scripts/post_disk_recovery_sweep.py` runs. Core uses an extensible registry in
`disk_recovery/registry.py`; platform commands live in separate handlers:

- `disk_recovery/handlers/macos.py`: CalendarAgent, Calendar, Mail, iCloud
  Drive/CloudKit (`cloudd`, `bird`) when running.
- `disk_recovery/handlers/windows.py`: OneSyncSvc, classic Outlook through COM,
  and OneDrive.
- `disk_recovery/handlers/common.py`: Dropbox, Google Drive, and Slack when
  running, with platform-specific commands.

The sweep is silent and does not touch UI. It records touched apps and an
anonymous network-activity delta in
`runtime/operations/post-disk-recovery-sweep.jsonl`. To add apps, register a
new handler in the registry; do not hardcode macOS/Windows commands in shared
core.

## Memory And Runtime Contracts

### Session Diary

Startup continuity reads are client-neutral. Interactive diary sources such as
`claude`, `codex`, `desktop`, `nexo-chat`, or future clients are included by
default; automated sources such as cron, self-audit, watchdog, and minimal
auto-close diaries are filtered unless the caller asks for `include_automated`.
Reads by explicit `session_id` remain exact and unfiltered.

### Workflow And Goals

Goal and workflow runtime tools are first-class server tools, not only plugin
registrations. New durable-goal or replay/handoff behavior must be available
through the core MCP server surface so every client sees the same contract.

### Credentials And BYOK

Credential list/dashboard surfaces never expose values. DB credentials are
reported with backend `db`; Desktop-connected BYOK files under
`credentials/byok/` are reported as `byok_local`. Notes are operational text
only: if notes look like a token, API key, password, or bearer value, creation
and update are rejected; existing secret-like notes are redacted in public
metadata views.

### Email Monitor Contract

Email has two separate layers:

- account configuration in `nexo.db/email_accounts`;
- monitor history in `runtime/nexo-email/nexo-email.db` (`emails`,
  `email_events`).

Dashboard and diagnostics expose this split through `/api/email/contract` so
account setup is not confused with monitor activity.

### Change Log Retention

`change_log` cleanup is explicit and configurable with
`NEXO_CHANGE_LOG_RETENTION_DAYS` (default `90`). Cleanup also deletes matching
`unified_search` rows where `source='change'`; the dashboard exposes the active
policy at `/api/change-log/retention`.
