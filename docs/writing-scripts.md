# Writing Personal Scripts for NEXO

Personal scripts extend NEXO with custom automation. They live in `NEXO_HOME/scripts/` and use the stable CLI as their interface.

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

4. Run:
   ```bash
   nexo scripts run my-script -- --query "something"
   ```

## Metadata

Add inline metadata in the first 25 lines using `# nexo:` comments:

```python
# nexo: name=my-script
# nexo: description=What this script does
# nexo: runtime=python
# nexo: timeout=60
# nexo: requires=git,rsync
# nexo: tools=nexo_learning_search,nexo_schedule_status
```

All keys are optional. Without metadata, the script name defaults to the filename stem.

### Supported Keys

| Key | Description |
|-----|-------------|
| `name` | Script name (default: filename stem) |
| `description` | One-line description |
| `runtime` | `python` or `shell` (auto-detected from shebang/extension) |
| `timeout` | Max execution time in seconds |
| `requires` | Comma-separated commands that must be in PATH |
| `tools` | Comma-separated NEXO MCP tools this script uses |
| `hidden` | `true` to hide from default list |

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
- **DON'T** import `db`, `server`, `cognitive`, or other NEXO internals
- **DON'T** access `nexo.db` or `cognitive.db` directly
- **DON'T** use `sqlite3` to query NEXO databases

The `nexo scripts doctor` command checks for these violations.

## CLI Reference

```
nexo scripts list              # List personal scripts
nexo scripts list --all        # Include core/internal scripts
nexo scripts list --json       # JSON output
nexo scripts run NAME          # Run a script
nexo scripts run NAME -- args  # Run with arguments
nexo scripts doctor            # Validate all personal scripts
nexo scripts doctor NAME       # Validate a specific script
nexo scripts call TOOL --input JSON  # Call an MCP tool
```
