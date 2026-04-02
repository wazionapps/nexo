#!/usr/bin/env bash
# nexo-update.sh — Standalone NEXO update script
# Same logic as the MCP tool but usable when the server itself needs updating.
#
# Usage:
#   nexo-update.sh                    # pull from origin main
#   nexo-update.sh origin beta        # pull from origin beta
#   NEXO_HOME=/path nexo-update.sh    # custom NEXO_HOME

set -euo pipefail

# --- Configuration ---
REMOTE="${1:-origin}"
BRANCH="${2:-main}"
NEXO_HOME="${NEXO_HOME:-$HOME/.nexo}"

# Determine repo directory: script is at src/scripts/, repo root is ../../
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
SRC_DIR="$REPO_DIR/src"
PACKAGE_JSON="$REPO_DIR/package.json"

# --- Helpers ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[nexo-update]${NC} $*"; }
warn() { echo -e "${YELLOW}[nexo-update]${NC} $*"; }
err()  { echo -e "${RED}[nexo-update]${NC} $*" >&2; }

read_version() {
    python3 -c "import json; print(json.load(open('$PACKAGE_JSON')).get('version','unknown'))" 2>/dev/null || echo "unknown"
}

# --- Check if this is a git repo ---
if [ ! -d "$REPO_DIR/.git" ] && [ ! -f "$REPO_DIR/.git" ]; then
    err "ABORTED: Not a git repository at $REPO_DIR"
    err "For packaged installs, use: npm update -g nexo-brain"
    exit 1
fi

# --- Step 1: Check for uncommitted changes in entire worktree ---
log "Checking for uncommitted changes..."
cd "$REPO_DIR"

if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
    err "ABORTED: Uncommitted changes in worktree"
    git status --short
    exit 1
fi
log "Working tree clean."

# Record current state
OLD_VERSION="$(read_version)"
OLD_COMMIT="$(git rev-parse HEAD)"
REQ_FILE="$SRC_DIR/requirements.txt"
OLD_REQ_HASH=""
if [ -f "$REQ_FILE" ]; then
    OLD_REQ_HASH="$(shasum -a 256 "$REQ_FILE" | cut -d' ' -f1)"
fi
log "Current: v${OLD_VERSION} (${OLD_COMMIT:0:8})"

# --- Step 2: Backup databases ---
TIMESTAMP="$(date +%Y-%m-%d-%H%M)"
BACKUP_DIR="$NEXO_HOME/backups/pre-update-$TIMESTAMP"

