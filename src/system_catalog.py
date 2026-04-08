from __future__ import annotations
"""Live system catalog / ontology derived from canonical NEXO sources."""

import ast
import importlib.util
import inspect
import json
import os
import re
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

_DOC_ARG_LINE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$")


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


def _annotation_text_from_ast(node: ast.AST | None) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:
        return ""


def _literal_text(value) -> str:
    if value is inspect._empty:
        return ""
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if value is None:
        return "None"
    return repr(value)


def _default_text_from_ast(node: ast.AST | None) -> str:
    if node is None:
        return ""
    try:
        return _literal_text(ast.literal_eval(node))
    except Exception:
        try:
            return ast.unparse(node)
        except Exception:
            return "..."


def _annotation_text(annotation) -> str:
    if annotation is inspect._empty:
        return ""
    if isinstance(annotation, str):
        return annotation
    if getattr(annotation, "__module__", "") == "builtins" and hasattr(annotation, "__name__"):
        return str(annotation.__name__)
    text = str(annotation)
    return text.replace("typing.", "")


def _parse_arg_docs(doc: str) -> dict[str, str]:
    docs: dict[str, str] = {}
    if not doc.strip():
        return docs
    in_args = False
    current_arg = ""
    for raw_line in doc.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if stripped in {"Args:", "Arguments:"}:
            in_args = True
            current_arg = ""
            continue
        if not in_args:
            continue
        if stripped and not raw_line.startswith((" ", "\t")) and stripped.endswith(":"):
            break
        if not stripped:
            current_arg = ""
            continue
        match = _DOC_ARG_LINE_RE.match(raw_line)
        if match:
            current_arg = match.group(1)
            docs[current_arg] = match.group(2).strip()
            continue
        if current_arg:
            docs[current_arg] = f"{docs[current_arg]} {stripped}".strip()
    return docs


def _build_signature(name: str, params: list[dict], return_annotation: str = "") -> str:
    pieces: list[str] = []
    for param in params:
        part = param["name"]
        if param.get("annotation"):
            part += f": {param['annotation']}"
        if not param.get("required", False):
            part += f" = {param.get('default', '')}"
        pieces.append(part)
    signature = f"{name}({', '.join(pieces)})"
    if return_annotation:
        signature += f" -> {return_annotation}"
    return signature


def _example_value_for_param(param: dict) -> str:
    name = str(param.get("name", "value"))
    annotation = str(param.get("annotation", "")).lower()
    if name == "id" or name.endswith("_id"):
        return '"..."'
    if name.endswith("_token") or name == "read_token":
        return '"TOKEN"'
    if "bool" in annotation:
        return "True"
    if "int" in annotation:
        return "1"
    if "float" in annotation:
        return "1.0"
    if "list" in annotation or name.endswith("s"):
        return '["..."]'
    if "dict" in annotation or "object" in annotation:
        return '{"key": "value"}'
    return '"..."'


def _generic_example(name: str, params: list[dict]) -> str:
    required = [param for param in params if param.get("required", False)]
    if not required:
        return f"{name}()"
    pieces = [f"{param['name']}={_example_value_for_param(param)}" for param in required]
    return f"{name}({', '.join(pieces)})"


def _ast_params_for_node(node: ast.FunctionDef, arg_docs: dict[str, str]) -> list[dict]:
    params: list[dict] = []
    positional = list(node.args.posonlyargs) + list(node.args.args)
    positional_defaults = [None] * (len(positional) - len(node.args.defaults)) + list(node.args.defaults)
    for arg_node, default_node in zip(positional, positional_defaults):
        if arg_node.arg in {"self", "cls"}:
            continue
        params.append(
            {
                "name": arg_node.arg,
                "annotation": _annotation_text_from_ast(arg_node.annotation),
                "required": default_node is None,
                "default": "" if default_node is None else _default_text_from_ast(default_node),
                "description": arg_docs.get(arg_node.arg, ""),
            }
        )
    for arg_node, default_node in zip(node.args.kwonlyargs, node.args.kw_defaults):
        if arg_node.arg in {"self", "cls"}:
            continue
        params.append(
            {
                "name": arg_node.arg,
                "annotation": _annotation_text_from_ast(arg_node.annotation),
                "required": default_node is None,
                "default": "" if default_node is None else _default_text_from_ast(default_node),
                "description": arg_docs.get(arg_node.arg, ""),
            }
        )
    return params


def _callable_params(func, arg_docs: dict[str, str]) -> list[dict]:
    params: list[dict] = []
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        return params
    for name, param in signature.parameters.items():
        if name in {"self", "cls"}:
            continue
        required = param.default is inspect._empty
        params.append(
            {
                "name": name,
                "annotation": _annotation_text(param.annotation),
                "required": required,
                "default": "" if required else _literal_text(param.default),
                "description": arg_docs.get(name, ""),
            }
        )
    return params


