#!/bin/bash
# NEXO SessionStart hook — runs when a new Claude Code session begins.
# Generates a session briefing from the last self-audit and cognitive stats.

NEXO_HOME="${NEXO_HOME:-$HOME/.nexo}"
BRIEFING_FILE="$NEXO_HOME/coordination/session-briefing.txt"

mkdir -p "$NEXO_HOME/coordination"

# Read last self-audit summary if available
AUDIT_SUMMARY=""
if [ -f "$NEXO_HOME/logs/self-audit-summary.json" ]; then
    AUDIT_SUMMARY=$(cat "$NEXO_HOME/logs/self-audit-summary.json" 2>/dev/null)
fi

# Read GitHub status if available
GITHUB_STATUS=""
if [ -f "$NEXO_HOME/github-status.json" ]; then
    GITHUB_STATUS=$(cat "$NEXO_HOME/github-status.json" 2>/dev/null)
fi

# Write briefing
cat > "$BRIEFING_FILE" << EOF
Session started: $(date '+%Y-%m-%d %H:%M:%S')
Self-audit: ${AUDIT_SUMMARY:-"no recent audit"}
GitHub: ${GITHUB_STATUS:-"no status available"}
EOF
