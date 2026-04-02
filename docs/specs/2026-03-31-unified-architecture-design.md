# NEXO Unified Architecture

**Date:** 2026-03-31
**Status:** Approved (pending implementation)
**Goal:** Eliminate dual maintenance by making the repo the single source of code, with personal data living separately in NEXO_HOME.

## Problem

Today NEXO has two copies of its code:
- `~/claude/nexo-mcp/` — local production (Spanish strings, personal data mixed in, source of truth for functionality)
- `~/Documents/_PhpstormProjects/nexo/src/` — public repo (English, sanitized, may be missing features)

Every change requires: edit local → manually copy/sanitize → push to repo. This has caused feature drift, missing functionality in the repo, and wasted effort.

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Code location | Repo direct (MCP points to repo/src/) | Developer works on repo, changes are immediate |
| Data location | NEXO_HOME configurable | Developer: `~/claude/`, new users: `~/.nexo/` |
| Personal plugins | `NEXO_HOME/plugins/` | Plugin loader scans repo + personal dir |
| Personal scripts | `NEXO_HOME/scripts/` | Same pattern as plugins |
| Update mechanism | Git pull + verify + backup + migrate | Explicit action with safety net |
| Migration approach | In-place script, single step | Move data, update config, delete old install |
| Language | All English in code + outputs | User presentation language via CLAUDE.md |
| i18n | None (LLM handles presentation) | Zero complexity, CLAUDE.md controls language |

## Architecture

### Directory Layout

```
# CODE (repo, shared, updatable via git)
~/Documents/_PhpstormProjects/nexo/
  src/
    server.py                    # MCP entry point
    db/                          # Database layer + migrations
    cognitive/                   # Semantic memory, trust, vectors
    plugins/                     # Repo plugins (guard, cortex, etc.)
    dashboard/                   # Web dashboard
    hooks/                       # Session lifecycle hooks
    scripts/                     # Cron jobs, maintenance, watchdog
    rules/                       # Core rules JSON
  bin/                           # npm installer
  templates/                     # CLAUDE.md template, LaunchAgent plists
  scripts/                       # Version migration scripts
  tests/                         # Test suite

# DATA (personal, persistent, survives updates)
~/claude/                        # NEXO_HOME for developer
  data/
    nexo.db                      # Primary operational DB
    cognitive.db                 # Semantic memory DB
  plugins/
    email.py                     # Personal plugin (IMAP/SMTP with credentials from DB)
  scripts/
    (personal scripts — email helpers, custom crons, etc.)
  backups/
    (hourly + weekly backups)
  brain/                         # Existing brain directory (policies, session_buffer, etc.)
  operations/                    # Existing operations directory

# For new users:
~/.nexo/                         # Default NEXO_HOME
  data/
  plugins/
  scripts/
  backups/
```

### MCP Configuration

`~/.claude/mcp-cortex.json`:
```json
{
  "mcpServers": {
    "nexo": {
      "command": "~/Documents/_PhpstormProjects/nexo/src/.venv/bin/python",
      "args": ["~/Documents/_PhpstormProjects/nexo/src/server.py"],
      "env": {
        "NEXO_HOME": "$NEXO_HOME"
      }
    }
  }
}
```

### Plugin Loader Changes

Current: scans only `src/plugins/` relative to server.py.

New behavior:
1. Scan `{server_dir}/plugins/` — repo plugins (always loaded first)
2. Scan `{NEXO_HOME}/plugins/` — personal plugins (loaded second)
3. If a personal plugin has the same filename as a repo plugin, personal wins (override)
4. Personal plugins have access to all the same imports (db, cognitive, etc.)

### `nexo update` Tool + Script

Available as both MCP tool (`nexo_update`) and standalone script (`nexo-update.sh`).

Flow:
```
1. Check for uncommitted changes in repo src/ → abort if dirty
2. Backup NEXO_HOME/data/*.db → NEXO_HOME/backups/pre-update-{YYYY-MM-DD-HHMM}/
3. Record current version from package.json
4. git pull origin main (in repo directory)
5. Record new version from package.json
6. If version changed:
   a. Run init_db() → executes pending migrations
   b. Log update: old_version → new_version
7. Verify: import server.py successfully
8. If any step fails:
   a. git reset --hard to previous commit
   b. Restore DB backups
   c. Report error
9. Signal MCP restart needed (or auto-restart if running as tool)
```

### Path Resolution

