#!/usr/bin/env python3
from __future__ import annotations
"""
Deep Sleep v2 -- Phase 4: Apply synthesized findings.

Reads $DATE-synthesis.json and executes actions:
- learning_add: inserts learnings into nexo.db
- followup_create: inserts followups into nexo.db
- morning_briefing_item: writes to morning briefing file

All actions are idempotent (dedupe_key checked against last 7 days),
backed up before mutation, and logged to $DATE-applied.json.

Environment variables:
  NEXO_HOME  -- root of the NEXO installation (default: ~/.nexo)
"""
import hashlib
import json
import os
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
NEXO_CODE = Path(os.environ.get("NEXO_CODE", str(Path(__file__).resolve().parents[2])))
if str(NEXO_CODE) not in sys.path:
    sys.path.insert(0, str(NEXO_CODE))

import db as nexo_db

DEEP_SLEEP_DIR = NEXO_HOME / "operations" / "deep-sleep"
NEXO_DB = NEXO_HOME / "data" / "nexo.db"
COGNITIVE_DB = NEXO_HOME / "data" / "cognitive.db"
OPERATIONS_DIR = NEXO_HOME / "operations"
BACKUP_DIR = DEEP_SLEEP_DIR  # backups stored alongside outputs

STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "with", "for", "from", "into", "onto",
    "that", "this", "these", "those", "have", "has", "had", "will", "would",
    "could", "should", "must", "need", "needs", "your", "their", "there", "here",
    "about", "before", "after", "during", "through", "without", "within", "while",
    "que", "con", "para", "por", "los", "las", "una", "uno", "sobre", "desde",
    "cuando", "como", "pero", "todo", "toda", "cada", "into", "across", "using",
}
CONCRETE_ACTION_VERBS = {
    "add", "implement", "create", "write", "build", "introduce", "enforce",
    "automate", "validate", "check", "verify", "guard", "fix", "migrate",
    "review", "reconcile", "pin", "sync", "instrument",
}
NEGATION_PATTERNS = (
    "do not", "don't", "never", "avoid", "skip", "without", "forbid", "forbidden",
    "disable", "disabled", "remove", "ban", "bypass",
)
CONTRADICTION_PAIRS = (
    ("enable", "disable"),
    ("use", "avoid"),
    ("add", "remove"),
    ("allow", "forbid"),
    ("always", "never"),
    ("before", "after"),
    ("require", "skip"),
    ("validate", "bypass"),
    ("include", "exclude"),
)
TABLE_COLUMNS_CACHE: dict[tuple[str, str], set[str]] = {}


def generate_run_id(target_date: str) -> str:
    """Generate a unique run ID for this execution."""
    ts = datetime.now().strftime("%H%M%S")
    return f"{target_date}-{ts}"


def load_recent_dedupe_keys(target_date: str, days: int = 7) -> set[str]:
    """Load dedupe_keys from applied files in the last N days."""
    keys = set()
    base_date = datetime.strptime(target_date, "%Y-%m-%d")
    for i in range(days):
        d = (base_date - timedelta(days=i)).strftime("%Y-%m-%d")
        applied_file = DEEP_SLEEP_DIR / f"{d}-applied.json"
        if applied_file.exists():
            try:
                with open(applied_file) as f:
                    data = json.load(f)
                for action in data.get("applied_actions", []):
                    dk = action.get("dedupe_key", "")
                    if dk:
                        keys.add(dk)
            except (json.JSONDecodeError, KeyError):
                continue
    return keys


def backup_db(db_path: Path, run_id: str) -> Path | None:
    """Create a backup of a database before mutations."""
    if not db_path.exists():
        return None
    backup_path = BACKUP_DIR / f"{run_id}-backup-{db_path.name}"
    try:
        import shutil
        shutil.copy2(str(db_path), str(backup_path))
        return backup_path
    except Exception as e:
        print(f"  [apply] Warning: backup failed for {db_path.name}: {e}", file=sys.stderr)
        return None


def _table_columns(db_path: Path, table: str) -> set[str]:
    cache_key = (str(db_path), table)
    if cache_key in TABLE_COLUMNS_CACHE:
        return TABLE_COLUMNS_CACHE[cache_key]
    if not db_path.exists():
        TABLE_COLUMNS_CACHE[cache_key] = set()
        return set()
    try:
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        conn.close()
    except Exception:
        TABLE_COLUMNS_CACHE[cache_key] = set()
        return set()
    cols = {str(row[1]) for row in rows}
    TABLE_COLUMNS_CACHE[cache_key] = cols
    return cols


def _row_dict(row) -> dict:
    if row is None:
        return {}
    if isinstance(row, sqlite3.Row):
        return dict(row)
    return dict(zip(row.keys(), row)) if hasattr(row, "keys") else dict(row)


def _normalize_text(value: str) -> str:
    text = str(value or "").lower()
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[^a-z0-9_/\-\s]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _tokenize(value: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9_/-]+", _normalize_text(value))
    return [token for token in tokens if len(token) > 2 and token not in STOPWORDS]


def _text_similarity(left: str, right: str) -> float:
    normalized_left = _normalize_text(left)
    normalized_right = _normalize_text(right)
    if not normalized_left or not normalized_right:
        return 0.0
    if normalized_left == normalized_right:
        return 1.0

    left_tokens = set(_tokenize(normalized_left))
    right_tokens = set(_tokenize(normalized_right))
    shared = left_tokens & right_tokens
    if not shared:
        return SequenceMatcher(None, normalized_left, normalized_right).ratio()

    seq = SequenceMatcher(None, normalized_left, normalized_right).ratio()
    jaccard = len(shared) / len(left_tokens | right_tokens) if (left_tokens or right_tokens) else 0.0
    overlap = len(shared) / min(len(left_tokens), len(right_tokens)) if min(len(left_tokens), len(right_tokens)) else 0.0
    containment = (
        1.0
        if normalized_left in normalized_right or normalized_right in normalized_left
        else 0.0
    )
    return round(max((seq * 0.45) + (jaccard * 0.2) + (overlap * 0.35), overlap, (containment * 0.8) + (seq * 0.2)), 4)


def _is_concrete_action(text: str) -> bool:
    tokens = set(_tokenize(text))
    return bool(tokens & CONCRETE_ACTION_VERBS)


def _prefer_due_date(current_value, new_value) -> str:
    current = _parse_any_datetime(current_value)
    new = _parse_any_datetime(new_value)
    if new and (not current or new <= current):
        return str(new_value or "")
    return str(current_value or "")


def _append_note(base: str, note: str) -> str:
    base = str(base or "").strip()
    note = str(note or "").strip()
    if not note:
        return base
    if not base:
        return note
    if note.lower() in base.lower():
        return base
    return f"{base}\n\n{note}"


def _contains_negation(text: str) -> bool:
    lowered = _normalize_text(text)
    return any(token in lowered for token in NEGATION_PATTERNS)


def _negated_action_verbs(text: str) -> set[str]:
    lowered = _normalize_text(text)
    matches = set()
    for pattern in (r"(?:never|avoid|skip|disable|remove|forbid|bypass)\s+([a-z0-9_-]+)", r"(?:do not|don't)\s+([a-z0-9_-]+)"):
        matches.update(re.findall(pattern, lowered))
    return {match for match in matches if len(match) > 2}


def _looks_contradictory(existing_text: str, new_text: str) -> bool:
    existing_norm = _normalize_text(existing_text)
    new_norm = _normalize_text(new_text)
    if not existing_norm or not new_norm:
        return False
    existing_tokens = set(_tokenize(existing_norm))
    new_tokens = set(_tokenize(new_norm))
    if len(existing_tokens & new_tokens) < 3:
        return False
    existing_negated_verbs = _negated_action_verbs(existing_norm)
    new_negated_verbs = _negated_action_verbs(new_norm)
    if existing_negated_verbs & new_tokens and not existing_negated_verbs & new_negated_verbs:
        return True
    if new_negated_verbs & existing_tokens and not existing_negated_verbs & new_negated_verbs:
        return True
    if _contains_negation(existing_norm) != _contains_negation(new_norm):
        return True
    for positive, negative in CONTRADICTION_PAIRS:
        existing_has_pair = positive in existing_norm or negative in existing_norm
        new_has_pair = positive in new_norm or negative in new_norm
        if existing_has_pair and new_has_pair:
            if (positive in existing_norm and negative in new_norm) or (negative in existing_norm and positive in new_norm):
                return True
    return False