def _guide_for_tool(name: str) -> dict[str, list]:
    if name == "nexo_learning_add":
        return {
            "workflow": [
                "Usa `applies_to` si quieres que el guard recuerde este learning antes de tocar un archivo, directorio o patrón concreto.",
                "Usa `priority` (`critical`, `high`, `medium`, `low`) para marcar severidad operativa.",
            ],
            "examples": [
                {
                    "title": "Learning mínimo",
                    "code": 'nexo_learning_add(category="shopify", title="Hacer pull antes de editar", content="Siempre sincronizar antes de editar el tema live.")',
                },
                {
                    "title": "Learning ligado a archivo o patrón",
                    "code": 'nexo_learning_add(category="recambios-bmw", title="Pull antes de editar theme", content="El admin puede tocar JSONs live.", applies_to="/abs/path/templates/product.json,templates/*.json,sections/*.liquid", prevention="Ejecutar `shopify theme pull` antes de editar.", priority="high")',
                },
            ],
            "common_errors": [
                "Usar `severity` en vez de `priority`.",
                "Olvidar `title`, que es obligatorio.",
                "No poner `applies_to` cuando quieres que el warning salte antes de tocar archivos concretos.",
            ],
        }
    if name == "nexo_learning_update":
        return {
            "workflow": [
                "Úsalo para completar o endurecer un learning existente cuando descubres nuevos archivos afectados, mejor `prevention` o prioridad distinta.",
            ],
            "examples": [
                {
                    "title": "Añadir alcance a un learning existente",
                    "code": 'nexo_learning_update(id=57, applies_to="/abs/path/file.py,src/plugins/*.py", prevention="Leer schema antes del primer uso", priority="high")',
                },
            ],
            "common_errors": [
                "Intentar recrear el learning desde cero cuando basta con actualizar el existente.",
            ],
        }
    if name == "nexo_reminder_get":
        return {
            "workflow": [
                "Devuelve el `READ_TOKEN` necesario para `update`, `delete`, `restore` y `note` sobre ese reminder.",
            ],
            "examples": [
                {
                    "title": "Leer reminder y obtener token",
                    "code": 'nexo_reminder_get(id="R87")',
                },
            ],
            "common_errors": [
                "Intentar editar o borrar un reminder sin llamar antes a `nexo_reminder_get`.",
            ],
        }
    if name in {"nexo_reminder_update", "nexo_reminder_delete", "nexo_reminder_restore", "nexo_reminder_note"}:
        return {
            "workflow": [
                "Primero llama `nexo_reminder_get(id=\"R87\")` para obtener `READ_TOKEN`.",
                f"Luego reutiliza ese `READ_TOKEN` en `{name}(...)`.",
            ],
            "examples": [
                {
                    "title": "1. Obtener token",
                    "code": 'nexo_reminder_get(id="R87")',
                },
                {
                    "title": "2. Reutilizar READ_TOKEN",
                    "code": f'{name}(id="R87", read_token="TOKEN")',
                },
            ],
            "common_errors": [
                "Llamar a esta tool sin `READ_TOKEN` válido.",
                "Usar un `READ_TOKEN` de otro reminder o de una lectura antigua.",
            ],
        }
    if name == "nexo_followup_get":
        return {
            "workflow": [
                "Devuelve el `READ_TOKEN` necesario para `update`, `delete`, `restore` y `note` sobre ese followup.",
            ],
            "examples": [
                {
                    "title": "Leer followup y obtener token",
                    "code": 'nexo_followup_get(id="NF45")',
                },
            ],
            "common_errors": [
                "Intentar editar o borrar un followup sin llamar antes a `nexo_followup_get`.",
            ],
        }
    if name in {"nexo_followup_update", "nexo_followup_delete", "nexo_followup_restore", "nexo_followup_note"}:
        return {
            "workflow": [
                "Primero llama `nexo_followup_get(id=\"NF45\")` para obtener `READ_TOKEN`.",
                f"Luego reutiliza ese `READ_TOKEN` en `{name}(...)`.",
            ],
            "examples": [
                {
                    "title": "1. Obtener token",
                    "code": 'nexo_followup_get(id="NF45")',
                },
                {
                    "title": "2. Reutilizar READ_TOKEN",
                    "code": f'{name}(id="NF45", read_token="TOKEN")',
                },
            ],
            "common_errors": [
                "Llamar a esta tool sin `READ_TOKEN` válido.",
                "Usar un `READ_TOKEN` de otro followup o de una lectura antigua.",
            ],
        }
    return {}


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
        arg_docs = _parse_arg_docs(doc)
        params = _ast_params_for_node(node, arg_docs)
        entries.append(
            {
                "kind": "core_tool",
                "name": node.name,
                "description": first_line,
                "doc": doc,
                "category": _tool_category(node.name),
                "path": str(SERVER_PATH),
                "line": int(getattr(node, "lineno", 0) or 0),
                "params": params,
                "signature": _build_signature(
                    node.name,
                    params,
                    _annotation_text_from_ast(node.returns),
                ),
                "quick_example": _generic_example(node.name, params),
                "source": "core",
            }
        )
    return entries


