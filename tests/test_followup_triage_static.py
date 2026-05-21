from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_followup_runner_triages_stale_items_out_of_executable_batch():
    src = (REPO_ROOT / "src" / "scripts" / "nexo-followup-runner.py").read_text(encoding="utf-8")

    assert "STALE_FOLLOWUP_TRIAGE_DAYS = 14" in src
    assert 'result["stale_triage"].append(stale_fu)' in src
    assert 'status="stale_review"' in src
    assert "upsert_attention_reminder(" in src
    assert "MAX_NEEDS_OPERATOR_BRIEFING" in src


def test_followup_runner_excludes_done_status_from_executable_batch():
    src = (REPO_ROOT / "src" / "scripts" / "nexo-followup-runner.py").read_text(encoding="utf-8")

    assert "followup_lifecycle_snapshot" in src
    assert '(snapshot.get("lanes") or {}).get("active", [])' in src


def test_followup_runner_reads_item_history_not_legacy_followup_history():
    src = (REPO_ROOT / "src" / "scripts" / "nexo-followup-runner.py").read_text(encoding="utf-8")

    assert "FROM item_history" in src
    assert "item_type='followup'" in src
    assert "FROM followup_history" not in src


def test_followup_hygiene_escalates_stale_items_instead_of_only_logging():
    src = (REPO_ROOT / "src" / "scripts" / "nexo-followup-hygiene.py").read_text(encoding="utf-8")

    assert "status=\"stale_review\"" in src
    assert "history_event=\"stale_triage\"" in src
    assert "stale_escalated_count" in src
    assert "expired_count" in src
    assert "updated_at < ?" in src
    assert "UPPER(COALESCE(status, '')) NOT IN" in src