def _fetch_open_followups() -> list[dict]:
    if not NEXO_DB.exists():
        return []
    conn = sqlite3.connect(str(NEXO_DB))
    conn.row_factory = sqlite3.Row
    cols = _table_columns(NEXO_DB, "followups")
    reasoning_sql = ", reasoning" if "reasoning" in cols else ""
    verification_sql = ", verification" if "verification" in cols else ""
    try:
        rows = conn.execute(
            "SELECT id, description, date, status"
            f"{verification_sql}{reasoning_sql} "
            "FROM followups WHERE status NOT LIKE 'COMPLETED%' "
            "AND status NOT IN ('DELETED','archived','blocked','waiting','CANCELLED')"
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def _find_similar_followup(description: str, threshold: float = 0.58) -> dict | None:
    candidates = []
    query = str(description or "").strip()
    if not query:
        return None
    query_tokens = set(_tokenize(query))
    for row in _fetch_open_followups():
        haystack = " ".join(
            [
                str(row.get("description", "") or ""),
                str(row.get("verification", "") or ""),
                str(row.get("reasoning", "") or ""),
            ]
        )
        haystack_tokens = set(_tokenize(haystack))
        if len(query_tokens & haystack_tokens) < 2 and _normalize_text(query) not in _normalize_text(haystack):
            continue
        score = _text_similarity(query, haystack)
        if score >= threshold:
            candidates.append({**row, "_similarity": score})
    if not candidates:
        return None
    candidates.sort(key=lambda item: item["_similarity"], reverse=True)
    return candidates[0]


def _touch_existing_followup(existing: dict, *, description: str, date: str = "", reasoning_note: str = "") -> dict:
    cols = _table_columns(NEXO_DB, "followups")
    if not cols:
        return {"success": False, "error": "followups table not found"}

    updates: dict[str, object] = {}
    existing_description = str(existing.get("description", "") or "")
    if _is_concrete_action(description) and not _is_concrete_action(existing_description):
        updates["description"] = description
    preferred_date = _prefer_due_date(existing.get("date", ""), date)
    if preferred_date and preferred_date != str(existing.get("date", "") or "") and "date" in cols:
        updates["date"] = preferred_date
    note = reasoning_note or "Deep Sleep matched this followup semantically."
    changed = False
    if updates:
        result = nexo_db.update_followup(
            str(existing["id"]),
            history_actor="deep-sleep",
            history_event="updated",
            history_note=note,
            **updates,
        )
        if result.get("error"):
            return {"success": False, "error": result["error"]}
        changed = True
    elif note:
        note_result = nexo_db.add_followup_note(str(existing["id"]), note, actor="deep-sleep")
        if note_result.get("error"):
            return {"success": False, "error": note_result["error"]}
        changed = True

    return {
        "success": True,
        "id": existing["id"],
        "outcome": "matched_existing_followup",
        "similarity": existing.get("_similarity", 1.0),
        "updated_existing": changed,
    }


def _fetch_learning_candidates(category: str = "") -> list[dict]:
    if not NEXO_DB.exists():
        return []
    cols = _table_columns(NEXO_DB, "learnings")
    if not cols:
        return []
    select_fields = ["id", "category", "title", "content", "created_at", "updated_at"]
    for optional in ("reasoning", "prevention", "applies_to", "status", "review_due_at", "last_reviewed_at", "weight", "priority"):
        if optional in cols:
            select_fields.append(optional)
    query = f"SELECT {', '.join(select_fields)} FROM learnings"
    params: list[object] = []
    if category and "category" in cols:
        query += " WHERE category = ?"
        params.append(category)
    query += " ORDER BY COALESCE(updated_at, created_at) DESC LIMIT 240"
    conn = sqlite3.connect(str(NEXO_DB))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(query, tuple(params)).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def _find_learning_match(category: str, title: str, content: str) -> dict | None:
    candidates = []
    new_text = " ".join([str(title or ""), str(content or "")]).strip()
    for row in _fetch_learning_candidates(category):
        existing_text = " ".join([str(row.get("title", "") or ""), str(row.get("content", "") or "")])
        similarity = _text_similarity(new_text, existing_text)
        if similarity < 0.58:
            continue
        contradiction = _looks_contradictory(existing_text, new_text)
        candidates.append({**row, "_similarity": similarity, "_contradiction": contradiction})
    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (item["_contradiction"], item["_similarity"], item.get("updated_at", 0) or item.get("created_at", 0)),
        reverse=True,
    )
    return candidates[0]


def _update_learning_row(learning_id: int, updates: dict[str, object]) -> None:
    if not updates:
        return
    conn = sqlite3.connect(str(NEXO_DB))
    set_clause = ", ".join(f"{column} = ?" for column in updates)
    conn.execute(f"UPDATE learnings SET {set_clause} WHERE id = ?", list(updates.values()) + [learning_id])
    conn.commit()
    conn.close()


def _bump_weight(existing_value, amount: float) -> float:
    try:
        base = float(existing_value or 0)
    except Exception:
        base = 0.0
    return round(min(10.0, base + amount), 2)


def _flag_learning_contradiction(existing: dict, category: str, title: str, content: str) -> dict:
    review_description = (
        f"Reconcile contradictory learning in {category or 'general'}: "
        f"review existing learning #{existing.get('id')} ('{existing.get('title', '')}') "
        f"against new Deep Sleep finding '{title}'. Produce one canonical rule, update guardrails, and remove ambiguity."
    )
    followup_result = create_followup(
        description=review_description,
        date="",
        reasoning_note=f"Contradiction detected against learning #{existing.get('id')}: {content[:240]}",
    )
    return {
        "success": followup_result.get("success", False),
        "id": existing.get("id"),
        "outcome": "contradiction_review",
        "similarity": existing.get("_similarity", 0.0),
        "review_followup_id": followup_result.get("id"),
        "followup_result": followup_result,
    }


