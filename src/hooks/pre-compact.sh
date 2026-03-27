#!/bin/bash
# NEXO PreCompact Hook — Save checkpoint + inject preservation instructions
# This runs BEFORE Claude Code compacts. It:
# 1. Enriches the session checkpoint in SQLite with latest diary draft data
# 2. Injects a systemMessage telling the operator to save any WIP via MCP tools
set -euo pipefail

NEXO_HOME="${NEXO_HOME:-$HOME/.nexo}"
NEXO_DB="$NEXO_HOME/nexo.db"
TODAY=$(date +%Y-%m-%d)
LOG_FILE="$NEXO_HOME/operations/tool-logs/${TODAY}.jsonl"
LOG_LINES=0
if [ -f "$LOG_FILE" ]; then
    LOG_LINES=$(wc -l < "$LOG_FILE" | tr -d ' ')
fi

# Enrich checkpoint: copy diary draft context into checkpoint if exists
if [ -f "$NEXO_DB" ]; then
    # Get latest active session's diary draft
    LATEST_SID=$(sqlite3 "$NEXO_DB" "
        SELECT sid FROM sessions ORDER BY last_update_epoch DESC LIMIT 1
    " 2>/dev/null || echo "")

    if [ -n "$LATEST_SID" ]; then
        # Pull diary draft data into checkpoint
        sqlite3 "$NEXO_DB" "
            INSERT INTO session_checkpoints (sid, task, current_goal, updated_at)
            SELECT s.sid, s.task, COALESCE(d.last_context_hint, s.task), datetime('now')
            FROM sessions s
            LEFT JOIN session_diary_draft d ON d.sid = s.sid
            WHERE s.sid = '$LATEST_SID'
            ON CONFLICT(sid) DO UPDATE SET
                task = excluded.task,
                current_goal = CASE
                    WHEN excluded.current_goal != '' THEN excluded.current_goal
                    ELSE session_checkpoints.current_goal
                END,
                updated_at = datetime('now')
        " 2>/dev/null || true
    fi
fi

cat << HOOKEOF
{
  "systemMessage": "CONTEXT IS ABOUT TO BE COMPRESSED.\n\nOBLIGATORY ACTIONS BEFORE COMPACTION:\n1. Save critical state via MCP: nexo_checkpoint_save with current task, active files, decisions, errors, next step, and reasoning thread.\n2. If there is work in progress without a commit, save data via nexo_entity_create, nexo_preference_set, nexo_learning_add, nexo_followup_create.\n3. PERSISTENT TOOL LOGS: ${NEXO_HOME}/operations/tool-logs/${TODAY}.jsonl has ${LOG_LINES} entries.\n4. After compaction, the PostCompact hook will re-inject a Core Memory Block with the checkpoint.\n5. MCP tools (nexo_*) preserve all state — use them to recover context."
}
HOOKEOF
