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
from .extractors import chunk_text, contains_secret, entities, extract_text, summarize
from .logging import log_event, tail
from .privacy import classify_path, is_queryable_path, should_extract, should_skip_file, should_skip_tree
from .util import content_hash, json_dumps, json_loads, norm_path, now, quick_fingerprint, redact_path, stable_id, system_label, tokenize

LOCAL_INDEX_SERVICE_LABEL = "com.nexo.local-index"
LOCAL_INDEX_SCRIPT_NAME = "nexo-local-index.py"
LOCAL_INDEX_WINDOWS_TASK = "NEXO Local Memory"
LOCAL_INDEX_LINUX_UNIT = "nexo-local-index.service"
DEFAULT_LIVE_ASSET_LIMIT = int(os.environ.get("NEXO_LOCAL_INDEX_LIVE_ASSET_LIMIT", "2000") or "2000")
DEFAULT_LIVE_DIR_LIMIT = int(os.environ.get("NEXO_LOCAL_INDEX_LIVE_DIR_LIMIT", "300") or "300")
DEFAULT_LIVE_FILE_LIMIT = int(os.environ.get("NEXO_LOCAL_INDEX_LIVE_FILE_LIMIT", "1000") or "1000")


def ensure_ready() -> None:
    init_db()
    run_migrations()


def _conn():
    ensure_ready()
    return get_db()


def add_root(path: str, *, mode: str = "normal", depth: int | None = None) -> dict:
    conn = _conn()
    root_path = norm_path(path)
    if should_skip_tree(root_path):
        log_event("warn", "root_rejected_private", "Root rejected by local memory privacy rules", path=redact_path(root_path))
        return {"ok": False, "error": "root_blocked_by_privacy", "root_path": root_path}
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
    cleanup = _purge_removed_root_payloads(conn, root_paths=[root_path])
    conn.commit()
    log_event("info", "root_removed", "Root removed", path=redact_path(root_path), cleanup=cleanup)
    return {"ok": True, "root_path": root_path, "cleanup": cleanup}


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
            if _should_skip_mounted_root(candidate):
                continue
            resolved = candidate.resolve()
            if resolved == root_resolved:
                continue
            roots.append(str(candidate))
        except Exception:
            continue
    return roots


def _local_email_roots() -> list[str]:
    home = Path.home()
    roots: list[Path] = [home / ".nexo" / "runtime" / "nexo-email"]
    mac_roots = [
        home / "Library" / "Mail",
        home / "Library" / "Group Containers" / "UBF8T346G9.Office" / "Outlook" / "Outlook 15 Profiles",
    ]
    local_app_data = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
    roaming_app_data = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
    windows_roots = [
        home / "Documents" / "Outlook Files",
        local_app_data / "Microsoft" / "Outlook",
        roaming_app_data / "Microsoft" / "Outlook",
        local_app_data / "Packages" / "microsoft.windowscommunicationsapps_8wekyb3d8bbwe" / "LocalState",
    ]
    linux_roots = [home / ".thunderbird", home / ".mozilla-thunderbird"]

    if sys.platform == "darwin":
        roots.extend(mac_roots)
    elif sys.platform.startswith("win"):
        roots.extend(windows_roots)
    else:
        roots.extend(linux_roots)

    # CI and migrated profiles can expose platform-specific mail stores while
    # running on another OS. Include only the stores that actually exist.
    for optional_root in [*mac_roots, *windows_roots, *linux_roots]:
        if optional_root.exists() and optional_root not in roots:
            roots.append(optional_root)
    return [str(root) for root in roots]


def default_roots() -> list[str]:
    home = Path.home()
    configured = os.environ.get("NEXO_LOCAL_INDEX_DEFAULT_ROOTS", "").strip()
    if configured:
        return _dedupe_roots([item for item in configured.split(os.pathsep) if item.strip()])
    return _dedupe_roots([str(home), *_local_email_roots(), *_mounted_volume_roots()])


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


def _should_skip_mounted_root(candidate: Path) -> bool:
    name = candidate.name.strip().lower()
    if name in {"nexo desktop", "nexo desktop beta"} or name.startswith("nexo desktop "):
        return True
    try:
        app_bundles = [child.name.lower() for child in candidate.iterdir() if child.suffix.lower() == ".app"]
    except Exception:
        app_bundles = []
    if any(name.startswith("nexo desktop") for name in app_bundles):
        installer_markers = (
            candidate / ".background",
            candidate / "Applications",
            candidate / ".DS_Store",
        )
        if any(marker.exists() for marker in installer_markers):
            return True
    return False


def _removed_root_filters(conn, *, root_paths: list[str] | None = None) -> tuple[list[int], list[str]]:
    if root_paths:
        placeholders = ",".join("?" for _ in root_paths)
        rows = conn.execute(
            f"SELECT id, root_path FROM local_index_roots WHERE root_path IN ({placeholders}) AND status='removed'",
            tuple(root_paths),
        ).fetchall()
    else:
        rows = conn.execute("SELECT id, root_path FROM local_index_roots WHERE status='removed'").fetchall()
    return [int(row["id"]) for row in rows], [str(row["root_path"]) for row in rows]


def _removed_root_payload_counts(conn, *, root_paths: list[str] | None = None) -> dict:
    root_ids, removed_paths = _removed_root_filters(conn, root_paths=root_paths)
    if not root_ids and not removed_paths:
        return {"assets": 0, "jobs": 0, "errors": 0, "dirs": 0, "checkpoints": 0}
    asset_filter, params = _removed_root_asset_filter(root_ids, removed_paths)
    if not asset_filter:
        return {"assets": 0, "jobs": 0, "errors": 0, "dirs": 0, "checkpoints": 0}
    asset_subquery = f"SELECT asset_id FROM local_assets WHERE {asset_filter}"
    assets = int(conn.execute(f"SELECT COUNT(*) AS total FROM local_assets WHERE {asset_filter}", tuple(params)).fetchone()["total"] or 0)
    jobs = int(conn.execute(f"SELECT COUNT(*) AS total FROM local_index_jobs WHERE asset_id IN ({asset_subquery})", tuple(params)).fetchone()["total"] or 0)
    errors = int(conn.execute(f"SELECT COUNT(*) AS total FROM local_index_errors WHERE asset_id IN ({asset_subquery})", tuple(params)).fetchone()["total"] or 0)
    for path in removed_paths:
        errors += int(conn.execute("SELECT COUNT(*) AS total FROM local_index_errors WHERE asset_id='' AND (path = ? OR path LIKE ?)", (path, f"{path}/%")).fetchone()["total"] or 0)
    dirs = 0
    checkpoints = 0
    if root_ids:
        root_placeholders = ",".join("?" for _ in root_ids)
        dirs = int(conn.execute(f"SELECT COUNT(*) AS total FROM local_index_dirs WHERE root_id IN ({root_placeholders})", tuple(root_ids)).fetchone()["total"] or 0)
        checkpoints = int(conn.execute(f"SELECT COUNT(*) AS total FROM local_index_checkpoints WHERE root_id IN ({root_placeholders})", tuple(root_ids)).fetchone()["total"] or 0)
    return {"assets": assets, "jobs": jobs, "errors": errors, "dirs": dirs, "checkpoints": checkpoints}


