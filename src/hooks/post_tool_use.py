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


def _extract_command(payload: dict) -> str:
    tool_input = _tool_input(payload)
    for key in ("command", "cmd", "script"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _is_production_mutation_command(cmd: str) -> bool:
    patterns = (
        r"\bgit\s+push\b(?!.*--dry-run)(?=.*\b(?:origin\s+)?(?:main|master|stable|release)\b)",
        r"\bgcloud\s+builds\s+submit\b",
        r"\bgcloud\s+builds\s+triggers\s+run\b",
        r"\bgcloud\s+run\s+(?:deploy|services\s+update|jobs\s+deploy|jobs\s+update)\b",
        r"\bgcloud\s+dns\s+record-sets\s+transaction\s+execute\b",
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


def _task_close_payload_has_change_trace(payload: dict) -> bool:
    tool_input = _tool_input(payload)
    files = str(tool_input.get("files_changed") or "").strip()
    what = str(tool_input.get("change_summary") or tool_input.get("summary") or "").strip()
    why = str(tool_input.get("change_why") or tool_input.get("triggered_by") or "").strip()
    return bool(files and what and why)


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


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_json(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + f".tmp-{os.getpid()}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def check_production_change_log_closeout(payload: dict, sid: str) -> str | None:
    if not sid:
        sid = "unknown"
    tool_name = _tool_name(payload)
    pending_path = _pending_change_log_path(sid)
    rotation_followup_id = _ensure_webroot_backup_rotation_followup(payload, sid)
    if _is_change_log_tool(tool_name):
        pending_path.unlink(missing_ok=True)
        return None

    cmd = _extract_command(payload)
    if cmd and _is_production_mutation_command(cmd):
        _write_json(
            pending_path,
            {
                "sid": sid,
                "command": cmd[:500],
                "tool_name": tool_name,
                "created_at": time.time(),
                "triggered_by": "PostToolUse production mutation detector",
            },
        )

    pending = _read_json(pending_path)
    if not pending:
        return None

    if _is_task_close_tool(tool_name) and _queue_change_log_from_task_close(payload, sid, pending):
        pending_path.unlink(missing_ok=True)
        return None

    message = (
        "Cierre pendiente: se detectó una señal de despliegue/publicación de producción y todavía no consta "
        "`nexo_change_log(...)` ni un `nexo_task_close(...)` con archivos, motivo y verificación suficiente. "
        "Registra el cambio antes de declarar la tarea cerrada."
    )
    if rotation_followup_id:
        message += f" Además, el canary webroot creó el followup de rotación {rotation_followup_id}."
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
        change_log_message = check_production_change_log_closeout(payload, sid)
        g1_message: str | None = None
        try:
            from g1_enforcer import check_response_contract_gate  # type: ignore
            g1_message = check_response_contract_gate(sid)
        except Exception:
            g1_message = None
        combined = _combine_system_messages(protocol_message, reminder, change_log_message, g1_message)
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
