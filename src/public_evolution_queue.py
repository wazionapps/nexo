from __future__ import annotations

"""Durable queue for public-core ports discovered outside the public runner.

Managed flows such as self-audit may apply a local/core fix inline. When that
fix belongs in the public repository as well, we persist a normalized queue
entry in ``evolution_log`` so the weekly public contribution cycle can port it
later instead of losing the improvement inside one machine.
"""

import json
import os
import sqlite3
from pathlib import Path


NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo"))).expanduser()
QUEUE_CLASSIFICATION = "public_port_queue"
QUEUE_STATUS_PENDING = "pending_public_port"
PUBLIC_ALLOWED_PREFIXES = (
    "src/",
    "bin/",
    "tests/",
    "templates/",
    "hooks/",
    "migrations/",
    ".claude-plugin/",
)


def resolve_repo_root(nexo_code: str | os.PathLike[str] | None = None) -> Path | None:
    raw = Path(
        nexo_code
        or os.environ.get("NEXO_CODE")
        or str(NEXO_HOME)
    ).expanduser()
    candidates = []
    if raw.name == "src":
        candidates.append(raw.parent)
    candidates.append(raw)
    for candidate in candidates:
        if (candidate / "package.json").exists():
            return candidate.resolve()
    return None


def normalize_public_path(
    filepath: str,
    *,
    repo_root: Path | None = None,
) -> str:
    text = str(filepath or "").strip()
    if not text:
        return ""

    normalized_raw = text.replace("\\", "/").lstrip("./")
    if any(
        normalized_raw == prefix.rstrip("/")
        or normalized_raw.startswith(prefix)
        for prefix in PUBLIC_ALLOWED_PREFIXES
    ):
        return normalized_raw

    repo_root = repo_root or resolve_repo_root()
    if not repo_root:
        return ""

    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        candidate = repo_root / candidate
    try:
        rel = candidate.resolve().relative_to(repo_root.resolve()).as_posix()
    except Exception:
        for prefix in PUBLIC_ALLOWED_PREFIXES:
            marker = normalized_raw.find(prefix)
            if marker >= 0:
                return normalized_raw[marker:]
        return ""
    if any(rel == prefix.rstrip("/") or rel.startswith(prefix) for prefix in PUBLIC_ALLOWED_PREFIXES):
        return rel
    return ""


def is_public_core_path(filepath: str, *, repo_root: Path | None = None) -> bool:
    return bool(normalize_public_path(filepath, repo_root=repo_root))


def queue_public_port_candidate(
    conn: sqlite3.Connection,
    *,
    title: str,
    reasoning: str,
    files_changed: list[str],
    source: str,
    metadata: dict | None = None,
) -> dict:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='evolution_log'"
    ).fetchone()
    if not row:
        return {"ok": False, "reason": "evolution_log_missing"}

    repo_root = resolve_repo_root()
    normalized_files: list[str] = []
    seen: set[str] = set()
    for filepath in files_changed:
        rel = normalize_public_path(filepath, repo_root=repo_root)
        if rel and rel not in seen:
            normalized_files.append(rel)
            seen.add(rel)
    if not normalized_files:
        return {"ok": False, "reason": "no_public_files"}

    proposal = str(title or "").strip()[:300] or "Managed core autofix queued for public port"
    clean_reasoning = str(reasoning or "").strip()[:4000] or "Queued for public-core port."
    payload = dict(metadata or {})
    payload.setdefault("source", source)
    payload["files"] = normalized_files

    existing = conn.execute(
        """SELECT id, status
           FROM evolution_log
           WHERE classification = ?
             AND proposal = ?
             AND files_changed = ?
             AND status IN (?, 'draft_pr_created', 'skipped_duplicate_existing_pr')
           ORDER BY id DESC
           LIMIT 1""",
        (
            QUEUE_CLASSIFICATION,
            proposal,
            json.dumps(normalized_files),
            QUEUE_STATUS_PENDING,
        ),
    ).fetchone()
    if existing:
        return {
            "ok": True,
            "queued": False,
            "log_id": int(existing["id"]),
            "status": str(existing["status"] or ""),
            "files_changed": normalized_files,
        }

    cur = conn.execute(
        """INSERT INTO evolution_log (
               cycle_number, dimension, proposal, classification, reasoning, status, files_changed, test_result
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            0,
            "public_core",
            proposal,
            QUEUE_CLASSIFICATION,
            clean_reasoning,
            QUEUE_STATUS_PENDING,
            json.dumps(normalized_files),
            json.dumps(payload, ensure_ascii=False),
        ),
    )
    return {
        "ok": True,
        "queued": True,
        "log_id": int(cur.lastrowid),
        "status": QUEUE_STATUS_PENDING,
        "files_changed": normalized_files,
    }


def list_pending_public_port_candidates(
    conn: sqlite3.Connection,
    *,
    limit: int = 3,
) -> list[dict]:
    rows = conn.execute(
        """SELECT id, created_at, proposal, reasoning, status, files_changed, test_result
           FROM evolution_log
           WHERE classification = ?
             AND status = ?
           ORDER BY created_at ASC, id ASC
           LIMIT ?""",
        (QUEUE_CLASSIFICATION, QUEUE_STATUS_PENDING, max(1, int(limit))),
    ).fetchall()
    results: list[dict] = []
    for row in rows:
        metadata = {}
        raw_payload = str(row["test_result"] or "").strip()
        if raw_payload:
            try:
                parsed = json.loads(raw_payload)
                if isinstance(parsed, dict):
                    metadata = parsed
            except Exception:
                metadata = {"raw": raw_payload}
        files_changed = []
        raw_files = str(row["files_changed"] or "").strip()
        if raw_files:
            try:
                parsed_files = json.loads(raw_files)
                if isinstance(parsed_files, list):
                    files_changed = [str(item).strip() for item in parsed_files if str(item).strip()]
            except Exception:
                pass
        results.append(
            {
                "id": int(row["id"]),
                "created_at": str(row["created_at"] or ""),
                "title": str(row["proposal"] or ""),
                "reasoning": str(row["reasoning"] or ""),
                "status": str(row["status"] or ""),
                "files_changed": files_changed,
                "metadata": metadata,
            }
        )
    return results


def update_public_port_candidate(
    conn: sqlite3.Connection,
    log_id: int,
    *,
    status: str,
    metadata_patch: dict | None = None,
) -> None:
    row = conn.execute(
        "SELECT test_result FROM evolution_log WHERE id = ? LIMIT 1",
        (int(log_id),),
    ).fetchone()
    payload: dict = {}
    if row and str(row["test_result"] or "").strip():
        try:
            parsed = json.loads(str(row["test_result"]))
            if isinstance(parsed, dict):
                payload = parsed
        except Exception:
            payload = {"raw": str(row["test_result"])}
    if metadata_patch:
        payload.update(metadata_patch)
    conn.execute(
        "UPDATE evolution_log SET status = ?, test_result = ? WHERE id = ?",
        (status, json.dumps(payload, ensure_ascii=False), int(log_id)),
    )
