# Personal Artifacts Manual

This is the canonical operational guide for personal NEXO artifacts.

Use it when deciding what to create, where it should live, how it should be declared, and how to verify that it is wired correctly. This document exists so NEXO does not improvise different rules across sessions or operators.

## Scope

This manual covers five decisions:

1. Should this be a personal script?
2. Should this be a skill?
3. Should this be a personal plugin?
4. Should this only be a personal schedule attached to an existing script?
5. Should this be a core repo change instead of a personal artifact?

If a proposal touches personal runtime behavior and this manual does not clearly cover it, stop and update this document before building more surface.

## First Principles

- Personal artifacts live under `NEXO_HOME`, not in repo `src/`, unless the behavior should ship to every user.
- Git installs update on merge to `main`. Release tags are for packaged artifacts and public release surfaces, not the only path by which users get code updates.
- A personal cron is not a separate code artifact. It is a managed schedule attached to a personal script or daemon helper.
- Do not edit LaunchAgent plist files directly for personal automation. Use the official scheduling flow.
- Personal scripts and skill executables must use the stable CLI or `nexo_helper.py`, not direct imports into NEXO databases or private runtime internals.
- The source of truth is behavior implemented in the runtime: parser, scheduler, doctor, loader, and templates.

## Decision Tree

Use this order:

1. If the change should become part of the product for every user, make a core repo change.
2. If you need a new MCP tool callable by Claude/Codex/Desktop sessions, make a personal plugin.
3. If you need a reusable procedure for agent behavior, make a skill.
4. If you need autonomous execution, file/system work, or scheduled automation, make a personal script.
5. If you already have the script and only need timing, add or reconcile a personal schedule to that script.

### Quick Selection Table

| Need | Correct artifact | Why |
|---|---|---|
| Background automation, cron, file operations, integrations, reports | Personal script | Scripts are the runtime unit for autonomous execution and scheduling |
| Reusable operator procedure, guide, executable task wrapper | Skill | Skills encode repeatable process for the agent layer |
| New MCP tool in the runtime surface | Personal plugin | Plugins expose callable tools to every connected client |
| Timing only for an existing script | Personal schedule | Scheduling is attached to scripts, not invented as a separate artifact |
| Capability should ship to all users | Core repo change | Personal artifacts should not masquerade as product changes |

## Ownership Map

| Surface | Purpose | Lives in |
|---|---|---|
| Core plugins | Product MCP tools for all users | `src/plugins/` |
| Personal plugins | Local runtime extensions/overrides | `NEXO_HOME/plugins/` |
| Core skills | Product skill definitions | `src/skills/` in development, `NEXO_HOME/skills-core/` in packaged installs |
| Personal skills | Local operator skills | `NEXO_HOME/skills/` |
| Personal scripts | Local automation units | `NEXO_HOME/scripts/` |
| Runtime-staged skill executables | Generated/executable runtime copies | `NEXO_HOME/skills-runtime/` |
| Personal schedules | Registry + LaunchAgent/systemd artifacts attached to scripts | discovered from `NEXO_HOME/scripts/` metadata and registered by the scheduler |
| Product docs | Shared documentation for all users | repo `docs/` and `README.md` |

## Artifact Rules

### 1. Personal Scripts

Use a personal script when the main thing you need is autonomous work:

- scheduled execution
- file or shell operations
- repeated operational jobs
- data pulls, reports, integrations
- agentic automation that should run outside an interactive session

Official path:

1. Scaffold with `nexo scripts create NAME` or copy `templates/script-template.py` / `templates/script-template.sh`.
2. Add inline metadata in the first 25 lines.
3. Validate with `nexo scripts doctor NAME`.
4. Reconcile with `nexo scripts reconcile`.
5. Verify with `nexo scripts list`, `nexo scripts schedules`, and `nexo doctor --tier runtime`.

Do not:

- query `nexo.db` or `cognitive.db` directly
- import internal DB modules from personal scripts
- hardcode `claude -p` or provider-specific model choices inside the script
- touch personal LaunchAgents manually

### 2. Skills

Use a skill when the main thing you need is a reusable procedure for the agent layer:

- a standard operating procedure
- a repeatable multi-step workflow
- a task the agent should match and apply by intent
- a procedure that may later gain an executable script

Modes:

- `guide`: text-only procedure
- `execute`: script-backed skill
- `hybrid`: guide plus executable

Official path:

1. Create a directory under `NEXO_HOME/skills/` for personal use.
2. Add `skill.json` and `guide.md`.
3. Add `script.py` or `script.sh` only if the skill truly needs execution.
4. Sync with `nexo skills sync`.
5. Inspect with `nexo skills get ...`.
6. Validate behavior with `nexo skills apply ... --dry-run` before relying on it.

Do not use a skill as a substitute for a background scheduler. Skills are agent procedures, not cron entries.

### 3. Personal Plugins

Use a personal plugin when you need a new MCP tool available to the local NEXO runtime and all connected clients:

- a custom tool callable from Claude Code, Codex, or Claude Desktop
- a runtime extension that does not belong in public core yet
- a local override of a repo plugin by filename

Official path:

1. Scaffold with `nexo_personal_plugin_create(...)` or `templates/plugin-template.py`.
2. Edit the file in `NEXO_HOME/plugins/`.
3. Load or reload with `nexo_plugin_load("filename.py")`.
4. Verify with `nexo_plugin_list()`.