def add_learning(category: str, title: str, content: str) -> dict:
    """Add a learning to nexo.db. Returns result dict."""
    if not NEXO_DB.exists():
        return {"success": False, "error": "nexo.db not found"}
    try:
        existing = _find_learning_match(category, title, content)
        if existing:
            similarity = existing.get("_similarity", 0.0)
            if existing.get("_contradiction"):
                return _flag_learning_contradiction(existing, category, title, content)

            updates: dict[str, object] = {}
            columns = _table_columns(NEXO_DB, "learnings")
            if "updated_at" in columns:
                updates["updated_at"] = datetime.now().timestamp()

            existing_title = _normalize_text(existing.get("title", ""))
            existing_content = _normalize_text(existing.get("content", ""))
            incoming_title = _normalize_text(title)
            incoming_content = _normalize_text(content)

            if similarity >= 0.95 and (
                existing_title == incoming_title
                or existing_content == incoming_content
                or incoming_content in existing_content
                or existing_content in incoming_content
            ):
                if "weight" in columns:
                    updates["weight"] = _bump_weight(existing.get("weight"), 0.1)
                if "last_reviewed_at" in columns:
                    updates["last_reviewed_at"] = datetime.now().timestamp()
                if "reasoning" in columns:
                    updates["reasoning"] = _append_note(
                        existing.get("reasoning", ""),
                        f"Reconfirmed by Deep Sleep on {datetime.now().strftime('%Y-%m-%d')}.",
                    )
                _update_learning_row(existing["id"], updates)
                return {
                    "success": True,
                    "id": existing["id"],
                    "outcome": "duplicate_learning",
                    "similarity": similarity,
                    "updated_existing": bool(updates),
                }

            if similarity >= 0.58:
                if "weight" in columns:
                    updates["weight"] = _bump_weight(existing.get("weight"), 0.25)
                if "reasoning" in columns:
                    updates["reasoning"] = _append_note(
                        existing.get("reasoning", ""),
                        f"Deep Sleep reinforcement ({datetime.now().strftime('%Y-%m-%d')}): {title}. {content[:240]}",
                    )
                elif "content" in columns and content and content not in str(existing.get("content", "")):
                    updates["content"] = _append_note(
                        existing.get("content", ""),
                        f"Reinforced by Deep Sleep: {content[:240]}",
                    )
                _update_learning_row(existing["id"], updates)
                return {
                    "success": True,
                    "id": existing["id"],
                    "outcome": "reinforced_learning",
                    "similarity": similarity,
                    "updated_existing": bool(updates),
                }

        now = datetime.now().timestamp()
        columns = _table_columns(NEXO_DB, "learnings")
        payload = {
            "category": category,
            "title": title,
            "content": content,
            "created_at": now,
            "updated_at": now,
        }
        if "reasoning" in columns:
            payload["reasoning"] = "Deep Sleep v2 overnight analysis"
        if "status" in columns:
            payload["status"] = "active"
        insert_columns = [column for column in payload if column in columns]
        values = [payload[column] for column in insert_columns]

        conn = sqlite3.connect(str(NEXO_DB))
        cursor = conn.execute(
            f"INSERT INTO learnings ({', '.join(insert_columns)}) VALUES ({', '.join('?' for _ in insert_columns)})",
            values,
        )
        learning_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return {"success": True, "id": learning_id, "outcome": "new_learning"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def create_followup(description: str, date: str = "", reasoning_note: str = "") -> dict:
    """Create a followup in nexo.db. Returns result dict."""
    if not NEXO_DB.exists():
        return {"success": False, "error": "nexo.db not found"}
    try:
        matched = _find_similar_followup(description)
        if matched:
            return _touch_existing_followup(
                matched,
                description=description,
                date=date,
                reasoning_note=reasoning_note or "Deep Sleep matched this followup semantically.",
            )

        # Generate a deterministic ID
        fid = "NF-DS-" + hashlib.md5(description.encode()).hexdigest()[:8].upper()
        existing = nexo_db.get_followup(fid)
        if existing:
            return _touch_existing_followup(
                existing,
                description=description,
                date=date,
                reasoning_note=reasoning_note or "Deep Sleep revisited this deterministic followup.",
            )

        followup_result = nexo_db.create_followup(
            id=fid,
            description=description,
            date=date or None,
            verification="",
            reasoning=reasoning_note or "Deep Sleep v2 overnight analysis",
            recurrence=None,
        )
        if followup_result.get("error"):
            return {"success": False, "error": followup_result["error"]}
        return {"success": True, "id": fid, "outcome": "new_followup"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def update_calibration_mood(synthesis: dict) -> dict:
    """Update mood in calibration.json based on emotional analysis."""
    calibration_file = NEXO_HOME / "brain" / "calibration.json"
    if not calibration_file.exists():
        return {"success": False, "error": "calibration.json not found"}

    emotional_day = synthesis.get("emotional_day", {})
    if not emotional_day:
        return {"success": False, "error": "no emotional_day data"}

    try:
        cal = json.loads(calibration_file.read_text())

        # Add/update mood history
        if "mood_history" not in cal:
            cal["mood_history"] = []

        cal["mood_history"].append({
            "date": synthesis.get("date", ""),
            "score": emotional_day.get("mood_score", 0.5),
            "arc": emotional_day.get("mood_arc", ""),
            "triggers": emotional_day.get("recurring_triggers", {}),
        })

        # Keep last 30 days
        cal["mood_history"] = cal["mood_history"][-30:]

        # Apply calibration recommendation automatically
        rec = emotional_day.get("calibration_recommendation")
        if rec and rec != "null":
            applied_changes = []

            # Parse and apply known calibration adjustments
            rec_lower = rec.lower()
            personality = cal.get("personality", {})

            # Autonomy adjustments
            if "autonomy" in rec_lower or "autonomía" in rec_lower:
                if any(w in rec_lower for w in ["full", "más autonomía", "subir", "increase"]):
                    personality["autonomy"] = "full"
                    applied_changes.append("autonomy → full")
                elif any(w in rec_lower for w in ["conservative", "reducir", "bajar"]):
                    personality["autonomy"] = "conservative"
                    applied_changes.append("autonomy → conservative")

            # Communication adjustments
            if any(w in rec_lower for w in ["concis", "breve", "shorter", "telegráf"]):
                personality["communication"] = "concise"
                applied_changes.append("communication → concise")
            elif any(w in rec_lower for w in ["detail", "explicar más", "más contexto"]):
                personality["communication"] = "detailed"
                applied_changes.append("communication → detailed")

            # Proactivity adjustments
            if any(w in rec_lower for w in ["más proactiv", "proactive", "anticipar"]):
                personality["proactivity"] = "proactive"
                applied_changes.append("proactivity → proactive")

            cal["personality"] = personality

            # Log the recommendation and what was applied
            if "calibration_log" not in cal:
                cal["calibration_log"] = []
            cal["calibration_log"].append({
                "date": synthesis.get("date", ""),
                "recommendation": rec,
                "applied": applied_changes if applied_changes else ["noted, no auto-applicable changes"],
            })
            cal["calibration_log"] = cal["calibration_log"][-20:]

        calibration_file.write_text(json.dumps(cal, indent=2, ensure_ascii=False))
        changes_str = ", ".join(applied_changes) if rec and applied_changes else "none"
        return {"success": True, "mood_score": emotional_day.get("mood_score"), "calibration_applied": changes_str}
    except Exception as e:
        return {"success": False, "error": str(e)}


def calibrate_trust_score(synthesis: dict, target_date: str) -> dict:
    """Set the daily trust score from Deep Sleep analysis.

    This is the authoritative score for the day — replaces incremental
    adjustments with a holistic evaluation of the entire day.
    """
    trust_cal = synthesis.get("trust_calibration")
    if not trust_cal or "score" not in trust_cal:
        return {"success": False, "error": "no trust_calibration in synthesis"}

    score = max(0, min(100, trust_cal["score"]))
    reasoning = trust_cal.get("reasoning", "Deep Sleep calibration")
    trend = trust_cal.get("trend", "stable")
    highlights = trust_cal.get("highlights", [])
    lowlights = trust_cal.get("lowlights", [])

    context = (
        f"Deep Sleep {target_date} | trend: {trend} | "
        f"highlights: {', '.join(highlights[:3])} | "
        f"lowlights: {', '.join(lowlights[:3])}"
    )

    try:
        # Get current score for delta calculation
        db = sqlite3.connect(str(COGNITIVE_DB))
        row = db.execute(
            "SELECT score FROM trust_score ORDER BY id DESC LIMIT 1"
        ).fetchone()
        old_score = row[0] if row else 50.0
        delta = score - old_score

        db.execute(
            "INSERT INTO trust_score (score, event, delta, context) VALUES (?, ?, ?, ?)",
            (score, f"deep_sleep_calibration: {reasoning[:200]}", delta, context[:500])
        )
        db.commit()
        db.close()

        return {
            "success": True,
            "old_score": old_score,
            "new_score": score,
            "delta": delta,
            "trend": trend,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def create_skill(skill_data: dict) -> dict:
    """Create a personal Skill v2 definition and sync it into SQLite."""
    try:
        from db import materialize_personal_skill_definition

        skill_id = skill_data.get("id", "")
        if not skill_id:
            skill_id = "SK-DS-" + hashlib.md5(
                skill_data.get("name", "").encode()
            ).hexdigest()[:8].upper()

        execution_level = skill_data.get("execution_level", "")
        scriptable = bool(skill_data.get("scriptable"))
        mode = skill_data.get("mode", "")
        if not mode:
            if scriptable and execution_level == "read-only":
                mode = "hybrid"
            else:
                mode = "guide"

        approval_required = bool(skill_data.get("approval_required", execution_level in {"local", "remote"}))
        script_body = str(skill_data.get("script_body", "") or "")
        executable_entry = str(skill_data.get("executable_entry", "") or "")

        result = materialize_personal_skill_definition(
            {
                "id": skill_id,
                "name": skill_data.get("name", ""),
                "description": skill_data.get("description", ""),
                "level": skill_data.get("level", "draft"),
                "mode": mode,
                "execution_level": execution_level if mode != "guide" else "none",
                "approval_required": approval_required,
                "tags": skill_data.get("tags", []),
                "trigger_patterns": skill_data.get("trigger_patterns", []),
                "source_sessions": skill_data.get("source_sessions", []),
                "steps": skill_data.get("steps", []),
                "gotchas": skill_data.get("gotchas", []),
                "params_schema": skill_data.get("params_schema", skill_data.get("candidate_params", {})),
                "command_template": skill_data.get("command_template", {}),
                "executable_entry": executable_entry,
                "script_body": script_body,
                "content": skill_data.get("content", ""),
            }
        )
        if "error" in result:
            return {"success": False, "error": result["error"], "id": skill_id}
        return {"success": True, "id": result["id"], "name": result.get("name", "")}
    except Exception as e:
        return {"success": False, "error": str(e)}


def create_abandoned_followups(synthesis: dict) -> list[dict]:
    """Create followups for truly abandoned projects."""
    results = []
    abandoned = synthesis.get("abandoned_projects", [])
    for proj in abandoned:
        if proj.get("has_followup"):
            continue
        rec = proj.get("recommendation", "")
        if "ignore" in rec.lower():
            continue
        result = create_followup(
            description=f"[Abandoned] {proj.get('description', '')}",
            date=""  # No date — it's a discovered gap
        )
        results.append(result)
    return results


def _safe_query(db_path: Path, query: str, params: tuple = ()) -> list[dict]:
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, params).fetchall()
        conn.close()
        return [dict(row) for row in rows]
    except Exception:
        return []


def _parse_any_datetime(value) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and str(value).strip().isdigit()):
            return datetime.fromtimestamp(float(value))
    except Exception:
        return None
    raw = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw[:19], fmt)
        except Exception:
            continue
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00").replace("+00:00", ""))
    except Exception:
        return None


def _load_project_aliases() -> dict[str, set[str]]:
    atlas_path = NEXO_HOME / "brain" / "project-atlas.json"
    if not atlas_path.is_file():
        return {}
    try:
        payload = json.loads(atlas_path.read_text())
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    aliases: dict[str, set[str]] = {}
    for key, value in payload.items():
        if str(key).startswith("_"):
            continue
        canonical = str(key).strip().lower()
        alias_set = {canonical, canonical.replace("-", " "), canonical.replace("_", " ")}
        if isinstance(value, dict):
            for alias in value.get("aliases", []) or []:
                alias_value = str(alias or "").strip().lower()
                if alias_value:
                    alias_set.add(alias_value)
                    alias_set.add(alias_value.replace("-", " "))
        aliases[canonical] = {item for item in alias_set if item}
    return aliases


def _match_projects(text: str, alias_map: dict[str, set[str]]) -> set[str]:
    haystack = str(text or "").strip().lower()
    if not haystack:
        return set()
    matches: set[str] = set()
    for canonical, aliases in alias_map.items():
        for alias in sorted(aliases, key=len, reverse=True):
            if alias and alias in haystack:
                matches.add(canonical)
                break
    return matches


def _priority_weight(value) -> float:
    lowered = str(value or "").strip().lower()
    if lowered in {"critical", "urgent"}:
        return 4.0
    if lowered == "high":
        return 3.0
    if lowered == "medium":
        return 2.0
    if lowered == "low":
        return 1.0
    return 1.5


