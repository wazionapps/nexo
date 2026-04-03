# Skills v2

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
- `read-only`: can auto-run without approval
- `local`: requires explicit approval
- `remote`: requires explicit approval

Deep Sleep may auto-create guide skills and read-only executable drafts, but it must not auto-approve local or remote execution.
