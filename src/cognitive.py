"""NEXO Cognitive Engine — Vector memory with Atkinson-Shiffrin model."""

import math
import os
import sqlite3
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

COGNITIVE_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cognitive.db")
EMBEDDING_DIM = 384
LAMBDA_STM = 0.1      # half-life ~7 days
LAMBDA_LTM = 0.012    # half-life ~60 days

# Discriminating entities — if these differ between two high-similarity memories,
# they are siblings (similar-but-incompatible), NOT duplicates to merge.
DISCRIMINATING_ENTITIES = {
    # OS / Environment
    "linux", "mac", "macos", "windows", "darwin", "ubuntu", "debian", "alpine",
    # Platforms
    "shopify", "wazion", "project-b", "project-a", "whatsapp", "chrome", "firefox",
    # Languages / Runtimes
    "python", "php", "javascript", "typescript", "node", "deno", "ruby",
    # Versions
    "v1", "v2", "v3", "v4", "v5", "5.6", "7.4", "8.0", "8.1", "8.2",
    # Infrastructure
    "hosting-provider", "cloudrun", "gcloud", "vps", "local", "production", "staging",
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
}
URGENCY_SIGNALS = {
    "rápido", "ya", "ahora", "urgente", "asap", "inmediatamente", "corre",
}

# Trust score events and their point values
TRUST_EVENTS = {
    # Positive
    "explicit_thanks": +3,
    "delegation": +2,        # User delegates new task without micromanaging
    "paradigm_shift": +2,    # User teaches, NEXO learns
    "sibling_detected": +3,  # NEXO avoided context error on its own
    "proactive_action": +2,  # NEXO did something useful without being asked
    # Negative
    "correction": -3,        # User corrects NEXO
    "repeated_error": -7,    # Error on something NEXO already had a learning for
    "override": -5,          # NEXO's memory was wrong
    "correction_fatigue": -10, # Same memory corrected 3+ times
    "forgot_followup": -4,   # Forgot to mark followup or execute it
}

_model = None
_conn = None


def _get_db() -> sqlite3.Connection:
    """Get or create SQLite connection with WAL mode."""
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(COGNITIVE_DB, check_same_thread=False)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
        _conn.row_factory = sqlite3.Row
        _init_tables(_conn)
    return _conn


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

        -- Trust score: NEXO's alignment index (0-100, starts at 50)
        CREATE TABLE IF NOT EXISTS trust_score (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            score REAL NOT NULL,
            event TEXT NOT NULL,           -- what caused the change
            delta REAL NOT NULL,           -- points gained or lost
            context TEXT DEFAULT '',       -- details
            created_at TEXT DEFAULT (datetime('now'))
        );

        -- Sentiment readings: User's detected mood per interaction
        CREATE TABLE IF NOT EXISTS sentiment_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sentiment TEXT NOT NULL,       -- 'positive', 'negative', 'neutral', 'urgent'
            intensity REAL DEFAULT 0.5,    -- 0.0 to 1.0
            signals TEXT DEFAULT '',       -- keywords detected
            created_at TEXT DEFAULT (datetime('now'))
        );

        -- Correction tracking: when User overrides a memory's guidance
        CREATE TABLE IF NOT EXISTS memory_corrections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            memory_id INTEGER NOT NULL,
            store TEXT NOT NULL,           -- 'stm' or 'ltm'
            correction_type TEXT NOT NULL, -- 'override', 'exception', 'paradigm_shift'
            context TEXT DEFAULT '',       -- what user said
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit()


def _get_model():
    """Lazy-load fastembed TextEmbedding model."""
    global _model
    if _model is None:
        from fastembed import TextEmbedding
        _model = TextEmbedding("BAAI/bge-small-en-v1.5")
    return _model


def embed(text: str) -> np.ndarray:
    """Embed text into a 384-dim float32 vector. Returns zeros for empty text."""
    if not text or not text.strip():
        return np.zeros(EMBEDDING_DIM, dtype=np.float32)
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


