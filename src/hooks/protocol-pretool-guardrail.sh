#!/bin/bash
# NEXO PreToolUse hook — strict protocol blocking before writes/deletes

INPUT=$(cat || true)
[ -z "$INPUT" ] && exit 0

NEXO_CODE="${NEXO_CODE:-${HOME}/.nexo}"
NEXO_HOOK_PHASE=pre python3 "$NEXO_CODE/hook_guardrails.py" <<< "$INPUT"
exit $?
