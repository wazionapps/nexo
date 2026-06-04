"""Tests for Deep Sleep transcript collection across Claude Code and Codex."""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
COLLECT_PATH = REPO_ROOT / "src" / "scripts" / "deep-sleep" / "collect.py"


def _load_collect_module(monkeypatch, home: Path):
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("NEXO_HOME", str(home / "nexo-home"))
    monkeypatch.setenv("NEXO_CODE", str(REPO_ROOT / "src"))
    sys.modules.pop("deep_sleep_collect_test", None)
    spec = importlib.util.spec_from_file_location("deep_sleep_collect_test", COLLECT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_collect_transcripts_since_reads_claude_and_codex(monkeypatch, tmp_path):
    claude_file = tmp_path / ".claude" / "projects" / "demo" / "session-1.jsonl"
    claude_file.parent.mkdir(parents=True)
    claude_file.write_text(
        "\n".join(
            [
                json.dumps({"type": "user", "message": {"content": "Need help with deploy"}}),
                json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Sure."}]}}),
                json.dumps({"type": "user", "message": {"content": "The backend path changed"}}),
                json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Check nginx."}]}}),
                json.dumps({"type": "user", "message": {"content": "Now it works"}}),
                json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Great."}]}}),
            ]
        )
        + "\n"
    )

    codex_file = tmp_path / ".codex" / "sessions" / "2026" / "04" / "05" / "rollout-demo.jsonl"
    codex_file.parent.mkdir(parents=True)
    codex_file.write_text(
        "\n".join(
            [
                json.dumps({
                    "timestamp": "2026-04-05T01:00:00Z",
                    "type": "session_meta",
                    "payload": {
                        "id": "codex-demo",
                        "cwd": "/repo",
                        "originator": "codex_cli_rs",
                        "source": "cli",
                    },
                }),
                json.dumps({"timestamp": "2026-04-05T01:00:01Z", "type": "event_msg", "payload": {"type": "user_message", "message": "Need to debug watcher"}}),
                json.dumps({"timestamp": "2026-04-05T01:00:02Z", "type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Checking logs."}]}}),
                json.dumps({"timestamp": "2026-04-05T01:00:03Z", "type": "event_msg", "payload": {"type": "user_message", "message": "The cron did not run"}}),
                json.dumps({"timestamp": "2026-04-05T01:00:04Z", "type": "response_item", "payload": {"type": "function_call", "name": "mcp__nexo__nexo_heartbeat", "arguments": "{\"sid\":\"x\"}"}}),
                json.dumps({"timestamp": "2026-04-05T01:00:05Z", "type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "I found the issue."}]}}),
                json.dumps({"timestamp": "2026-04-05T01:00:06Z", "type": "event_msg", "payload": {"type": "user_message", "message": "Fix it please"}}),
                json.dumps({"timestamp": "2026-04-05T01:00:07Z", "type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Patched."}]}}),
            ]
        )
        + "\n"
    )

    collect = _load_collect_module(monkeypatch, tmp_path)
    sessions = collect.collect_transcripts_since("2000-01-01T00:00:00")

    assert len(sessions) == 2
    by_client = {session["client"]: session for session in sessions}
    assert by_client["claude_code"]["session_file"] == "claude_code:session-1.jsonl"
    assert by_client["codex"]["session_file"] == "codex:rollout-demo.jsonl"
    assert by_client["codex"]["tool_use_count"] == 1
    assert by_client["codex"]["originator"] == "codex_cli_rs"


