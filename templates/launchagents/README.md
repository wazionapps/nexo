# NEXO LaunchAgent Templates

macOS LaunchAgents that run NEXO's background processes automatically. These keep the system alive, consistent, and self-improving without any manual intervention.

## What are LaunchAgents?

LaunchAgents are macOS's native mechanism for running programs automatically per user session. They are XML property list (`.plist`) files stored in `~/Library/LaunchAgents/`. `launchd` reads them at login and keeps them running according to their schedule. Unlike cron, they survive sleep/wake cycles and can run at load, on a fixed interval, or at a specific calendar time.

## Installation

### 1. Set your paths

All templates use two placeholders that you must replace before installing:

| Placeholder | Replace with |
|------------|-------------|
| `{{NEXO_HOME}}` | Absolute path to your NEXO knowledge base directory (default: `~/.nexo` or `$NEXO_HOME`) |
| `{{HOME}}` | Your home directory (e.g. `/Users/yourname` or `$HOME`) |

Replace them in every file you want to install:

```bash
# Example — replace both placeholders in all files at once
NEXO_HOME="$HOME/.nexo"
HOME_DIR="$HOME"

for f in *.plist; do
  sed -i '' \
    "s|{{NEXO_HOME}}|$NEXO_HOME|g; s|{{HOME}}|$HOME_DIR|g" \
    "$f"
done
```

### 2. Check the Python path

Each plist calls `/usr/bin/python3`. If your Python 3 is elsewhere (e.g. Homebrew at `/opt/homebrew/bin/python3`), update the `ProgramArguments` first line accordingly.

### 3. Create required directories

The agents write logs to `{{NEXO_HOME}}/logs/` and `{{NEXO_HOME}}/coordination/`. Create them if they do not exist:

```bash
mkdir -p "$NEXO_HOME/logs" "$NEXO_HOME/coordination"
```

### 4. Copy and load

```bash
# Copy plists to LaunchAgents directory
cp *.plist ~/Library/LaunchAgents/

# Load each one
for f in ~/Library/LaunchAgents/com.nexo.*.plist; do
  launchctl load "$f"
done
```

### Unloading

```bash
for f in ~/Library/LaunchAgents/com.nexo.*.plist; do
  launchctl unload "$f"
done
```

### Checking status

```bash
# List all loaded NEXO agents
launchctl list | grep nexo

# Check a specific one (exit code 0 = running or loaded OK)
launchctl list com.nexo.watchdog
```

---

## Agents

### Essential

These agents are required for NEXO to function correctly. Install all of them.

| File | Schedule | What it does |
|------|----------|-------------|
| `com.nexo.auto-close-sessions.plist` | Every 5 min | Expires stale sessions (no heartbeat for 15+ min). Prevents ghost sessions from cluttering the startup menu. |
| `com.nexo.watchdog.plist` | Every 30 min | Monitors that key services and cron jobs are alive. Writes `watchdog-status.json` and sets a flag when failures are detected. NEXO reads this at startup and alerts you before anything else. |
| `com.nexo.catchup.plist` | At login | Processes overdue followups and missed scheduled jobs after reboot or sleep. Ensures no maintenance task is permanently skipped. |

### Core background intelligence

These agents power NEXO's learning and memory systems. Strongly recommended.

| File | Schedule | What it does |
|------|----------|-------------|
| `com.nexo.deep-sleep.plist` | Daily 04:30 | Reads full session transcripts from the previous day and extracts learnings that were not captured during live sessions. NEXO's "REM sleep." |
| `com.nexo.cognitive-decay.plist` | Daily 03:00 | Applies Ebbinghaus forgetting curve to the vector memory database. Reduces confidence scores for memories that have not been reinforced recently, keeping retrieval fresh and accurate. |
| `com.nexo.synthesis.plist` | Every 2 hours | Aggregates recent session diaries, error patterns, and pending items into `coordination/daily-synthesis.md`. This file is read at every session startup. |
| `com.nexo.postmortem.plist` | Daily 23:30 | Produces an end-of-day consolidated summary of decisions, changes, and errors. Feeds into the next morning's synthesis. |
| `com.nexo.self-audit.plist` | Daily 07:00 | Audits the learning system health. Flags repeated errors, calculates the learning repetition rate, and alerts if it exceeds 30%. |
| `com.nexo.immune.plist` | Every 30 min | Scans the knowledge base for internal contradictions and stale data. Prevents memory drift over time. |

### Weekly maintenance

| File | Schedule | What it does |
|------|----------|-------------|
| `com.nexo.evolution.plist` | Machine-staggered weekly (managed installs) | Reviews the week's patterns and proposes improvements to NEXO's own configuration. Managed installs spread each machine across the week to avoid PR spikes; the static plist template is only a manual fallback. |
| `com.nexo.followup-hygiene.plist` | Sundays 05:00 | Cleans up stale followups and reminders. Archives long-pending items and deduplicates entries. Keeps the operational database noise-free. |

### Optional

| File | Schedule | What it does |
|------|----------|-------------|
| `com.nexo.dashboard.plist` | Persistent (KeepAlive) | Runs the NEXO web dashboard on `http://localhost:6174`. Provides a browser-based view of sessions, reminders, followups, and system health. Only needed if you want the dashboard UI. |
| `com.nexo.github-monitor.plist` | Daily 08:00 | Checks the NEXO public GitHub repository for open issues, pull requests, and pending releases. Writes results to `~/.nexo/github-status.json` for NEXO to read at startup. Only relevant if you maintain the public NEXO repository. |

---

## Logs

All agents write stdout and stderr to files under `{{NEXO_HOME}}/logs/` (or `{{NEXO_HOME}}/coordination/` for the session-related ones). Check these first when debugging:

```bash
tail -50 "$NEXO_HOME/logs/watchdog-stdout.log"
tail -50 "$NEXO_HOME/logs/deep-sleep-stderr.log"
```

## Notes

- All times are local machine time.
- If the machine is off or sleeping when a scheduled time fires, `launchd` will NOT run the job retroactively — that is what `com.nexo.catchup` handles at the next login.
- The `com.nexo.dashboard` agent uses `KeepAlive: true`, meaning macOS will restart it automatically if it exits. Unload it explicitly if you want to stop it.
- Python path in the templates is `/usr/bin/python3` (macOS system Python). If your scripts require packages installed in a virtualenv or Homebrew Python, update the path to match (e.g. `/opt/homebrew/bin/python3`).
