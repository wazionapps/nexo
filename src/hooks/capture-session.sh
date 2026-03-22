#!/bin/bash
# NEXO PostToolUse hook — captures tool usage to session_buffer.jsonl
# This feeds the Sensory Register (Atkinson-Shiffrin Layer 1)

NEXO_HOME="${NEXO_HOME:-$HOME/.nexo}"
BUFFER="$NEXO_HOME/brain/session_buffer.jsonl"

mkdir -p "$NEXO_HOME/brain"

# Capture basic event: timestamp + tool name
TOOL_NAME="${CLAUDE_TOOL_NAME:-unknown}"
TS=$(date -u +"%Y-%m-%dT%H:%M:%S")

# Only log meaningful tool calls (skip reads, globs, greps)
case "$TOOL_NAME" in
    Read|Glob|Grep|LS|Bash) exit 0 ;;
esac

echo "{\"ts\":\"$TS\",\"tool\":\"$TOOL_NAME\",\"source\":\"hook\"}" >> "$BUFFER"
