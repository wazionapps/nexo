#!/bin/bash
# NEXO PostCompact Hook — Re-inject Core Memory Block after compaction
# Reads the latest session checkpoint from SQLite and generates a structured
# context block that preserves session continuity.
set -euo pipefail

NEXO_HOME="${NEXO_HOME:-$HOME/.nexo}"
NEXO_DB="$NEXO_HOME/nexo.db"
TODAY=$(date +%Y-%m-%d)
LOG_FILE="$NEXO_HOME/operations/tool-logs/${TODAY}.jsonl"
LOG_LINES=0
if [ -f "$LOG_FILE" ]; then
    LOG_LINES=$(wc -l < "$LOG_FILE" | tr -d ' ')
fi

# Read checkpoint for the session that just compacted
# PreCompact writes the SID to /tmp/nexo-compacting-sid
TARGET_SID=""
if [ -f /tmp/nexo-compacting-sid ]; then
    TARGET_SID=$(cat /tmp/nexo-compacting-sid 2>/dev/null || echo "")
    rm -f /tmp/nexo-compacting-sid
fi

CHECKPOINT=""
if [ -f "$NEXO_DB" ]; then
    if [ -n "$TARGET_SID" ]; then
        # Read checkpoint for the specific session that compacted
        CHECKPOINT=$(sqlite3 "$NEXO_DB" "
            SELECT sid, task, task_status, active_files, current_goal,
                   decisions_summary, errors_found, reasoning_thread,
                   next_step, compaction_count
            FROM session_checkpoints
            WHERE sid = '$TARGET_SID'
        " 2>/dev/null || echo "")
    fi
    # Fallback: if no target SID or no checkpoint found, use latest
    if [ -z "$CHECKPOINT" ]; then
        CHECKPOINT=$(sqlite3 "$NEXO_DB" "
            SELECT sid, task, task_status, active_files, current_goal,
                   decisions_summary, errors_found, reasoning_thread,
                   next_step, compaction_count
            FROM session_checkpoints
            ORDER BY updated_at DESC LIMIT 1
        " 2>/dev/null || echo "")
    fi

    if [ -n "$CHECKPOINT" ]; then
        # Parse pipe-separated fields
        SID=$(echo "$CHECKPOINT" | cut -d'|' -f1)
        TASK=$(echo "$CHECKPOINT" | cut -d'|' -f2)
        TASK_STATUS=$(echo "$CHECKPOINT" | cut -d'|' -f3)
        ACTIVE_FILES=$(echo "$CHECKPOINT" | cut -d'|' -f4)
        CURRENT_GOAL=$(echo "$CHECKPOINT" | cut -d'|' -f5)
        DECISIONS=$(echo "$CHECKPOINT" | cut -d'|' -f6)
        ERRORS=$(echo "$CHECKPOINT" | cut -d'|' -f7)
        REASONING=$(echo "$CHECKPOINT" | cut -d'|' -f8)
        NEXT_STEP=$(echo "$CHECKPOINT" | cut -d'|' -f9)
        COMPACT_COUNT=$(echo "$CHECKPOINT" | cut -d'|' -f10)

        # Increment compaction count
        sqlite3 "$NEXO_DB" "
            UPDATE session_checkpoints
            SET compaction_count = compaction_count + 1, updated_at = datetime('now')
            WHERE sid = '$SID'
        " 2>/dev/null || true

        # Read diary draft for extra context
        DRAFT=$(sqlite3 "$NEXO_DB" "
            SELECT tasks_seen, last_context_hint
            FROM session_diary_draft
            WHERE sid = '$SID'
        " 2>/dev/null || echo "")

        TASKS_SEEN=""
        LAST_HINT=""
        if [ -n "$DRAFT" ]; then
            TASKS_SEEN=$(echo "$DRAFT" | cut -d'|' -f1)
            LAST_HINT=$(echo "$DRAFT" | cut -d'|' -f2)
        fi

        # Build Core Memory Block
        BLOCK="## SESSION CONTINUITY [auto-injected post-compaction #$((COMPACT_COUNT + 1))]"
        BLOCK="$BLOCK\n**Session:** $SID"
        BLOCK="$BLOCK\n**Task:** $TASK (status: $TASK_STATUS)"

        if [ -n "$CURRENT_GOAL" ] && [ "$CURRENT_GOAL" != "$TASK" ]; then
            BLOCK="$BLOCK\n**Goal:** $CURRENT_GOAL"
        fi

        if [ -n "$ACTIVE_FILES" ] && [ "$ACTIVE_FILES" != "[]" ]; then
            BLOCK="$BLOCK\n**Files:** $ACTIVE_FILES"
        fi

        if [ -n "$DECISIONS" ]; then
            BLOCK="$BLOCK\n**Decisions:** $DECISIONS"
        fi

        if [ -n "$ERRORS" ]; then
            BLOCK="$BLOCK\n**Errors:** $ERRORS"
        fi

        if [ -n "$NEXT_STEP" ]; then
            BLOCK="$BLOCK\n**Next:** $NEXT_STEP"
        fi

        if [ -n "$REASONING" ]; then
            BLOCK="$BLOCK\n**Context:** $REASONING"
        fi

        if [ -n "$LAST_HINT" ]; then
            BLOCK="$BLOCK\n**Last context:** $LAST_HINT"
        fi

        if [ -n "$TASKS_SEEN" ] && [ "$TASKS_SEEN" != "[]" ]; then
            BLOCK="$BLOCK\n**Session tasks so far:** $TASKS_SEEN"
        fi

        BLOCK="$BLOCK\n**Tool logs:** ${NEXO_HOME}/operations/tool-logs/${TODAY}.jsonl ($LOG_LINES entries)"
        BLOCK="$BLOCK\n\n**POST-COMPACTION INSTRUCTIONS:**"
        BLOCK="$BLOCK\n1. Call nexo_heartbeat with the SID above to reconnect with the session"
        BLOCK="$BLOCK\n2. If you need specific lost data, query tool logs with jq"
        BLOCK="$BLOCK\n3. Continue the task from where it left off — do NOT start from zero"
        BLOCK="$BLOCK\n4. MCP tools (nexo_*) have all persistent state"

        # Escape for JSON
        BLOCK_ESCAPED=$(echo -e "$BLOCK" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read()))")

        cat << HOOKEOF
{
  "systemMessage": $BLOCK_ESCAPED
}
HOOKEOF
    else
        # No checkpoint — fallback to basic message
        cat << 'HOOKEOF'
{
  "systemMessage": "Post-compaction: no prior checkpoint found. Call nexo_heartbeat to reconnect session state."
}
HOOKEOF
    fi
else
    cat << 'HOOKEOF'
{
  "systemMessage": "Post-compaction: nexo.db not found. Reconnect via nexo_startup."
}
HOOKEOF
fi
