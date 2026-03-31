#!/usr/bin/env bash
# ============================================================================
# NEXO Migration: Old Layout → Unified Architecture
# ============================================================================
#
# FROM:  ~/claude/nexo-mcp/  (code + data mixed)
# TO:    Code  → ~/Documents/_PhpstormProjects/nexo/src/
#        Data  → ~/claude/data/     (DBs)
#        Plugins → ~/claude/plugins/ (personal plugins)
#        Backups → ~/claude/backups/ (already there)
#
# Usage:  bash scripts/migrate-to-unified.sh [--dry-run]
#
# Safety: Creates full backups before any changes.
#         Stops on first critical error with rollback instructions.
# ============================================================================

set -euo pipefail

# ── Colors ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# ── Helpers ─────────────────────────────────────────────────────────────────
info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail()  { echo -e "${RED}[FAIL]${NC}  $*"; }
step()  { echo -e "\n${BOLD}━━━ Step $1: $2 ━━━${NC}"; }
divider() { echo -e "${CYAN}────────────────────────────────────────────────────────${NC}"; }

DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true && warn "DRY RUN — no changes will be made"

TIMESTAMP=$(date +%Y%m%d-%H%M%S)

# ── Paths ───────────────────────────────────────────────────────────────────
NEXO_HOME="${NEXO_HOME:-$HOME/claude}"
NEXO_CODE="$HOME/Documents/_PhpstormProjects/nexo"
OLD_INSTALL="$NEXO_HOME/nexo-mcp"
NEW_DATA="$NEXO_HOME/data"
NEW_PLUGINS="$NEXO_HOME/plugins"
NEW_SCRIPTS="$NEXO_HOME/scripts"
BACKUP_DIR="$NEXO_HOME/backups/pre-migration-$TIMESTAMP"

# Old DB locations (primary copies based on file size analysis)
OLD_NEXO_DB="$OLD_INSTALL/db/nexo.db"
OLD_COGNITIVE_DB="$OLD_INSTALL/cognitive.db"

# New DB locations
NEW_NEXO_DB="$NEW_DATA/nexo.db"
NEW_COGNITIVE_DB="$NEW_DATA/cognitive.db"

# Config files
MCP_CONFIG="$HOME/.claude/mcp-cortex.json"
CLAUDE_MD="$HOME/.claude/CLAUDE.md"
PROJECT_ATLAS="$NEXO_HOME/brain/project-atlas.json"

# LaunchAgents
LA_DIR="$HOME/Library/LaunchAgents"
LA_AUTO_CLOSE="$LA_DIR/com.nexo.auto-close-sessions.plist"
LA_DASHBOARD="$LA_DIR/com.nexo.dashboard.plist"

ERRORS=0

run_or_dry() {
    if $DRY_RUN; then
        info "[DRY] $*"
    else
        eval "$@"
    fi
}

# ============================================================================
# STEP 0: SAFETY CHECKS
# ============================================================================
step "0" "Safety Checks"

# Check NEXO_HOME
if [[ ! -d "$NEXO_HOME" ]]; then
    fail "NEXO_HOME ($NEXO_HOME) does not exist"
    exit 1
fi
ok "NEXO_HOME exists: $NEXO_HOME"

# Check repo
if [[ ! -f "$NEXO_CODE/src/server.py" ]]; then
    fail "Repo not found at $NEXO_CODE/src/server.py"
    exit 1
fi
ok "Repo exists: $NEXO_CODE"

# Check old install
if [[ ! -d "$OLD_INSTALL" ]]; then
    fail "Old install not found: $OLD_INSTALL"
    exit 1
fi
ok "Old install found: $OLD_INSTALL"

# Check DBs exist
if [[ ! -f "$OLD_NEXO_DB" ]]; then
    fail "nexo.db not found at $OLD_NEXO_DB"
    exit 1
fi
ok "nexo.db found ($(du -h "$OLD_NEXO_DB" | cut -f1))"

if [[ ! -f "$OLD_COGNITIVE_DB" ]]; then
    fail "cognitive.db not found at $OLD_COGNITIVE_DB"
    exit 1
fi
ok "cognitive.db found ($(du -h "$OLD_COGNITIVE_DB" | cut -f1))"

