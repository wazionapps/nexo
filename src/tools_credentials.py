"""Credentials CRUD tools: get, create, update, delete, list.

Two storage backends live behind these handlers:

  1) The default SQLite credentials table (DB). Used by the agent for any
     internally-managed secret (Anthropic platform key, Stripe keys, etc.).

  2) The BYOK filesystem store at ``~/.nexo/credentials/byok/{slug}.json``.
     Used for the user's own API keys connected from NEXO Desktop's
     "Connections" settings tab. Keys NEVER cross to the NEXO backend and
     never go through the DB — they live only on the user's machine.

When ``service='byok'`` the handlers transparently route to the filesystem
backend (get / list / delete). Create is intentionally NOT routed: BYOK
keys are written through Desktop's UI, which performs remote validation
before persisting. The agent should never mint a BYOK entry on its own.
"""

import json
import os
import re
from pathlib import Path

from db import create_credential, update_credential, delete_credential, get_credential, list_credentials, get_db


BYOK_SERVICE = "byok"
REDACTED_SECRET_NOTE = "[redacted: secret-like note]"
SECRET_NOTE_PATTERNS = (
    re.compile(r"\b(?:npm|ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{16,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    re.compile(r"\b(?:api[_-]?key|secret|token|password|passwd|pwd)\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}\b", re.IGNORECASE),
    re.compile(r"\b[A-Za-z0-9+/=_-]{40,}\b"),
)


def credential_note_has_secret(notes: str) -> bool:
    """Detect notes that appear to contain a credential value."""
    clean = (notes or "").strip()
    if not clean:
        return False
    return any(pattern.search(clean) for pattern in SECRET_NOTE_PATTERNS)


def redact_credential_notes(notes: str) -> str:
    """Return notes safe for list/dashboard surfaces."""
    clean = notes or ""
    if credential_note_has_secret(clean):
        return REDACTED_SECRET_NOTE
    return clean


def public_credential_records(service: str = "") -> list[dict]:
    """List credential metadata from DB and BYOK without leaking values."""
    requested = (service or "").strip()
    records: list[dict] = []

    if requested != BYOK_SERVICE:
        for row in list_credentials(requested if requested else None):
            records.append(
                {
                    "service": row.get("service", ""),
                    "key": row.get("key", ""),
                    "notes": redact_credential_notes(row.get("notes") or ""),
                    "created_at": row.get("created_at", ""),
                    "updated_at": row.get("updated_at", ""),
                    "backend": "db",
                }
            )

    if requested in ("", BYOK_SERVICE):
        for row in _byok_get(""):
            records.append(
                {
                    "service": row.get("service", BYOK_SERVICE),
                    "key": row.get("key", ""),
                    "notes": redact_credential_notes(row.get("notes") or ""),
                    "created_at": "",
                    "updated_at": "",
                    "backend": "byok_local",
                }
            )

    return records


def _credential_exists(service: str, key: str) -> bool:
    """Fase 2 R02 helper — exact (service, key) match against active credentials.

    The credentials table already enforces uniqueness via a PRIMARY/UNIQUE key
    and create_credential returns an IntegrityError-wrapped dict when the
    collision is hit. This helper lets the handler surface the R02 message
    BEFORE the DB round-trip, with a more informative error structure that
    matches the rest of the Fase 2 dedup family (R01, R05, R09).
    """
    conn = get_db()
    row = conn.execute(
        "SELECT 1 FROM credentials WHERE service = ? AND key = ? LIMIT 1",
        (service, key),
    ).fetchone()
    return row is not None


def _byok_base_dir() -> Path:
    home = os.environ.get("NEXO_HOME") or str(Path.home() / ".nexo")
    return Path(home) / "credentials" / "byok"


def _safe_byok_slug(raw: str) -> str:
    cleaned = (raw or "").strip().lower()
    return "".join(ch for ch in cleaned if ch.isalnum() or ch in "-_")


