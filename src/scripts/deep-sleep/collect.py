#!/usr/bin/env python3
from __future__ import annotations
"""
Deep Sleep v2 -- Phase 1: Collect all context for overnight analysis.

Gathers transcripts, DB data, logs, and discovered files into a single
plain-text context file that subsequent phases read via the configured
automation backend.

Environment variables:
  NEXO_HOME  -- root of the NEXO installation (default: ~/.nexo)
  NEXO_CODE  -- path to the NEXO source repo (optional, for self-analysis)
"""
import json
import os
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

_DEFAULT_RUNTIME_ROOT = Path(__file__).resolve().parents[2]
NEXO_CODE = Path(os.environ.get("NEXO_CODE", str(_DEFAULT_RUNTIME_ROOT)))
if str(NEXO_CODE) not in sys.path:
    sys.path.insert(0, str(NEXO_CODE))

import transcript_utils as _transcripts

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
DEEP_SLEEP_DIR = NEXO_HOME / "operations" / "deep-sleep"
NEXO_DB = NEXO_HOME / "data" / "nexo.db"
COGNITIVE_DB = NEXO_HOME / "data" / "cognitive.db"
_TABLE_COLUMNS_CACHE: dict[tuple[str, str], set[str]] = {}

MIN_USER_MESSAGES = 3  # Skip trivial sessions

# Patterns that indicate sensitive data (passwords, tokens, API keys, etc.)
_SENSITIVE_PATTERNS = re.compile(
    r'(?:'
    r'sk-ant-[A-Za-z0-9_-]+'           # Anthropic API keys
    r'|shpat_[A-Fa-f0-9]+'             # Shopify admin tokens
    r'|shpss_[A-Fa-f0-9]+'             # Shopify shared secret
    r'|sk-[A-Za-z0-9]{20,}'            # OpenAI-style keys
    r'|ghp_[A-Za-z0-9]{36,}'           # GitHub PATs
    r'|gho_[A-Za-z0-9]{36,}'           # GitHub OAuth tokens
    r'|AIza[A-Za-z0-9_-]{35}'          # Google API keys
    r'|ya29\.[A-Za-z0-9_-]+'           # Google OAuth tokens
    r'|xox[bpsa]-[A-Za-z0-9-]+'        # Slack tokens
    r'|EAAG[A-Za-z0-9]+'              # Meta/Facebook tokens
    r'|[Pp]assword\s*[:=]\s*\S+'       # password: value or password=value
    r'|[Ss]ecret\s*[:=]\s*\S+'         # secret: value
    r'|[Tt]oken\s*[:=]\s*\S+'          # token: value
    r'|[Aa]pi[_-]?[Kk]ey\s*[:=]\s*\S+'  # api_key: value
    r')'
)


def _redact_sensitive(text: str) -> str:
    """Replace sensitive patterns in text with [REDACTED]."""
    return _SENSITIVE_PATTERNS.sub('[REDACTED]', text)


# ── Transcript collection (Claude Code + Codex) ────────────────────────────


def _session_identifier(client: str, session_file: str) -> str:
    return f"{client}:{session_file}"


def find_claude_session_files() -> list[Path]:
    """Find Claude Code session JSONL files under ~/.claude/projects."""
    return _transcripts.find_claude_session_files()


def find_codex_session_files() -> list[Path]:
    """Find Codex session JSONL files under ~/.codex/sessions and archived_sessions."""
    return _transcripts.find_codex_session_files()


def extract_claude_session(jsonl_path: Path) -> dict | None:
    """Extract clean transcript from a Claude Code JSONL session."""
    return _transcripts.extract_claude_session(jsonl_path)


def extract_codex_session(jsonl_path: Path) -> dict | None:
    """Extract clean transcript from a Codex JSONL session."""
    return _transcripts.extract_codex_session(jsonl_path)


def collect_transcripts_since(since_iso: str, until_iso: str = "") -> list[dict]:
    """Collect all sessions modified after `since_iso` (exclusive) up to `until_iso` (inclusive).

    Uses a watermark approach: deep sleep tracks the last processed timestamp
    so nothing is missed regardless of when sessions happen (day, night, etc.).
    """
    return _transcripts.collect_transcripts_since(since_iso, until_iso)


