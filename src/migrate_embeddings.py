#!/usr/bin/env python3
"""
Migrate cognitive.db embeddings between models.

Usage:
  python migrate_embeddings.py upgrade   # re-embed to the current pinned model
  python migrate_embeddings.py rollback  # Restore from backup
  python migrate_embeddings.py verify    # Check current embedding dims and model pin
"""

import os
import shutil
import sqlite3
import sys
import time
import numpy as np

import paths
from local_models import build_fastembed_embedding, get_local_model_spec

NEXO_HOME = os.environ.get("NEXO_HOME", os.path.expanduser("~/.nexo"))
_cognitive_dir = paths.cognitive_dir()
_cognitive_dir.mkdir(parents=True, exist_ok=True)
DB_PATH = str(_cognitive_dir / "cognitive.db")
BACKUP_PATH = DB_PATH + ".bak-embedding-current-pre-upgrade"


def _base_embedding_dim() -> int:
    try:
        return int(get_local_model_spec("bge-base-embeddings").dimension or 384)
    except Exception:
        return 384


MODELS = {
    "small": ("bge-small-embeddings", 384),
    "base": ("bge-base-embeddings", _base_embedding_dim()),
}


def verify():
    """Check current embedding dimensions and current pinned embedding model."""
    model_name, expected_dim = MODELS["base"]
    spec = get_local_model_spec(model_name)
    print(f"  expected model: {spec.model_id}@{spec.revision} ({expected_dim} dims)")
    conn = sqlite3.connect(DB_PATH)
    try:
        for table in ["stm_memories", "ltm_memories"]:
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if count == 0:
                print(f"  {table}: {count} rows (empty)")
                continue
            row = conn.execute(f"SELECT embedding FROM {table} LIMIT 1").fetchone()
            vec = np.frombuffer(row[0], dtype=np.float32)
            print(f"  {table}: {count} rows, embedding dim = {len(vec)}")
    finally:
        conn.close()


def upgrade():
    """Re-embed all memories to the current pinned base embedding model."""
    # Verify current state
    print("Current state:")
    verify()

    # Verify backup exists
    if not os.path.exists(BACKUP_PATH):
        print(f"\nCreating backup at {BACKUP_PATH}")
        shutil.copy2(DB_PATH, BACKUP_PATH)
    else:
        print(f"\nBackup already exists at {BACKUP_PATH}")

    # Load new model
    model_name, expected_dim = MODELS["base"]
    spec = get_local_model_spec(model_name)
    print(f"\nLoading {spec.model_id}@{spec.revision}...")
    model = build_fastembed_embedding(model_name)

    conn = sqlite3.connect(DB_PATH)
    try:
        for table in ["stm_memories", "ltm_memories"]:
            rows = conn.execute(f"SELECT id, content FROM {table}").fetchall()
            if not rows:
                print(f"\n{table}: empty, skipping")
                continue

            print(f"\n{table}: re-embedding {len(rows)} memories...")
            t0 = time.time()

            # Batch embed for speed
            contents = [r[1] for r in rows]
            ids = [r[0] for r in rows]

            embeddings = list(model.embed(contents))

            for mem_id, emb in zip(ids, embeddings):
                blob = np.array(emb, dtype=np.float32).tobytes()
                conn.execute(f"UPDATE {table} SET embedding = ? WHERE id = ?", (blob, mem_id))

            conn.commit()
            elapsed = time.time() - t0
            print(f"  Done: {len(rows)} memories in {elapsed:.1f}s ({elapsed/len(rows)*1000:.0f}ms/memory)")
    finally:
        conn.close()

    print("\nAfter upgrade:")
    verify()
    print("\nUpgrade complete. Run 'verify' to confirm.")


def rollback():
    """Restore database from pre-upgrade backup."""
    if not os.path.exists(BACKUP_PATH):
        print(f"ERROR: Backup not found at {BACKUP_PATH}")
        sys.exit(1)

    print(f"Restoring from {BACKUP_PATH}...")
    shutil.copy2(BACKUP_PATH, DB_PATH)
    print("Restored. Current state:")
    verify()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python migrate_embeddings.py [upgrade|rollback|verify]")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "upgrade":
        upgrade()
    elif cmd == "rollback":
        rollback()
    elif cmd == "verify":
        verify()
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
