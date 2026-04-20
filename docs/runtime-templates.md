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

## Prompt Catalog Templates

These are product-controlled prompt bodies, not personal scaffolds:

| Template | Path |
|---|---|
| Email monitor | `templates/core-prompts/email-monitor.md` |
| Followup runner | `templates/core-prompts/followup-runner.md` |
| Morning agent | `templates/core-prompts/morning-agent.md` |
| Daily synthesis | `templates/core-prompts/daily-synthesis.md` |
| Postmortem consolidator | `templates/core-prompts/postmortem-consolidator.md` |
| Sleep | `templates/core-prompts/sleep.md` |

## Rules

- Prefer these templates over ad-hoc copies.
- Keep personal artifacts under `NEXO_HOME`, not inside repo `src/`, unless the behavior should ship to all users.
- If a template no longer reflects the runtime contract, update the template and the consuming docs/tests together.
- Do not create parallel template trees under `personal/` that shadow the product contract without a specific reason.
