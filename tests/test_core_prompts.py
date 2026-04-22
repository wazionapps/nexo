from __future__ import annotations

import ast
import importlib
import os
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
os.environ["NEXO_CODE"] = str(SRC)
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


import core_prompts
importlib.reload(core_prompts)


_PROMPT_LIKE_NAME = re.compile(r".*(PROMPT|QUESTION|TEMPLATE|INJECTION).*")


def test_prompt_catalog_dir_exists_and_contains_automation_prompts():
    assert core_prompts.PROMPTS_DIR.is_dir()
    assert (core_prompts.PROMPTS_DIR / "automation-backend-probe.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "autonomy-mandate-question.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "catchup-assessment.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "check-context.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "daily-synthesis.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "daily-self-audit.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "deep-sleep-extract-json-conversion.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "deep-sleep-extract-json-output.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "codex-protocol-contract.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "drive-area-classifier-system.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "drive-area-classifier-user.md").is_file()
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
    assert (core_prompts.PROMPTS_DIR / "heartbeat-diary-overdue.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "heartbeat-guard-reminder.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "heartbeat-learning-reminder.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "hook-protocol-warning-startup-required.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "hook-protocol-warning-task-open-guard-note.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "hook-protocol-warning-task-open-required.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "hook-protocol-warning-heartbeat-close-evidence.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "hook-protocol-warning-guard-required.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "hook-protocol-warning-workflow-required.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "hook-protocol-warning-task-close-evidence.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "immune-triage.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "interactive-startup.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "json-object-only.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "learning-validator.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "morning-agent.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "morning-agent-json-output.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "post-tool-inbox-reminder.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "postmortem-consolidator.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "r13-pre-edit-guard-injection.md").is_file()
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
    assert (core_prompts.PROMPTS_DIR / "server-mcp-instructions.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "t4-r15-project-context-gate.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "t4-r23e-force-push-gate.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "t4-r23f-db-no-where-gate.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "t4-r23h-shebang-mismatch-gate.md").is_file()
    assert (core_prompts.PROMPTS_DIR / "watchdog-repair.md").is_file()


def test_render_core_prompt_replaces_named_tokens():
    prompt = core_prompts.render_core_prompt(
        "email-monitor",
        assistant_name="Nova",
        agent_mailbox="agent@example.com",
        recent_hot_context="Recent memory: nothing pending.",
        project_atlas_path=Path("/tmp/project-atlas.json"),
        operator_name="Laura",
        operator_language="en",
        email_db_path=Path("/tmp/nexo-email.db"),
        debt_sla_hours=3,
        zombie_timeout_hours=2,
        config_path=Path("/tmp/config.json"),
        agent_email_label="agent@example.com",
        send_reply_target="owner@example.com",
        operator_aliases_label="owner@example.com",
        python_executable="/usr/bin/python3",
        send_reply_script=Path("/tmp/nexo-send-reply.py"),
        trusted_domains_label="example.com",
        routing_rules="No special routing rules.",
        extra_instructions_block="",
        target_block="",
        interactive_block="",
        debt_block="",
    )

    assert "You are Nova" in prompt
    assert "This is your mailbox (agent@example.com)." in prompt
    assert "ALWAYS use the operator's preferred language: en." in prompt
    assert "/tmp/project-atlas.json" in prompt


