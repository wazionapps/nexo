from __future__ import annotations

"""Project/artifact locator helpers with Project Atlas as authority."""

import json
from pathlib import Path
from typing import Callable, Iterable


FallbackSearch = Callable[[str, int], Iterable[dict]]


def load_project_atlas(path: str | Path) -> dict:
    try:
        payload = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _projects(atlas: dict) -> dict:
    if not isinstance(atlas, dict):
        return {}
    if isinstance(atlas.get("projects"), dict):
        return atlas["projects"]
    return atlas


def resolve_project(atlas: dict, query: str) -> dict | None:
    clean_query = str(query or "").strip().lower()
    if not clean_query:
        return None
    for key, entry in _projects(atlas).items():
        if not isinstance(entry, dict):
            continue
        aliases = [str(key), *(entry.get("aliases") or [])]
        haystack = " ".join([*aliases, str(entry.get("description") or "")]).lower()
        if clean_query == str(key).lower() or clean_query in haystack:
            return {"key": str(key), **entry}
    return None


def project_locations(project: dict | None) -> dict:
    if not project:
        return {}
    locations = project.get("locations")
    return locations if isinstance(locations, dict) else {}


def locate_artifact(
    *,
    atlas: dict,
    query: str,
    artifact_kind: str = "",
    fallback_search: FallbackSearch | None = None,
    limit: int = 5,
) -> dict:
    project = resolve_project(atlas, query)
    locations = project_locations(project)
    matches: list[dict] = []
    if project:
        for name, value in locations.items():
            if artifact_kind and artifact_kind not in str(name):
                continue
            matches.append({
                "source": "project_atlas",
                "project_key": project["key"],
                "kind": str(name),
                "path": str(value),
                "confidence": 1.0,
            })
    if not matches and fallback_search:
        for row in list(fallback_search(query, limit))[:limit]:
            if not isinstance(row, dict):
                continue
            matches.append({
                "source": str(row.get("source") or "fallback"),
                "project_key": str(row.get("project_key") or ""),
                "kind": str(row.get("kind") or artifact_kind or "artifact"),
                "path": str(row.get("path") or row.get("file") or ""),
                "confidence": float(row.get("confidence") or row.get("score") or 0.4),
            })
    return {
        "query": query,
        "artifact_kind": artifact_kind,
        "project_key": project["key"] if project else "",
        "matches": matches,
        "used_fallback": not bool(project) and bool(fallback_search),
    }
