from __future__ import annotations

"""Managed bootstrap documents for Claude Code and Codex."""

import json
import os
import re
from pathlib import Path

from client_preferences import (
    BACKEND_NONE,
    CLIENT_CLAUDE_CODE,
    CLIENT_CODEX,
    INTERACTIVE_CLIENT_KEYS,
    normalize_backend_key,
    normalize_client_key,
    normalize_client_preferences,
)

def _resolve_templates_dir(module_file: str | os.PathLike[str]) -> Path:
    module_dir = Path(module_file).resolve().parent
    direct = module_dir / "templates"
    if direct.is_dir():
        return direct
    parent = module_dir.parent / "templates"
    if parent.is_dir():
        return parent
    return direct


TEMPLATES_DIR = _resolve_templates_dir(__file__)

CORE_LABEL = "******CORE******"
USER_LABEL = "******USER******"
CORE_START = "<!-- nexo:core:start -->"
CORE_END = "<!-- nexo:core:end -->"
USER_START = "<!-- nexo:user:start -->"
USER_END = "<!-- nexo:user:end -->"

BOOTSTRAP_SPECS = {
    CLIENT_CLAUDE_CODE: {
        "label": "Claude Code",
        "template": TEMPLATES_DIR / "CLAUDE.md.template",
        "target_parts": (".claude", "CLAUDE.md"),
        "version_pattern": r"nexo-claude-md-version:\s*([\d.]+)",
        "version_file": "claude_md_version.txt",
    },
    CLIENT_CODEX: {
        "label": "Codex",
        "template": TEMPLATES_DIR / "CODEX.AGENTS.md.template",
        "target_parts": (".codex", "AGENTS.md"),
        "version_pattern": r"nexo-codex-agents-version:\s*([\d.]+)",
        "version_file": "codex_agents_version.txt",
    },
}


def _user_home() -> Path:
    return Path(os.environ.get("HOME", str(Path.home()))).expanduser()


def _default_nexo_home() -> Path:
    return Path(os.environ.get("NEXO_HOME", str(_user_home() / ".nexo"))).expanduser()


def _resolve_operator_name(nexo_home: Path, explicit: str = "") -> str:
    explicit = (explicit or "").strip()
    if explicit:
        return explicit
    env_name = os.environ.get("NEXO_NAME", "").strip()
    if env_name:
        return env_name
    version_file = nexo_home / "version.json"
    if version_file.is_file():
        try:
            candidate = str(json.loads(version_file.read_text()).get("operator_name", "")).strip()
            if candidate:
                return candidate
        except Exception:
            pass
    return "NEXO"


def _read_version(text: str, pattern: str) -> str:
    match = re.search(pattern, text)
    return match.group(1) if match else ""


def _template_version_comment(text: str) -> str:
    first_line = text.splitlines()[0] if text else ""
    return first_line if first_line.startswith("<!--") and "version:" in first_line else ""


def _strip_version_comment(text: str) -> str:
    return re.sub(r"^<!--\s*nexo-[^-]+(?:-[^-]+)*-version:\s*[\d.]+\s*-->\s*\n?", "", text, count=1)


def _extract_block(text: str, start: str, end: str) -> str:
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.DOTALL)
    match = pattern.search(text)
    return match.group(0) if match else ""


def _replace_block(text: str, start: str, end: str, replacement: str) -> str:
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.DOTALL)
    if pattern.search(text):
        return pattern.sub(replacement, text, count=1)
    return text.rstrip() + "\n\n" + replacement.rstrip() + "\n"


def _build_user_block(user_payload: str, template_text: str) -> str:
    template_user = _extract_block(template_text, USER_START, USER_END)
    if not user_payload.strip():
        return template_user
    return f"{USER_START}\n{user_payload.rstrip()}\n{USER_END}"


