from __future__ import annotations

import json

from transcript_coverage import analyze_transcript_file, build_transcript_coverage_report


def _write_codex(path, user_messages: int, session_id: str = "codex-session"):
    rows = [
        {"type": "session_meta", "payload": {"id": session_id, "source": "codex", "cwd": "/tmp/project"}},
    ]
    for index in range(user_messages):
        rows.append({"type": "event_msg", "payload": {"type": "user_message", "message": f"user {index}"}})
        rows.append({
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": f"answer {index}"}],
            },
        })
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def test_analyze_transcript_file_marks_short_codex_session_invisible(tmp_path):
    path = tmp_path / "short.jsonl"
    _write_codex(path, 2)

    row = analyze_transcript_file(path, "codex")

    assert row["covered"] is False
    assert row["reason"] == "below_min_user_messages"
    assert row["user_message_count"] == 2


def test_analyze_transcript_file_marks_covered_codex_session(tmp_path):
    path = tmp_path / "covered.jsonl"
    _write_codex(path, 3, session_id="conv-1")

    row = analyze_transcript_file(path, "codex")

    assert row["covered"] is True
    assert row["reason"] == "covered"
    assert row["session_uid"] == "conv-1"


def test_build_transcript_coverage_report_detects_desktop_gap(tmp_path):
    covered = tmp_path / "covered.jsonl"
    short = tmp_path / "short.jsonl"
    _write_codex(covered, 3, session_id="conv-covered")
    _write_codex(short, 2, session_id="conv-short")

    report = build_transcript_coverage_report(
        [("codex", covered), ("codex", short)],
        desktop_conversations=[
            {"id": "conv-covered"},
            {"id": "desktop-only"},
        ],
    )

    assert report["counts"]["files"] == 2
    assert report["counts"]["covered"] == 1
    assert report["reasons"]["below_min_user_messages"] == 1
    assert report["desktop_without_transcript"] == ["desktop-only"]
