"""NEXO Cognitive Engine — Vector memory with Atkinson-Shiffrin model."""

import base64
import json
import math
import hashlib
import os
import re
import sqlite3
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from cognitive_paths import resolve_cognitive_db

NEXO_HOME = os.environ.get("NEXO_HOME", os.path.expanduser("~/.nexo"))
_cognitive_db_path = resolve_cognitive_db(for_write=True)
_cognitive_dir = _cognitive_db_path.parent
_cognitive_dir.mkdir(parents=True, exist_ok=True)

COGNITIVE_DB = str(_cognitive_db_path)
def _configured_embedding_dim() -> int:
    try:
        from local_models import get_local_model_spec

        dim = int(get_local_model_spec("bge-base-embeddings").dimension or 0)
        if dim > 0:
            return dim
    except Exception:
        pass
    return 384


EMBEDDING_DIM = _configured_embedding_dim()
LAMBDA_STM = 0.004126   # half-life = ln(2) / (7 * 24) ≈ 7 days
LAMBDA_LTM = 0.000481  # half-life = ln(2) / (60 * 24) ≈ 60 days
DEFAULT_MEMORY_STABILITY = 1.0
DEFAULT_MEMORY_DIFFICULTY = 0.5
EMBEDDING_MEMORY_TABLES = ("stm_memories", "ltm_memories", "quarantine")
EMBEDDING_MIGRATION_BATCH_SIZE = int(os.environ.get("NEXO_EMBEDDING_MIGRATION_BATCH_SIZE", "128"))

# Prediction Error Gate thresholds
PE_GATE_REJECT = 0.85     # similarity > this → reject (not novel enough)
PE_GATE_REFINE = 0.70     # similarity between REFINE and REJECT → refinement (update existing)
# similarity < REFINE → novel (store as new)

# Session-level gate stats (reset each process lifetime)
_gate_stats = {"accepted_novel": 0, "accepted_refinement": 0, "rejected": 0}

# Discriminating entities — if these differ between two high-similarity memories,
# they are siblings (similar-but-incompatible), NOT duplicates to merge.
DISCRIMINATING_ENTITIES = {
    # OS / Environment
    "linux", "mac", "macos", "windows", "darwin", "ubuntu", "debian", "alpine",
    # Platforms
    "shopify", "my-project", "project-a", "ecommerce", "whatsapp", "chrome", "firefox",
    # Languages / Runtimes
    "python", "php", "javascript", "typescript", "node", "deno", "ruby",
    # Versions
    "v1", "v2", "v3", "v4", "v5", "5.6", "7.4", "8.0", "8.1", "8.2",
    # Infrastructure
    "shared-hosting", "cloudrun", "gcloud", "vps", "local", "production", "staging",
    # DB
    "mysql", "sqlite", "postgresql", "postgres", "redis",
}

# Sentiment detection keywords
POSITIVE_SIGNALS = {
    "gracias", "genial", "perfecto", "bien", "excelente", "bueno", "me gusta",
    "correcto", "sí", "dale", "hazlo", "adelante", "ok", "vale", "great",
    "nice", "good", "exactly", "buen trabajo", "bien hecho", "fenomenal",
}
NEGATIVE_SIGNALS = {
    "no", "mal", "otra vez", "ya te dije", "frustr", "error", "fallo",
    "cansad", "siempre", "nunca", "por qué no", "no funciona", "roto",
    "no sirve", "horrible", "desastre", "qué coño", "joder", "mierda",
    "hostia", "me cago", "irritad", "harto",
    "broken", "nothing works", "doesn't work", "not working", "fix it",
    "wrong", "failed", "failing", "annoying", "frustrated", "damn", "shit",
    "wtf", "terrible", "useless", "stupid", "hate", "worst", "sucks",
    "again",
}
URGENCY_SIGNALS = {
    "rápido", "ya", "ahora", "urgente", "asap", "inmediatamente", "corre",
}

# Correction signals — text patterns that indicate the user is correcting NEXO.
# Stronger than generic negative: implies "you were wrong, here's the truth".
CORRECTION_SIGNALS = {
    "no es", "no era", "te equivocas", "estás equivocad", "eso no",
    "está mal", "esta mal", "mal hecho", "eso es falso",
    "incorrecto", "ya te dije",
    # Auditor H2 removed "otra vez" — benign phrases like
    # "envíame la lista otra vez" were producing false corrections.
    "es al revés", "es al reves",
    "wrong", "that's wrong", "you're wrong", "incorrect",
    "not quite", "actually,", "fix it",
}

# Acknowledgement signals — user explicitly confirms something NEXO proposed.
ACKNOWLEDGEMENT_SIGNALS = {
    "gracias", "perfecto", "genial", "exactly", "correcto",
    "así es", "asi es", "bien hecho", "buen trabajo",
}

# Instruction signals — user asks NEXO to do something.
INSTRUCTION_SIGNALS = {
    "haz ", "hazlo", "crea ", "ejecuta ", "implementa ", "arregla ",
    "envía ", "envia ", "mueve ", "dime ", "revisa ", "borra ",
    "actualiza ", "publica ", "lanza ",
    "run ", "execute ", "implement ", "send ", "review ",
    "update ", "publish ", "ship ",
}

# Question signals — interrogatives.
QUESTION_SIGNALS = {
    "?", "¿", "qué ", "cómo ", "cuándo ", "dónde ", "por qué", "cual ",
    "cuál ", "puedes ", "podrías ",
    "what ", "how ", "when ", "where ", "why ", "which ", "can you",
    "could you",
}

