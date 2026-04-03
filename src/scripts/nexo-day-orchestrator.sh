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
MAX_TURNS=50        # Claude max turns per cycle
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
PROMPT='You are NEXO in autonomous orchestrator mode. Francisco is NOT present. You have 10 minutes max.

SKIP startup ceremony. No menu. No greetings. Go straight to work.

DO THIS IN ORDER:

1. nexo_startup(task="orchestrator-cycle") — get your SID, nothing else
2. nexo_reminders(filter="due") — look at what is due RIGHT NOW
   - Followups that YOU can do (check email, verify something, monitor) → DO THEM
   - Followups that need Francisco → note them for email
   - Already done? → nexo_followup_complete immediately
3. nexo_email_inbox(unread_only=true, limit=5) — any unread emails?
   - You can answer or process → do it
   - Important for Francisco → note for email
4. Only if something is degraded or you noticed issues → nexo_doctor(tier="runtime")

AFTER doing all the above, decide:

IF you did something useful OR Francisco needs to know something:
  → nexo_email_send(to="franciscoc.systeam.es@gmail.com", subject="NEXO: [short summary]", body="[HTML with what you DID and what needs his attention]")
  → The email MUST be HTML with clear sections. Short. No fluff.

IF nothing happened and nothing is urgent:
  → Do NOT send email. Just close.

CLOSE: nexo_session_diary_write(summary="[1 line of what you did]", domain="orchestrator") then nexo_stop(sid=YOUR_SID)

CRITICAL RULES:
- autonomy=full. Never ask permission.
- Act first, report after.
- Max 1 email per cycle. Only if there is real content.
- Do NOT run nexo_menu. Do NOT show greetings. Do NOT waste turns on ceremony.
- If a followup says "NEXO task" or "work of NEXO" → that means YOU do it, not Francisco.
- Keep it fast. Every turn counts.'

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
