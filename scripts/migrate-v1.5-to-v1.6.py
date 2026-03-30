#!/usr/bin/env python3
"""
NEXO Migration Script: v1.5.x -> v1.6.0

Upgrades both nexo.db and cognitive.db to the v1.6.0 schema.
Safe to run multiple times (fully idempotent).

Usage:
    python3 migrate-v1.5-to-v1.6.py [--dry-run] [--nexo-home /path/to/nexo]

What this migration adds:

  nexo.db:
    - Table: error_repetitions (guard system)
    - Table: guard_checks (guard system)
    - Table: session_diary_draft (auto-diary)
    - Table: adaptive_log (adaptive personality mode)
    - Table: somatic_events (somatic event log)
    - Table: maintenance_schedule (maintenance cron tasks)
    - Table: diary_archive (permanent diary archive)
    - Table: artifact_registry (structured artifact index)
    - Table: artifact_aliases (artifact alias search)
    - Table: session_checkpoints (compaction continuity)
    - Table: schema_migrations (migration tracking)
    - Column: learnings.reasoning, prevention, applies_to, status, review_due_at, last_reviewed_at
    - Column: followups.reasoning
    - Column: task_history.reasoning
    - Column: decisions.status, review_due_at, last_reviewed_at
    - Column: session_diary.mental_state, domain, user_signals, self_critique, source
    - Column: sessions.claude_session_id
    - Indexes: 20+ new indexes across tables

  cognitive.db:
    - Table: kg_nodes (knowledge graph nodes)
    - Table: kg_edges (knowledge graph bi-temporal edges)
    - Table: somatic_markers (persistent somatic markers)
    - Table: claims (atomic claim graph)
    - Table: claim_links (claim relationships)
    - Table: trust_event_config (customizable trust deltas)
    - Indexes: 12+ new indexes
"""

import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


# ── Constants ────────────────────────────────────────────────────────

VERSION_FROM = "1.5.x"
VERSION_TO = "1.6.0"


# ── Helpers ──────────────────────────────────────────────────────────

def find_nexo_home(override: str = None) -> Path:
    """Locate NEXO home directory. Checks in order:
    1. Explicit override path
    2. NEXO_HOME env var
    3. ~/.nexo/
    4. ./  (current directory)
    """
    candidates = []
    if override:
        candidates.append(Path(override))
    if os.environ.get("NEXO_HOME"):
        candidates.append(Path(os.environ["NEXO_HOME"]))
    candidates.append(Path.home() / ".nexo")
    candidates.append(Path.cwd())

    for c in candidates:
        nexo_db = c / "nexo.db"
        if nexo_db.exists():
            return c

    # If no nexo.db found anywhere, return the first candidate that exists as dir
    for c in candidates:
        if c.is_dir():
            return c

    return candidates[0]


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
        (table,)
    ).fetchone()
    return row[0] > 0


def column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    if not table_exists(conn, table):
        return False
    cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(c[1] == column for c in cols)


def index_exists(conn: sqlite3.Connection, index_name: str) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name=?",
        (index_name,)
    ).fetchone()
    return row[0] > 0


def add_column(conn: sqlite3.Connection, table: str, column: str, col_type: str, dry_run: bool = False) -> bool:
    """Add column if missing. Returns True if added."""
    if column_exists(conn, table, column):
        return False
    sql = f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"
    if dry_run:
        print(f"  [DRY RUN] Would execute: {sql}")
    else:
        conn.execute(sql)
    return True


def add_index(conn: sqlite3.Connection, name: str, table: str, columns: str, dry_run: bool = False) -> bool:
    """Add index if missing. Returns True if added."""
    if index_exists(conn, name):
        return False
    sql = f"CREATE INDEX IF NOT EXISTS {name} ON {table}({columns})"
    if dry_run:
        print(f"  [DRY RUN] Would execute: {sql}")
    else:
        conn.execute(sql)
    return True


def create_table(conn: sqlite3.Connection, table: str, ddl: str, dry_run: bool = False) -> bool:
    """Create table if missing. Returns True if created."""
    if table_exists(conn, table):
        return False
    if dry_run:
        print(f"  [DRY RUN] Would create table: {table}")
    else:
        conn.execute(ddl)
    return True


