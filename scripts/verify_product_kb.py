#!/usr/bin/env python3
"""Validate the structured NEXO product knowledge catalog."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from product_knowledge import catalog_entries_for_system_catalog, list_capabilities, validate_catalog
from verify_product_surface_alignment import validate_product_surface_alignment


def main() -> int:
    errors = validate_catalog()
    surface_alignment = validate_product_surface_alignment()
    errors.extend(surface_alignment["errors"])
    capabilities = list_capabilities()
    entries = catalog_entries_for_system_catalog()
    ids = {capability["id"] for capability in capabilities}
    entry_names = {entry["name"] for entry in entries}
    missing_entries = sorted(ids - entry_names)
    if missing_entries:
        errors.append("system catalog entries missing for: " + ", ".join(missing_entries))
    sensitive_categories = {"credits", "cloud", "email", "support", "memory", "automation"}
    for capability in capabilities:
        if capability["category"] not in sensitive_categories:
            continue
        safety = capability.get("safety") or {}
        if "yes" not in str(safety.get("consent_required", "")).lower() and capability["category"] != "support":
            errors.append(f"{capability['id']} must explicitly document consent requirements")
        if not safety.get("forbidden_actions"):
            errors.append(f"{capability['id']} must list forbidden_actions")
    result = {
        "ok": not errors,
        "capability_count": len(capabilities),
        "system_catalog_entry_count": len(entries),
        "surface_alignment": surface_alignment,
        "errors": errors,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
