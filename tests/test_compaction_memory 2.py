"""Tests for structured auto-flush before compaction."""

import json


def test_record_auto_flush_persists_structured_row(tmp_path):
    import compaction_memory

    log_file = tmp_path / "tool-log.jsonl"
    log_file.write_text(
        "\n".join(
            [
                json.dumps({"timestamp": "2026-04-09T10:00:00Z", "tool_name": "Edit", "tool_input": {"file_path": "/tmp/example.py"}}),
                json.dumps({"timestamp": "2026-04-09T10:01:00Z", "tool_name": "Bash", "tool_input": {"command": "git commit -m 'test'"}}),
            ]
        ),
        encoding="utf-8",
    )

    row = compaction_memory.record_auto_flush(
        session_id="nexo-test",
        task="Prepare release 4.0.0",
        current_goal="Keep continuity before compaction",
        log_file=str(log_file),
        last_diary_ts="2026-04-09T09:00:00Z",
    )

    assert row["session_id"] == "nexo-test"
    assert row["metadata"]["entry_count"] == 2
    assert "example.py" in row["summary"]


def test_auto_flush_stats_counts_recent_rows(tmp_path):
    import compaction_memory

    log_file = tmp_path / "tool-log.jsonl"
    log_file.write_text(
        json.dumps({"timestamp": "2026-04-09T10:00:00Z", "tool_name": "Write", "tool_input": {"path": "/tmp/out.md"}}),
        encoding="utf-8",
    )
    compaction_memory.record_auto_flush(
        session_id="nexo-test",
        task="Export memory",
        log_file=str(log_file),
        last_diary_ts="",
    )
    stats = compaction_memory.auto_flush_stats(days=7)
    assert stats["total"] >= 1
    assert "pre-compact-hook" in stats["by_source"]
