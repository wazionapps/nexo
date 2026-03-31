#!/bin/bash
# NEXO TCC Auto-Approve — grants macOS permissions to new Claude Code versions.
#
# macOS only. On Linux this is a no-op (Linux doesn't have TCC).
# Runs at load to approve any new Claude versions that appeared.
#
# What it does:
#   1. Scans ~/.local/share/claude/versions/ for Claude binaries
#   2. For each new version, grants TCC access to Documents, Desktop, Downloads, etc.
#   3. Also approves the Python binary used by NEXO's venv
#   4. Tracks which versions have been approved to avoid re-processing
#
# Why: Claude Code updates frequently. Each new binary needs macOS permission
# grants or the user gets popup dialogs interrupting their work.

set -euo pipefail

# Linux: nothing to do
if [ "$(uname -s)" != "Darwin" ]; then
    exit 0
fi

NEXO_HOME="${NEXO_HOME:-$HOME/.nexo}"
TCC_DB="$HOME/Library/Application Support/com.apple.TCC/TCC.db"
VERSIONS_DIR="$HOME/.local/share/claude/versions"
MARKER_DIR="$NEXO_HOME/data/.tcc-approved"
LOG="$NEXO_HOME/logs/tcc-auto-approve.log"

mkdir -p "$MARKER_DIR" "$(dirname "$LOG")"

# TCC services Claude Code needs
SERVICES=(
    kTCCServiceSystemPolicyDocumentsFolder
    kTCCServiceSystemPolicyDesktopFolder
    kTCCServiceSystemPolicyDownloadsFolder
    kTCCServiceMediaLibrary
    kTCCServiceSystemPolicyNetworkVolumes
    kTCCServiceSystemPolicyAppData
    kTCCServiceFileProviderDomain
)

# Approve Claude versions
if [ -d "$VERSIONS_DIR" ]; then
    for bin_path in "$VERSIONS_DIR"/*; do
        [ ! -e "$bin_path" ] && continue
        version=$(basename "$bin_path")
        marker="$MARKER_DIR/$version"

        # Skip if already approved
        [ -f "$marker" ] && continue

        echo "$(date '+%Y-%m-%d %H:%M:%S') Approving Claude $version" >> "$LOG"

        for svc in "${SERVICES[@]}"; do
            sqlite3 "$TCC_DB" "
            INSERT OR REPLACE INTO access (service, client, client_type, auth_value, auth_reason, auth_version)
            VALUES ('$svc', '$bin_path', 1, 2, 4, 1);
            " 2>/dev/null
        done

        touch "$marker"
        echo "$(date '+%Y-%m-%d %H:%M:%S') Done: Claude $version — ${#SERVICES[@]} services approved" >> "$LOG"
    done
fi

# Also approve Python from NEXO's venv (if it exists)
NEXO_CODE="${NEXO_CODE:-}"
if [ -n "$NEXO_CODE" ]; then
    PYTHON_BIN="$(dirname "$NEXO_CODE")/.venv/bin/python"
    if [ -e "$PYTHON_BIN" ]; then
        PYTHON_REAL=$(readlink -f "$PYTHON_BIN" 2>/dev/null || echo "$PYTHON_BIN")
        for svc in "${SERVICES[@]}"; do
            sqlite3 "$TCC_DB" "
            INSERT OR REPLACE INTO access (service, client, client_type, auth_value, auth_reason, auth_version)
            VALUES ('$svc', '$PYTHON_REAL', 1, 2, 4, 1);
            " 2>/dev/null
        done
    fi
fi