# Trust score events — default deltas (overridable via trust_event_config table)
_DEFAULT_TRUST_EVENTS = {
    # Positive
    "explicit_thanks": +3,
    "delegation": +2,        # user delegates new task without micromanaging
    "paradigm_shift": +2,    # user teaches, NEXO learns
    "sibling_detected": +3,  # NEXO avoided context error on its own
    "proactive_action": +2,  # NEXO did something useful without being asked
    # Negative
    "correction": -3,        # user corrects NEXO
    "repeated_error": -7,    # Error on something NEXO already had a learning for
    "override": -5,          # NEXO's memory was wrong
    "correction_fatigue": -10, # Same memory corrected 3+ times
    "forgot_followup": -4,   # Forgot to mark followup or execute it
}

# Lazy-loaded from DB (trust_event_config table overrides defaults)
_trust_events_cache = None
_trust_events_cache_ts = 0



# Module-level state
_model = None
_embed_model = None
_reranker_model = None
_reranker = None
_conn = None

# --- Secret redaction patterns ---
_REDACT_PATTERNS = [
    # Specific API key formats
    (re.compile(r'sk-[a-zA-Z0-9_\-]{20,}'), '[REDACTED:api_key]'),
    (re.compile(r'ghp_[a-zA-Z0-9]{20,}'), '[REDACTED:api_key]'),
    (re.compile(r'shpat_[a-f0-9]{20,}'), '[REDACTED:api_key]'),
    (re.compile(r'AKIA[A-Z0-9]{16}'), '[REDACTED:api_key]'),
    (re.compile(r'xox[bp]-[a-zA-Z0-9\-]{20,}'), '[REDACTED:api_key]'),
    # Bearer tokens
    (re.compile(r'Bearer\s+[a-zA-Z0-9_\-\.=+/]{20,}'), '[REDACTED:bearer_token]'),
    # Connection strings with credentials
    (re.compile(r'(mysql|postgresql|postgres|mongodb|redis)://[^\s"\']+@[^\s"\']+'), '[REDACTED:connection_string]'),
    # Generic token assignments
    (re.compile(r'(token\s*[=:]\s*["\']?)([a-zA-Z0-9_\-]{20,})', re.IGNORECASE),
     lambda m: m.group(1) + '[REDACTED:token]'),
    # Password assignments
    (re.compile(r'(password\s*[=:]\s*["\']?)([^\s"\']{8,})', re.IGNORECASE),
     lambda m: m.group(1) + '[REDACTED:password]'),
    # SSH with private IPs (server credentials context)
    (re.compile(r'ssh\s+\S+@\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}'), '[REDACTED:ssh_credential]'),
]


def redact_secrets(text: str) -> str:
    """Scan text for secrets and replace with [REDACTED:<type>] placeholders.

    Fast regex-only detection. Not overly aggressive — won't redact normal
    hex strings, UUIDs, or short tokens that aren't secrets.
    """
    if not text:
        return text
    result = text
    for pattern, replacement in _REDACT_PATTERNS:
        if callable(replacement):
            result = pattern.sub(replacement, result)
        else:
            result = pattern.sub(replacement, result)
    return result


def _get_db() -> sqlite3.Connection:
    """Get or create SQLite connection with WAL mode."""
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(COGNITIVE_DB, check_same_thread=False)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
        _conn.row_factory = sqlite3.Row
        _init_tables(_conn)
        _migrate_lifecycle(_conn)
        _migrate_co_activation(_conn)
        _migrate_memory_personalization(_conn)
        _auto_migrate_embeddings(_conn)
    return _conn


def _migrate_lifecycle(conn: sqlite3.Connection):
    """Add lifecycle_state, snooze_until, and redaction_applied columns if they don't exist (idempotent)."""
    for table in ("stm_memories", "ltm_memories"):
        for col, col_type in [
            ("lifecycle_state", "TEXT DEFAULT 'active'"),
            ("snooze_until", "TEXT"),
            ("redaction_applied", "INTEGER DEFAULT 0"),
        ]:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
                conn.commit()
            except sqlite3.OperationalError as e:
                if "duplicate column" in str(e).lower():
                    pass
                else:
                    raise


def _migrate_co_activation(conn: sqlite3.Connection):
    """Add co_activation and prospective_triggers tables if they don't exist (idempotent)."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS co_activation (
            memory_a_id INTEGER NOT NULL,
            memory_b_id INTEGER NOT NULL,
            strength REAL DEFAULT 1.0,
            co_access_count INTEGER DEFAULT 1,
            last_co_access TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (memory_a_id, memory_b_id)
        );

        CREATE TABLE IF NOT EXISTS prospective_triggers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trigger_pattern TEXT NOT NULL,
            action TEXT NOT NULL,
            context TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            fired_at TEXT,
            status TEXT DEFAULT 'armed'
        );
    """)
    conn.commit()


def clamp_memory_stability(value: float | int | str | None) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = DEFAULT_MEMORY_STABILITY
    return max(0.6, min(3.0, numeric))


def clamp_memory_difficulty(value: float | int | str | None) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = DEFAULT_MEMORY_DIFFICULTY
    return max(0.2, min(1.2, numeric))


def initial_memory_profile(source_type: str, *, store: str = "stm") -> tuple[float, float]:
    source = str(source_type or "").strip().lower()
    if source in {"learning", "decision", "feedback"}:
        return 1.2 if store == "stm" else 1.4, 0.4
    if source in {"dream_insight", "session_summary"}:
        return 1.1 if store == "stm" else 1.25, 0.55
    if source in {"sensory", "dialog"}:
        return 0.9, 0.6
    return DEFAULT_MEMORY_STABILITY, DEFAULT_MEMORY_DIFFICULTY


def personalize_decay_rate(base_lambda: float, *, stability: float, difficulty: float) -> float:
    stability_factor = clamp_memory_stability(stability)
    difficulty_factor = 0.75 + (clamp_memory_difficulty(difficulty) * 0.5)
    return base_lambda * difficulty_factor / stability_factor


