"""Readable export and auto-flush inspection tools."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import cognitive
import claim_graph
import compaction_memory
import media_memory
import paths
import user_state_model
from db import get_db
from memory_backends import get_backend, list_backends

try:
    from semantic_layers import redact_value as _redact_value
except Exception:  # pragma: no cover - bootstrap fallback
    def _redact_value(value, *, max_chars=4000):
        return str(value or "")[:max_chars]


_SENSITIVE_EXPORT_KEYS = {
    "api_key", "apikey", "token", "secret", "password", "authorization",
    "bearer", "credential", "cred_ref", "provider_payload", "raw_prompt",
    "raw_response", "transcript", "file_path", "path",
}


def _nexo_home() -> Path:
    return Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo"))).expanduser()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _safe_text(value: object, *, max_chars: int = 500) -> str:
    return _redact_value(value, max_chars=max_chars)


def _safe_value(value, *, _depth: int = 0):
    if _depth > 5:
        return "[redacted_depth]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, dict):
        clean = {}
        for key, item in value.items():
            raw_key = str(key or "").strip().lower()
            safe_key = _safe_text(key, max_chars=120) or "field"
            if raw_key in _SENSITIVE_EXPORT_KEYS:
                clean[safe_key] = "[redacted]"
            else:
                clean[safe_key] = _safe_value(item, _depth=_depth + 1)
        return clean
    if isinstance(value, (list, tuple, set)):
        return [_safe_value(item, _depth=_depth + 1) for item in list(value)[:100]]
    return _safe_text(value, max_chars=800)


def handle_auto_flush_recent(limit: int = 20, session_id: str = "") -> str:
    rows = compaction_memory.list_auto_flushes(session_id=session_id, limit=limit)
    if not rows:
        return "No auto-flush records."
    lines = [f"AUTO-FLUSH — {len(rows)} record(s):", ""]
    for row in rows:
        lines.append(f"  #{row['id']} {_safe_text(row['created_at'], max_chars=80)} [{_safe_text(row.get('session_id','unknown'), max_chars=120)}]")
        lines.append(f"    {_safe_text(row.get('summary',''), max_chars=220)}")
        if row.get("next_step"):
            lines.append(f"    next: {_safe_text(row['next_step'], max_chars=160)}")
    return "\n".join(lines)


def handle_auto_flush_stats(days: int = 7) -> str:
    stats = compaction_memory.auto_flush_stats(days=days)
    return (
        f"AUTO-FLUSH STATS — {stats['window_days']}d\n"
        f"  total: {stats['total']}\n"
        f"  backend: {stats['backend']}\n"
        f"  by_source: {stats['by_source']}"
    )


def handle_memory_backend_status() -> str:
    active = get_backend()
    backends = list_backends()
    lines = [
        f"MEMORY BACKEND: {active.key} — {active.label}",
        f"Description: {active.description}",
        f"Supports: {', '.join(active.supports)}",
        "",
        "Registered backends:",
    ]
    for item in backends:
        marker = "*" if item["active"] else "-"
        lines.append(f"  {marker} {item['key']} [{item['maturity']}] {item['label']}")
    return "\n".join(lines)


def handle_memory_export(format: str = "markdown", output_dir: str = "") -> str:
    if format.strip().lower() != "markdown":
        return "ERROR: only markdown export is supported for now."

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    root = (
        Path(output_dir).expanduser()
        if output_dir.strip()
        else (paths.exports_dir() / "memory" / stamp)
    )
    root.mkdir(parents=True, exist_ok=True)

    conn = get_db()
    learnings = [dict(r) for r in conn.execute("SELECT id, category, title, status, prevention, updated_at FROM learnings ORDER BY updated_at DESC LIMIT 50").fetchall()]
    decisions = [dict(r) for r in conn.execute("SELECT id, domain, decision, confidence, status, created_at FROM decisions ORDER BY created_at DESC LIMIT 50").fetchall()]
    claims = claim_graph.search_claims(limit=100)
    claim_lint = claim_graph.lint_claims(limit=50)
    media = media_memory.list_media_memories(limit=100)
    flushes = compaction_memory.list_auto_flushes(limit=100)
    user_state = user_state_model.build_user_state(days=7, persist=False)
    user_history = user_state_model.list_user_state_snapshots(limit=30)
    cognitive_stats = cognitive.get_stats()

    _write(
        root / "README.md",
        "\n".join(
            [
                "# NEXO Memory Export",
                "",
                f"- Generated: {datetime.now().isoformat(timespec='seconds')}",
                f"- Backend: {get_backend().key}",
                f"- Learnings: {len(learnings)}",
                f"- Decisions: {len(decisions)}",
                f"- Claims: {len(claims)}",
                f"- Media memories: {len(media)}",
                f"- Auto-flush records: {len(flushes)}",
                "",
                "Files:",
                "- `learnings.md`",
                "- `decisions.md`",
                "- `claims.md`",
                "- `media.md`",
                "- `auto-flush.md`",
                "- `user-state.md`",
                "- `cognitive.json`",
            ]
        ),
    )
    _write(
        root / "learnings.md",
        "\n".join(
            ["# Learnings", ""]
            + [
                f"- #{item['id']} [{_safe_text(item.get('category','general'), max_chars=80)}] {_safe_text(item['title'])} "
                f"({_safe_text(item.get('status','active'), max_chars=40)}, updated {_safe_text(item.get('updated_at',''), max_chars=80)})"
                for item in learnings
            ]
        ),
    )
    _write(
        root / "decisions.md",
        "\n".join(
            ["# Decisions", ""]
            + [
                f"- #{item['id']} [{_safe_text(item.get('domain','other'), max_chars=80)}] {_safe_text(item['decision'])} "
                f"({_safe_text(item.get('confidence','medium'), max_chars=40)}, {_safe_text(item.get('status','pending_review'), max_chars=40)})"
                for item in decisions
            ]
        ),
    )
    _write(
        root / "claims.md",
        "\n".join(
            ["# Claims", ""]
            + [
                f"- #{item['id']} [{item.get('verification_status','unverified')}] "
                f"{_safe_text(item.get('freshness_state','?'), max_chars=40)}({item.get('freshness_score',0)}): {_safe_text(item['text'])}"
                for item in claims
            ]
            + ["", "## Attention", ""]
            + [
                f"- #{item['id']} [{_safe_text(', '.join(item.get('lint_reasons', [])), max_chars=120)}] {_safe_text(item['text'])}"
                for item in claim_lint
            ]
        ),
    )
    _write(
        root / "media.md",
        "\n".join(
            ["# Media Memory", ""]
            + [
                f"- #{item['id']} [{_safe_text(item['media_type'], max_chars=40)}] {_safe_text(item['title'])} :: {_safe_text(item.get('file_path') or item.get('url') or 'n/a')}"
                for item in media
            ]
        ),
    )
    _write(
        root / "auto-flush.md",
        "\n".join(
            ["# Auto Flush", ""]
            + [
                f"- #{item['id']} [{_safe_text(item.get('session_id','unknown'), max_chars=120)}] {_safe_text(item.get('created_at',''), max_chars=80)}: "
                f"{_safe_text(item.get('summary',''))}"
                for item in flushes
            ]
        ),
    )
    _write(
        root / "user-state.md",
        "\n".join(
            [
                "# User State",
                "",
                f"- Current: {_safe_text(user_state['state_label'], max_chars=80)} ({user_state['confidence']})",
                f"- Trust: {user_state['trust_score']}",
                f"- Guidance: {_safe_text(user_state['guidance'])}",
                "",
                "## Signals",
            ]
            + [f"- {_safe_text(key, max_chars=120)}: {_safe_text(value)}" for key, value in user_state["signals"].items()]
            + ["", "## History", ""]
            + [f"- {_safe_text(item['created_at'], max_chars=80)} :: {_safe_text(item['state_label'], max_chars=80)} ({item['confidence']})" for item in user_history]
        ),
    )
    _write(root / "cognitive.json", json.dumps(_safe_value(cognitive_stats), indent=2, sort_keys=True))
    return f"Memory export written to {_safe_text(root)}"


TOOLS = [
    (handle_auto_flush_recent, "nexo_auto_flush_recent", "Show recent structured auto-flush records written before compaction."),
    (handle_auto_flush_stats, "nexo_auto_flush_stats", "Stats for pre-compaction auto-flush activity."),
    (handle_memory_backend_status, "nexo_memory_backend_status", "Show the active memory backend contract and registered backend list."),
    (handle_memory_export, "nexo_memory_export", "Export a readable markdown snapshot of key NEXO memory layers."),
]
