#!/usr/bin/env python3
"""Verify that managed MCP provider locks are pinned to npm latest.

The managed MCP catalog is Brain-owned. Release prep should not rely on a
human remembering to check npm packages manually, so this script compares
src/managed_mcp/lock.json against the registry's current latest metadata.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "src" / "managed_mcp" / "lock.json"
DEFAULT_REGISTRY = "https://registry.npmjs.org"


def fail(message: str) -> None:
    raise SystemExit(f"[managed-mcp-lock] {message}")


def load_lock(path: Path = LOCK_PATH) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"invalid JSON in {path}: {exc}")
    if not isinstance(payload, dict):
        fail(f"{path} must contain a JSON object")
    providers = payload.get("providers")
    if not isinstance(providers, dict) or not providers:
        fail(f"{path} must contain providers")
    return payload


def write_lock(payload: dict[str, Any], path: Path = LOCK_PATH) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def registry_package_url(package: str, registry: str = DEFAULT_REGISTRY) -> str:
    base = registry.rstrip("/")
    encoded = urllib.parse.quote(package, safe="")
    return f"{base}/{encoded}"


def fetch_npm_package(
    package: str,
    *,
    registry: str = DEFAULT_REGISTRY,
    urlopen: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, Any]:
    request = urllib.request.Request(
        registry_package_url(package, registry=registry),
        headers={
            "Accept": "application/json",
            "User-Agent": "nexo-managed-mcp-release-check",
        },
    )
    try:
        with urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        fail(f"npm registry returned HTTP {exc.code} for {package}")
    except urllib.error.URLError as exc:
        fail(f"npm registry request failed for {package}: {exc.reason}")
    if not isinstance(payload, dict):
        fail(f"npm registry payload for {package} must be an object")
    return payload


def normalize_bin(value: Any) -> dict[str, str]:
    if isinstance(value, str) and value.strip():
        return {"": value.strip()}
    if not isinstance(value, dict):
        return {}
    result: dict[str, str] = {}
    for key, raw in value.items():
        name = str(key or "").strip()
        path = str(raw or "").strip()
        if name and path:
            result[name] = path
    return result


def latest_metadata(package_payload: dict[str, Any]) -> dict[str, Any]:
    dist_tags = package_payload.get("dist-tags")
    latest = str((dist_tags or {}).get("latest") or "").strip() if isinstance(dist_tags, dict) else ""
    versions = package_payload.get("versions")
    if not latest or not isinstance(versions, dict) or latest not in versions:
        fail("npm registry payload missing dist-tags.latest version metadata")
    metadata = versions[latest]
    if not isinstance(metadata, dict):
        fail(f"npm registry latest metadata for {latest} must be an object")
    return metadata


def expected_lock_fields(package_payload: dict[str, Any]) -> dict[str, Any]:
    latest = latest_metadata(package_payload)
    package = str(latest.get("name") or package_payload.get("name") or "").strip()
    version = str(latest.get("version") or "").strip()
    dist = latest.get("dist") if isinstance(latest.get("dist"), dict) else {}
    integrity = str(dist.get("integrity") or "").strip()
    tarball = str(dist.get("tarball") or "").strip()
    bins = normalize_bin(latest.get("bin"))
    preferred_bin = ""
    if bins:
        package_bin = package.rsplit("/", 1)[-1]
        mcp_bin = f"{package_bin}-mcp"
        if mcp_bin in bins:
            preferred_bin = mcp_bin
        elif package_bin in bins and "mcp" in package_bin:
            preferred_bin = package_bin
        else:
            mcp_candidates = [name for name in bins if "mcp" in name.lower()]
            preferred_bin = (mcp_candidates or [package_bin if package_bin in bins else next(iter(bins))])[0]
    engines = latest.get("engines") if isinstance(latest.get("engines"), dict) else {}
    if not package or not version or not integrity or not tarball or not preferred_bin:
        fail(f"npm latest metadata incomplete for {package or '<unknown>'}@{version or '<unknown>'}")
    return {
        "source_type": "npm",
        "package": package,
        "version": version,
        "integrity": integrity,
        "tarball": tarball,
        "bin": preferred_bin,
        "engines": dict(engines),
    }


def check_lock(
    lock: dict[str, Any],
    *,
    fetcher: Callable[[str], dict[str, Any]],
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    errors: list[str] = []
    latest_by_provider: dict[str, dict[str, Any]] = {}
    providers = lock.get("providers") if isinstance(lock.get("providers"), dict) else {}
    for provider_id, current in providers.items():
        if not isinstance(current, dict):
            errors.append(f"{provider_id}: provider lock must be an object")
            continue
        if current.get("source_type") != "npm":
            continue
        package = str(current.get("package") or "").strip()
        if not package:
            errors.append(f"{provider_id}: package missing")
            continue
        latest = expected_lock_fields(fetcher(package))
        latest_by_provider[str(provider_id)] = latest
        for field in ("version", "integrity", "tarball", "bin"):
            if str(current.get(field) or "").strip() != str(latest.get(field) or "").strip():
                errors.append(
                    f"{provider_id}: {field} is {current.get(field)!r}, latest is {latest.get(field)!r}"
                )
        if current.get("package") != latest.get("package"):
            errors.append(f"{provider_id}: package is {current.get('package')!r}, latest metadata is {latest.get('package')!r}")
        if "@latest" in str(current.get("version") or "") or str(current.get("version") or "") == "0.0.0-managed":
            errors.append(f"{provider_id}: version must be an exact npm version, not {current.get('version')!r}")
    return errors, latest_by_provider


def update_lock_to_latest(lock: dict[str, Any], latest_by_provider: dict[str, dict[str, Any]]) -> dict[str, Any]:
    updated = deepcopy(lock)
    providers = updated.get("providers") if isinstance(updated.get("providers"), dict) else {}
    for provider_id, latest in latest_by_provider.items():
        current = providers.get(provider_id)
        if not isinstance(current, dict):
            continue
        current.update(latest)
    return updated


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", default=str(LOCK_PATH), help="Managed MCP lockfile path.")
    parser.add_argument("--registry", default=DEFAULT_REGISTRY, help="npm registry base URL.")
    parser.add_argument("--update", action="store_true", help="Rewrite the lockfile to npm latest metadata.")
    args = parser.parse_args()

    lock_path = Path(args.lock).expanduser().resolve()
    lock = load_lock(lock_path)
    errors, latest = check_lock(
        lock,
        fetcher=lambda package: fetch_npm_package(package, registry=args.registry),
    )

    if args.update:
        updated = update_lock_to_latest(lock, latest)
        write_lock(updated, lock_path)
        errors, _latest_after = check_lock(
            updated,
            fetcher=lambda package: fetch_npm_package(package, registry=args.registry),
        )
        if errors:
            fail("lock still out of date after update:\n- " + "\n- ".join(errors))
        print(f"[managed-mcp-lock] updated {lock_path} to npm latest ({len(latest)} providers)")
        return 0

    if errors:
        fail(
            "managed MCP lock is not pinned to npm latest:\n- "
            + "\n- ".join(errors)
            + "\nRun: python3 scripts/verify_managed_mcp_lock.py --update"
        )
    print(f"[managed-mcp-lock] OK ({len(latest)} npm providers pinned to latest)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
