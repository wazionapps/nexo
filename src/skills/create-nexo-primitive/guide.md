# Create NEXO Primitive

Use this skill when the operator or the agent needs to create or modify one of these artifact families:

- personal script
- skill
- personal plugin
- schedule attached to an existing script
- core repo capability instead of a personal artifact

This skill exists so NEXO stops improvising the wrong primitive or pulling half-remembered scaffolds from older sessions.

## Source of truth

Before deciding anything, read:

1. `docs/product-engineering-handbook.md`
2. `docs/agent-product-playbook.md`
3. `docs/personal-artifacts-manual.md`
4. `docs/runtime-templates.md`

If those docs do not cover the case clearly, update the docs before building more surface.

## Decision flow

1. Decide whether the need belongs to the product or only to one operator/runtime.
2. If the capability should ship to every user, make a core repo change.
3. If you need a new MCP tool callable by clients, use a personal plugin.
4. If you need a reusable agent procedure, use a skill.
5. If you need autonomous execution, shell/file work, or scheduling, use a personal script.
6. If timing is the only missing piece for an existing script, use a schedule-only change.

## Canonical scaffolds

Always start from `templates/`:

- `templates/script-template.py`
- `templates/script-template.sh`
- `templates/plugin-template.py`
- `templates/skill-template.md`
- `templates/skill-script-template.py`
- `templates/email-template.md`
- `templates/nexo_helper.py`

Do not create a parallel scaffold tree under `personal/` unless the product docs explicitly require it.

Worked starting points and example usage now live in `docs/runtime-templates.md`. Use that document instead of copying old operator files by memory.

## Validation by artifact type

### Personal script

1. Put it under `NEXO_HOME/personal/scripts/`.
2. Add inline metadata in the header.
3. Run `nexo scripts doctor NAME`.
4. Run `nexo scripts reconcile`.
5. Verify with `nexo scripts list`, `nexo scripts schedules`, and `nexo doctor --tier runtime`.

### Skill

1. Put it under `NEXO_HOME/personal/skills/<slug>/` for personal use or `src/skills/<slug>/` if it is product-core.
2. Create `skill.json` and `guide.md`.
3. Add `script.py` only if it really needs executable behavior.
4. Run `nexo skills sync`.
5. Check with `nexo skills get SK-...` and `nexo skills apply SK-... --dry-run`.

### Personal plugin

1. Put it under `NEXO_HOME/personal/plugins/`.
2. Scaffold from `templates/plugin-template.py`.
3. Load it with `nexo_plugin_load(...)`.
4. Verify with `nexo_plugin_list()` and `nexo_tool_explain(...)`.

### Schedule-only change

1. Keep the script as the canonical automation unit.
2. Declare schedule metadata in the script where possible.
3. Use `nexo scripts reconcile` as the canonical sync path.
4. Verify with `nexo scripts schedules`.

## Guardrails

- Do not create a plugin when a script is enough.
- Do not edit LaunchAgent plist files directly as the official path.
- Do not let a personal artifact masquerade as a core product feature.
- Do not create names that collide with shipped core scripts or skills.
- Do not claim the artifact is ready until the canonical validation step passes.
