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
import user_state_model
from db import get_db
from memory_backends import get_backend, list_backends


def _nexo_home() -> Path:
    return Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo"))).expanduser()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def handle_auto_flush_recent(limit: int = 20, session_id: str = "") -> str:
    rows = compaction_memory.list_auto_flushes(session_id=session_id, limit=limit)
    if not rows:
        return "No auto-flush records."
    lines = [f"AUTO-FLUSH — {len(rows)} record(s):", ""]
    for row in rows:
        lines.append(f"  #{row['id']} {row['created_at']} [{row.get('session_id','unknown')}]")
        lines.append(f"    {row.get('summary','')[:220]}")
        if row.get("next_step"):
            lines.append(f"    next: {row['next_step'][:160]}")
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
    root = Path(output_dir).expanduser() if output_dir.strip() else (_nexo_home() / "exports" / "memory" / stamp)
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
                f"- #{item['id']} [{item.get('category','general')}] {item['title']} "
                f"({item.get('status','active')}, updated {item.get('updated_at','')})"
                for item in learnings
            ]
        ),
    )
    _write(
        root / "decisions.md",
        "\n".join(
            ["# Decisions", ""]
            + [
                f"- #{item['id']} [{item.get('domain','other')}] {item['decision']} "
                f"({item.get('confidence','medium')}, {item.get('status','pending_review')})"
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
                f"{item.get('freshness_state','?')}({item.get('freshness_score',0)}): {item['text']}"
                for item in claims
            ]
            + ["", "## Attention", ""]
            + [
                f"- #{item['id']} [{', '.join(item.get('lint_reasons', []))}] {item['text']}"
                for item in claim_lint
            ]
        ),
    )
    _write(
        root / "media.md",
        "\n".join(
            ["# Media Memory", ""]
            + [
                f"- #{item['id']} [{item['media_type']}] {item['title']} :: {item.get('file_path') or item.get('url') or 'n/a'}"
                for item in media
            ]
        ),
    )
    _write(
        root / "auto-flush.md",
        "\n".join(
            ["# Auto Flush", ""]
            + [
                f"- #{item['id']} [{item.get('session_id','unknown')}] {item.get('created_at','')}: "
                f"{item.get('summary','')}"
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
                f"- Current: {user_state['state_label']} ({user_state['confidence']})",
                f"- Trust: {user_state['trust_score']}",
                f"- Guidance: {user_state['guidance']}",
                "",
                "## Signals",
            ]
            + [f"- {key}: {value}" for key, value in user_state["signals"].items()]
            + ["", "## History", ""]
            + [f"- {item['created_at']} :: {item['state_label']} ({item['confidence']})" for item in user_history]
        ),
    )
    _write(root / "cognitive.json", json.dumps(cognitive_stats, indent=2, sort_keys=True))
    return f"Memory export written to {root}"


TOOLS = [
    (handle_auto_flush_recent, "nexo_auto_flush_recent", "Show recent structured auto-flush records written before compaction."),
    (handle_auto_flush_stats, "nexo_auto_flush_stats", "Stats for pre-compaction auto-flush activity."),
    (handle_memory_backend_status, "nexo_memory_backend_status", "Show the active memory backend contract and registered backend list."),
    (handle_memory_export, "nexo_memory_export", "Export a readable markdown snapshot of key NEXO memory layers."),
]