All code uses `NEXO_HOME` env var with fallback to `~/.nexo/`.

Paths that must use NEXO_HOME:
- `db/_core.py` → `NEXO_HOME/data/nexo.db`
- `cognitive/_core.py` → `NEXO_HOME/data/cognitive.db`
- `plugins/backup.py` → `NEXO_HOME/backups/`
- `plugin_loader.py` → `NEXO_HOME/plugins/` (secondary scan)
- `auto_close_sessions.py` → `NEXO_HOME/data/`
- `storage_router.py` → already implemented

### DB Status Migration

Developer's local DB uses Spanish status values. Migration script will:
```sql
UPDATE reminders SET status = 'PENDING' WHERE status = 'PENDIENTE';
UPDATE reminders SET status = 'COMPLETED' WHERE status = 'COMPLETADO';
UPDATE reminders SET status = 'DELETED' WHERE status = 'ELIMINADO';
UPDATE followups SET status = 'PENDING' WHERE status = 'PENDIENTE';
UPDATE followups SET status = 'COMPLETED' WHERE status = 'COMPLETADO';
UPDATE followups SET status = 'DELETED' WHERE status = 'ELIMINADO';
```

This runs as part of the migration script, not as a DB migration (since it's a one-time data fix for existing installs).

## Implementation Phases

### Phase 0: Audit (MUST complete before anything else)

Exhaustive functional diff of every .py file between local and repo. Not just file existence — line-by-line logic comparison. Any feature that exists locally but not in the repo must be ported. Known gaps already fixed:
- anti-duplicate followup check (fixed in beta.3)
- auto-resolve reporting (fixed in beta.3)
- core_rules table creation (fixed in beta.3)

Remaining to verify: every function in every file.

### Phase 1: Code changes in repo

1. `plugin_loader.py` — add NEXO_HOME/plugins/ scanning
2. `db/_core.py` — ensure DB path uses NEXO_HOME/data/
3. `cognitive/_core.py` — ensure DB path uses NEXO_HOME/data/
4. `plugins/backup.py` — backup dir uses NEXO_HOME/backups/
5. Verify all remaining files use NEXO_HOME (not hardcoded paths)
6. Add `nexo_update` tool to server.py
7. Add `nexo-update.sh` standalone script
8. Add email plugin template (generic, credentials via nexo_credential_get)

### Phase 2: Migration script

`scripts/migrate-to-unified.sh`:
1. Detect current layout (~/claude/nexo-mcp/ exists?)
2. Create NEXO_HOME subdirectories (data/, plugins/, scripts/, backups/)
3. Move DBs: nexo-mcp/db/nexo.db → data/nexo.db
4. Move DBs: nexo-mcp/cognitive.db → data/cognitive.db
5. Move personal plugins: nexo-mcp/plugins/email.py → plugins/
6. Move personal scripts: identify and move
7. Move backups: nexo-mcp/backups/ → backups/
8. Migrate DB status values (Spanish → English)
9. Update mcp-cortex.json to point to repo
10. Clean up: delete dead files (db_*.py, cog_*.py, .bak, orphan DBs)
11. Delete ~/claude/nexo-mcp/ (the old install directory)
12. Verify: start MCP, check it responds

### Phase 3: Verify and clean up

1. Start a new NEXO session, verify all tools work
2. Verify personal plugins load from NEXO_HOME/plugins/
3. Verify DB data is intact
4. Verify backups still run
5. Test `nexo update` flow
6. Remove ~/claude/nexo-mcp/ if still present
7. Update CLAUDE.md if any paths changed

## Risks and Mitigations

| Risk | Mitigation |
|------|-----------|
| Repo missing functionality | Phase 0 audit catches this before migration |
| DB corruption during move | Backup before move, verify after |
| MCP fails to start after migration | Old install preserved until verification passes |
| Personal plugin can't import db/cognitive | Plugin loader adds repo src/ to sys.path |
| git pull breaks server.py | nexo update does pre-pull backup + rollback |
| venv path changes | Migration script creates/updates venv in repo |

## Success Criteria

- [ ] NEXO MCP starts from repo code with NEXO_HOME pointing to ~/claude/
- [ ] All tools work identically to current behavior
- [ ] Personal plugins (email.py) load from NEXO_HOME/plugins/
- [ ] `nexo update` pulls, migrates, and restarts successfully
- [ ] ~/claude/nexo-mcp/ is deleted
- [ ] No more dual maintenance — changes go directly to repo