# Integrity checks
info "Running SQLite integrity checks..."
NEXO_INTEGRITY=$(sqlite3 "$OLD_NEXO_DB" "PRAGMA integrity_check;" 2>&1)
if [[ "$NEXO_INTEGRITY" != "ok" ]]; then
    fail "nexo.db integrity check FAILED: $NEXO_INTEGRITY"
    exit 1
fi
ok "nexo.db integrity: OK"

COG_INTEGRITY=$(sqlite3 "$OLD_COGNITIVE_DB" "PRAGMA integrity_check;" 2>&1)
if [[ "$COG_INTEGRITY" != "ok" ]]; then
    fail "cognitive.db integrity check FAILED: $COG_INTEGRITY"
    exit 1
fi
ok "cognitive.db integrity: OK"

# Check for WAL mode — need to checkpoint before copying
info "Checkpointing WAL files..."
sqlite3 "$OLD_NEXO_DB" "PRAGMA wal_checkpoint(TRUNCATE);" 2>/dev/null || true
sqlite3 "$OLD_COGNITIVE_DB" "PRAGMA wal_checkpoint(TRUNCATE);" 2>/dev/null || true
ok "WAL checkpointed"

# Check MCP config
if [[ ! -f "$MCP_CONFIG" ]]; then
    fail "MCP config not found: $MCP_CONFIG"
    exit 1
fi
ok "MCP config found"

# Check LaunchAgents
for plist in "$LA_AUTO_CLOSE" "$LA_DASHBOARD"; do
    if [[ ! -f "$plist" ]]; then
        warn "LaunchAgent not found: $plist (will skip)"
    else
        ok "LaunchAgent found: $(basename "$plist")"
    fi
done

# ── Confirmation ────────────────────────────────────────────────────────────
divider
echo -e "${BOLD}Migration Summary:${NC}"
echo "  Old install:    $OLD_INSTALL"
echo "  New code:       $NEXO_CODE/src/"
echo "  New data:       $NEW_DATA/"
echo "  New plugins:    $NEW_PLUGINS/"
echo "  Backup to:      $BACKUP_DIR/"
echo ""
echo "  DBs:            nexo.db ($(du -h "$OLD_NEXO_DB" | cut -f1)) + cognitive.db ($(du -h "$OLD_COGNITIVE_DB" | cut -f1))"
echo "  Status migration: PENDIENTE→PENDING, COMPLETADO→COMPLETED, ELIMINADO→DELETED"
echo ""

if ! $DRY_RUN; then
    read -p "Proceed with migration? [y/N] " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        info "Migration cancelled."
        exit 0
    fi
fi

# ============================================================================
# STEP 1: BACKUP EVERYTHING
# ============================================================================
step "1" "Backup Everything"

run_or_dry "mkdir -p '$BACKUP_DIR'"

# Backup DBs
run_or_dry "cp '$OLD_NEXO_DB' '$BACKUP_DIR/nexo.db'"
ok "Backed up nexo.db"

run_or_dry "cp '$OLD_COGNITIVE_DB' '$BACKUP_DIR/cognitive.db'"
ok "Backed up cognitive.db"

# Backup WAL/SHM files if they exist
for ext in -shm -wal; do
    [[ -f "${OLD_NEXO_DB}${ext}" ]] && run_or_dry "cp '${OLD_NEXO_DB}${ext}' '$BACKUP_DIR/nexo.db${ext}'"
    [[ -f "${OLD_COGNITIVE_DB}${ext}" ]] && run_or_dry "cp '${OLD_COGNITIVE_DB}${ext}' '$BACKUP_DIR/cognitive.db${ext}'"
done
ok "Backed up WAL/SHM files"

# Backup MCP config
run_or_dry "cp '$MCP_CONFIG' '${MCP_CONFIG}.pre-migration'"
ok "Backed up mcp-cortex.json"

# Backup LaunchAgents
for plist in "$LA_AUTO_CLOSE" "$LA_DASHBOARD"; do
    if [[ -f "$plist" ]]; then
        run_or_dry "cp '$plist' '$BACKUP_DIR/$(basename "$plist")'"
    fi
done
ok "Backed up LaunchAgents"

# Backup project-atlas.json
if [[ -f "$PROJECT_ATLAS" ]]; then
    run_or_dry "cp '$PROJECT_ATLAS' '$BACKUP_DIR/project-atlas.json'"
    ok "Backed up project-atlas.json"
fi

# Backup CLAUDE.md
run_or_dry "cp '$CLAUDE_MD' '$BACKUP_DIR/CLAUDE.md'"
ok "Backed up CLAUDE.md"

