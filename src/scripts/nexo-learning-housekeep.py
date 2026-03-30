import os
#!/usr/bin/env python3
"""NEXO Learning Housekeeping — Nightly dedup, weight adjustment, and review.

Runs daily. Adjusts learning weights based on usage (guard_hits),
detects duplicates via semantic similarity, and archives stale learnings.
"""

import json
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))

sys.path.insert(0, str(NEXO_HOME / "nexo-mcp"))

DB_PATH = NEXO_HOME / "nexo-mcp" / "db" / "nexo.db"
STATE_FILE = NEXO_HOME / "operations" / ".catchup-state.json"

# Weight adjustment rates
GUARD_HIT_BOOST = 0.02       # per guard hit since last run
DECAY_RATE = 0.005            # daily decay for unused learnings
MIN_WEIGHT = 0.05
MAX_WEIGHT = 1.0
DEDUP_THRESHOLD = 0.85        # cosine similarity for duplicate detection
ARCHIVE_AFTER_DAYS = 90       # archive if weight < 0.1 and no hits in this many days


def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def update_catchup_state():
    try:
        state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    except Exception:
        state = {}
    state["learning-housekeep"] = datetime.now().isoformat()
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def adjust_weights(conn):
    """Boost weight for frequently-used learnings, decay unused ones."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    now = time.time()
    one_day_ago = now - 86400

    learnings = conn.execute(
        "SELECT id, weight, guard_hits, last_guard_hit_at, priority, created_at "
        "FROM learnings WHERE status = 'active'"
    ).fetchall()

    adjusted = 0
    for l in learnings:
        old_weight = l["weight"] or 0.5
        hits = l["guard_hits"] or 0
        last_hit = l["last_guard_hit_at"] or 0
        priority = l["priority"] or "medium"

        # Priority floor — critical learnings never drop below 0.5
        priority_floor = {"critical": 0.5, "high": 0.3, "medium": 0.1, "low": 0.05}[priority]

        new_weight = old_weight

        if last_hit > one_day_ago:
            # Recent guard hit — boost
            recent_hits = 1  # Simplified: at least 1 hit today
            new_weight = min(MAX_WEIGHT, old_weight + (GUARD_HIT_BOOST * recent_hits))
        else:
            # No recent hits — decay
            new_weight = max(priority_floor, old_weight - DECAY_RATE)

        new_weight = max(MIN_WEIGHT, min(MAX_WEIGHT, new_weight))

        if abs(new_weight - old_weight) > 0.001:
            conn.execute("UPDATE learnings SET weight = ? WHERE id = ?", (round(new_weight, 4), l["id"]))
            adjusted += 1

    conn.commit()
    print(f"[{ts}] Weight adjustment: {adjusted}/{len(learnings)} learnings adjusted")
    return adjusted


def auto_prioritize(conn):
    """Auto-upgrade priority based on guard hits and repetitions."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Learnings with 10+ guard hits that are still medium → upgrade to high
    upgraded = conn.execute(
        "UPDATE learnings SET priority = 'high', weight = MAX(weight, 0.7) "
        "WHERE status = 'active' AND priority = 'medium' AND guard_hits >= 10"
    ).rowcount

    # Learnings with repetitions (same error happened again) → upgrade to high
    repeated = conn.execute(
        """UPDATE learnings SET priority = 'high', weight = MAX(weight, 0.7)
           WHERE status = 'active' AND priority IN ('medium', 'low')
           AND id IN (SELECT original_learning_id FROM error_repetitions)"""
    ).rowcount

    conn.commit()
    total = upgraded + repeated
    if total > 0:
        print(f"[{ts}] Auto-prioritize: {upgraded} by guard_hits, {repeated} by repetitions")
    return total