def _removed_root_asset_filter(root_ids: list[int], removed_paths: list[str]) -> tuple[str, list[Any]]:
    filters: list[str] = []
    params: list[Any] = []
    if root_ids:
        root_placeholders = ",".join("?" for _ in root_ids)
        filters.append(f"root_id IN ({root_placeholders})")
        params.extend(root_ids)
    for path in removed_paths:
        filters.append("(path = ? OR path LIKE ?)")
        params.extend([path, f"{path}/%"])
    return " OR ".join(filters), params


def _purge_removed_root_payloads(conn, *, root_paths: list[str] | None = None) -> dict:
    root_ids, removed_paths = _removed_root_filters(conn, root_paths=root_paths)
    if not root_ids and not removed_paths:
        return {"assets": 0, "jobs": 0, "errors": 0, "dirs": 0, "checkpoints": 0}

    asset_filter, params = _removed_root_asset_filter(root_ids, removed_paths)
    if not asset_filter:
        return {"assets": 0, "jobs": 0, "errors": 0, "dirs": 0, "checkpoints": 0}
    asset_subquery = f"SELECT asset_id FROM local_assets WHERE {asset_filter}"
    counts = _removed_root_payload_counts(conn, root_paths=root_paths)

    for table in ("local_embeddings", "local_chunks", "local_entities", "local_asset_versions"):
        conn.execute(f"DELETE FROM {table} WHERE asset_id IN ({asset_subquery})", tuple(params))
    conn.execute(f"DELETE FROM local_relations WHERE source_asset_id IN ({asset_subquery})", tuple(params))
    conn.execute(f"DELETE FROM local_relations WHERE target_asset_id IN ({asset_subquery})", tuple(params))
    conn.execute(f"DELETE FROM local_relations WHERE target_ref IN ({asset_subquery})", tuple(params))
    conn.execute(f"DELETE FROM local_index_jobs WHERE asset_id IN ({asset_subquery})", tuple(params))
    conn.execute(f"DELETE FROM local_index_errors WHERE asset_id IN ({asset_subquery})", tuple(params))

    for path in removed_paths:
        conn.execute("DELETE FROM local_index_errors WHERE path = ? OR path LIKE ?", (path, f"{path}/%"))

    if root_ids:
        root_placeholders = ",".join("?" for _ in root_ids)
        conn.execute(f"DELETE FROM local_index_dirs WHERE root_id IN ({root_placeholders})", tuple(root_ids))
        conn.execute(f"DELETE FROM local_index_checkpoints WHERE root_id IN ({root_placeholders})", tuple(root_ids))
    conn.execute(f"DELETE FROM local_assets WHERE {asset_filter}", tuple(params))
    return counts


def _purge_asset_ids(conn, asset_ids: list[str]) -> dict:
    unique_ids = [asset_id for asset_id in dict.fromkeys(asset_ids) if asset_id]
    counts = {"assets": len(unique_ids), "jobs": 0, "errors": 0, "chunks": 0, "embeddings": 0, "entities": 0, "relations": 0, "versions": 0}
    if not unique_ids:
        return counts
    for start in range(0, len(unique_ids), 500):
        batch = unique_ids[start:start + 500]
        placeholders = ",".join("?" for _ in batch)
        for key, table in (
            ("embeddings", "local_embeddings"),
            ("chunks", "local_chunks"),
            ("entities", "local_entities"),
            ("versions", "local_asset_versions"),
            ("jobs", "local_index_jobs"),
            ("errors", "local_index_errors"),
        ):
            counts[key] += int(conn.execute(f"DELETE FROM {table} WHERE asset_id IN ({placeholders})", tuple(batch)).rowcount or 0)
        counts["relations"] += int(conn.execute(f"DELETE FROM local_relations WHERE source_asset_id IN ({placeholders})", tuple(batch)).rowcount or 0)
        counts["relations"] += int(conn.execute(f"DELETE FROM local_relations WHERE target_asset_id IN ({placeholders})", tuple(batch)).rowcount or 0)
        counts["relations"] += int(conn.execute(f"DELETE FROM local_relations WHERE target_ref IN ({placeholders})", tuple(batch)).rowcount or 0)
        conn.execute(f"DELETE FROM local_assets WHERE asset_id IN ({placeholders})", tuple(batch))
    return counts


def _privacy_unsafe_asset_ids(conn) -> list[str]:
    rows = conn.execute("SELECT asset_id, path, privacy_class FROM local_assets").fetchall()
    unsafe: list[str] = []
    for row in rows:
        privacy_class = str(row["privacy_class"] or "")
        if should_skip_file(str(row["path"] or "")) or privacy_class in {"private_profile_blocked", "system_blocked", "sensitive_inventory_only"}:
            unsafe.append(str(row["asset_id"]))
    return unsafe


def _privacy_unsafe_dir_ids(conn) -> list[str]:
    rows = conn.execute("SELECT dir_id, path FROM local_index_dirs").fetchall()
    return [str(row["dir_id"]) for row in rows if should_skip_tree(str(row["path"] or ""))]


def _content_secret_asset_ids(conn) -> list[str]:
    rows = conn.execute(
        """
        SELECT c.asset_id, c.text
        FROM local_chunks c
        JOIN local_assets a ON a.asset_id=c.asset_id
        WHERE a.status='active'
          AND COALESCE(a.privacy_class, 'normal')='normal'
        ORDER BY c.asset_id, c.chunk_index
        """
    ).fetchall()
    secret_ids: set[str] = set()
    for row in rows:
        asset_id = str(row["asset_id"])
        if asset_id in secret_ids:
            continue
        if contains_secret(str(row["text"] or "")):
            secret_ids.add(asset_id)
    return sorted(secret_ids)


def _mark_content_secret_assets(conn, asset_ids: list[str]) -> int:
    unique_ids = [asset_id for asset_id in dict.fromkeys(asset_ids) if asset_id]
    if not unique_ids:
        return 0
    for start in range(0, len(unique_ids), 500):
        batch = unique_ids[start:start + 500]
        placeholders = ",".join("?" for _ in batch)
        for table in ("local_embeddings", "local_chunks", "local_entities"):
            conn.execute(f"DELETE FROM {table} WHERE asset_id IN ({placeholders})", tuple(batch))
        conn.execute(f"DELETE FROM local_relations WHERE source_asset_id IN ({placeholders})", tuple(batch))
        conn.execute(f"DELETE FROM local_relations WHERE target_asset_id IN ({placeholders})", tuple(batch))
        conn.execute(f"DELETE FROM local_relations WHERE target_ref IN ({placeholders})", tuple(batch))
        conn.execute(
            f"""
            UPDATE local_index_jobs
            SET status='done', last_error_code='content_secret_blocked', updated_at=?
            WHERE asset_id IN ({placeholders})
            """,
            (now(), *batch),
        )
        conn.execute(
            f"""
            UPDATE local_asset_versions
            SET summary='', metadata_json=?
            WHERE asset_id IN ({placeholders})
            """,
            (json_dumps({"content_blocked": "secret_pattern"}), *batch),
        )
        conn.execute(
            f"""
            UPDATE local_assets
            SET privacy_class='content_secret_inventory_only',
                depth=1,
                depth_reason='content_secret',
                phase='privacy_blocked',
                updated_at=?
            WHERE asset_id IN ({placeholders})
            """,
            (now(), *batch),
        )
    return len(unique_ids)


