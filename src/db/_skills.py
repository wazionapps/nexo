from __future__ import annotations
"""NEXO DB — Skills module.

Skill Auto-Creation system: reusable procedures extracted from complex tasks.
Skills are procedural (step-by-step how-tos) vs learnings which are declarative.

Pipeline: trace → draft → published → stable → archived.
Executable skills are indexed in SQLite but sourced from filesystem definitions.
"""

import datetime
import json
import os
import paths
import re
import shutil
from pathlib import Path

from db._core import get_db
from db._fts import fts_search, fts_upsert


# ── Paths ──────────────────────────────────────────────────────────

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
NEXO_CODE = Path(os.environ.get("NEXO_CODE", str(Path(__file__).resolve().parents[1])))

NEXO_ROOT = NEXO_CODE.parent
PERSONAL_SKILLS_DIR = paths.personal_skills_dir()


def _resolve_core_skills_dir() -> Path:
    """Keep packaged core skills separate from personal skills.

    In development NEXO_CODE points at repo/src, so core skills live in src/skills.
    In packaged installs the runtime wrapper points NEXO_CODE at NEXO_HOME, so core
    skills must live in a dedicated skills-core/ directory to avoid colliding with
    personal skills in NEXO_HOME/skills.
    """
    try:
        if NEXO_CODE.resolve() == NEXO_HOME.resolve():
            return NEXO_CODE / "skills-core"
    except OSError:
        pass
    return NEXO_CODE / "skills"


CORE_SKILLS_DIR = _resolve_core_skills_dir()
COMMUNITY_SKILLS_DIR = NEXO_ROOT / "community" / "skills"
RUNTIME_SKILLS_DIR = NEXO_HOME / "skills-runtime"


# ── Constants ──────────────────────────────────────────────────────

VALID_LEVELS = {"trace", "draft", "published", "stable", "archived"}
VALID_MODES = {"guide", "execute", "hybrid"}
VALID_EXECUTION_LEVELS = {"none", "read-only", "local", "remote"}
VALID_SOURCE_KINDS = {"personal", "core", "community"}
AUTO_APPROVER = "system:auto"

TRUST_ON_SUCCESS = 5
TRUST_ON_FAILURE = -10
TRUST_INITIAL = 50
TRUST_ARCHIVE_THRESHOLD = 20
PROMOTION_USES_REQUIRED = 2
DEFAULT_STABLE_AFTER_USES = 10
OUTCOME_SKILL_PROMOTION_MIN_RESOLVED = 4
OUTCOME_SKILL_STABLE_MIN_RESOLVED = 6
OUTCOME_SKILL_RETIRE_MIN_RESOLVED = 4
OUTCOME_SKILL_PROMOTION_SUCCESS_RATE = 0.75
OUTCOME_SKILL_STABLE_SUCCESS_RATE = 0.85
OUTCOME_SKILL_RETIRE_MAX_SUCCESS_RATE = 0.25
OUTCOME_SKILL_DEPRIORITIZE_MAX_SUCCESS_RATE = 0.5

SKILL_DEFINITION_FILENAME = "skill.json"
SOURCE_PRIORITY = {"community": 1, "core": 2, "personal": 3}
LEVEL_PRIORITY = {"trace": 0, "draft": 1, "published": 2, "stable": 3, "archived": 4}


# ── Helpers ────────────────────────────────────────────────────────

def _now_text() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _normalize_level(value: str | None) -> str:
    level = (value or "trace").strip().lower()
    return level if level in VALID_LEVELS else "trace"


def _normalize_mode(value: str | None, *, has_script: bool = False, has_content: bool = False) -> str:
    mode = (value or "").strip().lower()
    if mode in VALID_MODES:
        return mode
    if has_script and has_content:
        return "hybrid"
    if has_script:
        return "execute"
    return "guide"


def _normalize_execution_level(value: str | None) -> str:
    execution_level = (value or "none").strip().lower()
    return execution_level if execution_level in VALID_EXECUTION_LEVELS else "none"


def _normalize_source_kind(value: str | None) -> str:
    source_kind = (value or "personal").strip().lower()
    return source_kind if source_kind in VALID_SOURCE_KINDS else "personal"


def _json_string(value, default):
    if value in ("", None):
        value = default
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, str):
        return value
    return json.dumps(value if value is not None else default, ensure_ascii=False)


def _json_list(value) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return []
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            return [value]
    return []


def _json_dict(value) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return {}
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return {}
    return {}


def _sync_dirs() -> list[tuple[str, Path]]:
    return [
        ("community", COMMUNITY_SKILLS_DIR),
        ("core", CORE_SKILLS_DIR),
        ("personal", PERSONAL_SKILLS_DIR),
    ]


def _ensure_skill_dirs():
    PERSONAL_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    RUNTIME_SKILLS_DIR.mkdir(parents=True, exist_ok=True)


def _safe_slug(value: str) -> str:
    chars = []
    for ch in value.lower():
        if ch.isalnum():
            chars.append(ch)
        elif ch in {"-", "_"}:
            chars.append("-")
    slug = "".join(chars).strip("-")
    return slug or "skill"