def test_extract_codex_session_ignores_environment_context(monkeypatch, tmp_path):
    codex_file = tmp_path / ".codex" / "sessions" / "2026" / "04" / "05" / "rollout-env.jsonl"
    codex_file.parent.mkdir(parents=True)
    codex_file.write_text(
        "\n".join(
            [
                json.dumps({"type": "session_meta", "payload": {"id": "codex-env", "source": "cli"}}),
                json.dumps({"type": "event_msg", "payload": {"type": "user_message", "message": "<environment_context>\n<cw>/tmp</cw>\n</environment_context>"}}),
                json.dumps({"type": "event_msg", "payload": {"type": "user_message", "message": "one"}}),
                json.dumps({"type": "event_msg", "payload": {"type": "user_message", "message": "two"}}),
                json.dumps({"type": "event_msg", "payload": {"type": "user_message", "message": "three"}}),
                json.dumps({"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "done"}]}}),
            ]
        )
        + "\n"
    )

    collect = _load_collect_module(monkeypatch, tmp_path)
    session = collect.extract_codex_session(codex_file)

    assert session is not None
    assert session["user_message_count"] == 3
    assert all("<environment_context>" not in msg["text"] for msg in session["messages"])


def test_collect_long_horizon_context_blends_recent_and_older(monkeypatch, tmp_path):
    collect = _load_collect_module(monkeypatch, tmp_path)
    nexo_home = Path(os.environ["NEXO_HOME"])
    data_dir = nexo_home / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "nexo.db"

    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE session_diary (session_id TEXT, created_at TEXT, summary TEXT, mental_state TEXT, domain TEXT, self_critique TEXT, source TEXT)"
    )
    conn.execute(
        "CREATE TABLE learnings (category TEXT, title TEXT, content TEXT, created_at TEXT, updated_at TEXT, reasoning TEXT, prevention TEXT, applies_to TEXT)"
    )
    conn.execute(
        "CREATE TABLE followups (id TEXT, description TEXT, date TEXT, status TEXT, created_at TEXT, updated_at TEXT)"
    )
    for day in range(1, 31):
        created = f"2026-03-{day:02d}T08:00:00"
        conn.execute(
            "INSERT INTO session_diary VALUES (?, ?, ?, ?, ?, ?, ?)",
            (f"s-{day}", created, f"summary {day}", "focused", "shopify" if day % 2 else "wazion", f"critique {day % 3}", "codex" if day % 2 else "claude"),
        )
    conn.execute(
        "INSERT INTO learnings VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("ops", "Learning A", "Remember the worker port", "2026-03-10T10:00:00", "2026-04-01T10:00:00", "reason", "prevent", "deploy"),
    )
    conn.execute(
        "INSERT INTO followups VALUES (?, ?, ?, ?, ?, ?)",
        ("F1", "Old unresolved item", "2026-03-15", "PENDING", "2026-03-10T10:00:00", "2026-03-11T10:00:00"),
    )
    conn.commit()
    conn.close()

    recent_file = tmp_path / ".codex" / "sessions" / "2026" / "04" / "02" / "recent.jsonl"
    recent_file.parent.mkdir(parents=True)
    recent_file.write_text("{}\n")
    older_file = tmp_path / ".claude" / "projects" / "demo" / "older.jsonl"
    older_file.parent.mkdir(parents=True)
    older_file.write_text("{}\n")
    os.utime(recent_file, (0, datetime(2026, 4, 2, 12, 0, 0).timestamp()))
    os.utime(older_file, (0, datetime(2026, 2, 20, 12, 0, 0).timestamp()))

    deep_sleep_dir = nexo_home / "operations" / "deep-sleep"
    deep_sleep_dir.mkdir(parents=True, exist_ok=True)
    (deep_sleep_dir / "2026-W13-weekly-summary.json").write_text(json.dumps({
        "label": "2026-W13",
        "window_start": "2026-03-23",
        "window_end": "2026-03-29",
        "summary": "weekly drift summary",
        "top_projects": [{"project": "wazion", "score": 9.5}],
        "top_patterns": [{"pattern": "deploy drift", "count": 2}],
    }))
    (deep_sleep_dir / "2026-03-monthly-summary.json").write_text(json.dumps({
        "label": "2026-03",
        "window_start": "2026-03-01",
        "window_end": "2026-03-31",
        "summary": "monthly drift summary",
        "top_projects": [{"project": "shopify", "score": 7.0}],
        "top_patterns": [{"pattern": "auth retries", "count": 3}],
    }))

    context = collect.collect_long_horizon_context("2026-04-05", max_diaries=10, max_sessions=6)

    assert context["sample_strategy"] == "70% recent + 30% older evenly sampled"
    assert len(context["historical_diaries"]) <= 10
    assert len(context["historical_sessions"]) <= 6
    assert context["historical_learnings"][0]["title"] == "Learning A"
    assert any(item["id"] == "F1" for item in context["stale_followups"])
    diary_dates = [entry["created_at"] for entry in context["historical_diaries"]]
    assert any(date.startswith("2026-03-3") for date in diary_dates)
    assert any(date.startswith("2026-03-0") or date.startswith("2026-03-1") for date in diary_dates)
    assert context["weekly_summaries"][0]["label"] == "2026-W13"
    assert context["monthly_summaries"][0]["label"] == "2026-03"
    assert context["project_priority_signals"]


def test_project_priority_signals_handles_optional_schema_columns(monkeypatch, tmp_path):
    collect = _load_collect_module(monkeypatch, tmp_path)
    nexo_home = Path(os.environ["NEXO_HOME"])
    (nexo_home / "brain").mkdir(parents=True, exist_ok=True)
    (nexo_home / "brain" / "project-atlas.json").write_text(json.dumps({
        "wazion": {"aliases": ["wazion"]},
    }))
    data_dir = nexo_home / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "nexo.db"

    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE learnings (category TEXT, title TEXT, content TEXT, created_at TEXT, updated_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE followups (id TEXT, description TEXT, date TEXT, status TEXT, created_at TEXT, updated_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE decisions (domain TEXT, outcome TEXT, created_at TEXT)"
    )
    conn.execute(
        "INSERT INTO learnings VALUES (?, ?, ?, ?, ?)",
        ("ops", "Wazion deploy gotcha", "wazion deploy drift", "2026-04-01T08:00:00", "2026-04-02T08:00:00"),
    )
    conn.execute(
        "INSERT INTO followups VALUES (?, ?, ?, ?, ?, ?)",
        ("F1", "Review wazion deploy", "2026-04-03", "PENDING", "2026-04-01T09:00:00", "2026-04-01T09:00:00"),
    )
    conn.execute(
        "INSERT INTO decisions VALUES (?, ?, ?)",
        ("wazion", "blocked deploy", "2026-04-02T10:00:00"),
    )
    conn.commit()
    conn.close()

    signals = collect._project_priority_signals(
        datetime(2026, 4, 5),
        [{"created_at": "2026-04-02T11:00:00", "domain": "wazion", "summary": "wazion deploy", "self_critique": ""}],
    )

    assert signals
    assert signals[0]["project"] == "wazion"


def _make_session(
    session_file: str,
    *,
    uid: str,
    parent: str = "",
    text: str = "work",
    role: str = "",
    nickname: str = "",
    msgs: int = 3,
    tools: int = 2,
    client: str = "codex",
) -> dict:
    messages = [
        {"role": "user" if i % 2 == 0 else "assistant", "index": i, "text": f"{text} {i}"}
        for i in range(msgs)
    ]
    return {
        "session_file": session_file,
        "client": client,
        "session_uid": uid,
        "parent_thread_id": parent,
        "thread_source": "subagent" if parent else "user",
        "agent_role": role,
        "agent_nickname": nickname,
        "source": {"subagent": {"thread_spawn": {"parent_thread_id": parent}}} if parent else "cli",
        "message_count": msgs,
        "tool_use_count": tools,
        "messages": messages,
        "tool_uses": [{"tool": "rg", "file": "x"} for _ in range(tools)],
        "modified": "2026-06-03T00:00:00",
    }


def test_dedupe_folds_subagents_into_parent_thread(monkeypatch, tmp_path):
    collect = _load_collect_module(monkeypatch, tmp_path)

    parent = _make_session("codex:parent.jsonl", uid="P", text="parent", msgs=4)
    subs = [
        _make_session(
            f"codex:sub-{n}.jsonl", uid=f"S{n}", parent="P", text=f"explorer {n}",
            role="explorer", nickname=name, msgs=3,
        )
        for n, name in enumerate(["Helmholtz", "Hume", "Godel", "Arendt"], 1)
    ]
    other = _make_session("codex:other.jsonl", uid="O", text="unrelated", msgs=5)
    claude = _make_session("claude_code:cc.jsonl", uid="CC", text="claude work", msgs=2, client="claude_code")
    claude["source"] = "claude_projects"

    kept, report = collect.dedupe_sessions([parent, *subs, other, claude])
    kept_ids = {s["session_file"] for s in kept}

    # 7 inputs (parent + 4 sub-agents + other + claude) -> 3 real threads.
    assert len(kept) == 3
    assert kept_ids == {"codex:parent.jsonl", "codex:other.jsonl", "claude_code:cc.jsonl"}

    rep = next(s for s in kept if s["session_file"] == "codex:parent.jsonl")
    assert len(rep["folded_subagents"]) == 4
    assert {f["agent_nickname"] for f in rep["folded_subagents"]} == {"Helmholtz", "Hume", "Godel", "Arendt"}
    # No content lost: 4 own msgs + 4 fold labels + 4 explorers * 3 msgs each.
    assert rep["message_count"] == 4 + 4 + 4 * 3

    assert len(report) == 1
    assert report[0]["root_thread"] == "P"
    assert report[0]["count"] == 5
    assert len(report[0]["folded"]) == 4


def test_dedupe_groups_orphan_siblings_and_keeps_distinct(monkeypatch, tmp_path):
    collect = _load_collect_module(monkeypatch, tmp_path)

    # Parent thread "GONE" is not in the batch; its two sibling explorers must
    # still collapse together rather than counting as two separate threads.
    s1 = _make_session("codex:s1.jsonl", uid="A", parent="GONE", text="a", nickname="X", role="explorer")
    s2 = _make_session("codex:s2.jsonl", uid="B", parent="GONE", text="b", nickname="Y", role="explorer")
    distinct = _make_session("codex:d.jsonl", uid="D", text="d")

    kept, report = collect.dedupe_sessions([s1, s2, distinct])
    kept_ids = {s["session_file"] for s in kept}

    assert len(kept) == 2
    assert "codex:d.jsonl" in kept_ids
    assert len(report) == 1
    assert report[0]["root_thread"] == "GONE"
    assert report[0]["count"] == 2

    # Fully distinct top-level sessions are never merged.
    a = _make_session("codex:x.jsonl", uid="X1", text="x")
    b = _make_session("claude_code:y.jsonl", uid="Y1", text="y", client="claude_code")
    kept2, report2 = collect.dedupe_sessions([a, b])
    assert len(kept2) == 2
    assert report2 == []
    assert all(s.get("folded_subagents", []) == [] for s in kept2)
