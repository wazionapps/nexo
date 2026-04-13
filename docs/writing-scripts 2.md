# Writing Personal Scripts for NEXO

Personal scripts extend NEXO with custom automation. They live in `NEXO_HOME/scripts/`, use the stable CLI as their interface, and are registered in NEXO's personal script registry so updates and scheduling don't get confused with core jobs.

If you are not sure whether you need a script, a skill, a plugin, or only a schedule, read [Personal Artifacts Manual](./personal-artifacts-manual.md) first. That document is the canonical decision guide.

## Quick Start

1. Copy the template:
   ```bash
   cp $NEXO_HOME/templates/script-template.py $NEXO_HOME/scripts/my-script.py
   ```

2. Edit the metadata and logic.

3. Validate:
   ```bash
   nexo scripts doctor my-script
   ```

4. Reconcile registry + declared schedules:
   ```bash
   nexo scripts reconcile
   ```

5. Run:
   ```bash
   nexo scripts run my-script -- --query "something"
   ```

## Registry Model

NEXO now tracks personal scripts as first-class entities:

- Filesystem remains the source of truth: `NEXO_HOME/scripts/`
- SQLite stores the registry: what the script is, where it lives, what runtime it uses, and what schedules are attached
- Personal schedules are discovered from personal LaunchAgents/systemd timers and linked back to the script

This lets NEXO answer questions like:

- Which personal scripts already exist?
- Which ones were created by NEXO vs manually?
- Which script has a cron attached?
- Which personal schedules are stale or drifted?

## Metadata

Add inline metadata in the first 25 lines using `# nexo:` comments:

```python
# nexo: name=my-script
# nexo: description=What this script does
# nexo: category=shopify
# nexo: runtime=python
# nexo: timeout=60
# nexo: requires=git,rsync
# nexo: tools=nexo_learning_search,nexo_schedule_status
# nexo: interval_seconds=300
# nexo: cron_id=my-script
```

All keys are optional. Without metadata, the script name defaults to the filename stem.

### Supported Keys

| Key | Description |
|-----|-------------|
| `name` | Script name (default: filename stem) |
| `description` | One-line description |
| `category` | Optional grouping label for humans/NEXO |
| `runtime` | `python`, `shell`, `node`, or `php` (auto-detected from shebang/extension) |
| `timeout` | Max execution time in seconds |
| `requires` | Comma-separated commands that must be in PATH |
| `tools` | Comma-separated NEXO MCP tools this script uses |
| `hidden` | `true` to hide from default list |
| `cron_id` | Stable schedule ID when the script has a personal cron |
| `schedule` | Calendar schedule: `HH:MM` or `HH:MM:weekday` |
| `interval_seconds` | Interval schedule in seconds |
| `schedule_required` | `true` if the script must have a schedule |
| `recovery_policy` | Recovery contract such as `catchup` or `restart_daemon` |

### Declaring Personal Schedules

If a script should run automatically, declare it inline and let NEXO reconcile it:

```python
# nexo: name=email-monitor
# nexo: description=Monitor inbox every 5 minutes
# nexo: runtime=python
# nexo: interval_seconds=300
# nexo: schedule_required=true
```

Or for a calendar schedule:

```python
# nexo: name=morning-brief
# nexo: description=Send the morning briefing
# nexo: runtime=shell
# nexo: schedule=08:00
# nexo: schedule_required=true
```

Or for a daemon-style `KeepAlive` helper:

```bash
# nexo: name=wake-recovery
# nexo: description=Repair interval LaunchAgents after sleep/wake gaps
# nexo: runtime=shell
# nexo: cron_id=wake-recovery
# nexo: schedule_required=true
# nexo: recovery_policy=restart_daemon
# nexo: run_on_boot=true
```

`restart_daemon` is the official way to declare a personal `KeepAlive` service. NEXO will reconcile it as a managed daemon instead of treating it as an unmanaged manual LaunchAgent.

Then run:

```bash
nexo scripts reconcile
```

This does three things in order:

1. Classifies everything in `NEXO_HOME/scripts/`
2. Syncs personal scripts into the registry
3. Creates or repairs any **declared personal schedules**

NEXO must never invent a core cron by touching `crons/manifest.json` for a personal script.

### Monthly Jobs

The declared schedule parser does not support a dedicated `monthly:1` syntax.

The canonical monthly pattern is:

1. declare a daily calendar schedule such as `schedule=09:00`
2. keep `schedule_required=true`
3. self-gate inside the script so it only performs real work on the intended day-of-month unless forced

This is cleaner than manual plist editing and matches how current monthly jobs already work in production.

## Calling NEXO Tools

Use the `nexo_helper.py` module (in `NEXO_HOME/templates/`):

```python
from nexo_helper import call_tool_text, call_tool_json

# Text output
result = call_tool_text("nexo_learning_search", {"query": "cron errors"})
print(result)

# JSON output
data = call_tool_json("nexo_schedule_status", {"hours": 24})
print(data)
```