# ── Database queries ──────────────────────────────────────────────────────


def safe_query(db_path: Path, query: str, params: tuple = ()) -> list[dict]:
    """Run a query and return rows as dicts. Returns [] on any error."""
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, params).fetchall()
        result = [dict(r) for r in rows]
        conn.close()
        return result
    except Exception as e:
        print(f"  [collect] DB query error ({db_path.name}): {e}", file=sys.stderr)
        return []


def _table_columns(db_path: Path, table_name: str) -> set[str]:
    cache_key = (str(db_path), table_name)
    cached = _TABLE_COLUMNS_CACHE.get(cache_key)
    if cached is not None:
        return cached
    if not db_path.exists():
        _TABLE_COLUMNS_CACHE[cache_key] = set()
        return set()
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        conn.close()
    except Exception:
        _TABLE_COLUMNS_CACHE[cache_key] = set()
        return set()
    columns = {str(row["name"]) for row in rows}
    _TABLE_COLUMNS_CACHE[cache_key] = columns
    return columns


def _optional_column_sql(db_path: Path, table_name: str, column_name: str, default_sql: str = "''") -> str:
    if column_name in _table_columns(db_path, table_name):
        return column_name
    return f"{default_sql} AS {column_name}"


def collect_followups() -> list[dict]:
    """Active followups from nexo.db."""
    return safe_query(
        NEXO_DB,
        "SELECT * FROM followups WHERE status NOT IN ('COMPLETED', 'CANCELLED') ORDER BY date ASC"
    )


def collect_learnings() -> list[dict]:
    """Active learnings from nexo.db."""
    return safe_query(NEXO_DB, "SELECT * FROM learnings ORDER BY updated_at DESC LIMIT 200")


def collect_diaries(target_date: str) -> list[dict]:
    """Today's session diaries."""
    # Diaries store created_at as unix timestamp or ISO string -- handle both
    start_ts = datetime.strptime(target_date, "%Y-%m-%d").timestamp()
    end_ts = start_ts + 86400
    rows = safe_query(
        NEXO_DB,
        "SELECT * FROM session_diary WHERE created_at >= ? AND created_at < ? ORDER BY created_at ASC",
        (start_ts, end_ts)
    )
    if not rows:
        # Try ISO format
        rows = safe_query(
            NEXO_DB,
            "SELECT * FROM session_diary WHERE created_at >= ? AND created_at < ? ORDER BY created_at ASC",
            (target_date + "T00:00:00", target_date + "T23:59:59")
        )
    return rows


def collect_trust_score() -> list[dict]:
    """Current trust score and 7-day history from cognitive.db."""
    return safe_query(
        COGNITIVE_DB,
        "SELECT * FROM trust_score ORDER BY rowid DESC LIMIT 1"
    )


def _parse_diary_created_at(value) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and str(value).strip().isdigit()):
            return datetime.fromtimestamp(float(value))
    except Exception:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00").replace("+00:00", ""))
    except Exception:
        return None


def _sample_evenly(rows: list[dict], limit: int) -> list[dict]:
    if limit <= 0 or not rows:
        return []
    if len(rows) <= limit:
        return list(rows)
    if limit == 1:
        return [rows[-1]]
    step = (len(rows) - 1) / float(limit - 1)
    indices = sorted({round(i * step) for i in range(limit)})
    sampled = [rows[idx] for idx in indices]
    i = 0
    while len(sampled) < limit and i < len(rows):
        if rows[i] not in sampled:
            sampled.append(rows[i])
        i += 1
    return sampled[:limit]


def _compact_diary_row(row: dict) -> dict:
    created = _parse_diary_created_at(row.get("created_at"))
    return {
        "session_id": row.get("session_id", ""),
        "created_at": created.isoformat() if created else str(row.get("created_at", "")),
        "domain": row.get("domain", "") or "",
        "mental_state": row.get("mental_state", "") or "",
        "summary": str(row.get("summary", "") or "")[:240],
        "self_critique": str(row.get("self_critique", "") or "")[:240],
        "source": row.get("source", "") or "",
    }


