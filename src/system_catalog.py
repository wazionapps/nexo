from __future__ import annotations
"""Live system catalog / ontology derived from canonical NEXO sources."""

import ast
import importlib.util
import json
import os
import sys
from pathlib import Path

from db import get_db, list_skills, sync_skill_directories
from plugin_loader import PERSONAL_PLUGINS_DIR, PLUGINS_DIR, list_plugins
from script_registry import list_scripts

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
NEXO_CODE = Path(__file__).resolve().parent
SERVER_PATH = NEXO_CODE / "server.py"
MANIFEST_PATHS = [NEXO_CODE / "crons" / "manifest.json", NEXO_HOME / "crons" / "manifest.json"]
ATLAS_PATH = NEXO_HOME / "brain" / "project-atlas.json"

SECTION_ORDER = (
    "core_tools",
    "plugin_tools",
    "skills",
    "scripts",
    "crons",
    "projects",
    "artifacts",
)


def _normalize_text(text: str | None) -> str:
    return str(text or "").strip().lower()


def _tokenize(text: str | None) -> set[str]:
    import re
    normalized = _normalize_text(text)
    return {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9._:-]{1,}", normalized)
        if len(token) >= 3
    }


def _score(query_tokens: set[str], haystack: str) -> float:
    if not query_tokens:
        return 0.0
    haystack_tokens = _tokenize(haystack)
    if not haystack_tokens:
        return 0.0
    overlap = query_tokens & haystack_tokens
    if not overlap:
        return 0.0
    return len(overlap) / max(1, min(len(query_tokens), len(haystack_tokens)))


def _truncate(text: str | None, limit: int = 180) -> str:
    clean = str(text or "").strip()
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3] + "..."


def _tool_category(name: str) -> str:
    if name.startswith("nexo_recent_context") or name.startswith("nexo_pre_action_context") or name.startswith("nexo_hot_context"):
        return "recent_memory"
    if name.startswith("nexo_transcript"):
        return "transcripts"
    if name.startswith("nexo_session") or name.startswith("nexo_checkpoint"):
        return "sessions"
    if name.startswith("nexo_followup") or name.startswith("nexo_reminder"):
        return "reminders"
    if name.startswith("nexo_skill"):
        return "skills"
    if name.startswith("nexo_plugin"):
        return "plugins"
    if name.startswith("nexo_goal") or name.startswith("nexo_workflow"):
        return "workflow"
    if name.startswith("nexo_learning"):
        return "learnings"
    if name.startswith("nexo_guard") or name.startswith("nexo_task") or name.startswith("nexo_cortex"):
        return "protocol"
    return "general"


def _parse_core_tools() -> list[dict]:
    if not SERVER_PATH.is_file():
        return []
    try:
        tree = ast.parse(SERVER_PATH.read_text())
    except Exception:
        return []

    entries: list[dict] = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        if not any(
            isinstance(dec, ast.Attribute) and getattr(dec.value, "id", "") == "mcp" and dec.attr == "tool"
            for dec in node.decorator_list
        ):
            continue
        doc = ast.get_docstring(node) or ""
        first_line = doc.strip().splitlines()[0].strip() if doc.strip() else ""
        entries.append(
            {
                "kind": "core_tool",
                "name": node.name,
                "description": first_line,
                "category": _tool_category(node.name),
                "path": str(SERVER_PATH),
                "line": int(getattr(node, "lineno", 0) or 0),
                "source": "core",
            }
        )
    return entries


def _plugin_module_tools(filename: str, created_by: str) -> dict[str, str]:
    module_name = f"plugins.{filename[:-3]}"
    module = sys.modules.get(module_name)
    if module is None:
        plugin_dir = PLUGINS_DIR if created_by == "repo" else PERSONAL_PLUGINS_DIR
        path = Path(plugin_dir) / filename
        if not path.is_file():
            return {}
        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                return {}
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception:
            return {}
    tools = getattr(module, "TOOLS", []) or []
    result: dict[str, str] = {}
    for item in tools:
        try:
            _, name, description = item
        except Exception:
            continue
        result[str(name)] = str(description or "")
    return result