def _legacy_user_payload(existing_text: str) -> str:
    cleaned = re.sub(r"<!--\s*nexo-[^-]+-[^-]+-version:\s*[\d.]+\s*-->\s*", "", existing_text)
    if "<!-- nexo:start:" in cleaned:
        first_section = re.search(r"<!-- nexo:start:\w+ -->", cleaned)
        prefix = cleaned[: first_section.start()] if first_section else ""
        remainder = cleaned[first_section.start() :] if first_section else ""
        remainder = re.sub(
            r"<!-- nexo:start:\w+ -->.*?<!-- nexo:end:\w+ -->",
            "",
            remainder,
            flags=re.DOTALL,
        )
        residue = "\n".join([prefix, remainder]).strip()
        filtered_lines: list[str] = []
        for line in residue.splitlines():
            stripped = line.strip()
            if not stripped:
                filtered_lines.append("")
                continue
            if stripped.startswith("# ") and "Cognitive Co-Operator" in stripped:
                continue
            if stripped.startswith("I am ") and "powered by NEXO Brain" in stripped:
                continue
            if "Tool-coupled behavioral rules" in stripped:
                continue
            filtered_lines.append(line)
        residue = "\n".join(filtered_lines).strip()
        return residue
    return cleaned.strip()


def _target_path(client: str, *, user_home: Path | None = None) -> Path:
    spec = BOOTSTRAP_SPECS[client]
    home = user_home or _user_home()
    return home.joinpath(*spec["target_parts"])


def _version_tracker_path(nexo_home: Path, client: str) -> Path:
    return nexo_home / "data" / BOOTSTRAP_SPECS[client]["version_file"]


def render_bootstrap_template(
    client: str,
    *,
    nexo_home: str | os.PathLike[str] | None = None,
    operator_name: str = "",
) -> str:
    client_key = normalize_client_key(client)
    spec = BOOTSTRAP_SPECS[client_key]
    template_text = spec["template"].read_text()
    nexo_home_path = Path(nexo_home).expanduser() if nexo_home else _default_nexo_home()
    name = _resolve_operator_name(nexo_home_path, explicit=operator_name)
    return (
        template_text
        .replace("{{NAME}}", name)
        .replace("{{NEXO_HOME}}", str(nexo_home_path))
    )


def load_bootstrap_prompt(
    client: str,
    *,
    nexo_home: str | os.PathLike[str] | None = None,
    operator_name: str = "",
    user_home: str | os.PathLike[str] | None = None,
) -> str:
    client_key = normalize_client_key(client)
    if client_key not in BOOTSTRAP_SPECS:
        return ""

    home_path = Path(user_home).expanduser() if user_home else _user_home()
    target_path = _target_path(client_key, user_home=home_path)
    if target_path.exists():
        text = target_path.read_text()
        if text.strip():
            return _strip_version_comment(text).strip()

    rendered = render_bootstrap_template(
        client_key,
        nexo_home=nexo_home,
        operator_name=operator_name,
    )
    return _strip_version_comment(rendered).strip()


