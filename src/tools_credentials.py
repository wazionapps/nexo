"""Credentials CRUD tools: get, create, update, delete, list."""

from db import create_credential, update_credential, delete_credential, get_credential, list_credentials


def handle_credential_get(service: str, key: str = '') -> str:
    """Retrieve credential(s) including their values. Use for reading secrets."""
    results = get_credential(service, key if key else None)
    if not results:
        target = f"{service}/{key}" if key else service
        return f"ERROR: No se encontraron credenciales para '{target}'."
    is_fuzzy = any(r.get("_fuzzy") for r in results)
    lines = []
    if is_fuzzy:
        lines.append(f"⚠ No existe servicio '{service}' exacto. Resultados similares ({len(results)}):")
        lines.append("")
    for r in results:
        lines.append(f"CREDENCIAL {r['service']}/{r['key']}:")
        lines.append(f"  Valor: {r['value']}")
        notes = r.get("notes") or ""
        lines.append(f"  Notas: {notes if notes else '—'}")
    return "\n".join(lines)


def handle_credential_create(service: str, key: str, value: str, notes: str = '') -> str:
    """Create a new credential entry."""
    result = create_credential(service, key, value, notes)
    if "error" in result:
        return f"ERROR: {result['error']}"
    return f"Credencial {service}/{key} creada."


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
    return f"Credencial {service}/{key} actualizada."


def handle_credential_delete(service: str, key: str = '') -> str:
    """Delete a credential or all credentials for a service."""
    deleted = delete_credential(service, key if key else None)
    if not deleted:
        target = f"{service}/{key}" if key else service
        return f"ERROR: No se encontraron credenciales para '{target}'."
    if key:
        return f"Credential deleted."
    return f"All credentials for service deleted."


def handle_credential_list(service: str = '') -> str:
    """List credential service/key names and notes — values are never shown."""
    results = list_credentials(service if service else None)
    label = service if service else "TODAS"
    if not results:
        return f"CREDENCIALES {label.upper()}: Sin entradas."
    lines = [f"CREDENCIALES {label.upper()} ({len(results)}):"]
    for r in results:
        notes = r.get("notes") or ""
        suffix = f" — {notes}" if notes else ""
        lines.append(f"  {r['service']}/{r['key']}{suffix}")
    return "\n".join(lines)
