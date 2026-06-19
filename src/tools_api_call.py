"""HTTP API calls to the nexo-desktop-web backend using the user's session bearer.

Exposes MCP tools registered in server.py:
  - nexo_api_call(method, path, body_json, idempotency_key, headers_json, base_url)
  - nexo_create_app_token(name, abilities, allowed_platforms, expires_at)
  - nexo_support_ticket_list(status, limit)
  - nexo_support_ticket_read(ticket_id)
  - nexo_support_ticket_create(subject, message, priority)
  - nexo_support_ticket_message(ticket_id, body, client_message_id)
  - nexo_support_ticket_close(ticket_id)
  - nexo_support_ticket_reopen(ticket_id)

The session bearer (Sanctum personal access token) is stored by NEXO Desktop in
the OS keychain at:
  service = "com.nexo.shared-auth"
  account = "sanctum-token"

Cross-platform via the `keyring` library (macOS Keychain, Windows Credential
Manager, secret-service on Linux). The bearer is never echoed back to the
agent — it only flows in the Authorization header to the backend.

These tools let the agent (Nero) call any /api/* endpoint on the user's behalf
without the agent having to manage tokens. Use them from fichas that need
backend-driven NEXO Credits flows, or to mint persistent AppTokens for
embeddable resources (chatbot snippets, widgets) that the user pastes on
their own website.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote, urlencode

import keyring
import requests

KEYCHAIN_SERVICE = "com.nexo.shared-auth"
KEYCHAIN_ACCOUNT = "sanctum-token"
DEFAULT_BASE_URL = "https://nexo-desktop.com"
ALLOWED_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH"}
REQUEST_TIMEOUT_SECONDS = 60
MAX_BODY_PREVIEW_CHARS = 6000

# Allowed abilities for AppToken creation. Keep in sync with
# AppTokenService::ABILITY_* constants in the Laravel backend.
ALLOWED_ABILITIES = {
    "provider-proxy:call",
    "provider-proxy:estimate",
    "credits:read",
}


def _read_session_bearer() -> str | None:
    try:
        return keyring.get_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)
    except Exception:
        return None


def _parse_json_arg(raw: str, label: str) -> tuple[Any, str | None]:
    text = (raw or "").strip()
    if not text:
        return None, None
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, f"ERROR: {label} is not valid JSON: {exc}"


def _format_response(method: str, path: str, status: int, body_text: str) -> str:
    return f"HTTP {status} {method} {path}\n{body_text}"


def handle_api_call(
    method: str,
    path: str,
    body_json: str = "",
    idempotency_key: str = "",
    headers_json: str = "",
    base_url: str = "",
) -> str:
    """Make an authenticated request to the NEXO Desktop backend.

    The session bearer is auto-loaded from the OS keychain. It is never
    returned to the caller. Use this for any /api/* endpoint the user has
    permission for (provider-proxy/*, credits/*, cards/*, etc).
    """
    bearer = _read_session_bearer()
    if not bearer:
        return (
            "ERROR: no NEXO Desktop session token found in the system keychain. "
            "The user must be logged in to NEXO Desktop before this tool can be used."
        )

    method_upper = (method or "").strip().upper()
    if method_upper not in ALLOWED_METHODS:
        return f"ERROR: method '{method}' is not allowed. Use one of {sorted(ALLOWED_METHODS)}."

    cleaned_path = (path or "").strip()
    if not cleaned_path.startswith("/"):
        return "ERROR: path must start with '/' (e.g. /api/provider-proxy/call)."

    base = (base_url or "").strip() or DEFAULT_BASE_URL
    url = base.rstrip("/") + cleaned_path

    headers = {
        "Authorization": f"Bearer {bearer}",
        "Accept": "application/json",
    }

    extra_headers, err = _parse_json_arg(headers_json, "headers_json")
    if err:
        return err
    if extra_headers is not None:
        if not isinstance(extra_headers, dict):
            return "ERROR: headers_json must be a JSON object."
        for k, v in extra_headers.items():
            # Never let caller override Authorization.
            if str(k).lower() == "authorization":
                continue
            headers[str(k)] = str(v)

    if idempotency_key.strip():
        headers["Idempotency-Key"] = idempotency_key.strip()

    body, err = _parse_json_arg(body_json, "body_json")
    if err:
        return err
    if body is not None:
        headers["Content-Type"] = "application/json"

    try:
        resp = requests.request(
            method_upper,
            url,
            headers=headers,
            json=body,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.exceptions.Timeout:
        return f"ERROR: request to {cleaned_path} timed out after {REQUEST_TIMEOUT_SECONDS}s."
    except requests.exceptions.RequestException as exc:
        return f"ERROR: network error calling {cleaned_path}: {exc}"

    try:
        parsed = resp.json()
        body_text = json.dumps(parsed, indent=2, ensure_ascii=False)
    except ValueError:
        body_text = (resp.text or "")[:MAX_BODY_PREVIEW_CHARS]

    if len(body_text) > MAX_BODY_PREVIEW_CHARS:
        body_text = body_text[:MAX_BODY_PREVIEW_CHARS] + "\n... [truncated]"

    return _format_response(method_upper, cleaned_path, resp.status_code, body_text)


def handle_create_app_token(
    name: str,
    abilities: str = "",
    allowed_platforms: str = "",
    expires_at: str = "",
) -> str:
    """Create a persistent AppToken for the current user via POST /api/auth/app-tokens.

    Use this when a card needs to mint a token that will live inside a snippet
    the user pastes on their own website (chatbot widget, embed, public API
    autoresponder). The plain-text token is returned ONCE — the agent must
    embed it in the snippet immediately and never store it elsewhere.
    """
    label = (name or "").strip()
    if not label:
        return "ERROR: name is required (human label for the token, e.g. 'chatbot-mitienda-com')."

    requested_abilities = [a.strip() for a in (abilities or "").split(",") if a.strip()]
    if not requested_abilities:
        requested_abilities = ["provider-proxy:call"]

    invalid = [a for a in requested_abilities if a not in ALLOWED_ABILITIES]
    if invalid:
        return (
            f"ERROR: invalid abilities {invalid}. "
            f"Allowed: {sorted(ALLOWED_ABILITIES)}."
        )

    payload: dict[str, Any] = {
        "name": label,
        "abilities": requested_abilities,
    }

    platforms = [p.strip() for p in (allowed_platforms or "").split(",") if p.strip()]
    if platforms:
        payload["allowed_platforms"] = platforms

    expires_clean = (expires_at or "").strip()
    if expires_clean:
        payload["expires_at"] = expires_clean

    return handle_api_call(
        method="POST",
        path="/api/auth/app-tokens",
        body_json=json.dumps(payload),
    )


def handle_support_ticket_list(status: str = "", limit: int = 20) -> str:
    """List real NEXO support tickets for the signed-in Desktop user."""
    query: dict[str, str] = {}
    clean_status = (status or "").strip()
    if clean_status:
        query["status"] = clean_status
    try:
        parsed_limit = max(1, min(100, int(limit or 20)))
    except Exception:
        parsed_limit = 20
    query["per_page"] = str(parsed_limit)
    suffix = "?" + urlencode(query) if query else ""
    return handle_api_call("GET", f"/api/support/tickets{suffix}")


def handle_support_ticket_read(ticket_id: str) -> str:
    """Read one real NEXO support ticket by id for the signed-in Desktop user."""
    clean = (ticket_id or "").strip()
    if not clean:
        return "ERROR: ticket_id is required."
    return handle_api_call("GET", f"/api/support/tickets/{quote(clean, safe='')}")


def _normalize_support_priority(priority: str) -> str:
    clean_priority = (priority or "normal").strip().lower()
    if clean_priority == "urgent":
        return "critical"
    if clean_priority not in {"low", "normal", "high", "critical"}:
        return "normal"
    return clean_priority


def handle_support_ticket_create(
    subject: str,
    message: str,
    priority: str = "normal",
    client_message_id: str = "",
    origin: str = "desktop",
) -> str:
    """Create a real NEXO support ticket instead of a private/internal followup."""
    clean_subject = (subject or "").strip()
    clean_message = (message or "").strip()
    clean_priority = _normalize_support_priority(priority)
    clean_origin = (origin or "desktop").strip().lower()
    if not clean_subject:
        return "ERROR: subject is required."
    if not clean_message:
        return "ERROR: message is required."
    if clean_origin not in {"desktop", "web", "auto_incident"}:
        clean_origin = "desktop"
    payload = {
        "title": clean_subject,
        "description": clean_message,
        "priority": clean_priority,
        "origin": clean_origin,
    }
    clean_client_message_id = (client_message_id or "").strip()
    if clean_client_message_id:
        payload["client_message_id"] = clean_client_message_id
    return handle_api_call("POST", "/api/support/tickets", body_json=json.dumps(payload, ensure_ascii=False))


def handle_support_ticket_message(ticket_id: str, body: str, client_message_id: str = "") -> str:
    """Append an evidence note to a real NEXO support ticket."""
    clean = (ticket_id or "").strip()
    clean_body = (body or "").strip()
    if not clean:
        return "ERROR: ticket_id is required."
    if not clean_body:
        return "ERROR: body is required."
    payload = {"body": clean_body}
    clean_client_message_id = (client_message_id or "").strip()
    if clean_client_message_id:
        payload["client_message_id"] = clean_client_message_id
    return handle_api_call(
        "POST",
        f"/api/support/tickets/{quote(clean, safe='')}/messages",
        body_json=json.dumps(payload, ensure_ascii=False),
    )


def handle_support_ticket_close(ticket_id: str) -> str:
    """Close a real NEXO support ticket after evidence has been recorded."""
    clean = (ticket_id or "").strip()
    if not clean:
        return "ERROR: ticket_id is required."
    return handle_api_call("POST", f"/api/support/tickets/{quote(clean, safe='')}/close")


def handle_support_ticket_reopen(ticket_id: str) -> str:
    """Reopen a real NEXO support ticket when fresh evidence shows it is still active."""
    clean = (ticket_id or "").strip()
    if not clean:
        return "ERROR: ticket_id is required."
    return handle_api_call("POST", f"/api/support/tickets/{quote(clean, safe='')}/reopen")
