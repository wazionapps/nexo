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
MARKER_DIR="$NEXO_HOME/runtime/data/.tcc-approved"
LOG="$NEXO_HOME/runtime/logs/tcc-auto-approve.log"

mkdir -p "$MARKER_DIR" "$(dirname "$LOG")"

FAILED=0
APPROVED_VERSIONS=0
PYTHON_APPROVED=0

log_line() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"
}

approve_service() {
    local svc="$1"
    local client="$2"
    local output

    if output=$(sqlite3 "$TCC_DB" "
        INSERT OR REPLACE INTO access (service, client, client_type, auth_value, auth_reason, auth_version)
        VALUES ('$svc', '$client', 1, 2, 4, 1);
        " 2>&1); then
        return 0
    fi

    log_line "WARN: failed TCC approval service=$svc client=$client: ${output:-sqlite3 failed}"
    return 1
}

approve_client() {
    local label="$1"
    local client="$2"
    local marker="${3:-}"
    local failed=0

    log_line "Approving $label"

    for svc in "${SERVICES[@]}"; do
        if ! approve_service "$svc" "$client"; then
            failed=$((failed + 1))
        fi
    done

    if [ "$failed" -eq 0 ]; then
        [ -n "$marker" ] && touch "$marker"
        log_line "Done: $label — ${#SERVICES[@]} services approved"
        return 0
    fi

    log_line "FAILED: $label — $failed/${#SERVICES[@]} services failed"
    return 1
}

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

        if approve_client "Claude $version" "$bin_path" "$marker"; then
            APPROVED_VERSIONS=$((APPROVED_VERSIONS + 1))
        else
            FAILED=1
        fi
    done
fi

# Also approve Python from NEXO's venv (if it exists)
NEXO_CODE="${NEXO_CODE:-}"
if [ -n "$NEXO_CODE" ]; then
    PYTHON_BIN="$(dirname "$NEXO_CODE")/.venv/bin/python"
    if [ -e "$PYTHON_BIN" ]; then
        PYTHON_REAL=$(readlink -f "$PYTHON_BIN" 2>/dev/null || echo "$PYTHON_BIN")
        if approve_client "NEXO Python" "$PYTHON_REAL"; then
            PYTHON_APPROVED=1
        else
            FAILED=1
        fi
    fi
fi

if [ "$FAILED" -ne 0 ]; then
    echo "TCC auto-approve failed; see $LOG" >&2
    exit 1
fi

if [ "$APPROVED_VERSIONS" -eq 0 ] && [ "$PYTHON_APPROVED" -eq 0 ]; then
    echo "TCC auto-approve: nothing pending"
else
    echo "TCC auto-approve: approved $APPROVED_VERSIONS Claude version(s)"
fi
