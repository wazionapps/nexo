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