backup_dbs() {
    local found=0
    # Check data/, NEXO_HOME root, and src/ for .db files
    for dir in "$NEXO_HOME/data" "$NEXO_HOME" "$SRC_DIR"; do
        if [ -d "$dir" ]; then
            for db in "$dir"/*.db; do
                [ -f "$db" ] || continue
                found=1
                mkdir -p "$BACKUP_DIR"
                cp "$db" "$BACKUP_DIR/$(basename "$db")"
                log "  Backed up: $(basename "$db")"
            done
        fi
    done
    if [ "$found" -eq 0 ]; then
        log "  No databases found to backup."
    fi
}

log "Backing up databases..."
backup_dbs

# --- Step 3: git pull ---
log "Pulling from ${REMOTE}/${BRANCH}..."
PULL_OUTPUT="$(git pull "$REMOTE" "$BRANCH" 2>&1)" || {
    err "git pull failed:"
    err "$PULL_OUTPUT"
    exit 1
}
log "$PULL_OUTPUT"

if echo "$PULL_OUTPUT" | grep -q "Already up to date"; then
    log "Already up to date (v${OLD_VERSION}). Done."
    exit 0
fi

# --- Step 4: Check version ---
NEW_VERSION="$(read_version)"
log "New version: v${NEW_VERSION}"

# --- Step 4b: Reinstall Python dependencies if requirements.txt changed ---
NEW_REQ_HASH=""
if [ -f "$REQ_FILE" ]; then
    NEW_REQ_HASH="$(shasum -a 256 "$REQ_FILE" | cut -d' ' -f1)"
fi

DEPS_CHANGED=false
if [ "$OLD_REQ_HASH" != "$NEW_REQ_HASH" ]; then
    DEPS_CHANGED=true
fi

reinstall_pip_deps() {
    local VENV_PIP="$NEXO_HOME/.venv/bin/pip"
    if [ -f "$REQ_FILE" ]; then
        if [ -x "$VENV_PIP" ]; then
            "$VENV_PIP" install --quiet -r "$REQ_FILE" || return 1
        else
            python3 -m pip install --quiet -r "$REQ_FILE" --break-system-packages 2>/dev/null || return 1
        fi
    fi
    return 0
}

if [ "$DEPS_CHANGED" = true ] || [ "$OLD_VERSION" != "$NEW_VERSION" ]; then
    log "Reinstalling Python dependencies..."
    if ! reinstall_pip_deps; then
        err "pip install failed! Rolling back..."
        git reset --hard "$OLD_COMMIT"
        reinstall_pip_deps || warn "pip rollback also had issues"
        if [ -d "$BACKUP_DIR" ]; then
            for db in "$BACKUP_DIR"/*.db; do
                [ -f "$db" ] || continue
                BASENAME="$(basename "$db")"
                for candidate in "$NEXO_HOME/data/$BASENAME" "$NEXO_HOME/$BASENAME" "$SRC_DIR/$BASENAME"; do
                    if [ -f "$candidate" ]; then
                        cp "$db" "$candidate"
                        warn "  Restored: $BASENAME"
                        break
                    fi
                done
            done
        fi
        err "Rolled back to ${OLD_COMMIT:0:8}. Databases restored."
        exit 1
    fi
    log "Python dependencies updated."
fi

# --- Step 5: Run migrations if version changed ---
if [ "$OLD_VERSION" != "$NEW_VERSION" ]; then
    log "Version changed: ${OLD_VERSION} -> ${NEW_VERSION}"
    log "Running migrations..."
    if ! (cd "$SRC_DIR" && python3 -c "import db; db.init_db()" 2>&1); then
        err "Migration failed! Rolling back..."
        git reset --hard "$OLD_COMMIT"
        # Reinstall pip deps from restored old requirements.txt
        reinstall_pip_deps || warn "pip rollback also had issues"
        # Restore DB backups
        if [ -d "$BACKUP_DIR" ]; then
            for db in "$BACKUP_DIR"/*.db; do
                [ -f "$db" ] || continue
                BASENAME="$(basename "$db")"
                for candidate in "$NEXO_HOME/data/$BASENAME" "$NEXO_HOME/$BASENAME" "$SRC_DIR/$BASENAME"; do
                    if [ -f "$candidate" ]; then
                        cp "$db" "$candidate"
                        warn "  Restored: $BASENAME"
                        break
                    fi
                done
            done
        fi
        err "Rolled back to ${OLD_COMMIT:0:8}. Databases and deps restored."
        exit 1
    fi
    log "Migrations applied."
else
    log "Version unchanged (${OLD_VERSION}), skipping migrations."
fi

# --- Step 6: Verify import ---
log "Verifying server.py import..."
if ! (cd "$SRC_DIR" && python3 -c "import server" 2>&1); then
    err "Import verification failed! Rolling back..."
    git reset --hard "$OLD_COMMIT"
    # Reinstall pip deps from restored old requirements.txt
    reinstall_pip_deps || warn "pip rollback also had issues"
    if [ -d "$BACKUP_DIR" ]; then
        for db in "$BACKUP_DIR"/*.db; do
            [ -f "$db" ] || continue
            BASENAME="$(basename "$db")"
            for candidate in "$NEXO_HOME/data/$BASENAME" "$NEXO_HOME/$BASENAME" "$SRC_DIR/$BASENAME"; do
                if [ -f "$candidate" ]; then
                    cp "$db" "$candidate"
                    warn "  Restored: $BASENAME"
                    break
                fi
            done
        done
    fi
    err "Rolled back to ${OLD_COMMIT:0:8}. Databases and deps restored."
    exit 1
fi

# --- Step 7: Sync hooks to NEXO_HOME ---
HOOKS_SRC="$SRC_DIR/hooks"
HOOKS_DEST="$NEXO_HOME/hooks"
if [ -d "$HOOKS_SRC" ]; then
    mkdir -p "$HOOKS_DEST"
    SYNCED=0
    for hook in "$HOOKS_SRC"/*.sh; do
        [ -f "$hook" ] || continue
        cp "$hook" "$HOOKS_DEST/$(basename "$hook")"
        chmod 755 "$HOOKS_DEST/$(basename "$hook")"
        SYNCED=$((SYNCED + 1))
    done
    if [ "$SYNCED" -gt 0 ]; then
        log "Synced $SYNCED hook(s) to $HOOKS_DEST"
    fi
fi

# --- Done ---
echo ""
log "========================================="
log " UPDATE SUCCESSFUL"
if [ "$OLD_VERSION" != "$NEW_VERSION" ]; then
    log " Version: ${OLD_VERSION} -> ${NEW_VERSION}"
else
    log " Version: ${OLD_VERSION} (unchanged)"
fi
log " Branch: ${REMOTE}/${BRANCH}"
log " Backup: ${BACKUP_DIR}"
log "========================================="
echo ""
warn "MCP server restart needed to load new code."
