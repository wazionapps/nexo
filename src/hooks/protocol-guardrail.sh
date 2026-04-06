#!/bin/bash
# NEXO PostToolUse hook — conditioned file discipline guardrail

INPUT=$(cat || true)
[ -z "$INPUT" ] && exit 0

NEXO_CODE="${NEXO_CODE:-${HOME}/.nexo}"
python3 "$NEXO_CODE/hook_guardrails.py" <<< "$INPUT" 2>/dev/null || true

exit 0