def backup_db(db_path: Path) -> Path:
    """Create a timestamped backup. Returns backup path."""
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = db_path.parent / f"{db_path.stem}.backup-{ts}{db_path.suffix}"
    shutil.copy2(db_path, backup)
    return backup


# ── nexo.db migrations ──────────────────────────────────────────────

def migrate_nexo_db(conn: sqlite3.Connection, dry_run: bool = False) -> dict:
    """Apply all v1.5 -> v1.6 schema changes to nexo.db."""
    stats = {"tables_created": 0, "columns_added": 0, "indexes_created": 0, "data_seeded": 0}

    # ── schema_migrations tracking table ────────────────────────────
    if create_table(conn, "schema_migrations", """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT DEFAULT (datetime('now'))
        )
    """, dry_run):
        stats["tables_created"] += 1
        print("  + Created table: schema_migrations")

    # ── Migration 1: learnings columns ──────────────────────────────
    print("\n  [M1] learnings columns...")
    for col, ctype in [
        ("reasoning", "TEXT"),
        ("prevention", "TEXT DEFAULT ''"),
        ("applies_to", "TEXT DEFAULT ''"),
        ("status", "TEXT DEFAULT 'active'"),
        ("review_due_at", "REAL"),
        ("last_reviewed_at", "REAL"),
    ]:
        if add_column(conn, "learnings", col, ctype, dry_run):
            stats["columns_added"] += 1
            print(f"    + Column: learnings.{col}")

    # ── Migration 2: followups/task_history reasoning ───────────────
    print("  [M2] followups & task_history reasoning...")
    if add_column(conn, "followups", "reasoning", "TEXT", dry_run):
        stats["columns_added"] += 1
        print("    + Column: followups.reasoning")
    if add_column(conn, "task_history", "reasoning", "TEXT", dry_run):
        stats["columns_added"] += 1
        print("    + Column: task_history.reasoning")

    # ── Migration 3: decisions review columns + indexes ─────────────
    print("  [M3] decisions review columns...")
    for col, ctype in [
        ("status", "TEXT DEFAULT 'pending_review'"),
        ("review_due_at", "TEXT"),
        ("last_reviewed_at", "TEXT"),
    ]:
        if add_column(conn, "decisions", col, ctype, dry_run):
            stats["columns_added"] += 1
            print(f"    + Column: decisions.{col}")
    for idx, tbl, col in [
        ("idx_decisions_domain", "decisions", "domain"),
        ("idx_decisions_created", "decisions", "created_at"),
        ("idx_decisions_review_due", "decisions", "review_due_at"),
    ]:
        if add_index(conn, idx, tbl, col, dry_run):
            stats["indexes_created"] += 1
            print(f"    + Index: {idx}")

    # ── Migration 4: session_diary columns ──────────────────────────
    print("  [M4] session_diary columns...")
    if add_index(conn, "idx_session_diary_sid", "session_diary", "session_id", dry_run):
        stats["indexes_created"] += 1
        print("    + Index: idx_session_diary_sid")
    for col in ["mental_state", "domain", "user_signals", "self_critique"]:
        if add_column(conn, "session_diary", col, "TEXT", dry_run):
            stats["columns_added"] += 1
            print(f"    + Column: session_diary.{col}")

    # ── Migration 5: change_log & learnings indexes ─────────────────
    print("  [M5] change_log & learnings indexes...")
    for idx, tbl, col in [
        ("idx_change_log_created", "change_log", "created_at"),
        ("idx_change_log_files", "change_log", "files"),
        ("idx_learnings_status", "learnings", "status"),
        ("idx_learnings_review_due", "learnings", "review_due_at"),
    ]:
        if add_index(conn, idx, tbl, col, dry_run):
            stats["indexes_created"] += 1
            print(f"    + Index: {idx}")

    # ── Migration 6: error guard tables ─────────────────────────────
    print("  [M6] error guard tables...")
    if create_table(conn, "error_repetitions", """
        CREATE TABLE IF NOT EXISTS error_repetitions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            new_learning_id INTEGER NOT NULL,
            original_learning_id INTEGER NOT NULL,
            similarity REAL NOT NULL,
            area TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """, dry_run):
        stats["tables_created"] += 1
        print("    + Table: error_repetitions")
    if create_table(conn, "guard_checks", """
        CREATE TABLE IF NOT EXISTS guard_checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            files TEXT,
            area TEXT,
            learnings_returned INTEGER DEFAULT 0,
            blocking_rules_returned INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """, dry_run):
        stats["tables_created"] += 1
        print("    + Table: guard_checks")
    for idx, tbl, col in [
        ("idx_error_repetitions_area", "error_repetitions", "area"),
        ("idx_guard_checks_session", "guard_checks", "session_id"),
    ]:
        if add_index(conn, idx, tbl, col, dry_run):
            stats["indexes_created"] += 1
            print(f"    + Index: {idx}")

    # ── Migration 7: diary source + draft table ─────────────────────
    print("  [M7] diary source & draft...")
    if add_column(conn, "session_diary", "source", "TEXT DEFAULT 'claude'", dry_run):
        stats["columns_added"] += 1
        print("    + Column: session_diary.source")
    if create_table(conn, "session_diary_draft", """
        CREATE TABLE IF NOT EXISTS session_diary_draft (
            sid TEXT PRIMARY KEY,
            summary_draft TEXT DEFAULT '',
            tasks_seen TEXT DEFAULT '[]',
            change_ids TEXT DEFAULT '[]',
            decision_ids TEXT DEFAULT '[]',
            last_context_hint TEXT DEFAULT '',
            heartbeat_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """, dry_run):
        stats["tables_created"] += 1
        print("    + Table: session_diary_draft")

    # ── Migration 8: adaptive_log + somatic_events ──────────────────
    print("  [M8] adaptive_log & somatic_events...")
    if create_table(conn, "adaptive_log", """
        CREATE TABLE IF NOT EXISTS adaptive_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT DEFAULT (datetime('now')),
            mode TEXT NOT NULL,
            tension_score REAL NOT NULL,
            sig_vibe REAL DEFAULT 0,
            sig_corrections REAL DEFAULT 0,
            sig_brevity REAL DEFAULT 0,
            sig_topic REAL DEFAULT 0,
            sig_tool_errors REAL DEFAULT 0,
            sig_git_diff REAL DEFAULT 0,
            context_hint TEXT DEFAULT '',
            feedback_event TEXT DEFAULT NULL,
            feedback_delta INTEGER DEFAULT NULL,
            feedback_ts TEXT DEFAULT NULL
        )
    """, dry_run):
        stats["tables_created"] += 1
        print("    + Table: adaptive_log")
    if add_index(conn, "idx_adaptive_log_ts", "adaptive_log", "timestamp", dry_run):
        stats["indexes_created"] += 1
        print("    + Index: idx_adaptive_log_ts")

    if create_table(conn, "somatic_events", """
        CREATE TABLE IF NOT EXISTS somatic_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT DEFAULT (datetime('now')),
            target TEXT NOT NULL,
            target_type TEXT NOT NULL,
            event_type TEXT NOT NULL,
            delta REAL NOT NULL,
            source TEXT DEFAULT '',
            projected INTEGER DEFAULT 0
        )
    """, dry_run):
        stats["tables_created"] += 1
        print("    + Table: somatic_events")
    for idx, tbl, col in [
        ("idx_somatic_events_target", "somatic_events", "target"),
        ("idx_somatic_events_projected", "somatic_events", "projected"),
    ]:
        if add_index(conn, idx, tbl, col, dry_run):
            stats["indexes_created"] += 1
            print(f"    + Index: {idx}")

    # ── Migration 9: maintenance_schedule ───────────────────────────
    print("  [M9] maintenance_schedule...")
    if create_table(conn, "maintenance_schedule", """
        CREATE TABLE IF NOT EXISTS maintenance_schedule (
            task_name TEXT PRIMARY KEY,
            interval_hours REAL NOT NULL,
            last_run_at TEXT DEFAULT NULL,
            last_duration_ms INTEGER DEFAULT 0,
            run_count INTEGER DEFAULT 0
        )
    """, dry_run):
        stats["tables_created"] += 1
        print("    + Table: maintenance_schedule")
    # Seed default tasks
    tasks = [
        ('cognitive_decay', 20), ('synthesis', 20), ('self_audit', 144),
        ('weight_learning', 20), ('somatic_projection', 20), ('somatic_decay', 20),
        ('graph_maintenance', 48),
    ]
    if not dry_run:
        for name, hours in tasks:
            conn.execute(
                "INSERT OR IGNORE INTO maintenance_schedule (task_name, interval_hours) VALUES (?, ?)",
                (name, hours)
            )
        stats["data_seeded"] += len(tasks)
        print(f"    + Seeded {len(tasks)} maintenance tasks")
    else:
        print(f"  [DRY RUN] Would seed {len(tasks)} maintenance tasks")

    # ── Migration 10: diary_archive ─────────────────────────────────
    print("  [M10] diary_archive...")
    if create_table(conn, "diary_archive", """
        CREATE TABLE IF NOT EXISTS diary_archive (
            id INTEGER PRIMARY KEY,
            session_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            decisions TEXT NOT NULL,
            discarded TEXT,
            pending TEXT,
            context_next TEXT,
            summary TEXT NOT NULL,
            mental_state TEXT,
            domain TEXT,
            user_signals TEXT,
            self_critique TEXT DEFAULT '',
            source TEXT DEFAULT 'claude',
            archived_at TEXT DEFAULT (datetime('now'))
        )
    """, dry_run):
        stats["tables_created"] += 1
        print("    + Table: diary_archive")
    for idx, tbl, col in [
        ("idx_diary_archive_created", "diary_archive", "created_at"),
        ("idx_diary_archive_domain", "diary_archive", "domain"),
    ]:
        if add_index(conn, idx, tbl, col, dry_run):
            stats["indexes_created"] += 1
            print(f"    + Index: {idx}")

    # ── Migration 11: artifact_registry ─────────────────────────────
    print("  [M11] artifact_registry...")
    if create_table(conn, "artifact_registry", """
        CREATE TABLE IF NOT EXISTS artifact_registry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            canonical_name TEXT NOT NULL,
            aliases TEXT DEFAULT '[]',
            description TEXT DEFAULT '',
            uri TEXT DEFAULT '',
            ports TEXT DEFAULT '[]',
            paths TEXT DEFAULT '[]',
            run_cmd TEXT DEFAULT '',
            repo TEXT DEFAULT '',
            domain TEXT DEFAULT '',
            state TEXT DEFAULT 'active',
            session_id TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            last_touched_at TEXT DEFAULT (datetime('now')),
            last_verified_at TEXT DEFAULT NULL,
            metadata TEXT DEFAULT '{}'
        )
    """, dry_run):
        stats["tables_created"] += 1
        print("    + Table: artifact_registry")
    if create_table(conn, "artifact_aliases", """
        CREATE TABLE IF NOT EXISTS artifact_aliases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            artifact_id INTEGER NOT NULL REFERENCES artifact_registry(id) ON DELETE CASCADE,
            phrase TEXT NOT NULL,
            source TEXT DEFAULT 'manual',
            confidence REAL DEFAULT 1.0,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(artifact_id, phrase)
        )
    """, dry_run):
        stats["tables_created"] += 1
        print("    + Table: artifact_aliases")
    for idx, tbl, col in [
        ("idx_artifact_state", "artifact_registry", "state"),
        ("idx_artifact_kind", "artifact_registry", "kind"),
        ("idx_artifact_domain", "artifact_registry", "domain"),
        ("idx_artifact_last_touched", "artifact_registry", "last_touched_at"),
        ("idx_artifact_aliases_phrase", "artifact_aliases", "phrase"),
        ("idx_artifact_aliases_aid", "artifact_aliases", "artifact_id"),
    ]:
        if add_index(conn, idx, tbl, col, dry_run):
            stats["indexes_created"] += 1
            print(f"    + Index: {idx}")

    # ── Migration 12: session_checkpoints ───────────────────────────
    print("  [M12] session_checkpoints...")
    if create_table(conn, "session_checkpoints", """
        CREATE TABLE IF NOT EXISTS session_checkpoints (
            sid TEXT PRIMARY KEY,
            task TEXT DEFAULT '',
            task_status TEXT DEFAULT 'active',
            active_files TEXT DEFAULT '[]',
            current_goal TEXT DEFAULT '',
            decisions_summary TEXT DEFAULT '',
            errors_found TEXT DEFAULT '',
            reasoning_thread TEXT DEFAULT '',
            next_step TEXT DEFAULT '',
            compaction_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """, dry_run):
        stats["tables_created"] += 1
        print("    + Table: session_checkpoints")

    # ── Migration 13: claude_session_id (NEW in v1.6) ───────────────
    print("  [M13] sessions.claude_session_id...")
    if add_column(conn, "sessions", "claude_session_id", "TEXT DEFAULT ''", dry_run):
        stats["columns_added"] += 1
        print("    + Column: sessions.claude_session_id")
    if add_index(conn, "idx_sessions_claude_sid", "sessions", "claude_session_id", dry_run):
        stats["indexes_created"] += 1
        print("    + Index: idx_sessions_claude_sid")

    # ── Record migrations in schema_migrations ──────────────────────
    if not dry_run and table_exists(conn, "schema_migrations"):
        migration_names = [
            (1, "learnings_columns"),
            (2, "followups_reasoning"),
            (3, "decisions_review"),
            (4, "session_diary_columns"),
            (5, "change_log_indexes"),
            (6, "error_guard_tables"),
            (7, "diary_source_and_draft"),
            (8, "adaptive_log_and_somatic"),
            (9, "maintenance_schedule"),
            (10, "diary_archive"),
            (11, "artifact_registry"),
            (12, "session_checkpoints"),
            (13, "claude_session_id"),
        ]
        for version, name in migration_names:
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, name) VALUES (?, ?)",
                (version, name)
            )
        print("  + Recorded all 13 migrations in schema_migrations")

    if not dry_run:
        conn.commit()

    return stats