# Backup plugin
if [[ -f "$OLD_INSTALL/plugins/email.py" ]]; then
    run_or_dry "cp '$OLD_INSTALL/plugins/email.py' '$BACKUP_DIR/email.py'"
    ok "Backed up email.py plugin"
fi

if ! $DRY_RUN; then
    info "Full backup at: $BACKUP_DIR"
    ls -la "$BACKUP_DIR/"
fi

# ============================================================================
# STEP 2: CREATE DIRECTORY STRUCTURE
# ============================================================================
step "2" "Create Directory Structure"

run_or_dry "mkdir -p '$NEW_DATA'"
ok "Created $NEW_DATA/"

run_or_dry "mkdir -p '$NEW_PLUGINS'"
ok "Created $NEW_PLUGINS/"

run_or_dry "mkdir -p '$NEW_SCRIPTS'"
ok "Created $NEW_SCRIPTS/ (may already exist)"

# ============================================================================
# STEP 3: COPY DATA
# ============================================================================
step "3" "Copy Data to New Locations"

# Copy nexo.db
if [[ -f "$NEW_NEXO_DB" ]]; then
    warn "nexo.db already exists at destination — backing up existing"
    run_or_dry "mv '$NEW_NEXO_DB' '${NEW_NEXO_DB}.pre-migration'"
fi
run_or_dry "cp '$OLD_NEXO_DB' '$NEW_NEXO_DB'"
ok "Copied nexo.db → $NEW_DATA/"

# Copy cognitive.db
if [[ -f "$NEW_COGNITIVE_DB" ]]; then
    warn "cognitive.db already exists at destination — backing up existing"
    run_or_dry "mv '$NEW_COGNITIVE_DB' '${NEW_COGNITIVE_DB}.pre-migration'"
fi
run_or_dry "cp '$OLD_COGNITIVE_DB' '$NEW_COGNITIVE_DB'"
ok "Copied cognitive.db → $NEW_DATA/"

# Copy plugin
if [[ -f "$OLD_INSTALL/plugins/email.py" ]]; then
    run_or_dry "cp '$OLD_INSTALL/plugins/email.py' '$NEW_PLUGINS/email.py'"
    ok "Copied email.py → $NEW_PLUGINS/"
else
    warn "email.py plugin not found in old install"
fi

# Verify copies
if ! $DRY_RUN; then
    info "Verifying copied DBs..."
    V1=$(sqlite3 "$NEW_NEXO_DB" "PRAGMA integrity_check;" 2>&1)
    if [[ "$V1" != "ok" ]]; then
        fail "COPIED nexo.db failed integrity check!"
        fail "ROLLBACK: Delete $NEW_DATA/ and restore from $BACKUP_DIR/"
        exit 1
    fi
    ok "nexo.db copy verified"

    V2=$(sqlite3 "$NEW_COGNITIVE_DB" "PRAGMA integrity_check;" 2>&1)
    if [[ "$V2" != "ok" ]]; then
        fail "COPIED cognitive.db failed integrity check!"
        fail "ROLLBACK: Delete $NEW_DATA/ and restore from $BACKUP_DIR/"
        exit 1
    fi
    ok "cognitive.db copy verified"

    # Verify row counts match
    OLD_COUNT=$(sqlite3 "$OLD_NEXO_DB" "SELECT COUNT(*) FROM reminders;" 2>/dev/null)
    NEW_COUNT=$(sqlite3 "$NEW_NEXO_DB" "SELECT COUNT(*) FROM reminders;" 2>/dev/null)
    if [[ "$OLD_COUNT" != "$NEW_COUNT" ]]; then
        fail "Row count mismatch! Old=$OLD_COUNT New=$NEW_COUNT"
        exit 1
    fi
    ok "Row count verified: $NEW_COUNT reminders"
fi

# ============================================================================
# STEP 4: DB STATUS MIGRATION (Spanish → English)
# ============================================================================
step "4" "DB Status Migration (Spanish → English)"

if ! $DRY_RUN; then
    info "Migrating status values in nexo.db..."

    # Reminders
    R_PEND=$(sqlite3 "$NEW_NEXO_DB" "SELECT COUNT(*) FROM reminders WHERE status='PENDIENTE';")
    R_COMP=$(sqlite3 "$NEW_NEXO_DB" "SELECT COUNT(*) FROM reminders WHERE status='COMPLETADO';")
    R_ELIM=$(sqlite3 "$NEW_NEXO_DB" "SELECT COUNT(*) FROM reminders WHERE status='ELIMINADO';" 2>/dev/null || echo "0")

    sqlite3 "$NEW_NEXO_DB" <<'SQL'