def _plugin_entries() -> list[dict]:
    rows = list_plugins()
    entries: list[dict] = []
    for row in rows:
        filename = str(row.get("filename") or "")
        created_by = str(row.get("created_by") or row.get("source") or "repo")
        descriptions = _plugin_module_tools(filename, created_by)
        names = str(row.get("tool_names") or "").split(",")
        for name in [n.strip() for n in names if n.strip()]:
            entries.append(
                {
                    "kind": "plugin_tool",
                    "name": name,
                    "description": descriptions.get(name, ""),
                    "plugin": filename,
                    "source": created_by,
                    "category": _tool_category(name),
                }
            )
    return entries


def _skill_entries() -> list[dict]:
    try:
        sync_skill_directories()
    except Exception:
        pass
    entries: list[dict] = []
    for row in list_skills():
        entries.append(
            {
                "kind": "skill",
                "name": row.get("id", ""),
                "display_name": row.get("name", ""),
                "description": row.get("description", "") or "",
                "source": row.get("source_kind", "") or "",
                "level": row.get("level", "") or "",
                "mode": row.get("mode", "") or "",
                "execution_level": row.get("execution_level", "") or "",
                "trust_score": row.get("trust_score", 0),
                "tags": row.get("tags", "[]"),
            }
        )
    return entries


def _script_entries() -> list[dict]:
    entries: list[dict] = []
    for row in list_scripts(include_core=True):
        entries.append(
            {
                "kind": "script",
                "name": row.get("name", ""),
                "description": row.get("description", "") or "",
                "runtime": row.get("runtime", "") or "",
                "path": row.get("path", "") or "",
                "source": "core" if row.get("core") else "personal",
                "classification": row.get("classification", "") or "",
                "declared_schedule": row.get("declared_schedule", {}) or {},
            }
        )
    return entries


def _cron_entries() -> list[dict]:
    manifest = None
    for path in MANIFEST_PATHS:
        if path.is_file():
            try:
                manifest = json.loads(path.read_text())
                break
            except Exception:
                continue
    if not isinstance(manifest, dict):
        return []
    entries: list[dict] = []
    for cron in manifest.get("crons", []) or []:
        entries.append(
            {
                "kind": "cron",
                "name": cron.get("id", ""),
                "description": cron.get("description", "") or "",
                "script": cron.get("script", "") or "",
                "schedule": cron.get("schedule", {}) or {},
                "optional": bool(cron.get("optional", False)),
            }
        )
    return entries


def _project_entries() -> list[dict]:
    if not ATLAS_PATH.is_file():
        return []
    try:
        payload = json.loads(ATLAS_PATH.read_text())
    except Exception:
        return []
    entries: list[dict] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            if str(key).startswith("_"):
                continue
            if not isinstance(value, dict):
                continue
            entries.append(
                {
                    "kind": "project",
                    "name": key,
                    "path": value.get("path", "") or "",
                    "domain": value.get("domain", "") or "",
                    "aliases": value.get("aliases", []) or [],
                    "services": value.get("services", {}) or {},
                    "plugins": value.get("plugins", "") or value.get("plugin_path", "") or "",
                }
            )
    return entries


def _artifact_entries() -> list[dict]:
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT canonical_name, kind, domain, state, uri, paths, ports, aliases FROM artifact_registry ORDER BY last_touched_at DESC LIMIT 100"
        ).fetchall()
    except Exception:
        return []
    return [
        {
            "kind": "artifact",
            "name": row["canonical_name"],
            "artifact_kind": row["kind"],
            "domain": row["domain"],
            "state": row["state"],
            "uri": row["uri"],
            "paths": row["paths"],
            "ports": row["ports"],
            "aliases": row["aliases"],
        }
        for row in rows
    ]


def build_system_catalog() -> dict:
    catalog = {
        "core_tools": _parse_core_tools(),
        "plugin_tools": _plugin_entries(),
        "skills": _skill_entries(),
        "scripts": _script_entries(),
        "crons": _cron_entries(),
        "projects": _project_entries(),
        "artifacts": _artifact_entries(),
    }
    catalog["summary"] = {
        section: len(catalog.get(section) or [])
        for section in SECTION_ORDER
    }
    return catalog


