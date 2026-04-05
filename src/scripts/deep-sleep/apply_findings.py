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
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
NEXO_CODE = Path(os.environ.get("NEXO_CODE", str(Path(__file__).resolve().parents[2])))
if str(NEXO_CODE) not in sys.path:
    sys.path.insert(0, str(NEXO_CODE))

DEEP_SLEEP_DIR = NEXO_HOME / "operations" / "deep-sleep"
NEXO_DB = NEXO_HOME / "data" / "nexo.db"
COGNITIVE_DB = NEXO_HOME / "data" / "cognitive.db"
OPERATIONS_DIR = NEXO_HOME / "operations"
BACKUP_DIR = DEEP_SLEEP_DIR  # backups stored alongside outputs


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


def add_learning(category: str, title: str, content: str) -> dict:
    """Add a learning to nexo.db. Returns result dict."""
    if not NEXO_DB.exists():
        return {"success": False, "error": "nexo.db not found"}
    try:
        now = datetime.now().timestamp()
        conn = sqlite3.connect(str(NEXO_DB))
        cursor = conn.execute(
            "INSERT INTO learnings (category, title, content, created_at, updated_at, reasoning) VALUES (?, ?, ?, ?, ?, ?)",
            (category, title, content, now, now, "Deep Sleep v2 overnight analysis")
        )
        learning_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return {"success": True, "id": learning_id}
    except Exception as e:
        return {"success": False, "error": str(e)}


def create_followup(description: str, date: str = "") -> dict:
    """Create a followup in nexo.db. Returns result dict."""
    if not NEXO_DB.exists():
        return {"success": False, "error": "nexo.db not found"}
    try:
        now = datetime.now().timestamp()
        # Generate a deterministic ID
        fid = "NF-DS-" + hashlib.md5(description.encode()).hexdigest()[:8].upper()
        conn = sqlite3.connect(str(NEXO_DB))
        conn.execute(
            "INSERT OR IGNORE INTO followups (id, description, date, status, created_at, updated_at, reasoning) VALUES (?, ?, ?, 'PENDING', ?, ?, ?)",
            (fid, description, date, now, now, "Deep Sleep v2 overnight analysis")
        )
        conn.commit()
        conn.close()
        return {"success": True, "id": fid}
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


def _build_period_summary(target_date: str, synthesis: dict, *, kind: str, window_days: int) -> dict:
    target_day = datetime.strptime(target_date, "%Y-%m-%d")
    window_start = (target_day - timedelta(days=max(0, window_days - 1))).strftime("%Y-%m-%d")
    label = (
        f"{target_day.isocalendar().year}-W{target_day.isocalendar().week:02d}"
        if kind == "weekly"
        else target_day.strftime("%Y-%m")
    )
    syntheses = _load_period_syntheses(target_date, window_days=window_days)
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

    summary_parts = [f"{len(syntheses)} Deep Sleep run(s)"]
    if top_projects:
        summary_parts.append(f"top focus: {top_projects[0]['project']}")
    if top_patterns:
        summary_parts.append(f"recurring pattern: {top_patterns[0]['pattern']}")
    if avg_trust is not None:
        summary_parts.append(f"avg trust {avg_trust:.1f}")
    summary = " | ".join(summary_parts)

    return {
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
        "top_patterns": top_patterns,
        "recurring_agenda": recurring_agenda,
        "summary": summary,
    }


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

    if summary.get("top_projects"):
        lines.append("## Top Projects")
        lines.append("")
        for item in summary["top_projects"][:5]:
            lines.append(f"- **{item['project']}** — score {item['score']}")
            if item.get("reasons"):
                lines.append(f"  Reasons: {', '.join(item['reasons'])}")
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
            date=content.get("date", "")
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