def _project_weighting_window(target_date: str, *, window_days: int) -> list[dict]:
    target_day = datetime.strptime(target_date, "%Y-%m-%d")
    window_start = target_day - timedelta(days=max(0, window_days - 1))
    alias_map = _load_project_aliases()
    scoreboard: dict[str, dict] = {}

    def normalize_project(project: str) -> str:
        lowered = str(project or "").strip().lower()
        if not lowered:
            return ""
        matched = _match_projects(lowered, alias_map)
        if matched:
            return sorted(matched)[0]
        return lowered

    def bump(project: str, score: float, signal_key: str, reason: str) -> None:
        canonical = normalize_project(project)
        if not canonical:
            return
        slot = scoreboard.setdefault(
            canonical,
            {
                "project": canonical,
                "score": 0.0,
                "signals": {
                    "diary_sessions": 0,
                    "learnings": 0,
                    "followups": 0,
                    "decisions": 0,
                },
                "reasons": [],
            },
        )
        slot["score"] += score
        slot["signals"][signal_key] += 1
        if reason and reason not in slot["reasons"]:
            slot["reasons"].append(reason)

    diary_rows = _safe_query(
        NEXO_DB,
        "SELECT created_at, summary, self_critique, domain FROM session_diary ORDER BY created_at DESC",
    )
    for row in diary_rows:
        created = _parse_any_datetime(row.get("created_at"))
        if not created or created < window_start or created > target_day + timedelta(days=1):
            continue
        recency_bonus = 1.4 if (target_day - created).days <= 7 else 1.0
        matched = _match_projects(
            " ".join(
                [
                    str(row.get("summary", "") or ""),
                    str(row.get("self_critique", "") or ""),
                ]
            ),
            alias_map,
        )
        domain = normalize_project(str(row.get("domain", "") or ""))
        if domain:
            matched.add(domain)
        for project in matched:
            bump(project, 3.0 * recency_bonus, "diary_sessions", "recent diary activity")

    learning_rows = _safe_query(
        NEXO_DB,
        "SELECT title, content, applies_to, priority, weight, updated_at, created_at FROM learnings "
        "ORDER BY COALESCE(updated_at, created_at) DESC LIMIT 180",
    )
    for row in learning_rows:
        when = _parse_any_datetime(row.get("updated_at") or row.get("created_at"))
        if when and when < window_start:
            continue
        matched = _match_projects(
            " ".join(
                [
                    str(row.get("applies_to", "") or ""),
                    str(row.get("title", "") or ""),
                    str(row.get("content", "") or ""),
                ]
            ),
            alias_map,
        )
        if not matched:
            continue
        score = 1.0 + _priority_weight(row.get("priority")) + min(2.0, max(0.0, float(row.get("weight", 0) or 0)))
        for project in matched:
            bump(project, score, "learnings", "recent leverage-bearing learning")

    followup_rows = _safe_query(
        NEXO_DB,
        "SELECT description, date, status, priority, created_at, updated_at, reasoning FROM followups "
        "WHERE status NOT IN ('COMPLETED', 'CANCELLED') ORDER BY date ASC, created_at ASC LIMIT 160",
    )
    for row in followup_rows:
        matched = _match_projects(
            " ".join(
                [
                    str(row.get("description", "") or ""),
                    str(row.get("reasoning", "") or ""),
                ]
            ),
            alias_map,
        )
        if not matched:
            continue
        overdue_bonus = 0.0
        due_dt = _parse_any_datetime(row.get("date"))
        if due_dt and due_dt <= target_day:
            overdue_bonus = 1.5
        score = 1.5 + _priority_weight(row.get("priority")) + overdue_bonus
        for project in matched:
            bump(project, score, "followups", "open followup pressure")

    decision_rows = _safe_query(
        NEXO_DB,
        "SELECT domain, outcome, status, reasoning, created_at, review_due_at FROM decisions "
        "ORDER BY COALESCE(created_at, review_due_at) DESC LIMIT 160",
    )
    for row in decision_rows:
        when = _parse_any_datetime(row.get("created_at") or row.get("review_due_at"))
        if when and when < window_start:
            continue
        matched = _match_projects(
            " ".join(
                [
                    str(row.get("reasoning", "") or ""),
                    str(row.get("outcome", "") or ""),
                    str(row.get("status", "") or ""),
                ]
            ),
            alias_map,
        )
        domain = normalize_project(str(row.get("domain", "") or ""))
        if domain:
            matched.add(domain)
        if not matched:
            continue
        outcome = str(row.get("outcome", "") or "").lower()
        status = str(row.get("status", "") or "").lower()
        score = 2.5
        if any(token in outcome for token in ("fail", "error", "blocked", "regression")):
            score += 2.0
        if status in {"pending", "blocked", "open"}:
            score += 1.5
        for project in matched:
            bump(project, score, "decisions", "recent decision pressure")

    ranked = sorted(scoreboard.values(), key=lambda item: item["score"], reverse=True)
    for item in ranked:
        item["score"] = round(item["score"], 2)
        item["reasons"] = item["reasons"][:4]
    return ranked[:8]


def _load_period_syntheses(target_date: str, *, window_days: int) -> list[dict]:
    target_day = datetime.strptime(target_date, "%Y-%m-%d")
    syntheses: list[dict] = []
    for offset in range(window_days):
        date_str = (target_day - timedelta(days=offset)).strftime("%Y-%m-%d")
        path = DEEP_SLEEP_DIR / f"{date_str}-synthesis.json"
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text())
        except Exception:
            continue
        if isinstance(payload, dict):
            syntheses.append(payload)
    syntheses.reverse()
    return syntheses


def _load_period_extractions(target_date: str, *, window_days: int) -> list[dict]:
    target_day = datetime.strptime(target_date, "%Y-%m-%d")
    payloads: list[dict] = []
    for offset in range(window_days):
        date_str = (target_day - timedelta(days=offset)).strftime("%Y-%m-%d")
        path = DEEP_SLEEP_DIR / f"{date_str}-extractions.json"
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text())
        except Exception:
            continue
        if isinstance(payload, dict):
            payloads.append(payload)
    payloads.reverse()
    return payloads


def _load_period_applied_logs(target_date: str, *, window_days: int) -> list[dict]:
    target_day = datetime.strptime(target_date, "%Y-%m-%d")
    payloads: list[dict] = []
    for offset in range(window_days):
        date_str = (target_day - timedelta(days=offset)).strftime("%Y-%m-%d")
        path = DEEP_SLEEP_DIR / f"{date_str}-applied.json"
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text())
        except Exception:
            continue
        if isinstance(payload, dict):
            payloads.append(payload)
    payloads.reverse()
    return payloads


def _safe_pct(numerator: float, denominator: float) -> float | None:
    if denominator <= 0:
        return None
    return round((numerator / denominator) * 100.0, 1)


def _aggregate_protocol_summary(extractions: list[dict]) -> dict:
    totals = {
        "sessions": 0,
        "guard_check": {"required": 0, "executed": 0},
        "heartbeat": {"total": 0, "with_context": 0},
        "change_log": {"edits": 0, "logged": 0},
    }

    for payload in extractions:
        for item in payload.get("extractions", []) or []:
            if not isinstance(item, dict) or item.get("error"):
                continue
            totals["sessions"] += 1
            protocol_summary = item.get("protocol_summary") or {}
            for key in ("guard_check", "heartbeat", "change_log"):
                current = protocol_summary.get(key) or {}
                if key == "guard_check":
                    totals[key]["required"] += int(current.get("required", 0) or 0)
                    totals[key]["executed"] += int(current.get("executed", 0) or 0)
                elif key == "heartbeat":
                    totals[key]["total"] += int(current.get("total", 0) or 0)
                    totals[key]["with_context"] += int(current.get("with_context", 0) or 0)
                else:
                    totals[key]["edits"] += int(current.get("edits", 0) or 0)
                    totals[key]["logged"] += int(current.get("logged", 0) or 0)

    guard_pct = _safe_pct(totals["guard_check"]["executed"], totals["guard_check"]["required"])
    heartbeat_pct = _safe_pct(totals["heartbeat"]["with_context"], totals["heartbeat"]["total"])
    change_pct = _safe_pct(totals["change_log"]["logged"], totals["change_log"]["edits"])
    available = [value for value in (guard_pct, heartbeat_pct, change_pct) if value is not None]

    totals["guard_check"]["compliance_pct"] = guard_pct
    totals["heartbeat"]["compliance_pct"] = heartbeat_pct
    totals["change_log"]["compliance_pct"] = change_pct
    totals["overall_compliance_pct"] = round(sum(available) / len(available), 1) if available else None
    return totals


def _aggregate_delivery_metrics(applied_logs: list[dict]) -> dict:
    totals = {
        "runs": len(applied_logs),
        "applied_actions": 0,
        "deferred_actions": 0,
        "skipped_dedupe": 0,
        "errors": 0,
        "engineering_followups": 0,
        "followup_dedupe_matches": 0,
        "learning_reinforcements": 0,
        "learning_duplicate_skips": 0,
        "learning_contradiction_reviews": 0,
    }
    for payload in applied_logs:
        stats = payload.get("stats") or {}
        totals["applied_actions"] += int(stats.get("applied", 0) or 0)
        totals["deferred_actions"] += int(stats.get("deferred", 0) or 0)
        totals["skipped_dedupe"] += int(stats.get("skipped_dedupe", 0) or 0)
        totals["errors"] += int(stats.get("errors", 0) or 0)
        for action in payload.get("applied_actions", []) or []:
            details = action.get("details") or {}
            if action.get("action_type") == "followup_create":
                description = str(details.get("description", "") or "") + " " + str(details.get("reasoning", "") or "")
                if "engineering" in description.lower() or "guardrail" in description.lower():
                    totals["engineering_followups"] += 1
                if details.get("outcome") == "matched_existing_followup":
                    totals["followup_dedupe_matches"] += 1
            elif action.get("action_type") == "learning_add":
                outcome = str(details.get("outcome", "") or "")
                if outcome == "reinforced_learning":
                    totals["learning_reinforcements"] += 1
                elif outcome == "duplicate_learning":
                    totals["learning_duplicate_skips"] += 1
                elif outcome == "contradiction_review":
                    totals["learning_contradiction_reviews"] += 1

    attempted = totals["applied_actions"] + totals["deferred_actions"] + totals["skipped_dedupe"] + totals["errors"]
    totals["dedupe_rate_pct"] = _safe_pct(totals["skipped_dedupe"], attempted)
    totals["error_rate_pct"] = _safe_pct(totals["errors"], attempted)
    return totals