def search(
    query_text: str,
    top_k: int = 10,
    min_score: float = 0.5,
    stores: str = "both",
    exclude_dormant: bool = True,
    rehearse: bool = True,
    source_type_filter: str = ""
) -> list[dict]:
    """Full vector search across STM and/or LTM with rehearsal and dormant reactivation."""
    db = _get_db()
    query_vec = embed(query_text)
    if np.linalg.norm(query_vec) == 0:
        return []

    results = []
    reactivated_ids = set()

    # Search STM
    if stores in ("both", "stm"):
        where = "WHERE promoted_to_ltm = 0"
        if source_type_filter:
            where += f" AND source_type = ?"
            rows = db.execute(f"SELECT * FROM stm_memories {where}", (source_type_filter,)).fetchall()
        else:
            rows = db.execute(f"SELECT * FROM stm_memories {where}").fetchall()

        for row in rows:
            vec = _blob_to_array(row["embedding"])
            score = cosine_similarity(query_vec, vec)
            if score >= min_score:
                results.append({
                    "store": "stm",
                    "id": row["id"],
                    "content": row["content"],
                    "source_type": row["source_type"],
                    "source_id": row["source_id"],
                    "source_title": row["source_title"],
                    "domain": row["domain"],
                    "created_at": row["created_at"],
                    "strength": row["strength"],
                    "access_count": row["access_count"],
                    "score": score,
                })

    # Search LTM (active)
    if stores in ("both", "ltm"):
        where = "WHERE is_dormant = 0"
        if source_type_filter:
            where += " AND source_type = ?"
            rows = db.execute(f"SELECT * FROM ltm_memories {where}", (source_type_filter,)).fetchall()
        else:
            rows = db.execute(f"SELECT * FROM ltm_memories {where}").fetchall()

        for row in rows:
            vec = _blob_to_array(row["embedding"])
            score = cosine_similarity(query_vec, vec)
            if score >= min_score:
                results.append({
                    "store": "ltm",
                    "id": row["id"],
                    "content": row["content"],
                    "source_type": row["source_type"],
                    "source_id": row["source_id"],
                    "source_title": row["source_title"],
                    "domain": row["domain"],
                    "created_at": row["created_at"],
                    "strength": row["strength"],
                    "access_count": row["access_count"],
                    "score": score,
                    "tags": row["tags"],
                })

    # Check dormant LTM for reactivation
    if stores in ("both", "ltm") and not exclude_dormant:
        dormant_rows = db.execute("SELECT * FROM ltm_memories WHERE is_dormant = 1").fetchall()
        for row in dormant_rows:
            vec = _blob_to_array(row["embedding"])
            score = cosine_similarity(query_vec, vec)
            if score > 0.8:
                # Reactivate
                db.execute(
                    "UPDATE ltm_memories SET is_dormant = 0, strength = 0.5, last_accessed = datetime('now') WHERE id = ?",
                    (row["id"],)
                )
                reactivated_ids.add(("ltm", row["id"]))
                results.append({
                    "store": "ltm",
                    "id": row["id"],
                    "content": row["content"],
                    "source_type": row["source_type"],
                    "source_id": row["source_id"],
                    "source_title": row["source_title"],
                    "domain": row["domain"],
                    "created_at": row["created_at"],
                    "strength": 0.5,
                    "access_count": row["access_count"],
                    "score": score,
                    "tags": row["tags"],
                    "reactivated": True,
                })
        if reactivated_ids:
            db.commit()

    # Sort by score descending and take top_k
    results.sort(key=lambda x: x["score"], reverse=True)
    results = results[:top_k]

    # Rehearsal: update strength and access_count for returned results
    if rehearse and results:
        now = datetime.utcnow().isoformat()
        for r in results:
            if (r["store"], r["id"]) in reactivated_ids:
                continue
            table = "stm_memories" if r["store"] == "stm" else "ltm_memories"
            db.execute(
                f"UPDATE {table} SET strength = 1.0, access_count = access_count + 1, last_accessed = ? WHERE id = ?",
                (now, r["id"])
            )
        db.commit()

    # Log retrieval
    top_score = results[0]["score"] if results else 0.0
    db.execute(
        "INSERT INTO retrieval_log (query_text, results_count, top_score) VALUES (?, ?, ?)",
        (query_text[:500], len(results), top_score)
    )
    db.commit()

    return results


def ingest(
    content: str,
    source_type: str,
    source_id: str = "",
    source_title: str = "",
    domain: str = ""
) -> int:
    """Embed and store content in STM. Returns row ID."""
    db = _get_db()
    vec = embed(content)
    blob = _array_to_blob(vec)
    cur = db.execute(
        """INSERT INTO stm_memories (content, embedding, source_type, source_id, source_title, domain)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (content, blob, source_type, source_id, source_title, domain)
    )
    db.commit()
    return cur.lastrowid


def ingest_to_ltm(
    content: str,
    source_type: str,
    source_id: str = "",
    source_title: str = "",
    domain: str = "",
    tags: str = ""
) -> int:
    """Embed and store content directly in LTM. Returns row ID."""
    db = _get_db()
    vec = embed(content)
    blob = _array_to_blob(vec)
    cur = db.execute(
        """INSERT INTO ltm_memories (content, embedding, source_type, source_id, source_title, domain, tags)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (content, blob, source_type, source_id, source_title, domain, tags)
    )
    db.commit()
    return cur.lastrowid


def apply_decay():
    """Apply Ebbinghaus decay to all memories. Mark LTM as dormant if strength < 0.1."""
    db = _get_db()
    now = datetime.utcnow()

    # STM decay
    rows = db.execute("SELECT id, last_accessed, strength FROM stm_memories WHERE promoted_to_ltm = 0").fetchall()
    for row in rows:
        last = datetime.fromisoformat(row["last_accessed"])
        hours = (now - last).total_seconds() / 3600.0
        new_strength = row["strength"] * math.exp(-LAMBDA_STM * hours)
        db.execute("UPDATE stm_memories SET strength = ? WHERE id = ?", (new_strength, row["id"]))

    # LTM decay
    rows = db.execute("SELECT id, last_accessed, strength FROM ltm_memories WHERE is_dormant = 0").fetchall()
    for row in rows:
        last = datetime.fromisoformat(row["last_accessed"])
        hours = (now - last).total_seconds() / 3600.0
        new_strength = row["strength"] * math.exp(-LAMBDA_LTM * hours)
        if new_strength < 0.1:
            db.execute("UPDATE ltm_memories SET strength = ?, is_dormant = 1 WHERE id = ?", (new_strength, row["id"]))
        else:
            db.execute("UPDATE ltm_memories SET strength = ? WHERE id = ?", (new_strength, row["id"]))

    db.commit()