def sync_client_bootstrap(
    client: str,
    *,
    nexo_home: str | os.PathLike[str] | None = None,
    operator_name: str = "",
    user_home: str | os.PathLike[str] | None = None,
) -> dict:
    client_key = normalize_client_key(client)
    if client_key not in BOOTSTRAP_SPECS:
        return {"ok": False, "client": client_key or str(client), "error": "unsupported bootstrap target"}

    nexo_home_path = Path(nexo_home).expanduser() if nexo_home else _default_nexo_home()
    home_path = Path(user_home).expanduser() if user_home else _user_home()
    target_path = _target_path(client_key, user_home=home_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    rendered = render_bootstrap_template(
        client_key,
        nexo_home=nexo_home_path,
        operator_name=operator_name,
    )
    template_version = _read_version(rendered, BOOTSTRAP_SPECS[client_key]["version_pattern"])
    rendered_core = _extract_block(rendered, CORE_START, CORE_END)
    if not rendered_core:
        return {"ok": False, "client": client_key, "path": str(target_path), "error": "template missing CORE block"}

    if not target_path.exists() or not target_path.read_text().strip():
        target_path.write_text(rendered)
        if template_version:
            tracker = _version_tracker_path(nexo_home_path, client_key)
            tracker.parent.mkdir(parents=True, exist_ok=True)
            tracker.write_text(template_version)
        return {
            "ok": True,
            "client": client_key,
            "action": "created",
            "path": str(target_path),
            "version": template_version,
            "content": rendered,
        }

    existing = target_path.read_text()
    if CORE_START in existing and CORE_END in existing and USER_START in existing and USER_END in existing:
        user_block = _extract_block(existing, USER_START, USER_END) or _extract_block(rendered, USER_START, USER_END)
        updated = _replace_block(existing, CORE_START, CORE_END, rendered_core)
        updated = _replace_block(updated, USER_START, USER_END, user_block)
        action = "updated" if updated != existing else "unchanged"
    else:
        legacy_user = _legacy_user_payload(existing)
        updated = _replace_block(rendered, USER_START, USER_END, _build_user_block(legacy_user, rendered))
        action = "migrated"

    if template_version:
        comment = _template_version_comment(rendered)
        if comment:
            updated = re.sub(r"<!--\s*nexo-[^-]+-[^-]+-version:\s*[\d.]+\s*-->", comment, updated, count=1)
            if comment not in updated:
                updated = comment + "\n" + updated.lstrip()

    if updated != existing:
        backup_path = target_path.with_suffix(target_path.suffix + ".bak")
        try:
            backup_path.write_text(existing)
        except Exception:
            pass
        target_path.write_text(updated)

    if template_version:
        tracker = _version_tracker_path(nexo_home_path, client_key)
        tracker.parent.mkdir(parents=True, exist_ok=True)
        tracker.write_text(template_version)

    return {
        "ok": True,
        "client": client_key,
        "action": action,
        "path": str(target_path),
        "version": template_version,
        "content": updated,
    }


def sync_enabled_bootstraps(
    *,
    nexo_home: str | os.PathLike[str] | None = None,
    operator_name: str = "",
    user_home: str | os.PathLike[str] | None = None,
    enabled_clients: list[str] | tuple[str, ...] | set[str] | None = None,
    preferences: dict | None = None,
) -> dict[str, dict]:
    if enabled_clients is None:
        if preferences is None:
            enabled = {CLIENT_CLAUDE_CODE, CLIENT_CODEX}
        else:
            prefs = normalize_client_preferences(preferences)
            enabled = {
                key
                for key in INTERACTIVE_CLIENT_KEYS
                if prefs.get("interactive_clients", {}).get(key, False)
                and key in BOOTSTRAP_SPECS
            }
            backend = normalize_backend_key(prefs.get("automation_backend"))
            if prefs.get("automation_enabled", True) and backend and backend != BACKEND_NONE:
                if backend in BOOTSTRAP_SPECS:
                    enabled.add(backend)
            if not enabled:
                enabled.add(CLIENT_CLAUDE_CODE)
    else:
        enabled = {normalize_client_key(item) for item in enabled_clients if normalize_client_key(item) in BOOTSTRAP_SPECS}
        if not enabled:
            enabled = {CLIENT_CLAUDE_CODE}

    results: dict[str, dict] = {}
    for client_key in (CLIENT_CLAUDE_CODE, CLIENT_CODEX):
        if client_key not in enabled:
            results[client_key] = {
                "ok": True,
                "client": client_key,
                "skipped": True,
                "reason": "disabled in client preferences",
                "path": str(_target_path(client_key, user_home=Path(user_home).expanduser() if user_home else None)),
            }
            continue
        try:
            results[client_key] = sync_client_bootstrap(
                client_key,
                nexo_home=nexo_home,
                operator_name=operator_name,
                user_home=user_home,
            )
        except Exception as exc:
            results[client_key] = {"ok": False, "client": client_key, "error": str(exc)}
    return results


def get_bootstrap_status(
    client: str,
    *,
    nexo_home: str | os.PathLike[str] | None = None,
    user_home: str | os.PathLike[str] | None = None,
) -> dict:
    client_key = normalize_client_key(client)
    if client_key not in BOOTSTRAP_SPECS:
        return {"client": client_key or str(client), "supported": False}

    nexo_home_path = Path(nexo_home).expanduser() if nexo_home else _default_nexo_home()
    home_path = Path(user_home).expanduser() if user_home else _user_home()
    target_path = _target_path(client_key, user_home=home_path)
    template_text = render_bootstrap_template(client_key, nexo_home=nexo_home_path)
    template_version = _read_version(template_text, BOOTSTRAP_SPECS[client_key]["version_pattern"])
    status = {
        "client": client_key,
        "path": str(target_path),
        "exists": target_path.exists(),
        "supported": True,
        "template_version": template_version,
        "version": "",
        "markers_ok": False,
        "user_block_ok": False,
    }
    if not target_path.exists():
        return status

    text = target_path.read_text()
    status["version"] = _read_version(text, BOOTSTRAP_SPECS[client_key]["version_pattern"])
    status["markers_ok"] = CORE_START in text and CORE_END in text and USER_START in text and USER_END in text
    status["user_block_ok"] = USER_START in text and USER_END in text
    return status
