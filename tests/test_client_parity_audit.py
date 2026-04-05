"""Regression audit for shared client parity surfaces."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relpath: str) -> str:
    return (ROOT / relpath).read_text()


def test_agentic_jobs_route_through_shared_runner():
    job_files = [
        "src/scripts/nexo-immune.py",
        "src/scripts/nexo-postmortem-consolidator.py",
        "src/scripts/nexo-daily-self-audit.py",
        "src/scripts/nexo-evolution-run.py",
        "src/scripts/nexo-catchup.py",
        "src/scripts/deep-sleep/extract.py",
        "src/scripts/deep-sleep/synthesize.py",
        "src/scripts/nexo-learning-validator.py",
        "src/scripts/nexo-synthesis.py",
        "src/scripts/nexo-sleep.py",
        "src/scripts/check-context.py",
    ]

    for relpath in job_files:
        text = _read(relpath)
        assert "run_automation_prompt(" in text, f"{relpath} no longer uses the shared automation runner"
        assert "claude -p" not in text, f"{relpath} regressed to a hardcoded Claude CLI call"


def test_deep_sleep_collects_transcripts_from_claude_and_codex():
    text = _read("src/scripts/deep-sleep/collect.py")
    assert ".claude" in text and ".codex" in text
    assert "find_claude_session_files" in text
    assert "find_codex_session_files" in text


def test_codex_bootstrap_uses_generic_session_token_guidance():
    text = _read("templates/CODEX.AGENTS.md.template")
    assert "session_token='codex-<task>-<date>'" in text
    assert "session_client='codex'" in text


def test_dashboard_followups_use_selected_terminal_client():
    text = _read("src/dashboard/app.py")
    assert "build_followup_terminal_shell_command" in text
    assert "selected terminal client" in text