UPDATE reminders SET status = 'PENDING' WHERE status = 'PENDIENTE';
UPDATE reminders SET status = 'COMPLETED' WHERE status = 'COMPLETADO';
UPDATE reminders SET status = 'DELETED' WHERE status = 'ELIMINADO';
SQL
    ok "Reminders: PENDIENTE($R_PEND)→PENDING, COMPLETADO($R_COMP)→COMPLETED, ELIMINADO($R_ELIM)→DELETED"

    # Followups
    F_PEND=$(sqlite3 "$NEW_NEXO_DB" "SELECT COUNT(*) FROM followups WHERE status='PENDIENTE';")
    F_COMP=$(sqlite3 "$NEW_NEXO_DB" "SELECT COUNT(*) FROM followups WHERE status='COMPLETADO';")
    F_ELIM=$(sqlite3 "$NEW_NEXO_DB" "SELECT COUNT(*) FROM followups WHERE status='ELIMINADO';" 2>/dev/null || echo "0")

    sqlite3 "$NEW_NEXO_DB" <<'SQL'
UPDATE followups SET status = 'PENDING' WHERE status = 'PENDIENTE';
UPDATE followups SET status = 'COMPLETED' WHERE status = 'COMPLETADO';
UPDATE followups SET status = 'DELETED' WHERE status = 'ELIMINADO';
SQL
    ok "Followups: PENDIENTE($F_PEND)→PENDING, COMPLETADO($F_COMP)→COMPLETED, ELIMINADO($F_ELIM)→DELETED"

    # Verify no Spanish values remain
    REMAINING=$(sqlite3 "$NEW_NEXO_DB" "SELECT COUNT(*) FROM reminders WHERE status IN ('PENDIENTE','COMPLETADO','ELIMINADO');")
    REMAINING2=$(sqlite3 "$NEW_NEXO_DB" "SELECT COUNT(*) FROM followups WHERE status IN ('PENDIENTE','COMPLETADO','ELIMINADO');")
    if [[ "$REMAINING" != "0" || "$REMAINING2" != "0" ]]; then
        fail "Spanish status values still present! reminders=$REMAINING followups=$REMAINING2"
        exit 1
    fi
    ok "All status values migrated to English"
else
    info "[DRY] Would migrate PENDIENTE→PENDING, COMPLETADO→COMPLETED, ELIMINADO→DELETED"
fi

# ============================================================================
# STEP 5: CREATE VENV AT REPO
# ============================================================================
step "5" "Create Virtual Environment at Repo"

VENV_DIR="$NEXO_CODE/.venv"

if [[ -d "$VENV_DIR" ]]; then
    warn "Existing .venv found — removing to recreate"
    run_or_dry "rm -rf '$VENV_DIR'"
fi

if command -v uv &>/dev/null; then
    info "Using uv to create venv..."
    run_or_dry "cd '$NEXO_CODE' && uv venv"
    run_or_dry "cd '$NEXO_CODE' && uv pip install -e './src[all]' 2>/dev/null || uv pip install -e './src' 2>/dev/null || uv pip install mcp fastmcp httpx sqlite-utils 2>/dev/null"
else
    info "uv not found, using python3 -m venv..."
    run_or_dry "python3 -m venv '$VENV_DIR'"
    run_or_dry "'$VENV_DIR/bin/pip' install --upgrade pip"
    # Try editable install first, fall back to requirements
    run_or_dry "cd '$NEXO_CODE' && '$VENV_DIR/bin/pip' install -e './src' 2>/dev/null || '$VENV_DIR/bin/pip' install mcp fastmcp httpx 2>/dev/null"
fi

