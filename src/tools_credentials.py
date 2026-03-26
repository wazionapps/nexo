"""Credentials CRUD tools: get, create, update, delete, list."""

from db import create_credential, update_credential, delete_credential, get_credential, list_credentials


def handle_credential_get(service: str, key: str = '') -> str:
    """Retrieve credential(s) including their values. Use for reading secrets."""
    results = get_credential(service, key if key else None)
    if not results:
        target = f"{service}/{key}" if key else service
        return f"ERROR: No credentials found for '{target}'."
    is_fuzzy = any(r.get("_fuzzy") for r in results)
    lines = []
    if is_fuzzy:
        lines.append(f"No exact match for '{service}'. Similar results ({len(results)}):")
        lines.append("")
    for r in results:
        lines.append(f"CREDENTIAL {r['service']}/{r['key']}:")
        lines.append(f"  Value: {r['value']}")
        notes = r.get("notes") or ""
        lines.append(f"  Notes: {notes if notes else '—'}")
    return "\n".join(lines)


def handle_credential_create(service: str, key: str, value: str, notes: str = '') -> str:
    """Create a new credential entry."""
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
        return f"Credential {service}/{key} deleted."
    return f"All credentials for {service} deleted."


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