Use a companion script only if the plugin needs a local automation unit as part of its implementation.

Do not use a plugin when a script is enough. If the capability is "run this job every day", that is a script plus schedule problem, not a plugin problem.

### 4. Personal Schedules

Use a personal schedule only as an attachment to a personal script or daemon helper.

Official path:

1. Declare schedule metadata in the script.
2. Run `nexo scripts reconcile` to sync filesystem state and create/repair the schedule.
3. Verify with `nexo scripts schedules` and `nexo doctor --tier runtime`.

You may use `nexo_schedule_add(...)` directly for one-off schedule creation, but declared metadata plus reconcile is the canonical path for persistent personal automation.

Do not:

- edit `~/Library/LaunchAgents/` manually
- create unmanaged manual schedules and then pretend they are canonical

### 5. Core Repo Changes

Use a core repo change when:

- the behavior is part of NEXO Brain as a product
- every user should get it via merge/update
- the docs are meant to define product-wide behavior
- the code lives in `src/`, `docs/`, `templates/`, tests, or release assets

If the change belongs to all users, do not hide it in `NEXO_HOME/`.

## Scheduling Metadata: Exact Supported Rules

The declared schedule parser accepts these relevant keys:

- `name`
- `description`
- `runtime`
- `cron_id`
- `schedule`
- `interval_seconds`
- `schedule_required`
- `recovery_policy`
- `run_on_boot`
- `run_on_wake`
- `idempotent`
- `max_catchup_age`

### Supported formats

- `schedule=HH:MM`
- `schedule=HH:MM:weekday`
- `interval_seconds=<positive integer>`
- `max_catchup_age=<integer seconds>`

### Unsupported formats

- `monthly:1`
- `2d`
- arbitrary cron expressions
- direct plist edits presented as official scheduling

If both `schedule` and `interval_seconds` are set, the declaration is invalid.

If a script is declared as scheduled, it must also provide:

- `name`
- `runtime`
- `cron_id`
- `schedule_required=true`

## Monthly Jobs: Canonical Pattern

LaunchAgent calendar scheduling does not give NEXO a dedicated "day 1 of month" metadata format in the declared parser.

The canonical pattern for monthly personal jobs is:

1. declare a daily calendar schedule, for example `schedule=09:00`
2. keep `schedule_required=true`
3. inside the script, self-gate on day-of-month and skip on other days unless forced

This is the live pattern already used by monthly jobs such as `gbp-monthly-audit.py`.

Do not invent `monthly:1` in inline metadata.

## Stub vs Real Runner

Some workflows have both:

- a helper/template/stub file
- the actual executable runner

Before attaching a schedule, verify which file is the real executable.

Rule:

- schedule the real runner
- do not schedule the stub/helper if its own header says the real job lives elsewhere

This matters especially for paired `.py` and `.sh` variants of the same logical workflow.

## Verification Checklists

### Personal script checklist

- file is in `NEXO_HOME/scripts/`
- metadata matches the real runtime behavior
- `nexo scripts doctor NAME` passes
- `nexo scripts reconcile` completes without drift
- `nexo scripts schedules` shows the expected managed schedule
- `nexo doctor --tier runtime` does not report orphan/drift for that script

### Skill checklist

- skill lives in the right directory (`NEXO_HOME/skills/` for personal)
- `skill.json` and `guide.md` exist
- optional script uses stable CLI / helper path
- `nexo skills sync` succeeds
- `nexo skills get` shows the expected metadata
- `nexo skills apply --dry-run` behaves coherently

### Personal plugin checklist

- file lives in `NEXO_HOME/plugins/`
- tool names are explicit and prefixed appropriately
- `nexo_plugin_load(...)` succeeds
- `nexo_plugin_list()` shows the plugin and tools
- if the plugin depends on a script, that companion artifact is documented

### Core change checklist

- change lives in repo, not `NEXO_HOME`
- docs/tests updated together
- branch/PR/merge path is used
- remember: git users get the code at merge to `main`; packaged release surfaces are a second channel

## Anti-Patterns

Never do these:

- edit personal LaunchAgent plist files directly and call it the official path
- document unsupported parser syntax as if it were real
- turn a personal workaround into product-wide documentation without code backing it
- use a skill for a background scheduler problem
- use a plugin when a simple script is enough
- schedule a helper/stub file instead of the real executable
- change only a runtime/personal copy of a core plugin and forget the repo source of truth
- claim users need a tagged release to get git-based updates after merge to `main`

## What To Tell Another Session

If another session proposes a personal artifact flow, check these first:

1. Does it match the actual parser/scheduler formats?
2. Does it keep ownership correct: core vs `NEXO_HOME`?
3. Is it using reconcile/doctor instead of manual plist edits?
4. Is it scheduling the real runner, not the stub?
5. Is it describing git-update vs packaged-release behavior honestly?

If any answer is "no", correct the plan before building.

## Source Anchors

This manual is grounded in the current runtime behavior implemented in:

- `docs/writing-scripts.md`
- `docs/skills-v2.md`
- `src/script_registry.py`
- `src/plugins/schedule.py`
- `src/plugins/personal_scripts.py`
- `src/plugins/personal_plugins.py`
- `src/plugin_loader.py`
- `src/doctor/providers/runtime.py`
- `templates/script-template.py`
- `templates/plugin-template.py`
- `templates/skill-template.md`
- `templates/skill-script-template.py`

When runtime behavior changes, update this manual in the same change.