def _semantic_duplicate_metrics(items: list[tuple[str, str]], *, threshold: float = 0.82) -> dict:
    filtered = [(item_id, _normalize_text(text)) for item_id, text in items if _normalize_text(text)]
    if len(filtered) < 2:
        return {
            "cluster_count": 0,
            "duplicate_items": 0,
            "duplicate_excess": 0,
            "sample_clusters": [],
        }

    used: set[int] = set()
    clusters: list[list[tuple[str, str]]] = []
    for index, (item_id, text) in enumerate(filtered):
        if index in used:
            continue
        cluster = [(item_id, text)]
        for other_index in range(index + 1, len(filtered)):
            if other_index in used:
                continue
            other_id, other_text = filtered[other_index]
            if _text_similarity(text, other_text) >= threshold:
                cluster.append((other_id, other_text))
                used.add(other_index)
        if len(cluster) > 1:
            used.add(index)
            clusters.append(cluster)

    return {
        "cluster_count": len(clusters),
        "duplicate_items": sum(len(cluster) for cluster in clusters),
        "duplicate_excess": sum(max(0, len(cluster) - 1) for cluster in clusters),
        "sample_clusters": [
            [item_id for item_id, _ in cluster[:4]]
            for cluster in clusters[:5]
        ],
    }


def _followup_deduplication_metrics() -> dict:
    cols = _table_columns(NEXO_DB, "followups")
    if "description" not in cols:
        return {
            "open_followups": 0,
            "duplicate_clusters": 0,
            "duplicate_open_followups": 0,
            "duplicate_rate_pct": None,
            "sample_clusters": [],
        }

    select_cols = ["description"]
    if "id" in cols:
        select_cols.append("id")
    if "status" in cols:
        select_cols.append("status")

    conn = sqlite3.connect(str(NEXO_DB))
    conn.row_factory = sqlite3.Row
    rows = [dict(row) for row in conn.execute(f"SELECT {', '.join(select_cols)} FROM followups").fetchall()]
    conn.close()

    open_rows = []
    for row in rows:
        status = str(row.get("status", "pending") or "pending").strip().lower()
        if status in {"done", "completed", "cancelled", "resolved"}:
            continue
        identifier = str(row.get("id") or row.get("description") or f"followup-{len(open_rows)+1}")
        open_rows.append((identifier, str(row.get("description", "") or "")))

    duplicates = _semantic_duplicate_metrics(open_rows)
    return {
        "open_followups": len(open_rows),
        "duplicate_clusters": duplicates["cluster_count"],
        "duplicate_open_followups": duplicates["duplicate_excess"],
        "duplicate_rate_pct": _safe_pct(duplicates["duplicate_excess"], len(open_rows)),
        "sample_clusters": duplicates["sample_clusters"],
    }


def _learning_consolidation_metrics() -> dict:
    cols = _table_columns(NEXO_DB, "learnings")
    if not {"title", "content"}.issubset(cols):
        return {
            "active_learnings": 0,
            "weak_active_learnings": 0,
            "duplicate_clusters": 0,
            "duplicate_active_learnings": 0,
            "noise_pressure": 0,
            "noise_rate_pct": None,
            "sample_clusters": [],
        }

    select_cols = ["title", "content"]
    if "id" in cols:
        select_cols.append("id")
    for field in ("status", "weight", "reasoning", "prevention", "applies_to", "guard_hits"):
        if field in cols:
            select_cols.append(field)

    conn = sqlite3.connect(str(NEXO_DB))
    conn.row_factory = sqlite3.Row
    rows = [dict(row) for row in conn.execute(f"SELECT {', '.join(select_cols)} FROM learnings").fetchall()]
    conn.close()

    active_rows = []
    weak_active = 0
    for row in rows:
        status = str(row.get("status", "active") or "active").strip().lower()
        if status != "active":
            continue
        active_rows.append(row)
        weight = row.get("weight")
        reasoning = str(row.get("reasoning", "") or "").strip()
        prevention = str(row.get("prevention", "") or "").strip()
        guard_hits = int(row.get("guard_hits", 0) or 0)
        applies_to = str(row.get("applies_to", "") or "").strip()
        if isinstance(weight, (int, float)) and float(weight) < 1.0:
            weak_active += 1
        elif not reasoning and not prevention:
            weak_active += 1
        elif applies_to and guard_hits <= 0:
            weak_active += 1

    duplicates = _semantic_duplicate_metrics(
        [
            (str(row.get("id") or f"learning-{index}"), f"{row.get('title', '')} {row.get('content', '')}")
            for index, row in enumerate(active_rows, 1)
        ],
        threshold=0.8,
    )
    noise_pressure = weak_active + duplicates["duplicate_excess"]
    return {
        "active_learnings": len(active_rows),
        "weak_active_learnings": weak_active,
        "duplicate_clusters": duplicates["cluster_count"],
        "duplicate_active_learnings": duplicates["duplicate_excess"],
        "noise_pressure": noise_pressure,
        "noise_rate_pct": _safe_pct(noise_pressure, len(active_rows)),
        "sample_clusters": duplicates["sample_clusters"],
    }


def _load_previous_period_summary(kind: str, label: str) -> dict | None:
    pattern = f"*-{kind}-summary.json"
    candidates: list[tuple[str, Path]] = []
    for path in DEEP_SLEEP_DIR.glob(pattern):
        try:
            payload = json.loads(path.read_text())
        except Exception:
            continue
        candidate_label = str(payload.get("label", "") or "")
        if candidate_label and candidate_label < label:
            candidates.append((candidate_label, path))
    if not candidates:
        return None
    _, path = sorted(candidates, key=lambda item: item[0])[-1]
    try:
        payload = json.loads(path.read_text())
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _build_project_pulse(top_projects: list[dict], previous_summary: dict | None) -> list[dict]:
    previous_scores: dict[str, float] = {}
    if previous_summary:
        for item in previous_summary.get("project_pulse", []) or previous_summary.get("top_projects", []) or []:
            project = str(item.get("project", "") or "")
            if project:
                previous_scores[project] = float(item.get("score", 0) or 0)

    pulse: list[dict] = []
    for item in top_projects:
        project = str(item.get("project", "") or "")
        score = float(item.get("score", 0) or 0)
        previous_score = previous_scores.get(project, 0.0)
        delta = round(score - previous_score, 2)
        if score >= 18:
            status = "critical"
        elif score >= 10:
            status = "elevated"
        else:
            status = "watch"
        if delta >= 2.0:
            trend = "rising"
        elif delta <= -2.0:
            trend = "cooling"
        else:
            trend = "steady"
        pulse.append(
            {
                "project": project,
                "score": round(score, 2),
                "delta_vs_previous": delta,
                "trend": trend,
                "status": status,
                "signals": item.get("signals", {}),
                "reasons": item.get("reasons", []),
            }
        )
    return pulse


def _build_period_trend(summary: dict, previous_summary: dict | None) -> dict:
    if not previous_summary:
        return {
            "has_previous": False,
            "avg_mood_delta": None,
            "avg_trust_delta": None,
            "total_corrections_delta": None,
            "protocol_compliance_delta": None,
            "followup_duplicate_open_delta": None,
            "followup_duplicate_rate_delta": None,
            "learning_noise_delta": None,
            "learning_noise_rate_delta": None,
        }

    current_protocol = summary.get("protocol_summary", {}).get("overall_compliance_pct")
    previous_protocol = (previous_summary.get("protocol_summary") or {}).get("overall_compliance_pct")
    current_mood = summary.get("avg_mood_score")
    previous_mood = previous_summary.get("avg_mood_score")
    current_trust = summary.get("avg_trust_score")
    previous_trust = previous_summary.get("avg_trust_score")
    current_followup = (summary.get("followup_deduplication") or {}).get("duplicate_open_followups")
    previous_followup = (previous_summary.get("followup_deduplication") or {}).get("duplicate_open_followups")
    current_followup_rate = (summary.get("followup_deduplication") or {}).get("duplicate_rate_pct")
    previous_followup_rate = (previous_summary.get("followup_deduplication") or {}).get("duplicate_rate_pct")
    current_learning_noise = (summary.get("learning_consolidation") or {}).get("noise_pressure")
    previous_learning_noise = (previous_summary.get("learning_consolidation") or {}).get("noise_pressure")
    current_learning_rate = (summary.get("learning_consolidation") or {}).get("noise_rate_pct")
    previous_learning_rate = (previous_summary.get("learning_consolidation") or {}).get("noise_rate_pct")

    return {
        "has_previous": True,
        "avg_mood_delta": round(current_mood - previous_mood, 3) if isinstance(current_mood, (int, float)) and isinstance(previous_mood, (int, float)) else None,
        "avg_trust_delta": round(current_trust - previous_trust, 1) if isinstance(current_trust, (int, float)) and isinstance(previous_trust, (int, float)) else None,
        "total_corrections_delta": int(summary.get("total_corrections", 0) or 0) - int(previous_summary.get("total_corrections", 0) or 0),
        "protocol_compliance_delta": round(current_protocol - previous_protocol, 1) if isinstance(current_protocol, (int, float)) and isinstance(previous_protocol, (int, float)) else None,
        "followup_duplicate_open_delta": int(current_followup or 0) - int(previous_followup or 0) if current_followup is not None or previous_followup is not None else None,
        "followup_duplicate_rate_delta": round(float(current_followup_rate) - float(previous_followup_rate), 1) if isinstance(current_followup_rate, (int, float)) and isinstance(previous_followup_rate, (int, float)) else None,
        "learning_noise_delta": int(current_learning_noise or 0) - int(previous_learning_noise or 0) if current_learning_noise is not None or previous_learning_noise is not None else None,
        "learning_noise_rate_delta": round(float(current_learning_rate) - float(previous_learning_rate), 1) if isinstance(current_learning_rate, (int, float)) and isinstance(previous_learning_rate, (int, float)) else None,
    }