Or call tools directly from the CLI:

```bash
nexo scripts call nexo_learning_search --input '{"query":"cron errors"}'
nexo scripts call nexo_schedule_status --input '{"hours":24}' --json-output
```

## Hot Context Pattern for Automation

If a script makes decisions across repeated runs, email cycles, or client handoffs, it should use the recent-memory layer explicitly.

Use this pattern:

1. `nexo_pre_action_context(...)` before acting
2. `nexo_recent_context_capture(...)` when a topic becomes active, blocked, or waiting
3. `nexo_recent_context_resolve(...)` when the topic is clearly done
4. `nexo_transcript_search(...)` / `nexo_transcript_read(...)` when the script knows the discussion happened but recent-memory capture is incomplete
5. `nexo_system_catalog(...)` / `nexo_tool_explain(...)` when the script needs a live map of NEXO's own tools, scripts, skills, crons, projects, or artifacts

Example:

```python
from nexo_helper import call_tool_text

bundle = call_tool_text("nexo_pre_action_context", {
    "query": "francisco email dns recambiosbmw",
    "hours": 24,
    "limit": 6,
})
print(bundle)

call_tool_text("nexo_recent_context_capture", {
    "title": "Awaiting registrar action",
    "summary": "Asked Maria to handle the registrar. Waiting external action.",
    "topic": "dns recambiosbmw",
    "state": "waiting_third_party",
    "owner": "maria",
    "source_type": "email",
    "source_id": "thread-123",
})
```

This is different from reminder/followup history:

- reminder/followup history records changes to a specific item
- hot context keeps the last 24 hours of live operational continuity fresh across channels

## System Catalog Pattern

Do not hardcode your mental model of NEXO forever inside one script.

When a script needs to know what NEXO currently exposes, prefer:

```python
catalog = call_tool_text("nexo_system_catalog", {
    "section": "scripts",
    "query": "doctor",
    "limit": 10,
})
print(catalog)

tool_help = call_tool_text("nexo_tool_explain", {
    "name": "nexo_pre_action_context",
})
print(tool_help)
```

The system catalog is generated from live canonical sources. It updates as core tools, plugins, skills, scripts, crons, projects, and artifacts change.

## Automation Task Profiles

When a personal script needs intelligence from the automation backend, prefer task profiles over hardcoding a provider-specific command line. `nexo-agent-run.py` now accepts:

```bash
nexo-agent-run.py --task-profile fast --prompt "Summarize these logs"
nexo-agent-run.py --task-profile deep --prompt-file prompt.md
```

Available profiles:

| Profile | Intent |
|---------|--------|
| `fast` | Prefer the lower-latency / lower-cost backend path when available |
| `balanced` | Use the configured default backend/runtime profile |
| `deep` | Prefer the heavier reasoning path for high-stakes synthesis/review |

If the selected backend is unavailable, NEXO now falls back safely to another installed terminal backend instead of failing half-configured.

## Environment Variables

When running via `nexo scripts run`, these env vars are injected:

| Variable | Description |
|----------|-------------|
| `NEXO_HOME` | NEXO home directory |
| `NEXO_CODE` | NEXO source code directory |
| `NEXO_SCRIPT_NAME` | Script name (from metadata or filename) |
| `NEXO_SCRIPT_PATH` | Absolute path to the script |
| `NEXO_CLI` | Always `nexo` |

## Rules

- **DO** use `nexo scripts call` or `nexo_helper.py` for NEXO interaction
- **DO** use argparse for script arguments
- **DO** return clean exit codes (0 = success)
- **DO** use `nexo scripts reconcile` as the official path for declared schedules
- **DON'T** import `db`, `server`, `cognitive`, or other NEXO internals
- **DON'T** access `nexo.db` or `cognitive.db` directly
- **DON'T** use `sqlite3` to query NEXO databases
- **DON'T** edit personal LaunchAgents manually
- **DON'T** document unsupported schedule syntax such as `monthly:1`

The `nexo scripts doctor` command checks for these violations.

## CLI Reference

```
nexo scripts list              # List personal scripts
nexo scripts list --all        # Include core/internal scripts
nexo scripts list --json       # JSON output
nexo scripts create NAME       # Create scaffold in NEXO_HOME/scripts
nexo scripts classify          # Classify files in NEXO_HOME/scripts
nexo scripts sync              # Sync registry from filesystem + personal LaunchAgents
nexo scripts reconcile         # Sync and ensure declared schedules
nexo scripts ensure-schedules  # Create/repair schedules declared in metadata
nexo scripts schedules         # List registered personal schedules
nezo scripts unschedule NAME   # Remove a script's personal schedules
nezo scripts remove NAME       # Unschedule + remove a personal script
nexo scripts run NAME          # Run a script
nexo scripts run NAME -- args  # Run with arguments
nexo scripts doctor            # Validate all personal scripts
nexo scripts doctor NAME       # Validate a specific script
nexo scripts call TOOL --input JSON  # Call an MCP tool
```