def search_system_catalog(query: str, *, section: str = "", limit: int = 20) -> list[dict]:
    catalog = build_system_catalog()
    query_tokens = _tokenize(query)
    sections = [section] if section in SECTION_ORDER else list(SECTION_ORDER)
    matches: list[dict] = []
    for section_name in sections:
        for entry in catalog.get(section_name) or []:
            haystack = " ".join(
                [
                    section_name,
                    str(entry.get("name", "") or ""),
                    str(entry.get("display_name", "") or ""),
                    str(entry.get("description", "") or ""),
                    str(entry.get("source", "") or ""),
                    str(entry.get("category", "") or ""),
                    str(entry.get("plugin", "") or ""),
                    str(entry.get("domain", "") or ""),
                    str(entry.get("path", "") or ""),
                    json.dumps(entry, ensure_ascii=False),
                ]
            )
            score = _score(query_tokens, haystack) if query_tokens else 0.5
            if query_tokens and score <= 0:
                continue
            row = dict(entry)
            row["_section"] = section_name
            row["_score"] = round(score, 4)
            matches.append(row)
    matches.sort(key=lambda row: (row["_score"], row.get("name", "")), reverse=True)
    return matches[: max(1, int(limit or 20))]


def explain_tool(name: str) -> dict | None:
    clean = _normalize_text(name)
    if not clean:
        return None
    exact = search_system_catalog(clean, limit=200)
    for row in exact:
        if _normalize_text(row.get("name")) == clean:
            return row
    for row in exact:
        if clean in _normalize_text(row.get("name")):
            return row
    return None


def format_catalog(catalog: dict, *, section: str = "", query: str = "", limit: int = 20) -> str:
    summary = catalog.get("summary") or {}
    if query:
        matches = search_system_catalog(query, section=section, limit=limit)
        if not matches:
            scope = section or "all sections"
            return f"No system-catalog matches for '{query}' in {scope}."
        lines = [f"SYSTEM CATALOG SEARCH — '{query}' ({len(matches)} match(es))"]
        for row in matches:
            label = row.get("_section", "")
            title = row.get("display_name") or row.get("name") or "(unnamed)"
            desc = _truncate(row.get("description") or row.get("path") or row.get("script") or "", 180)
            suffix = f" — {desc}" if desc else ""
            lines.append(f"- [{label}] {title}{suffix}")
        return "\n".join(lines)

    if section in SECTION_ORDER:
        entries = catalog.get(section) or []
        if not entries:
            return f"SYSTEM CATALOG — {section}: empty"
        lines = [f"SYSTEM CATALOG — {section} ({len(entries)})"]
        for row in entries[: max(1, int(limit or 20))]:
            title = row.get("display_name") or row.get("name") or "(unnamed)"
            desc = _truncate(row.get("description") or row.get("path") or row.get("script") or "", 180)
            suffix = f" — {desc}" if desc else ""
            lines.append(f"- {title}{suffix}")
        return "\n".join(lines)

    lines = ["SYSTEM CATALOG SUMMARY"]
    for name in SECTION_ORDER:
        lines.append(f"- {name}: {summary.get(name, 0)}")
    return "\n".join(lines)


def format_tool_explanation(entry: dict | None) -> str:
    if not entry:
        return "Tool/capability not found in the live system catalog."
    lines = [
        f"CATALOG ENTRY — {entry.get('name') or entry.get('display_name')}",
        f"Section: {entry.get('_section') or entry.get('kind')}",
    ]
    if entry.get("display_name"):
        lines.append(f"Display name: {entry['display_name']}")
    if entry.get("description"):
        lines.append(f"Description: {entry['description']}")
    if entry.get("category"):
        lines.append(f"Category: {entry['category']}")
    if entry.get("source"):
        lines.append(f"Source: {entry['source']}")
    if entry.get("plugin"):
        lines.append(f"Plugin: {entry['plugin']}")
    if entry.get("path"):
        lines.append(f"Path: {entry['path']}")
    if entry.get("line"):
        lines.append(f"Line: {entry['line']}")
    if entry.get("script"):
        lines.append(f"Script: {entry['script']}")
    if entry.get("runtime"):
        lines.append(f"Runtime: {entry['runtime']}")
    if entry.get("level"):
        lines.append(f"Level: {entry['level']}")
    if entry.get("mode"):
        lines.append(f"Mode: {entry['mode']}")
    if entry.get("execution_level"):
        lines.append(f"Execution level: {entry['execution_level']}")
    if entry.get("domain"):
        lines.append(f"Domain: {entry['domain']}")
    return "\n".join(lines)