def _load_project_aliases() -> dict[str, set[str]]:
    atlas_path = NEXO_HOME / "brain" / "project-atlas.json"
    aliases: dict[str, set[str]] = {}
    if not atlas_path.is_file():
        return aliases
    try:
        payload = json.loads(atlas_path.read_text())
    except Exception:
        return aliases
    if not isinstance(payload, dict):
        return aliases
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


def _compact_periodic_summary(data: dict) -> dict:
    return {
        "label": data.get("label", ""),
        "window_start": data.get("window_start", ""),
        "window_end": data.get("window_end", ""),
        "summary": str(data.get("summary", "") or "")[:320],
        "top_projects": data.get("top_projects", [])[:4],
        "top_patterns": data.get("top_patterns", [])[:4],
        "avg_mood_score": data.get("avg_mood_score"),
        "avg_trust_score": data.get("avg_trust_score"),
    }


def _load_periodic_summaries(target_date: str, *, kind: str, limit: int = 2) -> list[dict]:
    target_day = datetime.strptime(target_date, "%Y-%m-%d")
    summaries: list[tuple[str, dict]] = []
    pattern = "*-weekly-summary.json" if kind == "weekly" else "*-monthly-summary.json"
    for path in sorted(DEEP_SLEEP_DIR.glob(pattern)):
        try:
            payload = json.loads(path.read_text())
        except Exception:
            continue
        window_end_raw = str(payload.get("window_end", "") or "")
        parsed = _parse_diary_created_at(window_end_raw)
        if parsed and parsed >= target_day:
            continue
        summaries.append((window_end_raw, _compact_periodic_summary(payload)))
    summaries.sort(key=lambda item: item[0], reverse=True)
    return [item for _, item in summaries[:limit]]


