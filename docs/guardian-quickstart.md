# Guardian Quickstart (Phase 2 Protocol Enforcer)

Guardian is the runtime enforcement layer that turns any Claude client (Code, Codex, Desktop) into an agent that follows the NEXO protocol completely.

## What does Guardian do?

It watches each user message, each agent tool call, and each piece of agent text. When it detects:

- **Pre-Edit without guard_check** -> reminder to call `nexo_guard_check` first.
- **Operator corrections** -> requires capturing a learning in the next window.
- **Premature "done" declaration without evidence** -> reminder to call `nexo_task_close`.
- **Destructive commands on read-only hosts** -> blocks.
- **Force-push to main / master / release-*** -> blocks.
- **SQL DELETE/UPDATE without WHERE against production** -> blocks.
- **Another ~40 patterns** cataloged in `src/presets/guardian_default.json`.

## Installation

```bash
python3 scripts/install_guardian.py
```

This creates / updates:

- `~/.nexo/config/guardian.json` - per-rule modes (off/shadow/soft/hard).
- `~/.nexo/brain/presets/entities_universal.json` - entity baseline.
- `~/.nexo/brain/presets/guardian_default.json` - versioned defaults.
- `~/.nexo/brain/presets/ssh_imported_hosts.json` - hosts from `~/.ssh/config` imported as `access_mode=unknown`.
- `~/.nexo/config/schedule.json` - `automation_backend=claude_code` (if the operator does not have `automation_user_override=true`).

Flags:

- `--dry-run` - reports what it would do without touching anything.
- `--force` - overwrites guardian.json and presets (destructive to customizations).

## Per-rule modes

Each rule can be configured in 4 modes:

| Mode | Behavior |
|------|----------------|
| `off` | Rule disabled. Core rules (R13/R14/R16/R25/R30) reject this value automatically. |
| `shadow` | The rule evaluates and logs, but does not inject visible reminders. Useful for rollout. |
| `soft` | Injects a reminder to the agent; the agent may ignore it if context justifies it. |
| `hard` | Injects with high priority; effectively blocks the action without an explicit operator override. |

Edit `~/.nexo/config/guardian.json`:

```json
{
  "version": "1.3.3",
  "rules": {
    "R13_pre_edit_guard": "hard",
    "R25_nora_maria_read_only": "hard",
    "R21_legacy_path": "shadow",
    "R23h_shebang_mismatch": "off"
  }
}
```

Rules not listed in your file inherit the packaged default.

## Add your projects

Guardian uses brain entities (SQLite + preset) to know which projects you are talking about. For R15 (project_context), R19 (require_grep), or R23b (deploy vhost mismatch) to work with your projects:

```bash
# Via MCP (preferred):
nexo_entity_create type=project name=MyProject metadata='{"local_path":"/Users/me/work/myproject","aliases":["myproj"]}'

# For vhosts:
nexo_entity_create type=vhost_mapping name=myshop_com metadata='{"domain":"myshop.com","host":"myserver","docroot":"/var/www/myshop"}'
```

The preset already includes 8 vhost_mapping + 8 destructive_command + 3 legacy_path entries. Adding yours is incremental: Guardian grows with your reality.

## Add your SSH hosts

`install_guardian.py` automatically imports your hosts from `~/.ssh/config`. They remain as `access_mode=unknown`; if you want to mark one as read-only (Nora/Maria pattern):

```bash
nexo_entity_update name=maria_server type=host metadata='{"access_mode":"read_only","reason":"prod box tenant Maria"}'
```

R25 will block destructive commands against that host until the operator says `force OK` in the message.

## Telemetry

Each Guardian injection is logged in `~/.nexo/logs/guardian-telemetry.ndjson`. Data stays local and never leaves your machine unless you enable `telemetry_external_optin=true` in the future (Phase F).

## Troubleshooting

**"Guardian is not injecting anything"** - verify that `~/.nexo/config/guardian.json` exists. If not, run `python3 scripts/install_guardian.py`.

**"It is blocking a command I know I want to run"** - in the next message to the agent, explicitly say `force OK` or `si borra` (allowed synonyms are in `r25_nora_maria_read_only.py::PERMIT_MARKERS`).

**"I want to turn off one specific rule"** - edit `~/.nexo/config/guardian.json` -> `rules.<rule_id> = "off"`. Core rules cannot be turned off (defense in depth).
