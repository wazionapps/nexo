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
    db.commit()


def add_claim(text: str, source_type: str = "", source_id: str = "",
              source_memory_store: str = "", source_memory_id: int = 0,
              confidence: float = 1.0, domain: str = "") -> dict:
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
        db.execute("UPDATE claims SET confidence = ?, updated_at = datetime('now') WHERE id = ?",
                   (new_conf, dup["id"]))
        db.commit()
        return {"id": dup["id"], "action": "merged", "confidence": new_conf}

    cursor = db.execute(
        """INSERT INTO claims (text, embedding, source_type, source_id,
           source_memory_store, source_memory_id, confidence, domain)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (text, blob, source_type, source_id, source_memory_store,
         source_memory_id, confidence, domain)
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
            d = dict(row)
            d.pop("embedding", None)
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
        "updated_at = datetime('now') WHERE id = ?",
        (status, claim_id)
    )
    db.commit()
    row = db.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone()
    if row:
        d = dict(row)
        d.pop("embedding", None)
        return d
    return {"error": f"Claim {claim_id} not found"}


def get_claim(claim_id: int) -> Optional[dict]:
    """Get a single claim with its links."""
    db = _get_db()
    init_tables()

    row = db.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone()
    if not row:
        return None

    d = dict(row)
    d.pop("embedding", None)

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
    return [dict(r) for r in rows]


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

    return {
        "total_claims": total,
        "by_status": by_status,
        "by_domain": by_domain,
        "total_links": links,
        "contradictions": contradictions,
    }