def _byok_file_for(key: str) -> Path | None:
    safe = _safe_byok_slug(key)
    if not safe:
        return None
    return _byok_base_dir() / f"{safe}.json"


def _byok_read_file(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _byok_entry_from_file(slug: str, data: dict) -> dict:
    """Shape a BYOK file into the {service, key, value, notes} schema the
    handlers expect — same as DB rows. ``value`` carries the actual API key.
    """
    provider = data.get("provider") or slug
    label = data.get("label") or ""
    validation = data.get("validation_status") or "unknown"
    connected_at = data.get("connected_at") or ""
    last_validated = data.get("last_validated_at") or ""
    note_parts = [
        f"provider={provider}",
        f"label={label}" if label else "",
        f"validation={validation}",
        f"connected_at={connected_at}" if connected_at else "",
        f"last_validated_at={last_validated}" if last_validated else "",
    ]
    notes = " | ".join(p for p in note_parts if p)
    return {
        "service": BYOK_SERVICE,
        "key": slug,
        "value": str(data.get("api_key") or ""),
        "notes": notes,
    }


def _byok_get(key: str = "") -> list[dict]:
    """Filesystem-backed lookup for BYOK keys. Returns list of dicts in the
    same shape as get_credential() so the handler can render them uniformly.
    """
    base = _byok_base_dir()
    if not base.is_dir():
        return []

    if key:
        path = _byok_file_for(key)
        if not path or not path.is_file():
            return []
        data = _byok_read_file(path)
        if data is None:
            return []
        return [_byok_entry_from_file(_safe_byok_slug(key), data)]

    out: list[dict] = []
    for path in sorted(base.glob("*.json")):
        data = _byok_read_file(path)
        if data is None:
            continue
        out.append(_byok_entry_from_file(path.stem, data))
    return out


def _byok_delete(key: str = "") -> int:
    """Filesystem-backed delete. Returns the number of files removed."""
    base = _byok_base_dir()
    if not base.is_dir():
        return 0
    if key:
        path = _byok_file_for(key)
        if not path or not path.is_file():
            return 0
        try:
            path.unlink()
            return 1
        except Exception:
            return 0
    removed = 0
    for path in base.glob("*.json"):
        try:
            path.unlink()
            removed += 1
        except Exception:
            continue
    return removed


def handle_credential_get(service: str, key: str = '') -> str:
    """Retrieve credential(s) including their values. Use for reading secrets.

    When ``service='byok'`` the values are read from the local filesystem
    store written by NEXO Desktop's Settings > Connections UI (the user's
    own provider API keys, e.g. OpenAI/Anthropic/Gemini/ElevenLabs).
    """
    if service == BYOK_SERVICE:
        results = _byok_get(key)
    else:
        results = get_credential(service, key if key else None)
    if not results:
        target = f"{service}/{key}" if key else service
        return f"ERROR: No credentials found for '{target}'."
    is_fuzzy = any(r.get("_fuzzy") for r in results)
    lines = []
    if is_fuzzy:
        lines.append(f"⚠ No exact match for '{service}'. Similar results ({len(results)}):")
        lines.append("")
    for r in results:
        lines.append(f"CREDENTIAL {r['service']}/{r['key']}:")
        lines.append(f"  Value: {r['value']}")
        notes = r.get("notes") or ""
        lines.append(f"  Notes: {notes if notes else '—'}")
    return "\n".join(lines)


def handle_credential_create(service: str, key: str, value: str, notes: str = '', force: str = '') -> str:
    """Create a new credential entry.

    Args:
        service: Service identifier (e.g., 'meta', 'stripe', 'anthropic').
        key: Credential key within the service (e.g., 'api_key', 'token_live').
        value: The secret value.
        notes: Free-form operational notes — never include the value.
        force: Set to '1'/'true' to OVERWRITE an existing (service, key) pair.
               Without force, Fase 2 R02 rejects duplicates and points at
               nexo_credential_update as the canonical edit path.

    BYOK keys (service='byok') are intentionally NOT mintable through this
    tool: they must be added via NEXO Desktop's Settings > Connections, which
    validates the key against the provider before saving. The agent should
    not silently inject BYOK credentials.
    """
    if service == BYOK_SERVICE:
        return (
            "ERROR: BYOK credentials cannot be created through this tool. "
            "Ask the user to open NEXO Desktop > Settings > Connections and "
            "connect the provider there (the UI validates the key with the "
            "provider before saving)."
        )
    if credential_note_has_secret(notes):
        return (
            "ERROR: Credential notes look like they contain a secret. "
            "Put the secret in value, and keep notes operational only."
        )
    # ── R02 (Fase 2 Protocol Enforcer): reject exact (service, key) duplicates ──
    force_flag = str(force or "").strip().lower() in {"1", "true", "yes", "on"}
    if not force_flag and _credential_exists(service, key):
        return (
            f"ERROR: Credential {service}/{key} already exists (R02). "
            f"Use nexo_credential_update to modify the value/notes, "
            f"nexo_credential_delete to remove it, or pass force='true' to "
            f"overwrite (last resort — prefer update for auditability)."
        )
    if force_flag and _credential_exists(service, key):
        # force path — delete the old row then re-insert so updated_at,
        # notes and value land in a single audit trail entry.
        delete_credential(service, key)
    result = create_credential(service, key, value, notes)
    if "error" in result:
        return f"ERROR: {result['error']}"
    return f"Credential {service}/{key} created."


def handle_credential_update(service: str, key: str, value: str = '', notes: str = '') -> str:
    """Update the value and/or notes of an existing credential.

    BYOK entries are not editable from the agent side; users update them by
    re-connecting the provider from Settings > Connections in Desktop.
    """
    if service == BYOK_SERVICE:
        return (
            "ERROR: BYOK credentials are not editable from the agent. "
            "Ask the user to update the connection in NEXO Desktop > Settings > Connections."
        )
    if credential_note_has_secret(notes):
        return (
            "ERROR: Credential notes look like they contain a secret. "
            "Put the secret in value, and keep notes operational only."
        )
    result = update_credential(
        service,
        key,
        value if value else None,
        notes if notes else None,
    )
    if "error" in result:
        return f"ERROR: {result['error']}"
    return f"Credential {service}/{key} updated."


def handle_credential_delete(service: str, key: str = '') -> str:
    """Delete a credential or all credentials for a service.

    For ``service='byok'`` the delete reaches the filesystem store; the file
    is removed but the provider account on the user's side is untouched.
    """
    if service == BYOK_SERVICE:
        removed = _byok_delete(key if key else None)
        if removed == 0:
            target = f"{service}/{key}" if key else service
            return f"ERROR: No credentials found for '{target}'."
        if key:
            return f"Credential deleted."
        return f"All BYOK credentials deleted ({removed} files)."

    deleted = delete_credential(service, key if key else None)
    if not deleted:
        target = f"{service}/{key}" if key else service
        return f"ERROR: No credentials found for '{target}'."
    if key:
        return f"Credential deleted."
    return f"All credentials for service deleted."


def handle_credential_list(service: str = '') -> str:
    """List credential service/key names and notes — values are never shown.

    Listing without ``service`` only returns DB entries (the historical
    behaviour). Pass ``service='byok'`` to list the BYOK filesystem store.
    """
    results = public_credential_records(service)
    label = service if service else "ALL"
    if not results:
        return f"CREDENTIALS {label.upper()}: No entries."
    lines = [f"CREDENTIALS {label.upper()} ({len(results)}):"]
    for r in results:
        notes = r.get("notes") or ""
        suffix = f" — {notes}" if notes else ""
        backend = r.get("backend") or "db"
        lines.append(f"  {r['service']}/{r['key']} ({backend}){suffix}")
    return "\n".join(lines)
