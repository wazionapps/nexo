#!/bin/bash
# NEXO PostToolUse hook — captures tool usage to session_buffer.jsonl
# Feeds the Sensory Register (Atkinson-Shiffrin Layer 1).
#
# IMPORTANT: Claude Code passes the tool name in a JSON payload over stdin,
# NOT as the $CLAUDE_TOOL_NAME env var. Earlier revisions of this hook
# assumed the env var existed and always wrote "unknown", masking the
# entire sensory-register stream. Do not reintroduce that pattern.

set -uo pipefail

NEXO_HOME="${NEXO_HOME:-$HOME/.nexo}"
BUFFER="$NEXO_HOME/brain/session_buffer.jsonl"
mkdir -p "$NEXO_HOME/brain"

INPUT=$(cat 2>/dev/null || true)
[ -z "$INPUT" ] && exit 0

# Extract tool_name from the stdin JSON payload. Fall back to env var for
# compatibility with any platform that still sets it; final fallback is an
# empty string, in which case we exit without writing so "unknown" noise
# never reaches the buffer.
TOOL_NAME=$(echo "$INPUT" \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('tool_name',''))" 2>/dev/null \
    || true)
if [ -z "$TOOL_NAME" ]; then
    TOOL_NAME="${CLAUDE_TOOL_NAME:-}"
fi
[ -z "$TOOL_NAME" ] && exit 0

# Skip high-frequency read-only tools: they add noise without signal.
# Bash / Write / Edit / MultiEdit / Task / MCP tools ARE kept — that is
# where real state change happens and where the sensory register matters.
case "$TOOL_NAME" in
    Read|Glob|Grep|LS|Skill|ToolSearch|TodoWrite) exit 0 ;;
esac

TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
# Escape embedded quotes / special chars in tool names (MCP names can be
# long and contain colons, underscores, and dashes).
ESCAPED_NAME=$(printf '%s' "$TOOL_NAME" \
    | python3 -c "import sys,json; print(json.dumps(sys.stdin.read().strip()))")
echo "{\"ts\":\"$TS\",\"tool\":$ESCAPED_NAME,\"source\":\"hook\"}" >> "$BUFFER"