def test_render_core_prompt_supports_catchup_and_immune_templates():
    probe = core_prompts.render_core_prompt("automation-backend-probe")
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
    assert probe == "Reply exactly OK."

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
    conversion = core_prompts.render_core_prompt(
        "deep-sleep-extract-json-conversion",
        analysis="The operator corrected two protocol misses.",
    )
    deep_sleep_json = core_prompts.render_core_prompt(
        "deep-sleep-extract-json-output",
        session_id="session-123",
    )
    area_system = core_prompts.render_core_prompt("drive-area-classifier-system")
    area_user = core_prompts.render_core_prompt(
        "drive-area-classifier-user",
        text="The sender mailbox keeps bouncing customer replies.",
    )
    autonomy_question = core_prompts.render_core_prompt("autonomy-mandate-question")
    drive_system = core_prompts.render_core_prompt("drive-signal-classifier-system")
    drive_user = core_prompts.render_core_prompt(
        "drive-signal-classifier-user",
        text="ROAS dropped 35% after yesterday's deploy.",
    )

    assert "Return exactly one valid JSON object." in json_only
    assert "Return raw JSON only." in morning_json
    assert "The operator corrected two protocol misses." in conversion
    assert "protocol_summary" in conversion
    assert "session-123" in deep_sleep_json
    assert "cannot_comply" in deep_sleep_json
    assert "stop deferring normal in-scope work" in autonomy_question
    assert "exactly nine labels: shopify, google-ads, meta-ads, wazion, nexo, canaririural, seo, email, none" in area_system
    assert "The sender mailbox keeps bouncing customer replies." in area_user
    assert "one of exactly five labels: anomaly, pattern, gap, opportunity, none" in drive_system
    assert "ROAS dropped 35% after yesterday's deploy." in drive_user


def test_render_core_prompt_supports_enforcer_and_startup_templates():
    strict = core_prompts.render_core_prompt("enforcement-classifier-strict")
    retry = core_prompts.render_core_prompt("enforcement-classifier-retry")
    r14_question = core_prompts.render_core_prompt("r14-correction-learning-question")
    r14_injection = core_prompts.render_core_prompt("r14-correction-learning-injection")
    r13 = core_prompts.render_core_prompt(
        "r13-pre-edit-guard-injection",
        tool_name="Edit",
        path_str="/repo/src/foo.py",
        first_file="/repo/src/foo.py",
    )
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
    codex_contract = core_prompts.render_core_prompt("codex-protocol-contract")
    server_instructions = core_prompts.render_core_prompt("server-mcp-instructions", assistant_name="Nero")
    inbox_reminder = core_prompts.render_core_prompt("post-tool-inbox-reminder", pending="3")
    heartbeat_diary = core_prompts.render_core_prompt(
        "heartbeat-diary-overdue",
        heartbeat_count=14,
        active_minutes=37,
    )
    heartbeat_guard = core_prompts.render_core_prompt("heartbeat-guard-reminder")
    heartbeat_learning = core_prompts.render_core_prompt("heartbeat-learning-reminder")
    hook_startup = core_prompts.render_core_prompt("hook-protocol-warning-startup-required")
    hook_guard_note = core_prompts.render_core_prompt("hook-protocol-warning-task-open-guard-note")
    hook_task_open = core_prompts.render_core_prompt(
        "hook-protocol-warning-task-open-required",
        guard_note=hook_guard_note,
    )
    hook_close = core_prompts.render_core_prompt("hook-protocol-warning-heartbeat-close-evidence")
    hook_guard = core_prompts.render_core_prompt("hook-protocol-warning-guard-required", task_id="PT-42")
    hook_workflow = core_prompts.render_core_prompt("hook-protocol-warning-workflow-required", task_id="PT-42")
    hook_task_close = core_prompts.render_core_prompt(
        "hook-protocol-warning-task-close-evidence",
        task_id="PT-42",
        change_note=" If you really edit, capture `nexo_change_log(...)` too.",
        closeout_note=" If this edit wave came from a user correction or you are leaving a blocker unresolved, include `correction_happened=true` with a reusable learning, or `followup_needed=true`, when you call `nexo_task_close(...)`.",
    )
    watchdog = core_prompts.render_core_prompt(
        "watchdog-repair",
        fail_details="[core] demo failure",
        propagate_block="PROPAGATE",
        nexo_home="/Users/franciscoc/.nexo",
    )

    assert "Respond with EXACTLY ONE WORD: yes OR no." in strict
    assert "Emit 'yes' or 'no' and stop." in retry
    assert "nexo_guard_check(files='/repo/src/foo.py')" in r13
    assert "teaching the assistant a rule it should have known" in r14_question
    assert "nexo_learning_add" in r14_injection
    assert "followup_needed=true" in r14_injection
    assert "nexo-desktop" in r15
    assert "nexo_followup_create" in catalog
    assert "shared brain" in r34_probe
    assert "past-tense denial" in r34_question
    # v7.7 Gap 2: expanded vocabulary so the on_event
    # done_claimed_with_open_task trigger covers sent / deployed /
    # published / released / fixed / resolved (plus Spanish). The
    # token list here must stay in sync with the classifier prompt.
    assert "finished, completed, shipped" in r16_question
    assert "sent, delivered, published, deployed" in r16_question
    assert "released, fixed, resolved" in r16_question
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
    assert "NEXO PROTOCOL (MANDATORY)" in codex_contract
    assert "conditioned learnings or blocking guard rules" in codex_contract
    assert "Nero — cognitive co-operator." in server_instructions
    assert "R26b silent enforcement" in server_instructions
    assert "3 unread inbox message(s)" in inbox_reminder
    assert "14 heartbeats, 37min active" in heartbeat_diary
    assert "nexo_session_diary_write" in heartbeat_diary
    assert "nexo_guard_check" in heartbeat_guard
    assert "nexo_learning_add" in heartbeat_learning
    assert "before `nexo_startup(...)`" in hook_startup
    assert "Run `nexo_guard_check(...)` before reading conditioned or shared code." in hook_guard_note
    assert "without `nexo_task_open(...)`" in hook_task_open
    assert "nexo_change_log(...)" in hook_close
    assert "Task PT-42 is active without a visible guard." in hook_guard
    assert "Task PT-42 already looks multi-step" in hook_workflow
    assert "Protocol reminder for PT-42" in hook_task_close
    assert "followup_needed=true" in hook_task_close
    assert "[core] demo failure" in watchdog
    assert "/Users/franciscoc/.nexo/runtime/logs/watchdog-repair-result.log" in watchdog


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