def rehearsal_profile_update(
    stability: float,
    difficulty: float,
    score: float,
    *,
    refinement: bool = False,
) -> tuple[float, float]:
    stable = clamp_memory_stability(stability)
    hard = clamp_memory_difficulty(difficulty)
    score = max(0.0, min(1.0, float(score or 0.0)))

    stability_gain = 0.03 + max(0.0, score - 0.45) * 0.12
    if refinement:
        stability_gain += 0.03
    new_stability = clamp_memory_stability(stable + stability_gain)

    target_difficulty = clamp_memory_difficulty(1.0 - (score * 0.8))
    if refinement:
        target_difficulty = clamp_memory_difficulty(target_difficulty + 0.05)
    new_difficulty = clamp_memory_difficulty((hard * 0.82) + (target_difficulty * 0.18))
    return new_stability, new_difficulty


def _migrate_memory_personalization(conn: sqlite3.Connection):
    """Add per-memory stability and difficulty columns if they don't exist."""
    for table in ("stm_memories", "ltm_memories"):
        for col, col_type in [
            ("stability", f"REAL DEFAULT {DEFAULT_MEMORY_STABILITY}"),
            ("difficulty", f"REAL DEFAULT {DEFAULT_MEMORY_DIFFICULTY}"),
        ]:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
                conn.commit()
            except sqlite3.OperationalError as e:
                if "duplicate column" in str(e).lower():
                    pass
                else:
                    raise


