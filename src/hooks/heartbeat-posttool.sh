#!/bin/bash
# NEXO PostToolUse hook — heartbeat enforcement checker
set -uo pipefail

INPUT=$(cat || true)
[ -z "$INPUT" ] && exit 0

NEXO_HOME="${NEXO_HOME:-$HOME/.nexo}"
HELPER=""
if [ -n "${NEXO_CODE:-}" ] && [ -f "${NEXO_CODE%/}/hooks/heartbeat-enforcement.py" ]; then
    HELPER="${NEXO_CODE%/}/hooks/heartbeat-enforcement.py"
elif [ -f "$NEXO_HOME/hooks/heartbeat-enforcement.py" ]; then
    HELPER="$NEXO_HOME/hooks/heartbeat-enforcement.py"
fi

[ -z "$HELPER" ] && exit 0
HEARTBEAT_MODE=post_tool python3 "$HELPER" <<< "$INPUT" 2>/dev/null || true
exit 0