def test_all_render_core_prompt_calls_point_to_existing_templates():
    src_root = SRC
    pattern = re.compile(r'render_core_prompt\("([^"]+)"')
    seen: set[str] = set()
    for path in sorted(src_root.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            seen.add(match.group(1))
    missing = sorted(name for name in seen if not (core_prompts.PROMPTS_DIR / f"{name}.md").is_file())
    assert not missing, f"Missing core prompt templates for: {missing}"


def test_prompt_like_constants_do_not_embed_inline_prompt_text():
    allowed_names = {"PROMPT_TEMPLATE_NAMES", "CORTEX_PROMPT"}

    def _is_allowed_prompt_source(node: ast.AST) -> bool:
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "render_core_prompt":
                return True
            if node.func.id == "_find_evolution_file":
                return True
        return False

    def _is_inline_stringish(node: ast.AST) -> bool:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return True
        if isinstance(node, ast.JoinedStr):
            return True
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return _is_inline_stringish(node.left) or _is_inline_stringish(node.right)
        return False

    violations: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                continue
            name = node.targets[0].id
            if name in allowed_names or not _PROMPT_LIKE_NAME.match(name):
                continue
            if _is_allowed_prompt_source(node.value):
                continue
            if _is_inline_stringish(node.value):
                violations.append(f"{path.relative_to(SRC)}:{name}")
    assert not violations, f"Inline prompt-like constants must use core prompt catalog: {violations}"


def test_model_callsites_do_not_embed_inline_prompt_literals():
    violations: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func_name = None
            if isinstance(node.func, ast.Name):
                func_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                func_name = node.func.attr
            if func_name not in {"run_automation_prompt", "call_model_raw"}:
                continue
            inline_fields: list[str] = []
            if node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    inline_fields.append("arg0")
                elif isinstance(first, ast.JoinedStr):
                    inline_fields.append("arg0")
            for kw in node.keywords:
                if kw.arg not in {"prompt", "system", "append_system_prompt"}:
                    continue
                if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    inline_fields.append(kw.arg)
                elif isinstance(kw.value, ast.JoinedStr):
                    inline_fields.append(kw.arg)
            if inline_fields:
                violations.append(f"{path.relative_to(SRC)}:{func_name}:{','.join(inline_fields)}")
    assert not violations, f"Inline model prompts must come from the core prompt catalog: {violations}"
