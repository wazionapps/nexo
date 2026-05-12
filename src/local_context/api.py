from __future__ import annotations

import json
import os
import shutil
import stat
import hashlib
import subprocess
import sys
from pathlib import Path
from typing import Any

from db import get_db, init_db
from db._schema import run_migrations

from . import embeddings
from .extractors import chunk_text, entities, extract_text, summarize
from .logging import log_event, tail
from .privacy import classify_path, should_extract
from .util import content_hash, json_dumps, json_loads, norm_path, now, quick_fingerprint, redact_path, stable_id, system_label, tokenize

LOCAL_INDEX_SERVICE_LABEL = "com.nexo.local-index"
LOCAL_INDEX_SCRIPT_NAME = "nexo-local-index.py"
LOCAL_INDEX_WINDOWS_TASK = "NEXO Local Memory"
LOCAL_INDEX_LINUX_UNIT = "nexo-local-index.service"


def ensure_ready() -> None:
    init_db()
    run_migrations()


def _conn():
    ensure_ready()
    return get_db()


def add_root(path: str, *, mode: str = "normal", depth: int | None = None) -> dict:
    conn = _conn()
    root_path = norm_path(path)
    depth_value = 2 if depth is None else int(depth)
    conn.execute(
        """
        INSERT INTO local_index_roots(root_path, display_path, mode, depth, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'active', ?, ?)
        ON CONFLICT(root_path) DO UPDATE SET
          display_path=excluded.display_path,
          mode=excluded.mode,
          depth=excluded.depth,
          status='active',
          updated_at=excluded.updated_at
        """,
        (root_path, path, mode, depth_value, now(), now()),
    )
    conn.commit()
    log_event("info", "root_added", "Root added", path=redact_path(root_path), mode=mode, depth=depth_value)
    return {"ok": True, "root_path": root_path, "mode": mode, "depth": depth_value}


def remove_root(path: str) -> dict:
    conn = _conn()
    root_path = norm_path(path)
    conn.execute("UPDATE local_index_roots SET status='removed', updated_at=? WHERE root_path=?", (now(), root_path))
    conn.commit()
    log_event("info", "root_removed", "Root removed", path=redact_path(root_path))
    return {"ok": True, "root_path": root_path}


def list_roots() -> list[dict]:
    conn = _conn()
    rows = conn.execute("SELECT * FROM local_index_roots WHERE status != 'removed' ORDER BY root_path").fetchall()
    return [dict(row) for row in rows]


