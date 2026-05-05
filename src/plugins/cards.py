"""Authenticated client for NEXO official protocol cards.

The protocol corpus lives on the private NEXO Desktop backend. This open-source
plugin only knows how to authenticate and fetch cards at runtime.
"""

from __future__ import annotations

import json
import os
import platform
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_API_BASE = "https://nexo-desktop.com"
SHARED_AUTH_DIRNAME = "nexo-shared-auth"
SHARED_AUTH_FILENAME = "session.json"

_urlopen = urllib.request.urlopen


def _json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "si", "sí", "on"}


def _as_int(value: Any, default: int = 5) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _api_base() -> str:
    raw = os.environ.get("NEXO_DESKTOP_API_BASE") or os.environ.get("NEXO_CARDS_API_BASE") or DEFAULT_API_BASE
    return raw.strip().rstrip("/") or DEFAULT_API_BASE


def _normalize_locale(locale: str = "es") -> str:
    return "en" if str(locale or "").lower().startswith("en") else "es"


def _shared_auth_candidates() -> list[Path]:
    candidates: list[Path] = []
    for env_name in ("NEXO_SHARED_AUTH_FILE", "NEXO_DESKTOP_SHARED_AUTH_FILE"):
        raw = os.environ.get(env_name)
        if raw:
            candidates.append(Path(raw).expanduser())

    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / SHARED_AUTH_DIRNAME / SHARED_AUTH_FILENAME)

    home = Path.home()
    system = platform.system().lower()
    if system == "darwin":
        candidates.append(home / "Library" / "Application Support" / SHARED_AUTH_DIRNAME / SHARED_AUTH_FILENAME)
    elif system == "windows":
        candidates.append(home / "AppData" / "Roaming" / SHARED_AUTH_DIRNAME / SHARED_AUTH_FILENAME)
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        if xdg:
            candidates.append(Path(xdg) / SHARED_AUTH_DIRNAME / SHARED_AUTH_FILENAME)
        candidates.append(home / ".config" / SHARED_AUTH_DIRNAME / SHARED_AUTH_FILENAME)

    nexo_home = Path(os.environ.get("NEXO_HOME", str(home / ".nexo"))).expanduser()
    candidates.append(nexo_home / "runtime" / "shared-auth" / SHARED_AUTH_FILENAME)
    return candidates


def _read_token_from_shared_auth() -> str:
    for path in _shared_auth_candidates():
        try:
            if not path.is_file():
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            token = str(payload.get("token") or "").strip()
            if token:
                return token
        except Exception:
            continue
    return ""


def _read_token() -> str:
    for env_name in ("NEXO_DESKTOP_AUTH_TOKEN", "NEXO_CARDS_TOKEN", "NEXO_AUTH_TOKEN"):
        token = str(os.environ.get(env_name) or "").strip()
        if token:
            return token
    return _read_token_from_shared_auth()


def _error(error_type: str, message: str, *, status: int = 0, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": False,
        "error": {
            "type": error_type,
            "message": message,
        },
    }
    if status:
        payload["status"] = status
    if extra:
        payload["error"].update(extra)
    return payload


def _decode_body(raw: bytes) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        decoded = json.loads(raw.decode("utf-8"))
        return decoded if isinstance(decoded, dict) else {"data": decoded}
    except Exception:
        return {"raw": raw.decode("utf-8", errors="replace")[:1000]}


def _request_json(method: str, path: str, *, body: dict[str, Any] | None = None, locale: str = "es") -> dict[str, Any]:
    token = _read_token()
    if not token:
        return _error(
            "not_authenticated",
            "No hay sesión NEXO Desktop disponible para consultar fichas.",
        )

    data = json.dumps(body or {}, ensure_ascii=False).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        f"{_api_base()}{path}",
        data=data,
        method=method.upper(),
        headers={
            "Accept": "application/json",
            "Accept-Language": _normalize_locale(locale),
            "Authorization": f"Bearer {token}",
            **({"Content-Type": "application/json"} if data is not None else {}),
        },
    )
    try:
        with _urlopen(request, timeout=20) as response:
            payload = _decode_body(response.read())
            if isinstance(payload, dict):
                payload.setdefault("ok", True)
                payload.setdefault("status", getattr(response, "status", 200))
                return payload
            return {"ok": True, "data": payload, "status": getattr(response, "status", 200)}
    except urllib.error.HTTPError as exc:
        payload = _decode_body(exc.read())
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            return _error(
                str(error.get("type") or "request_failed"),
                str(error.get("message") or f"HTTP {exc.code}"),
                status=int(exc.code or 0),
                extra={k: v for k, v in error.items() if k not in {"type", "message"}},
            )
        return _error("request_failed", f"Protocol cards API returned HTTP {exc.code}.", status=int(exc.code or 0))
    except Exception as exc:
        return _error("network_error", str(exc))


def handle_card_catalog(locale: str = "es") -> str:
    """Return the visible official protocol card catalog. Never includes protocols."""
    params = urllib.parse.urlencode({"locale": _normalize_locale(locale)})
    return _json(_request_json("GET", f"/api/cards/catalog?{params}", locale=locale))


def handle_card_get(slug: str, locale: str = "es") -> str:
    """Fetch one official protocol card, including protocol text, by slug."""
    clean_slug = str(slug or "").strip()
    if not clean_slug:
        return _json(_error("invalid_input", "slug is required"))
    params = urllib.parse.urlencode({"locale": _normalize_locale(locale)})
    safe_slug = urllib.parse.quote(clean_slug, safe="")
    return _json(_request_json("GET", f"/api/cards/{safe_slug}?{params}", locale=locale))


def handle_card_match(
    query: str,
    limit: int = 5,
    include_protocol: bool = True,
    locale: str = "es",
    category: str = "",
    business_type: str = "",
) -> str:
    """Find official protocol cards for a user request.

    Use this before non-trivial work when available. Protocols are fetched from
    the authenticated backend at runtime; this package does not embed them.
    """
    clean_query = str(query or "").strip()
    if not clean_query:
        return _json(_error("invalid_input", "query is required"))
    body = {
        "query": clean_query,
        "limit": max(1, min(20, _as_int(limit, 5))),
        "include_protocol": _as_bool(include_protocol, True),
        "locale": _normalize_locale(locale),
    }
    if category:
        body["category"] = str(category).strip()
    if business_type:
        body["business_type"] = str(business_type).strip()
    return _json(_request_json("POST", "/api/cards/match", body=body, locale=locale))


TOOLS = [
    (handle_card_catalog, "nexo_card_catalog", "List visible official NEXO protocol cards from the authenticated backend. Does not return protocol text."),
    (handle_card_get, "nexo_card_get", "Fetch one official NEXO protocol card by slug from the authenticated backend, including protocol text."),
    (handle_card_match, "nexo_card_match", "Find the official NEXO protocol card for a user request. Call before non-trivial work when available."),
]
