#!/bin/bash
# NEXO Stop hook — runs when Claude Code session ends.
# Captures a summary event to session_buffer for the Sensory Register.

NEXO_HOME="${NEXO_HOME:-$HOME/.nexo}"
BUFFER="$NEXO_HOME/brain/session_buffer.jsonl"

mkdir -p "$NEXO_HOME/brain"

TS=$(date -u +"%Y-%m-%dT%H:%M:%S")
echo "{\"ts\":\"$TS\",\"tasks\":[\"session ended\"],\"source\":\"hook-stop\"}" >> "$BUFFER"