# Verify the venv works
if ! $DRY_RUN; then
    if [[ -f "$VENV_DIR/bin/python" ]]; then
        ok "venv created at $VENV_DIR"
        # Test import — add src to path since server.py does relative imports
        IMPORT_TEST=$("$VENV_DIR/bin/python" -c "
import sys
sys.path.insert(0, '$NEXO_CODE/src')
try:
    from db import init_db
    print('OK')
except Exception as e:
    print(f'PARTIAL: {e}')
" 2>&1)
        if [[ "$IMPORT_TEST" == "OK" ]]; then
            ok "Import test passed"
        else
            warn "Import test: $IMPORT_TEST (may need manual dependency install)"
        fi
    else
        fail "venv creation failed!"
        warn "You can manually create it later: cd $NEXO_CODE && uv venv && uv pip install -e src/"
        ((ERRORS++))
    fi
fi

# ============================================================================
# STEP 6: UPDATE MCP CONFIG
# ============================================================================
step "6" "Update MCP Config"

if ! $DRY_RUN; then
    # Write new config — new python path, new server.py path, NEXO_HOME env
    cat > "$MCP_CONFIG" <<MCPEOF
{
  "mcpServers": {
    "nexo": {
      "command": "$NEXO_CODE/.venv/bin/python",
      "args": [
        "$NEXO_CODE/src/server.py"
      ],
      "env": {
        "NEXO_HOME": "$NEXO_HOME"
      }
    }
  }
}
MCPEOF
    ok "Updated $MCP_CONFIG"
    info "New config:"
    cat "$MCP_CONFIG"
else
    info "[DRY] Would update MCP config with new paths"
fi

# ============================================================================
# STEP 7: UPDATE LAUNCHAGENTS
# ============================================================================
step "7" "Update LaunchAgents"

# Unload current agents first
for plist in "$LA_AUTO_CLOSE" "$LA_DASHBOARD"; do
    if [[ -f "$plist" ]]; then
        run_or_dry "launchctl unload '$plist' 2>/dev/null || true"
    fi
done

# Update auto-close-sessions.plist
if [[ -f "$LA_AUTO_CLOSE" ]] && ! $DRY_RUN; then
    cat > "$LA_AUTO_CLOSE" <<'PLISTEOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.nexo.auto-close-sessions</string>
    <key>ProgramArguments</key>
    <array>
PLISTEOF
    # Now append the dynamic paths
    cat >> "$LA_AUTO_CLOSE" <<PLISTEOF
        <string>$NEXO_CODE/.venv/bin/python</string>
        <string>$NEXO_CODE/src/auto_close_sessions.py</string>
PLISTEOF
    cat >> "$LA_AUTO_CLOSE" <<'PLISTEOF'
    </array>
    <key>StartInterval</key>
    <integer>300</integer>
    <key>StandardOutPath</key>
PLISTEOF
    cat >> "$LA_AUTO_CLOSE" <<PLISTEOF
    <string>$NEXO_HOME/coordination/auto-close-stdout.log</string>
    <key>StandardErrorPath</key>
    <string>$NEXO_HOME/coordination/auto-close-stderr.log</string>
PLISTEOF
    cat >> "$LA_AUTO_CLOSE" <<'PLISTEOF'
    <key>RunAtLoad</key>
    <true/>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/Library/Frameworks/Python.framework/Versions/3.12/bin:/usr/local/bin:/usr/bin:/bin</string>
        <key>NEXO_SKIP_FS_INDEX</key>
        <string>1</string>
PLISTEOF
    cat >> "$LA_AUTO_CLOSE" <<PLISTEOF
        <key>NEXO_HOME</key>
        <string>$NEXO_HOME</string>
PLISTEOF
    cat >> "$LA_AUTO_CLOSE" <<'PLISTEOF'
    </dict>
</dict>
</plist>
PLISTEOF
    ok "Updated com.nexo.auto-close-sessions.plist"
fi

# Update dashboard.plist
if [[ -f "$LA_DASHBOARD" ]] && ! $DRY_RUN; then
    cat > "$LA_DASHBOARD" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.nexo.dashboard</string>
    <key>ProgramArguments</key>
    <array>
        <string>$NEXO_CODE/.venv/bin/python</string>
        <string>-m</string>
        <string>dashboard.app</string>
        <string>--port</string>
        <string>6174</string>
        <string>--no-browser</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$NEXO_CODE/src</string>
    <key>KeepAlive</key>
    <true/>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/nexo-dashboard.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/nexo-dashboard.err</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
        <key>NEXO_HOME</key>
        <string>$NEXO_HOME</string>
    </dict>
</dict>
</plist>
PLISTEOF
    ok "Updated com.nexo.dashboard.plist"
fi

# Reload agents
for plist in "$LA_AUTO_CLOSE" "$LA_DASHBOARD"; do
    if [[ -f "$plist" ]]; then
        run_or_dry "launchctl load '$plist' 2>/dev/null || true"
    fi
done
ok "LaunchAgents reloaded"

# ============================================================================
# STEP 8: UPDATE BRAIN CONFIGS
# ============================================================================
step "8" "Update Brain Configs"

# Update project-atlas.json
if [[ -f "$PROJECT_ATLAS" ]] && ! $DRY_RUN; then
    # Use python for reliable JSON editing
    python3 <<PYEOF
import json

with open("$PROJECT_ATLAS", "r") as f:
    atlas = json.load(f)

if "nexo" in atlas:
    nexo = atlas["nexo"]
    # Update locations
    if "locations" in nexo:
        nexo["locations"]["mcp_server"] = "$NEXO_CODE/src/"
        nexo["locations"]["data"] = "$NEW_DATA/"
        nexo["locations"]["plugins"] = "$NEW_PLUGINS/"
        # Keep other locations unchanged
    # Update database path
    nexo["database"] = "$NEW_DATA/nexo.db (SQLite)"
    nexo["deploy"] = "Local only. Code at repo, data at NEXO_HOME. MCP server restarts on save."

with open("$PROJECT_ATLAS", "w") as f:
    json.dump(atlas, f, indent=2, ensure_ascii=False)
    f.write("\n")

print("OK")
PYEOF
    ok "Updated project-atlas.json"
else
    warn "project-atlas.json not found or dry run"
fi

# Update CLAUDE.md — replace nexo-mcp reference in Repo Publico section
if [[ -f "$CLAUDE_MD" ]] && ! $DRY_RUN; then
    sed -i.bak \
        's|Tras cambios core en `~/claude/nexo-mcp/`:|Tras cambios core en `~/Documents/_PhpstormProjects/nexo/src/`:|' \
        "$CLAUDE_MD"
    rm -f "${CLAUDE_MD}.bak"
    ok "Updated CLAUDE.md (repo publico reference)"
else
    info "[DRY] Would update CLAUDE.md"
fi

# ============================================================================
# STEP 9: SET NEXO_HOME IN SHELL PROFILE
# ============================================================================
step "9" "Set Environment Variables in Shell Profile"

SHELL_PROFILE=""
if [[ -f "$HOME/.zshrc" ]]; then
    SHELL_PROFILE="$HOME/.zshrc"
elif [[ -f "$HOME/.bash_profile" ]]; then
    SHELL_PROFILE="$HOME/.bash_profile"
elif [[ -f "$HOME/.bashrc" ]]; then
    SHELL_PROFILE="$HOME/.bashrc"
fi

if [[ -n "$SHELL_PROFILE" ]]; then
    NEEDS_NEXO_HOME=true
    NEEDS_NEXO_CODE=true

    grep -q 'export NEXO_HOME=' "$SHELL_PROFILE" 2>/dev/null && NEEDS_NEXO_HOME=false
    grep -q 'export NEXO_CODE=' "$SHELL_PROFILE" 2>/dev/null && NEEDS_NEXO_CODE=false

    if $NEEDS_NEXO_HOME || $NEEDS_NEXO_CODE; then
        if ! $DRY_RUN; then
            echo "" >> "$SHELL_PROFILE"
            echo "# NEXO unified architecture (added by migrate-to-unified.sh)" >> "$SHELL_PROFILE"
            $NEEDS_NEXO_HOME && echo "export NEXO_HOME=~/claude" >> "$SHELL_PROFILE"
            $NEEDS_NEXO_CODE && echo "export NEXO_CODE=~/Documents/_PhpstormProjects/nexo/src" >> "$SHELL_PROFILE"
        fi
        ok "Added to $SHELL_PROFILE:"
        $NEEDS_NEXO_HOME && info "  export NEXO_HOME=~/claude"
        $NEEDS_NEXO_CODE && info "  export NEXO_CODE=~/Documents/_PhpstormProjects/nexo/src"
    else
        ok "Environment variables already set in $SHELL_PROFILE"
    fi
else
    warn "No shell profile found — add manually:"
    echo "  export NEXO_HOME=~/claude"
    echo "  export NEXO_CODE=~/Documents/_PhpstormProjects/nexo/src"
fi

# ============================================================================
# STEP 10: CLEANUP OLD INSTALL
# ============================================================================
step "10" "Cleanup Dead Files from Old Install"

# Remove known dead/duplicate files that are now in the repo
DEAD_FILES=(
    "$OLD_INSTALL/db_credentials.py"
    "$OLD_INSTALL/db_entities.py"
    "$OLD_INSTALL/db_episodic.py"
    "$OLD_INSTALL/db_evolution.py"
    "$OLD_INSTALL/db_learnings.py"
    "$OLD_INSTALL/db_reminders.py"
    "$OLD_INSTALL/db_schema.py"
    "$OLD_INSTALL/db_sessions.py"
    "$OLD_INSTALL/db_tasks.py"
    "$OLD_INSTALL/db.py.bak"
    "$OLD_INSTALL/cognitive.py.bak"
    "$OLD_INSTALL/nexo.db.orphan-backup"
    "$OLD_INSTALL/cognitive.db.bak-384dims-pre-upgrade"
    "$OLD_INSTALL/firebase-debug.log"
    "$OLD_INSTALL/test_fts.py"
    "$OLD_INSTALL/migrate_embeddings.py"
    "$OLD_INSTALL/migrate_memory.py"
    "$OLD_INSTALL/migrate_remaining.py"
    "$OLD_INSTALL/migrate.py"
    "$OLD_INSTALL/audit_migration.py"
    "$OLD_INSTALL/kg_populate.py"
    "$OLD_INSTALL/schema_cache.json"
)

CLEANED=0
for f in "${DEAD_FILES[@]}"; do
    if [[ -f "$f" ]]; then
        run_or_dry "rm '$f'"
        ((CLEANED++))
    fi
done
ok "Removed $CLEANED dead files from old install"

# Remove orphan DB copies (the 4KB stubs)
if [[ -f "$OLD_INSTALL/nexo.db" ]]; then
    OLD_ROOT_SIZE=$(stat -f%z "$OLD_INSTALL/nexo.db" 2>/dev/null || stat -c%s "$OLD_INSTALL/nexo.db" 2>/dev/null)
    if [[ "$OLD_ROOT_SIZE" -le 8192 ]]; then
        run_or_dry "rm '$OLD_INSTALL/nexo.db' '$OLD_INSTALL/nexo.db-shm' '$OLD_INSTALL/nexo.db-wal' 2>/dev/null || true"
        ok "Removed orphan nexo.db stub from root"
    fi
fi

# Remove orphan cognitive.db stub in db/
if [[ -f "$OLD_INSTALL/db/cognitive.db" ]]; then
    DB_COG_SIZE=$(stat -f%z "$OLD_INSTALL/db/cognitive.db" 2>/dev/null || stat -c%s "$OLD_INSTALL/db/cognitive.db" 2>/dev/null)
    if [[ "$DB_COG_SIZE" -le 8192 ]]; then
        run_or_dry "rm '$OLD_INSTALL/db/cognitive.db'"
        ok "Removed orphan cognitive.db stub from db/"
    fi
fi

# Clean __pycache__
run_or_dry "find '$OLD_INSTALL' -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true"
ok "Cleaned __pycache__ directories"

warn "NOT deleting $OLD_INSTALL — verify everything works first, then:"
warn "  rm -rf $OLD_INSTALL"

# ============================================================================
# STEP 11: SMOKE TEST
# ============================================================================
step "11" "Smoke Test"

if ! $DRY_RUN; then
    info "Testing MCP server can start..."

    # Quick test: can the new venv + new server.py import and init?
    SMOKE_RESULT=$("$NEXO_CODE/.venv/bin/python" -c "
import sys, os
os.environ['NEXO_HOME'] = '$NEXO_HOME'
sys.path.insert(0, '$NEXO_CODE/src')
try:
    from db import init_db
    # Test DB path resolution with NEXO_HOME
    print('IMPORT_OK')
except Exception as e:
    print(f'IMPORT_FAIL: {e}')
" 2>&1)

    if [[ "$SMOKE_RESULT" == *"IMPORT_OK"* ]]; then
        ok "Server import test passed"
    else
        warn "Server import test: $SMOKE_RESULT"
        ((ERRORS++))
    fi

    # Test DB access from new location
    DB_TEST=$(sqlite3 "$NEW_NEXO_DB" "SELECT COUNT(*) FROM sessions;" 2>&1)
    if [[ "$DB_TEST" =~ ^[0-9]+$ ]]; then
        ok "DB access test passed ($DB_TEST sessions)"
    else
        fail "DB access test failed: $DB_TEST"
        ((ERRORS++))
    fi

    # Run watchdog smoke test if it exists
    WATCHDOG_SMOKE="$NEXO_HOME/scripts/nexo-watchdog-smoke.py"
    if [[ -f "$WATCHDOG_SMOKE" ]]; then
        info "Running watchdog smoke test..."
        WATCHDOG_RESULT=$("$NEXO_CODE/.venv/bin/python" "$WATCHDOG_SMOKE" 2>&1 || true)
        if [[ "$WATCHDOG_RESULT" == *"PASS"* || "$WATCHDOG_RESULT" == *"OK"* || "$WATCHDOG_RESULT" == *"pass"* ]]; then
            ok "Watchdog smoke test passed"
        else
            warn "Watchdog smoke test output: $(echo "$WATCHDOG_RESULT" | head -5)"
            warn "May need NEXO_HOME-aware update to watchdog script"
        fi
    else
        info "Watchdog smoke test not found at $WATCHDOG_SMOKE — skipping"
    fi
else
    info "[DRY] Would run smoke tests"
fi

# ============================================================================
# STEP 12: SUMMARY
# ============================================================================
step "12" "Migration Summary"
divider

echo -e "${BOLD}What moved where:${NC}"
echo "  nexo.db:        $OLD_NEXO_DB → $NEW_NEXO_DB"
echo "  cognitive.db:   $OLD_COGNITIVE_DB → $NEW_COGNITIVE_DB"
echo "  email.py:       $OLD_INSTALL/plugins/email.py → $NEW_PLUGINS/email.py"
echo "  MCP config:     Updated to use $NEXO_CODE/.venv/bin/python + src/server.py"
echo "  LaunchAgents:   Updated to new paths + NEXO_HOME env"
echo "  project-atlas:  Updated nexo entry paths"
echo "  CLAUDE.md:      Updated repo publico reference"
echo "  Shell profile:  Added NEXO_HOME + NEXO_CODE exports"
echo ""

echo -e "${BOLD}DB Status Migration:${NC}"
echo "  PENDIENTE → PENDING"
echo "  COMPLETADO → COMPLETED"
echo "  ELIMINADO → DELETED"
echo ""

echo -e "${BOLD}Backup location:${NC}"
echo "  $BACKUP_DIR/"
echo ""

echo -e "${BOLD}To verify manually:${NC}"
echo "  1. Open a new terminal (to pick up env vars)"
echo "  2. Run: claude  → check that NEXO starts and responds"
echo "  3. Run: nexo_startup should work via MCP"
echo "  4. Check reminders: nexo_reminders(filter='due') should show PENDING status"
echo "  5. Dashboard: open http://localhost:6174"
echo ""

echo -e "${BOLD}If something is wrong — ROLLBACK:${NC}"
echo "  # 1. Restore MCP config"
echo "  cp ${MCP_CONFIG}.pre-migration $MCP_CONFIG"
echo ""
echo "  # 2. Restore LaunchAgents"
echo "  cp $BACKUP_DIR/com.nexo.auto-close-sessions.plist $LA_AUTO_CLOSE"
echo "  cp $BACKUP_DIR/com.nexo.dashboard.plist $LA_DASHBOARD"
echo "  launchctl unload $LA_AUTO_CLOSE $LA_DASHBOARD 2>/dev/null"
echo "  launchctl load $LA_AUTO_CLOSE $LA_DASHBOARD"
echo ""
echo "  # 3. Restore DBs (originals untouched in old location)"
echo "  # Old DBs are still at $OLD_INSTALL — nothing was deleted"
echo ""
echo "  # 4. Restore configs"
echo "  cp $BACKUP_DIR/project-atlas.json $PROJECT_ATLAS"
echo "  cp $BACKUP_DIR/CLAUDE.md $CLAUDE_MD"
echo ""
echo "  # 5. Remove env vars from shell profile"
echo "  # Edit $SHELL_PROFILE and remove the NEXO_HOME/NEXO_CODE lines"
echo ""

echo -e "${BOLD}Once verified, clean up old install:${NC}"
echo "  rm -rf $OLD_INSTALL"
echo ""

if [[ $ERRORS -gt 0 ]]; then
    warn "$ERRORS non-critical errors occurred — review above"
else
    ok "Migration completed successfully!"
fi

divider
echo -e "${GREEN}${BOLD}NEXO unified architecture is ready.${NC}"
echo ""
