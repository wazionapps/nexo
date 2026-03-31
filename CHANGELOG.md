# Changelog

## [1.8.0-beta.1] - 2026-03-31

### Added
- **Hybrid Architecture**: Tool-coupled behavioral rules moved from CLAUDE.md to the MCP server `instructions` field. Rules are now protocol-level, injected at the same priority as CLAUDE.md.
- **Migration script** (`migrate-v1.7-to-v1.8.py`): Automatically slims existing CLAUDE.md files by removing sections that are now MCP-owned. Idempotent with backup.
- **Expanded MCP instructions**: Heartbeat, Guard, Delegation, Reminders, Memory, Trust Score, Dissonance, Change Log, Session Diary, and Cortex rules now ship with the server.

### Changed
- **CLAUDE.md template**: 130 → 50 lines. Now contains only bootstrap (identity, profile, format, autonomy, project atlas, hooks). All tool-coupled rules removed.
- **Context token savings**: ~3K fewer tokens consumed per session by eliminating rule duplication between CLAUDE.md and MCP instructions.


## [1.7.0] - 2026-03-31

### Added
- **Linux support**: systemd user timers (preferred) or crontab fallback for automated cognitive processes. Same 4 scheduled tasks as macOS LaunchAgents.
- **Auto-resolve followups**: Change log entries automatically cross-reference and complete matching open followups via file overlap, keyword similarity, and ID reference.
- **Find similar followups**: Duplicate detection before creating new followups using asymmetric keyword overlap scoring.
- **Free-form learning categories**: Removed hardcoded category validation. Users can now use any category name (e.g., 'backend', 'frontend', 'devops').

### Changed
- **Full internationalization**: All UI strings, error messages, labels, and DB status values translated to English. Status values: `PENDING`, `COMPLETED`, `DELETED` (previously Spanish).
- **CLAUDE.md template rewrite**: 494→127 lines. Compact, procedural format. Same capabilities, zero prose.
- **Complete sanitization**: Removed all personal data, hardcoded paths, and project-specific references from the entire codebase. Every file uses `NEXO_HOME` env var.
- **Deleted 48 macOS Finder duplicate files** ("file 2.ext" pattern).

### Fixed
- Syntax error in `nexo-evolution-run.py` from automated NEXO_HOME insertion.
- Hardcoded locale (Europe/Madrid, es_ES) in menu date formatting.
- Personal directory path in FTS code indexing configuration.

## [1.7.0-beta.1] - 2026-03-30

### Added
- **Migration system** (`nexo-migrate.py`): Automatic, idempotent upgrades between versions with backup-before-migrate and a versioned migration registry.
- **Install script** (`nexo-install.py`): First-time setup that creates `~/.nexo/` structure, initializes databases, copies repo files, and sets `NEXO_HOME` in shell profile.
- **Diary brief mode**: `nexo_session_diary_read` now accepts `brief=True` to return only the last entry's summary + mental_state + context_next (~1K chars) for fast startup.

### Changed
- **Heartbeat optimization** (8 ops down to 3): Removed sentiment detection, trust auto-detect, adaptive mode computation, RAG retrieval, and auto-prime from the heartbeat hot path. These cognitive features remain available on-demand via dedicated tools (`nexo_cognitive_sentiment`, `nexo_cognitive_trust`, `nexo_cognitive_retrieve`, `nexo_context_packet`).
- **Path standardization**: All files now use `NEXO_HOME` env var with `~/.nexo` fallback instead of hardcoded `~/claude` paths. Affected: `_fts.py`, `auto_close_sessions.py`, `nexo-watchdog.sh`, `nexo-deep-sleep.sh`, `nexo-postmortem-consolidator.py`, `dashboard/app.py`.

### Fixed
- Dashboard watchdog endpoint used `~/claude` as fallback instead of `~/.nexo`.

## [1.6.0] - 2026-03-29

### Added
- Artifact registry for tracking services, dashboards, scripts, and APIs.
- Session checkpoints for intelligent auto-compaction.
- Claude session ID linking for inter-terminal coordination.
- Learnings priority, weight, and guard usage tracking.
- Deep sleep analysis system with catch-up for missed days.
- Watchdog comprehensive health monitor (15+ services).
- Postmortem consolidator v2 with Claude CLI intelligence.

### Changed
- Episodic memory plugin expanded with diary archive (permanent subconscious memory).
- FTS5 unified search with cross-language synonyms and code file indexing.