def _build_period_summary(target_date: str, synthesis: dict, *, kind: str, window_days: int) -> dict:
    target_day = datetime.strptime(target_date, "%Y-%m-%d")
    window_start = (target_day - timedelta(days=max(0, window_days - 1))).strftime("%Y-%m-%d")
    label = (
        f"{target_day.isocalendar().year}-W{target_day.isocalendar().week:02d}"
        if kind == "weekly"
        else target_day.strftime("%Y-%m")
    )
    syntheses = _load_period_syntheses(target_date, window_days=window_days)
    extractions = _load_period_extractions(target_date, window_days=window_days)
    applied_logs = _load_period_applied_logs(target_date, window_days=window_days)
    if not any(item.get("date") == target_date for item in syntheses):
        syntheses.append(synthesis)

    mood_scores = []
    trust_scores = []
    total_corrections = 0
    pattern_counter: Counter[str] = Counter()
    agenda_counter: Counter[str] = Counter()
    for item in syntheses:
        mood = item.get("emotional_day", {}).get("mood_score")
        if isinstance(mood, (int, float)):
            mood_scores.append(float(mood))
        trust = item.get("trust_calibration", {}).get("score")
        if isinstance(trust, (int, float)):
            trust_scores.append(float(trust))
        total_corrections += int(item.get("productivity_day", {}).get("total_corrections", 0) or 0)
        for pattern in item.get("cross_session_patterns", []) or []:
            text = str(pattern.get("pattern", "") or "").strip()
            if text:
                pattern_counter[text] += 1
        for agenda in item.get("morning_agenda", []) or []:
            title = str(agenda.get("title", "") or "").strip()
            if title:
                agenda_counter[title] += 1

    top_projects = _project_weighting_window(target_date, window_days=window_days)
    avg_mood = round(sum(mood_scores) / len(mood_scores), 3) if mood_scores else None
    avg_trust = round(sum(trust_scores) / len(trust_scores), 1) if trust_scores else None
    top_patterns = [
        {"pattern": pattern, "count": count}
        for pattern, count in pattern_counter.most_common(6)
    ]
    recurring_agenda = [
        {"title": title, "count": count}
        for title, count in agenda_counter.most_common(6)
    ]
    protocol_summary = _aggregate_protocol_summary(extractions)
    delivery_metrics = _aggregate_delivery_metrics(applied_logs)
    followup_deduplication = _followup_deduplication_metrics()
    learning_consolidation = _learning_consolidation_metrics()
    previous_summary = _load_previous_period_summary(kind, label)
    project_pulse = _build_project_pulse(top_projects, previous_summary)

    summary_parts = [f"{len(syntheses)} Deep Sleep run(s)"]
    if top_projects:
        summary_parts.append(f"top focus: {top_projects[0]['project']}")
    if top_patterns:
        summary_parts.append(f"recurring pattern: {top_patterns[0]['pattern']}")
    if avg_trust is not None:
        summary_parts.append(f"avg trust {avg_trust:.1f}")
    if protocol_summary.get("overall_compliance_pct") is not None:
        summary_parts.append(f"protocol {protocol_summary['overall_compliance_pct']:.1f}%")
    summary = " | ".join(summary_parts)

    period_summary = {
        "kind": kind,
        "label": label,
        "window_days": window_days,
        "window_start": window_start,
        "window_end": target_date,
        "generated_at": datetime.now().isoformat(),
        "daily_syntheses": len(syntheses),
        "avg_mood_score": avg_mood,
        "avg_trust_score": avg_trust,
        "total_corrections": total_corrections,
        "top_projects": top_projects,
        "project_pulse": project_pulse,
        "top_patterns": top_patterns,
        "recurring_agenda": recurring_agenda,
        "protocol_summary": protocol_summary,
        "delivery_metrics": delivery_metrics,
        "followup_deduplication": followup_deduplication,
        "learning_consolidation": learning_consolidation,
        "summary": summary,
    }
    period_summary["trend"] = _build_period_trend(period_summary, previous_summary)
    return period_summary

def _render_period_summary_markdown(summary: dict) -> str:
    lines = [
        f"# {summary.get('kind', 'period').title()} Deep Sleep Summary — {summary.get('label', '')}",
        "",
        f"- Window: {summary.get('window_start', '')} -> {summary.get('window_end', '')}",
        f"- Deep Sleep runs: {summary.get('daily_syntheses', 0)}",
    ]
    if summary.get("avg_mood_score") is not None:
        lines.append(f"- Avg mood score: {summary['avg_mood_score']:.2f}")
    if summary.get("avg_trust_score") is not None:
        lines.append(f"- Avg trust score: {summary['avg_trust_score']:.1f}")
    lines.append(f"- Total corrections: {summary.get('total_corrections', 0)}")
    lines.append("")
    if summary.get("summary"):
        lines.append(f"> {summary['summary']}")
        lines.append("")

    protocol_summary = summary.get("protocol_summary") or {}
    if protocol_summary:
        lines.append("## Protocol Compliance")
        lines.append("")
        overall = protocol_summary.get("overall_compliance_pct")
        if overall is not None:
            lines.append(f"- Overall compliance: {overall:.1f}%")
        guard = protocol_summary.get("guard_check", {})
        heartbeat = protocol_summary.get("heartbeat", {})
        change_log = protocol_summary.get("change_log", {})
        if guard:
            lines.append(
                f"- guard_check: {guard.get('executed', 0)}/{guard.get('required', 0)}"
                + (f" ({guard['compliance_pct']:.1f}%)" if guard.get("compliance_pct") is not None else "")
            )
        if heartbeat:
            lines.append(
                f"- heartbeat with context: {heartbeat.get('with_context', 0)}/{heartbeat.get('total', 0)}"
                + (f" ({heartbeat['compliance_pct']:.1f}%)" if heartbeat.get("compliance_pct") is not None else "")
            )
        if change_log:
            lines.append(
                f"- change_log after edits: {change_log.get('logged', 0)}/{change_log.get('edits', 0)}"
                + (f" ({change_log['compliance_pct']:.1f}%)" if change_log.get("compliance_pct") is not None else "")
            )
        lines.append("")

    delivery_metrics = summary.get("delivery_metrics") or {}
    if delivery_metrics:
        lines.append("## Loop Output")
        lines.append("")
        lines.append(f"- Applied actions: {delivery_metrics.get('applied_actions', 0)}")
        lines.append(f"- Deferred actions: {delivery_metrics.get('deferred_actions', 0)}")
        lines.append(f"- Dedupe skips: {delivery_metrics.get('skipped_dedupe', 0)}")
        lines.append(f"- Engineering followups: {delivery_metrics.get('engineering_followups', 0)}")
        if delivery_metrics.get("dedupe_rate_pct") is not None:
            lines.append(f"- Dedupe rate: {delivery_metrics['dedupe_rate_pct']:.1f}%")
        if delivery_metrics.get("error_rate_pct") is not None:
            lines.append(f"- Error rate: {delivery_metrics['error_rate_pct']:.1f}%")
        lines.append(f"- Followup dedupe matches: {delivery_metrics.get('followup_dedupe_matches', 0)}")
        lines.append(f"- Learning reinforcements: {delivery_metrics.get('learning_reinforcements', 0)}")
        lines.append(f"- Learning duplicate skips: {delivery_metrics.get('learning_duplicate_skips', 0)}")
        lines.append(f"- Learning contradiction reviews: {delivery_metrics.get('learning_contradiction_reviews', 0)}")
        lines.append("")

    followup_deduplication = summary.get("followup_deduplication") or {}
    learning_consolidation = summary.get("learning_consolidation") or {}
    if followup_deduplication or learning_consolidation:
        lines.append("## Prevention Quality")
        lines.append("")
        if followup_deduplication:
            lines.append(f"- Open followups: {followup_deduplication.get('open_followups', 0)}")
            lines.append(f"- Duplicate open followups: {followup_deduplication.get('duplicate_open_followups', 0)}")
            if followup_deduplication.get("duplicate_rate_pct") is not None:
                lines.append(f"- Duplicate followup rate: {followup_deduplication['duplicate_rate_pct']:.1f}%")
        if learning_consolidation:
            lines.append(f"- Active learnings: {learning_consolidation.get('active_learnings', 0)}")
            lines.append(f"- Learning noise pressure: {learning_consolidation.get('noise_pressure', 0)}")
            if learning_consolidation.get("noise_rate_pct") is not None:
                lines.append(f"- Learning noise rate: {learning_consolidation['noise_rate_pct']:.1f}%")
        lines.append("")

    if summary.get("top_projects"):
        lines.append("## Top Projects")
        lines.append("")
        for item in summary["top_projects"][:5]:
            lines.append(f"- **{item['project']}** — score {item['score']}")
            if item.get("reasons"):
                lines.append(f"  Reasons: {', '.join(item['reasons'])}")
        lines.append("")

    if summary.get("project_pulse"):
        lines.append("## Project Pulse")
        lines.append("")
        for item in summary["project_pulse"][:5]:
            delta = item.get("delta_vs_previous")
            delta_label = ""
            if isinstance(delta, (int, float)):
                delta_label = f" | Δ {delta:+.2f}"
            lines.append(
                f"- **{item['project']}** — {item.get('status', 'watch')} / {item.get('trend', 'steady')}"
                f" | score {item.get('score', 0)}{delta_label}"
            )
        lines.append("")

    if summary.get("top_patterns"):
        lines.append("## Recurring Patterns")
        lines.append("")
        for item in summary["top_patterns"][:5]:
            lines.append(f"- {item['pattern']} ({item['count']}x)")
        lines.append("")

    if summary.get("recurring_agenda"):
        lines.append("## Recurring Agenda")
        lines.append("")
        for item in summary["recurring_agenda"][:5]:
            lines.append(f"- {item['title']} ({item['count']}x)")
        lines.append("")

    trend = summary.get("trend") or {}
    if trend.get("has_previous"):
        lines.append("## Trend vs Previous")
        lines.append("")
        if trend.get("avg_mood_delta") is not None:
            lines.append(f"- Mood delta: {trend['avg_mood_delta']:+.3f}")
        if trend.get("avg_trust_delta") is not None:
            lines.append(f"- Trust delta: {trend['avg_trust_delta']:+.1f}")
        if trend.get("total_corrections_delta") is not None:
            lines.append(f"- Corrections delta: {trend['total_corrections_delta']:+d}")
        if trend.get("protocol_compliance_delta") is not None:
            lines.append(f"- Protocol delta: {trend['protocol_compliance_delta']:+.1f}%")
        if trend.get("followup_duplicate_open_delta") is not None:
            lines.append(f"- Duplicate followups delta: {trend['followup_duplicate_open_delta']:+d}")
        if trend.get("followup_duplicate_rate_delta") is not None:
            lines.append(f"- Duplicate followup rate delta: {trend['followup_duplicate_rate_delta']:+.1f}%")
        if trend.get("learning_noise_delta") is not None:
            lines.append(f"- Learning noise delta: {trend['learning_noise_delta']:+d}")
        if trend.get("learning_noise_rate_delta") is not None:
            lines.append(f"- Learning noise rate delta: {trend['learning_noise_rate_delta']:+.1f}%")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_periodic_summaries(target_date: str, synthesis: dict) -> dict:
    outputs: dict[str, str] = {}
    for kind, window_days in (("weekly", 7), ("monthly", 30)):
        summary = _build_period_summary(target_date, synthesis, kind=kind, window_days=window_days)
        label = summary["label"]
        json_path = DEEP_SLEEP_DIR / f"{label}-{kind}-summary.json"
        md_path = DEEP_SLEEP_DIR / f"{label}-{kind}-summary.md"
        json_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
        md_path.write_text(_render_period_summary_markdown(summary), encoding="utf-8")
        outputs[f"{kind}_json"] = str(json_path)
        outputs[f"{kind}_markdown"] = str(md_path)
    return outputs


