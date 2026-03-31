#!/bin/bash
# nexo-inbox-hook.sh — PostToolUse: automatic inter-terminal inbox check (D+)
#
# Zero output when no messages = zero tokens consumed in Claude's context.
# Reads SQLite directly (no MCP overhead). Write-only: INSERT OR IGNORE for mark-as-read.
# Debounce: skips if last check was <2 seconds ago.

INPUT=$(cat)

# 1. Skip read-only tools (same logic as capture-tool-logs.sh)
TOOL_NAME=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('tool_name',''))" 2>/dev/null)
case "$TOOL_NAME" in
    Read|Grep|Glob|LS|Skill|ToolSearch|Agent) exit 0 ;;
esac

# 2. Extract Claude Code session_id
CLAUDE_SID=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('session_id',''))" 2>/dev/null)
[ -z "$CLAUDE_SID" ] && exit 0

# 3. Debounce: skip if last check <2s ago
DEBOUNCE_FILE="/tmp/nexo-inbox-ts-${CLAUDE_SID}"
NOW=$(date +%s)
LAST=$(cat "$DEBOUNCE_FILE" 2>/dev/null || echo 0)
DIFF=$((NOW - LAST))
[ "$DIFF" -lt 2 ] && exit 0
echo "$NOW" > "$DEBOUNCE_FILE"

# 4. Find NEXO SID mapped to this Claude session_id
NEXO_HOME="${NEXO_HOME:-$HOME/.nexo}"
DB="$NEXO_HOME/data/nexo.db"
[ -f "$DB" ] || exit 0

NEXO_SID=$(sqlite3 "$DB" "SELECT sid FROM sessions WHERE claude_session_id = '${CLAUDE_SID}' AND last_update_epoch > (strftime('%s','now') - 900) ORDER BY last_update_epoch DESC LIMIT 1;" 2>/dev/null)
[ -z "$NEXO_SID" ] && exit 0

# 5. Check inbox — messages addressed to this session or broadcast
MESSAGES=$(sqlite3 -separator '|' "$DB" "
    SELECT m.id, m.from_sid, m.text FROM messages m
    WHERE (m.to_sid = 'all' OR m.to_sid = '${NEXO_SID}')
    AND m.from_sid != '${NEXO_SID}'
    AND m.id NOT IN (SELECT message_id FROM message_reads WHERE sid = '${NEXO_SID}')
    LIMIT 5;
" 2>/dev/null)

# 6. Check pending questions
QUESTIONS=$(sqlite3 -separator '|' "$DB" "
    SELECT qid, from_sid, question FROM questions
    WHERE to_sid = '${NEXO_SID}' AND answer IS NULL
    LIMIT 3;
" 2>/dev/null)

# 7. If empty → silent exit (0 tokens consumed)
[ -z "$MESSAGES" ] && [ -z "$QUESTIONS" ] && exit 0

# 8. Format and output (injected into Claude's context)
echo ""
echo "📨 INTER-TERMINAL MESSAGE (auto-detected):"

if [ -n "$MESSAGES" ]; then
    echo "$MESSAGES" | while IFS='|' read -r mid from text; do
        echo "  [$from]: $text"
        # Mark as read (lightweight INSERT, WAL mode, no lock contention)
        sqlite3 "$DB" "INSERT OR IGNORE INTO message_reads (message_id, sid) VALUES ('${mid}', '${NEXO_SID}');" 2>/dev/null
    done
fi

if [ -n "$QUESTIONS" ]; then
    echo "  ⚠ PREGUNTAS de otra terminal — responder con nexo_answer:"
    echo "$QUESTIONS" | while IFS='|' read -r qid from question; do
        echo "  Q[$qid] de [$from]: $question"
    done
fi

exit 0
