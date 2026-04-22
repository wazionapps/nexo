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
