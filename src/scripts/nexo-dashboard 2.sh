#!/bin/bash
# ============================================================================
# NEXO Dashboard — Web UI at localhost:6174
# Schedule: keepAlive (persistent daemon, auto-restart on crash)
# ============================================================================
set -uo pipefail

NEXO_HOME="${NEXO_HOME:-$HOME/.nexo}"
NEXO_CODE="${NEXO_CODE:-$NEXO_HOME}"

# Find Python
if [ -x "$NEXO_HOME/.venv/bin/python3" ]; then
    PYTHON="$NEXO_HOME/.venv/bin/python3"
else
    PYTHON="python3"
fi

# Dashboard module location: prefer NEXO_CODE (repo), fallback NEXO_HOME (installed)
if [ -f "$NEXO_CODE/dashboard/app.py" ]; then
    DASH_DIR="$NEXO_CODE"
elif [ -f "$NEXO_HOME/dashboard/app.py" ]; then
    DASH_DIR="$NEXO_HOME"
else
    echo "Dashboard not found" >&2
    exit 1
fi

cd "$DASH_DIR"
exec "$PYTHON" -m dashboard.app --no-browser --port 6174
