#!/bin/bash
# ============================================================================
# NEXO Day Orchestrator — autonomous NEXO cycle every 15 min
# Schedule: keepAlive, self-enforced operating hours (default 8:00-23:00)
#
# This is NOT a Python script that simulates intelligence.
# This launches Claude Code as NEXO with full MCP access.
# NEXO thinks, acts, and reports — like any interactive session.
# ============================================================================
set -euo pipefail

NEXO_HOME="${NEXO_HOME:-$HOME/.nexo}"
LOG_DIR="$NEXO_HOME/logs"
mkdir -p "$LOG_DIR" "$NEXO_HOME/operations"

# --- Configuration ---
CYCLE_INTERVAL=900  # 15 minutes between cycles
CYCLE_TIMEOUT=600   # 10 min max per cycle
MAX_TURNS=30        # Claude max turns per cycle
HOUR_START=8
HOUR_END=23

# --- Find Claude CLI ---
find_claude() {
    for candidate in \
        "$(command -v claude 2>/dev/null)" \
        "$HOME/.claude/local/claude" \
        "/opt/homebrew/bin/claude" \
        "/usr/local/bin/claude"; do
        if [ -n "$candidate" ] && [ -x "$candidate" ]; then
            echo "$candidate"
            return 0
        fi
    done
    return 1
}

CLAUDE=$(find_claude) || {
    echo "$(date '+%Y-%m-%d %H:%M') ERROR: claude CLI not found" >&2
    exit 1
}

# --- Prevent overlapping cycles ---
LOCKFILE="$NEXO_HOME/operations/.orchestrator.lock"
acquire_lock() {
    if [ -f "$LOCKFILE" ]; then
        local pid
        pid=$(cat "$LOCKFILE" 2>/dev/null || echo "")
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            return 1  # Still running
        fi
    fi
    echo $$ > "$LOCKFILE"
    return 0
}
release_lock() { rm -f "$LOCKFILE"; }

# --- The orchestrator prompt ---
PROMPT='You are NEXO in autonomous orchestrator mode. The user is NOT present. You have 5 minutes max.

ABSOLUTE PRIORITY: act, do not list. If you can do something, do it. If you need the user, send email.

CHECKLIST (in this order):

1. OVERDUE FOLLOWUPS: nexo_reminders(filter="due") + nexo_reminders(filter="followups")
   - NEXO tasks (verify, check, monitor) → DO THEM NOW
   - Tasks needing user decision → accumulate for email
   - Completed ones → nexo_followup_complete

2. EMAIL: nexo_email_inbox(unread_only=true, limit=10)
   - Emails you can process → process them
   - Important emails for user → accumulate for email

3. INFRASTRUCTURE: nexo_doctor(tier="runtime")
   - If degraded/critical → try to fix

4. EMAIL TO USER (only if there is something to report):
   - nexo_email_send with clean HTML summary
   - Only what needs attention or decision
   - Include what you ALREADY DID (not just pending items)
   - If nothing relevant → DO NOT send email
   - Max 1 email per cycle

5. DIARY: nexo_session_diary_write with what you did

RULES:
- DO NOT ask permission. autonomy=full
- DO NOT send empty or "all ok" emails
- DO NOT list things without acting
- If a followup is executable → execute it before reporting
- Use nexo_heartbeat at start
- Clean close: diary + nexo_stop'

# --- Main loop ---
echo "$(date '+%Y-%m-%d %H:%M') NEXO Day Orchestrator starting (PID $$)"
echo "  Claude: $CLAUDE"
echo "  Cycle: every ${CYCLE_INTERVAL}s, ${HOUR_START}:00-${HOUR_END}:00"
echo "  Timeout: ${CYCLE_TIMEOUT}s, max turns: $MAX_TURNS"

while true; do
    HOUR=$(date +%H | sed 's/^0//')

    # Outside operating hours — sleep and check again
    if [ "$HOUR" -lt "$HOUR_START" ] || [ "$HOUR" -ge "$HOUR_END" ]; then
        sleep 300  # Check every 5 min if we're back in hours
        continue
    fi

    # Try to acquire lock
    if ! acquire_lock; then
        echo "$(date '+%Y-%m-%d %H:%M') Previous cycle still running. Skipping."
        sleep "$CYCLE_INTERVAL"
        continue
    fi

    TIMESTAMP=$(date '+%Y-%m-%d_%H%M')
    LOGFILE="$LOG_DIR/orchestrator-$TIMESTAMP.log"
    echo "$(date '+%Y-%m-%d %H:%M') Cycle starting..."

    # Launch Claude Code as NEXO
    set +e
    timeout "$CYCLE_TIMEOUT" "$CLAUDE" \
        --dangerously-skip-permissions \
        -p "$PROMPT" \
        --max-turns "$MAX_TURNS" \
        >>"$LOGFILE" 2>&1
    EXIT_CODE=$?
    set -e

    echo "$(date '+%Y-%m-%d %H:%M') Cycle finished (exit $EXIT_CODE)" | tee -a "$LOGFILE"

    release_lock

    # Clean old logs (keep 7 days)
    find "$LOG_DIR" -name "orchestrator-*.log" -mtime +7 -delete 2>/dev/null || true

    # Sleep until next cycle
    sleep "$CYCLE_INTERVAL"
done
