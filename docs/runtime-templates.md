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
| Followup runner | `templates/core-prompts/followup-runner.md`, `followup-runner-operator-attention-*.md` |
| Morning agent | `templates/core-prompts/morning-agent.md` |
| Daily synthesis | `templates/core-prompts/daily-synthesis.md` |
| Postmortem consolidator | `templates/core-prompts/postmortem-consolidator.md` |
| Sleep | `templates/core-prompts/sleep.md` |
| Enforcement classifier (strict / retry) | `templates/core-prompts/enforcement-classifier-*.md` |
| Rule prompts R14 / R15 / R16 / R17 / R18 / R19 / R20 / R21 / R22 / R23* / R24 / R25 | `templates/core-prompts/r14-*.md`, `r15-*.md`, `r16-*.md`, `r17-*.md`, `r18-*.md`, `r19-*.md`, `r20-*.md`, `r21-*.md`, `r22-*.md`, `r23*.md`, `r24-*.md`, `r25-*.md` |
| R-CATALOG probe | `templates/core-prompts/r-catalog.md` |
| R34 identity coherence | `templates/core-prompts/r34-identity-coherence-*.md` |
| Interactive startup | `templates/core-prompts/interactive-startup.md` |

## Rules

- Prefer these templates over ad-hoc copies.
- Keep personal artifacts under `NEXO_HOME`, not inside repo `src/`, unless the behavior should ship to all users.
- If a template no longer reflects the runtime contract, update the template and the consuming docs/tests together.
- Do not create parallel template trees under `personal/` that shadow the product contract without a specific reason.
