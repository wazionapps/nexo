#!/usr/bin/env python3
"""PostToolUse unified handler.

Runs the five shell scripts that used to be registered individually for this
event (capture-tool-logs, capture-session, inbox-hook, protocol-guardrail,
heartbeat-posttool). Also pipes the tool result through auto_capture.py so
decision/correction/explicit facts from tool outputs reach the cognitive
layer.

v6.0.1 adds an inbox-autodetect stage at the end: when the session has
unread ``nexo_send`` messages AND has gone for ≥60s without a heartbeat,
the hook emits a ``systemMessage`` telling the agent to run
``nexo_heartbeat`` and pick them up. Rate-limited to one reminder per
minute per SID via the ``hook_inbox_reminders`` table (migration m42).

Failures in one sub-step do not cancel the others. Hook is best-effort;
exit code is always 0 so Claude Code never sees a PostToolUse failure.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path


_DIR = Path(__file__).resolve().parent
if str(_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_DIR.parent))

from core_prompts import render_core_prompt
from operator_language import append_operator_language_contract

_NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))

INBOX_CHECK_THRESHOLD_SECONDS = int(
    os.environ.get("NEXO_INBOX_CHECK_THRESHOLD_SECONDS", "60")
)
AUTO_GUARD_TTL_SECONDS = int(os.environ.get("NEXO_AUTO_GUARD_TTL_SECONDS", "300"))


def _resolve_sid_from_payload(payload: dict) -> str:
    """Resolve the NEXO SID from the hook payload or fall back to env.

    Claude Code delivers its own ``session_id`` in the payload; we map
    it back to our SID via ``sessions.external_session_id``. The
    fallback is ``NEXO_SID`` in the environment, which headless crons
    export directly.
    """
    candidates: list[str] = []
    if isinstance(payload, dict):
        for key in ("nexo_sid", "sid", "session_id", "sessionId"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                candidates.append(value.strip())
    env_sid = os.environ.get("NEXO_SID", "").strip()
    if env_sid:
        candidates.append(env_sid)
    env_claude = os.environ.get("CLAUDE_SESSION_ID", "").strip()
    if env_claude:
        candidates.append(env_claude)

    # Try each candidate: first as a NEXO-shaped SID (nexo-<epoch>-<pid>),
    # then as a Claude external id we need to translate.
    try:
        sys.path.insert(0, str(_DIR.parent))
        from db import (  # type: ignore
            resolve_sid_from_external,
            get_last_heartbeat_ts,
        )
    except Exception:
        return ""

    for cand in candidates:
        if cand.startswith("nexo-"):
            return cand
        resolved = resolve_sid_from_external(cand)
        if resolved:
            return resolved
    return ""


def check_inbox_and_emit_reminder(sid: str, now: float | None = None) -> str | None:
    """Return the systemMessage string when a reminder should be surfaced.

    Returns ``None`` when any gate fails (no sid, no pending messages,
    heartbeat too recent, rate-limited on reminders).
    """
    if not sid:
        return None
    try:
        sys.path.insert(0, str(_DIR.parent))
        from db import (  # type: ignore
            count_pending_inbox_messages,
            get_last_heartbeat_ts,
            get_last_reminder_ts,
            mark_reminder_sent,
        )
    except Exception:
        return None

    pending = count_pending_inbox_messages(sid)
    if pending <= 0:
        return None
    last_hb = get_last_heartbeat_ts(sid)
    if last_hb is None:
        return None  # pre-v6.0.1 row or brand-new session
    current = float(now) if now is not None else time.time()
    if current - last_hb < INBOX_CHECK_THRESHOLD_SECONDS:
        return None
    last_rem = get_last_reminder_ts(sid) or 0.0
    if current - last_rem < INBOX_CHECK_THRESHOLD_SECONDS:
        return None  # rate limit: max 1 reminder/min/session
    mark_reminder_sent(sid, current)
    return append_operator_language_contract(
        render_core_prompt(
            "post-tool-inbox-reminder",
            pending=str(pending),
        )
    )


def _record(duration_ms: int, exit_code: int, summary: str) -> None:
    try:
        sys.path.insert(0, str(_DIR.parent))
        import hook_observability  # type: ignore
        hook_observability.record_hook_run(
            "post_tool_use",
            duration_ms=duration_ms,
            exit_code=exit_code,
            summary=summary,
            session_id=os.environ.get("CLAUDE_SESSION_ID", ""),
        )
    except Exception:
        pass


def _run(cmd: list[str], timeout: int) -> int:
    try:
        return subprocess.run(cmd, timeout=timeout, capture_output=True).returncode
    except Exception:
        return 1


def _run_step(cmd: list[str], timeout: int) -> tuple[int, str]:
    try:
        result = subprocess.run(cmd, timeout=timeout, capture_output=True, text=True)
        return result.returncode, (result.stdout or "").strip()
    except Exception:
        return 1, ""


def _combine_system_messages(*messages: str | None) -> str | None:
    parts = [str(item).strip() for item in messages if str(item or "").strip()]
    if not parts:
        return None
    return "\n\n".join(parts)


def _read_stdin_json() -> dict:
    """Read the Claude Code hook payload from stdin. Never raises."""
    if sys.stdin.isatty():
        return {}
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return {}
        return json.loads(raw)
    except Exception:
        return {}


def _extract_tool_text(payload: dict) -> str:
    """Pull the bit we actually care about from the tool result envelope."""
    if not isinstance(payload, dict):
        return ""
    result = payload.get("tool_result") or payload.get("result") or {}
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        content = result.get("content") or result.get("output") or result.get("text") or ""
        if isinstance(content, str) and content.strip():
            return content
        if isinstance(content, list):
            return "\n".join(
                str(item.get("text", "")) if isinstance(item, dict) else str(item)
                for item in content
            )
    fallback_parts = []
    for key in ("tool_response", "output", "stdout", "stderr"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            fallback_parts.append(value)
    if fallback_parts:
        return "\n".join(fallback_parts)
    return ""


def _tool_name(payload: dict) -> str:
    return str(payload.get("tool_name") or payload.get("name") or "").strip()


def _tool_input(payload: dict) -> dict:
    for key in ("tool_input", "input", "arguments"):
        value = payload.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _production_closeout_dir() -> Path:
    try:
        import paths  # type: ignore

        root = paths.operations_dir()
    except Exception:
        root = _NEXO_HOME / "runtime" / "operations"
    path = root / "protocol-closeout"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _pending_change_log_path(sid: str) -> Path:
    safe_sid = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in (sid or "unknown"))
    return _production_closeout_dir() / f"change-log-required-{safe_sid}.json"


def _pending_learning_path(sid: str) -> Path:
    safe_sid = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in (sid or "unknown"))
    return _production_closeout_dir() / f"learning-required-{safe_sid}.json"


def _extract_command(payload: dict) -> str:
    tool_input = _tool_input(payload)
    for key in ("command", "cmd", "script"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _is_production_mutation_command(cmd: str) -> bool:
    patterns = (
        r"\bgit\s+push\s+origin\s+main\b(?!.*--dry-run)",
        r"\bfirebase\s+deploy\b(?!.*--dry-run)",
        r"\bshopify\s+theme\s+push\b(?!.*--dry-run)",
        r"\bgh\s+pr\s+merge\b(?!.*--dry-run)",
        r"\baz\s+vm\s+create\b(?!.*--dry-run)",
        r"\bgcloud\s+run\s+deploy\b(?!.*--dry-run)",
        r"\bgit\s+push\b(?!.*--dry-run)(?=.*\b(?:origin\s+)?(?:main|master|stable|release)\b)",
        r"\bgit\s+push\b(?!.*--dry-run)(?=.*--tags\b)",
        r"\bgh\s+release\s+(?:create|upload|edit)\b",
        r"\bgcloud\s+builds\s+submit\b",
        r"\bgcloud\s+builds\s+triggers\s+run\b",
        r"\bgcloud\s+run\s+(?:deploy|services\s+update|jobs\s+deploy|jobs\s+update)\b",
        r"\bgcloud\s+dns\s+record-sets\s+transaction\s+execute\b",
        r"\b(?:alembic\s+upgrade|prisma\s+migrate\s+deploy|sequelize\s+db:migrate|knex\s+migrate:latest|rails\s+db:migrate|python(?:3)?\s+manage\.py\s+migrate|php\s+artisan\s+migrate)\b",
        r"\bg(?:sutil|cloud\s+storage)\b.*\b(?:cp|rsync)\b.*\b(?:release|stable|cdn|bucket|buckets)\b",
        r"\b(?:rsync|scp)\b(?!.*--dry-run).+\s+\S+:(?:/[^ \n\r;]*)(?:public_html|httpdocs|www|webroot|home/nexodesk)\b",
        r"\bssh\b[^'\"]*['\"][^'\"]*(?:sed\s+-i|tee\s+|>\s*\S|>>\s*\S|rm\s+-|mv\s+|cp\s+)[^'\"]*(?:public_html|httpdocs|/var/www|/opt/|/home/nexodesk)[^'\"]*['\"]",
        r"\b(?:whmapi1|uapi|cpapi2)\b",
        r"\b(?:cloudflare|cfcli)\b.*\b(?:dns|record)\b.*\b(?:create|delete|update|patch|put|post)\b",
        r"\bcurl\b(?=.*api\.cloudflare\.com/client/v4/zones/.*/dns_records)(?=.*(?:-X|--request)\s*(?:POST|PUT|PATCH|DELETE)\b)",
        r"\bcws-upload(?:\.sh)?\b.*\bpublish\b",
        r"\bnpm\s+publish\b",
    )
    return any(re.search(pattern, cmd, re.IGNORECASE | re.DOTALL) for pattern in patterns)


def _mutation_files(payload: dict) -> list[str]:
    if not _is_shared_mutation_payload(payload):
        return []
    tool_input = _tool_input(payload)
    files: set[str] = set()
    for key in ("file_path", "path", "files", "paths"):
        files.update(_split_files(tool_input.get(key)))
    return sorted(files)


def _project_atlas_path() -> Path:
    try:
        import paths  # type: ignore

        return paths.brain_dir() / "project-atlas.json"
    except Exception:
        return _NEXO_HOME / "brain" / "project-atlas.json"


def _atlas_projects(atlas: dict) -> dict:
    if isinstance(atlas.get("projects"), dict):
        return atlas["projects"]
    return {key: value for key, value in atlas.items() if isinstance(value, dict) and not str(key).startswith("_")}


def _iter_atlas_locations(entry: dict):
    locations = entry.get("locations")
    if isinstance(locations, dict):
        for value in locations.values():
            if isinstance(value, str) and value.strip():
                yield value.strip()
    for key in ("repo", "local", "theme_local", "main_repo", "mcp_server"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            yield value.strip()


def _resolve_area_from_atlas(files: list[str]) -> str:
    if not files:
        return ""
    try:
        atlas = json.loads(_project_atlas_path().read_text(encoding="utf-8"))
    except Exception:
        return ""
    expanded_files = [str(Path(item).expanduser()) for item in files if item]
    best: tuple[int, str] = (0, "")
    for project_key, entry in _atlas_projects(atlas).items():
        if not isinstance(entry, dict):
            continue
        for raw_location in _iter_atlas_locations(entry):
            location = str(Path(raw_location.replace("~", str(Path.home()))).expanduser())
            if not location or location == ".":
                continue
            for filepath in expanded_files:
                if filepath == location or filepath.startswith(location.rstrip("/") + "/"):
                    score = len(location)
                    if score > best[0]:
                        best = (score, str(project_key))
    if best[1]:
        return best[1]
    joined = "\n".join(expanded_files).lower()
    if "/.nexo/core/" in joined or "/documents/_phpstormprojects/nexo/" in joined:
        return "nexo"
    return ""


def _recent_guard_check_exists(sid: str, files: list[str], area: str, now: float | None = None) -> bool:
    if not sid:
        return False
    try:
        from db import get_db  # type: ignore
    except Exception:
        return False
    cutoff = float(now) if now is not None else time.time()
    cutoff -= AUTO_GUARD_TTL_SECONDS
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT files, area, strftime('%s', created_at) AS created_epoch "
            "FROM guard_checks WHERE session_id = ? ORDER BY id DESC LIMIT 50",
            (sid,),
        ).fetchall()
    except Exception:
        return False
    file_tokens = {Path(item).name for item in files if item}
    file_tokens.update(item for item in files if item)
    for row in rows:
        try:
            created_epoch = float(row["created_epoch"] or 0)
        except Exception:
            created_epoch = 0.0
        if created_epoch and created_epoch < cutoff:
            continue
        row_area = str(row["area"] or "").strip()
        row_files = str(row["files"] or "")
        if area and row_area == area:
            return True
        if any(token and token in row_files for token in file_tokens):
            return True
    return False


def _record_auto_guard_debt(sid: str, evidence: str) -> None:
    try:
        from db import create_protocol_debt  # type: ignore

        create_protocol_debt(
            sid or "unknown",
            "auto_guard_check_failed",
            severity="error",
            evidence=evidence[:4000],
        )
    except Exception:
        pass


def _queue_auto_guard_check(payload: dict, sid: str, now: float | None = None) -> str | None:
    files = _mutation_files(payload)
    if not files:
        return None
    area = _resolve_area_from_atlas(files)
    if _recent_guard_check_exists(sid, files, area, now=now):
        return None
    guard_payload = {
        "files": ",".join(files),
        "area": area,
        "project_hint": area,
        "include_schemas": "true",
        "enforce_runtime_core_block": "true",
    }
    try:
        from mcp_write_queue import enqueue_write  # type: ignore

        queued = enqueue_write("guard_check", guard_payload, priority="high", wait=True, timeout_ms=2500)
    except Exception as exc:
        evidence = f"PostToolUse auto guard_check could not enqueue: {type(exc).__name__}: {exc}"
        _record_auto_guard_debt(sid, evidence)
        return append_operator_language_contract(
            "No he podido registrar automáticamente la revisión previa del cambio; queda marcada para revisión antes del cierre."
        )
    error_text = str(queued.get("last_error") or queued.get("error") or "")
    if not queued.get("accepted") or queued.get("status") in {"failed", "dead_letter"} or "Unknown tool" in error_text:
        evidence = f"PostToolUse auto guard_check failed for files={files}, area={area}: {queued}"
        _record_auto_guard_debt(sid, evidence)
        return append_operator_language_contract(
            "No he podido registrar automáticamente la revisión previa del cambio; queda marcada para revisión antes del cierre."
        )
    return None


def _is_release_publication_command(cmd: str) -> bool:
    patterns = (
        r"\bgit\s+push\b(?!.*--dry-run)(?=.*--tags\b)",
        r"\bnpm\s+publish\b",
        r"\bgh\s+release\s+(?:create|upload|edit)\b",
        r"\bupload-release\.sh\b",
        r"\bcws-upload(?:\.sh)?\b.*\bpublish\b",
    )
    return any(re.search(pattern, cmd, re.IGNORECASE | re.DOTALL) for pattern in patterns)


_WEBROOT_BACKUP_RE = re.compile(
    r"https?://[^\s'\"<>]+(?:\.php\.(?:bak|old|new)|\.(?:bak|old|new|sql|zip|tar|tgz|gz|env))(?:[?#][^\s'\"<>]*)?",
    re.IGNORECASE,
)
_HTTP_200_RE = re.compile(r"\b(?:HTTP/\d(?:\.\d)?\s+200|200\s+OK|http_code\s*=\s*200|status\s*[:=]\s*200)\b", re.IGNORECASE)
_SECRET_MARKER_RE = re.compile(
    r"\b(?:OPENAI_API_KEY|DB_PASSWORD|DB_USERNAME|MYSQL_PASSWORD|SHOPIFY_TOKEN|STRIPE_SECRET|"
    r"api[_-]?key|secret|password|Bearer\s+[A-Za-z0-9._-]{12,}|sk_(?:live|test)_[A-Za-z0-9]+|sk-proj-[A-Za-z0-9_-]+)\b",
    re.IGNORECASE,
)


def _served_backup_secret_signal(payload: dict) -> dict | None:
    text = "\n".join(part for part in (_extract_command(payload), _extract_tool_text(payload)) if part)
    if not text:
        return None
    match = _WEBROOT_BACKUP_RE.search(text)
    if not match:
        return None
    if not _HTTP_200_RE.search(text):
        return None
    if not _SECRET_MARKER_RE.search(text):
        return None
    return {"url": match.group(0)[:240]}


def _security_followup_id(seed: str) -> str:
    import hashlib

    digest = hashlib.sha1(seed.encode("utf-8", errors="ignore"), usedforsecurity=False).hexdigest()[:8].upper()
    return f"NF-SECURITY-WEBROOT-BACKUP-ROTATE-{digest}"


def _ensure_webroot_backup_rotation_followup(payload: dict, sid: str) -> str | None:
    signal = _served_backup_secret_signal(payload)
    if not signal:
        return None
    followup_id = _security_followup_id(f"{sid}:{signal['url']}")
    try:
        from db import create_followup, get_followup  # type: ignore

        if not get_followup(followup_id):
            create_followup(
                followup_id,
                description=(
                    "SEGURIDAD: canary HTTP detectó un backup o artefacto temporal servible desde webroot "
                    f"con marcador de secreto ({signal['url']}). Rotar/revocar los secretos expuestos, "
                    "mover el artefacto fuera del webroot y dejar bloqueo/canary verificado."
                ),
                date=time.strftime("%Y-%m-%d"),
                verification=(
                    "Cierre solo con HTTP público ya no servible (404/403), evidencia de revocación/rotación "
                    "de la credencial antigua y nueva ubicación segura registrada."
                ),
                reasoning="post_tool_use webroot backup canary detected served secret",
                priority="critical",
                internal=1,
                owner="agent",
            )
    except Exception:
        return None
    return followup_id


def _is_change_log_tool(tool_name: str) -> bool:
    return tool_name in {"nexo_change_log", "mcp__nexo__nexo_change_log"}


def _is_task_close_tool(tool_name: str) -> bool:
    return tool_name in {"nexo_task_close", "mcp__nexo__nexo_task_close"}


def _is_learning_tool(tool_name: str) -> bool:
    return tool_name in {"nexo_learning_add", "mcp__nexo__nexo_learning_add"}


def _learning_tool_result_has_id(payload: dict) -> bool:
    result_text = _extract_tool_text(payload)
    try:
        serialized = json.dumps(payload.get("tool_result") or payload.get("result") or {}, ensure_ascii=False)
    except Exception:
        serialized = ""
    joined = "\n".join(part for part in (result_text, serialized) if part)
    if not joined.strip():
        return False
    if re.search(r"\b(?:learning_id|id)\s*[:=]\s*(?:null|none|0)\b", joined, re.IGNORECASE):
        return False
    return bool(
        re.search(r"\b(?:learning_id|id)\s*[:=]\s*[1-9]\d*\b", joined, re.IGNORECASE)
        or re.search(r"\b(?:LEARNING|aprendizaje)\s*#\s*[1-9]\d*\b", joined, re.IGNORECASE)
    )


def _task_close_payload_has_change_trace(payload: dict) -> bool:
    tool_input = _tool_input(payload)
    files = str(tool_input.get("files_changed") or "").strip()
    what = str(tool_input.get("change_summary") or tool_input.get("summary") or "").strip()
    why = str(tool_input.get("change_why") or tool_input.get("triggered_by") or "").strip()
    return bool(files and what and why)


def _task_close_payload_has_learning_trace(payload: dict) -> bool:
    tool_input = _tool_input(payload)
    title = str(tool_input.get("learning_title") or "").strip()
    content = str(tool_input.get("learning_content") or "").strip()
    if title and content:
        return True
    joined = "\n".join(str(tool_input.get(key) or "") for key in ("evidence", "evidence_refs", "verification", "summary", "result"))
    return bool(re.search(r"\b(?:nexo_learning_add|learning_id|aprendizaje\s+#?\d+|learning\s+#?\d+)\b", joined, re.IGNORECASE))


def _is_production_learning_file(path: str) -> bool:
    lowered = str(path or "").lower()
    if not lowered:
        return False
    if lowered.endswith((".php", ".js", ".jsx", ".ts", ".tsx", ".py", ".sh", ".yml", ".yaml", ".json")):
        return any(
            marker in lowered
            for marker in (
                "/documents/_phpstormprojects/",
                "/public_html/",
                "/httpdocs/",
                "/var/www/",
                "/home/nexodesk/",
                "/opt/",
                "/scripts/",
                "/infra/",
                "/cron/",
                "/src/",
            )
        )
    return False


def _learning_relevant_files(payload: dict) -> list[str]:
    if not _is_shared_mutation_payload(payload):
        return []
    return [path for path in _mutation_files(payload) if _is_production_learning_file(path)]


def _edit_resolution_signal(payload: dict) -> bool:
    tool_input = _tool_input(payload)
    try:
        text = json.dumps(tool_input, ensure_ascii=False)
    except Exception:
        text = str(tool_input or "")
    return bool(
        re.search(
            r"\b(fix|fixed|bug|error|exception|regression|resolved|solution|"
            r"correg|arregl|fallo|error|regresi[oó]n|soluci[oó]n|reusable|patr[oó]n)\b",
            text,
            re.IGNORECASE,
        )
    )


def check_learning_capture_closeout(payload: dict, sid: str) -> str | None:
    if not sid:
        sid = "unknown"
    tool_name = _tool_name(payload)
    pending_path = _pending_learning_path(sid)
    if _is_learning_tool(tool_name):
        if _learning_tool_result_has_id(payload):
            pending_path.unlink(missing_ok=True)
            return None
        _write_json(
            pending_path,
            {
                "sid": sid,
                "files": [],
                "tool_name": tool_name,
                "created_at": time.time(),
                "reason": "learning_add returned without a durable learning id",
            },
        )
        return append_operator_language_contract(
            "`nexo_learning_add(...)` no devolvió un ID de aprendizaje válido. Reintenta la captura antes de continuar."
        )
        return None

    files = _learning_relevant_files(payload)
    if files and _edit_resolution_signal(payload):
        _write_json(
            pending_path,
            {
                "sid": sid,
                "files": files[:20],
                "tool_name": tool_name,
                "created_at": time.time(),
                "reason": "production edit looked like a bugfix/reusable solution",
            },
        )
        return append_operator_language_contract(
            "Antes del cierre: si esta edición resolvió un error o dejó una solución reutilizable, llama a "
            "`nexo_learning_add(...)` antes de `nexo_task_close(...)`."
        )

    if not _is_task_close_tool(tool_name):
        return None
    pending = _read_json(pending_path)
    if not pending:
        return None
    if _task_close_payload_has_learning_trace(payload):
        pending_path.unlink(missing_ok=True)
        return None
    files_text = ", ".join((pending.get("files") or [])[:4]) or "edición productiva"
    return append_operator_language_contract(
        "Cierre pendiente: hay una edición productiva que parece haber resuelto un error o patrón reutilizable. "
        f"Registra primero `nexo_learning_add(...)` o incluye `learning_title` y `learning_content` en `nexo_task_close(...)`. "
        f"Archivos: {files_text}."
    )


def _change_log_has_production_release_refs(payload: dict) -> bool:
    tool_input = _tool_input(payload)
    try:
        joined = json.dumps(tool_input, ensure_ascii=False).lower()
    except Exception:
        joined = str(tool_input).lower()
    has_production_scope = (
        str(tool_input.get("scope") or "").strip().lower() == "production"
        or "scope=production" in joined
        or '"scope": "production"' in joined
        or '"scope":"production"' in joined
    )
    has_commit = bool(
        re.search(r"\bcommit(?:_ref)?\b", joined)
        and re.search(r"\b[0-9a-f]{7,40}\b", joined, re.IGNORECASE)
    )
    has_tag = bool(re.search(r"\b(?:tag|version)\b", joined) and re.search(r"\bv?\d+\.\d+\.\d+(?:[-+][a-z0-9_.-]+)?\b", joined, re.IGNORECASE))
    has_release_url = bool(re.search(r"https://github\.com/[^ \n\r\t'\"<>]+/releases/(?:tag|download)/[^ \n\r\t'\"<>]+", joined, re.IGNORECASE))
    return has_production_scope and (has_commit or has_tag or has_release_url)


def _queue_change_log_from_task_close(payload: dict, sid: str, pending: dict) -> bool:
    if not _task_close_payload_has_change_trace(payload):
        return False
    try:
        from mcp_write_queue import enqueue_write  # type: ignore
    except Exception:
        return False
    tool_input = _tool_input(payload)
    queued = enqueue_write(
        "change_log",
        {
            "session_id": sid,
            "files": str(tool_input.get("files_changed") or ""),
            "what_changed": str(tool_input.get("change_summary") or tool_input.get("summary") or ""),
            "why": str(tool_input.get("change_why") or tool_input.get("triggered_by") or pending.get("command") or ""),
            "triggered_by": str(tool_input.get("triggered_by") or pending.get("triggered_by") or "post_tool_use production mutation"),
            "affects": str(tool_input.get("change_summary") or ""),
            "risks": str(tool_input.get("change_risks") or ""),
            "verify": str(tool_input.get("change_verify") or tool_input.get("verification") or tool_input.get("evidence") or ""),
            "commit_ref": str(tool_input.get("commit_ref") or ""),
        },
        priority="high",
    )
    return bool(queued.get("accepted"))


def _extract_workdir(payload: dict) -> str:
    tool_input = _tool_input(payload)
    for key in ("cwd", "workdir", "working_dir"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _git_head_change_context(workdir: str) -> dict:
    if not workdir:
        return {}
    path = Path(workdir)
    if not path.is_dir():
        return {}
    try:
        inside = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(path),
            capture_output=True,
            text=True,
            timeout=2,
        )
        if inside.returncode != 0 or inside.stdout.strip() != "true":
            return {}
        head = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=str(path),
            capture_output=True,
            text=True,
            timeout=2,
        )
        files = subprocess.run(
            ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"],
            cwd=str(path),
            capture_output=True,
            text=True,
            timeout=2,
        )
    except Exception:
        return {}
    result: dict[str, str] = {}
    if head.returncode == 0 and head.stdout.strip():
        result["commit_ref"] = head.stdout.strip()
    if files.returncode == 0 and files.stdout.strip():
        result["files"] = ", ".join(line.strip() for line in files.stdout.splitlines() if line.strip())
    return result


def _production_change_log_payload(payload: dict, sid: str) -> dict:
    cmd = _extract_command(payload)
    tool_text = _extract_tool_text(payload)
    context = _git_head_change_context(_extract_workdir(payload))
    combined = "\n".join(part for part in (cmd, tool_text) if part)
    version_match = re.search(r"\bv?\d+\.\d+\.\d+(?:[-+][A-Za-z0-9_.-]+)?\b", combined)
    sha_match = re.search(r"\b[0-9a-f]{7,40}\b", combined, re.IGNORECASE)
    files = context.get("files") or "produccion: mutacion detectada por comando"
    commit_ref = context.get("commit_ref") or (sha_match.group(0)[:12] if sha_match else "")
    evidence = tool_text.strip()[:1000] if tool_text.strip() else "PostToolUse detecto comando de produccion; pendiente de evidencia adicional en task_close si aplica."
    what = "Cambio de produccion detectado automaticamente"
    if version_match:
        what += f" ({version_match.group(0)})"
    return {
        "session_id": sid,
        "files": files,
        "what_changed": what,
        "why": cmd[:500],
        "triggered_by": "PostToolUse automatic production mutation detector",
        "affects": "produccion",
        "risks": "registro automatico conservador; revisar si el comando fue un falso positivo",
        "verify": evidence,
        "commit_ref": commit_ref,
    }


def _queue_change_log_from_production_mutation(payload: dict, sid: str) -> bool:
    try:
        from mcp_write_queue import enqueue_write  # type: ignore
    except Exception:
        return False
    queued = enqueue_write(
        "change_log",
        _production_change_log_payload(payload, sid),
        priority="high",
    )
    return bool(queued.get("accepted"))


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_json(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + f".tmp-{os.getpid()}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _pending_trace_path(sid: str) -> Path:
    safe_sid = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in (sid or "unknown"))
    return _production_closeout_dir() / f"post-change-trace-{safe_sid}.json"


def _split_files(value: object) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, (list, tuple, set)):
        raw = "\n".join(str(item) for item in value)
    else:
        raw = str(value)
    parts = re.split(r"[\n,;]+", raw)
    return {part.strip() for part in parts if part and part.strip()}


def _record_post_change_trace(payload: dict, sid: str) -> None:
    if not sid:
        sid = "unknown"
    path = _pending_trace_path(sid)
    trace = _read_json(path) or {
        "sid": sid,
        "touched_files": [],
        "guard_files": [],
        "change_log_files": [],
        "production_mutation": False,
        "created_at": time.time(),
    }
    tool_name = _tool_name(payload)
    tool_input = _tool_input(payload)
    cmd = _extract_command(payload)

    touched = set(trace.get("touched_files") or [])
    guards = set(trace.get("guard_files") or [])
    logged = set(trace.get("change_log_files") or [])

    if _is_shared_mutation_payload(payload):
        touched.update(_split_files(tool_input.get("file_path")))
        touched.update(_split_files(tool_input.get("path")))
        touched.update(_split_files(tool_input.get("files")))
        touched.update(_split_files(tool_input.get("paths")))
        if cmd:
            trace["last_mutation_command"] = cmd[:500]
            if _is_production_mutation_command(cmd):
                trace["production_mutation"] = True

    if tool_name in {"nexo_guard_check", "mcp__nexo__nexo_guard_check"}:
        guards.update(_split_files(tool_input.get("files")))

    if _is_change_log_tool(tool_name):
        logged.update(_split_files(tool_input.get("files")))
        logged.update(_split_files(tool_input.get("files_changed")))
        if not logged and touched:
            logged.update(touched)

    if _is_task_close_tool(tool_name):
        touched.update(_split_files(tool_input.get("files_changed")))

    trace["touched_files"] = sorted(touched)
    trace["guard_files"] = sorted(guards)
    trace["change_log_files"] = sorted(logged)
    trace["updated_at"] = time.time()

    if touched or guards or logged or trace.get("production_mutation"):
        _write_json(path, trace)


def _missing_trace_items(payload: dict, sid: str) -> list[str]:
    if not _is_task_close_tool(_tool_name(payload)):
        return []
    trace = _read_json(_pending_trace_path(sid or "unknown"))
    if not trace:
        return []
    tool_input = _tool_input(payload)
    touched = set(trace.get("touched_files") or [])
    if not touched and not trace.get("production_mutation"):
        return []
    guards = set(trace.get("guard_files") or [])
    logged = set(trace.get("change_log_files") or [])
    closing_files = _split_files(tool_input.get("files_changed"))

    missing = []
    if touched and not guards:
        missing.append("guardias ejecutados")
    if trace.get("production_mutation") and not logged and not _task_close_payload_has_change_trace(payload):
        missing.append("registro de cambios")
    if touched and closing_files and not touched.issubset(closing_files):
        missing.append("files_changed completo")
    if touched and not closing_files:
        missing.append("files_changed")
    return missing


def check_post_change_trace_closeout(payload: dict, sid: str) -> str | None:
    if not sid:
        sid = "unknown"
    _record_post_change_trace(payload, sid)
    missing = _missing_trace_items(payload, sid)
    if not missing:
        if _is_task_close_tool(_tool_name(payload)):
            _pending_trace_path(sid).unlink(missing_ok=True)
        return None
    trace = _read_json(_pending_trace_path(sid))
    files = ", ".join((trace.get("touched_files") or [])[:6]) or "cambio detectado"
    message = (
        "Cierre bloqueado: antes de marcar completado hay que cuadrar archivos tocados, "
        f"guardias y registro de cambios. Falta: {', '.join(missing)}. "
        f"Archivos detectados: {files}."
    )
    return append_operator_language_contract(message)


def check_production_change_log_closeout(payload: dict, sid: str) -> str | None:
    if not sid:
        sid = "unknown"
    tool_name = _tool_name(payload)
    pending_path = _pending_change_log_path(sid)
    rotation_followup_id = _ensure_webroot_backup_rotation_followup(payload, sid)
    if _is_change_log_tool(tool_name):
        pending = _read_json(pending_path)
        if pending.get("requires_explicit_production_change_log") and not _change_log_has_production_release_refs(payload):
            message = (
                "Cierre pendiente: el cambio de release/tag/publicación requiere `nexo_change_log(...)` "
                "con `scope=production` y una referencia verificable a commit, tag o URL de release."
            )
            return append_operator_language_contract(message)
        pending_path.unlink(missing_ok=True)
        return None

    cmd = _extract_command(payload)
    if cmd and _is_production_mutation_command(cmd):
        is_release_publication = _is_release_publication_command(cmd)
        _write_json(
            pending_path,
            {
                "sid": sid,
                "command": cmd[:500],
                "tool_name": tool_name,
                "created_at": time.time(),
                "triggered_by": "PostToolUse production mutation detector",
                "requires_explicit_production_change_log": is_release_publication,
            },
        )
        if not is_release_publication and _queue_change_log_from_production_mutation(payload, sid):
            pending_path.unlink(missing_ok=True)
            return None

    pending = _read_json(pending_path)
    if not pending:
        return None

    if (
        _is_task_close_tool(tool_name)
        and not pending.get("requires_explicit_production_change_log")
        and _queue_change_log_from_task_close(payload, sid, pending)
    ):
        pending_path.unlink(missing_ok=True)
        return None

    if pending.get("requires_explicit_production_change_log"):
        message = (
            "Cierre pendiente: se detectó una publicación/tag/release. Antes de cerrar debe constar "
            "`nexo_change_log(...)` con `scope=production` y referencia a commit, tag o URL de release."
        )
    else:
        message = (
            "Cierre pendiente: se detectó una señal de despliegue/publicación de producción y todavía no consta "
            "`nexo_change_log(...)` ni un `nexo_task_close(...)` con archivos, motivo y verificación suficiente. "
            "Registra el cambio antes de declarar la tarea cerrada."
        )
    if rotation_followup_id:
        message += f" Además, el canary webroot creó el followup de rotación {rotation_followup_id}."
    return append_operator_language_contract(message)


def _domain_error_cascade_path(sid: str) -> Path:
    safe_sid = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in (sid or "unknown"))
    return _production_closeout_dir() / f"domain-error-cascade-{safe_sid}.json"


def _payload_error_text(payload: dict) -> str:
    parts = [_extract_command(payload), _extract_tool_text(payload)]
    for key in ("stderr", "stdout", "output", "tool_response"):
        value = payload.get(key)
        if isinstance(value, str):
            parts.append(value)
    for key in ("exit_code", "returncode", "status"):
        value = payload.get(key)
        if value not in (None, "", 0, "0", "success", "ok"):
            parts.append(f"{key}={value}")
    return "\n".join(part for part in parts if part)


def _detect_error_domain(payload: dict) -> str:
    text = _payload_error_text(payload)
    if not text:
        return ""
    if not re.search(
        r"\b(error|failed|failure|traceback|exception|timeout|denied|quota|resource_exhausted|429|5\d\d|could not|no se pudo|fall[oó])\b",
        text,
        re.IGNORECASE,
    ):
        return ""
    domain_patterns = (
        ("gcloud", r"\b(gcloud|cloudbuild|cloud\s+build|cloud\s+run|cloud\s+sql|googleapis|resource_exhausted)\b"),
        ("credits", r"\b(credits?|billing|stripe|saldo|recarga|quota|coste|cost)\b"),
        ("imap", r"\b(imap|mailbox|email|correo|mxroute|smtp)\b"),
        ("recovery", r"\b(recovery|carrito|abandon|checkout|whatsappqueue|queuedrain)\b"),
        ("cloud", r"\b(cloud|dns|cloudflare|secret\s+manager|secretmanager|cloud\s+storage)\b"),
    )
    for domain, pattern in domain_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return domain
    return "general"


def check_domain_error_cascade(payload: dict, sid: str, now: float | None = None) -> str | None:
    domain = _detect_error_domain(payload)
    if not domain:
        return None
    current = float(now) if now is not None else time.time()
    path = _domain_error_cascade_path(sid or "unknown")
    state = _read_json(path) or {"sid": sid or "unknown", "domains": {}}
    domains = state.setdefault("domains", {})
    events = [
        item for item in domains.get(domain, [])
        if isinstance(item, dict) and current - float(item.get("ts") or 0) <= 1800
    ]
    events.append({"ts": current, "tool": _tool_name(payload), "command": _extract_command(payload)[:240]})
    domains[domain] = events[-5:]
    _write_json(path, state)
    if len(events) < 2:
        return None
    last_prompt = float(state.get("last_prompted", {}).get(domain, 0) if isinstance(state.get("last_prompted"), dict) else 0)
    if current - last_prompt < 900:
        return None
    prompted = state.setdefault("last_prompted", {})
    prompted[domain] = current
    _write_json(path, state)
    message = (
        f"Cascada detectada en `{domain}`: van {len(events)} errores del mismo dominio en la ventana reciente. "
        "Antes de seguir parcheando en serie, abre subagentes en paralelo con piezas independientes: "
        "1) causa raíz/logs, 2) credenciales/cuotas/configuración, 3) fix mínimo + prueba de cierre. "
        "Cada subagente debe recibir alcance acotado y parar si no puede verificar."
    )
    return append_operator_language_contract(message)


_SUPPORT_TICKET_TOOLS = {
    "nexo_support_ticket_create",
    "mcp__nexo__nexo_support_ticket_create",
    "nexo_support_ticket_message",
    "mcp__nexo__nexo_support_ticket_message",
}


def _support_ticket_failure_domain(payload: dict) -> str:
    if _tool_name(payload) not in _SUPPORT_TICKET_TOOLS:
        return ""
    try:
        text = json.dumps(_tool_input(payload), ensure_ascii=False)
    except Exception:
        text = str(_tool_input(payload) or "")
    combined = "\n".join(part for part in (text, _extract_tool_text(payload)) if part)
    if not combined:
        return ""
    domain_patterns = (
        ("cloud", r"\b(cloud|gcloud|cloudflare|dns|provision(?:ing)?|secret\s*manager|secretmanager|cloud\s*run|cloud\s*sql)\b"),
        ("credits", r"\b(credits?|cr[eé]ditos?|billing|saldo|stripe|checkout|portal|quota|cuota|coste|cost)\b"),
        ("voice", r"\b(voice|voz|vapi|elevenlabs|audio|tts|stt)\b"),
        ("image", r"\b(image|imagen|imagenes|im[aá]genes|fal|replicate|gpt-image|render)\b"),
        ("provisioning", r"\b(provision(?:ing)?|alta|tenant|workspace|account|cuenta|api\s*key|scope|token)\b"),
    )
    for domain, pattern in domain_patterns:
        if re.search(pattern, combined, re.IGNORECASE):
            return domain
    return ""


def _support_ticket_client_key(payload: dict) -> str:
    tool_input = _tool_input(payload)
    for key in ("client_message_id", "ticket_id", "customer_id", "account_id", "shop_id", "tenant_id"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:160]
    subject = str(tool_input.get("subject") or tool_input.get("title") or "").strip()
    message = str(tool_input.get("message") or tool_input.get("body") or "").strip()
    seed = (subject or message or _extract_tool_text(payload) or _extract_command(payload)).strip()
    return seed[:160]


def _support_ticket_cascade_path(sid: str) -> Path:
    safe_sid = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in (sid or "unknown"))
    return _production_closeout_dir() / f"support-ticket-cascade-{safe_sid}.json"


def check_support_ticket_second_failure_sweep(payload: dict, sid: str, now: float | None = None) -> str | None:
    domain = _support_ticket_failure_domain(payload)
    if not domain:
        return None
    current = float(now) if now is not None else time.time()
    path = _support_ticket_cascade_path(sid or "unknown")
    state = _read_json(path) or {"sid": sid or "unknown", "domains": {}}
    domains = state.setdefault("domains", {})
    events = [
        item for item in domains.get(domain, [])
        if isinstance(item, dict) and current - float(item.get("ts") or 0) <= 72 * 3600
    ]
    client_key = _support_ticket_client_key(payload)
    events.append(
        {
            "ts": current,
            "tool": _tool_name(payload),
            "client": client_key,
            "summary": str(_tool_input(payload).get("subject") or _tool_input(payload).get("title") or "")[:240],
        }
    )
    domains[domain] = events[-20:]
    _write_json(path, state)
    distinct_clients = {str(item.get("client") or "").strip() for item in events if str(item.get("client") or "").strip()}
    if len(events) < 2 or len(distinct_clients) < 2:
        return None
    prompted = state.setdefault("last_prompted", {})
    last_prompt = float(prompted.get(domain, 0) if isinstance(prompted, dict) else 0)
    if last_prompt and current - last_prompt < 6 * 3600:
        return None
    prompted[domain] = current
    _write_json(path, state)
    message = (
        f"Segundo fallo de cliente en `{domain}` detectado dentro de 72h. "
        "Antes de responder el siguiente ticket, activa `SK-SUPPORT-SECOND-TICKET-PARALLEL-SWEEP` "
        "y lanza 2-3 subagentes en paralelo sobre el flujo completo: idempotencia/reservas, "
        "scope tokens/configuración, errores cacheados/logs y smoke final. Cierra el P1 en la misma tanda."
    )
    return append_operator_language_contract(message)


_SHARED_MUTATION_TOOLS = {
    "Edit",
    "Write",
    "MultiEdit",
    "NotebookEdit",
    "apply_patch",
    "functions.apply_patch",
}
_SHARED_PATH_RE = re.compile(
    r"("
    r"/Users/[^ \n\r\t'\"]+/Documents/_PhpstormProjects/|"
    r"/Users/[^ \n\r\t'\"]+/.nexo/core/|"
    r"/home/nexodesk/|"
    r"/var/www/|"
    r"/public_html/|"
    r"/httpdocs/"
    r")",
    re.IGNORECASE,
)
_SCOPE_REQUIRED_MARKERS = {
    "conversation": re.compile(r"\b(conversation|conversaci[oó]n|hilo|thread|email|ticket|mensaje|message|n/a)\b", re.IGNORECASE),
    "tenant": re.compile(r"\b(tenant|tienda|shop|cuenta|account|cliente|client|n/a)\b", re.IGNORECASE),
    "language": re.compile(r"\b(idioma|language|lang|locale|es|en|de|fr|pt|it|ca|n/a)\b", re.IGNORECASE),
    "environment": re.compile(r"\b(entorno|environment|local|runtime|producto|producci[oó]n|production|prod|staging)\b", re.IGNORECASE),
    "surface": re.compile(r"\b(superficie|surface|api|ui|dominio|domain|web|public)\b", re.IGNORECASE),
    "deploy": re.compile(r"\b(deploy|despliegue|publicado|published|release|rama|branch|n/a)\b", re.IGNORECASE),
}


def _payload_scope_text(payload: dict) -> str:
    try:
        return json.dumps(_tool_input(payload), ensure_ascii=False)
    except Exception:
        return str(_tool_input(payload) or "")


def _is_shared_mutation_payload(payload: dict) -> bool:
    tool_name = _tool_name(payload)
    cmd = _extract_command(payload)
    input_text = _payload_scope_text(payload)
    combined = "\n".join(part for part in (tool_name, cmd, input_text) if part)
    if tool_name in _SHARED_MUTATION_TOOLS and _SHARED_PATH_RE.search(combined):
        return True
    if cmd and (_is_production_mutation_command(cmd) or _SHARED_PATH_RE.search(cmd)):
        return True
    return False


def check_shared_scope_closeout(payload: dict) -> str | None:
    if not _is_shared_mutation_payload(payload):
        return None
    scope_text = _payload_scope_text(payload)
    missing = [
        label
        for label, pattern in _SCOPE_REQUIRED_MARKERS.items()
        if not pattern.search(scope_text)
    ]
    if not missing:
        return None
    message = (
        "Antes de seguir con este cambio compartido, deja fijado el alcance operativo: "
        "conversación afectada, tenant/tienda, idiomas, entorno, superficie pública y estado de deploy. "
        "Si algún campo no aplica, márcalo como N/A y continúa con evidencia."
    )
    return append_operator_language_contract(message)


def _run_auto_capture(payload: dict) -> int:
    """Pipe the tool result into auto_capture for post-output classification."""
    text = _extract_tool_text(payload)
    if not text or len(text) < 15:
        return 0
    try:
        proc = subprocess.run(
            ["python3", str(_DIR / "auto_capture.py")],
            input=text,
            capture_output=True,
            text=True,
            timeout=4,
        )
        return proc.returncode
    except Exception:
        return 1


def _run_post_edit_change_log(payload: dict) -> int:
    """Record write-tool visibility without calling MCP from the hook."""
    try:
        proc = subprocess.run(
            ["python3", str(_DIR / "post_edit_change_log.py")],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=5,
        )
        return proc.returncode
    except Exception:
        return 1


def main() -> int:
    started = time.time()
    payload = _read_stdin_json()

    steps = [
        (["bash", str(_DIR / "capture-tool-logs.sh")], 5),
        (["bash", str(_DIR / "capture-session.sh")],   3),
        (["bash", str(_DIR / "inbox-hook.sh")],        5),
        (["bash", str(_DIR / "protocol-guardrail.sh")],5),
        (["bash", str(_DIR / "heartbeat-posttool.sh")],3),
    ]
    exits = []
    protocol_message = ""
    for cmd, timeout in steps:
        script = Path(cmd[-1])
        if not script.is_file():
            continue
        exit_code, stdout = _run_step(cmd, timeout)
        exits.append(exit_code)
        if script.name == "protocol-guardrail.sh" and stdout:
            protocol_message = stdout

    exits.append(_run_auto_capture(payload))
    exits.append(_run_post_edit_change_log(payload))

    # v6.0.1 — inbox autodetect runs LAST so it sees the latest DB state
    # (including any writes the previous steps may have done). Emits a
    # single-line JSON systemMessage so Claude Code surfaces it to the
    # agent without breaking the tool pipeline.
    # v7.2.0 — G1 enforcer (response_contract.mode physical gate) plugs in
    # alongside the inbox reminder. Shadow mode only records a debt row;
    # hard mode appends a nudge to the systemMessage.
    try:
        sid = _resolve_sid_from_payload(payload)
        reminder = check_inbox_and_emit_reminder(sid)
        auto_guard_message = _queue_auto_guard_check(payload, sid)
        change_log_message = check_production_change_log_closeout(payload, sid)
        learning_capture_message = check_learning_capture_closeout(payload, sid)
        post_change_trace_message = check_post_change_trace_closeout(payload, sid)
        shared_scope_message = check_shared_scope_closeout(payload)
        cascade_message = check_domain_error_cascade(payload, sid)
        support_second_ticket_message = check_support_ticket_second_failure_sweep(payload, sid)
        g1_message: str | None = None
        try:
            from g1_enforcer import check_response_contract_gate  # type: ignore
            g1_message = check_response_contract_gate(sid)
        except Exception:
            g1_message = None
        combined = _combine_system_messages(
            protocol_message,
            reminder,
            auto_guard_message,
            change_log_message,
            learning_capture_message,
            post_change_trace_message,
            shared_scope_message,
            cascade_message,
            support_second_ticket_message,
            g1_message,
        )
        if combined:
            print(json.dumps({"systemMessage": combined}))
    except Exception:
        pass

    final_exit = max(exits) if exits else 0
    duration_ms = int((time.time() - started) * 1000)
    _record(duration_ms, final_exit, f"steps={len(exits)}")
    return 0  # never block the tool pipeline


if __name__ == "__main__":
    sys.exit(main())