def _dedupe_roots(roots: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for root in roots:
        normalized = norm_path(root)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _mounted_volume_roots() -> list[str]:
    candidates: list[Path] = []
    if sys.platform == "darwin":
        candidates.extend((Path("/Volumes")).iterdir() if Path("/Volumes").is_dir() else [])
    elif sys.platform.startswith("win"):
        candidates.extend(Path(f"{letter}:\\") for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    else:
        user = os.environ.get("USER") or os.environ.get("USERNAME") or ""
        mount_bases = [Path("/mnt")]
        if user:
            mount_bases.extend([Path("/media") / user, Path("/run/media") / user])
        for base in mount_bases:
            if base.is_dir():
                candidates.extend(base.iterdir())

    roots: list[str] = []
    root_resolved = Path("/").resolve()
    for candidate in candidates:
        try:
            if candidate.name.startswith(".") or not candidate.is_dir():
                continue
            resolved = candidate.resolve()
            if resolved == root_resolved:
                continue
            roots.append(str(candidate))
        except Exception:
            continue
    return roots


def default_roots() -> list[str]:
    home = Path.home()
    configured = os.environ.get("NEXO_LOCAL_INDEX_DEFAULT_ROOTS", "").strip()
    if configured:
        return _dedupe_roots([item for item in configured.split(os.pathsep) if item.strip()])
    return _dedupe_roots([str(home), *_mounted_volume_roots()])


def ensure_default_roots() -> dict:
    existing_paths = {row["root_path"] for row in list_roots()}
    created = []
    for root in default_roots():
        if root in existing_paths:
            continue
        candidate = Path(root).expanduser()
        if candidate.exists() and candidate.is_dir():
            created.append(add_root(str(candidate), mode="normal", depth=2))
    return {"ok": True, "created": len(created), "roots": list_roots()}


def add_exclusion(path: str, *, reason: str = "user") -> dict:
    conn = _conn()
    excluded_path = norm_path(path)
    conn.execute(
        """
        INSERT INTO local_index_exclusions(path, display_path, reason, created_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET display_path=excluded.display_path, reason=excluded.reason
        """,
        (excluded_path, path, reason, now()),
    )
    conn.commit()
    log_event("info", "exclusion_added", "Exclusion added", path=redact_path(excluded_path), reason=reason)
    return {"ok": True, "path": excluded_path}


def remove_exclusion(path: str) -> dict:
    conn = _conn()
    excluded_path = norm_path(path)
    conn.execute("DELETE FROM local_index_exclusions WHERE path=?", (excluded_path,))
    conn.commit()
    log_event("info", "exclusion_removed", "Exclusion removed", path=redact_path(excluded_path))
    return {"ok": True, "path": excluded_path}


def list_exclusions() -> list[dict]:
    conn = _conn()
    rows = conn.execute("SELECT * FROM local_index_exclusions ORDER BY path").fetchall()
    return [dict(row) for row in rows]


def _set_state(key: str, value: str) -> None:
    conn = _conn()
    conn.execute(
        """
        INSERT INTO local_index_state(key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
        """,
        (key, value, now()),
    )
    conn.commit()


def _get_state(key: str, default: str = "") -> str:
    conn = _conn()
    row = conn.execute("SELECT value FROM local_index_state WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def pause() -> dict:
    _set_state("paused", "1")
    log_event("info", "index_paused", "Local memory indexing paused")
    return {"ok": True, "paused": True}


def resume() -> dict:
    _set_state("paused", "0")
    log_event("info", "index_resumed", "Local memory indexing resumed")
    return {"ok": True, "paused": False}


def _is_paused() -> bool:
    return _get_state("paused", "0") == "1"


def _is_excluded(path: str, exclusions: list[str]) -> bool:
    value = norm_path(path)
    return any(value == item or value.startswith(item + os.sep) for item in exclusions)


def _file_type(path: Path) -> str:
    if path.is_dir():
        return "folder"
    suffix = path.suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".heic", ".webp"}:
        return "photo"
    if suffix in {".py", ".js", ".ts", ".tsx", ".jsx", ".php", ".sql", ".css", ".html"}:
        return "code"
    if suffix in {".eml"}:
        return "email"
    if suffix in {".pdf", ".docx", ".pptx", ".xlsx", ".md", ".txt", ".csv", ".tsv"}:
        return "document"
    return "file"


def _permission_state(path: Path) -> str:
    try:
        path.stat()
    except PermissionError:
        return "denied"
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "limited"
    return "granted"


def _upsert_asset(conn, root_id: int, path: Path, seen_at: float, root_depth: int) -> tuple[str, bool, str]:
    raw_path = str(path)
    normalized = norm_path(raw_path)
    asset_id = stable_id("asset", normalized)
    perm = _permission_state(path)
    depth, privacy_class, depth_reason = classify_path(normalized)
    depth = min(depth, root_depth)
    try:
        st = path.stat()
    except Exception as exc:
        conn.execute(
            """
            INSERT INTO local_index_errors(asset_id, path, phase, error_code, user_message, technical_detail, retryable, created_at)
            VALUES (?, ?, 'quick_index', ?, ?, ?, 1, ?)
            """,
            (asset_id, normalized, type(exc).__name__, "Algunos archivos no se pudieron leer", str(exc), now()),
        )
        return asset_id, False, "error"
    fingerprint = quick_fingerprint(path, st)
    row = conn.execute("SELECT quick_fingerprint, status FROM local_assets WHERE asset_id=?", (asset_id,)).fetchone()
    changed = not row or row["quick_fingerprint"] != fingerprint or row["status"] == "deleted"
    parent = norm_path(path.parent)
    conn.execute(
        """
        INSERT INTO local_assets(
          asset_id, root_id, path, display_path, parent_path, volume_id, file_type, extension,
          size_bytes, created_at_fs, modified_at_fs, quick_fingerprint, depth, depth_reason,
          phase, status, privacy_class, permission_state, first_seen_at, last_seen_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'quick_index', 'active', ?, ?, ?, ?, ?)
        ON CONFLICT(asset_id) DO UPDATE SET
          root_id=excluded.root_id,
          path=excluded.path,
          display_path=excluded.display_path,
          parent_path=excluded.parent_path,
          volume_id=excluded.volume_id,
          file_type=excluded.file_type,
          extension=excluded.extension,
          size_bytes=excluded.size_bytes,
          created_at_fs=excluded.created_at_fs,
          modified_at_fs=excluded.modified_at_fs,
          quick_fingerprint=excluded.quick_fingerprint,
          depth=excluded.depth,
          depth_reason=excluded.depth_reason,
          status='active',
          privacy_class=excluded.privacy_class,
          permission_state=excluded.permission_state,
          last_seen_at=excluded.last_seen_at,
          updated_at=excluded.updated_at,
          deleted_at=NULL
        """,
        (
            asset_id,
            root_id,
            normalized,
            raw_path,
            parent,
            path.anchor or "/",
            _file_type(path),
            path.suffix.lower(),
            int(st.st_size),
            float(st.st_ctime),
            float(st.st_mtime),
            fingerprint,
            depth,
            depth_reason,
            privacy_class,
            perm,
            seen_at,
            seen_at,
            seen_at,
        ),
    )
    if changed:
        version_id = stable_id("ver", f"{asset_id}:{fingerprint}")
        conn.execute(
            """
            INSERT OR IGNORE INTO local_asset_versions(
              version_id, asset_id, quick_fingerprint, content_hash, size_bytes, modified_at_fs, created_at
            ) VALUES (?, ?, ?, '', ?, ?, ?)
            """,
            (version_id, asset_id, fingerprint, int(st.st_size), float(st.st_mtime), now()),
        )
        if should_extract(normalized, depth):
            enqueue_job(conn, asset_id, "light_extraction", priority=60)
        enqueue_job(conn, asset_id, "graph", priority=40)
    return asset_id, changed, "ok"


def enqueue_job(conn, asset_id: str, job_type: str, *, priority: int = 50) -> str:
    job_id = stable_id("job", f"{asset_id}:{job_type}")
    conn.execute(
        """
        INSERT INTO local_index_jobs(job_id, asset_id, job_type, status, priority, created_at, updated_at)
        VALUES (?, ?, ?, 'pending', ?, ?, ?)
        ON CONFLICT(job_id) DO UPDATE SET status='pending', priority=excluded.priority, updated_at=excluded.updated_at
        """,
        (job_id, asset_id, job_type, int(priority), now(), now()),
    )
    return job_id


def _iter_files(root: Path, exclusions: list[str], *, limit: int | None = None, start_after: str = ""):
    seen_dirs: set[tuple[int, int]] = set()
    count = 0
    stack = [root]
    start_after_norm = norm_path(start_after) if start_after else ""
    while stack:
        current = stack.pop()
        if _is_excluded(str(current), exclusions):
            continue
        try:
            st = current.stat()
        except Exception:
            continue
        key = (getattr(st, "st_dev", 0), getattr(st, "st_ino", 0))
        if key in seen_dirs:
            continue
        seen_dirs.add(key)
        try:
            entries = sorted(current.iterdir(), key=lambda item: str(item).lower())
        except Exception:
            continue
        dirs: list[Path] = []
        for entry in entries:
            if _is_excluded(str(entry), exclusions):
                continue
            if entry.is_symlink():
                continue
            if entry.is_dir():
                dirs.append(entry)
                continue
            if entry.is_file():
                normalized = norm_path(entry)
                if start_after_norm and normalized <= start_after_norm:
                    continue
                yield entry
                count += 1
                if limit and count >= limit:
                    return
        stack.extend(reversed(dirs))


def _checkpoint_for_root(conn, root_id: int) -> dict:
    row = conn.execute(
        """
        SELECT current_path, metadata_json
        FROM local_index_checkpoints
        WHERE root_id=? AND phase='quick_index'
        ORDER BY id DESC
        LIMIT 1
        """,
        (root_id,),
    ).fetchone()
    if not row:
        return {"current_path": "", "cycle_started_at": now()}
    metadata = json_loads(row["metadata_json"], {})
    return {
        "current_path": row["current_path"] or "",
        "cycle_started_at": float(metadata.get("cycle_started_at") or now()),
    }


def _save_checkpoint(conn, root_id: int, current_path: str, *, cycle_started_at: float, totals: dict) -> None:
    metadata = {"cycle_started_at": cycle_started_at}
    _clear_checkpoint(conn, root_id)
    conn.execute(
        """
        INSERT INTO local_index_checkpoints(
          root_id, phase, current_path, total_seen, total_changed, total_errors,
          eta_seconds, metadata_json, created_at, updated_at
        )
        VALUES (?, 'quick_index', ?, ?, ?, ?, NULL, ?, ?, ?)
        """,
        (
            root_id,
            current_path,
            int(totals.get("seen", 0) or 0),
            int(totals.get("changed", 0) or 0),
            int(totals.get("errors", 0) or 0),
            json_dumps(metadata),
            now(),
            now(),
        ),
    )


def _clear_checkpoint(conn, root_id: int) -> None:
    conn.execute("DELETE FROM local_index_checkpoints WHERE root_id=? AND phase='quick_index'", (root_id,))


def scan_once(*, limit: int | None = None) -> dict:
    conn = _conn()
    if _is_paused():
        log_event("info", "scan_skipped_paused", "Local memory scan skipped because indexing is paused")
        return {"ok": True, "paused": True, "roots": 0, "seen": 0, "changed": 0, "errors": 0, "partial": False}
    started = now()
    roots = list_roots()
    exclusions = [row["path"] for row in list_exclusions()]
    totals = {"roots": len(roots), "seen": 0, "changed": 0, "errors": 0, "partial": False}
    log_event("info", "scan_started", "Local memory scan started", roots=len(roots))
    for root in roots:
        root_path = Path(root["root_path"]).expanduser()
        root_id = int(root["id"])
        if not root_path.exists():
            conn.execute(
                "UPDATE local_index_roots SET status='offline', last_scan_at=?, updated_at=? WHERE id=?",
                (now(), now(), root_id),
            )
            log_event("warn", "root_offline", "Root is not available", path=redact_path(str(root_path)))
            continue
        conn.execute(
            "UPDATE local_index_roots SET status='scanning', last_scan_at=?, updated_at=? WHERE id=?",
            (now(), now(), root_id),
        )
        checkpoint = _checkpoint_for_root(conn, root_id) if limit else {"current_path": "", "cycle_started_at": started}
        cycle_started_at = float(checkpoint["cycle_started_at"])
        seen_for_root = 0
        last_seen_path = ""
        for file_path in _iter_files(root_path, exclusions, limit=limit, start_after=str(checkpoint["current_path"] or "")):
            asset_id, changed, state = _upsert_asset(conn, root_id, file_path, cycle_started_at, int(root["depth"] or 2))
            last_seen_path = norm_path(file_path)
            totals["seen"] += 1
            seen_for_root += 1
            if changed:
                totals["changed"] += 1
            if state != "ok":
                totals["errors"] += 1
        partial_root = bool(limit and seen_for_root >= limit)
        totals["partial"] = bool(totals["partial"] or partial_root)
        if partial_root:
            log_event(
                "info",
                "scan_partial",
                "Local memory scan checkpointed before deletion reconciliation",
                path=redact_path(str(root_path)),
            )
            if last_seen_path:
                _save_checkpoint(conn, root_id, last_seen_path, cycle_started_at=cycle_started_at, totals=totals)
        else:
            rows = conn.execute(
                "SELECT asset_id FROM local_assets WHERE root_id=? AND status='active' AND last_seen_at < ?",
                (root_id, cycle_started_at),
            ).fetchall()
            for row in rows:
                conn.execute(
                    "UPDATE local_assets SET status='deleted', deleted_at=?, updated_at=? WHERE asset_id=?",
                    (now(), now(), row["asset_id"]),
                )
            _clear_checkpoint(conn, root_id)
        conn.execute(
            "UPDATE local_index_roots SET status='active', last_scan_at=?, updated_at=? WHERE id=?",
            (now(), now(), root_id),
        )
    conn.commit()
    log_event("info", "scan_finished", "Local memory scan finished", **totals)
    return {"ok": True, **totals}


def _latest_version_id(conn, asset_id: str) -> str:
    row = conn.execute(
        "SELECT version_id FROM local_asset_versions WHERE asset_id=? ORDER BY created_at DESC LIMIT 1",
        (asset_id,),
    ).fetchone()
    return row["version_id"] if row else stable_id("ver", asset_id)


def _replace_chunks(conn, asset_id: str, version_id: str, text: str) -> None:
    conn.execute("DELETE FROM local_chunks WHERE asset_id=?", (asset_id,))
    conn.execute("DELETE FROM local_embeddings WHERE asset_id=?", (asset_id,))
    for index, chunk in enumerate(chunk_text(text)):
        chunk_id = stable_id("chunk", f"{version_id}:{index}:{chunk[:80]}")
        conn.execute(
            """
            INSERT INTO local_chunks(chunk_id, asset_id, version_id, chunk_index, text, token_count, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (chunk_id, asset_id, version_id, index, chunk, len(tokenize(chunk)), now()),
        )
        vector = embeddings.embed_text(chunk)
        conn.execute(
            """
            INSERT INTO local_embeddings(embedding_id, asset_id, chunk_id, model_id, model_revision, dimension, vector_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                stable_id("emb", f"{chunk_id}:{embeddings.MODEL_ID}:{embeddings.MODEL_REVISION}"),
                asset_id,
                chunk_id,
                embeddings.MODEL_ID,
                embeddings.MODEL_REVISION,
                embeddings.DIMENSION,
                json_dumps(vector),
                now(),
            ),
        )


def _replace_entities(conn, asset_id: str, version_id: str, values: list[str]) -> None:
    conn.execute("DELETE FROM local_entities WHERE asset_id=?", (asset_id,))
    for value in values:
        entity_id = stable_id("entity", value.lower())
        conn.execute(
            """
            INSERT OR IGNORE INTO local_entities(entity_id, asset_id, version_id, name, entity_type, confidence, evidence, created_at)
            VALUES (?, ?, ?, ?, 'entity', 0.55, '', ?)
            """,
            (entity_id, asset_id, version_id, value, now()),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO local_relations(relation_id, source_asset_id, target_ref, relation_type, confidence, evidence, active, created_at)
            VALUES (?, ?, ?, 'asset_mentions_entity', 0.55, ?, 1, ?)
            """,
            (stable_id("rel", f"{asset_id}:mentions:{entity_id}"), asset_id, entity_id, value, now()),
        )


def process_jobs(*, limit: int = 100) -> dict:
    conn = _conn()
    if _is_paused():
        log_event("info", "jobs_skipped_paused", "Local memory jobs skipped because indexing is paused")
        return {"ok": True, "paused": True, "processed": 0, "failed": 0}
    rows = conn.execute(
        """
        SELECT j.*, a.path, a.depth, a.status AS asset_status
        FROM local_index_jobs j
        JOIN local_assets a ON a.asset_id = j.asset_id
        WHERE j.status='pending'
        ORDER BY j.priority DESC, j.created_at ASC
        LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    processed = 0
    failed = 0
    for row in rows:
        job_id = row["job_id"]
        asset_id = row["asset_id"]
        job_type = row["job_type"]
        conn.execute(
            "UPDATE local_index_jobs SET status='running', claimed_by='local-process', lease_expires_at=?, updated_at=? WHERE job_id=?",
            (now() + 300, now(), job_id),
        )
        try:
            if row["asset_status"] != "active":
                raise FileNotFoundError(row["path"])
            if job_type == "light_extraction":
                text, metadata = extract_text(Path(row["path"]))
                version_id = _latest_version_id(conn, asset_id)
                summary = summarize(text)
                conn.execute(
                    "UPDATE local_asset_versions SET summary=?, metadata_json=? WHERE version_id=?",
                    (summary, json_dumps(metadata), version_id),
                )
                _replace_chunks(conn, asset_id, version_id, text)
                _replace_entities(conn, asset_id, version_id, entities(text))
                conn.execute("UPDATE local_assets SET phase='embeddings', updated_at=? WHERE asset_id=?", (now(), asset_id))
            elif job_type == "graph":
                conn.execute(
                    """
                    INSERT OR IGNORE INTO local_relations(relation_id, source_asset_id, target_ref, relation_type, confidence, evidence, active, created_at)
                    VALUES (?, ?, ?, 'file_in_folder', 1.0, 'path metadata', 1, ?)
                    """,
                    (stable_id("rel", f"{asset_id}:folder"), asset_id, str(Path(row["path"]).parent), now()),
                )
            conn.execute(
                "UPDATE local_index_jobs SET status='done', updated_at=?, last_error_code='' WHERE job_id=?",
                (now(), job_id),
            )
            processed += 1
        except Exception as exc:
            failed += 1
            conn.execute(
                """
                UPDATE local_index_jobs
                SET status='failed', attempt_count=attempt_count+1, next_attempt_at=?, last_error_code=?, updated_at=?
                WHERE job_id=?
                """,
                (now() + 3600, type(exc).__name__, now(), job_id),
            )
            conn.execute(
                """
                INSERT INTO local_index_errors(asset_id, path, phase, error_code, user_message, technical_detail, retryable, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (asset_id, row["path"], job_type, type(exc).__name__, "Algunos archivos no se pudieron leer", str(exc), now()),
            )
    conn.commit()
    if processed or failed:
        log_event("info", "jobs_processed", "Local memory jobs processed", processed=processed, failed=failed)
    return {"ok": True, "processed": processed, "failed": failed}


def run_once(*, root: str | None = None, limit: int | None = None, process_limit: int = 100) -> dict:
    if root:
        add_root(root)
    scan_result = scan_once(limit=limit)
    job_result = process_jobs(limit=process_limit)
    return {"ok": True, "scan": scan_result, "jobs": job_result}


def _problem_rows(conn) -> list[dict]:
    rows = conn.execute(
        """
        SELECT path, phase, error_code, user_message, technical_detail, retryable, created_at
        FROM local_index_errors
        ORDER BY id DESC
        LIMIT 20
        """
    ).fetchall()
    return [
        {
            "user_message": row["user_message"],
            "recommended_action": "NEXO lo volvera a intentar mas tarde" if row["retryable"] else "Revisa permisos o archivo",
            "technical_detail": row["technical_detail"],
            "support_code": row["error_code"],
            "severity": "warning",
            "retryable": bool(row["retryable"]),
            "path": redact_path(row["path"]),
            "phase": row["phase"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def _command_output(args: list[str], *, timeout: int = 2) -> tuple[int, str, str]:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        return 127, "", str(exc)
    except subprocess.TimeoutExpired as exc:
        return 124, exc.stdout or "", exc.stderr or "timeout"
    except Exception as exc:
        return 1, "", str(exc)
    return result.returncode, result.stdout or "", result.stderr or ""


def _process_running(pattern: str) -> bool:
    if system_label() == "windows":
        command = (
            "$pattern = '" + pattern.replace("'", "''") + "'; "
            "$match = Get-CimInstance Win32_Process | "
            "Where-Object { $_.CommandLine -like \"*$pattern*\" } | "
            "Select-Object -First 1 -ExpandProperty ProcessId; "
            "if ($match) { Write-Output $match }"
        )
        code, stdout, _ = _command_output(["powershell", "-NoProfile", "-Command", command], timeout=4)
        return code == 0 and bool(stdout.strip())

    code, stdout, _ = _command_output(["pgrep", "-f", pattern], timeout=2)
    if code == 0 and stdout.strip():
        return True
    code, stdout, _ = _command_output(["ps", "aux"], timeout=2)
    return code == 0 and pattern in stdout


def _macos_local_index_service_status() -> dict:
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{LOCAL_INDEX_SERVICE_LABEL}.plist"
    installed = plist_path.is_file()
    running = False
    active_process = False
    pid = ""

    code, stdout, _ = _command_output(["launchctl", "list"], timeout=2)
    if code == 0:
        for line in stdout.splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[-1] == LOCAL_INDEX_SERVICE_LABEL:
                installed = True
                pid = parts[0]
                running = True
                active_process = pid.isdigit() and int(pid) > 0
                break

    if not installed:
        code, _, _ = _command_output(["launchctl", "print", f"gui/{os.getuid()}/{LOCAL_INDEX_SERVICE_LABEL}"], timeout=2)
        installed = code == 0

    if not active_process:
        active_process = _process_running(LOCAL_INDEX_SCRIPT_NAME)
    running = running or active_process

    return {
        "installed": installed,
        "running": running,
        "active_process": active_process,
        "manager": "launchagent",
        "label": LOCAL_INDEX_SERVICE_LABEL,
        "pid": pid,
        "config_path": str(plist_path),
    }


def _windows_local_index_service_status() -> dict:
    command = (
        "$task = Get-ScheduledTask -TaskName 'NEXO Local Memory' -ErrorAction SilentlyContinue; "
        "if ($task) { Write-Output $task.State }"
    )
    code, stdout, _ = _command_output(["powershell", "-NoProfile", "-Command", command], timeout=4)
    task_state = stdout.strip()
    task_state_key = task_state.lower()
    installed = code == 0 and bool(task_state)
    active_process = task_state_key == "running"
    if not active_process:
        active_process = _process_running(LOCAL_INDEX_SCRIPT_NAME)
    running = task_state_key in {"ready", "running"} or active_process
    return {
        "installed": installed,
        "running": running,
        "active_process": active_process,
        "manager": "scheduled_task",
        "task_name": LOCAL_INDEX_WINDOWS_TASK,
        "task_state": task_state,
    }


def _linux_local_index_service_status() -> dict:
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_path = unit_dir / LOCAL_INDEX_LINUX_UNIT
    timer_path = unit_dir / "nexo-local-index.timer"
    installed = unit_path.is_file() or timer_path.is_file()

    code, stdout, _ = _command_output(["systemctl", "--user", "is-active", LOCAL_INDEX_LINUX_UNIT], timeout=2)
    unit_state = stdout.strip()
    running = code == 0 and unit_state == "active"
    active_process = _process_running(LOCAL_INDEX_SCRIPT_NAME)
    running = running or active_process

    return {
        "installed": installed,
        "running": running,
        "active_process": active_process,
        "manager": "systemd_user",
        "unit": LOCAL_INDEX_LINUX_UNIT,
        "unit_state": unit_state,
        "config_path": str(unit_path),
    }


def _local_index_service_status() -> dict:
    platform_value = system_label()
    if platform_value == "macos":
        service = _macos_local_index_service_status()
    elif platform_value == "windows":
        service = _windows_local_index_service_status()
    else:
        service = _linux_local_index_service_status()
    service.setdefault("installed", False)
    service.setdefault("running", False)
    service["platform"] = platform_value
    service["started_at"] = ""
    service["last_heartbeat_at"] = ""
    return service


def status() -> dict:
    conn = _conn()
    paused = _is_paused()
    assets = conn.execute(
        "SELECT COUNT(*) AS total, SUM(CASE WHEN status='active' THEN 1 ELSE 0 END) AS active FROM local_assets"
    ).fetchone()
    pending = conn.execute("SELECT COUNT(*) AS total FROM local_index_jobs WHERE status='pending'").fetchone()["total"]
    done = conn.execute("SELECT COUNT(*) AS total FROM local_index_jobs WHERE status='done'").fetchone()["total"]
    total_jobs = pending + done
    percent = 100 if total_jobs == 0 else int((done / max(total_jobs, 1)) * 100)
    roots = list_roots()
    volumes = []
    by_volume = conn.execute(
        "SELECT volume_id, COUNT(*) AS files FROM local_assets WHERE status='active' GROUP BY volume_id ORDER BY volume_id"
    ).fetchall()
    for row in by_volume:
        volumes.append({"id": row["volume_id"], "label": row["volume_id"] or "Disk", "files": row["files"], "status": "active"})
    service = _local_index_service_status()
    service["state"] = "paused" if paused else ("idle" if pending == 0 else "indexing")
    return {
        "ok": True,
        "service": service,
        "global": {
            "phase": "paused" if paused else ("idle" if pending == 0 else "light_extraction"),
            "percent": percent,
            "files_found": int(assets["total"] or 0),
            "files_processed": int(done or 0),
            "changes_pending": int(pending or 0),
            "elapsed_seconds": 0,
            "eta_seconds": None,
        },
        "volumes": volumes,
        "roots": roots,
        "exclusions": list_exclusions(),
        "problems": _problem_rows(conn),
        "permissions": [],
        "models": model_status()["models"],
        "support_log_available": True,
    }


def diagnostics_tail(limit: int = 100) -> dict:
    return {"ok": True, "logs": tail(limit)}


def model_status() -> dict:
    models = [{
        "profile": "local_context_embedding_fallback",
        "name": embeddings.MODEL_ID,
        "kind": "deterministic_embedding",
        "revision": embeddings.MODEL_REVISION,
        "dimension": embeddings.DIMENSION,
        "state": "available",
        "required": True,
    }]
    try:
        import local_models
        for spec in local_models.list_local_model_specs():
            verification = local_models.verify_local_model_dir(spec)
            models.append({
                "profile": spec.name,
                "name": spec.model_id,
                "kind": spec.kind,
                "revision": spec.revision,
                "dimension": spec.dimension,
                "state": "available" if verification["ok"] else "not_warmed",
                "required": spec.required,
                "path": verification["path"],
                "problems": verification["problems"],
            })
    except Exception as exc:
        models.append({
            "profile": "local_model_manifest",
            "name": "local_model_manifest",
            "kind": "manifest",
            "state": "error",
            "required": False,
            "problems": [str(exc)],
        })
    return {"ok": True, "models": models}


def warmup_models(*, local_files_only: bool = True) -> dict:
    results = []
    try:
        import local_models
        specs = local_models.list_local_model_specs()
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__, "message": str(exc), "results": results}
    for spec in specs:
        if spec.kind not in {"fastembed_embedding", "local_presence_llm"}:
            continue
        try:
            if spec.kind == "fastembed_embedding":
                path = local_models.ensure_local_model(spec.name, local_files_only=local_files_only)
                state = "available"
            else:
                verification = local_models.verify_local_model_dir(spec)
                path = Path(verification["path"])
                state = "available" if verification["ok"] else "not_warmed"
            results.append({"name": spec.name, "kind": spec.kind, "state": state, "path": str(path)})
        except Exception as exc:
            results.append({"name": spec.name, "kind": spec.kind, "state": "error", "error": type(exc).__name__, "message": str(exc)})
    return {"ok": all(item.get("state") != "error" for item in results), "results": results}


def _search_text_score(query: str, text: str) -> float:
    q = set(tokenize(query))
    if not q:
        return 0.0
    tokens = set(tokenize(text))
    return len(q & tokens) / max(len(q), 1)


def context_query(query: str, *, intent: str = "answer", limit: int = 12, evidence_required: bool = True, current_context: str = "") -> dict:
    conn = _conn()
    qvec = embeddings.embed_text(query)
    rows = conn.execute(
        """
        SELECT c.chunk_id, c.asset_id, c.text, a.path, a.file_type, v.summary, e.vector_json
        FROM local_chunks c
        JOIN local_assets a ON a.asset_id = c.asset_id
        LEFT JOIN local_asset_versions v ON v.version_id = c.version_id
        LEFT JOIN local_embeddings e ON e.chunk_id = c.chunk_id
        WHERE a.status='active'
        LIMIT 1000
        """
    ).fetchall()
    scored = []
    for row in rows:
        vector = json_loads(row["vector_json"], [])
        score = max(_search_text_score(query, row["text"]), embeddings.cosine(qvec, vector))
        if score > 0:
            scored.append((score, row))
    scored.sort(key=lambda item: item[0], reverse=True)
    assets = []
    chunks = []
    evidence_refs = []
    seen_assets = set()
    for score, row in scored[: int(limit)]:
        if row["asset_id"] not in seen_assets:
            assets.append({
                "asset_id": row["asset_id"],
                "path": row["path"],
                "display_path": redact_path(row["path"]),
                "file_type": row["file_type"],
                "score": round(float(score), 4),
                "summary": row["summary"] or "",
            })
            seen_assets.add(row["asset_id"])
        chunks.append({
            "chunk_id": row["chunk_id"],
            "asset_id": row["asset_id"],
            "text": row["text"][:1200],
            "score": round(float(score), 4),
        })
        evidence_refs.append(f"local_asset:{row['asset_id']}#chunk:{row['chunk_id']}")
    entity_rows = conn.execute(
        "SELECT DISTINCT name, entity_type, asset_id FROM local_entities WHERE lower(name) LIKE ? LIMIT ?",
        (f"%{query.lower()}%", int(limit)),
    ).fetchall()
    entities_payload = [dict(row) for row in entity_rows]
    warnings = []
    if evidence_required and not evidence_refs:
        warnings.append("No local evidence found for this query.")
    summary = ""
    if assets:
        summary = f"Found {len(assets)} local asset(s) related to '{query}'."
    conn.execute(
        """
        INSERT INTO local_context_queries(query_hash, intent, result_count, confidence, warnings_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            hashlib.sha256(query.encode("utf-8", errors="ignore")).hexdigest(),
            intent,
            len(assets),
            0.75 if evidence_refs else 0.0,
            json_dumps(warnings),
            now(),
        ),
    )
    conn.commit()
    return {
        "ok": True,
        "query": query,
        "intent": intent,
        "confidence": 0.75 if evidence_refs else 0.0,
        "summary": summary,
        "assets": assets,
        "entities": entities_payload,
        "relations": [],
        "chunks": chunks,
        "warnings": warnings,
        "evidence_refs": evidence_refs,
    }


def get_asset(asset_id: str) -> dict:
    conn = _conn()
    row = conn.execute("SELECT * FROM local_assets WHERE asset_id=?", (asset_id,)).fetchone()
    if not row:
        return {"ok": False, "error": "asset_not_found"}
    return {"ok": True, "asset": dict(row)}


def get_neighbors(asset_id: str, *, limit: int = 30) -> dict:
    conn = _conn()
    rows = conn.execute(
        """
        SELECT * FROM local_relations
        WHERE source_asset_id=? AND active=1
        ORDER BY confidence DESC
        LIMIT ?
        """,
        (asset_id, int(limit)),
    ).fetchall()
    return {"ok": True, "relations": [dict(row) for row in rows]}


def purge_asset(asset_id: str) -> dict:
    conn = _conn()
    for table in ("local_embeddings", "local_chunks", "local_entities"):
        conn.execute(f"DELETE FROM {table} WHERE asset_id=?", (asset_id,))
    conn.execute("DELETE FROM local_relations WHERE source_asset_id=?", (asset_id,))
    conn.execute("DELETE FROM local_index_errors WHERE asset_id=?", (asset_id,))
    conn.execute("DELETE FROM local_index_jobs WHERE asset_id=?", (asset_id,))
    conn.execute("DELETE FROM local_asset_versions WHERE asset_id=?", (asset_id,))
    conn.execute("DELETE FROM local_assets WHERE asset_id=?", (asset_id,))
    conn.commit()
    log_event("info", "asset_purged", "Asset purged", asset_id=asset_id)
    return {"ok": True, "asset_id": asset_id}


def clear_index() -> dict:
    conn = _conn()
    for table in (
        "local_embeddings",
        "local_chunks",
        "local_entities",
        "local_relations",
        "local_index_errors",
        "local_index_jobs",
        "local_asset_versions",
        "local_assets",
        "local_context_queries",
    ):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()
    log_event("warn", "index_cleared", "Local memory index cleared")
    return {"ok": True}


def render_service_config(platform_name: str | None = None) -> dict:
    platform_value = platform_name or system_label()
    core_dir = Path(__file__).resolve().parents[1]
    script_path = core_dir / "scripts" / "nexo-local-index.py"
    logs_path = Path.home() / ".nexo" / "logs" / "local-index.log"
    if platform_value == "macos":
        return {
            "ok": True,
            "platform": "macos",
            "kind": "launchagent",
            "label": "com.nexo.local-index",
            "filename": "com.nexo.local-index.plist",
            "script": str(script_path),
            "log_file": str(logs_path),
            "install": {
                "managed_by": "src/crons/manifest.json",
                "command": [sys.executable, str(core_dir / "crons" / "sync.py")],
            },
            "start": ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/com.nexo.local-index"],
            "status": ["launchctl", "print", f"gui/{os.getuid()}/com.nexo.local-index"],
            "stop": ["launchctl", "bootout", f"gui/{os.getuid()}", str(Path.home() / "Library" / "LaunchAgents" / "com.nexo.local-index.plist")],
        }
    if platform_value == "windows":
        task_name = "NEXO Local Memory"
        script = str(script_path)
        return {
            "ok": True,
            "platform": "windows",
            "kind": "scheduled_task",
            "task_name": task_name,
            "script": script,
            "log_file": str(logs_path),
            "interval_seconds": 60,
            "install": {
                "managed_by": "NEXO Desktop service bridge",
                "powershell": (
                    f"$action = New-ScheduledTaskAction -Execute '{sys.executable}' -Argument '\"{script}\"'; "
                    "$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) "
                    "-RepetitionInterval (New-TimeSpan -Minutes 1); "
                    f"Register-ScheduledTask -TaskName '{task_name}' -Action $action -Trigger $trigger "
                    "-Description 'NEXO Local Context Layer indexing cycle' -Force"
                ),
            },
            "start": ["schtasks", "/Run", "/TN", task_name],
            "status": ["schtasks", "/Query", "/TN", task_name],
            "stop": ["schtasks", "/End", "/TN", task_name],
            "uninstall": ["schtasks", "/Delete", "/TN", task_name, "/F"],
        }
    return {
        "ok": True,
        "platform": platform_value,
        "kind": "systemd_user",
        "unit": "nexo-local-index.service",
        "script": str(script_path),
        "log_file": str(logs_path),
        "install": {"managed_by": "src/crons/manifest.json", "command": [sys.executable, str(core_dir / "crons" / "sync.py")]},
    }