# ── cognitive.db migrations ─────────────────────────────────────────

def migrate_cognitive_db(conn: sqlite3.Connection, dry_run: bool = False) -> dict:
    """Apply v1.6.0 schema additions to cognitive.db."""
    stats = {"tables_created": 0, "columns_added": 0, "indexes_created": 0}

    # ── Knowledge Graph: kg_nodes ───────────────────────────────────
    print("\n  [KG] Knowledge Graph tables...")
    if create_table(conn, "kg_nodes", """
        CREATE TABLE IF NOT EXISTS kg_nodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            node_type TEXT NOT NULL,
            node_ref TEXT NOT NULL,
            label TEXT NOT NULL,
            properties TEXT DEFAULT '{}',
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(node_type, node_ref)
        )
    """, dry_run):
        stats["tables_created"] += 1
        print("    + Table: kg_nodes")
    for idx, tbl, col in [
        ("idx_kg_nodes_type", "kg_nodes", "node_type"),
        ("idx_kg_nodes_label", "kg_nodes", "label"),
    ]:
        if add_index(conn, idx, tbl, col, dry_run):
            stats["indexes_created"] += 1
            print(f"    + Index: {idx}")

    # ── Knowledge Graph: kg_edges ───────────────────────────────────
    if create_table(conn, "kg_edges", """
        CREATE TABLE IF NOT EXISTS kg_edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL REFERENCES kg_nodes(id),
            target_id INTEGER NOT NULL REFERENCES kg_nodes(id),
            relation TEXT NOT NULL,
            weight REAL DEFAULT 1.0,
            confidence REAL DEFAULT 1.0,
            valid_from TEXT DEFAULT (datetime('now')),
            valid_until TEXT DEFAULT NULL,
            source_memory_id TEXT DEFAULT '',
            properties TEXT DEFAULT '{}',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """, dry_run):
        stats["tables_created"] += 1
        print("    + Table: kg_edges")
    for idx, tbl, col in [
        ("idx_kg_edges_source", "kg_edges", "source_id"),
        ("idx_kg_edges_target", "kg_edges", "target_id"),
        ("idx_kg_edges_relation", "kg_edges", "relation"),
    ]:
        if add_index(conn, idx, tbl, col, dry_run):
            stats["indexes_created"] += 1
            print(f"    + Index: {idx}")

    # ── Somatic Markers (persistent) ────────────────────────────────
    print("  [SOM] Somatic markers...")
    if create_table(conn, "somatic_markers", """
        CREATE TABLE IF NOT EXISTS somatic_markers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target TEXT NOT NULL,
            target_type TEXT NOT NULL,
            valence REAL DEFAULT 0.0,
            arousal REAL DEFAULT 0.5,
            confidence REAL DEFAULT 0.5,
            event_count INTEGER DEFAULT 0,
            last_event_type TEXT DEFAULT '',
            last_event_at TEXT DEFAULT NULL,
            last_guard_decay_date TEXT DEFAULT NULL,
            last_validated_at TEXT DEFAULT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(target, target_type)
        )
    """, dry_run):
        stats["tables_created"] += 1
        print("    + Table: somatic_markers")
    if add_index(conn, "idx_somatic_target", "somatic_markers", "target", dry_run):
        stats["indexes_created"] += 1
        print("    + Index: idx_somatic_target")

    # ── Claim Graph ─────────────────────────────────────────────────
    print("  [CLM] Claim graph tables...")
    if create_table(conn, "claims", """
        CREATE TABLE IF NOT EXISTS claims (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            embedding BLOB,
            source_type TEXT NOT NULL DEFAULT '',
            source_id TEXT NOT NULL DEFAULT '',
            source_memory_store TEXT DEFAULT '',
            source_memory_id INTEGER DEFAULT 0,
            confidence REAL DEFAULT 1.0,
            verification_status TEXT DEFAULT 'unverified',
            verified_at TEXT,
            domain TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """, dry_run):
        stats["tables_created"] += 1
        print("    + Table: claims")
    if create_table(conn, "claim_links", """
        CREATE TABLE IF NOT EXISTS claim_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_claim_id INTEGER NOT NULL REFERENCES claims(id),
            target_claim_id INTEGER NOT NULL REFERENCES claims(id),
            relation TEXT NOT NULL,
            confidence REAL DEFAULT 1.0,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(source_claim_id, target_claim_id, relation)
        )
    """, dry_run):
        stats["tables_created"] += 1
        print("    + Table: claim_links")
    for idx, tbl, col in [
        ("idx_claims_source", "claims", "source_type, source_id"),
        ("idx_claims_domain", "claims", "domain"),
        ("idx_claims_status", "claims", "verification_status"),
        ("idx_claim_links_source", "claim_links", "source_claim_id"),
        ("idx_claim_links_target", "claim_links", "target_claim_id"),
    ]:
        if add_index(conn, idx, tbl, col, dry_run):
            stats["indexes_created"] += 1
            print(f"    + Index: {idx}")

    # ── Trust Event Config ──────────────────────────────────────────
    print("  [TRS] Trust event config...")
    if create_table(conn, "trust_event_config", """
        CREATE TABLE IF NOT EXISTS trust_event_config (
            event TEXT PRIMARY KEY,
            delta REAL NOT NULL,
            description TEXT DEFAULT '',
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """, dry_run):
        stats["tables_created"] += 1
        print("    + Table: trust_event_config")

    if not dry_run:
        conn.commit()

    return stats


