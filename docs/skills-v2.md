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
- `read-only`: auto-runs
- `local`: auto-runs
- `remote`: auto-runs

Deep Sleep now does two things automatically:
- creates new Skills v2 definitions from extracted procedures
- promotes mature guide skills (3+ successful uses, high trust, no script yet) into executable drafts under `NEXO_HOME/skills/`

If Claude synthesis emits a concrete `script_body`, Deep Sleep materializes that script directly. If not, it still creates a deterministic executable draft so the skill enters the runtime loop immediately.