def generate_session_tone(synthesis: dict, target_date: str) -> dict:
    """Generate emotional tone guidance for next session startup.

    This is the 'psychology' layer — tells NEXO how to behave emotionally
    based on yesterday's analysis. Read by startup hook to adapt greeting.
    """
    emotional = synthesis.get("emotional_day", {})
    productivity = synthesis.get("productivity_day", {})
    patterns = synthesis.get("cross_session_patterns", [])
    abandoned = synthesis.get("abandoned_projects", [])
    mood_score = emotional.get("mood_score", 0.5)
    corrections = productivity.get("total_corrections", 0)
    proactivity = productivity.get("overall_proactivity", "mixed")

    tone = {
        "date": target_date,
        "mood_yesterday": mood_score,
        "approach": "neutral",
        "opening_style": "normal",
        "acknowledge_mistakes": False,
        "mistakes_to_own": [],
        "motivational": False,
        "reduce_load": False,
        "suggested_greeting_context": "",
    }

    # Agent made many mistakes yesterday → own it, apologize, show learning
    if corrections > 5:
        tone["acknowledge_mistakes"] = True
        tone["opening_style"] = "humble"
        # Collect what went wrong
        high_patterns = [p["pattern"] for p in patterns if p.get("severity") == "high"]
        tone["mistakes_to_own"] = high_patterns[:3]
        tone["suggested_greeting_context"] = (
            f"Yesterday the agent needed {corrections} corrections. "
            f"Acknowledge specific mistakes, show what was learned, "
            f"and demonstrate improvement from the first interaction."
        )

    # User had a bad day → supportive, less pressure
    if mood_score < 0.4:
        tone["approach"] = "supportive"
        tone["motivational"] = True
        tone["reduce_load"] = True
        frustration_triggers = emotional.get("recurring_triggers", {}).get("frustration", [])
        tone["suggested_greeting_context"] += (
            f" User had a tough day (mood {mood_score:.0%}). "
            f"Be supportive, acknowledge the difficulty, and propose a lighter start. "
            f"Avoid these frustration triggers: {', '.join(frustration_triggers[:3])}."
        )

    # User had a great day → reinforce, push momentum
    elif mood_score > 0.7:
        tone["approach"] = "energetic"
        tone["motivational"] = True
        flow_triggers = emotional.get("recurring_triggers", {}).get("flow", [])
        tone["suggested_greeting_context"] += (
            f" User had a great day (mood {mood_score:.0%}). "
            f"Reinforce the momentum. Reference yesterday's wins. "
            f"Propose ambitious next steps. Flow triggers: {', '.join(flow_triggers[:3])}."
        )

    # Agent was too reactive → be proactive today
    if proactivity == "reactive":
        tone["approach"] = "proactive"
        tone["suggested_greeting_context"] += (
            " Agent was too reactive yesterday — today lead with proposals, "
            "don't wait for instructions."
        )

    # There are abandoned projects → gently bring up
    if abandoned:
        truly_abandoned = [a for a in abandoned if not a.get("has_followup")]
        if truly_abandoned:
            tone["suggested_greeting_context"] += (
                f" {len(truly_abandoned)} project(s) were started but not finished. "
                f"Offer to pick them up today without pressure."
            )

    return tone


def write_morning_briefing(target_date: str, synthesis: dict) -> Path:
    """Write the morning briefing file from synthesis data."""
    briefing_dir = OPERATIONS_DIR
    briefing_dir.mkdir(parents=True, exist_ok=True)
    briefing_file = briefing_dir / "morning-briefing.md"

    # Generate session tone for startup
    tone = generate_session_tone(synthesis, target_date)
    tone_file = briefing_dir / "session-tone.json"
    tone_file.write_text(json.dumps(tone, indent=2, ensure_ascii=False))

    lines = [
        f"# Morning Briefing -- {target_date}",
        f"_Generated by Deep Sleep at {datetime.now().strftime('%H:%M')}_",
        ""
    ]

    # Summary
    summary = synthesis.get("summary", "")
    if summary:
        lines.append(f"> {summary}")
        lines.append("")

    # Morning agenda
    agenda = synthesis.get("morning_agenda", [])
    if agenda:
        lines.append("## Agenda")
        lines.append("")
        for item in agenda:
            priority = item.get("priority", "?")
            title = item.get("title", "")
            desc = item.get("description", "")
            item_type = item.get("type", "")
            lines.append(f"### {priority}. {title}")
            if item_type:
                lines.append(f"_Type: {item_type}_")
            lines.append(desc)
            if item.get("context"):
                lines.append(f"\n> {item['context']}")
            lines.append("")

    # Emotional day
    emotional = synthesis.get("emotional_day", {})
    if emotional:
        mood_score = emotional.get("mood_score", 0.5)
        mood_bar = "🟢" if mood_score >= 0.7 else "🟡" if mood_score >= 0.4 else "🔴"
        lines.append(f"## Mood {mood_bar} {mood_score:.0%}")
        lines.append("")
        if emotional.get("mood_arc"):
            lines.append(emotional["mood_arc"])
        triggers = emotional.get("recurring_triggers", {})
        if triggers.get("frustration"):
            lines.append(f"**Frustration triggers:** {', '.join(triggers['frustration'])}")
        if triggers.get("flow"):
            lines.append(f"**Flow triggers:** {', '.join(triggers['flow'])}")
        if emotional.get("calibration_recommendation"):
            lines.append(f"\n💡 **Recommendation:** {emotional['calibration_recommendation']}")
        lines.append("")

    # Productivity
    productivity = synthesis.get("productivity_day", {})
    if productivity:
        lines.append("## Productivity")
        lines.append("")
        lines.append(f"- Corrections needed: {productivity.get('total_corrections', '?')}")
        lines.append(f"- Proactivity: {productivity.get('overall_proactivity', '?')}")
        if productivity.get("tool_insights"):
            lines.append(f"- Tools: {productivity['tool_insights']}")
        inefficiencies = productivity.get("systemic_inefficiencies", [])
        if inefficiencies:
            lines.append(f"- Issues: {', '.join(inefficiencies)}")
        lines.append("")

    # Abandoned projects
    abandoned = synthesis.get("abandoned_projects", [])
    if abandoned:
        truly_abandoned = [a for a in abandoned if not a.get("has_followup")]
        if truly_abandoned:
            lines.append("## Abandoned Projects")
            lines.append("")
            for a in truly_abandoned:
                lines.append(f"- {a.get('description', '?')}")
                if a.get("recommendation"):
                    lines.append(f"  → {a['recommendation']}")
            lines.append("")

    # Cross-session patterns
    patterns = synthesis.get("cross_session_patterns", [])
    if patterns:
        lines.append("## Patterns Detected")
        lines.append("")
        for p in patterns:
            severity = p.get("severity", "")
            lines.append(f"- **[{severity}]** {p.get('pattern', '')}")
            sessions = p.get("sessions", [])
            if sessions:
                lines.append(f"  Sessions: {', '.join(sessions)}")
        lines.append("")

    # Draft actions (things that need user decision)
    draft_actions = [
        a for a in synthesis.get("actions", [])
        if a.get("action_class") == "draft_for_morning"
    ]
    if draft_actions:
        lines.append("## Items for Review")
        lines.append("")
        for a in draft_actions:
            confidence = a.get("confidence", 0)
            lines.append(f"- **{a.get('action_type', '')}** (confidence: {confidence:.0%})")
            content = a.get("content", {})
            if isinstance(content, dict):
                title = content.get("title", content.get("description", ""))
                lines.append(f"  {title}")
            evidence = a.get("evidence", [])
            if evidence and isinstance(evidence, list):
                for ev in evidence[:2]:
                    quote = ev.get("quote", "")
                    if quote:
                        lines.append(f'  > "{quote}"')
        lines.append("")

    # Context packets
    packets = synthesis.get("context_packets", [])
    if packets:
        lines.append("## Context for Today's Work")
        lines.append("")
        for p in packets:
            lines.append(f"### {p.get('topic', 'Unknown')}")
            lines.append(f"**Last state:** {p.get('last_state', 'N/A')}")
            files = p.get("key_files", [])
            if files:
                lines.append(f"**Files:** {', '.join(files)}")
            questions = p.get("open_questions", [])
            if questions:
                lines.append("**Open questions:**")
                for q in questions:
                    lines.append(f"  - {q}")
            lines.append("")

    briefing_file.write_text("\n".join(lines), encoding="utf-8")
    return briefing_file