def _project_priority_signals(target_day: datetime, compact_diaries: list[dict]) -> list[dict]:
    alias_map = _load_project_aliases()
    scoreboard: dict[str, dict] = {}

    def bump(project: str, score: float, signal_key: str, reason: str) -> None:
        if not project:
            return
        slot = scoreboard.setdefault(
            project,
            {
                "project": project,
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

    for row in compact_diaries:
        created = _parse_diary_created_at(row.get("created_at"))
        recency_bonus = 1.0
        if created:
            age_days = max(0.0, (target_day - created).total_seconds() / 86400)
            recency_bonus = 1.4 if age_days <= 7 else 1.0
        candidates = set()
        domain = str(row.get("domain", "") or "").strip().lower()
        if domain:
            candidates.add(domain)
        candidates |= _match_projects(" ".join([row.get("summary", ""), row.get("self_critique", "")]), alias_map)
        for project in candidates:
            bump(project, 3.0 * recency_bonus, "diary_sessions", "recent session diary activity")

    learning_priority_sql = _optional_column_sql(NEXO_DB, "learnings", "priority", "'medium'")
    learning_weight_sql = _optional_column_sql(NEXO_DB, "learnings", "weight", "0")
    learning_applies_sql = _optional_column_sql(NEXO_DB, "learnings", "applies_to", "''")
    learning_rows = safe_query(
        NEXO_DB,
        f"SELECT category, title, content, created_at, updated_at, {learning_priority_sql}, "
        f"{learning_weight_sql}, {learning_applies_sql} FROM learnings "
        "ORDER BY COALESCE(updated_at, created_at) DESC LIMIT 160",
    )
    for row in learning_rows:
        text = " ".join(
            [
                str(row.get("applies_to", "") or ""),
                str(row.get("title", "") or ""),
                str(row.get("content", "") or ""),
                str(row.get("category", "") or ""),
            ]
        )
        matched = _match_projects(text, alias_map)
        if not matched:
            continue
        weight = float(row.get("weight", 0) or 0)
        score = 1.0 + _priority_weight(row.get("priority")) + min(2.0, max(0.0, weight))
        for project in matched:
            bump(project, score, "learnings", "recent leverage-bearing learning")

    followup_priority_sql = _optional_column_sql(NEXO_DB, "followups", "priority", "'medium'")
    followup_reasoning_sql = _optional_column_sql(NEXO_DB, "followups", "reasoning", "''")
    followup_rows = safe_query(
        NEXO_DB,
        f"SELECT id, description, date, status, {followup_priority_sql}, created_at, updated_at, "
        f"{followup_reasoning_sql} FROM followups "
        "WHERE status NOT IN ('COMPLETED', 'CANCELLED') ORDER BY date ASC, created_at ASC LIMIT 120",
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
        due_value = str(row.get("date", "") or "")
        try:
            if due_value:
                due_dt = datetime.strptime(due_value[:10], "%Y-%m-%d")
                if due_dt <= target_day:
                    overdue_bonus = 1.5
        except Exception:
            overdue_bonus = 0.0
        score = 1.5 + _priority_weight(row.get("priority")) + overdue_bonus
        for project in matched:
            bump(project, score, "followups", "open followup pressure")

    decision_status_sql = _optional_column_sql(NEXO_DB, "decisions", "status", "''")
    decision_reasoning_sql = _optional_column_sql(NEXO_DB, "decisions", "reasoning", "''")
    decision_review_due_sql = _optional_column_sql(NEXO_DB, "decisions", "review_due_at", "NULL")
    decision_rows = safe_query(
        NEXO_DB,
        f"SELECT domain, outcome, {decision_status_sql}, {decision_reasoning_sql}, decision, based_on, created_at, "
        f"{decision_review_due_sql} FROM decisions "
        "ORDER BY COALESCE(created_at, review_due_at) DESC LIMIT 120",
    )
    for row in decision_rows:
        matched = set()
        domain = str(row.get("domain", "") or "").strip().lower()
        if domain:
            matched.add(domain)
        matched |= _match_projects(
            " ".join(
                [
                    str(row.get("reasoning", "") or ""),
                    str(row.get("decision", "") or ""),
                    str(row.get("based_on", "") or ""),
                    str(row.get("outcome", "") or ""),
                    str(row.get("status", "") or ""),
                ]
            ),
            alias_map,
        )
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


def collect_long_horizon_context(
    target_date: str,
    *,
    horizon_days: int = 60,
    recent_days: int = 14,
    max_diaries: int = 20,
    max_sessions: int = 12,
) -> dict:
    """Build long-horizon context blending recent and older evidence.

    Strategy:
    - recent 70% from the last `recent_days`
    - older 30% sampled evenly from the rest of the `horizon_days` window
    """
    target_day = datetime.strptime(target_date, "%Y-%m-%d")
    horizon_start = target_day - timedelta(days=horizon_days)
    recent_start = target_day - timedelta(days=recent_days)

    diary_rows = safe_query(
        NEXO_DB,
        "SELECT session_id, created_at, summary, mental_state, domain, self_critique, source "
        "FROM session_diary ORDER BY created_at ASC"
    )
    compact_diaries = []
    for row in diary_rows:
        created = _parse_diary_created_at(row.get("created_at"))
        if not created:
            continue
        if not (horizon_start <= created < target_day):
            continue
        compact_diaries.append(_compact_diary_row(row))

    recent_diaries = [row for row in compact_diaries if _parse_diary_created_at(row.get("created_at")) and _parse_diary_created_at(row.get("created_at")) >= recent_start]
    older_diaries = [row for row in compact_diaries if row not in recent_diaries]
    recent_quota = max(1, round(max_diaries * 0.7))
    older_quota = max(0, max_diaries - recent_quota)
    sampled_diaries = recent_diaries[-recent_quota:] + _sample_evenly(older_diaries, older_quota)
    sampled_diaries.sort(key=lambda row: row.get("created_at", ""))

    recurring_domains = Counter(row["domain"] for row in compact_diaries if row.get("domain"))
    recurring_states = Counter(row["mental_state"] for row in compact_diaries if row.get("mental_state"))
    recurring_critiques = Counter(row["self_critique"] for row in compact_diaries if row.get("self_critique"))

    learning_reasoning_sql = _optional_column_sql(NEXO_DB, "learnings", "reasoning", "''")
    learning_prevention_sql = _optional_column_sql(NEXO_DB, "learnings", "prevention", "''")
    learning_applies_sql = _optional_column_sql(NEXO_DB, "learnings", "applies_to", "''")
    learning_rows = safe_query(
        NEXO_DB,
        f"SELECT category, title, content, created_at, updated_at, {learning_reasoning_sql}, "
        f"{learning_prevention_sql}, {learning_applies_sql} "
        "FROM learnings ORDER BY COALESCE(updated_at, created_at) DESC LIMIT 120"
    )
    long_horizon_learnings = []
    for row in learning_rows:
        long_horizon_learnings.append({
            "category": row.get("category", ""),
            "title": str(row.get("title", "") or "")[:140],
            "content": str(row.get("content", "") or "")[:260],
            "reasoning": str(row.get("reasoning", "") or "")[:180],
            "prevention": str(row.get("prevention", "") or "")[:180],
            "applies_to": str(row.get("applies_to", "") or "")[:180],
            "updated_at": str(row.get("updated_at", "") or row.get("created_at", "")),
        })
    long_horizon_learnings = long_horizon_learnings[:24]

    transcript_candidates: list[dict] = []
    transcript_files: list[tuple[str, Path]] = [
        ("claude_code", path) for path in find_claude_session_files()
    ] + [
        ("codex", path) for path in find_codex_session_files()
    ]
    horizon_end = target_day
    for client, path in transcript_files:
        try:
            modified = datetime.fromtimestamp(path.stat().st_mtime)
        except OSError:
            continue
        if not (horizon_start <= modified < horizon_end):
            continue
        transcript_candidates.append({
            "client": client,
            "session_file": _session_identifier(client, path.name),
            "modified": modified.isoformat(),
            "session_path": str(path),
        })
    transcript_candidates.sort(key=lambda row: row["modified"])
    recent_sessions = [row for row in transcript_candidates if datetime.fromisoformat(row["modified"]) >= recent_start]
    older_sessions = [row for row in transcript_candidates if row not in recent_sessions]
    recent_session_quota = max(1, round(max_sessions * 0.7))
    older_session_quota = max(0, max_sessions - recent_session_quota)
    sampled_sessions = recent_sessions[-recent_session_quota:] + _sample_evenly(older_sessions, older_session_quota)
    sampled_sessions.sort(key=lambda row: row["modified"])

    stale_followups = safe_query(
        NEXO_DB,
        "SELECT id, description, date, status, created_at, updated_at FROM followups "
        "WHERE status NOT IN ('COMPLETED', 'CANCELLED') ORDER BY date ASC, created_at ASC LIMIT 50"
    )
    older_than_week = []
    week_ago = target_day - timedelta(days=7)
    for row in stale_followups:
        created = _parse_diary_created_at(row.get("created_at"))
        if created and created < week_ago:
            older_than_week.append({
                "id": row.get("id", ""),
                "description": str(row.get("description", "") or "")[:180],
                "date": row.get("date", ""),
                "status": row.get("status", ""),
                "created_at": created.isoformat(),
            })

    weekly_summaries = _load_periodic_summaries(target_date, kind="weekly", limit=2)
    monthly_summaries = _load_periodic_summaries(target_date, kind="monthly", limit=2)
    project_priority_signals = _project_priority_signals(target_day, compact_diaries)

    return {
        "horizon_days": horizon_days,
        "recent_window_days": recent_days,
        "sample_strategy": "70% recent + 30% older evenly sampled",
        "historical_diaries": sampled_diaries,
        "historical_sessions": sampled_sessions,
        "historical_learnings": long_horizon_learnings,
        "recurring_domains": recurring_domains.most_common(8),
        "recurring_mental_states": recurring_states.most_common(8),
        "recurring_self_critiques": recurring_critiques.most_common(6),
        "stale_followups": older_than_week[:12],
        "project_priority_signals": project_priority_signals,
        "weekly_summaries": weekly_summaries,
        "monthly_summaries": monthly_summaries,
    }


# ── Discovery: scan NEXO_HOME for non-core content ───────────────────────

CORE_DIRS = {"data", "operations", "logs", "coordination", "brain"}
CORE_FILES = {"config.json", "nexo.db", "cognitive.db"}


def discover_extras() -> list[dict]:
    """Scan NEXO_HOME for non-core directories and files."""
    extras = []
    if not NEXO_HOME.exists():
        return extras

    for item in sorted(NEXO_HOME.iterdir()):
        name = item.name
        if name.startswith("."):
            continue
        if name in CORE_DIRS or name in CORE_FILES:
            continue

        entry = {"name": name, "path": str(item), "type": "dir" if item.is_dir() else "file"}

        if item.is_dir():
            # Count contents and list interesting files
            files = list(item.rglob("*"))
            entry["file_count"] = len([f for f in files if f.is_file()])
            entry["notable_files"] = [
                str(f.relative_to(item))
                for f in files
                if f.is_file() and f.suffix in (".py", ".sh", ".json", ".db", ".log", ".sqlite")
            ][:20]
        elif item.is_file():
            entry["size"] = item.stat().st_size

        extras.append(entry)

    return extras


# ── LaunchAgent logs ──────────────────────────────────────────────────────


def collect_error_logs(target_date: str) -> list[dict]:
    """Scan NEXO_HOME/logs/ for lines containing errors from today."""
    log_dir = NEXO_HOME / "logs"
    if not log_dir.exists():
        return []

    errors = []
    for log_file in sorted(log_dir.glob("*.log")):
        try:
            lines = log_file.read_text(errors="replace").splitlines()
        except Exception:
            continue

        file_errors = []
        for i, line in enumerate(lines):
            # Match lines from today that contain error indicators
            if target_date in line and any(
                kw in line.lower() for kw in ("error", "exception", "traceback", "failed", "fatal", "critical")
            ):
                # Include surrounding context (1 line before, 2 after)
                start = max(0, i - 1)
                end = min(len(lines), i + 3)
                file_errors.append({
                    "line": i + 1,
                    "context": "\n".join(lines[start:end])
                })

        if file_errors:
            errors.append({
                "file": log_file.name,
                "path": str(log_file),
                "errors": file_errors[:50]  # Cap per file
            })

    return errors


# ── Format output as plain text ───────────────────────────────────────────


def format_section(title: str, data, indent: int = 0) -> str:
    """Format a data section as readable plain text."""
    prefix = "  " * indent
    lines = [f"\n{'=' * 70}", f"{title}", f"{'=' * 70}"]

    if isinstance(data, list):
        if not data:
            lines.append(f"{prefix}(none)")
        else:
            for i, item in enumerate(data):
                lines.append(f"\n{prefix}--- [{i + 1}] ---")
                if isinstance(item, dict):
                    for k, v in item.items():
                        val_str = str(v)
                        if len(val_str) > 500:
                            val_str = val_str[:500] + "..."
                        lines.append(f"{prefix}  {k}: {val_str}")
                else:
                    lines.append(f"{prefix}  {item}")
    elif isinstance(data, dict):
        for k, v in data.items():
            val_str = str(v)
            if len(val_str) > 500:
                val_str = val_str[:500] + "..."
            lines.append(f"{prefix}{k}: {val_str}")
    elif isinstance(data, str):
        lines.append(data)
    else:
        lines.append(str(data))

    return "\n".join(lines)


def format_transcripts(sessions: list[dict]) -> str:
    """Format transcripts in a readable way for Claude to analyze."""
    lines = [f"\n{'=' * 70}", "SESSION TRANSCRIPTS", f"{'=' * 70}"]
    lines.append(f"Total sessions: {len(sessions)}")

    for i, session in enumerate(sessions):
        lines.append(f"\n{'─' * 60}")
        lines.append(f"SESSION {i + 1}: {session['session_file']}")
        lines.append(f"Client: {session.get('client', 'unknown')}")
        if session.get("source"):
            lines.append(f"Source: {session['source']}")
        lines.append(f"Modified: {session['modified']}")
        lines.append(f"Messages: {session['message_count']}, Tool uses: {session['tool_use_count']}")
        lines.append(f"{'─' * 60}")

        for msg in session["messages"]:
            role = "USER" if msg["role"] == "user" else "AGENT"
            idx = msg.get("index", "?")
            lines.append(f"\n[{role} @{idx}]")
            lines.append(_redact_sensitive(msg["text"]))

        if session["tool_uses"]:
            lines.append(f"\n  -- Tool usage log --")
            for tu in session["tool_uses"]:
                file_info = f" [{_redact_sensitive(tu['file'][:80])}]" if tu.get("file") else ""
                lines.append(f"  - {tu['tool']}{file_info}")

    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────


def main():
    # Watermark-based collection: since_iso and until_iso passed by the wrapper script
    # argv[1] = run_id (date label for output files)
    # argv[2] = since_iso (exclusive lower bound, e.g. "2026-04-01T04:30:00")
    # argv[3] = until_iso (inclusive upper bound, e.g. "2026-04-02T04:30:00") — optional, defaults to now
    run_id = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m-%d")
    since_iso = sys.argv[2] if len(sys.argv) > 2 else ""
    until_iso = sys.argv[3] if len(sys.argv) > 3 else ""

    DEEP_SLEEP_DIR.mkdir(parents=True, exist_ok=True)

    print(f"[collect] Phase 1: Collecting context (run_id={run_id})")

    # 1. Transcripts — watermark-based
    if since_iso:
        print(f"[collect] Gathering transcripts since {since_iso}" + (f" until {until_iso}" if until_iso else ""))
        sessions = collect_transcripts_since(since_iso, until_iso)
    else:
        # Fallback: collect everything from last 48h (safe catch-all)
        fallback_since = (datetime.now() - timedelta(hours=48)).isoformat()
        print(f"[collect] No watermark — collecting last 48h since {fallback_since}")
        sessions = collect_transcripts_since(fallback_since)
    print(f"  Found {len(sessions)} sessions")

    if not sessions:
        print(f"[collect] No new sessions found. Writing minimal context file.")
        output_file = DEEP_SLEEP_DIR / f"{run_id}-context.txt"
        output_file.write_text(
            f"Deep Sleep Context for {run_id}\n\nNo sessions found.\n"
        )
        print(f"[collect] Output: {output_file}")
        return

    target_date = run_id  # Keep variable name for downstream compat

    # 2. Core DB data
    print("[collect] Querying databases...")
    followups = collect_followups()
    print(f"  Active followups: {len(followups)}")

    learnings = collect_learnings()
    print(f"  Learnings: {len(learnings)}")

    diaries = collect_diaries(target_date)
    print(f"  Diaries today: {len(diaries)}")

    trust_history = collect_trust_score()
    print(f"  Trust events (7d): {len(trust_history)}")

    # 3. Discovery
    print("[collect] Scanning for non-core content...")
    extras = discover_extras()
    print(f"  Discovered {len(extras)} extra items")

    # 4. Error logs
    print("[collect] Checking error logs...")
    error_logs = collect_error_logs(target_date)
    print(f"  Log files with errors: {len(error_logs)}")

    print("[collect] Building long-horizon context...")
    long_horizon = collect_long_horizon_context(target_date)
    print(
        "  Long horizon: "
        f"{len(long_horizon.get('historical_diaries', []))} diary samples, "
        f"{len(long_horizon.get('historical_sessions', []))} session samples"
    )

    # 5. Build per-session files + shared context
    date_dir = DEEP_SLEEP_DIR / target_date
    date_dir.mkdir(parents=True, exist_ok=True)
    print(f"[collect] Writing session files to {date_dir}/")

    # Shared context (followups, learnings, diaries, etc.) — one file
    shared_parts = [
        f"Deep Sleep Shared Context -- {target_date}",
        f"Generated at: {datetime.now().isoformat()}",
        f"NEXO_HOME: {NEXO_HOME}",
        f"Sessions: {len(sessions)}",
    ]
    shared_parts.append(format_section("ACTIVE FOLLOWUPS", followups))
    shared_parts.append(format_section("LEARNINGS (recent 200)", learnings))
    shared_parts.append(format_section("SESSION DIARIES TODAY", diaries))
    shared_parts.append(format_section("TRUST SCORE HISTORY (7d)", trust_history))
    shared_parts.append(format_section("DISCOVERED NON-CORE CONTENT", extras))
    shared_parts.append(format_section("ERROR LOGS", error_logs))
    shared_parts.append(format_section("LONG-HORIZON CONTEXT (60d blend)", long_horizon))

    shared_text = "\n".join(shared_parts)
    shared_file = date_dir / "shared-context.txt"
    shared_file.write_text(shared_text, encoding="utf-8")
    print(f"  Shared context: {len(shared_text) / 1024:.0f} KB")

    long_horizon_file = date_dir / "long-horizon-context.json"
    long_horizon_file.write_text(json.dumps(long_horizon, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Long horizon JSON: {long_horizon_file.name}")

    # Individual session files
    session_files_written = []
    session_txt_map = {}
    total_size = len(shared_text.encode("utf-8"))
    for i, session in enumerate(sessions):
        raw_id = session["session_file"].replace(".jsonl", "").replace(":", "-")
        sid_short = raw_id[:30]
        filename = f"session-{i+1:02d}-{sid_short}.txt"
        session_path = date_dir / filename

        lines = [
            f"Session: {session['session_file']}",
            f"Display name: {session.get('display_name', session['session_file'])}",
            f"Client: {session.get('client', 'unknown')}",
            f"Source: {session.get('source', 'unknown')}",
            f"Modified: {session['modified']}",
            f"Messages: {session['message_count']}, Tool uses: {session['tool_use_count']}",
            f"{'─' * 60}",
        ]
        if session.get("cwd"):
            lines.insert(4, f"CWD: {session['cwd']}")
        if session.get("originator"):
            lines.insert(4, f"Originator: {session['originator']}")
        for msg in session["messages"]:
            role = "USER" if msg["role"] == "user" else "AGENT"
            idx = msg.get("index", "?")
            lines.append(f"\n[{role} @{idx}]")
            lines.append(_redact_sensitive(msg["text"]))

        if session["tool_uses"]:
            lines.append(f"\n  -- Tool usage log --")
            for tu in session["tool_uses"]:
                file_info = f" [{_redact_sensitive(tu['file'][:80])}]" if tu.get("file") else ""
                lines.append(f"  - {tu['tool']}{file_info}")

        session_text = "\n".join(lines)
        session_path.write_text(session_text, encoding="utf-8")
        session_files_written.append(filename)
        session_txt_map[session["session_file"]] = filename
        total_size += len(session_text.encode("utf-8"))
        print(f"  {filename}: {len(session_text) / 1024:.0f} KB")

    # Also keep legacy single context file for backwards compat
    legacy_parts = [
        f"Deep Sleep Context -- {target_date}",
        f"Generated at: {datetime.now().isoformat()}",
        f"NEXO_HOME: {NEXO_HOME}",
        f"Sessions: {len(sessions)}",
    ]
    legacy_parts.append(format_transcripts(sessions))
    legacy_parts.append(shared_text)
    legacy_file = DEEP_SLEEP_DIR / f"{target_date}-context.txt"
    legacy_file.write_text("\n".join(legacy_parts), encoding="utf-8")

    # Metadata JSON
    meta = {
        "date": target_date,
        "sessions_found": len(sessions),
        "session_files": [s["session_file"] for s in sessions],
        "session_txt_files": session_files_written,
        "session_txt_map": session_txt_map,
        "session_manifest": [
            {
                "session_id": s["session_file"],
                "display_name": s.get("display_name", s["session_file"]),
                "client": s.get("client", "unknown"),
                "source": s.get("source", ""),
                "session_path": s.get("session_path", ""),
                "session_txt_file": session_txt_map.get(s["session_file"], ""),
            }
            for s in sessions
        ],
        "total_messages": sum(s["message_count"] for s in sessions),
        "total_tool_uses": sum(s["tool_use_count"] for s in sessions),
        "followups_active": len(followups),
        "learnings_count": len(learnings),
        "diaries_today": len(diaries),
        "error_log_files": len(error_logs),
        "date_dir": str(date_dir),
        "shared_context_file": str(shared_file),
        "long_horizon_file": str(long_horizon_file),
        "context_file": str(legacy_file),
        "total_size_bytes": total_size,
    }
    meta_file = DEEP_SLEEP_DIR / f"{target_date}-meta.json"
    with open(meta_file, "w") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    print(f"\n[collect] Done. {len(session_files_written)} session files + shared context ({total_size / 1024:.0f} KB total)")
    print(f"[collect] Dir: {date_dir}")
    print(f"[collect] Meta: {meta_file}")


if __name__ == "__main__":
    main()