def local_index_privacy_hygiene(*, fix: bool = False) -> dict:
    conn = _conn()
    asset_ids = _privacy_unsafe_asset_ids(conn)
    dir_ids = _privacy_unsafe_dir_ids(conn)
    content_secret_ids = _content_secret_asset_ids(conn)
    residue = {"assets": len(asset_ids), "dirs": len(dir_ids), "content_secret_assets": len(content_secret_ids)}
    cleanup = {"assets": 0, "jobs": 0, "errors": 0, "chunks": 0, "embeddings": 0, "entities": 0, "relations": 0, "versions": 0, "dirs": 0, "content_secret_assets": 0}
    if fix:
        cleanup.update(_purge_asset_ids(conn, asset_ids))
        if dir_ids:
            for start in range(0, len(dir_ids), 500):
                batch = dir_ids[start:start + 500]
                placeholders = ",".join("?" for _ in batch)
                cleanup["dirs"] += int(conn.execute(f"DELETE FROM local_index_dirs WHERE dir_id IN ({placeholders})", tuple(batch)).rowcount or 0)
        cleanup["content_secret_assets"] = _mark_content_secret_assets(conn, content_secret_ids)
        conn.commit()
        if asset_ids or dir_ids or content_secret_ids:
            log_event("warn", "privacy_hygiene_repaired", "Local memory privacy hygiene repaired", cleanup=cleanup)
    return {"ok": True, "fix": fix, "residue": residue, "cleanup": cleanup}


def local_index_hygiene(*, fix: bool = False) -> dict:
    conn = _conn()
    removed_paths: list[str] = []
    for row in conn.execute("SELECT id, root_path FROM local_index_roots").fetchall():
        path = str(row["root_path"] or "")
        if _should_skip_mounted_root(Path(path)) or should_skip_tree(path):
            removed_paths.append(path)
            if fix:
                conn.execute("UPDATE local_index_roots SET status='removed', updated_at=? WHERE id=?", (now(), row["id"]))
    before = _removed_root_payload_counts(conn)
    cleanup = {"assets": 0, "jobs": 0, "errors": 0, "dirs": 0, "checkpoints": 0}
    if fix:
        cleanup = _purge_removed_root_payloads(conn)
    conn.commit()
    privacy = local_index_privacy_hygiene(fix=fix)
    if fix and (removed_paths or any(int(cleanup.get(key, 0) or 0) for key in ("assets", "jobs", "errors", "dirs", "checkpoints"))):
        log_event("info", "index_hygiene_repaired", "Local memory index hygiene repaired", roots=[redact_path(path) for path in removed_paths], cleanup=cleanup)
    return {"ok": True, "fix": fix, "removed_roots": removed_paths, "residue": before, "cleanup": cleanup, "privacy": privacy}


def repair_index_hygiene() -> dict:
    return local_index_hygiene(fix=True)


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


def _path_prefix(path: str) -> str:
    normalized = norm_path(path)
    return normalized + os.sep if normalized else os.sep


def _file_type(path: Path) -> str:
    if path.is_dir():
        return "folder"
    suffix = path.suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".heic", ".webp"}:
        return "photo"
    if suffix in {".py", ".js", ".ts", ".tsx", ".jsx", ".php", ".sql", ".css", ".html"}:
        return "code"
    if suffix in {".eml", ".emlx", ".msg", ".pst", ".ost"}:
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


def _dir_fingerprint(path: Path, stat_result: os.stat_result | None = None) -> str:
    st = stat_result or path.stat()
    ctime_ns = getattr(st, "st_ctime_ns", int(float(st.st_ctime) * 1_000_000_000))
    return f"{int(st.st_mtime_ns)}:{int(ctime_ns)}"


def _upsert_dir(
    conn,
    root_id: int,
    path: Path,
    seen_at: float,
    stat_result: os.stat_result | None = None,
) -> tuple[bool, str]:
    raw_path = str(path)
    normalized = norm_path(raw_path)
    dir_id = stable_id("dir", normalized)
    parent = norm_path(path.parent)
    try:
        fingerprint = _dir_fingerprint(path, stat_result)
    except Exception:
        return False, "error"
    row = conn.execute(
        "SELECT quick_fingerprint, status FROM local_index_dirs WHERE dir_id=?",
        (dir_id,),
    ).fetchone()
    changed = not row or row["quick_fingerprint"] != fingerprint or row["status"] == "deleted"
    conn.execute(
        """
        INSERT INTO local_index_dirs(
          dir_id, root_id, path, display_path, parent_path, quick_fingerprint,
          status, first_seen_at, last_seen_at, updated_at, deleted_at
        )
        VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, NULL)
        ON CONFLICT(dir_id) DO UPDATE SET
          root_id=excluded.root_id,
          path=excluded.path,
          display_path=excluded.display_path,
          parent_path=excluded.parent_path,
          quick_fingerprint=excluded.quick_fingerprint,
          status='active',
          last_seen_at=excluded.last_seen_at,
          updated_at=excluded.updated_at,
          deleted_at=NULL
        """,
        (
            dir_id,
            root_id,
            normalized,
            raw_path,
            parent,
            fingerprint,
            seen_at,
            seen_at,
            seen_at,
        ),
    )
    return changed, fingerprint


def _upsert_asset(conn, root_id: int, path: Path, seen_at: float, root_depth: int) -> tuple[str, bool, str]:
    raw_path = str(path)
    normalized = norm_path(raw_path)
    asset_id = stable_id("asset", normalized)
    if should_skip_file(normalized):
        return asset_id, False, "skipped"
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


def _mark_asset_deleted(conn, asset_id: str, deleted_at: float | None = None) -> None:
    deleted_at = deleted_at or now()
    conn.execute(
        "UPDATE local_assets SET status='deleted', deleted_at=?, updated_at=? WHERE asset_id=? AND status!='deleted'",
        (deleted_at, deleted_at, asset_id),
    )
    conn.execute(
        """
        UPDATE local_index_jobs
        SET status='done', last_error_code='asset_deleted', updated_at=?
        WHERE asset_id=? AND status IN ('pending', 'running', 'failed')
        """,
        (deleted_at, asset_id),
    )


def _mark_dir_subtree_deleted(conn, dir_path: str, deleted_at: float | None = None) -> int:
    deleted_at = deleted_at or now()
    normalized = norm_path(dir_path)
    prefix = _path_prefix(normalized)
    conn.execute(
        """
        UPDATE local_index_dirs
        SET status='deleted', deleted_at=?, updated_at=?
        WHERE status='active' AND (path=? OR path LIKE ?)
        """,
        (deleted_at, deleted_at, normalized, prefix + "%"),
    )
    rows = conn.execute(
        "SELECT asset_id FROM local_assets WHERE status='active' AND (path=? OR path LIKE ?)",
        (normalized, prefix + "%"),
    ).fetchall()
    for row in rows:
        _mark_asset_deleted(conn, row["asset_id"], deleted_at)
    return len(rows)


