"""Credentials CRUD tools: get, create, update, delete, list."""

from db import create_credential, update_credential, delete_credential, get_credential, list_credentials, get_db


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


def handle_credential_get(service: str, key: str = '') -> str:
    """Retrieve credential(s) including their values. Use for reading secrets."""
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
    """
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
    """Update the value and/or notes of an existing credential."""
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
    """Delete a credential or all credentials for a service."""
    deleted = delete_credential(service, key if key else None)
    if not deleted:
        target = f"{service}/{key}" if key else service
        return f"ERROR: No credentials found for '{target}'."
    if key:
        return f"Credential deleted."
    return f"All credentials for service deleted."


def handle_credential_list(service: str = '') -> str:
    """List credential service/key names and notes — values are never shown."""
    results = list_credentials(service if service else None)
    label = service if service else "ALL"
    if not results:
        return f"CREDENTIALS {label.upper()}: No entries."
    lines = [f"CREDENTIALS {label.upper()} ({len(results)}):"]
    for r in results:
        notes = r.get("notes") or ""
        suffix = f" — {notes}" if notes else ""
        lines.append(f"  {r['service']}/{r['key']}{suffix}")
    return "\n".join(lines)