# ── Main ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description=f"NEXO Migration: {VERSION_FROM} -> {VERSION_TO}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Run without --dry-run to apply changes. Backups are created automatically."
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change without modifying anything")
    parser.add_argument("--nexo-home", type=str, default=None,
                        help="Override NEXO home directory (where nexo.db lives)")
    parser.add_argument("--skip-cognitive", action="store_true",
                        help="Skip cognitive.db migration (only migrate nexo.db)")
    args = parser.parse_args()

    print(f"{'=' * 60}")
    print(f"  NEXO Migration: {VERSION_FROM} -> {VERSION_TO}")
    print(f"{'=' * 60}")
    if args.dry_run:
        print("  MODE: DRY RUN (no changes will be made)\n")

    # ── Locate databases ────────────────────────────────────────────
    nexo_home = find_nexo_home(args.nexo_home)
    nexo_db_path = nexo_home / "nexo.db"
    cognitive_db_path = nexo_home / "cognitive.db"

    print(f"  NEXO home:      {nexo_home}")
    print(f"  nexo.db:        {nexo_db_path} {'(exists)' if nexo_db_path.exists() else '(NOT FOUND)'}")
    print(f"  cognitive.db:   {cognitive_db_path} {'(exists)' if cognitive_db_path.exists() else '(will be created)'}")

    if not nexo_db_path.exists():
        print(f"\n  ERROR: nexo.db not found at {nexo_db_path}")
        print("  Set NEXO_HOME env var or use --nexo-home to specify the correct path.")
        sys.exit(1)

    # ── Backup ──────────────────────────────────────────────────────
    if not args.dry_run:
        print(f"\n  Creating backups...")
        backup = backup_db(nexo_db_path)
        print(f"    nexo.db -> {backup}")
        if cognitive_db_path.exists():
            cog_backup = backup_db(cognitive_db_path)
            print(f"    cognitive.db -> {cog_backup}")
    else:
        print("\n  [DRY RUN] Skipping backup")

    # ── Migrate nexo.db ─────────────────────────────────────────────
    print(f"\n{'─' * 60}")
    print(f"  Migrating nexo.db...")
    print(f"{'─' * 60}")

    try:
        conn = sqlite3.connect(str(nexo_db_path), timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")

        nexo_stats = migrate_nexo_db(conn, dry_run=args.dry_run)

        if not args.dry_run:
            conn.commit()
        conn.close()
    except Exception as e:
        print(f"\n  ERROR migrating nexo.db: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    # ── Migrate cognitive.db ────────────────────────────────────────
    cog_stats = {"tables_created": 0, "columns_added": 0, "indexes_created": 0}
    if not args.skip_cognitive:
        print(f"\n{'─' * 60}")
        print(f"  Migrating cognitive.db...")
        print(f"{'─' * 60}")

        try:
            conn = sqlite3.connect(str(cognitive_db_path), timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("PRAGMA foreign_keys=ON")

            cog_stats = migrate_cognitive_db(conn, dry_run=args.dry_run)

            if not args.dry_run:
                conn.commit()
            conn.close()
        except Exception as e:
            print(f"\n  ERROR migrating cognitive.db: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)
    else:
        print("\n  Skipping cognitive.db (--skip-cognitive)")

    # ── Summary ─────────────────────────────────────────────────────
    total_tables = nexo_stats["tables_created"] + cog_stats["tables_created"]
    total_columns = nexo_stats["columns_added"] + cog_stats.get("columns_added", 0)
    total_indexes = nexo_stats["indexes_created"] + cog_stats["indexes_created"]

    print(f"\n{'=' * 60}")
    if args.dry_run:
        print("  DRY RUN SUMMARY")
    else:
        print("  MIGRATION COMPLETE")
    print(f"{'=' * 60}")
    print(f"  Tables created:  {total_tables}")
    print(f"  Columns added:   {total_columns}")
    print(f"  Indexes created: {total_indexes}")
    if nexo_stats.get("data_seeded"):
        print(f"  Data seeded:     {nexo_stats['data_seeded']} maintenance tasks")

    if total_tables == 0 and total_columns == 0 and total_indexes == 0:
        print("\n  Database is already at v1.6.0 schema. Nothing to do.")
    elif not args.dry_run:
        print(f"\n  Your database has been upgraded to v1.6.0.")
        print(f"  Backup saved alongside the original DB file.")

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
