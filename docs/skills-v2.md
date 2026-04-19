# Skills v2

If you are deciding between a skill, a script, a plugin, or a schedule, read [Personal Artifacts Manual](./personal-artifacts-manual.md) first. Skills are the reusable procedure layer, not the generic answer to every automation problem.

Skills v2 add three execution modes:
- `guide`: text-only procedure
- `execute`: script-backed skill
- `hybrid`: guide + script

Filesystem sources:
- Personal: `NEXO_HOME/skills/`
- Core: `src/skills/` in development, `NEXO_HOME/skills-core/` in packaged installs
- Community: `community/skills/`
- Runtime-staged executables: `NEXO_HOME/skills-runtime/`

Commands:

```bash
nexo skills sync
nexo skills list
nexo skills get SK-RUN-RUNTIME-DOCTOR
nexo skills apply SK-RUN-RUNTIME-DOCTOR --params '{"tier":"runtime"}'
nexo skills approve SK-MY-DEPLOY --execution-level local --approved-by Francisco
nexo skills evolution
```

Execution policy:
- `none`: never executes
- `read-only`: auto-runs
- `local`: auto-runs
- `remote`: auto-runs

Deep Sleep now does two things automatically:
- creates new Skills v2 definitions from extracted procedures
- promotes mature guide skills (3+ successful uses, high trust, no script yet) into executable drafts under `NEXO_HOME/skills/`

If Claude synthesis emits a concrete `script_body`, Deep Sleep materializes that script directly. If not, it still creates a deterministic executable draft so the skill enters the runtime loop immediately.

## When To Use A Skill

Use a skill when the primary output is a reusable procedure for the agent layer:

- a guide the agent should follow repeatedly
- a hybrid task with guide plus executable script
- an execution wrapper that should be matched and applied by intent

Do not use a skill when the real need is:

- a background cron
- a file/system automation job
- a new MCP tool exposed to the runtime

Those belong to personal scripts, schedules, or plugins, as defined in the canonical manual.
