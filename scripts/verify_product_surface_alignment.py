#!/usr/bin/env python3
"""Validate Product Knowledge against backend product surfaces.

The Product Knowledge catalog owns stable product semantics. The backend owns
live state. This release gate keeps those two layers from drifting when managed
backend routes are added, renamed, or removed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from product_knowledge import load_product_catalog


EXPECTED_BACKEND_SURFACES: tuple[dict[str, Any], ...] = (
    {
        "id": "cards",
        "capability_id": "nexo_protocol_cards",
        "route_fragments": ["cards/catalog", "cards/match", "cards/{slug}"],
        "source_refs": ["CardController", "CardCatalogService"],
    },
    {
        "id": "support",
        "capability_id": "nexo_support_tickets_api",
        "route_fragments": ["support/tickets"],
        "source_refs": ["SupportTicketController"],
    },
    {
        "id": "credits_provider_proxy",
        "capability_id": "nexo_credits_provider_proxy",
        "route_fragments": [
            "credits/balance",
            "provider-proxy/platforms",
            "provider-proxy/models",
            "provider-proxy/estimate",
            "provider-proxy/call",
            "nexo-credits",
            "nexo-credits/credentials",
            "nexo-credits/sync-openrouter",
        ],
        "source_refs": ["CreditsController", "ProviderProxyController", "ProviderCatalogService", "NexoCreditsController"],
    },
    {
        "id": "cloud_edge",
        "capability_id": "nexo_managed_cloud_edge",
        "route_fragments": [
            "nexo-cloud/projects",
            "nexo-cloud/provision",
            "nexo-edge/assets",
            "nexo-edge/domains/check",
            "nexo-edge/dns-records",
        ],
        "source_refs": ["NexoCloudController", "NexoEdgeController"],
    },
    {
        "id": "managed_email",
        "capability_id": "nexo_agent_email",
        "route_fragments": [
            "nexo-email/account",
            "nexo-email/availability",
            "nexo-email/account/ensure",
            "nexo-email/account/connection",
        ],
        "source_refs": ["NexoEmailController"],
    },
    {
        "id": "managed_communications",
        "capability_id": "nexo_managed_communications_providers",
        "route_fragments": [
            "nexo-vapi/resources",
            "nexo-twilio/resources",
            "nexo-wazion-whatsapp/sessions",
            "nexo-email-mass/domains",
            "desktop/voice/stt",
            "desktop/voice/tts/stream",
            "voice/stt",
            "voice/tts/stream",
        ],
        "source_refs": [
            "NexoVapiController",
            "NexoTwilioController",
            "NexoWazionWhatsappController",
            "NexoEmailMassController",
            "VoiceController",
        ],
    },
)

EXPECTED_MANAGED_ROUTE_PREFIXES = {
    "cards",
    "credits",
    "provider-proxy",
    "support",
    "voice",
    "desktop",
    "nexo-credits",
    "nexo-cloud",
    "nexo-edge",
    "nexo-email",
    "nexo-email-mass",
    "nexo-twilio",
    "nexo-vapi",
    "nexo-wazion-whatsapp",
}


def _default_backend_candidates() -> list[Path]:
    return [
        ROOT.parent / "nexo-desktop-web",
        Path.home() / "Documents" / "_PhpstormProjects" / "nexo-desktop-web",
    ]


def resolve_backend_root(raw: str = "") -> Path | None:
    candidates: list[Path] = []
    if raw.strip():
        candidates.append(Path(raw).expanduser())
    env_root = os.environ.get("NEXO_DESKTOP_WEB_ROOT", "").strip()
    if env_root:
        candidates.append(Path(env_root).expanduser())
    candidates.extend(_default_backend_candidates())
    for candidate in candidates:
        if (candidate / "routes" / "web.php").is_file():
            return candidate
    return None


def _catalog_by_id(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = catalog.get("capabilities") or []
    return {str(row.get("id") or ""): row for row in rows if isinstance(row, dict)}


def _capability_text(capability: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("id", "title", "category", "layer", "status", "summary"):
        parts.append(str(capability.get(key, "")))
    for key in ("aliases", "source_refs", "surfaces"):
        parts.extend(str(item) for item in capability.get(key) or [])
    live_state = capability.get("live_state") or {}
    parts.extend(str(value) for value in live_state.values())
    actions = capability.get("actions") or {}
    parts.extend(str(item) for values in actions.values() for item in (values or []))
    return "\n".join(parts)


def _read_backend_routes(backend_root: Path | None) -> str:
    if backend_root is None:
        return ""
    return (backend_root / "routes" / "web.php").read_text(encoding="utf-8")


def _managed_route_prefixes(routes_text: str) -> set[str]:
    prefixes: set[str] = set()
    for match in re.finditer(r"Route::(?:get|post|put|patch|delete|match)\(\s*(?:\[[^\]]+\]\s*,\s*)?['\"]([^'\"]+)['\"]", routes_text):
        route = match.group(1).strip("/")
        if not route:
            continue
        prefix = route.split("/", 1)[0]
        if prefix.startswith("nexo-") or prefix in {"cards", "credits", "provider-proxy", "support", "voice", "desktop"}:
            prefixes.add(prefix)
    return prefixes


def validate_product_surface_alignment(
    *,
    catalog: dict[str, Any] | None = None,
    backend_root: Path | str | None = None,
    require_backend: bool = False,
) -> dict[str, Any]:
    payload = catalog or load_product_catalog()
    capabilities = _catalog_by_id(payload)
    errors: list[str] = []
    warnings: list[str] = []
    backend = Path(backend_root).expanduser() if backend_root else resolve_backend_root()
    routes_text = ""

    if backend is None:
        message = "backend repo not found; set NEXO_DESKTOP_WEB_ROOT or pass --backend-root to validate live routes"
        if require_backend:
            errors.append(message)
        else:
            warnings.append(message)
    else:
        routes_file = backend / "routes" / "web.php"
        if not routes_file.is_file():
            errors.append(f"backend routes file missing: {routes_file}")
        else:
            routes_text = _read_backend_routes(backend)

    for surface in EXPECTED_BACKEND_SURFACES:
        capability_id = surface["capability_id"]
        capability = capabilities.get(capability_id)
        if capability is None:
            errors.append(f"{surface['id']}: missing catalog capability {capability_id}")
            continue
        text = _capability_text(capability)
        for source_ref in surface["source_refs"]:
            if source_ref not in text:
                errors.append(f"{surface['id']}: {capability_id} missing backend source ref {source_ref}")
        if routes_text:
            for fragment in surface["route_fragments"]:
                if fragment not in routes_text:
                    errors.append(f"{surface['id']}: backend route fragment missing from routes/web.php: {fragment}")

    if routes_text:
        unknown = sorted(_managed_route_prefixes(routes_text) - EXPECTED_MANAGED_ROUTE_PREFIXES)
        if unknown:
            errors.append("unmapped managed backend route prefixes: " + ", ".join(unknown))

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "backend_root": str(backend) if backend else "",
        "surfaces_checked": len(EXPECTED_BACKEND_SURFACES),
        "schema_version": 1,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-root", default="", help="Path to nexo-desktop-web. Defaults to sibling repo when present.")
    parser.add_argument("--require-backend", action="store_true", help="Fail if the backend repo cannot be found.")
    args = parser.parse_args(argv)

    result = validate_product_surface_alignment(
        backend_root=Path(args.backend_root).expanduser() if args.backend_root else None,
        require_backend=args.require_backend,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
