from __future__ import annotations

"""Inspectable user-state model built from multiple NEXO signals."""

import json
from datetime import UTC, datetime, timedelta

import cognitive
from db import get_db
from db._hot_context import search_hot_context
from memory_backends import get_backend


def init_tables() -> None:
    conn = get_db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS user_state_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            state_label TEXT NOT NULL,
            confidence REAL DEFAULT 0.0,
            guidance TEXT DEFAULT '',
            signals TEXT DEFAULT '{}',
            backend_key TEXT DEFAULT 'sqlite',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_user_state_snapshots_created ON user_state_snapshots(created_at);
        CREATE INDEX IF NOT EXISTS idx_user_state_snapshots_label ON user_state_snapshots(state_label);
        """
    )
    conn.commit()


def _recent_correction_count(days: int) -> int:
    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    row = cognitive._get_db().execute(
        "SELECT COUNT(*) FROM memory_corrections WHERE created_at >= ?",
        (cutoff,),
    ).fetchone()
    return int((row[0] if row else 0) or 0)


def _recent_trust_event_count(days: int, event_name: str) -> int:
    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    row = cognitive._get_db().execute(
        "SELECT COUNT(*) FROM trust_score WHERE created_at >= ? AND event = ?",
        (cutoff, event_name),
    ).fetchone()
    return int((row[0] if row else 0) or 0)


def _recent_diary_signal_count(days: int) -> int:
    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")
    row = get_db().execute(
        "SELECT COUNT(*) FROM session_diary WHERE created_at >= ? AND user_signals != ''",
        (cutoff,),
    ).fetchone()
    return int((row[0] if row else 0) or 0)


def build_user_state(days: int = 7, *, persist: bool = False) -> dict:
    init_tables()
    trust = float(cognitive.get_trust_score())
    history = cognitive.get_trust_history(days=days)
    sentiments = history.get("sentiment_distribution", {})
    negative = int((sentiments.get("negative") or {}).get("count", 0) or 0)
    urgent = int((sentiments.get("urgent") or {}).get("count", 0) or 0)
    positive = int((sentiments.get("positive") or {}).get("count", 0) or 0)
    corrections = _recent_correction_count(days)
    repeated_errors = _recent_trust_event_count(days, "repeated_error")
    productive_sessions = _recent_trust_event_count(days, "session_productive")
    delegation_events = _recent_trust_event_count(days, "delegation")
    diaries_with_signals = _recent_diary_signal_count(days)
    active_contexts = len(search_hot_context("", hours=min(max(days, 1) * 24, 168), limit=50, state="active"))
    waiting_contexts = len(search_hot_context("", hours=min(max(days, 1) * 24, 168), limit=50, state="waiting_user"))
    blocked_contexts = len(search_hot_context("", hours=min(max(days, 1) * 24, 168), limit=50, state="blocked"))

    frustration_score = negative * 1.5 + corrections * 0.8 + repeated_errors * 1.2 + (1 if trust < 45 else 0)
    flow_score = positive * 1.2 + productive_sessions * 1.0 + delegation_events * 0.8 + (1 if trust > 60 else 0)
    urgency_score = urgent * 2.0 + blocked_contexts * 0.6

    if urgency_score >= max(2.0, frustration_score, flow_score):
        label = "urgent"
        guidance = "Immediate execution. Keep answers short. Avoid speculative detours."
        confidence = min(0.98, 0.45 + urgency_score * 0.12)
    elif frustration_score >= max(2.0, flow_score):
        label = "frustrated"
        guidance = "Ultra-concise mode. Show concrete progress and avoid avoidable questions."
        confidence = min(0.98, 0.4 + frustration_score * 0.1)
    elif flow_score >= 2.5:
        label = "in_flow"
        guidance = "Keep momentum. Bias toward execution and only interrupt for real blockers."
        confidence = min(0.98, 0.4 + flow_score * 0.09)
    elif waiting_contexts > 0 or active_contexts > 6:
        label = "loaded"
        guidance = "Prefer batching, tight summaries, and explicit next actions."
        confidence = 0.68
    else:
        label = "stable"
        guidance = "Normal mode. Clear, direct execution with selective initiative."
        confidence = 0.6

    snapshot = {
        "state_label": label,
        "confidence": round(confidence, 2),
        "guidance": guidance,
        "trust_score": round(trust, 1),
        "signals": {
            "negative_sentiment": negative,
            "urgent_sentiment": urgent,
            "positive_sentiment": positive,
            "recent_corrections": corrections,
            "repeated_errors": repeated_errors,
            "productive_sessions": productive_sessions,
            "delegation_events": delegation_events,
            "diaries_with_user_signals": diaries_with_signals,
            "active_contexts": active_contexts,
            "waiting_contexts": waiting_contexts,
            "blocked_contexts": blocked_contexts,
        },
        "backend": get_backend().key,
    }

    if persist:
        conn = get_db()
        conn.execute(
            "INSERT INTO user_state_snapshots (state_label, confidence, guidance, signals, backend_key) VALUES (?, ?, ?, ?, ?)",
            (
                snapshot["state_label"],
                snapshot["confidence"],
                snapshot["guidance"],
                json.dumps(snapshot["signals"], ensure_ascii=True, sort_keys=True),
                snapshot["backend"],
            ),
        )
        conn.commit()

    return snapshot


def list_user_state_snapshots(limit: int = 20) -> list[dict]:
    init_tables()
    rows = get_db().execute(
        "SELECT * FROM user_state_snapshots ORDER BY created_at DESC, id DESC LIMIT ?",
        (max(1, int(limit or 20)),),
    ).fetchall()
    results = []
    for row in rows:
        item = dict(row)
        try:
            item["signals"] = json.loads(item.get("signals") or "{}")
        except Exception:
            item["signals"] = {}
        results.append(item)
    return results


def user_state_stats(days: int = 30) -> dict:
    init_tables()
    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")
    rows = get_db().execute(
        "SELECT state_label, COUNT(*) AS cnt FROM user_state_snapshots WHERE created_at >= ? GROUP BY state_label",
        (cutoff,),
    ).fetchall()
    return {
        "window_days": days,
        "snapshots": sum(int(row["cnt"]) for row in rows),
        "by_state": {row["state_label"]: row["cnt"] for row in rows},
        "backend": get_backend().key,
    }
