from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


def _write_codex_transcript(path: Path, *, session_id: str = "codex-short-session") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "type": "session_meta",
            "payload": {
                "id": session_id,
                "cwd": "/tmp/nexo-release",
                "source": "codex",
            },
        },
        {
            "type": "event_msg",
            "payload": {
                "type": "user_message",
                "message": "Needle continuity question about the NEXO brain map",
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": "The map should be updated after all release points close.",
                    }
                ],
            },
        },
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")


def test_transcript_index_includes_short_sessions_without_raw_search_regression(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    transcript_path = tmp_path / ".codex" / "sessions" / "2026" / "05" / "19" / "short.jsonl"
    _write_codex_transcript(transcript_path)

    from transcript_index import index_recent_transcripts, search_transcript_index
    from transcript_utils import search_transcripts

    assert search_transcripts("Needle continuity", hours=1, limit=5) == []

    indexed = index_recent_transcripts(hours=1, client="codex", limit=10)
    assert len(indexed) == 1
    assert indexed[0]["source_client"] == "codex"
    assert indexed[0]["user_message_count"] == 1
    assert indexed[0]["content_hash"]
    assert "Needle continuity question" in indexed[0]["sanitized_summary"]

    rows = search_transcript_index("Needle continuity", hours=1, client="codex", limit=5)
    assert len(rows) == 1
    assert rows[0]["session_id"] == "codex-short-session"
    assert rows[0]["path_ref"] == str(transcript_path)


def test_ensure_transcript_index_refreshes_when_new_source_file_appears(monkeypatch, tmp_path):
    import os
    import time

    monkeypatch.setenv("HOME", str(tmp_path))
    first = tmp_path / ".codex" / "sessions" / "2026" / "05" / "19" / "first.jsonl"
    second = tmp_path / ".codex" / "sessions" / "2026" / "05" / "19" / "second.jsonl"
    _write_codex_transcript(first, session_id="first-session")
    past_mtime = time.time() - 10
    os.utime(first, (past_mtime, past_mtime))

    import importlib
    import transcript_index
    import transcript_utils

    importlib.reload(transcript_utils)
    importlib.reload(transcript_index)

    initial = transcript_index.ensure_transcript_index(hours=1, client="codex", limit=10)
    assert initial["after"] == 1

    _write_codex_transcript(second, session_id="second-session")
    second_mtime = past_mtime + 5
    os.utime(second, (second_mtime, second_mtime))
    refreshed = transcript_index.ensure_transcript_index(hours=1, client="codex", limit=10)

    assert refreshed["stale"] is True
    rows = transcript_index.search_transcript_index("second-session", hours=1, client="codex", limit=5)
    assert len(rows) == 1


def test_transcript_index_upsert_updates_existing_row():
    from transcript_index import index_transcript_session, search_transcript_index

    session = {
        "client": "codex",
        "session_uid": "stable-session",
        "session_file": "codex:stable.jsonl",
        "display_name": "stable.jsonl",
        "session_path": "/tmp/stable.jsonl",
        "modified": datetime.now().isoformat(),
        "message_count": 2,
        "user_message_count": 1,
        "messages": [
            {"role": "user", "index": 1, "text": "first searchable marker"},
            {"role": "assistant", "index": 2, "text": "first reply"},
        ],
    }
    first = index_transcript_session(session)
    session["messages"][0]["text"] = "second searchable marker"
    second = index_transcript_session(session)

    assert first["id"] == second["id"]
    assert first["content_hash"] != second["content_hash"]
    assert search_transcript_index("second searchable", hours=1)[0]["id"] == first["id"]


def test_transcript_tools_resolve_short_session_by_filename_prefix(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    transcript_path = tmp_path / ".codex" / "sessions" / "2026" / "05" / "19" / "62dd7c9e-bee7.jsonl"
    _write_codex_transcript(transcript_path, session_id="codex-prefix-session")

    import importlib
    import tools_transcripts
    import transcript_index
    import transcript_utils

    importlib.reload(transcript_utils)
    importlib.reload(transcript_index)
    importlib.reload(tools_transcripts)

    search_text = tools_transcripts.handle_transcript_search("62dd7c9e", hours=1, limit=5)
    assert "TRANSCRIPTS (1)" in search_text
    assert "62dd7c9e-bee7.jsonl" in search_text
    assert "(index)" in search_text

    read_text = tools_transcripts.handle_transcript_read(session_ref="62dd7c9e", max_messages=10)
    assert "TRANSCRIPT codex:62dd7c9e-bee7.jsonl" in read_text
    assert "Needle continuity question" in read_text


def test_codex_transcript_file_discovery_keeps_same_basename_in_distinct_roots(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    active = tmp_path / ".codex" / "sessions" / "2026" / "05" / "19" / "same.jsonl"
    archived = tmp_path / ".codex" / "archived_sessions" / "2026" / "05" / "20" / "same.jsonl"
    _write_codex_transcript(active, session_id="active-same")
    _write_codex_transcript(archived, session_id="archived-same")

    import importlib
    import transcript_utils

    importlib.reload(transcript_utils)
    paths = {str(path) for path in transcript_utils.find_codex_session_files()}

    assert str(active) in paths
    assert str(archived) in paths