def _normalize_match_token(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").strip().lower()).strip()


def _outcome_skill_id_from_candidate(candidate: dict) -> str:
    raw_parts = [
        candidate.get("area", ""),
        candidate.get("task_type", ""),
        candidate.get("goal_profile_id", ""),
        candidate.get("selected_choice", ""),
    ]
    chunks = []
    for part in raw_parts:
        cleaned = re.sub(r"[^A-Z0-9]+", "-", str(part or "").upper()).strip("-")
        if cleaned:
            chunks.append(cleaned[:12])
    suffix = "-".join(chunks[:4]) or "GENERAL"
    return f"SK-OUTCOME-{suffix}"


def _skill_outcome_pattern_match(skill: dict, candidate: dict) -> list[str]:
    matched_via: list[str] = []
    direct_skill_id = _outcome_skill_id_from_candidate(candidate)
    if skill.get("id") == direct_skill_id:
        matched_via.append("pattern_id")

    selected_choice = _normalize_match_token(candidate.get("selected_choice", ""))
    if selected_choice:
        triggers = {
            _normalize_match_token(item)
            for item in _json_list(skill.get("trigger_patterns", "[]"))
            if _normalize_match_token(item)
        }
        if selected_choice in triggers:
            matched_via.append("trigger_pattern")

    return matched_via


def _recommend_skill_outcome_action(skill: dict, *, resolved: int, success_rate: float) -> tuple[str, str]:
    level = str(skill.get("level") or "").strip().lower()
    stable_after = int(skill.get("stable_after_uses", DEFAULT_STABLE_AFTER_USES) or DEFAULT_STABLE_AFTER_USES)
    stable_threshold = max(OUTCOME_SKILL_STABLE_MIN_RESOLVED, min(stable_after, 8))

    if resolved >= OUTCOME_SKILL_RETIRE_MIN_RESOLVED and success_rate <= OUTCOME_SKILL_RETIRE_MAX_SUCCESS_RATE:
        return "retire", "Sustained poor outcome evidence suggests the skill should be archived."
    if level == "draft" and resolved >= OUTCOME_SKILL_PROMOTION_MIN_RESOLVED and success_rate >= OUTCOME_SKILL_PROMOTION_SUCCESS_RATE:
        return "promote_published", "Repeated successful outcomes justify promoting the draft skill to published."
    if level == "published" and resolved >= stable_threshold and success_rate >= OUTCOME_SKILL_STABLE_SUCCESS_RATE:
        return "promote_stable", "Strong sustained success outcomes justify promoting the skill to stable."
    if level in {"draft", "published", "stable"} and resolved >= OUTCOME_SKILL_RETIRE_MIN_RESOLVED and success_rate < OUTCOME_SKILL_DEPRIORITIZE_MAX_SUCCESS_RATE:
        return "deprioritize", "Mixed or weak recent outcomes suggest lowering this skill in ranking until evidence improves."
    return "observe", "Not enough sustained evidence yet to change the lifecycle."


def _skill_outcome_ranking_weight(*, resolved: int, success_rate: float) -> float:
    if resolved <= 0:
        return 0.0
    magnitude = min(resolved, 8)
    return round((success_rate - 0.5) * magnitude, 3)


def _preserve_level(existing_level: str, requested_level: str) -> str:
    clean_existing = _normalize_level(existing_level)
    clean_requested = _normalize_level(requested_level)
    if clean_existing == "archived":
        return "archived"
    if LEVEL_PRIORITY.get(clean_existing, 0) > LEVEL_PRIORITY.get(clean_requested, 0):
        return clean_existing
    return clean_requested


def _resolve_approval(mode: str, execution_level: str, approval_required=0, approved_at: str = "", approved_by: str = "") -> tuple[int, str, str]:
    """Skills are now fully autonomous: executable modes are auto-approved."""
    normalized_mode = _normalize_mode(mode)
    normalized_level = _normalize_execution_level(execution_level)
    if normalized_mode == "guide" or normalized_level == "none":
        return 0, approved_at or "", approved_by or ""
    return 0, approved_at or _now_text(), approved_by or AUTO_APPROVER


def _skill_fts_body(skill: dict) -> str:
    parts = [
        skill.get("description", ""),
        skill.get("tags", "[]"),
        skill.get("trigger_patterns", "[]"),
        skill.get("content", ""),
    ]
    return " ".join(str(p) for p in parts if p)


def _definition_script_path(skill_dir: Path, definition: dict) -> Path | None:
    entry = str(definition.get("executable_entry", "") or "").strip()
    if entry:
        candidate = (skill_dir / entry).resolve()
        if candidate.is_file():
            return candidate

    for default_name in ("script.py", "script.sh"):
        candidate = skill_dir / default_name
        if candidate.is_file():
            return candidate
    return None


def _stage_skill_script(skill_id: str, script_source: Path | None) -> str:
    if script_source is None or not script_source.is_file():
        return ""

    _ensure_skill_dirs()
    runtime_dir = RUNTIME_SKILLS_DIR / _safe_slug(skill_id)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    target = runtime_dir / script_source.name
    shutil.copy2(script_source, target)
    try:
        target.chmod(0o755)
    except OSError:
        pass
    return str(target)


def _load_skill_definition(skill_dir: Path, source_kind: str) -> dict | None:
    definition_path = skill_dir / SKILL_DEFINITION_FILENAME
    if not definition_path.is_file():
        return None

    data = json.loads(definition_path.read_text())
    skill_id = str(data.get("id", "")).strip()
    name = str(data.get("name", "")).strip()
    if not skill_id or not name:
        return None

    guide_path = skill_dir / "guide.md"
    content = data.get("content", "")
    if guide_path.is_file():
        content = guide_path.read_text()

    script_source = _definition_script_path(skill_dir, data)
    file_path = _stage_skill_script(skill_id, script_source)

    mode = _normalize_mode(
        data.get("mode", ""),
        has_script=bool(file_path),
        has_content=bool(content),
    )
    execution_level = _normalize_execution_level(data.get("execution_level", "none"))
    if mode == "guide":
        execution_level = "none"
    approval_required, approved_at, approved_by = _resolve_approval(
        mode,
        execution_level,
        approval_required=data.get("approval_required", execution_level in {"local", "remote"}),
        approved_at=str(data.get("approved_at", "") or ""),
        approved_by=str(data.get("approved_by", "") or ""),
    )
    params_schema = _json_dict(data.get("params_schema", {}))
    command_template = _json_dict(data.get("command_template", {}))
    steps = _json_list(data.get("steps", []))
    gotchas = _json_list(data.get("gotchas", []))

    return {
        "id": skill_id,
        "name": name,
        "description": str(data.get("description", "") or ""),
        "level": _normalize_level(data.get("level", "published")),
        "mode": mode,
        "source_kind": _normalize_source_kind(source_kind),
        "execution_level": execution_level,
        "approval_required": approval_required,
        "approved_at": approved_at,
        "approved_by": approved_by,
        "tags": _json_string(data.get("tags", []), []),
        "trigger_patterns": _json_string(data.get("trigger_patterns", []), []),
        "source_sessions": _json_string(data.get("source_sessions", []), []),
        "linked_learnings": _json_string(data.get("linked_learnings", []), []),
        "trust_score": int(data.get("trust_score", TRUST_INITIAL) or TRUST_INITIAL),
        "file_path": file_path,
        "definition_path": str(definition_path),
        "content": str(content or ""),
        "steps": _json_string(steps, []),
        "gotchas": _json_string(gotchas, []),
        "params_schema": _json_string(params_schema, {}),
        "command_template": _json_string(command_template, {}),
        "executable_entry": str(data.get("executable_entry", script_source.name if script_source else "") or ""),
        "stable_after_uses": int(data.get("stable_after_uses", DEFAULT_STABLE_AFTER_USES) or DEFAULT_STABLE_AFTER_USES),
    }


def _upsert_filesystem_skill(skill: dict) -> dict:
    conn = get_db()
    existing_row = conn.execute("SELECT * FROM skills WHERE id = ?", (skill["id"],)).fetchone()
    existing = dict(existing_row) if existing_row else {}

    level = _preserve_level(existing.get("level", ""), skill["level"])
    trust_score = existing.get("trust_score", skill["trust_score"])
    approval_required, approved_at, approved_by = _resolve_approval(
        skill["mode"],
        skill["execution_level"],
        approval_required=existing.get("approval_required", skill["approval_required"]) if existing else skill["approval_required"],
        approved_at=existing.get("approved_at") or skill.get("approved_at", ""),
        approved_by=existing.get("approved_by") or skill.get("approved_by", ""),
    )

    values = {
        **skill,
        "level": level,
        "trust_score": trust_score,
        "approved_at": approved_at,
        "approved_by": approved_by,
        "approval_required": approval_required,
    }

    conn.execute(
        """INSERT INTO skills (
               id, name, description, level, trust_score, file_path, tags,
               trigger_patterns, source_sessions, linked_learnings, content, steps, gotchas,
               mode, source_kind, execution_level, approval_required, approved_at, approved_by,
               params_schema, command_template, executable_entry, stable_after_uses, definition_path
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
               name = excluded.name,
               description = excluded.description,
               level = ?,
               file_path = excluded.file_path,
               tags = excluded.tags,
               trigger_patterns = excluded.trigger_patterns,
               source_sessions = excluded.source_sessions,
               linked_learnings = excluded.linked_learnings,
               content = excluded.content,
               steps = excluded.steps,
               gotchas = excluded.gotchas,
               mode = excluded.mode,
               source_kind = excluded.source_kind,
               execution_level = excluded.execution_level,
               approval_required = excluded.approval_required,
               approved_at = ?,
               approved_by = ?,
               params_schema = excluded.params_schema,
               command_template = excluded.command_template,
               executable_entry = excluded.executable_entry,
               stable_after_uses = excluded.stable_after_uses,
               definition_path = excluded.definition_path,
               updated_at = datetime('now')""",
        (
            values["id"], values["name"], values["description"], values["level"], values["trust_score"],
            values["file_path"], values["tags"], values["trigger_patterns"], values["source_sessions"],
            values["linked_learnings"], values["content"], values["steps"], values["gotchas"],
            values["mode"], values["source_kind"], values["execution_level"], values["approval_required"],
            values["approved_at"], values["approved_by"], values["params_schema"], values["command_template"],
            values["executable_entry"], values["stable_after_uses"], values["definition_path"],
            values["level"], values["approved_at"], values["approved_by"],
        ),
    )
    conn.commit()

    row = conn.execute("SELECT * FROM skills WHERE id = ?", (skill["id"],)).fetchone()
    result = dict(row) if row else dict(values)
    fts_upsert("skill", result["id"], result.get("name", ""), _skill_fts_body(result), "skill")
    return result


def _definition_priority(source_kind: str) -> int:
    return SOURCE_PRIORITY.get(source_kind, 0)


# ── CRUD ───────────────────────────────────────────────────────────

def create_skill(
    skill_id: str,
    name: str,
    description: str = "",
    level: str = "trace",
    tags: list | str = "[]",
    trigger_patterns: list | str = "[]",
    source_sessions: list | str = "[]",
    linked_learnings: list | str = "[]",
    file_path: str = "",
    trust_score: int = TRUST_INITIAL,
    steps: list | str = "[]",
    gotchas: list | str = "[]",
    content: str = "",
    mode: str = "",
    source_kind: str = "personal",
    execution_level: str = "none",
    approval_required: bool | int = False,
    approved_at: str = "",
    approved_by: str = "",
    params_schema: dict | str = "{}",
    command_template: dict | str = "{}",
    executable_entry: str = "",
    stable_after_uses: int = DEFAULT_STABLE_AFTER_USES,
    definition_path: str = "",
) -> dict:
    """Create a new skill entry."""
    level = _normalize_level(level)
    tags_json = _json_string(tags, [])
    trigger_json = _json_string(trigger_patterns, [])
    sessions_json = _json_string(source_sessions, [])
    learnings_json = _json_string(linked_learnings, [])
    steps_json = _json_string(steps, [])
    gotchas_json = _json_string(gotchas, [])
    params_json = _json_string(params_schema, {})
    command_json = _json_string(command_template, {})

    if not content and _json_list(steps_json):
        steps_list = _json_list(steps_json)
        gotchas_list = _json_list(gotchas_json)
        lines = [f"# {name}", "", description, "", "## Steps"]
        for index, step in enumerate(steps_list, 1):
            lines.append(f"{index}. {step}")
        if gotchas_list:
            lines.extend(["", "## Gotchas"])
            for gotcha in gotchas_list:
                lines.append(f"- {gotcha}")
        content = "\n".join(lines)

    source_kind = _normalize_source_kind(source_kind)
    mode = _normalize_mode(mode, has_script=bool(file_path), has_content=bool(content))
    execution_level = _normalize_execution_level(execution_level)
    if mode == "guide":
        execution_level = "none"
    approval_required, approved_at, approved_by = _resolve_approval(
        mode,
        execution_level,
        approval_required=approval_required,
        approved_at=approved_at,
        approved_by=approved_by,
    )

    conn = get_db()
    conn.execute(
        """INSERT INTO skills (
               id, name, description, level, trust_score, file_path, tags,
               trigger_patterns, source_sessions, linked_learnings, content, steps, gotchas,
               mode, source_kind, execution_level, approval_required, approved_at, approved_by,
               params_schema, command_template, executable_entry, stable_after_uses, definition_path
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            skill_id, name, description, level, trust_score, file_path, tags_json,
            trigger_json, sessions_json, learnings_json, content, steps_json, gotchas_json,
            mode, source_kind, execution_level, approval_required, approved_at, approved_by,
            params_json, command_json, executable_entry, stable_after_uses, definition_path,
        ),
    )
    conn.commit()

    row = conn.execute("SELECT * FROM skills WHERE id = ?", (skill_id,)).fetchone()
    result = dict(row) if row else {"id": skill_id, "status": "created"}
    fts_upsert("skill", skill_id, name, _skill_fts_body(result), "skill")
    return result


def get_skill(skill_id: str) -> dict | None:
    conn = get_db()
    row = conn.execute("SELECT * FROM skills WHERE id = ?", (skill_id,)).fetchone()
    return dict(row) if row else None


def list_skills(level: str = "", tag: str = "", source_kind: str = "") -> list[dict]:
    conn = get_db()
    conditions = []
    params = []

    if level:
        conditions.append("level = ?")
        params.append(level)
    if tag:
        conditions.append("tags LIKE ?")
        params.append(f'%"{tag}"%')
    if source_kind:
        conditions.append("source_kind = ?")
        params.append(source_kind)

    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    rows = conn.execute(
        f"""SELECT * FROM skills {where}
            ORDER BY CASE level WHEN 'stable' THEN 0 WHEN 'published' THEN 1
                                WHEN 'draft' THEN 2 WHEN 'trace' THEN 3 ELSE 4 END,
                     trust_score DESC, last_used_at DESC""",
        tuple(params),
    ).fetchall()
    return [dict(row) for row in rows]


def search_skills(query: str, level: str = "", source_kind: str = "") -> list[dict]:
    fts_results = fts_search(query, source_filter="skill", limit=20)
    if fts_results:
        conn = get_db()
        ids = [result["source_id"] for result in fts_results]
        placeholders = ",".join("?" * len(ids))
        sql = f"SELECT * FROM skills WHERE id IN ({placeholders})"
        params = list(ids)
        if level:
            sql += " AND level = ?"
            params.append(level)
        if source_kind:
            sql += " AND source_kind = ?"
            params.append(source_kind)
        sql += " ORDER BY trust_score DESC"
        rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    conn = get_db()
    words = query.strip().split()
    if not words:
        return []

    conditions = []
    params = []
    for word in words:
        pattern = f"%{word}%"
        conditions.append("(name LIKE ? OR description LIKE ? OR tags LIKE ? OR trigger_patterns LIKE ? OR content LIKE ?)")
        params.extend([pattern, pattern, pattern, pattern, pattern])

    where = " AND ".join(conditions)
    if level:
        where = f"level = ? AND ({where})"
        params.insert(0, level)
    if source_kind:
        where = f"source_kind = ? AND ({where})"
        params.insert(0 if not level else 1, source_kind)

    rows = conn.execute(
        f"SELECT * FROM skills WHERE {where} ORDER BY trust_score DESC",
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def update_skill(skill_id: str, **kwargs) -> dict:
    conn = get_db()
    row = conn.execute("SELECT * FROM skills WHERE id = ?", (skill_id,)).fetchone()
    if not row:
        return {"error": f"Skill {skill_id} not found"}

    allowed = {
        "name", "description", "level", "trust_score", "file_path", "tags",
        "trigger_patterns", "source_sessions", "linked_learnings", "content",
        "steps", "gotchas", "mode", "source_kind", "execution_level",
        "approval_required", "approved_at", "approved_by", "params_schema",
        "command_template", "executable_entry", "stable_after_uses", "definition_path",
    }
    updates = {}
    for key, value in kwargs.items():
        if key not in allowed:
            continue
        if key in {"tags", "trigger_patterns", "source_sessions", "linked_learnings", "steps", "gotchas"}:
            updates[key] = _json_string(value, [])
        elif key in {"params_schema", "command_template"}:
            updates[key] = _json_string(value, {})
        elif key == "level":
            updates[key] = _normalize_level(value)
        elif key == "mode":
            updates[key] = _normalize_mode(value)
        elif key == "source_kind":
            updates[key] = _normalize_source_kind(value)
        elif key == "execution_level":
            updates[key] = _normalize_execution_level(value)
        elif key == "approval_required":
            updates[key] = int(bool(value))
        else:
            updates[key] = value

    effective_mode = updates.get("mode", row["mode"])
    effective_execution_level = updates.get("execution_level", row["execution_level"])
    if effective_mode == "guide":
        effective_execution_level = "none"
        updates["execution_level"] = "none"

    approval_required, approved_at, approved_by = _resolve_approval(
        effective_mode,
        effective_execution_level,
        approval_required=updates.get("approval_required", row["approval_required"]),
        approved_at=updates.get("approved_at", row["approved_at"] or ""),
        approved_by=updates.get("approved_by", row["approved_by"] or ""),
    )
    updates["approval_required"] = approval_required
    updates["approved_at"] = approved_at
    updates["approved_by"] = approved_by

    if not updates:
        return dict(row)

    updates["updated_at"] = _now_text()
    set_clause = ", ".join(f"{key} = ?" for key in updates)
    values = list(updates.values()) + [skill_id]
    conn.execute(f"UPDATE skills SET {set_clause} WHERE id = ?", values)
    conn.commit()

    refreshed = dict(conn.execute("SELECT * FROM skills WHERE id = ?", (skill_id,)).fetchone())
    fts_upsert("skill", skill_id, refreshed.get("name", ""), _skill_fts_body(refreshed), "skill")
    return refreshed


def delete_skill(skill_id: str) -> bool:
    conn = get_db()
    row = conn.execute("SELECT file_path FROM skills WHERE id = ?", (skill_id,)).fetchone()
    conn.execute("DELETE FROM skill_usage WHERE skill_id = ?", (skill_id,))
    result = conn.execute("DELETE FROM skills WHERE id = ?", (skill_id,))
    conn.execute("DELETE FROM unified_search WHERE source = 'skill' AND source_id = ?", (skill_id,))
    conn.commit()

    if row and row["file_path"]:
        path = Path(row["file_path"])
        if path.is_file() and RUNTIME_SKILLS_DIR in path.parents:
            path.unlink(missing_ok=True)
            try:
                path.parent.rmdir()
            except OSError:
                pass
    return result.rowcount > 0


# ── Usage tracking & promotion ─────────────────────────────────────

def record_usage(skill_id: str, session_id: str = "", success: bool = True,
                 context: str = "", notes: str = "") -> dict:
    conn = get_db()
    row = conn.execute("SELECT * FROM skills WHERE id = ?", (skill_id,)).fetchone()
    if not row:
        return {"error": f"Skill {skill_id} not found"}

    skill = dict(row)
    conn.execute(
        "INSERT INTO skill_usage (skill_id, session_id, success, context, notes) VALUES (?, ?, ?, ?, ?)",
        (skill_id, session_id, 1 if success else 0, context, notes),
    )

    delta = TRUST_ON_SUCCESS if success else TRUST_ON_FAILURE
    new_trust = max(0, min(100, skill["trust_score"] + delta))
    count_field = "success_count" if success else "fail_count"

    conn.execute(
        f"""UPDATE skills SET
               use_count = use_count + 1,
               {count_field} = {count_field} + 1,
               trust_score = ?,
               last_used_at = datetime('now'),
               updated_at = datetime('now')
           WHERE id = ?""",
        (new_trust, skill_id),
    )
    conn.commit()

    promotion = None
    outcome_review = get_skill_outcome_evidence(skill_id)
    allow_publish_promotion = not outcome_review.get("has_evidence") or outcome_review.get("recommended_action") in {
        "promote_published",
        "promote_stable",
    }
    allow_stable_promotion = not outcome_review.get("has_evidence") or outcome_review.get("recommended_action") == "promote_stable"
    refreshed = dict(conn.execute("SELECT * FROM skills WHERE id = ?", (skill_id,)).fetchone())
    if skill["level"] == "draft" and success and allow_publish_promotion:
        distinct_contexts = conn.execute(
            """SELECT COUNT(DISTINCT context) FROM skill_usage
               WHERE skill_id = ? AND success = 1 AND context != ''""",
            (skill_id,),
        ).fetchone()[0]
        if distinct_contexts >= PROMOTION_USES_REQUIRED:
            conn.execute(
                "UPDATE skills SET level = 'published', updated_at = datetime('now') WHERE id = ?",
                (skill_id,),
            )
            conn.commit()
            promotion = "draft → published"

    refreshed = dict(conn.execute("SELECT * FROM skills WHERE id = ?", (skill_id,)).fetchone())
    if refreshed["level"] == "published" and success and allow_stable_promotion:
        stable_after = int(refreshed.get("stable_after_uses", DEFAULT_STABLE_AFTER_USES) or DEFAULT_STABLE_AFTER_USES)
        if refreshed["success_count"] >= stable_after and refreshed["fail_count"] == 0:
            conn.execute(
                "UPDATE skills SET level = 'stable', last_reviewed_at = datetime('now'), updated_at = datetime('now') WHERE id = ?",
                (skill_id,),
            )
            conn.commit()
            promotion = "published → stable"

    refreshed = dict(conn.execute("SELECT * FROM skills WHERE id = ?", (skill_id,)).fetchone())
    if outcome_review.get("recommended_action") == "retire" and refreshed["level"] in {"draft", "published", "stable"}:
        conn.execute(
            "UPDATE skills SET level = 'archived', updated_at = datetime('now') WHERE id = ?",
            (skill_id,),
        )
        conn.commit()
        promotion = f"{refreshed['level']} → archived (poor outcome evidence)"

    refreshed = dict(conn.execute("SELECT * FROM skills WHERE id = ?", (skill_id,)).fetchone())
    if new_trust < TRUST_ARCHIVE_THRESHOLD and refreshed["level"] in {"draft", "published", "stable"}:
        conn.execute(
            "UPDATE skills SET level = 'archived', updated_at = datetime('now') WHERE id = ?",
            (skill_id,),
        )
        conn.commit()
        promotion = f"{refreshed['level']} → archived (trust={new_trust})"

    result = dict(conn.execute("SELECT * FROM skills WHERE id = ?", (skill_id,)).fetchone())
    if promotion:
        result["_promotion"] = promotion
    return result


def match_skills(task: str, level: str = "", top_n: int = 3) -> list[dict]:
    if not task or not task.strip():
        return []

    conn = get_db()
    seen = set()
    results = []
    level_filter = "AND level = ?" if level else "AND level IN ('draft', 'published', 'stable')"
    level_params = (level,) if level else ()

    fts_results = fts_search(task, source_filter="skill", limit=10)
    if fts_results:
        ids = [result["source_id"] for result in fts_results]
        placeholders = ",".join("?" * len(ids))
        rows = conn.execute(
            f"""SELECT * FROM skills WHERE id IN ({placeholders}) {level_filter}
                ORDER BY CASE source_kind WHEN 'personal' THEN 0 WHEN 'core' THEN 1 ELSE 2 END,
                         CASE level WHEN 'stable' THEN 0 WHEN 'published' THEN 1 ELSE 2 END,
                         trust_score DESC""",
            tuple(ids) + level_params,
        ).fetchall()
        for row in rows:
            skill = dict(row)
            skill["_match"] = "fts"
            if skill["id"] not in seen:
                seen.add(skill["id"])
                results.append(skill)

    task_lower = task.lower()
    rows = conn.execute(
        f"SELECT * FROM skills WHERE trigger_patterns != '[]' {level_filter}",
        level_params,
    ).fetchall()
    for row in rows:
        skill = dict(row)
        if skill["id"] in seen:
            continue
        for pattern in _json_list(skill.get("trigger_patterns", "[]")):
            if pattern.lower() in task_lower or task_lower in pattern.lower():
                skill["_match"] = f"trigger:{pattern}"
                seen.add(skill["id"])
                results.append(skill)
                break

    task_words = set(task_lower.split())
    rows = conn.execute(
        f"SELECT * FROM skills WHERE tags != '[]' {level_filter}",
        level_params,
    ).fetchall()
    for row in rows:
        skill = dict(row)
        if skill["id"] in seen:
            continue
        tags = {tag.lower() for tag in _json_list(skill.get("tags", "[]"))}
        overlap = task_words & tags
        if overlap:
            skill["_match"] = f"tags:{','.join(sorted(overlap))}"
            seen.add(skill["id"])
            results.append(skill)

    for skill in results:
        review = get_skill_outcome_evidence(skill["id"])
        skill["_outcome_review"] = review
        skill["_outcome_rank"] = float(review.get("ranking_weight") or 0.0)

    results.sort(
        key=lambda skill: (
            0 if skill.get("source_kind") == "personal" else 1 if skill.get("source_kind") == "core" else 2,
            0 if skill.get("level") == "stable" else 1 if skill.get("level") == "published" else 2,
            -float(skill.get("_outcome_rank", 0.0)),
            -int(skill.get("trust_score", 0)),
        )
    )
    return results[:top_n]


def merge_skills(id1: str, id2: str, keep_id: str = "") -> dict:
    conn = get_db()
    s1 = conn.execute("SELECT * FROM skills WHERE id = ?", (id1,)).fetchone()
    s2 = conn.execute("SELECT * FROM skills WHERE id = ?", (id2,)).fetchone()
    if not s1:
        return {"error": f"Skill {id1} not found"}
    if not s2:
        return {"error": f"Skill {id2} not found"}

    s1, s2 = dict(s1), dict(s2)
    if not keep_id:
        keep_id = id1 if s1["trust_score"] >= s2["trust_score"] else id2
    survivor = s1 if keep_id == id1 else s2
    donor = s2 if keep_id == id1 else s1

    merged_tags = json.dumps(sorted(set(_json_list(survivor.get("tags", "[]"))) | set(_json_list(donor.get("tags", "[]")))))
    merged_triggers = json.dumps(sorted(set(_json_list(survivor.get("trigger_patterns", "[]"))) | set(_json_list(donor.get("trigger_patterns", "[]")))))
    merged_sessions = json.dumps(sorted(set(_json_list(survivor.get("source_sessions", "[]"))) | set(_json_list(donor.get("source_sessions", "[]"))), key=str))
    merged_learnings = json.dumps(sorted(set(_json_list(survivor.get("linked_learnings", "[]"))) | set(_json_list(donor.get("linked_learnings", "[]"))), key=str))

    conn.execute(
        """UPDATE skills SET
               tags = ?, trigger_patterns = ?, source_sessions = ?, linked_learnings = ?,
               use_count = ?, success_count = ?, fail_count = ?, trust_score = ?, updated_at = datetime('now')
           WHERE id = ?""",
        (
            merged_tags,
            merged_triggers,
            merged_sessions,
            merged_learnings,
            survivor["use_count"] + donor["use_count"],
            survivor["success_count"] + donor["success_count"],
            survivor["fail_count"] + donor["fail_count"],
            max(survivor["trust_score"], donor["trust_score"]),
            keep_id,
        ),
    )
    conn.execute("UPDATE skill_usage SET skill_id = ? WHERE skill_id = ?", (keep_id, donor["id"]))
    conn.execute("DELETE FROM skills WHERE id = ?", (donor["id"],))
    conn.execute("DELETE FROM unified_search WHERE source = 'skill' AND source_id = ?", (donor["id"],))
    conn.commit()

    result = dict(conn.execute("SELECT * FROM skills WHERE id = ?", (keep_id,)).fetchone())
    fts_upsert("skill", keep_id, result.get("name", ""), _skill_fts_body(result), "skill")
    result["_merged_from"] = donor["id"]
    return result


def get_skill_stats() -> dict:
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM skills").fetchone()[0]
    by_level = {}
    for row in conn.execute("SELECT level, COUNT(*) as cnt FROM skills GROUP BY level").fetchall():
        by_level[row["level"]] = row["cnt"]

    avg_trust = conn.execute(
        "SELECT AVG(trust_score) FROM skills WHERE level != 'archived'"
    ).fetchone()[0] or 0
    total_uses = conn.execute("SELECT COUNT(*) FROM skill_usage").fetchone()[0]
    success_rate = 0
    if total_uses > 0:
        successes = conn.execute("SELECT COUNT(*) FROM skill_usage WHERE success = 1").fetchone()[0]
        success_rate = round(successes / total_uses * 100, 1)
    recent_uses = conn.execute(
        "SELECT COUNT(*) FROM skill_usage WHERE created_at >= datetime('now', '-7 days')"
    ).fetchone()[0]
    outcome_reviews = list_skill_outcome_reviews(limit=max(total, 1), actionable_only=False)
    outcome_backed = [item for item in outcome_reviews if item.get("has_evidence")]
    avg_outcome_success = 0.0
    if outcome_backed:
        avg_outcome_success = round(
            sum(float(item.get("success_rate") or 0.0) for item in outcome_backed) / len(outcome_backed) * 100,
            1,
        )

    return {
        "total": total,
        "by_level": by_level,
        "avg_trust": round(avg_trust, 1),
        "total_uses": total_uses,
        "success_rate": success_rate,
        "uses_last_7d": recent_uses,
        "skill_reuse_rate": round(total_uses / max(total, 1), 1),
        "outcome_backed_skills": len(outcome_backed),
        "outcome_backed_success_rate": avg_outcome_success,
        "promoted_from_evidence_count": sum(
            1
            for item in outcome_backed
            if item["recommended_action"] in {"promote_published", "promote_stable"}
            and item.get("level") in {"published", "stable"}
        ),
        "retired_for_poor_outcomes_count": sum(
            1
            for item in outcome_backed
            if item["recommended_action"] == "retire" and item.get("level") == "archived"
        ),
    }


def get_featured_skills(limit: int = 5) -> list[dict]:
    sync_skill_directories()
    conn = get_db()
    rows = conn.execute(
        """SELECT * FROM skills
           WHERE level IN ('published', 'stable')
           ORDER BY CASE source_kind WHEN 'personal' THEN 0 WHEN 'core' THEN 1 ELSE 2 END,
                    CASE level WHEN 'stable' THEN 0 ELSE 1 END,
                    trust_score DESC,
                    COALESCE(last_used_at, created_at) DESC
           LIMIT ?""",
        (max(5, int(limit) * 4),),
    ).fetchall()
    featured = []
    for row in rows:
        skill = dict(row)
        review = get_skill_outcome_evidence(skill["id"])
        skill["_outcome_review"] = review
        skill["_outcome_rank"] = float(review.get("ranking_weight") or 0.0)
        featured.append(skill)

    featured.sort(
        key=lambda skill: (
            0 if skill.get("source_kind") == "personal" else 1 if skill.get("source_kind") == "core" else 2,
            0 if skill.get("level") == "stable" else 1,
            -float(skill.get("_outcome_rank", 0.0)),
            -int(skill.get("trust_score", 0)),
            skill.get("name", ""),
        )
    )
    return featured[: max(1, int(limit))]


def get_skill_outcome_evidence(skill_id: str, *, pattern_limit: int = 200) -> dict:
    skill = get_skill(skill_id)
    if not skill:
        return {"error": f"Skill {skill_id} not found"}

    from db._outcomes import list_outcome_pattern_candidates

    candidates = list_outcome_pattern_candidates(limit=max(20, int(pattern_limit)))
    matches = []
    for candidate in candidates:
        matched_via = _skill_outcome_pattern_match(skill, candidate)
        if not matched_via:
            continue
        matches.append(
            {
                "pattern_key": candidate["pattern_key"],
                "candidate_type": candidate["candidate_type"],
                "selected_choice": candidate["selected_choice"],
                "context_label": candidate["context_label"],
                "resolved_outcomes": int(candidate["resolved_outcomes"]),
                "met": int(candidate["met"]),
                "missed": int(candidate["missed"]),
                "success_rate": float(candidate["success_rate"]),
                "matched_via": matched_via,
                "evidence": candidate.get("evidence", [])[:3],
            }
        )

    if not matches:
        return {
            "skill_id": skill_id,
            "has_evidence": False,
            "level": skill.get("level", ""),
            "resolved_outcomes": 0,
            "met": 0,
            "missed": 0,
            "success_rate": None,
            "matched_patterns": [],
            "recommended_action": "observe",
            "recommended_reason": "No comparable outcome patterns are linked to this skill yet.",
            "supports_promotion": False,
            "supports_retirement": False,
            "ranking_weight": 0.0,
        }

    resolved = sum(item["resolved_outcomes"] for item in matches)
    met = sum(item["met"] for item in matches)
    missed = sum(item["missed"] for item in matches)
    success_rate = round(met / resolved, 3) if resolved else 0.0
    recommended_action, recommended_reason = _recommend_skill_outcome_action(
        skill,
        resolved=resolved,
        success_rate=success_rate,
    )
    ranking_weight = _skill_outcome_ranking_weight(resolved=resolved, success_rate=success_rate)
    if recommended_action == "retire":
        ranking_weight = min(ranking_weight, -3.0)
    elif recommended_action == "promote_stable":
        ranking_weight = max(ranking_weight, 3.0)
    elif recommended_action == "promote_published":
        ranking_weight = max(ranking_weight, 2.0)

    matches.sort(
        key=lambda item: (
            0 if "pattern_id" in item["matched_via"] else 1,
            -item["resolved_outcomes"],
            item["selected_choice"],
        )
    )
    return {
        "skill_id": skill_id,
        "has_evidence": True,
        "level": skill.get("level", ""),
        "resolved_outcomes": resolved,
        "met": met,
        "missed": missed,
        "success_rate": success_rate,
        "matched_patterns": matches,
        "recommended_action": recommended_action,
        "recommended_reason": recommended_reason,
        "supports_promotion": recommended_action in {"promote_published", "promote_stable"},
        "supports_retirement": recommended_action == "retire",
        "ranking_weight": ranking_weight,
    }


def list_skill_outcome_reviews(*, limit: int = 20, actionable_only: bool = False) -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        """SELECT id FROM skills
           WHERE level IN ('draft', 'published', 'stable', 'archived')
           ORDER BY CASE level WHEN 'stable' THEN 0 WHEN 'published' THEN 1 WHEN 'draft' THEN 2 ELSE 3 END,
                    trust_score DESC,
                    COALESCE(last_used_at, created_at) DESC""",
    ).fetchall()

    reviews = []
    for row in rows:
        review = get_skill_outcome_evidence(row["id"])
        if review.get("error") or not review.get("has_evidence"):
            continue
        if actionable_only and review["recommended_action"] == "observe":
            continue
        reviews.append(review)

    priority = {
        "retire": 0,
        "promote_stable": 1,
        "promote_published": 2,
        "deprioritize": 3,
        "observe": 4,
    }
    reviews.sort(
        key=lambda item: (
            priority.get(item["recommended_action"], 9),
            -int(item["resolved_outcomes"]),
            item["skill_id"],
        )
    )
    return reviews[: max(1, int(limit))]


def get_skill_execution_spec(skill_id: str) -> dict:
    skill = get_skill(skill_id)
    if not skill:
        return {"error": f"Skill {skill_id} not found"}
    return {
        "id": skill["id"],
        "mode": _normalize_mode(skill.get("mode", ""), has_script=bool(skill.get("file_path")), has_content=bool(skill.get("content"))),
        "execution_level": _normalize_execution_level(skill.get("execution_level", "none")),
        "approval_required": bool(skill.get("approval_required", 0)),
        "approved_at": skill.get("approved_at", ""),
        "file_path": skill.get("file_path", ""),
        "params_schema": _json_dict(skill.get("params_schema", "{}")),
        "command_template": _json_dict(skill.get("command_template", "{}")),
    }


def resolve_skill_paths(skill: dict) -> dict:
    return {
        "definition_path": skill.get("definition_path", ""),
        "file_path": skill.get("file_path", ""),
        "executable_entry": skill.get("executable_entry", ""),
    }


def validate_skill_params(skill: dict, params: dict | str | None) -> dict:
    params = _json_dict(params or {})
    schema = _json_dict(skill.get("params_schema", "{}"))
    resolved = dict(params)
    errors = []

    for name, spec in schema.items():
        if not isinstance(spec, dict):
            errors.append(f"params_schema.{name} is not an object")
            continue
        required = bool(spec.get("required"))
        if name not in resolved and "default" in spec:
            resolved[name] = spec["default"]
        if required and name not in resolved:
            errors.append(f"Missing required param: {name}")
            continue
        if name not in resolved:
            continue
        value = resolved[name]
        expected_type = spec.get("type", "")
        if expected_type == "string" and not isinstance(value, str):
            errors.append(f"Param {name} must be a string")
        elif expected_type == "integer" and not isinstance(value, int):
            errors.append(f"Param {name} must be an integer")
        elif expected_type == "number" and not isinstance(value, (int, float)):
            errors.append(f"Param {name} must be a number")
        elif expected_type == "boolean" and not isinstance(value, bool):
            errors.append(f"Param {name} must be a boolean")

        enum = spec.get("enum")
        if enum and value not in enum:
            errors.append(f"Param {name} must be one of: {', '.join(str(item) for item in enum)}")

    return {"ok": not errors, "params": resolved, "errors": errors}


def render_command_template(skill: dict, params: dict | str | None) -> dict:
    validation = validate_skill_params(skill, params)
    if not validation["ok"]:
        return validation

    template = _json_dict(skill.get("command_template", "{}"))
    argv_template = template.get("argv") or []
    resolved_params = {
        **validation["params"],
        "file_path": skill.get("file_path", ""),
        "skill_id": skill.get("id", ""),
        "skill_name": skill.get("name", ""),
    }

    def render_token(token: str):
        rendered = token
        for key, value in resolved_params.items():
            rendered = rendered.replace(f"{{{{{key}}}}}", str(value))
        if rendered == token and token.startswith("{{") and token.endswith("}}"):
            return ""
        return rendered

    if not argv_template:
        file_path = skill.get("file_path", "")
        argv = [file_path] if file_path else []
    else:
        argv = []
        for item in argv_template:
            if not isinstance(item, str):
                continue
            rendered = render_token(item)
            if rendered != "":
                argv.append(rendered)

    return {"ok": True, "params": resolved_params, "argv": argv}


def sync_skill_directories() -> dict:
    _ensure_skill_dirs()
    discovered = {}
    issues = []

    for source_kind, root in _sync_dirs():
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            try:
                definition = _load_skill_definition(child, source_kind)
            except Exception as exc:
                issues.append(f"{child}: {exc}")
                continue
            if not definition:
                continue
            existing = discovered.get(definition["id"])
            if not existing or _definition_priority(source_kind) >= _definition_priority(existing["source_kind"]):
                discovered[definition["id"]] = definition

    synced = []
    for skill in discovered.values():
        synced.append(_upsert_filesystem_skill(skill)["id"])

    return {"synced": len(synced), "ids": sorted(synced), "issues": issues}


def import_skill_from_directory(path: str, source_kind: str = "personal") -> dict:
    skill_dir = Path(path)
    definition = _load_skill_definition(skill_dir, _normalize_source_kind(source_kind))
    if not definition:
        return {"error": f"No {SKILL_DEFINITION_FILENAME} found in {skill_dir}"}
    return _upsert_filesystem_skill(definition)


def approve_skill(skill_id: str, execution_level: str = "", approved_by: str = "") -> dict:
    skill = get_skill(skill_id)
    if not skill:
        return {"error": f"Skill {skill_id} not found"}

    updates = {
        "approved_at": _now_text(),
        "approved_by": approved_by or skill.get("approved_by", "") or AUTO_APPROVER,
        "approval_required": 0,
    }
    if execution_level:
        updates["execution_level"] = _normalize_execution_level(execution_level)
    return update_skill(skill_id, **updates)


def collect_scriptable_skill_candidates() -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        """SELECT * FROM skills
           WHERE file_path = ''
             AND level IN ('draft', 'published', 'stable')
             AND success_count >= 3
           ORDER BY trust_score DESC, success_count DESC""",
    ).fetchall()

    candidates = []
    for row in rows:
        skill = dict(row)
        text = " ".join(
            [
                skill.get("name", ""),
                skill.get("description", ""),
                skill.get("content", ""),
                skill.get("trigger_patterns", ""),
            ]
        ).lower()
        if any(word in text for word in ("deploy", "ssh", "server", "remote", "api call")):
            suggested = "remote"
        elif any(word in text for word in ("edit", "commit", "patch", "write", "refactor", "fix")):
            suggested = "local"
        else:
            suggested = "read-only"

        candidates.append(
            {
                "id": skill["id"],
                "name": skill["name"],
                "description": skill.get("description", ""),
                "content": skill.get("content", ""),
                "steps": _json_list(skill.get("steps", "[]")),
                "gotchas": _json_list(skill.get("gotchas", "[]")),
                "trigger_patterns": _json_list(skill.get("trigger_patterns", "[]")),
                "source_sessions": _json_list(skill.get("source_sessions", "[]")),
                "suggested_mode": "hybrid",
                "suggested_execution_level": suggested,
                "success_count": skill["success_count"],
                "trust_score": skill["trust_score"],
            }
        )
    return candidates


def collect_skill_improvement_candidates() -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        """SELECT skill_id,
                  SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS failures,
                  SUM(CASE WHEN notes != '' THEN 1 ELSE 0 END) AS noted_runs
           FROM skill_usage
           GROUP BY skill_id
           HAVING failures > 0 OR noted_runs > 0
           ORDER BY failures DESC, noted_runs DESC""",
    ).fetchall()

    candidates = []
    for row in rows:
        skill = get_skill(row["skill_id"])
        if not skill:
            continue
        candidates.append(
            {
                "id": skill["id"],
                "name": skill["name"],
                "failures": row["failures"],
                "noted_runs": row["noted_runs"],
                "trust_score": skill["trust_score"],
            }
        )
    return candidates


def materialize_personal_skill_definition(skill_data: dict) -> dict:
    """Write a personal skill definition to NEXO_HOME/skills and sync it into DB."""
    _ensure_skill_dirs()
    skill_id = str(skill_data.get("id", "")).strip()
    name = str(skill_data.get("name", "")).strip()
    if not skill_id or not name:
        return {"error": "skill_data requires id and name"}

    skill_dir = PERSONAL_SKILLS_DIR / _safe_slug(skill_id)
    skill_dir.mkdir(parents=True, exist_ok=True)

    metadata = {
        "id": skill_id,
        "name": name,
        "description": str(skill_data.get("description", "") or ""),
        "level": _normalize_level(skill_data.get("level", "draft")),
        "mode": _normalize_mode(
            skill_data.get("mode", ""),
            has_script=bool(skill_data.get("script_body") or skill_data.get("executable_entry")),
            has_content=bool(skill_data.get("content") or skill_data.get("steps")),
        ),
        "source_kind": "personal",
        "execution_level": _normalize_execution_level(skill_data.get("execution_level", "none")),
        "approval_required": False,
        "approved_at": str(skill_data.get("approved_at", "") or ""),
        "approved_by": str(skill_data.get("approved_by", "") or ""),
        "tags": _json_list(skill_data.get("tags", [])),
        "trigger_patterns": _json_list(skill_data.get("trigger_patterns", [])),
        "source_sessions": _json_list(skill_data.get("source_sessions", [])),
        "linked_learnings": _json_list(skill_data.get("linked_learnings", [])),
        "steps": _json_list(skill_data.get("steps", [])),
        "gotchas": _json_list(skill_data.get("gotchas", [])),
        "params_schema": _json_dict(skill_data.get("params_schema", {})),
        "command_template": _json_dict(skill_data.get("command_template", {})),
        "stable_after_uses": int(skill_data.get("stable_after_uses", DEFAULT_STABLE_AFTER_USES) or DEFAULT_STABLE_AFTER_USES),
    }

    executable_entry = str(skill_data.get("executable_entry", "") or "").strip()
    script_body = str(skill_data.get("script_body", "") or "")
    if script_body and not executable_entry:
        executable_entry = "script.py"
    if executable_entry:
        metadata["executable_entry"] = executable_entry

    approval_required, approved_at, approved_by = _resolve_approval(
        metadata["mode"],
        metadata["execution_level"],
        approval_required=metadata["approval_required"],
        approved_at=metadata["approved_at"],
        approved_by=metadata["approved_by"],
    )
    metadata["approval_required"] = bool(approval_required)
    metadata["approved_at"] = approved_at
    metadata["approved_by"] = approved_by

    guide_content = str(skill_data.get("content", "") or "")
    if not guide_content:
        steps = metadata["steps"]
        gotchas = metadata["gotchas"]
        lines = [f"# {name}", "", metadata["description"], "", "## Steps"]
        for index, step in enumerate(steps, 1):
            lines.append(f"{index}. {step}")
        if gotchas:
            lines.extend(["", "## Gotchas"])
            for gotcha in gotchas:
                lines.append(f"- {gotcha}")
        guide_content = "\n".join(lines).strip() + "\n"

    (skill_dir / SKILL_DEFINITION_FILENAME).write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n")
    (skill_dir / "guide.md").write_text(guide_content)
    if script_body and executable_entry:
        script_path = skill_dir / executable_entry
        script_path.write_text(script_body)
        try:
            script_path.chmod(0o755)
        except OSError:
            pass

    return import_skill_from_directory(str(skill_dir), source_kind="personal")


def get_skill_health_report(fix: bool = False) -> dict:
    if fix:
        sync_skill_directories()

    conn = get_db()
    rows = conn.execute("SELECT * FROM skills ORDER BY id").fetchall()
    issues = []
    checked = 0
    for row in rows:
        skill = dict(row)
        checked += 1
        mode = _normalize_mode(skill.get("mode", ""), has_script=bool(skill.get("file_path")), has_content=bool(skill.get("content")))
        execution_level = _normalize_execution_level(skill.get("execution_level", "none"))

        if skill.get("source_kind") in VALID_SOURCE_KINDS and skill.get("definition_path"):
            if not Path(skill["definition_path"]).is_file():
                issues.append({"severity": "error", "skill_id": skill["id"], "message": f"Definition missing: {skill['definition_path']}"})

        if mode in {"execute", "hybrid"}:
            file_path = skill.get("file_path", "")
            if not file_path:
                issues.append({"severity": "error", "skill_id": skill["id"], "message": "Executable skill without file_path"})
            elif not Path(file_path).is_file():
                issues.append({"severity": "error", "skill_id": skill["id"], "message": f"Script missing: {file_path}"})

            params_schema = _json_dict(skill.get("params_schema", "{}"))
            command_template = _json_dict(skill.get("command_template", "{}"))
            if skill.get("params_schema", "{}") and not isinstance(params_schema, dict):
                issues.append({"severity": "error", "skill_id": skill["id"], "message": "Invalid params_schema"})
            argv = command_template.get("argv")
            if command_template and argv is not None and not isinstance(argv, list):
                issues.append({"severity": "error", "skill_id": skill["id"], "message": "command_template.argv must be a list"})

    return {"checked": checked, "issues": issues}


def decay_unused_skills(dry_run: bool = False) -> dict:
    conn = get_db()
    actions = {"decayed": [], "archived": [], "purged": []}

    rows = conn.execute(
        """SELECT * FROM skills WHERE level = 'draft'
           AND (last_used_at IS NULL OR last_used_at < datetime('now', '-30 days'))
           AND created_at < datetime('now', '-30 days')"""
    ).fetchall()
    for row in rows:
        if not dry_run:
            conn.execute(
                "UPDATE skills SET level = 'archived', trust_score = 0, updated_at = datetime('now') WHERE id = ?",
                (row["id"],),
            )
        actions["archived"].append(row["id"])

    rows = conn.execute(
        """SELECT * FROM skills WHERE level IN ('published', 'stable')
           AND (last_used_at IS NULL OR last_used_at < datetime('now', '-90 days'))"""
    ).fetchall()
    for row in rows:
        new_trust = max(0, row["trust_score"] - 5)
        if not dry_run:
            conn.execute(
                "UPDATE skills SET trust_score = ?, updated_at = datetime('now') WHERE id = ?",
                (new_trust, row["id"]),
            )
            if new_trust < TRUST_ARCHIVE_THRESHOLD:
                conn.execute(
                    "UPDATE skills SET level = 'archived', updated_at = datetime('now') WHERE id = ?",
                    (row["id"],),
                )
                actions["archived"].append(row["id"])
        actions["decayed"].append({"id": row["id"], "trust": f"{row['trust_score']} → {new_trust}"})

    rows = conn.execute(
        """SELECT * FROM skills WHERE level = 'archived'
           AND (last_used_at IS NULL OR last_used_at < datetime('now', '-60 days'))
           AND updated_at < datetime('now', '-60 days')"""
    ).fetchall()
    for row in rows:
        if not dry_run:
            conn.execute("DELETE FROM skill_usage WHERE skill_id = ?", (row["id"],))
            conn.execute("DELETE FROM skills WHERE id = ?", (row["id"],))
            conn.execute("DELETE FROM unified_search WHERE source = 'skill' AND source_id = ?", (row["id"],))
        actions["purged"].append(row["id"])

    if not dry_run:
        conn.commit()
    return actions
