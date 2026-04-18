"""tools_guardian — MCP writer for Guardian runtime overrides.

Fase 2 spec item 0.17. The reader side of the override file lives in
``guardian_config.rule_mode`` (reads ``~/.nexo/config/guardian-runtime-
overrides.json`` on every turn with TTL checks). This module adds the
*writer* side as an MCP tool so an operator or automation can bump a
rule to shadow / soft / hard / off for a bounded window without editing
JSON by hand.

Safety rails (Fase 2 spec 0.19 + 0.5):

  - CORE_RULES (R13, R14, R16, R25, R30) reject ``mode="off"``. The
    writer enforces the same invariant the reader does — defence in
    depth.
  - Only the four canonical modes are accepted: ``off``, ``shadow``,
    ``soft``, ``hard``.
  - TTL must be ``1h``, ``24h``, or ``session``. ``session`` is encoded
    as a best-effort 12h window so the override never lingers forever
    if the process dies.
  - All writes are atomic (tmp + rename) and best-effort logged to
    ``~/.nexo/logs/guardian-overrides.log`` as NDJSON.
  - Fail-closed: invalid args raise ``GuardianOverrideError`` which the
    MCP server surfaces as an error string (Rule #249).
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

from guardian_config import CORE_RULES, VALID_MODES


class GuardianOverrideError(ValueError):
    """Structured error surfaced to the MCP tool caller."""


VALID_TTLS = {"1h": 3600, "24h": 86400, "session": 12 * 3600}


def _override_path() -> Path:
    nexo_home = os.environ.get("NEXO_HOME") or str(Path.home() / ".nexo")
    return Path(nexo_home) / "config" / "guardian-runtime-overrides.json"


def _log_path() -> Path:
    nexo_home = os.environ.get("NEXO_HOME") or str(Path.home() / ".nexo")
    return Path(nexo_home) / "logs" / "guardian-overrides.log"


def _load_existing(path: Path) -> dict:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        # Corrupt override file — treat as empty. A writer should not be
        # the one to fail catastrophically here.
        return {}
    return data if isinstance(data, dict) else {}


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".guardian-override-", suffix=".json")
    os.close(fd)
    try:
        Path(tmp).write_text(
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, path)
    finally:
        try:
            if Path(tmp).exists():
                os.unlink(tmp)
        except OSError:
            pass


def _append_log(event: dict) -> None:
    path = _log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError:
        # Log failure is not fatal — never re-enter the failure path from a
        # Guardian writer.
        pass


def set_guardian_rule_override(rule_id: str, mode: str, ttl: str) -> dict:
    rule_id = (rule_id or "").strip()
    mode = (mode or "").strip().lower()
    ttl = (ttl or "").strip().lower()

    if not rule_id:
        raise GuardianOverrideError("rule_id is required")
    if mode not in VALID_MODES:
        raise GuardianOverrideError(
            f"invalid mode {mode!r}; expected one of {sorted(VALID_MODES)}"
        )
    if rule_id in CORE_RULES and mode == "off":
        raise GuardianOverrideError(
            f"core rule {rule_id!r} cannot be set to 'off' "
            f"(core set: {sorted(CORE_RULES)})"
        )
    if ttl not in VALID_TTLS:
        raise GuardianOverrideError(
            f"invalid ttl {ttl!r}; expected one of {sorted(VALID_TTLS)}"
        )

    path = _override_path()
    data = _load_existing(path)
    now = time.time()
    entry = {
        "mode": mode,
        "set_at": now,
        "ttl_label": ttl,
        "expires_at": now + VALID_TTLS[ttl],
    }
    data[rule_id] = entry
    _atomic_write(path, data)

    _append_log({
        "ts": now,
        "event": "override_set",
        "rule_id": rule_id,
        "mode": mode,
        "ttl_label": ttl,
        "expires_at": entry["expires_at"],
        "path": str(path),
    })
    return {"ok": True, "rule_id": rule_id, **entry, "path": str(path)}


def clear_guardian_rule_override(rule_id: str) -> dict:
    rule_id = (rule_id or "").strip()
    if not rule_id:
        raise GuardianOverrideError("rule_id is required")
    path = _override_path()
    data = _load_existing(path)
    if rule_id not in data:
        return {"ok": True, "rule_id": rule_id, "cleared": False, "path": str(path)}
    data.pop(rule_id, None)
    _atomic_write(path, data)
    _append_log({
        "ts": time.time(),
        "event": "override_clear",
        "rule_id": rule_id,
        "path": str(path),
    })
    return {"ok": True, "rule_id": rule_id, "cleared": True, "path": str(path)}


def handle_guardian_rule_override(rule_id: str = "", mode: str = "", ttl: str = "") -> str:
    """MCP handler for ``nexo_guardian_rule_override``.

    - ``mode == ""`` with ``rule_id`` set → clear the override (idempotent).
    - Otherwise set/replace the override for ``rule_id``.
    """
    try:
        if rule_id and mode == "":
            result = clear_guardian_rule_override(rule_id)
        else:
            result = set_guardian_rule_override(rule_id, mode, ttl)
    except GuardianOverrideError as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
    return json.dumps(result, ensure_ascii=False)


__all__ = [
    "GuardianOverrideError",
    "VALID_TTLS",
    "handle_guardian_rule_override",
    "set_guardian_rule_override",
    "clear_guardian_rule_override",
]
