"""NEXO Claim Graph — Atomic claims with provenance and contradiction detection.

Decomposes blob memories into individual verifiable facts. Each claim has a
source memory, confidence score, and can be linked to contradicting claims.

Tables (created in cognitive.db alongside KG):
- claims: atomic facts with provenance
- claim_links: relationships between claims (supports, contradicts, refines)
"""

import json
import sqlite3
import numpy as np
from datetime import datetime, timezone
from typing import Optional


def _get_db():
    """Get cognitive.db connection."""
    import cognitive
    return cognitive._get_db()


def _embed(text: str) -> np.ndarray:
    import cognitive
    return cognitive.embed(text)


def _cosine_similarity(a, b) -> float:
    import cognitive
    return cognitive.cosine_similarity(a, b)


def _array_to_blob(arr: np.ndarray) -> bytes:
    return arr.astype(np.float32).tobytes()


def _blob_to_array(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


def _table_columns(table_name: str) -> set[str]:
    db = _get_db()
    rows = db.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row["name"]) for row in rows}


def _ensure_column(table_name: str, column_sql: str) -> None:
    name = column_sql.split()[0]
    if name in _table_columns(table_name):
        return
    db = _get_db()
    db.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}")
    db.commit()


def _parse_timestamp(raw: str | None) -> datetime | None:
    if not raw:
        return None
    value = str(raw).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(value)
    except Exception:
        try:
            dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        except Exception:
            return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _compute_freshness(row: dict) -> tuple[float, str, int]:
    freshness_days = max(1, int(row.get("freshness_days") or 30))
    status = str(row.get("verification_status") or "unverified")
    anchor = (
        _parse_timestamp(row.get("verified_at"))
        or _parse_timestamp(row.get("last_reviewed_at"))
        or _parse_timestamp(row.get("updated_at"))
        or _parse_timestamp(row.get("created_at"))
        or datetime.now(timezone.utc)
    )
    age_days = max(0, int((datetime.now(timezone.utc) - anchor).total_seconds() // 86400))
    score = max(0.0, 1.0 - (age_days / float(freshness_days)))
    if status == "contradicted":
        score = min(score, 0.05)
    elif status == "outdated":
        score = min(score, 0.25)
    if age_days > freshness_days:
        state = "stale"
    elif age_days > max(1, freshness_days // 2):
        state = "aging"
    else:
        state = "fresh"
    return round(score, 3), state, age_days


def _claim_with_derived_fields(row: dict) -> dict:
    item = dict(row)
    item.pop("embedding", None)
    score, state, age_days = _compute_freshness(item)
    item["freshness_score"] = score
    item["freshness_state"] = state
    item["age_days"] = age_days
    return item


def init_tables():
    """Create claim graph tables if they don't exist."""
    db = _get_db()
    db.executescript("""
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
            evidence TEXT DEFAULT '',
            freshness_days INTEGER DEFAULT 30,
            freshness_score REAL DEFAULT 1.0,
            last_reviewed_at TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS claim_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_claim_id INTEGER NOT NULL REFERENCES claims(id),
            target_claim_id INTEGER NOT NULL REFERENCES claims(id),
            relation TEXT NOT NULL,
            confidence REAL DEFAULT 1.0,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(source_claim_id, target_claim_id, relation)
        );

        CREATE INDEX IF NOT EXISTS idx_claims_source ON claims(source_type, source_id);
        CREATE INDEX IF NOT EXISTS idx_claims_domain ON claims(domain);
        CREATE INDEX IF NOT EXISTS idx_claims_status ON claims(verification_status);
        CREATE INDEX IF NOT EXISTS idx_claim_links_source ON claim_links(source_claim_id);
        CREATE INDEX IF NOT EXISTS idx_claim_links_target ON claim_links(target_claim_id);
    """)
    _ensure_column("claims", "evidence TEXT DEFAULT ''")
    _ensure_column("claims", "freshness_days INTEGER DEFAULT 30")
    _ensure_column("claims", "freshness_score REAL DEFAULT 1.0")
    _ensure_column("claims", "last_reviewed_at TEXT")
    db.commit()


def add_claim(text: str, source_type: str = "", source_id: str = "",
              source_memory_store: str = "", source_memory_id: int = 0,
              confidence: float = 1.0, domain: str = "",
              evidence: str = "", freshness_days: int = 30) -> dict:
    """Add an atomic claim to the graph.

    Returns the claim dict with id, or existing claim if duplicate detected.
    """
    db = _get_db()
    init_tables()

    # Embed the claim
    vec = _embed(text)
    blob = _array_to_blob(vec)

    # Check for near-duplicate claims (similarity > 0.92)
    existing = find_similar_claims(text, threshold=0.92, limit=1)
    if existing:
        # Update confidence if new source provides additional evidence
        dup = existing[0]
        new_conf = min(1.0, dup["confidence"] + 0.1)
        merged_evidence = str(dup.get("evidence") or "").strip()
        new_evidence = str(evidence or "").strip()
        if new_evidence and new_evidence not in merged_evidence:
            merged_evidence = f"{merged_evidence}\n{new_evidence}".strip()
        db.execute(
            "UPDATE claims SET confidence = ?, evidence = ?, freshness_days = ?, freshness_score = 1.0, updated_at = datetime('now') WHERE id = ?",
            (new_conf, merged_evidence, max(1, int(freshness_days or 30)), dup["id"]),
        )
        db.commit()
        return {"id": dup["id"], "action": "merged", "confidence": new_conf}

    cursor = db.execute(
        """INSERT INTO claims (text, embedding, source_type, source_id,
           source_memory_store, source_memory_id, confidence, domain, evidence, freshness_days, freshness_score)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1.0)""",
        (text, blob, source_type, source_id, source_memory_store,
         source_memory_id, confidence, domain, str(evidence or "").strip(), max(1, int(freshness_days or 30)))
    )
    db.commit()
    return {"id": cursor.lastrowid, "action": "added", "confidence": confidence}


def find_similar_claims(text: str, threshold: float = 0.8, limit: int = 10) -> list[dict]:
    """Find claims similar to the given text using cosine similarity."""
    db = _get_db()
    init_tables()

    query_vec = _embed(text)
    if np.linalg.norm(query_vec) == 0:
        return []

    rows = db.execute("SELECT * FROM claims").fetchall()
    results = []
    for row in rows:
        if not row["embedding"]:
            continue
        vec = _blob_to_array(row["embedding"])
        score = _cosine_similarity(query_vec, vec)
        if score >= threshold:
            d = _claim_with_derived_fields(dict(row))
            d["similarity"] = round(score, 4)
            results.append(d)

    results.sort(key=lambda x: x["similarity"], reverse=True)
    return results[:limit]


def link_claims(source_id: int, target_id: int, relation: str,
                confidence: float = 1.0) -> dict:
    """Create a relationship between two claims.

    Relations:
        - 'supports': source provides evidence for target
        - 'contradicts': source contradicts target
        - 'refines': source is a more specific version of target
        - 'supersedes': source replaces target (target is outdated)
    """
    db = _get_db()
    init_tables()

    valid_relations = {"supports", "contradicts", "refines", "supersedes"}
    if relation not in valid_relations:
        return {"error": f"Invalid relation. Use: {valid_relations}"}

    try:
        db.execute(
            "INSERT OR IGNORE INTO claim_links (source_claim_id, target_claim_id, relation, confidence) "
            "VALUES (?, ?, ?, ?)",
            (source_id, target_id, relation, confidence)
        )
        db.commit()

        # If contradiction, update verification status
        if relation == "contradicts":
            db.execute(
                "UPDATE claims SET verification_status = 'contradicted', updated_at = datetime('now') WHERE id = ?",
                (target_id,)
            )
            db.commit()

        return {"source": source_id, "target": target_id, "relation": relation}
    except Exception as e:
        return {"error": str(e)}


def detect_contradictions(claim_id: int) -> list[dict]:
    """Find claims that potentially contradict the given claim."""
    db = _get_db()
    init_tables()

    claim = db.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone()
    if not claim:
        return []

    claim_vec = _blob_to_array(claim["embedding"])

    # Find similar claims from different sources
    rows = db.execute(
        "SELECT * FROM claims WHERE id != ? AND domain = ?",
        (claim_id, claim["domain"])
    ).fetchall()

    contradictions = []
    for row in rows:
        if not row["embedding"]:
            continue
        vec = _blob_to_array(row["embedding"])
        sim = _cosine_similarity(claim_vec, vec)
        # High similarity but different source = potential contradiction
        if 0.6 <= sim <= 0.9 and row["source_id"] != claim["source_id"]:
            d = dict(row)
            d.pop("embedding", None)
            d["similarity"] = round(sim, 4)
            contradictions.append(d)

    return contradictions


def verify_claim(claim_id: int, status: str = "confirmed") -> dict:
    """Update verification status of a claim.

    Status: 'confirmed', 'contradicted', 'outdated', 'unverified'
    """
    db = _get_db()
    init_tables()

    valid = {"confirmed", "contradicted", "outdated", "unverified"}
    if status not in valid:
        return {"error": f"Invalid status. Use: {valid}"}

    db.execute(
        "UPDATE claims SET verification_status = ?, verified_at = datetime('now'), "
        "last_reviewed_at = datetime('now'), freshness_score = 1.0, updated_at = datetime('now') WHERE id = ?",
        (status, claim_id)
    )
    db.commit()
    row = db.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone()
    if row:
        return _claim_with_derived_fields(dict(row))
    return {"error": f"Claim {claim_id} not found"}


def get_claim(claim_id: int) -> Optional[dict]:
    """Get a single claim with its links."""
    db = _get_db()
    init_tables()

    row = db.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone()
    if not row:
        return None

    d = _claim_with_derived_fields(dict(row))

    # Get links
    outgoing = db.execute(
        "SELECT cl.*, c.text as target_text FROM claim_links cl "
        "JOIN claims c ON c.id = cl.target_claim_id "
        "WHERE cl.source_claim_id = ?", (claim_id,)
    ).fetchall()
    incoming = db.execute(
        "SELECT cl.*, c.text as source_text FROM claim_links cl "
        "JOIN claims c ON c.id = cl.source_claim_id "
        "WHERE cl.target_claim_id = ?", (claim_id,)
    ).fetchall()

    d["links_out"] = [dict(r) for r in outgoing]
    d["links_in"] = [dict(r) for r in incoming]
    return d


def search_claims(query: str = "", domain: str = "", status: str = "",
                  limit: int = 20) -> list[dict]:
    """Search claims by text, domain, and/or status."""
    db = _get_db()
    init_tables()

    if query:
        return find_similar_claims(query, threshold=0.5, limit=limit)

    conditions = []
    params = []
    if domain:
        conditions.append("domain = ?")
        params.append(domain)
    if status:
        conditions.append("verification_status = ?")
        params.append(status)

    where = " AND ".join(conditions) if conditions else "1=1"
    rows = db.execute(
        f"SELECT id, text, source_type, source_id, confidence, verification_status, "
        f"domain, created_at FROM claims WHERE {where} ORDER BY created_at DESC LIMIT ?",
        params + [limit]
    ).fetchall()
    return [_claim_with_derived_fields(dict(r)) for r in rows]


def lint_claims(max_age_days: int = 30, limit: int = 20) -> list[dict]:
    """Return stale, weak, or contradictory claims that need review."""
    db = _get_db()
    init_tables()

    rows = db.execute(
        "SELECT * FROM claims ORDER BY updated_at DESC, created_at DESC LIMIT 500"
    ).fetchall()
    results = []
    for row in rows:
        item = _claim_with_derived_fields(dict(row))
        reasons = []
        if item["verification_status"] == "unverified" and item["age_days"] >= max_age_days:
            reasons.append("unverified-too-old")
        if item["freshness_state"] == "stale":
            reasons.append("stale")
        if item["verification_status"] in {"contradicted", "outdated"}:
            reasons.append(item["verification_status"])
        if not str(item.get("evidence") or "").strip():
            reasons.append("missing-evidence")
        if reasons:
            item["lint_reasons"] = reasons
            results.append(item)
    results.sort(
        key=lambda item: (
            "contradicted" not in item["lint_reasons"],
            "stale" not in item["lint_reasons"],
            item["freshness_score"],
            -item["age_days"],
        )
    )
    return results[: max(1, int(limit or 20))]


def stats() -> dict:
    """Claim graph statistics."""
    db = _get_db()
    init_tables()

    total = db.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
    by_status = {}
    for row in db.execute(
        "SELECT verification_status, COUNT(*) as cnt FROM claims GROUP BY verification_status"
    ).fetchall():
        by_status[row["verification_status"]] = row["cnt"]
    by_domain = {}
    for row in db.execute(
        "SELECT domain, COUNT(*) as cnt FROM claims WHERE domain != '' GROUP BY domain ORDER BY cnt DESC LIMIT 10"
    ).fetchall():
        by_domain[row["domain"]] = row["cnt"]
    links = db.execute("SELECT COUNT(*) FROM claim_links").fetchone()[0]
    contradictions = db.execute(
        "SELECT COUNT(*) FROM claim_links WHERE relation = 'contradicts'"
    ).fetchone()[0]
    stale = len(lint_claims(max_age_days=30, limit=10000))

    return {
        "total_claims": total,
        "by_status": by_status,
        "by_domain": by_domain,
        "total_links": links,
        "contradictions": contradictions,
        "lint_attention": stale,
    }
