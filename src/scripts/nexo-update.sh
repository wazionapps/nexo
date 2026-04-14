#!/usr/bin/env bash
# nexo-update.sh — Thin wrapper around the canonical Python update core.
# Usage:
#   nexo-update.sh                    # pull from origin main
#   nexo-update.sh origin beta        # pull from origin beta

set -euo pipefail

REMOTE="${1:-origin}"
BRANCH="${2:-main}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RUNTIME_PYTHON="${NEXO_RUNTIME_PYTHON:-python3}"

export NEXO_CODE="${NEXO_CODE:-$SRC_DIR}"
export PYTHONPATH="$SRC_DIR${PYTHONPATH:+:$PYTHONPATH}"

"$RUNTIME_PYTHON" - "$REMOTE" "$BRANCH" <<'PY'
from __future__ import annotations

import sys

from plugins.update import handle_update


remote = sys.argv[1]
branch = sys.argv[2]
result = handle_update(remote=remote, branch=branch)
print(result)

raise SystemExit(1 if result.startswith(("ABORTED", "UPDATE FAILED")) else 0)
PY