def detect_duplicates(conn):
    """Find semantically similar learnings using fastembed."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        from fastembed import TextEmbedding
        import numpy as np
    except ImportError:
        print(f"[{ts}] Dedup skipped: fastembed not available")
        return []

    learnings = conn.execute(
        "SELECT id, title, content, weight, guard_hits FROM learnings WHERE status = 'active'"
    ).fetchall()

    if len(learnings) < 2:
        return []

    model = TextEmbedding("BAAI/bge-base-en-v1.5")
    texts = [f"{l['title']}: {l['content'][:300]}" for l in learnings]
    embeddings = list(model.embed(texts))
    embeddings = np.array(embeddings)

    # Normalize
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms[norms == 0] = 1
    embeddings = embeddings / norms

    duplicates = []
    for i in range(len(learnings)):
        for j in range(i + 1, len(learnings)):
            sim = float(np.dot(embeddings[i], embeddings[j]))
            if sim >= DEDUP_THRESHOLD:
                # Keep the one with higher weight/hits
                a, b = learnings[i], learnings[j]
                score_a = (a["weight"] or 0.5) + (a["guard_hits"] or 0) * 0.01
                score_b = (b["weight"] or 0.5) + (b["guard_hits"] or 0) * 0.01
                keep, drop = (a, b) if score_a >= score_b else (b, a)
                duplicates.append({
                    "keep_id": keep["id"], "keep_title": keep["title"],
                    "drop_id": drop["id"], "drop_title": drop["title"],
                    "similarity": round(sim, 3)
                })

    if duplicates:
        print(f"[{ts}] Duplicates found: {len(duplicates)} pairs (>= {DEDUP_THRESHOLD})")
        for d in duplicates[:10]:
            print(f"[{ts}]   [{d['similarity']}] keep #{d['keep_id']} '{d['keep_title'][:40]}', archive #{d['drop_id']} '{d['drop_title'][:40]}'")
            # Archive the duplicate (don't delete — just mark inactive)
            conn.execute("UPDATE learnings SET status = 'archived' WHERE id = ?", (d["drop_id"],))
        conn.commit()
    else:
        print(f"[{ts}] No duplicates found ({len(learnings)} learnings scanned)")

    return duplicates


def archive_stale(conn):
    """Archive learnings with very low weight and no recent guard hits."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cutoff = time.time() - (ARCHIVE_AFTER_DAYS * 86400)

    stale = conn.execute(
        "SELECT id, title, weight, last_guard_hit_at FROM learnings "
        "WHERE status = 'active' AND weight < 0.1 AND priority NOT IN ('critical', 'high') "
        "AND (last_guard_hit_at IS NULL OR last_guard_hit_at < ?)",
        (cutoff,)
    ).fetchall()

    if stale:
        for s in stale:
            conn.execute("UPDATE learnings SET status = 'archived' WHERE id = ?", (s["id"],))
            print(f"[{ts}]   Archived #{s['id']} '{s['title'][:50]}' (weight={s['weight']:.2f})")
        conn.commit()
        print(f"[{ts}] Archived {len(stale)} stale learnings")
    else:
        print(f"[{ts}] No stale learnings to archive")

    return len(stale)


def print_summary(conn):
    """Print summary stats."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    stats = conn.execute(
        """SELECT
            COUNT(*) as total,
            SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) as active,
            SUM(CASE WHEN status = 'archived' THEN 1 ELSE 0 END) as archived,
            SUM(CASE WHEN priority = 'critical' THEN 1 ELSE 0 END) as critical,
            SUM(CASE WHEN priority = 'high' THEN 1 ELSE 0 END) as high,
            SUM(CASE WHEN priority = 'medium' THEN 1 ELSE 0 END) as medium,
            SUM(CASE WHEN priority = 'low' THEN 1 ELSE 0 END) as low,
            printf('%.2f', AVG(CASE WHEN status = 'active' THEN weight END)) as avg_weight
        FROM learnings"""
    ).fetchone()
    print(f"[{ts}] Summary: {stats['active']} active, {stats['archived']} archived | "
          f"Priority: {stats['critical']}C {stats['high']}H {stats['medium']}M {stats['low']}L | "
          f"Avg weight: {stats['avg_weight']}")


def main():
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] Learning housekeeping starting...")

    conn = get_db()

    # 1. Adjust weights based on usage
    adjust_weights(conn)

    # 2. Auto-prioritize based on guard hits and repetitions
    auto_prioritize(conn)

    # 3. Detect and archive duplicates
    detect_duplicates(conn)

    # 4. Archive stale learnings
    archive_stale(conn)

    # 5. Summary
    print_summary(conn)

    conn.close()
    update_catchup_state()
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Done.")


if __name__ == "__main__":
    main()