def promote_stm_to_ltm():
    """Promote STM memories with access_count >= 3 to LTM. Mark as promoted."""
    db = _get_db()
    rows = db.execute(
        "SELECT * FROM stm_memories WHERE access_count >= 3 AND promoted_to_ltm = 0"
    ).fetchall()

    promoted = 0
    for row in rows:
        db.execute(
            """INSERT INTO ltm_memories (content, embedding, source_type, source_id, source_title, domain, original_stm_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (row["content"], row["embedding"], row["source_type"], row["source_id"],
             row["source_title"], row["domain"], row["id"])
        )
        db.execute("UPDATE stm_memories SET promoted_to_ltm = 1 WHERE id = ?", (row["id"],))
        promoted += 1

    db.commit()
    return promoted


def gc_stm():
    """Garbage collect STM: delete weak old memories and anything > 30 days."""
    db = _get_db()
    now = datetime.utcnow()
    cutoff_7d = (now - timedelta(days=7)).isoformat()
    cutoff_30d = (now - timedelta(days=30)).isoformat()

    # Delete STM with strength < 0.3 and older than 7 days
    cur1 = db.execute(
        "DELETE FROM stm_memories WHERE strength < 0.3 AND created_at < ? AND promoted_to_ltm = 0",
        (cutoff_7d,)
    )
    # Delete any STM older than 30 days
    cur2 = db.execute(
        "DELETE FROM stm_memories WHERE created_at < ? AND promoted_to_ltm = 0",
        (cutoff_30d,)
    )
    db.commit()
    return (cur1.rowcount or 0) + (cur2.rowcount or 0)


def ingest_sensory(
    content: str,
    source_id: str = "",
    domain: str = "",
    created_at: str = ""
) -> int:
    """Embed and store a sensory register event in STM with source_type='sensory'."""
    db = _get_db()
    vec = embed(content)
    blob = _array_to_blob(vec)
    ts = created_at or datetime.utcnow().isoformat()
    cur = db.execute(
        """INSERT INTO stm_memories (content, embedding, source_type, source_id, domain, created_at)
           VALUES (?, ?, 'sensory', ?, ?, ?)""",
        (content, blob, source_id, domain, ts)
    )
    db.commit()
    return cur.lastrowid


def detect_patterns(content_vec: np.ndarray, threshold: float = 0.65) -> list[dict]:
    """Compare a vector against LTM to find matching patterns (potential repetitions)."""
    db = _get_db()
    rows = db.execute("SELECT id, content, embedding, source_type, domain FROM ltm_memories WHERE is_dormant = 0").fetchall()
    matches = []
    for row in rows:
        vec = _blob_to_array(row["embedding"])
        score = cosine_similarity(content_vec, vec)
        if score >= threshold:
            matches.append({
                "ltm_id": row["id"],
                "content": row["content"][:200],
                "source_type": row["source_type"],
                "domain": row["domain"],
                "score": score,
            })
    matches.sort(key=lambda x: x["score"], reverse=True)
    return matches[:5]


def gc_sensory(max_age_hours: int = 48) -> int:
    """Garbage collect sensory memories older than max_age_hours. Returns count deleted."""
    db = _get_db()
    cutoff = (datetime.utcnow() - timedelta(hours=max_age_hours)).isoformat()
    cur = db.execute(
        "DELETE FROM stm_memories WHERE source_type = 'sensory' AND created_at < ? AND promoted_to_ltm = 0",
        (cutoff,)
    )
    db.commit()
    return cur.rowcount or 0


def gc_ltm_dormant(min_age_days: int = 30) -> int:
    """Delete dormant LTM memories with strength < 0.1 older than min_age_days."""
    db = _get_db()
    cutoff = (datetime.utcnow() - timedelta(days=min_age_days)).isoformat()
    cur = db.execute(
        "DELETE FROM ltm_memories WHERE is_dormant = 1 AND strength < 0.1 AND created_at < ?",
        (cutoff,)
    )
    db.commit()
    return cur.rowcount or 0


def format_results(results: list[dict]) -> str:
    """Format search results with enriched context."""
    if not results:
        return "No results found."

    lines = []
    for r in results:
        score = r["score"]
        stype = r["source_type"].upper()
        domain = r.get("domain", "")
        title = r.get("source_title", "")
        content = r["content"]

        # Header
        domain_str = f" ({domain})" if domain else ""
        title_str = f': "{title}"' if title else ""
        header = f"[{score:.2f}] {stype}{domain_str}{title_str}"

        # Content preview (300 chars)
        preview = content[:300]
        if len(content) > 300:
            preview += "..."

        # Proto-procedural: detect sequential markers in change logs
        if r["source_type"] == "change" and any(m in content for m in ["1.", "2.", "3.", "step ", "Step ", "then ", "first ", "First "]):
            header += " [PROCEDURE]"

        store_tag = r["store"].upper()
        reactivated = " [REACTIVATED]" if r.get("reactivated") else ""
        lines.append(f"{header} [{store_tag}]{reactivated}\n  {preview}")

        # Sibling mention: if this LTM memory has siblings, note them
        if r["store"] == "ltm":
            try:
                siblings = get_siblings(r["id"])
                if siblings:
                    for sib in siblings[:2]:
                        disc_str = ", ".join(sib["discriminators"].split(",")[:3])
                        lines.append(f"  ↳ SIBLING #{sib['sibling_id']} ({sib['domain']}): differs in [{disc_str}] — {sib['content'][:80]}...")
            except Exception:
                pass

    return "\n\n".join(lines)


def get_metrics(days: int = 7) -> dict:
    """Calculate spec section 9 metrics over the last N days.

    Returns:
        retrieval_relevance: % of retrievals with top_score >= 0.6
        repeat_error_rate: % of new learnings that duplicate existing LTM (cosine > 0.8)
        avg_top_score: average best match score across all retrievals
        total_retrievals: number of retrievals in period
        retrievals_per_day: average retrievals per day
        score_distribution: histogram buckets [<0.5, 0.5-0.6, 0.6-0.7, 0.7-0.8, >0.8]
    """
    db = _get_db()
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()

    rows = db.execute(
        "SELECT top_score FROM retrieval_log WHERE created_at >= ?", (cutoff,)
    ).fetchall()

    total = len(rows)
    if total == 0:
        return {
            "period_days": days,
            "total_retrievals": 0,
            "retrieval_relevance_pct": 0.0,
            "avg_top_score": 0.0,
            "retrievals_per_day": 0.0,
            "score_distribution": {"below_50": 0, "50_60": 0, "60_70": 0, "70_80": 0, "above_80": 0},
            "needs_multilingual": False,
        }

    scores = [r[0] for r in rows]
    relevant = sum(1 for s in scores if s >= 0.6)
    relevance_pct = round(relevant / total * 100, 1)
    avg_score = round(sum(scores) / total, 3)

    dist = {"below_50": 0, "50_60": 0, "60_70": 0, "70_80": 0, "above_80": 0}
    for s in scores:
        if s < 0.5:
            dist["below_50"] += 1
        elif s < 0.6:
            dist["50_60"] += 1
        elif s < 0.7:
            dist["60_70"] += 1
        elif s < 0.8:
            dist["70_80"] += 1
        else:
            dist["above_80"] += 1

    # Check if multilingual model is needed (spec 13.3)
    needs_multilingual = relevance_pct < 70.0 and total >= 10

    return {
        "period_days": days,
        "total_retrievals": total,
        "retrieval_relevance_pct": relevance_pct,
        "avg_top_score": avg_score,
        "retrievals_per_day": round(total / days, 1),
        "score_distribution": dist,
        "needs_multilingual": needs_multilingual,
    }


def check_repeat_errors() -> dict:
    """Compare recent learnings in STM against LTM to find duplicates (spec section 9).

    Returns count of new learnings that are semantically duplicate (cosine > 0.8).
    """
    db = _get_db()
    cutoff_7d = (datetime.utcnow() - timedelta(days=7)).isoformat()

    # Recent learning STM entries
    new_learnings = db.execute(
        "SELECT id, content, embedding FROM stm_memories WHERE source_type = 'learning' AND created_at >= ? AND promoted_to_ltm = 0",
        (cutoff_7d,)
    ).fetchall()

    # All LTM learnings
    ltm_learnings = db.execute(
        "SELECT id, content, embedding FROM ltm_memories WHERE source_type = 'learning' AND is_dormant = 0"
    ).fetchall()

    if not new_learnings or not ltm_learnings:
        return {"new_count": len(new_learnings), "duplicate_count": 0, "repeat_rate_pct": 0.0, "duplicates": []}

    duplicates = []
    for new in new_learnings:
        new_vec = _blob_to_array(new["embedding"])
        for ltm in ltm_learnings:
            ltm_vec = _blob_to_array(ltm["embedding"])
            score = cosine_similarity(new_vec, ltm_vec)
            if score > 0.8:
                duplicates.append({
                    "new_stm_id": new["id"],
                    "new_content": new["content"][:100],
                    "ltm_id": ltm["id"],
                    "ltm_content": ltm["content"][:100],
                    "score": round(score, 3),
                })
                break  # One match is enough

    repeat_rate = round(len(duplicates) / len(new_learnings) * 100, 1) if new_learnings else 0.0

    return {
        "new_count": len(new_learnings),
        "duplicate_count": len(duplicates),
        "repeat_rate_pct": repeat_rate,
        "duplicates": duplicates[:10],
    }


def rehearse_by_content(content_keywords: str, source_type: str = ""):
    """Passive rehearsal: find and strengthen cognitive memories that match content from classic tools.

    Called when nexo_recall or nexo_learning_search return results. Strengthens matching
    memories without returning them (side effect only). This closes the rehearsal loop
    so memories accessed via keyword tools also get reinforced in the vector store.

    Args:
        content_keywords: Text to match against (e.g., learning title + content)
        source_type: Optional filter by source_type
    """
    if not content_keywords or len(content_keywords.strip()) < 10:
        return

    try:
        db = _get_db()
        query_vec = embed(content_keywords[:500])  # cap to avoid slow embedding
        if np.linalg.norm(query_vec) == 0:
            return

        now = datetime.utcnow().isoformat()

        # Search both stores for matches >= 0.7
        for table in ("stm_memories", "ltm_memories"):
            extra_where = ""
            if table == "stm_memories":
                extra_where = " AND promoted_to_ltm = 0"
            if table == "ltm_memories":
                extra_where = " AND is_dormant = 0"

            rows = db.execute(f"SELECT id, embedding FROM {table} WHERE 1=1{extra_where}").fetchall()
            for row in rows:
                vec = _blob_to_array(row["embedding"])
                score = cosine_similarity(query_vec, vec)
                if score >= 0.7:
                    db.execute(
                        f"UPDATE {table} SET strength = 1.0, access_count = access_count + 1, last_accessed = ? WHERE id = ?",
                        (now, row["id"])
                    )

        db.commit()
    except Exception:
        pass  # Rehearsal is best-effort, never block the main tool


def _extract_discriminators(text: str) -> set:
    """Extract discriminating entities from text (OS, platform, language, infra)."""
    words = set(text.lower().split())
    # Also check for multi-word patterns
    text_lower = text.lower()
    found = set()
    for entity in DISCRIMINATING_ENTITIES:
        if entity in words or entity in text_lower:
            found.add(entity)
    return found


def _memories_are_siblings(content_a: str, content_b: str) -> tuple[bool, list[str]]:
    """Check if two memories are siblings (similar-but-incompatible).

    Returns (is_sibling, list_of_discriminating_entities_that_differ).
    """
    disc_a = _extract_discriminators(content_a)
    disc_b = _extract_discriminators(content_b)

    # Entities present in one but not the other
    only_a = disc_a - disc_b
    only_b = disc_b - disc_a

    if only_a or only_b:
        # There are discriminating entities that differ — these are siblings
        diff = sorted(only_a | only_b)
        return True, diff

    return False, []


def consolidate_semantic(threshold: float = 0.9, dry_run: bool = False) -> dict:
    """Merge LTM memories with cosine similarity > threshold, with discriminative fusion.

    Before merging, checks for discriminating entities (OS, platform, language, etc.).
    If two memories are >90% similar but differ in critical entities, they become
    "siblings" (linked but NOT merged) instead of being consolidated.

    Args:
        threshold: Cosine similarity threshold for considering duplicates (default 0.9)
        dry_run: If True, return pairs without merging

    Returns:
        Dict with 'merged' (list of merge actions) and 'siblings' (list of sibling links created)
    """
    db = _get_db()
    rows = db.execute(
        "SELECT id, content, embedding, source_type, domain, access_count, strength FROM ltm_memories WHERE is_dormant = 0"
    ).fetchall()

    if len(rows) < 2:
        return {"merged": [], "siblings": []}

    memories = []
    for row in rows:
        memories.append({
            "id": row["id"],
            "content": row["content"],
            "vec": _blob_to_array(row["embedding"]),
            "source_type": row["source_type"],
            "domain": row["domain"],
            "access_count": row["access_count"],
            "strength": row["strength"],
        })

    merged_ids = set()
    merge_actions = []
    sibling_actions = []

    for i in range(len(memories)):
        if memories[i]["id"] in merged_ids:
            continue
        for j in range(i + 1, len(memories)):
            if memories[j]["id"] in merged_ids:
                continue

            score = cosine_similarity(memories[i]["vec"], memories[j]["vec"])
            if score < threshold:
                continue

            # Check for discriminating entities before merging
            is_sibling, discriminators = _memories_are_siblings(
                memories[i]["content"], memories[j]["content"]
            )

            if is_sibling:
                # Don't merge — create sibling relationship
                sibling_action = {
                    "memory_a_id": memories[i]["id"],
                    "memory_b_id": memories[j]["id"],
                    "score": round(score, 4),
                    "discriminators": discriminators,
                    "content_a": memories[i]["content"][:100],
                    "content_b": memories[j]["content"][:100],
                }

                if not dry_run:
                    try:
                        db.execute(
                            "INSERT OR IGNORE INTO memory_siblings (memory_a_id, memory_b_id, similarity, discriminators) VALUES (?, ?, ?, ?)",
                            (memories[i]["id"], memories[j]["id"], score, ",".join(discriminators))
                        )
                    except Exception:
                        pass

                sibling_actions.append(sibling_action)
                continue

            # Safe to merge — no discriminating entities differ
            if memories[i]["access_count"] >= memories[j]["access_count"]:
                keep, drop = memories[i], memories[j]
            else:
                keep, drop = memories[j], memories[i]

            action = {
                "keep_id": keep["id"],
                "drop_id": drop["id"],
                "score": round(score, 4),
                "keep_content": keep["content"][:100],
                "drop_content": drop["content"][:100],
                "keep_access": keep["access_count"],
                "drop_access": drop["access_count"],
            }

            if not dry_run:
                separator = "\n\n[CONSOLIDATED]: "
                new_content = keep["content"]
                drop_words = set(drop["content"].lower().split())
                keep_words = set(keep["content"].lower().split())
                unique_words = drop_words - keep_words
                if len(unique_words) > 5:
                    new_content = keep["content"] + separator + drop["content"]

                new_vec = embed(new_content)
                new_blob = _array_to_blob(new_vec)

                db.execute(
                    "UPDATE ltm_memories SET content = ?, embedding = ?, access_count = access_count + ? WHERE id = ?",
                    (new_content, new_blob, drop["access_count"], keep["id"])
                )
                db.execute("DELETE FROM ltm_memories WHERE id = ?", (drop["id"],))
                merged_ids.add(drop["id"])

            merge_actions.append(action)

    if not dry_run and (merge_actions or sibling_actions):
        db.commit()

    return {"merged": merge_actions, "siblings": sibling_actions}


def get_siblings(memory_id: int) -> list[dict]:
    """Get sibling memories for a given memory ID (similar-but-incompatible)."""
    db = _get_db()
    rows = db.execute(
        """SELECT s.*,
                  CASE WHEN s.memory_a_id = ? THEN s.memory_b_id ELSE s.memory_a_id END as sibling_id
           FROM memory_siblings s
           WHERE s.memory_a_id = ? OR s.memory_b_id = ?""",
        (memory_id, memory_id, memory_id)
    ).fetchall()

    siblings = []
    for row in rows:
        sib_id = row["sibling_id"]
        sib_mem = db.execute("SELECT content, domain, source_type FROM ltm_memories WHERE id = ?", (sib_id,)).fetchone()
        if sib_mem:
            siblings.append({
                "sibling_id": sib_id,
                "similarity": row["similarity"],
                "discriminators": row["discriminators"],
                "content": sib_mem["content"][:200],
                "domain": sib_mem["domain"],
            })
    return siblings


def detect_dissonance(new_instruction: str, min_score: float = 0.65) -> list[dict]:
    """Detect cognitive dissonance: find LTM memories that contradict a new instruction.

    When the user gives a new instruction that conflicts with established LTM memories
    (strength > 0.8), this function surfaces the conflict so NEXO can verbalize it
    rather than silently obeying or silently resisting.

    Args:
        new_instruction: The new instruction or preference from user
        min_score: Minimum cosine similarity to consider as potential conflict

    Returns:
        List of conflicting memories with their strength and content
    """
    db = _get_db()
    query_vec = embed(new_instruction[:500])
    if np.linalg.norm(query_vec) == 0:
        return []

    rows = db.execute(
        "SELECT id, content, embedding, source_type, domain, strength, access_count FROM ltm_memories WHERE is_dormant = 0 AND strength > 0.8"
    ).fetchall()

    conflicts = []
    for row in rows:
        vec = _blob_to_array(row["embedding"])
        score = cosine_similarity(query_vec, vec)
        if score >= min_score:
            conflicts.append({
                "memory_id": row["id"],
                "content": row["content"],
                "source_type": row["source_type"],
                "domain": row["domain"],
                "strength": row["strength"],
                "access_count": row["access_count"],
                "similarity": round(score, 3),
            })

    conflicts.sort(key=lambda x: x["similarity"], reverse=True)
    return conflicts[:5]


def resolve_dissonance(memory_id: int, resolution: str, context: str = "") -> str:
    """Resolve a cognitive dissonance by applying user's decision.

    Args:
        memory_id: The LTM memory that conflicts with the new instruction
        resolution: One of:
            - 'paradigm_shift': User changed his mind permanently. Decay old memory,
              new instruction becomes the standard.
            - 'exception': This is a one-time override. Keep old memory as standard.
            - 'override': Old memory was wrong. Mark as corrupted and decay to dormant.

    Returns:
        Status message
    """
    db = _get_db()
    row = db.execute("SELECT * FROM ltm_memories WHERE id = ?", (memory_id,)).fetchone()
    if not row:
        return f"Memory #{memory_id} not found."

    now = datetime.utcnow().isoformat()

    if resolution == "paradigm_shift":
        # Instant decay to 0.3, will naturally fade. New instruction takes over.
        db.execute(
            "UPDATE ltm_memories SET strength = 0.3, last_accessed = ? WHERE id = ?",
            (now, memory_id)
        )
        msg = f"Paradigm shift: Memory #{memory_id} decayed to 0.3. New standard will replace it."

    elif resolution == "exception":
        # Keep memory as-is, just log the exception
        msg = f"Exception noted: Memory #{memory_id} remains standard. One-time override applied."

    elif resolution == "override":
        # Memory was wrong — mark as corrupted/dormant
        db.execute(
            "UPDATE ltm_memories SET strength = 0.05, is_dormant = 1, last_accessed = ? WHERE id = ?",
            (now, memory_id)
        )
        msg = f"Override: Memory #{memory_id} marked corrupted and dormant."

    else:
        return f"Unknown resolution: {resolution}. Use 'paradigm_shift', 'exception', or 'override'."

    # Log the correction
    db.execute(
        "INSERT INTO memory_corrections (memory_id, store, correction_type, context) VALUES (?, 'ltm', ?, ?)",
        (memory_id, resolution, context[:500])
    )
    db.commit()

    return msg


def check_correction_fatigue() -> list[dict]:
    """Find memories corrected 3+ times in the last 7 days — mark as 'under review'.

    These memories are unreliable: the user keeps overriding them, suggesting
    the memory itself may be wrong or outdated.

    Returns:
        List of memories that should be flagged as unreliable
    """
    db = _get_db()
    cutoff = (datetime.utcnow() - timedelta(days=7)).isoformat()

    rows = db.execute("""
        SELECT memory_id, COUNT(*) as correction_count,
               GROUP_CONCAT(correction_type) as types
        FROM memory_corrections
        WHERE created_at >= ? AND store = 'ltm'
        GROUP BY memory_id
        HAVING COUNT(*) >= 3
    """, (cutoff,)).fetchall()

    fatigued = []
    for row in rows:
        mem = db.execute(
            "SELECT content, strength, source_type, domain FROM ltm_memories WHERE id = ?",
            (row["memory_id"],)
        ).fetchone()
        if mem:
            fatigued.append({
                "memory_id": row["memory_id"],
                "corrections_7d": row["correction_count"],
                "types": row["types"],
                "content": mem["content"][:200],
                "strength": mem["strength"],
                "source_type": mem["source_type"],
                "domain": mem["domain"],
            })

            # Auto-mark as under review: decay strength to 0.2
            db.execute(
                "UPDATE ltm_memories SET strength = MIN(strength, 0.2), tags = CASE WHEN tags LIKE '%under_review%' THEN tags ELSE tags || ',under_review' END WHERE id = ?",
                (row["memory_id"],)
            )

    if fatigued:
        db.commit()

    return fatigued


def detect_sentiment(text: str) -> dict:
    """Analyze user's text for sentiment signals.

    Returns detected sentiment, intensity, and action guidance for NEXO.
    Not a model — keyword + heuristic based. Fast and deterministic.
    """
    if not text:
        return {"sentiment": "neutral", "intensity": 0.5, "signals": [], "guidance": ""}

    text_lower = text.lower()
    words = set(text_lower.split())

    positive_hits = [s for s in POSITIVE_SIGNALS if s in text_lower]
    negative_hits = [s for s in NEGATIVE_SIGNALS if s in text_lower]
    urgency_hits = [s for s in URGENCY_SIGNALS if s in text_lower]

    # Heuristics
    is_short = len(text) < 30
    has_caps = any(c.isupper() for c in text[1:]) if len(text) > 1 else False  # ignore first char
    has_exclamation = "!" in text
    all_caps_words = sum(1 for w in text.split() if w.isupper() and len(w) > 1)

    # Score
    pos_score = len(positive_hits)
    neg_score = len(negative_hits)

    # Caps/short boost negative
    if all_caps_words >= 2:
        neg_score += 2
    if is_short and neg_score > 0:
        neg_score += 1  # Short + negative = terse frustration

    if urgency_hits:
        neg_score += 1  # Urgency often means something is wrong

    # Determine sentiment
    if neg_score > pos_score and neg_score >= 1:
        sentiment = "negative"
        intensity = min(1.0, 0.3 + neg_score * 0.15)
        if intensity > 0.7:
            guidance = "MODE: Ultra-conciso. Cero explicaciones. Resolver y mostrar resultado."
        else:
            guidance = "MODE: Conciso. Menos contexto, más acción directa."
    elif pos_score > neg_score and pos_score >= 1:
        sentiment = "positive"
        intensity = min(1.0, 0.3 + pos_score * 0.15)
        guidance = "MODE: Normal. Buen momento para proponer ideas de backlog o mejoras."
    elif urgency_hits:
        sentiment = "urgent"
        intensity = 0.8
        guidance = "MODE: Acción inmediata. Sin preámbulos."
    else:
        sentiment = "neutral"
        intensity = 0.5
        guidance = ""

    return {
        "sentiment": sentiment,
        "intensity": round(intensity, 2),
        "signals": positive_hits + negative_hits + urgency_hits,
        "guidance": guidance,
    }


def log_sentiment(text: str) -> dict:
    """Detect and log user's sentiment. Returns the detection result."""
    result = detect_sentiment(text)
    if result["sentiment"] != "neutral":
        db = _get_db()
        db.execute(
            "INSERT INTO sentiment_log (sentiment, intensity, signals) VALUES (?, ?, ?)",
            (result["sentiment"], result["intensity"], ",".join(result["signals"]))
        )
        db.commit()
    return result


def get_trust_score() -> float:
    """Get current trust score. Starts at 50, range 0-100."""
    db = _get_db()
    row = db.execute("SELECT score FROM trust_score ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        # Initialize
        db.execute(
            "INSERT INTO trust_score (score, event, delta, context) VALUES (50, 'init', 0, 'Initial trust score')"
        )
        db.commit()
        return 50.0
    return row[0]


def adjust_trust(event: str, context: str = "", custom_delta: float = None) -> dict:
    """Adjust trust score based on an event.

    Args:
        event: Event type from TRUST_EVENTS or custom
        context: Description of what happened
        custom_delta: Override the default point value

    Returns:
        Dict with old_score, delta, new_score, event
    """
    db = _get_db()
    old_score = get_trust_score()

    delta = custom_delta if custom_delta is not None else TRUST_EVENTS.get(event, 0)
    if delta == 0 and custom_delta is None:
        return {"old_score": old_score, "delta": 0, "new_score": old_score, "event": event, "error": "unknown event"}

    new_score = max(0.0, min(100.0, old_score + delta))

    db.execute(
        "INSERT INTO trust_score (score, event, delta, context) VALUES (?, ?, ?, ?)",
        (new_score, event, delta, context[:500])
    )
    db.commit()

    return {
        "old_score": round(old_score, 1),
        "delta": delta,
        "new_score": round(new_score, 1),
        "event": event,
    }


def get_trust_history(days: int = 7) -> dict:
    """Get trust score history and sentiment summary."""
    db = _get_db()
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()

    # Trust events
    events = db.execute(
        "SELECT event, delta, score, context, created_at FROM trust_score WHERE created_at >= ? ORDER BY id",
        (cutoff,)
    ).fetchall()

    # Sentiment distribution
    sentiments = db.execute(
        "SELECT sentiment, COUNT(*) as cnt, AVG(intensity) as avg_int FROM sentiment_log WHERE created_at >= ? GROUP BY sentiment",
        (cutoff,)
    ).fetchall()

    current = get_trust_score()
    start_score = events[0]["score"] - events[0]["delta"] if events else current

    return {
        "current_score": round(current, 1),
        "period_start_score": round(start_score, 1),
        "net_change": round(current - start_score, 1),
        "events": [{"event": e["event"], "delta": e["delta"], "score": round(e["score"], 1), "context": e["context"][:100], "at": e["created_at"]} for e in events],
        "sentiment_distribution": {s["sentiment"]: {"count": s["cnt"], "avg_intensity": round(s["avg_int"], 2)} for s in sentiments},
    }


def get_stats() -> dict:
    """Return statistics about the cognitive memory system."""
    db = _get_db()

    stm_active = db.execute("SELECT COUNT(*) FROM stm_memories WHERE promoted_to_ltm = 0").fetchone()[0]
    ltm_active = db.execute("SELECT COUNT(*) FROM ltm_memories WHERE is_dormant = 0").fetchone()[0]
    ltm_dormant = db.execute("SELECT COUNT(*) FROM ltm_memories WHERE is_dormant = 1").fetchone()[0]

    avg_stm = db.execute("SELECT AVG(strength) FROM stm_memories WHERE promoted_to_ltm = 0").fetchone()[0] or 0.0
    avg_ltm = db.execute("SELECT AVG(strength) FROM ltm_memories WHERE is_dormant = 0").fetchone()[0] or 0.0

    total_retrievals = db.execute("SELECT COUNT(*) FROM retrieval_log").fetchone()[0]
    avg_retrieval_score = db.execute("SELECT AVG(top_score) FROM retrieval_log").fetchone()[0] or 0.0

    top_domains_stm = db.execute(
        "SELECT domain, COUNT(*) as cnt FROM stm_memories WHERE promoted_to_ltm = 0 AND domain != '' GROUP BY domain ORDER BY cnt DESC LIMIT 5"
    ).fetchall()
    top_domains_ltm = db.execute(
        "SELECT domain, COUNT(*) as cnt FROM ltm_memories WHERE is_dormant = 0 AND domain != '' GROUP BY domain ORDER BY cnt DESC LIMIT 5"
    ).fetchall()

    return {
        "stm_active": stm_active,
        "ltm_active": ltm_active,
        "ltm_dormant": ltm_dormant,
        "avg_stm_strength": round(avg_stm, 3),
        "avg_ltm_strength": round(avg_ltm, 3),
        "total_retrievals": total_retrievals,
        "avg_retrieval_score": round(avg_retrieval_score, 3),
        "top_domains_stm": [(r["domain"], r["cnt"]) for r in top_domains_stm],
        "top_domains_ltm": [(r["domain"], r["cnt"]) for r in top_domains_ltm],
    }