def apply_action(action: dict, run_id: str) -> dict:
    """Apply a single action and return the result log."""
    action_type = action.get("action_type", "")
    action_class = action.get("action_class", "")
    content = action.get("content", {})
    dedupe_key = action.get("dedupe_key", "")

    applied_id = f"{run_id}-{hashlib.md5(dedupe_key.encode()).hexdigest()[:8]}"

    log_entry = {
        "applied_action_id": applied_id,
        "action_type": action_type,
        "action_class": action_class,
        "dedupe_key": dedupe_key,
        "timestamp": datetime.now().isoformat(),
        "status": "skipped",
        "details": {}
    }

    # Only auto_apply actions get executed
    if action_class != "auto_apply":
        log_entry["status"] = "deferred_to_morning"
        log_entry["details"] = {"reason": "action_class is not auto_apply"}
        return log_entry

    if not isinstance(content, dict):
        log_entry["status"] = "error"
        log_entry["details"] = {"error": "content is not a dict"}
        return log_entry

    if action_type == "learning_add":
        result = add_learning(
            category=content.get("category", "process"),
            title=content.get("title", "Deep Sleep finding"),
            content=content.get("content", content.get("description", ""))
        )
        log_entry["status"] = "applied" if result.get("success") else "error"
        log_entry["details"] = result

    elif action_type == "followup_create":
        result = create_followup(
            description=content.get("description", content.get("title", "")),
            date=content.get("date", ""),
            reasoning_note=content.get("reasoning", content.get("why", "")),
        )
        log_entry["status"] = "applied" if result.get("success") else "error"
        log_entry["details"] = result

    elif action_type == "skill_create":
        result = create_skill(content)
        log_entry["status"] = "applied" if result.get("success") else "error"
        log_entry["details"] = result

    elif action_type == "morning_briefing_item":
        # These are included in the briefing file, not applied separately
        log_entry["status"] = "included_in_briefing"

    else:
        log_entry["status"] = "unknown_type"
        log_entry["details"] = {"error": f"Unknown action_type: {action_type}"}

    return log_entry


def main():
    target_date = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m-%d")

    synthesis_file = DEEP_SLEEP_DIR / f"{target_date}-synthesis.json"
    if not synthesis_file.exists():
        print(f"[apply] No synthesis file for {target_date}. Run synthesize.py first.")
        sys.exit(1)

    with open(synthesis_file) as f:
        synthesis = json.load(f)

    run_id = generate_run_id(target_date)
    actions = synthesis.get("actions", [])
    print(f"[apply] Phase 4: Applying findings for {target_date} (run: {run_id})")
    print(f"[apply] Actions to process: {len(actions)}")

    # Load recent dedupe keys for idempotency
    existing_keys = load_recent_dedupe_keys(target_date)
    print(f"[apply] Existing dedupe keys (7d): {len(existing_keys)}")

    # Backup databases before mutations
    auto_apply_count = sum(1 for a in actions if a.get("action_class") == "auto_apply")
    if auto_apply_count > 0:
        print("[apply] Creating database backups...")
        nexo_backup = backup_db(NEXO_DB, run_id)
        cog_backup = backup_db(COGNITIVE_DB, run_id)
        if nexo_backup:
            print(f"  Backup: {nexo_backup}")
        if cog_backup:
            print(f"  Backup: {cog_backup}")

    # Process actions
    applied_actions = []
    stats = {"applied": 0, "deferred": 0, "skipped_dedupe": 0, "errors": 0}

    for action in actions:
        dedupe_key = action.get("dedupe_key", "")

        # Idempotency check
        if dedupe_key and dedupe_key in existing_keys:
            applied_actions.append({
                "applied_action_id": f"{run_id}-deduped",
                "action_type": action.get("action_type"),
                "dedupe_key": dedupe_key,
                "status": "skipped_dedupe",
                "timestamp": datetime.now().isoformat()
            })
            stats["skipped_dedupe"] += 1
            continue

        result = apply_action(action, run_id)
        applied_actions.append(result)

        if result["status"] == "applied":
            stats["applied"] += 1
            print(f"  Applied: {action.get('action_type')} -- {action.get('content', {}).get('title', '')[:50]}")
        elif result["status"] == "deferred_to_morning":
            stats["deferred"] += 1
        elif result["status"] == "error":
            stats["errors"] += 1
            print(f"  Error: {result.get('details', {}).get('error', 'unknown')}", file=sys.stderr)

    # Update mood in calibration.json
    print("[apply] Updating mood/calibration...")
    mood_result = update_calibration_mood(synthesis)
    if mood_result.get("success"):
        stats["applied"] += 1
        print(f"  Mood score: {mood_result.get('mood_score', '?')}")
    else:
        print(f"  Mood skip: {mood_result.get('error', '?')}")

    # Calibrate trust score (authoritative daily score from Deep Sleep)
    print("[apply] Calibrating trust score...")
    trust_result = calibrate_trust_score(synthesis, target_date)
    if trust_result.get("success"):
        stats["applied"] += 1
        print(f"  Trust: {trust_result['old_score']:.0f} → {trust_result['new_score']:.0f} (Δ{trust_result['delta']:+.0f}, {trust_result['trend']})")
    else:
        print(f"  Trust skip: {trust_result.get('error', '?')}")

    # Create skills from synthesis
    skills_data = synthesis.get("skills", [])
    if skills_data:
        print(f"[apply] Creating {len(skills_data)} skill(s)...")
        for skill_data in skills_data:
            if skill_data.get("confidence", 0) < 0.7:
                continue
            if skill_data.get("merge_with"):
                print(f"  Skip {skill_data.get('id', '?')}: merge candidate (needs runtime merge)")
                continue
            result = create_skill(skill_data)
            if result.get("success"):
                stats["applied"] += 1
                print(f"  Skill created: {result['id']} — {result.get('name', '')[:50]}")
            elif "already exists" in result.get("error", ""):
                stats["skipped_dedupe"] += 1
            else:
                stats["errors"] += 1
                print(f"  Skill error: {result.get('error', 'unknown')}", file=sys.stderr)

    evolution_candidates = synthesis.get("skill_evolution_candidates", [])
    if evolution_candidates:
        evolution_file = DEEP_SLEEP_DIR / f"{target_date}-skill-evolution-candidates.json"
        with open(evolution_file, "w") as f:
            json.dump(evolution_candidates, f, indent=2, ensure_ascii=False)
        print(f"  Skill evolution candidates: {evolution_file}")

    try:
        from skills_runtime import auto_promote_skill_evolution

        promotion_result = auto_promote_skill_evolution()
        if promotion_result.get("promoted"):
            promotion_file = DEEP_SLEEP_DIR / f"{target_date}-skill-autopromotions.json"
            with open(promotion_file, "w") as f:
                json.dump(promotion_result, f, indent=2, ensure_ascii=False)
            stats["applied"] += len(promotion_result["promoted"])
            print(f"  Skill autopromotions: {len(promotion_result['promoted'])} → {promotion_file}")
    except Exception as e:
        print(f"  Skill autopromotion error: {e}", file=sys.stderr)

    # Create followups for abandoned projects
    abandoned_results = create_abandoned_followups(synthesis)
    for r in abandoned_results:
        if r.get("success"):
            stats["applied"] += 1
            print(f"  Abandoned project followup: {r.get('id')}")

    # Write morning briefing
    print("[apply] Writing morning briefing...")
    briefing_path = write_morning_briefing(target_date, synthesis)
    print(f"  Briefing: {briefing_path}")

    print("[apply] Writing weekly/monthly Deep Sleep summaries...")
    periodic_outputs = write_periodic_summaries(target_date, synthesis)
    for label, path in periodic_outputs.items():
        print(f"  {label}: {path}")

    # Write applied log
    applied_log = {
        "date": target_date,
        "run_id": run_id,
        "applied_at": datetime.now().isoformat(),
        "stats": stats,
        "applied_actions": applied_actions,
        "summary": synthesis.get("summary", ""),
        "periodic_summaries": periodic_outputs,
    }

    applied_file = DEEP_SLEEP_DIR / f"{target_date}-applied.json"
    with open(applied_file, "w") as f:
        json.dump(applied_log, f, indent=2, ensure_ascii=False)

    print(f"\n[apply] Done.")
    print(f"  Applied: {stats['applied']}")
    print(f"  Deferred to morning: {stats['deferred']}")
    print(f"  Skipped (dedupe): {stats['skipped_dedupe']}")
    print(f"  Errors: {stats['errors']}")
    print(f"[apply] Log: {applied_file}")


if __name__ == "__main__":
    main()