def _purge_dir_subtree(conn, dir_path: str) -> int:
    normalized = norm_path(dir_path)
    prefix = _path_prefix(normalized)
    rows = conn.execute(
        "SELECT asset_id FROM local_assets WHERE path=? OR path LIKE ?",
        (normalized, prefix + "%"),
    ).fetchall()
    asset_ids = [str(row["asset_id"]) for row in rows]
    _purge_asset_ids(conn, asset_ids)
    conn.execute("DELETE FROM local_index_dirs WHERE path=? OR path LIKE ?", (normalized, prefix + "%"))
    conn.execute("DELETE FROM local_index_errors WHERE path=? OR path LIKE ?", (normalized, prefix + "%"))
    return len(asset_ids)


def _record_index_error(
    conn,
    *,
    asset_id: str = "",
    path: str = "",
    phase: str,
    error_code: str,
    user_message: str,
    technical_detail: str,
    retryable: bool = True,
) -> None:
    conn.execute(
        """
        INSERT INTO local_index_errors(asset_id, path, phase, error_code, user_message, technical_detail, retryable, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (asset_id, path, phase, error_code, user_message, technical_detail, 1 if retryable else 0, now()),
    )


def _record_scan_error(conn, stats: dict | None, path: str, phase: str, exc: Exception) -> None:
    if stats is not None:
        stats["errors"] = int(stats.get("errors", 0) or 0) + 1
        logged = int(stats.get("_errors_logged", 0) or 0)
        if logged >= 20:
            return
        stats["_errors_logged"] = logged + 1
    _record_index_error(
        conn,
        path=path,
        phase=phase,
        error_code=type(exc).__name__,
        user_message="Algunas carpetas o archivos no se pudieron leer",
        technical_detail=str(exc),
        retryable=True,
    )


def _public_stats(stats: dict) -> dict:
    return {key: value for key, value in stats.items() if not str(key).startswith("_")}


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


def _iter_files(
    conn,
    root_id: int,
    root: Path,
    exclusions: list[str],
    *,
    limit: int | None = None,
    start_after: str = "",
    seen_at: float | None = None,
    stats: dict | None = None,
):
    seen_at = seen_at or now()
    seen_dirs: set[tuple[int, int]] = set()
    count = 0
    stack = [root]
    start_after_norm = norm_path(start_after) if start_after else ""
    while stack:
        current = stack.pop()
        if _is_excluded(str(current), exclusions):
            continue
        if current != root and should_skip_tree(str(current)):
            continue
        try:
            st = current.stat()
        except Exception as exc:
            _record_scan_error(conn, stats, str(current), "quick_index", exc)
            continue
        key = (getattr(st, "st_dev", 0), getattr(st, "st_ino", 0))
        if key in seen_dirs:
            continue
        seen_dirs.add(key)
        _upsert_dir(conn, root_id, current, seen_at, st)
        try:
            entries = sorted(current.iterdir(), key=lambda item: str(item).lower())
        except Exception as exc:
            _record_scan_error(conn, stats, str(current), "quick_index", exc)
            continue
        dirs: list[Path] = []
        for entry in entries:
            if _is_excluded(str(entry), exclusions):
                continue
            if entry.is_symlink():
                continue
            if entry.is_dir():
                if should_skip_tree(str(entry)):
                    continue
                dirs.append(entry)
                continue
            if entry.is_file():
                normalized = norm_path(entry)
                if should_skip_file(normalized):
                    continue
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


def _reconcile_known_assets(conn, exclusions: list[str], *, limit: int) -> dict:
    stats = {"checked": 0, "modified": 0, "deleted": 0, "excluded": 0, "offline": 0, "errors": 0}
    if limit <= 0:
        return stats
    rows = conn.execute(
        """
        SELECT a.asset_id, a.path, a.root_id, a.quick_fingerprint, a.depth, r.root_path
        FROM local_assets a
        LEFT JOIN local_index_roots r ON r.id = a.root_id
        WHERE a.status='active'
        ORDER BY a.updated_at ASC
        LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    seen_at = now()
    for row in rows:
        stats["checked"] += 1
        path = str(row["path"])
        root_path = Path(row["root_path"]).expanduser() if row["root_path"] else None
        if _is_excluded(path, exclusions):
            _purge_asset_ids(conn, [row["asset_id"]])
            stats["excluded"] += 1
            continue
        if should_skip_file(path):
            _purge_asset_ids(conn, [row["asset_id"]])
            stats["excluded"] += 1
            continue
        if root_path is not None and not root_path.exists():
            stats["offline"] += 1
            continue
        file_path = Path(path)
        try:
            if not file_path.exists() or not file_path.is_file():
                _mark_asset_deleted(conn, row["asset_id"], seen_at)
                stats["deleted"] += 1
                continue
            st = file_path.stat()
            fingerprint = quick_fingerprint(file_path, st)
        except Exception as exc:
            _record_scan_error(conn, stats, path, "live_reconcile", exc)
            continue
        if fingerprint != row["quick_fingerprint"]:
            _, changed, state = _upsert_asset(conn, int(row["root_id"] or 0), file_path, seen_at, int(row["depth"] or 2))
            if changed:
                stats["modified"] += 1
            if state != "ok":
                stats["errors"] += 1
        else:
            conn.execute("UPDATE local_assets SET updated_at=? WHERE asset_id=?", (seen_at, row["asset_id"]))
    return stats


def _prune_missing_children(
    conn,
    directory: Path,
    seen_files: set[str],
    seen_dirs: set[str],
    seen_at: float,
) -> tuple[int, int]:
    parent = norm_path(directory)
    deleted_files = 0
    deleted_dirs = 0
    file_rows = conn.execute(
        "SELECT asset_id, path FROM local_assets WHERE parent_path=? AND status='active'",
        (parent,),
    ).fetchall()
    for row in file_rows:
        if row["path"] not in seen_files:
            _mark_asset_deleted(conn, row["asset_id"], seen_at)
            deleted_files += 1
    dir_rows = conn.execute(
        "SELECT path FROM local_index_dirs WHERE parent_path=? AND status='active'",
        (parent,),
    ).fetchall()
    for row in dir_rows:
        if row["path"] not in seen_dirs:
            deleted_files += _mark_dir_subtree_deleted(conn, row["path"], seen_at)
            deleted_dirs += 1
    return deleted_files, deleted_dirs


def _scan_known_directory(
    conn,
    root_id: int,
    directory: Path,
    root_depth: int,
    exclusions: list[str],
    stats: dict,
    *,
    file_limit: int,
    dir_limit: int,
) -> None:
    stack = [directory]
    seen_at = now()
    scanned_dirs = 0
    while stack and stats["files_scanned"] < file_limit and scanned_dirs < dir_limit:
        current = stack.pop()
        if _is_excluded(str(current), exclusions):
            _mark_dir_subtree_deleted(conn, str(current), seen_at)
            stats["excluded_dirs"] += 1
            continue
        if current != directory and should_skip_tree(str(current)):
            continue
        try:
            st = current.stat()
            if not current.is_dir():
                continue
            entries = sorted(current.iterdir(), key=lambda item: str(item).lower())
        except Exception as exc:
            _record_scan_error(conn, stats, str(current), "live_reconcile", exc)
            continue
        scanned_dirs += 1
        stats["dirs_scanned"] += 1
        _upsert_dir(conn, root_id, current, seen_at, st)
        seen_files: set[str] = set()
        seen_dirs: set[str] = set()
        for entry in entries:
            if _is_excluded(str(entry), exclusions):
                continue
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir():
                    if should_skip_tree(str(entry)):
                        continue
                    changed, _ = _upsert_dir(conn, root_id, entry, seen_at)
                    seen_dirs.add(norm_path(entry))
                    if changed and scanned_dirs + len(stack) < dir_limit:
                        stack.append(entry)
                    continue
                if entry.is_file():
                    if should_skip_file(str(entry)):
                        continue
                    seen_files.add(norm_path(entry))
                    if stats["files_scanned"] >= file_limit:
                        continue
                    _, changed, state = _upsert_asset(conn, root_id, entry, seen_at, root_depth)
                    stats["files_scanned"] += 1
                    if changed:
                        stats["files_changed"] += 1
                    if state not in {"ok", "skipped"}:
                        stats["errors"] += 1
            except Exception as exc:
                _record_scan_error(conn, stats, str(entry), "live_reconcile", exc)
        deleted_files, deleted_dirs = _prune_missing_children(conn, current, seen_files, seen_dirs, seen_at)
        stats["files_deleted"] += deleted_files
        stats["dirs_deleted"] += deleted_dirs


def _reconcile_known_dirs(conn, exclusions: list[str], *, dir_limit: int, file_limit: int) -> dict:
    stats = {
        "checked": 0,
        "changed": 0,
        "dirs_scanned": 0,
        "files_scanned": 0,
        "files_changed": 0,
        "files_deleted": 0,
        "dirs_deleted": 0,
        "excluded_dirs": 0,
        "offline": 0,
        "errors": 0,
    }
    if dir_limit <= 0 or file_limit <= 0:
        return stats
    rows = conn.execute(
        """
        SELECT d.dir_id, d.path, d.quick_fingerprint, d.root_id, r.root_path, r.depth
        FROM local_index_dirs d
        LEFT JOIN local_index_roots r ON r.id = d.root_id
        WHERE d.status='active'
        ORDER BY d.updated_at ASC
        LIMIT ?
        """,
        (int(dir_limit),),
    ).fetchall()
    seen_at = now()
    for row in rows:
        stats["checked"] += 1
        dir_path = Path(row["path"])
        root_path = Path(row["root_path"]).expanduser() if row["root_path"] else None
        if _is_excluded(str(dir_path), exclusions):
            stats["files_deleted"] += _mark_dir_subtree_deleted(conn, str(dir_path), seen_at)
            stats["excluded_dirs"] += 1
            continue
        if should_skip_tree(str(dir_path)):
            stats["files_deleted"] += _purge_dir_subtree(conn, str(dir_path))
            stats["excluded_dirs"] += 1
            continue
        if root_path is not None and not root_path.exists():
            stats["offline"] += 1
            continue
        try:
            if not dir_path.exists() or not dir_path.is_dir():
                stats["files_deleted"] += _mark_dir_subtree_deleted(conn, str(dir_path), seen_at)
                stats["dirs_deleted"] += 1
                continue
            st = dir_path.stat()
            fingerprint = _dir_fingerprint(dir_path, st)
        except Exception as exc:
            _record_scan_error(conn, stats, str(dir_path), "live_reconcile", exc)
            continue
        if fingerprint != row["quick_fingerprint"]:
            stats["changed"] += 1
            _scan_known_directory(
                conn,
                int(row["root_id"] or 0),
                dir_path,
                int(row["depth"] or 2),
                exclusions,
                stats,
                file_limit=file_limit,
                dir_limit=dir_limit,
            )
        else:
            conn.execute("UPDATE local_index_dirs SET updated_at=? WHERE dir_id=?", (seen_at, row["dir_id"]))
    return stats


def reconcile_live_changes(
    *,
    asset_limit: int = DEFAULT_LIVE_ASSET_LIMIT,
    dir_limit: int = DEFAULT_LIVE_DIR_LIMIT,
    file_limit: int = DEFAULT_LIVE_FILE_LIMIT,
) -> dict:
    conn = _conn()
    if _is_paused():
        return {"ok": True, "paused": True, "assets": {}, "dirs": {}}
    exclusions = [row["path"] for row in list_exclusions()]
    asset_stats = _reconcile_known_assets(conn, exclusions, limit=int(asset_limit or 0))
    dir_stats = _reconcile_known_dirs(conn, exclusions, dir_limit=int(dir_limit or 0), file_limit=int(file_limit or 0))
    conn.commit()
    changed_total = (
        int(asset_stats.get("modified", 0))
        + int(asset_stats.get("deleted", 0))
        + int(asset_stats.get("excluded", 0))
        + int(dir_stats.get("files_changed", 0))
        + int(dir_stats.get("files_deleted", 0))
        + int(dir_stats.get("dirs_deleted", 0))
        + int(dir_stats.get("excluded_dirs", 0))
    )
    error_total = int(asset_stats.get("errors", 0) or 0) + int(dir_stats.get("errors", 0) or 0)
    public_asset_stats = _public_stats(asset_stats)
    public_dir_stats = _public_stats(dir_stats)
    if changed_total or error_total:
        log_event(
            "warn" if error_total else "info",
            "live_reconcile_finished",
            "Local memory live changes reconciled",
            assets=public_asset_stats,
            dirs=public_dir_stats,
        )
    return {"ok": True, "assets": public_asset_stats, "dirs": public_dir_stats}


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
        if should_skip_tree(str(root_path)):
            conn.execute(
                "UPDATE local_index_roots SET status='removed', last_scan_at=?, updated_at=? WHERE id=?",
                (now(), now(), root_id),
            )
            continue
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
        for file_path in _iter_files(
            conn,
            root_id,
            root_path,
            exclusions,
            limit=limit,
            start_after=str(checkpoint["current_path"] or ""),
            seen_at=cycle_started_at,
            stats=totals,
        ):
            asset_id, changed, state = _upsert_asset(conn, root_id, file_path, cycle_started_at, int(root["depth"] or 2))
            last_seen_path = norm_path(file_path)
            totals["seen"] += 1
            seen_for_root += 1
            if changed:
                totals["changed"] += 1
            if state not in {"ok", "skipped"}:
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
                _save_checkpoint(conn, root_id, last_seen_path, cycle_started_at=cycle_started_at, totals=_public_stats(totals))
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
    public_totals = _public_stats(totals)
    log_event("warn" if public_totals.get("errors") else "info", "scan_finished", "Local memory scan finished", **public_totals)
    return {"ok": True, **public_totals}


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


def _requeue_due_jobs(conn) -> dict:
    current = now()
    failed = conn.execute(
        """
        UPDATE local_index_jobs
        SET status='pending', claimed_by='', lease_expires_at=NULL, updated_at=?
        WHERE status='failed' AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
        """,
        (current, current),
    ).rowcount
    expired = conn.execute(
        """
        UPDATE local_index_jobs
        SET status='pending', claimed_by='', lease_expires_at=NULL, updated_at=?
        WHERE status='running' AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?
        """,
        (current, current),
    ).rowcount
    if failed or expired:
        log_event("warn", "jobs_requeued", "Local memory recovered stalled jobs", failed=failed, expired=expired)
    return {"failed": int(failed or 0), "expired": int(expired or 0)}


def process_jobs(*, limit: int = 100) -> dict:
    conn = _conn()
    if _is_paused():
        log_event("info", "jobs_skipped_paused", "Local memory jobs skipped because indexing is paused")
        return {"ok": True, "paused": True, "processed": 0, "failed": 0}
    recovered = _requeue_due_jobs(conn)
    rows = conn.execute(
        """
        SELECT j.*, a.path, a.depth, a.privacy_class, a.status AS asset_status
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
            if str(row["privacy_class"] or "normal") != "normal":
                conn.execute(
                    "UPDATE local_index_jobs SET status='done', updated_at=?, last_error_code='privacy_blocked' WHERE job_id=?",
                    (now(), job_id),
                )
                processed += 1
                continue
            if job_type == "light_extraction":
                text, metadata = extract_text(Path(row["path"]))
                version_id = _latest_version_id(conn, asset_id)
                if contains_secret(text):
                    _mark_content_secret_assets(conn, [asset_id])
                    conn.execute(
                        "UPDATE local_index_jobs SET status='done', updated_at=?, last_error_code='content_secret_blocked' WHERE job_id=?",
                        (now(), job_id),
                    )
                    processed += 1
                    continue
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
            _record_index_error(
                conn,
                asset_id=asset_id,
                path=row["path"],
                phase=job_type,
                error_code=type(exc).__name__,
                user_message="Algunos archivos no se pudieron leer",
                technical_detail=str(exc),
                retryable=True,
            )
    conn.commit()
    if processed or failed:
        log_event("info", "jobs_processed", "Local memory jobs processed", processed=processed, failed=failed)
    return {"ok": True, "processed": processed, "failed": failed, "recovered": recovered}


def run_once(
    *,
    root: str | None = None,
    limit: int | None = None,
    process_limit: int = 100,
    live_asset_limit: int = DEFAULT_LIVE_ASSET_LIMIT,
    live_dir_limit: int = DEFAULT_LIVE_DIR_LIMIT,
    live_file_limit: int = DEFAULT_LIVE_FILE_LIMIT,
) -> dict:
    if _get_state("privacy_hygiene_v2", "0") != "1":
        local_index_privacy_hygiene(fix=True)
        _set_state("privacy_hygiene_v2", "1")
    if root:
        add_root(root)
    elif (
        os.environ.get("NEXO_LOCAL_INDEX_DISABLE_DEFAULT_ROOTS", "").strip() != "1"
        and os.environ.get("NEXO_SKIP_FS_INDEX", "").strip() != "1"
        and not list_roots()
    ):
        ensure_default_roots()
    live_result = reconcile_live_changes(
        asset_limit=live_asset_limit,
        dir_limit=live_dir_limit,
        file_limit=live_file_limit,
    )
    scan_result = scan_once(limit=limit)
    job_result = process_jobs(limit=process_limit)
    return {"ok": True, "live": live_result, "scan": scan_result, "jobs": job_result}


def _problem_rows(conn) -> list[dict]:
    rows = conn.execute(
        """
        SELECT e.path, e.phase, e.error_code, e.user_message, e.technical_detail, e.retryable, e.created_at
        FROM local_index_errors e
        LEFT JOIN local_assets a ON a.asset_id=e.asset_id
        LEFT JOIN local_index_roots r ON r.id=a.root_id
        WHERE COALESCE(r.status, 'active') != 'removed'
          AND NOT EXISTS (
            SELECT 1
            FROM local_index_roots rr
            WHERE rr.status='removed'
              AND e.path != ''
              AND (e.path = rr.root_path OR e.path LIKE rr.root_path || '/%')
          )
        ORDER BY e.id DESC
        LIMIT 20
        """
    ).fetchall()
    problems = [
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
    last_success = conn.execute(
        "SELECT MAX(created_at) AS created_at FROM local_index_logs WHERE event='service_cycle_finished'"
    ).fetchone()["created_at"] or 0
    service_rows = conn.execute(
        """
        SELECT created_at, level, event, message, metadata_json
        FROM local_index_logs
        WHERE event IN ('service_cycle_failed', 'service_cycle_compat_fallback', 'service_cycle_skipped_lock')
          AND created_at > ?
        ORDER BY id DESC
        LIMIT 5
        """,
        (last_success,),
    ).fetchall()
    problems.extend(
        {
            "user_message": "La memoria local tuvo un problema temporal y NEXO la reintentara automaticamente",
            "recommended_action": "No tienes que hacer nada. Si se repite, abre soporte y diagnostico para ver el detalle.",
            "technical_detail": f"{row['event']}: {row['message']} {row['metadata_json']}",
            "support_code": row["event"],
            "severity": "warning" if row["level"] == "warn" else "error",
            "retryable": True,
            "path": "",
            "phase": "service",
            "created_at": row["created_at"],
        }
        for row in service_rows
    )
    return problems


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
    launchctl_status = ""

    code, stdout, _ = _command_output(["launchctl", "list"], timeout=2)
    if code == 0:
        for line in stdout.splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[-1] == LOCAL_INDEX_SERVICE_LABEL:
                installed = True
                pid = parts[0]
                launchctl_status = parts[1]
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
        "last_exit_code": launchctl_status,
        "config_path": str(plist_path),
    }


def _windows_local_index_service_status() -> dict:
    command = (
        "$task = Get-ScheduledTask -TaskName 'NEXO Local Memory' -ErrorAction SilentlyContinue; "
        "$info = if ($task) { Get-ScheduledTaskInfo -TaskName 'NEXO Local Memory' -ErrorAction SilentlyContinue }; "
        "if ($task) { "
        "$lastRun = if ($info -and $info.LastRunTime) { $info.LastRunTime.ToString('o') } else { '' }; "
        "$nextRun = if ($info -and $info.NextRunTime) { $info.NextRunTime.ToString('o') } else { '' }; "
        "$lastResult = if ($info) { [string]$info.LastTaskResult } else { '' }; "
        "Write-Output ($task.State.ToString() + '|' + $lastResult + '|' + $lastRun + '|' + $nextRun) "
        "}"
    )
    code, stdout, _ = _command_output(["powershell", "-NoProfile", "-Command", command], timeout=4)
    raw = stdout.strip()
    parts = raw.split("|") if "|" in raw else [raw]
    task_state = parts[0].strip() if parts else ""
    task_state_key = task_state.lower()
    last_task_result = parts[1].strip() if len(parts) > 1 else ""
    last_run_time = parts[2].strip() if len(parts) > 2 else ""
    next_run_time = parts[3].strip() if len(parts) > 3 else ""
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
        "last_task_result": last_task_result,
        "last_run_time": last_run_time,
        "next_run_time": next_run_time,
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


def _service_cycle_observation(conn) -> dict:
    last_success = conn.execute(
        "SELECT MAX(created_at) AS created_at FROM local_index_logs WHERE event='service_cycle_finished'"
    ).fetchone()["created_at"] or 0
    latest = conn.execute(
        """
        SELECT created_at, event, level, message, metadata_json
        FROM local_index_logs
        WHERE event IN ('service_cycle_finished', 'service_cycle_failed', 'service_cycle_compat_fallback', 'service_cycle_skipped_lock')
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    latest_error = conn.execute(
        """
        SELECT created_at, event, level, message, metadata_json
        FROM local_index_logs
        WHERE event IN ('service_cycle_failed', 'service_cycle_compat_fallback', 'service_cycle_skipped_lock')
          AND created_at > ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (last_success,),
    ).fetchone()
    observation = {
        "last_success_at": float(last_success or 0),
        "last_error_at": 0,
        "last_error_code": "",
        "last_error_detail": "",
        "healthy": latest_error is None,
    }
    if latest:
        observation["last_heartbeat_at"] = float(latest["created_at"] or 0)
    if latest_error:
        observation["last_error_at"] = float(latest_error["created_at"] or 0)
        observation["last_error_code"] = latest_error["event"]
        observation["last_error_detail"] = f"{latest_error['message']} {latest_error['metadata_json']}"
    return observation


def _index_timing(conn, *, done: int, active_jobs: int, percent: int) -> dict:
    first_seen = conn.execute(
        """
        SELECT MIN(created_at) AS created_at
        FROM local_index_logs
        WHERE event IN ('root_added', 'scan_started', 'scan_finished', 'jobs_processed', 'service_cycle_finished')
        """
    ).fetchone()["created_at"] or 0
    if not first_seen:
        first_seen = conn.execute(
            """
            SELECT MIN(first_seen_at) AS first_seen_at
            FROM local_assets
            WHERE status!='deleted'
            """
        ).fetchone()["first_seen_at"] or 0
    elapsed_seconds = max(0, int(now() - float(first_seen))) if first_seen else 0
    eta_seconds = None
    if elapsed_seconds > 0 and done > 0 and active_jobs > 0 and 0 < percent < 100:
        eta_seconds = max(0, int((elapsed_seconds / max(done, 1)) * active_jobs))
    return {"elapsed_seconds": elapsed_seconds, "eta_seconds": eta_seconds}


def _service_scheduler_has_error(service: dict) -> bool:
    if service.get("manager") == "launchagent":
        code = str(service.get("last_exit_code") or "").strip()
        return bool(code and code not in {"0", "-"})
    if service.get("manager") == "scheduled_task":
        code = str(service.get("last_task_result") or "").strip()
        return bool(code and code not in {"0"})
    return False


def _service_problem(service: dict) -> dict | None:
    if not service.get("installed"):
        return {
            "support_code": "local_index_service_not_installed",
            "user_message": "La memoria local aun no tiene activo el servicio en segundo plano",
            "recommended_action": "Reabre NEXO Desktop o actualiza a la ultima version para instalarlo automaticamente.",
            "technical_detail": f"manager={service.get('manager')} platform={service.get('platform')}",
        }
    if not service.get("running"):
        return {
            "support_code": "local_index_service_not_running",
            "user_message": "La memoria local no se esta actualizando en segundo plano",
            "recommended_action": "NEXO intentara recuperarlo automaticamente. Si se repite, abre soporte y diagnostico.",
            "technical_detail": f"manager={service.get('manager')} platform={service.get('platform')}",
        }
    if _service_scheduler_has_error(service):
        code = service.get("last_exit_code") or service.get("last_task_result") or ""
        return {
            "support_code": "local_index_service_last_run_failed",
            "user_message": "La ultima comprobacion de memoria local no termino correctamente",
            "recommended_action": "NEXO lo volvera a intentar automaticamente.",
            "technical_detail": f"last_result={code}",
        }
    if not service.get("healthy", True):
        return {
            "support_code": service.get("last_error_code") or "local_index_service_failed",
            "user_message": "La memoria local tuvo un problema temporal y NEXO la reintentara automaticamente",
            "recommended_action": "No tienes que hacer nada. Si se repite, abre soporte y diagnostico para ver el detalle.",
            "technical_detail": service.get("last_error_detail") or "",
        }
    return None


def status() -> dict:
    conn = _conn()
    paused = _is_paused()
    assets = conn.execute(
        """
        SELECT COUNT(*) AS total, SUM(CASE WHEN a.status='active' THEN 1 ELSE 0 END) AS active
        FROM local_assets a
        LEFT JOIN local_index_roots r ON r.id=a.root_id
        WHERE COALESCE(r.status, 'active') != 'removed'
        """
    ).fetchone()
    job_rows = conn.execute(
        """
        SELECT j.status, COUNT(*) AS total
        FROM local_index_jobs j
        JOIN local_assets a ON a.asset_id=j.asset_id
        LEFT JOIN local_index_roots r ON r.id=a.root_id
        WHERE a.status='active'
          AND COALESCE(r.status, 'active') != 'removed'
        GROUP BY j.status
        """
    ).fetchall()
    job_counts = {row["status"]: int(row["total"] or 0) for row in job_rows}
    pending = int(job_counts.get("pending", 0) or 0)
    running_jobs = int(job_counts.get("running", 0) or 0)
    failed_jobs = int(job_counts.get("failed", 0) or 0)
    done = int(job_counts.get("done", 0) or 0)
    active_jobs = pending + running_jobs + failed_jobs
    total_jobs = active_jobs + done
    percent = 100 if total_jobs == 0 else int((done / max(total_jobs, 1)) * 100)
    timing = _index_timing(conn, done=done, active_jobs=active_jobs, percent=percent)
    roots = list_roots()
    volumes = []
    by_volume = conn.execute(
        """
        SELECT a.volume_id, COUNT(*) AS files
        FROM local_assets a
        LEFT JOIN local_index_roots r ON r.id=a.root_id
        WHERE a.status='active'
          AND COALESCE(r.status, 'active') != 'removed'
        GROUP BY a.volume_id
        ORDER BY a.volume_id
        """
    ).fetchall()
    for row in by_volume:
        volumes.append({"id": row["volume_id"], "label": row["volume_id"] or "Disk", "files": row["files"], "status": "active"})
    service = _local_index_service_status()
    service.update(_service_cycle_observation(conn))
    problem = _service_problem(service)
    service["healthy"] = problem is None
    service["state"] = "paused" if paused else ("attention" if problem else ("idle" if active_jobs == 0 else "indexing"))
    problems = _problem_rows(conn)
    if problem:
        problems.insert(0, {
            "user_message": problem["user_message"],
            "recommended_action": problem["recommended_action"],
            "technical_detail": problem["technical_detail"],
            "support_code": problem["support_code"],
            "severity": "warning",
            "retryable": True,
            "path": "",
            "phase": "service",
            "created_at": now(),
        })
    return {
        "ok": True,
        "service": service,
        "global": {
            "phase": "paused" if paused else ("service_attention" if problem else ("idle" if active_jobs == 0 else "light_extraction")),
            "percent": percent,
            "files_found": int(assets["total"] or 0),
            "files_processed": int(done or 0),
            "changes_pending": int(active_jobs or 0),
            "jobs_pending": pending,
            "jobs_running": running_jobs,
            "jobs_failed": failed_jobs,
            "elapsed_seconds": timing["elapsed_seconds"],
            "eta_seconds": timing["eta_seconds"],
        },
        "volumes": volumes,
        "roots": roots,
        "exclusions": list_exclusions(),
        "problems": problems,
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


_QUERY_STOPWORDS = {
    "about",
    "archivos",
    "con",
    "context",
    "contexto",
    "cuanto",
    "dame",
    "del",
    "desde",
    "documentos",
    "donde",
    "esta",
    "está",
    "file",
    "files",
    "hay",
    "los",
    "para",
    "que",
    "qué",
    "related",
    "relacionado",
    "sabes",
    "sobre",
    "todo",
    "what",
    "where",
}


def _query_terms(query: str) -> list[str]:
    terms = []
    for token in tokenize(query):
        if len(token) < 3 or token in _QUERY_STOPWORDS:
            continue
        if token not in terms:
            terms.append(token)
    return terms[:10]


def _entity_match_score(query_lower: str, terms: list[str], name: str) -> float:
    entity = (name or "").strip().lower()
    if not entity:
        return 0.0
    entity_terms = set(tokenize(entity))
    if entity and entity in query_lower:
        return 1.0
    if not terms:
        return 0.0
    term_set = set(terms)
    overlap = term_set & entity_terms
    if overlap:
        return min(0.95, 0.45 + (len(overlap) / max(len(entity_terms), 1)) * 0.5)
    if any(term in entity for term in terms):
        return 0.6
    return 0.0


def _entity_matches_for_query(conn, query: str, *, limit: int) -> tuple[list[dict], dict[str, float]]:
    query_lower = (query or "").strip().lower()
    terms = _query_terms(query)
    if not query_lower or not terms:
        return [], {}

    clauses = " OR ".join("lower(e.name) LIKE ?" for _ in terms)
    params = [f"%{term}%" for term in terms]
    rows = conn.execute(
        f"""
        SELECT DISTINCT e.name, e.entity_type, e.asset_id, a.path, a.privacy_class
        FROM local_entities e
        JOIN local_assets a ON a.asset_id = e.asset_id
        WHERE a.status='active'
          AND a.privacy_class='normal'
          AND ({clauses})
        LIMIT ?
        """,
        [*params, max(int(limit) * 20, 40)],
    ).fetchall()

    matches = []
    boosts: dict[str, float] = {}
    seen = set()
    for row in rows:
        if not is_queryable_path(str(row["path"] or ""), str(row["privacy_class"] or "")):
            continue
        score = _entity_match_score(query_lower, terms, str(row["name"] or ""))
        if score <= 0:
            continue
        key = (row["name"], row["entity_type"], row["asset_id"])
        if key not in seen:
            matches.append({
                "name": row["name"],
                "entity_type": row["entity_type"],
                "asset_id": row["asset_id"],
                "score": round(float(score), 4),
            })
            seen.add(key)
        boosts[row["asset_id"]] = max(boosts.get(row["asset_id"], 0.0), float(score))

    matches.sort(key=lambda item: item.get("score", 0), reverse=True)
    return matches[: int(limit)], boosts


def _context_candidate_rows(conn, entity_asset_ids: list[str], *, base_limit: int = 5000) -> list:
    base_rows = conn.execute(
        """
        SELECT c.chunk_id, c.asset_id, c.text, a.path, a.file_type, a.privacy_class, v.summary, e.vector_json
        FROM local_chunks c
        JOIN local_assets a ON a.asset_id = c.asset_id
        LEFT JOIN local_asset_versions v ON v.version_id = c.version_id
        LEFT JOIN local_embeddings e ON e.chunk_id = c.chunk_id
        WHERE a.status='active'
          AND a.privacy_class='normal'
        ORDER BY c.created_at DESC
        LIMIT ?
        """,
        (int(base_limit),),
    ).fetchall()
    if not entity_asset_ids:
        return base_rows

    placeholders = ",".join("?" for _ in entity_asset_ids)
    entity_rows = conn.execute(
        f"""
        SELECT c.chunk_id, c.asset_id, c.text, a.path, a.file_type, a.privacy_class, v.summary, e.vector_json
        FROM local_chunks c
        JOIN local_assets a ON a.asset_id = c.asset_id
        LEFT JOIN local_asset_versions v ON v.version_id = c.version_id
        LEFT JOIN local_embeddings e ON e.chunk_id = c.chunk_id
        WHERE a.status='active'
          AND a.privacy_class='normal'
          AND c.asset_id IN ({placeholders})
        ORDER BY c.chunk_index ASC
        LIMIT ?
        """,
        [*entity_asset_ids, max(1000, len(entity_asset_ids) * 80)],
    ).fetchall()

    rows = []
    seen_chunks = set()
    for row in [*entity_rows, *base_rows]:
        chunk_id = row["chunk_id"]
        if chunk_id in seen_chunks:
            continue
        seen_chunks.add(chunk_id)
        rows.append(row)
    return rows


def context_query(query: str, *, intent: str = "answer", limit: int = 12, evidence_required: bool = True, current_context: str = "") -> dict:
    conn = _conn()
    qvec = embeddings.embed_text(query)
    entities_payload, entity_boosts = _entity_matches_for_query(conn, query, limit=max(int(limit), 1))
    rows = _context_candidate_rows(conn, list(entity_boosts.keys()), base_limit=5000)
    scored = []
    for row in rows:
        if not is_queryable_path(str(row["path"] or ""), str(row["privacy_class"] or "")):
            continue
        vector = json_loads(row["vector_json"], [])
        text_score = _search_text_score(query, row["text"])
        path_score = _search_text_score(query, row["path"] or "")
        summary_score = _search_text_score(query, row["summary"] or "")
        entity_score = entity_boosts.get(row["asset_id"], 0.0)
        vector_score = embeddings.cosine(qvec, vector)
        score = max(text_score, path_score, summary_score, vector_score)
        if entity_score > 0:
            direct_score = max(text_score, path_score, summary_score)
            if direct_score > 0:
                entity_rank = 0.82 + (0.42 * text_score) + (0.18 * path_score) + (0.12 * summary_score)
                score = max(score, entity_rank + min(0.2, entity_score * 0.2))
            else:
                # Entity-level matches keep older assets eligible, but do not let
                # unrelated chunks from a long document outrank direct evidence.
                score = max(score, min(0.48, 0.28 + entity_score * 0.2))
        if score > 0:
            scored.append((min(float(score), 1.6), row))
    scored.sort(key=lambda item: item[0], reverse=True)
    assets = []
    chunks = []
    evidence_refs = []
    seen_assets = set()
    for score, row in scored[: int(limit)]:
        if row["asset_id"] not in seen_assets:
            assets.append({
                "asset_id": row["asset_id"],
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
    relations_payload: list[dict] = []
    relation_asset_ids = list(dict.fromkeys([*seen_assets, *entity_boosts.keys()]))[: int(limit)]
    if relation_asset_ids:
        asset_ids = relation_asset_ids
        placeholders = ",".join("?" for _ in asset_ids)
        relation_rows = conn.execute(
            f"""
            SELECT relation_id, source_asset_id, target_ref, relation_type, confidence, evidence
            FROM local_relations
            WHERE active=1 AND source_asset_id IN ({placeholders})
            ORDER BY confidence DESC
            LIMIT ?
            """,
            [*asset_ids, int(limit) * 3],
        ).fetchall()
        relations_payload = [dict(row) for row in relation_rows]
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
        "relations": relations_payload,
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
    _purge_asset_ids(conn, [asset_id])
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
        "local_index_dirs",
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
