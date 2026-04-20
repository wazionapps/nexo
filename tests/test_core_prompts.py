from __future__ import annotations

import sys
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


import core_prompts


def test_prompt_catalog_dir_exists_and_contains_automation_prompts():
    assert core_prompts.PROMPTS_DIR.is_dir()
    assert (core_prompts.PROMPTS_DIR / "catchup-assessment.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "check-context.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "daily-synthesis.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "daily-self-audit.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "deep-sleep-extract-json-output.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "drive-signal-classifier-system.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "drive-signal-classifier-user.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "email-monitor.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "enforcement-classifier-retry.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "enforcement-classifier-strict.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "evolution-public-contribution.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "evolution-public-pr-review.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "evolution-weekly.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "followup-runner.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "immune-triage.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "interactive-startup.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "json-object-only.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "learning-validator.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "morning-agent.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "morning-agent-json-output.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "postmortem-consolidator.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "r-catalog.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "r34-identity-coherence-probe.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "r34-identity-coherence-question.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "sleep.md").is_file()


def test_render_core_prompt_replaces_named_tokens():
    prompt = core_prompts.render_core_prompt(
        "morning-agent",
        assistant_name="Nova",
        operator_name="Laura",
        operator_language="en",
        extra_section="",
        context_json='{"ok": true}',
    )

    assert "You are Nova, preparing the daily morning briefing email for Laura." in prompt
    assert "Use the operator's preferred language: en." in prompt
    assert '{"ok": true}' in prompt


def test_render_core_prompt_supports_catchup_and_immune_templates():
    catchup = core_prompts.render_core_prompt(
        "catchup-assessment",
        ran=3,
        skipped=1,
        state_summary='{"daily-synthesis": "2026-04-20T07:00:00"}',
        assessment_file=Path("/tmp/catchup-assessment.md"),
        now_label="2026-04-20 09:30",
    )
    immune = core_prompts.render_core_prompt(
        "immune-triage",
        triage_file=Path("/tmp/immune-triage.md"),
        findings_json='{"FAIL": 1, "WARN": 2}',
    )

    assert "The Mac was off/asleep and 3 scheduled tasks just ran as catch-up" in catchup
    assert "/tmp/catchup-assessment.md" in catchup
    assert "2026-04-20 09:30" in catchup

    assert "You are the NEXO Immune System triage analyst." in immune
    assert "/tmp/immune-triage.md" in immune
    assert '{"FAIL": 1, "WARN": 2}' in immune

    audit = core_prompts.render_core_prompt(
        "daily-self-audit",
        errors_count=2,
        warns_count=4,
        findings_json='[{"severity":"ERROR","title":"DB locked"}]',
        log_dir=Path("/tmp/runtime/logs"),
        audit_date="2026-04-20",
    )

    assert "The mechanical checks found" in audit
    assert "2 errors and 4 warnings" in audit
    assert '[{"severity":"ERROR","title":"DB locked"}]' in audit
    assert "/tmp/runtime/logs/self-audit-interpreted.md" in audit
    assert "# NEXO Self-Audit — 2026-04-20" in audit


def test_render_core_prompt_supports_learning_validator_and_context_dedup_templates():
    validator = core_prompts.render_core_prompt(
        "learning-validator",
        finding="Fix updater replacing only Contents/ broke packaged Desktop.",
        learnings_total=2,
        learnings_json='[{"id": 12, "title": "Updater replaces only Contents"}]',
    )
    checker = core_prompts.render_core_prompt(
        "check-context",
        action_description="Reply to Patricia about the overdue invoice.",
        additional_context="Customer follow-up pending since yesterday.",
        recent_actions_json='[{"action": "reply_email", "target": "patricia@example.com"}]',
    )

    assert "Fix updater replacing only Contents/ broke packaged Desktop." in validator
    assert '[{"id": 12, "title": "Updater replaces only Contents"}]' in validator
    assert "confidence >= 0.7 and same root cause = known: true" in validator

    assert "Reply to Patricia about the overdue invoice." in checker
    assert "Customer follow-up pending since yesterday." in checker
    assert '[{"action": "reply_email", "target": "patricia@example.com"}]' in checker


def test_render_core_prompt_supports_json_and_drive_classifier_templates():
    json_only = core_prompts.render_core_prompt("json-object-only")
    morning_json = core_prompts.render_core_prompt("morning-agent-json-output")
    deep_sleep_json = core_prompts.render_core_prompt(
        "deep-sleep-extract-json-output",
        session_id="session-123",
    )
    drive_system = core_prompts.render_core_prompt("drive-signal-classifier-system")
    drive_user = core_prompts.render_core_prompt(
        "drive-signal-classifier-user",
        text="ROAS dropped 35% after yesterday's deploy.",
    )

    assert "Return exactly one valid JSON object." in json_only
    assert "Return raw JSON only." in morning_json
    assert "session-123" in deep_sleep_json
    assert "cannot_comply" in deep_sleep_json
    assert "one of exactly five labels: anomaly, pattern, gap, opportunity, none" in drive_system
    assert "ROAS dropped 35% after yesterday's deploy." in drive_user


def test_render_core_prompt_supports_enforcer_and_startup_templates():
    strict = core_prompts.render_core_prompt("enforcement-classifier-strict")
    retry = core_prompts.render_core_prompt("enforcement-classifier-retry")
    catalog = core_prompts.render_core_prompt("r-catalog", tool="nexo_followup_create")
    r34_probe = core_prompts.render_core_prompt("r34-identity-coherence-probe")
    r34_question = core_prompts.render_core_prompt("r34-identity-coherence-question")
    startup = core_prompts.render_core_prompt("interactive-startup")

    assert "Respond with EXACTLY ONE WORD: yes OR no." in strict
    assert "Emit 'yes' or 'no' and stop." in retry
    assert "nexo_followup_create" in catalog
    assert "shared brain" in r34_probe
    assert "past-tense denial" in r34_question
    assert "run nexo_startup and nexo_heartbeat" in startup


def test_render_core_prompt_supports_evolution_templates():
    weekly = core_prompts.render_core_prompt(
        "evolution-weekly",
        learnings_this_week=4,
        decisions_this_week=2,
        changes_this_week=3,
        diaries_this_week=5,
        evolution_history=7,
        current_scores_json='{"autonomy": 37}',
        mode="managed",
        mode_desc="owner-managed",
        cycle_number=8,
        nexo_db="/tmp/nexo.db",
        week_cutoff_ts="12345",
        safe_zones="src/, tests/",
        immutable_files="server.py",
    )
    public_contrib = core_prompts.render_core_prompt(
        "evolution-public-contribution",
        repo_root="/tmp/public-repo",
        cycle_number=3,
        queued_section="PRIORITY PUBLIC-PORT QUEUE ITEM",
    )
    public_review = core_prompts.render_core_prompt(
        "evolution-public-pr-review",
        pr_number=42,
        author="nexo-bot",
        url="https://example.com/pr/42",
        title="fix: runtime drift",
        body="This closes a drift gap.",
        rendered_files="- src/update.py",
        trimmed_diff="diff --git a/x b/x",
    )

    assert "Current scores: {\"autonomy\": 37}" in weekly
    assert "Cycle: #3" in public_contrib
    assert "/tmp/public-repo" in public_contrib
    assert "Number: #42" in public_review
    assert "fix: runtime drift" in public_review