def _plugin_module_tools(filename: str, created_by: str) -> list[dict]:
    module_name = f"plugins.{filename[:-3]}"
    module = sys.modules.get(module_name)
    if module is None:
        plugin_dir = PLUGINS_DIR if created_by == "repo" else PERSONAL_PLUGINS_DIR
        path = Path(plugin_dir) / filename
        if not path.is_file():
            return []
        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                return []
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception:
            return []
    tools = getattr(module, "TOOLS", []) or []
    result: list[dict] = []
    for item in tools:
        try:
            func, name, description = item
        except Exception:
            continue
        doc = inspect.getdoc(func) or ""
        arg_docs = _parse_arg_docs(doc)
        params = _callable_params(func, arg_docs)
        try:
            return_annotation = _annotation_text(inspect.signature(func).return_annotation)
        except (TypeError, ValueError):
            return_annotation = ""
        result.append(
            {
                "kind": "plugin_tool",
                "name": str(name),
                "description": str(description or ""),
                "doc": doc,
                "params": params,
                "signature": _build_signature(
                    str(name),
                    params,
                    return_annotation,
                ),
                "quick_example": _generic_example(str(name), params),
                "plugin": filename,
                "source": created_by,
                "category": _tool_category(str(name)),
            }
        )
    return result


def _plugin_entries() -> list[dict]:
    rows = list_plugins()
    entries: list[dict] = []
    for row in rows:
        filename = str(row.get("filename") or "")
        created_by = str(row.get("created_by") or row.get("source") or "repo")
        plugin_tools = _plugin_module_tools(filename, created_by)
        if plugin_tools:
            entries.extend(plugin_tools)
            continue
        names = str(row.get("tool_names") or "").split(",")
        for name in [n.strip() for n in names if n.strip()]:
            entries.append(
                {
                    "kind": "plugin_tool",
                    "name": name,
                    "description": "",
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
    candidates = [clean]
    if clean.startswith("mcp__nexo__"):
        candidates.append(clean.split("mcp__nexo__", 1)[1])
    if "__" in clean:
        candidates.append(clean.split("__")[-1])
    seen: set[str] = set()
    for candidate in [item for item in candidates if item and not (item in seen or seen.add(item))]:
        exact = search_system_catalog(candidate, limit=200)
        for row in exact:
            if _normalize_text(row.get("name")) == candidate:
                return row
        for row in exact:
            if candidate in _normalize_text(row.get("name")):
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
    params = entry.get("params") or []
    required = [param for param in params if param.get("required")]
    optional = [param for param in params if not param.get("required")]
    guide = _guide_for_tool(str(entry.get("name") or ""))
    examples = [{"title": "Quick example", "code": entry["quick_example"]}] if entry.get("quick_example") else []
    examples.extend(guide.get("examples", []))
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
    if entry.get("signature"):
        lines.append(f"Signature: {entry['signature']}")
    if required:
        lines.append("Required args:")
        for param in required:
            detail = param.get("description") or "No description."
            annotation = f" ({param['annotation']})" if param.get("annotation") else ""
            lines.append(f"- {param['name']}{annotation}: {detail}")
    if optional:
        lines.append("Optional args:")
        for param in optional:
            detail = param.get("description") or "Optional."
            annotation = f" ({param['annotation']})" if param.get("annotation") else ""
            default = f" Default: {param['default']}." if param.get("default", "") != "" else ""
            lines.append(f"- {param['name']}{annotation}: {detail}{default}")
    if guide.get("workflow"):
        lines.append("Workflow notes:")
        for item in guide["workflow"]:
            lines.append(f"- {item}")
    if examples:
        lines.append("Examples:")
        for example in examples:
            title = str(example.get("title") or "").strip()
            code = str(example.get("code") or "").strip()
            if title:
                lines.append(f"- {title}")
            if code:
                lines.append(f"  {code}")
    if guide.get("common_errors"):
        lines.append("Common errors:")
        for item in guide["common_errors"]:
            lines.append(f"- {item}")
    return "\n".join(lines)
