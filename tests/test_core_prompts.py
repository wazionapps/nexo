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
    assert (core_prompts.PROMPTS_DIR / "followup-runner-operator-attention-context.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "followup-runner-operator-attention-question.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "immune-triage.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "interactive-startup.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "json-object-only.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "learning-validator.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "morning-agent.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "morning-agent-json-output.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "postmortem-consolidator.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "r14-correction-learning-injection.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "r14-correction-learning-question.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "r15-project-context-injection.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "r-catalog.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "r34-identity-coherence-probe.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "r34-identity-coherence-question.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "r16-declared-done-injection.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "r16-declared-done-question.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "r17-promise-debt-injection.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "r17-promise-debt-question.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "r18-followup-autocomplete-injection.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "r19-project-grep-injection.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "r20-constant-change-injection.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "r20-constant-change-question.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "r21-legacy-path-injection.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "r22-personal-script-injection.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "r23-ssh-without-atlas-injection.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "r23b-deploy-vhost-injection.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "r23c-cwd-mismatch-injection.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "r23d-chown-chmod-recursive-injection.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "r23e-force-push-main-injection.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "r23f-db-no-where-injection.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "r23g-secrets-in-output-injection.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "r23h-shebang-mismatch-injection.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "r23i-auto-deploy-ignored-injection.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "r23j-global-install-injection.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "r23k-script-duplicates-skill-injection.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "r23l-resource-collision-injection.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "r23m-message-duplicate-injection.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "r24-stale-memory-injection.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "r25-read-only-host-injection.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "sleep.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "t4-r15-project-context-gate.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "t4-r23e-force-push-gate.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "t4-r23f-db-no-where-gate.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "t4-r23h-shebang-mismatch-gate.md").is_file()


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

    followup_question = core_prompts.render_core_prompt(
        "followup-runner-operator-attention-question",
        subject="Laura",
    )
    followup_context = core_prompts.render_core_prompt(
        "followup-runner-operator-attention-context",
        pending_item="Laura still needs to approve the quote.",
    )
    assert "require Laura to intervene" in followup_question
    assert "Laura still needs to approve the quote." in followup_context
    assert "do not depend on literal keyword matching" in followup_context

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
    r14_question = core_prompts.render_core_prompt("r14-correction-learning-question")
    r14_injection = core_prompts.render_core_prompt("r14-correction-learning-injection")
    r15 = core_prompts.render_core_prompt("r15-project-context-injection", project="nexo-desktop")
    catalog = core_prompts.render_core_prompt("r-catalog", tool="nexo_followup_create")
    r34_probe = core_prompts.render_core_prompt("r34-identity-coherence-probe")
    r34_question = core_prompts.render_core_prompt("r34-identity-coherence-question")
    r16_question = core_prompts.render_core_prompt("r16-declared-done-question")
    r16_injection = core_prompts.render_core_prompt("r16-declared-done-injection")
    r17_question = core_prompts.render_core_prompt("r17-promise-debt-question")
    r17_injection = core_prompts.render_core_prompt("r17-promise-debt-injection")
    r18 = core_prompts.render_core_prompt("r18-followup-autocomplete-injection", count="2", items="- 11")
    r19 = core_prompts.render_core_prompt("r19-project-grep-injection", project="nexo", path="src/main.py")
    r20_question = core_prompts.render_core_prompt("r20-constant-change-question")
    r20_injection = core_prompts.render_core_prompt("r20-constant-change-injection", path="src/foo.py")
    r21 = core_prompts.render_core_prompt("r21-legacy-path-injection", legacy="~/claude", canonical="~/.nexo")
    r22 = core_prompts.render_core_prompt("r22-personal-script-injection", path="personal/scripts/foo.py")
    r23 = core_prompts.render_core_prompt("r23-ssh-without-atlas-injection", host="srv.example.com")
    r23b = core_prompts.render_core_prompt("r23b-deploy-vhost-injection", cmd="scp dist", docroot="/srv/www/a", mapped_domain="a.com", context_domain="b.com")
    r23c = core_prompts.render_core_prompt("r23c-cwd-mismatch-injection", cmd="rm -rf build", cwd="/tmp", project="nexo", expected="/repo/nexo")
    r23d = core_prompts.render_core_prompt("r23d-chown-chmod-recursive-injection", verb="chmod", cmd="chmod -R 777 /var/www", target="/var/www")
    r23e = core_prompts.render_core_prompt("r23e-force-push-main-injection", branch="main", cmd="git push --force origin main")
    r23f = core_prompts.render_core_prompt("r23f-db-no-where-injection", cmd="DELETE FROM users", verb="DELETE")
    r23g = core_prompts.render_core_prompt("r23g-secrets-in-output-injection", cmd="printenv", reason="dumps env")
    r23h = core_prompts.render_core_prompt("r23h-shebang-mismatch-injection", script="tool.py", shebang="/usr/bin/env python3.11", actual="/usr/local/bin/python3.14")
    r23i = core_prompts.render_core_prompt("r23i-auto-deploy-ignored-injection", project="nexo", path="/repo/nexo/src/x.py")
    r23j = core_prompts.render_core_prompt("r23j-global-install-injection", cmd="npm install -g foo", pkg="foo")
    r23k = core_prompts.render_core_prompt("r23k-script-duplicates-skill-injection", script="deploy-audit", skill="release operator", score="0.88", skill_id="42")
    r23l = core_prompts.render_core_prompt("r23l-resource-collision-injection", cmd="whmapi1 createacct username=demo", resource_type="cpanel_account", name="demo", existing_type="user")
    r23m = core_prompts.render_core_prompt("r23m-message-duplicate-injection", thread="patricia@example.com", similarity="97", age_sec="42")
    r24 = core_prompts.render_core_prompt("r24-stale-memory-injection", threshold_days="7")
    r25 = core_prompts.render_core_prompt("r25-read-only-host-injection", host="maria", matched="rm")
    startup = core_prompts.render_core_prompt("interactive-startup")

    assert "Respond with EXACTLY ONE WORD: yes OR no." in strict
    assert "Emit 'yes' or 'no' and stop." in retry
    assert "teaching the assistant a rule it should have known" in r14_question
    assert "nexo_learning_add" in r14_injection
    assert "nexo-desktop" in r15
    assert "nexo_followup_create" in catalog
    assert "shared brain" in r34_probe
    assert "past-tense denial" in r34_question
    assert "task is finished, completed, shipped, or already done" in r16_question
    assert "nexo_task_close" in r16_injection
    assert "explicitly promise a FUTURE action" in r17_question
    assert "promise without execution opens operational debt" in r17_injection
    assert "matches 2 active followup" in r18
    assert "project 'nexo' without Grep" in r19
    assert "module-level constant, configuration key" in r20_question
    assert "src/foo.py" in r20_injection
    assert "~/claude" in r21
    assert "personal/scripts/foo.py" in r22
    assert "srv.example.com" in r23
    assert "mapped to domain 'a.com'" in r23b
    assert "project 'nexo'" in r23c
    assert "chmod -R 777 /var/www" in r23d
    assert "protected branch" in r23e
    assert "production DB" in r23f
    assert "nexo_credential_get" in r23g
    assert "tool.py" in r23h
    assert "auto_deploy=true" in r23i
    assert "foo" in r23j
    assert "skill_id=42" in r23k
    assert "existing record first" in r23l
    assert "97% identical" in r23m
    assert "older than 7 days" in r24
    assert "access_mode=read_only" in r25
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