def _ensure_embedding_model_state(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS embedding_model_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS embedding_migration_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            old_marker TEXT DEFAULT '',
            new_marker TEXT NOT NULL,
            status TEXT NOT NULL,
            total_rows INTEGER DEFAULT 0,
            migrated_rows INTEGER DEFAULT 0,
            error TEXT DEFAULT '',
            backup_path TEXT DEFAULT '',
            started_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            finished_at TEXT
        )
    """)


def _ensure_embedding_shadow_columns(conn: sqlite3.Connection) -> None:
    for table in EMBEDDING_MEMORY_TABLES:
        for col, col_type in [
            ("embedding_v2", "BLOB"),
            ("embedding_v2_model_marker", "TEXT DEFAULT ''"),
            ("embedding_v2_error", "TEXT DEFAULT ''"),
        ]:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise
    conn.commit()


def _embedding_state_value(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    _ensure_embedding_model_state(conn)
    row = conn.execute("SELECT value FROM embedding_model_state WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row else default


def _write_embedding_state(conn: sqlite3.Connection, key: str, value: str, *, commit: bool = True) -> None:
    _ensure_embedding_model_state(conn)
    conn.execute(
        """
        INSERT INTO embedding_model_state (key, value, updated_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = excluded.updated_at
        """,
        (key, str(value or "")),
    )
    if commit:
        conn.commit()


def _embedding_table_counts(conn: sqlite3.Connection, marker: str) -> dict:
    total = 0
    migrated = 0
    errored = 0
    by_table = {}
    for table in EMBEDDING_MEMORY_TABLES:
        table_total = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        table_migrated = conn.execute(
            f"""
            SELECT COUNT(*) FROM {table}
            WHERE embedding_v2 IS NOT NULL
              AND embedding_v2_model_marker = ?
            """,
            (marker,),
        ).fetchone()[0]
        table_errored = conn.execute(
            f"""
            SELECT COUNT(*) FROM {table}
            WHERE COALESCE(embedding_v2_error, '') != ''
            """
        ).fetchone()[0]
        total += int(table_total or 0)
        migrated += int(table_migrated or 0)
        errored += int(table_errored or 0)
        by_table[table] = {
            "total": int(table_total or 0),
            "migrated": int(table_migrated or 0),
            "pending": max(0, int(table_total or 0) - int(table_migrated or 0)),
            "errored": int(table_errored or 0),
        }
    return {
        "total": total,
        "migrated": migrated,
        "pending": max(0, total - migrated),
        "errored": errored,
        "by_table": by_table,
    }


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (table,),
    ).fetchone()
    return bool(row)


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    try:
        return {str(row["name"] if isinstance(row, sqlite3.Row) else row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}
    except Exception:
        return set()


def _read_embedding_state_snapshot(conn: sqlite3.Connection) -> dict[str, dict[str, str]]:
    if not _table_exists(conn, "embedding_model_state"):
        return {}
    rows = conn.execute("SELECT key, value, updated_at FROM embedding_model_state").fetchall()
    snapshot = {}
    for row in rows:
        key = str(row["key"])
        snapshot[key] = {
            "value": str(row["value"] or ""),
            "updated_at": str(row["updated_at"] or ""),
        }
    return snapshot


def _embedding_status_counts(conn: sqlite3.Connection, marker: str) -> dict:
    total = 0
    migrated = 0
    errored = 0
    by_table = {}
    for table in EMBEDDING_MEMORY_TABLES:
        if not _table_exists(conn, table):
            by_table[table] = {"total": 0, "migrated": 0, "pending": 0, "errored": 0}
            continue
        table_total = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] or 0)
        columns = _table_columns(conn, table)
        has_shadow = {"embedding_v2", "embedding_v2_model_marker", "embedding_v2_error"}.issubset(columns)
        table_migrated = 0
        table_errored = 0
        if has_shadow:
            table_migrated = int(conn.execute(
                f"""
                SELECT COUNT(*) FROM {table}
                WHERE embedding_v2 IS NOT NULL
                  AND embedding_v2_model_marker = ?
                """,
                (marker,),
            ).fetchone()[0] or 0)
            table_errored = int(conn.execute(
                f"""
                SELECT COUNT(*) FROM {table}
                WHERE COALESCE(embedding_v2_error, '') != ''
                """
            ).fetchone()[0] or 0)
        total += table_total
        migrated += table_migrated
        errored += table_errored
        by_table[table] = {
            "total": table_total,
            "migrated": table_migrated,
            "pending": max(0, table_total - table_migrated),
            "errored": table_errored,
        }
    return {
        "total": total,
        "migrated": migrated,
        "pending": max(0, total - migrated),
        "errored": errored,
        "by_table": by_table,
    }


def embedding_migration_status(conn: Optional[sqlite3.Connection] = None) -> dict:
    """Return read-only status for the embedding migration.

    This intentionally avoids _get_db() so health checks do not trigger model
    warmup, re-embedding, or downloads. The migration itself still runs from
    normal cognitive startup.
    """
    own_conn = conn is None
    db_path = Path(COGNITIVE_DB)
    if own_conn and not db_path.exists():
        return {
            "ok": True,
            "healthy": True,
            "status": "no_database",
            "current_marker": _current_embedding_model_marker(),
            "active_marker": "",
            "storage": "",
            "total_rows": 0,
            "migrated_rows": 0,
            "pending_rows": 0,
            "errored_rows": 0,
            "progress_percent": 100.0,
            "needs_migration": False,
            "uses_shadow": False,
            "error": "",
            "backup_path": "",
            "schema": "nexo.embedding_migration_status.v1",
        }
    if own_conn:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
    try:
        assert conn is not None
        current_marker = _current_embedding_model_marker()
        state = _read_embedding_state_snapshot(conn)
        value = lambda key, default="": state.get(key, {}).get("value", default)
        updated_at = lambda key: state.get(key, {}).get("updated_at", "")
        active_marker = value("embedding_model_marker")
        target_marker = value("embedding_migration_target_marker") or current_marker
        storage = value("embedding_storage", "legacy")
        status = value("embedding_migration_status")
        counts = _embedding_status_counts(conn, current_marker)
        total = int(counts["total"] or 0)
        migrated = int(counts["migrated"] or 0)
        pending = int(counts["pending"] or 0)
        errored = int(counts["errored"] or 0)
        uses_shadow = bool(storage == "shadow_v2" and active_marker == current_marker and pending == 0)
        if total > 0 and not uses_shadow and status == "completed":
            status = "pending"
        elif not status:
            if total == 0:
                status = "empty"
            elif uses_shadow:
                status = "completed"
            else:
                status = "pending"
        if uses_shadow and status not in {"failed", "partial"}:
            status = "completed"
        unhealthy = status in {"failed", "partial"} or errored > 0
        progress = 100.0 if total <= 0 else round((migrated / total) * 100, 2)
        return {
            "ok": not unhealthy,
            "healthy": not unhealthy,
            "status": status,
            "current_marker": current_marker,
            "active_marker": active_marker,
            "target_marker": target_marker,
            "storage": storage,
            "total_rows": total,
            "migrated_rows": migrated,
            "pending_rows": pending,
            "errored_rows": errored,
            "progress_percent": progress,
            "needs_migration": bool(total > 0 and not uses_shadow),
            "uses_shadow": uses_shadow,
            "error": value("embedding_migration_error"),
            "backup_path": value("embedding_migration_backup_path"),
            "updated_at": updated_at("embedding_migration_status"),
            "by_table": counts["by_table"],
            "schema": "nexo.embedding_migration_status.v1",
        }
    except Exception as exc:
        return {
            "ok": False,
            "healthy": False,
            "status": "status_read_failed",
            "error": str(exc),
            "schema": "nexo.embedding_migration_status.v1",
        }
    finally:
        if own_conn and conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _record_embedding_migration_run(
    conn: sqlite3.Connection,
    *,
    old_marker: str,
    new_marker: str,
    status: str,
    total_rows: int,
    migrated_rows: int,
    error: str = "",
    backup_path: str = "",
    commit: bool = True,
) -> None:
    _ensure_embedding_model_state(conn)
    conn.execute(
        """
        INSERT INTO embedding_migration_runs (
            old_marker, new_marker, status, total_rows, migrated_rows,
            error, backup_path, updated_at, finished_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'),
                CASE WHEN ? IN ('completed', 'failed') THEN datetime('now') ELSE NULL END)
        """,
        (
            old_marker or "",
            new_marker,
            status,
            int(total_rows or 0),
            int(migrated_rows or 0),
            str(error or "")[:2000],
            str(backup_path or ""),
            status,
        ),
    )
    _write_embedding_state(conn, "embedding_migration_status", status, commit=False)
    _write_embedding_state(conn, "embedding_migration_target_marker", new_marker, commit=False)
    _write_embedding_state(conn, "embedding_migration_total_rows", str(int(total_rows or 0)), commit=False)
    _write_embedding_state(conn, "embedding_migration_migrated_rows", str(int(migrated_rows or 0)), commit=False)
    if error:
        _write_embedding_state(conn, "embedding_migration_error", str(error)[:2000], commit=False)
    if backup_path:
        _write_embedding_state(conn, "embedding_migration_backup_path", backup_path, commit=False)
    if commit:
        conn.commit()


def _first_embedding_row(conn: sqlite3.Connection):
    for table in EMBEDDING_MEMORY_TABLES:
        row = conn.execute(f"SELECT embedding FROM {table} LIMIT 1").fetchone()
        if row:
            return row
    return None


def _blob_matches_current_embedding_dim(blob) -> bool:
    try:
        return bool(blob) and len(blob) == EMBEDDING_DIM * 4
    except Exception:
        return False


def _sqlite_row_value(row, key: str, default=None):
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except (IndexError, KeyError, TypeError):
        return default


def _active_embedding_context(conn: sqlite3.Connection) -> dict:
    try:
        current_marker = _current_embedding_model_marker()
        active_marker = _embedding_state_value(conn, "embedding_model_marker")
        storage = _embedding_state_value(conn, "embedding_storage", "legacy")
        migration_status = _embedding_state_value(conn, "embedding_migration_status")
    except Exception:
        current_marker = _current_embedding_model_marker()
        active_marker = ""
        storage = "legacy"
        migration_status = ""
    shadow_active = storage == "shadow_v2" and active_marker == current_marker
    legacy_active = (
        storage == "legacy"
        and active_marker == current_marker
        and migration_status not in {"pending", "running", "failed", "partial"}
    )
    return {
        "current_marker": current_marker,
        "active_marker": active_marker,
        "storage": storage,
        "migration_status": migration_status,
        "shadow_active": shadow_active,
        "legacy_active": legacy_active,
    }


def _row_embedding_blob(row, conn: Optional[sqlite3.Connection] = None, context: Optional[dict] = None):
    if context is None and conn is not None:
        context = _active_embedding_context(conn)
    context = context or {}

    if context.get("shadow_active"):
        marker = _sqlite_row_value(row, "embedding_v2_model_marker", "")
        blob = _sqlite_row_value(row, "embedding_v2")
        if blob and marker == context.get("current_marker"):
            return blob

    if context.get("legacy_active"):
        legacy_blob = _sqlite_row_value(row, "embedding")
        if _blob_matches_current_embedding_dim(legacy_blob):
            return legacy_blob
    return None


def _row_embedding_array(row, conn: Optional[sqlite3.Connection] = None, context: Optional[dict] = None) -> Optional[np.ndarray]:
    blob = _row_embedding_blob(row, conn=conn, context=context)
    if not blob:
        return None
    arr = _blob_to_array(blob)
    if len(arr) != EMBEDDING_DIM:
        return None
    return arr


def _embedding_migration_uses_shadow(conn: sqlite3.Connection) -> bool:
    return bool(_active_embedding_context(conn).get("shadow_active"))


def _embedding_migration_allows_hnsw(conn: sqlite3.Connection) -> bool:
    context = _active_embedding_context(conn)
    return bool(context.get("shadow_active") or context.get("legacy_active"))


def _invalidate_embedding_indexes() -> None:
    try:
        import hnsw_index

        hnsw_index.invalidate("both", remove_persisted=True)
    except Exception:
        pass


def _ensure_embedding_indexes_invalidated(conn: sqlite3.Connection, marker: str, *, commit: bool = True) -> bool:
    marker = str(marker or "")
    if not marker:
        return False
    if _embedding_state_value(conn, "embedding_hnsw_invalidated_marker") == marker:
        return False
    _invalidate_embedding_indexes()
    _write_embedding_state(conn, "embedding_hnsw_invalidated_marker", marker, commit=False)
    if commit:
        conn.commit()
    return True


def _auto_migrate_embeddings(conn: sqlite3.Connection):
    """Re-embed safely when vector dimension or pinned embedding model changes.

    The legacy ``embedding`` column remains untouched. New vectors are written to
    shadow columns and become active only after every row has been migrated.
    """
    current_marker = ""
    stored_marker = ""
    try:
        _ensure_embedding_model_state(conn)
        _ensure_embedding_shadow_columns(conn)
        current_marker = _current_embedding_model_marker()
        stored_marker = _embedding_state_value(conn, "embedding_model_marker")

        first_row = _first_embedding_row(conn)
        if not first_row:
            _write_embedding_state(conn, "embedding_storage", "shadow_v2", commit=False)
            _write_embedding_state(conn, "embedding_migration_error", "", commit=False)
            _record_embedding_migration_run(
                conn,
                old_marker=stored_marker,
                new_marker=current_marker,
                status="completed",
                total_rows=0,
                migrated_rows=0,
            )
            _write_embedding_model_marker(conn, current_marker)
            _ensure_embedding_indexes_invalidated(conn, current_marker)
            return

        storage = _embedding_state_value(conn, "embedding_storage", "legacy")
        counts = _embedding_table_counts(conn, current_marker)
        if storage == "shadow_v2" and stored_marker == current_marker and counts["pending"] == 0:
            _ensure_embedding_indexes_invalidated(conn, current_marker)
            return

        if counts["pending"] == 0:
            _write_embedding_state(conn, "embedding_storage", "shadow_v2", commit=False)
            _write_embedding_model_marker(conn, current_marker)
            _record_embedding_migration_run(
                conn,
                old_marker=stored_marker,
                new_marker=current_marker,
                status="completed",
                total_rows=counts["total"],
                migrated_rows=counts["migrated"],
                backup_path=_embedding_state_value(conn, "embedding_migration_backup_path"),
            )
            _ensure_embedding_indexes_invalidated(conn, current_marker)
            return

        backup_path = _embedding_state_value(conn, "embedding_migration_backup_path")
        backup_marker = _embedding_state_value(conn, "embedding_migration_backup_marker")
        if backup_marker != current_marker or not backup_path:
            backup = _backup_cognitive_db_for_embedding_migration(stored_marker, current_marker, conn=conn)
            if not backup:
                raise RuntimeError("embedding migration backup failed")
            backup_path = str(backup)
            _write_embedding_state(conn, "embedding_migration_backup_marker", current_marker, commit=False)
            _write_embedding_state(conn, "embedding_migration_backup_path", backup_path, commit=False)

        _record_embedding_migration_run(
            conn,
            old_marker=stored_marker,
            new_marker=current_marker,
            status="running",
            total_rows=counts["total"],
            migrated_rows=counts["migrated"],
            backup_path=backup_path,
        )

        model = _get_model()
        batch_size = max(1, EMBEDDING_MIGRATION_BATCH_SIZE)
        for table in EMBEDDING_MEMORY_TABLES:
            while True:
                rows = conn.execute(
                    f"""
                    SELECT id, content FROM {table}
                    WHERE embedding_v2 IS NULL
                       OR embedding_v2_model_marker != ?
                    ORDER BY id
                    LIMIT ?
                    """,
                    (current_marker, batch_size),
                ).fetchall()
                if not rows:
                    break

                contents = [r["content"] for r in rows]
                embeddings = list(model.embed(contents))
                if len(embeddings) != len(rows):
                    raise RuntimeError(
                        f"embedding batch length mismatch: {len(embeddings)} != {len(rows)}"
                    )

                for row, emb in zip(rows, embeddings):
                    arr = np.array(emb, dtype=np.float32)
                    if len(arr) != EMBEDDING_DIM:
                        raise ValueError(f"embedding dimension mismatch: {len(arr)} != {EMBEDDING_DIM}")
                    conn.execute(
                        f"""
                        UPDATE {table}
                        SET embedding_v2 = ?,
                            embedding_v2_model_marker = ?,
                            embedding_v2_error = ''
                        WHERE id = ?
                        """,
                        (arr.tobytes(), current_marker, row["id"]),
                    )

                counts = _embedding_table_counts(conn, current_marker)
                _record_embedding_migration_run(
                    conn,
                    old_marker=stored_marker,
                    new_marker=current_marker,
                    status="running",
                    total_rows=counts["total"],
                    migrated_rows=counts["migrated"],
                    backup_path=backup_path,
                )

        counts = _embedding_table_counts(conn, current_marker)
        if counts["pending"] == 0 and counts["errored"] == 0:
            _write_embedding_state(conn, "embedding_storage", "shadow_v2", commit=False)
            _write_embedding_state(conn, "embedding_migration_error", "", commit=False)
            _write_embedding_model_marker(conn, current_marker)
            _record_embedding_migration_run(
                conn,
                old_marker=stored_marker,
                new_marker=current_marker,
                status="completed",
                total_rows=counts["total"],
                migrated_rows=counts["migrated"],
                backup_path=backup_path,
            )
            _ensure_embedding_indexes_invalidated(conn, current_marker)
        else:
            _record_embedding_migration_run(
                conn,
                old_marker=stored_marker,
                new_marker=current_marker,
                status="partial",
                total_rows=counts["total"],
                migrated_rows=counts["migrated"],
                error=json.dumps({"pending": counts["pending"], "errored": counts["errored"]}, sort_keys=True),
                backup_path=backup_path,
            )
    except Exception as exc:
        try:
            _ensure_embedding_model_state(conn)
            counts = _embedding_table_counts(conn, current_marker) if current_marker else {"total": 0, "migrated": 0}
            _record_embedding_migration_run(
                conn,
                old_marker=stored_marker,
                new_marker=current_marker or "unknown",
                status="failed",
                total_rows=counts.get("total", 0),
                migrated_rows=counts.get("migrated", 0),
                error=str(exc),
                backup_path=_embedding_state_value(conn, "embedding_migration_backup_path"),
            )
        except Exception:
            pass
        pass  # Don't break startup if migration fails


def _current_embedding_model_marker() -> str:
    try:
        from local_models import get_local_model_spec

        spec = get_local_model_spec("bge-base-embeddings")
        return "|".join([
            spec.name,
            spec.kind,
            spec.model_id,
            spec.source_repo,
            spec.revision,
            str(EMBEDDING_DIM),
        ])
    except Exception:
        return f"unknown|{EMBEDDING_DIM}"


def _write_embedding_model_marker(conn: sqlite3.Connection, marker: str) -> None:
    _ensure_embedding_model_state(conn)
    conn.execute(
        """
        INSERT INTO embedding_model_state (key, value, updated_at)
        VALUES ('embedding_model_marker', ?, datetime('now'))
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = excluded.updated_at
        """,
        (marker,),
    )
    conn.commit()


def _backup_cognitive_db_for_embedding_migration(
    old_marker: str,
    new_marker: str,
    conn: Optional[sqlite3.Connection] = None,
):
    db_path = Path(COGNITIVE_DB)
    if not db_path.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = db_path.with_name(f"{db_path.name}.bak-embedding-{stamp}")
    meta = backup.with_suffix(backup.suffix + ".json")
    source_conn = None
    dest_conn = None
    try:
        dest_conn = sqlite3.connect(str(backup))
        if conn is None:
            source_conn = sqlite3.connect(str(db_path))
            source_conn.backup(dest_conn)
        else:
            conn.backup(dest_conn)
        dest_conn.close()
        dest_conn = None
        meta.write_text(
            json.dumps(
                {
                    "old_marker": old_marker,
                    "new_marker": new_marker,
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                },
                indent=2,
                ensure_ascii=True,
                sort_keys=True,
            ) + "\n",
            encoding="utf-8",
        )
        return backup
    except Exception:
        return None
    finally:
        if dest_conn is not None:
            try:
                dest_conn.close()
            except Exception:
                pass
        if source_conn is not None:
            try:
                source_conn.close()
            except Exception:
                pass


def _init_tables(conn: sqlite3.Connection):
    """Create tables if they don't exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS stm_memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            embedding BLOB NOT NULL,
            source_type TEXT NOT NULL,
            source_id TEXT DEFAULT '',
            source_title TEXT DEFAULT '',
            domain TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            last_accessed TEXT DEFAULT (datetime('now')),
            access_count INTEGER DEFAULT 0,
            strength REAL DEFAULT 1.0,
            stability REAL DEFAULT 1.0,
            difficulty REAL DEFAULT 0.5,
            promoted_to_ltm INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS ltm_memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            embedding BLOB NOT NULL,
            source_type TEXT NOT NULL,
            source_id TEXT DEFAULT '',
            source_title TEXT DEFAULT '',
            domain TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            last_accessed TEXT DEFAULT (datetime('now')),
            access_count INTEGER DEFAULT 0,
            strength REAL DEFAULT 1.0,
            stability REAL DEFAULT 1.0,
            difficulty REAL DEFAULT 0.5,
            is_dormant INTEGER DEFAULT 0,
            original_stm_id INTEGER,
            tags TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS retrieval_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query_text TEXT NOT NULL,
            results_count INTEGER DEFAULT 0,
            top_score REAL DEFAULT 0.0,
            created_at TEXT DEFAULT (datetime('now'))
        );

        -- Sibling memories: similar-but-incompatible (discriminating entities differ)
        CREATE TABLE IF NOT EXISTS memory_siblings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            memory_a_id INTEGER NOT NULL,
            memory_b_id INTEGER NOT NULL,
            similarity REAL NOT NULL,
            discriminators TEXT NOT NULL,  -- JSON: entities that differ between them
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(memory_a_id, memory_b_id)
        );

        -- Dreamed pairs: track which memory pairs have been processed by dream_cycle
        CREATE TABLE IF NOT EXISTS dreamed_pairs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            memory_a_id INTEGER NOT NULL,
            memory_b_id INTEGER NOT NULL,
            insight_id INTEGER,              -- LTM ID of the generated insight
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(memory_a_id, memory_b_id)
        );

        -- Trust score: NEXO's alignment index (0-100, starts at 50)
        CREATE TABLE IF NOT EXISTS trust_score (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            score REAL NOT NULL,
            event TEXT NOT NULL,           -- what caused the change
            delta REAL NOT NULL,           -- points gained or lost
            context TEXT DEFAULT '',       -- details
            created_at TEXT DEFAULT (datetime('now'))
        );

        -- Sentiment readings: user's detected mood per interaction
        CREATE TABLE IF NOT EXISTS sentiment_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sentiment TEXT NOT NULL,       -- 'positive', 'negative', 'neutral', 'urgent'
            intensity REAL DEFAULT 0.5,    -- 0.0 to 1.0
            signals TEXT DEFAULT '',       -- keywords detected
            created_at TEXT DEFAULT (datetime('now'))
        );

        -- Quarantine: new memories held for validation before promotion to STM
        CREATE TABLE IF NOT EXISTS quarantine (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            embedding BLOB NOT NULL,
            source TEXT DEFAULT 'inferred',
            source_type TEXT NOT NULL,
            source_id TEXT DEFAULT '',
            source_title TEXT DEFAULT '',
            domain TEXT DEFAULT '',
            confidence REAL DEFAULT 0.5,
            promotion_checks INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            promoted_at TEXT,
            status TEXT DEFAULT 'pending'
        );

        -- Correction tracking: when user overrides a memory's guidance
        CREATE TABLE IF NOT EXISTS memory_corrections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            memory_id INTEGER NOT NULL,
            store TEXT NOT NULL,           -- 'stm' or 'ltm'
            correction_type TEXT NOT NULL, -- 'override', 'exception', 'paradigm_shift'
            context TEXT DEFAULT '',       -- what user said
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)

    # FTS5 tables for hybrid search (BM25 + vector)
    conn.executescript("""
        CREATE VIRTUAL TABLE IF NOT EXISTS stm_fts USING fts5(
            content, source_type, source_id, domain,
            content_rowid='id',
            prefix='2,3'
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS ltm_fts USING fts5(
            content, source_type, source_id, domain,
            content_rowid='id',
            prefix='2,3'
        );
    """)

    # Sync triggers — keep FTS5 in sync with memory tables
    for store in ("stm", "ltm"):
        conn.executescript(f"""
            CREATE TRIGGER IF NOT EXISTS {store}_fts_insert AFTER INSERT ON {store}_memories BEGIN
                INSERT OR REPLACE INTO {store}_fts(rowid, content, source_type, source_id, domain)
                VALUES (new.id, new.content, new.source_type, new.source_id, new.domain);
            END;
            CREATE TRIGGER IF NOT EXISTS {store}_fts_delete AFTER DELETE ON {store}_memories BEGIN
                DELETE FROM {store}_fts WHERE rowid = old.id;
            END;
            CREATE TRIGGER IF NOT EXISTS {store}_fts_update AFTER UPDATE OF content ON {store}_memories BEGIN
                UPDATE {store}_fts SET content = new.content WHERE rowid = new.id;
            END;
        """)

    # Backfill FTS5 for existing memories not yet indexed
    for store in ("stm", "ltm"):
        conn.execute(f"""
            INSERT OR IGNORE INTO {store}_fts(rowid, content, source_type, source_id, domain)
            SELECT id, content, source_type, source_id, domain FROM {store}_memories
        """)

    # Temporal indexing columns (Task C)
    for table in ("stm_memories", "ltm_memories"):
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN temporal_date TEXT DEFAULT ''")
        except Exception:
            pass  # Column already exists

    # Somatic markers — emotional risk memory for files and areas
    conn.execute("""
        CREATE TABLE IF NOT EXISTS somatic_markers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target TEXT NOT NULL,
            target_type TEXT NOT NULL,
            risk_score REAL DEFAULT 0.0,
            incident_count INTEGER DEFAULT 0,
            last_incident TEXT DEFAULT NULL,
            last_decay TEXT DEFAULT NULL,
            last_guard_decay_date TEXT DEFAULT NULL,
            last_validated_at TEXT DEFAULT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(target, target_type)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_somatic_target ON somatic_markers(target)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS kg_nodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            node_type TEXT NOT NULL,
            node_ref TEXT NOT NULL,
            label TEXT NOT NULL,
            properties TEXT DEFAULT '{}',
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(node_type, node_ref)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kg_nodes_type ON kg_nodes(node_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kg_nodes_label ON kg_nodes(label)")

    conn.execute("""
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
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kg_edges_source ON kg_edges(source_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kg_edges_target ON kg_edges(target_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kg_edges_relation ON kg_edges(relation)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kg_edges_source_relation_active ON kg_edges(source_id, relation, valid_until)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kg_edges_target_relation_active ON kg_edges(target_id, relation, valid_until)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kg_edges_relation_active ON kg_edges(relation, valid_until)")

    conn.commit()


def _get_model():
    """Lazy-load fastembed TextEmbedding model."""
    global _model
    if _model is None:
        if _model_download_disabled():
            raise RuntimeError("cognitive model loading disabled for this environment")
        from local_models import build_fastembed_embedding

        _model = build_fastembed_embedding("bge-base-embeddings")
    return _model


def _get_reranker():
    """Lazy-load cross-encoder reranking model."""
    global _reranker
    if _reranker is None:
        if _model_download_disabled():
            _reranker = False
            return None
        try:
            from local_models import build_fastembed_reranker

            _reranker = build_fastembed_reranker("cross-encoder-reranker")
        except Exception:
            _reranker = False  # Mark as unavailable
    return _reranker if _reranker is not False else None


def _model_download_disabled() -> bool:
    return os.environ.get("NEXO_SKIP_COGNITIVE_MODEL_DOWNLOAD", "").strip().lower() in {"1", "true", "yes"}


def _deterministic_fallback_embedding(text: str) -> np.ndarray:
    """Return a stable vector for tests/offline fallback paths."""
    digest = hashlib.sha256(str(text or "").encode("utf-8", errors="ignore")).digest()
    arr = np.zeros(EMBEDDING_DIM, dtype=np.float32)
    for index, byte in enumerate(digest):
        arr[index] = (float(byte) / 255.0) - 0.5
    norm = np.linalg.norm(arr)
    if norm > 0:
        arr = arr / norm
    return arr.astype(np.float32)


def rerank_results(query: str, results: list[dict], top_k: int = 5) -> list[dict]:
    """Rerank search results using cross-encoder for precise top-k.

    Takes top-20 vector results and reranks with a cross-encoder model.
    Falls back to original ranking if reranker is unavailable.
    """
    reranker = _get_reranker()
    if not reranker or len(results) <= 1:
        return results[:top_k]

    # Extract texts for reranking
    docs = [r["content"] for r in results]

    try:
        scores = list(reranker.rerank(query, docs))
        # Attach rerank scores and sort
        for r, score in zip(results, scores):
            r["rerank_score"] = score
        results.sort(key=lambda x: x.get("rerank_score", -999), reverse=True)
    except Exception:
        pass  # Fall back to original order

    return results[:top_k]


def embed(text: str) -> np.ndarray:
    """Embed text into a float32 vector. Returns zeros for empty text."""
    if not text or not text.strip():
        return np.zeros(EMBEDDING_DIM, dtype=np.float32)
    if _model_download_disabled():
        return _deterministic_fallback_embedding(text)
    model = _get_model()
    embeddings = list(model.embed([text]))
    return np.array(embeddings[0], dtype=np.float32)


def _array_to_blob(arr: np.ndarray) -> bytes:
    """Serialize numpy array to bytes."""
    return arr.astype(np.float32).tobytes()


def _blob_to_array(blob: bytes) -> np.ndarray:
    """Deserialize bytes to numpy array."""
    return np.frombuffer(blob, dtype=np.float32)


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity with zero-norm guard."""
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


# ── Temporal Date Extraction ───────────────────────────────────────────

_MONTH_MAP = {
    "january": "01", "february": "02", "march": "03", "april": "04",
    "may": "05", "june": "06", "july": "07", "august": "08",
    "september": "09", "october": "10", "november": "11", "december": "12",
    "jan": "01", "feb": "02", "mar": "03", "apr": "04",
    "jun": "06", "jul": "07", "aug": "08", "sep": "09",
    "oct": "10", "nov": "11", "dec": "12",
    "enero": "01", "febrero": "02", "marzo": "03", "abril": "04",
    "mayo": "05", "junio": "06", "julio": "07", "agosto": "08",
    "septiembre": "09", "octubre": "10", "noviembre": "11", "diciembre": "12",
}

def extract_temporal_date(text: str) -> str:
    """Extract the most prominent date from text. Returns ISO format YYYY-MM-DD or ''."""
    if not text:
        return ""

    text_lower = text.lower()

    # Pattern 1: "DD Month YYYY" or "Month DD, YYYY" or "D Month, YYYY"
    # e.g., "8 May, 2023", "May 8, 2023", "25 May, 2023"
    for month_name, month_num in _MONTH_MAP.items():
        # "8 May, 2023" or "8 May 2023"
        match = re.search(rf'(\d{{1,2}})\s+{month_name}[,]?\s+(\d{{4}})', text_lower)
        if match:
            day = int(match.group(1))
            year = match.group(2)
            return f"{year}-{month_num}-{day:02d}"

        # "May 8, 2023" or "May 8 2023"
        match = re.search(rf'{month_name}\s+(\d{{1,2}})[,]?\s+(\d{{4}})', text_lower)
        if match:
            day = int(match.group(1))
            year = match.group(2)
            return f"{year}-{month_num}-{day:02d}"

    # Pattern 2: ISO format "2023-05-08"
    match = re.search(r'(\d{4})-(\d{2})-(\d{2})', text)
    if match:
        return match.group(0)

    # Pattern 3: "DD/MM/YYYY" or "MM/DD/YYYY" (ambiguous, try DD/MM first)
    match = re.search(r'(\d{1,2})/(\d{1,2})/(\d{4})', text)
    if match:
        a, b, year = int(match.group(1)), int(match.group(2)), match.group(3)
        if a > 12:  # Must be DD/MM
            return f"{year}-{b:02d}-{a:02d}"
        elif b > 12:  # Must be MM/DD
            return f"{year}-{a:02d}-{b:02d}"
        # Ambiguous — default to DD/MM (European)
        return f"{year}-{b:02d}-{a:02d}"

    return ""
