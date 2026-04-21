from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _load_script_module(name: str, filename: str):
    path = SRC / "scripts" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_operator_profile_defaults_to_nova_when_calibration_missing(tmp_path, monkeypatch):
    home = tmp_path / "nexo-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("NEXO_HOME", str(home))

    sys.modules.pop("automation_controls", None)
    import automation_controls

    profile = automation_controls.get_operator_profile()
    assert profile["assistant_name"] == "Nova"


def test_send_reply_uses_sender_domain_and_generic_signature(tmp_path, monkeypatch):
    home = tmp_path / "nexo-home"
    (home / "nexo-email").mkdir(parents=True)
    monkeypatch.setenv("NEXO_HOME", str(home))
    module = _load_script_module("nexo_send_reply_test", "nexo-send-reply.py")

    config = {"email": "agent@hotel-example.com"}
    assert module._message_id_domain(config) == "hotel-example.com"
    assert module._signature_label(config).endswith("agent@hotel-example.com")
    assert "nexo@systeam.es" not in module._signature_label(config).lower()


def test_send_reply_uses_configured_sent_folder_and_skips_without_imap(tmp_path, monkeypatch):
    home = tmp_path / "nexo-home"
    (home / "nexo-email").mkdir(parents=True)
    monkeypatch.setenv("NEXO_HOME", str(home))
    module = _load_script_module("nexo_send_reply_sent_folder_test", "nexo-send-reply.py")

    calls: dict[str, object] = {}

    class FakeImap:
        def __init__(self, host, port):
            calls["host"] = host
            calls["port"] = port

        def login(self, email, password):
            calls["email"] = email
            calls["password"] = password

        def append(self, folder, flags, when, raw_message):
            calls["folder"] = folder
            calls["flags"] = flags
            calls["raw_message"] = raw_message

        def logout(self):
            calls["logged_out"] = True

    monkeypatch.setattr(module.imaplib, "IMAP4_SSL", FakeImap)

    ok = module.save_to_sent(
        {
            "email": "agent@hotel-example.com",
            "password": "secret",
            "imap_host": "imap.hotel-example.com",
            "imap_port": 993,
            "sent_folder": "Sent Items",
        },
        b"raw-message",
    )
    assert ok is True
    assert calls["folder"] == "Sent Items"
    assert calls["raw_message"] == b"raw-message"

    skipped = module.save_to_sent(
        {
            "email": "agent@hotel-example.com",
            "password": "secret",
            "smtp_host": "smtp.hotel-example.com",
            "smtp_port": 465,
        },
        b"raw-message",
    )
    assert skipped is False


def test_send_reply_semantic_event_classifier_handles_non_spanish_replies(tmp_path, monkeypatch):
    home = tmp_path / "nexo-home"
    (home / "nexo-email").mkdir(parents=True)
    monkeypatch.setenv("NEXO_HOME", str(home))
    module = _load_script_module("nexo_send_reply_semantic_event_test", "nexo-send-reply.py")

    monkeypatch.setattr(module, "_classify_reply_event_semantically", lambda _text: "resolution")
    assert module.classify_reply_event("I have attached the completed file and everything is now delivered.") == "resolution"


def test_send_reply_regex_priority_still_wins_over_semantic_fallback(tmp_path, monkeypatch):
    home = tmp_path / "nexo-home"
    (home / "nexo-email").mkdir(parents=True)
    monkeypatch.setenv("NEXO_HOME", str(home))
    module = _load_script_module("nexo_send_reply_regex_priority_test", "nexo-send-reply.py")

    monkeypatch.setattr(module, "_classify_reply_event_semantically", lambda _text: "commitment")
    assert module.classify_reply_event("Hecho, ya está.") == "resolution"


def test_email_monitor_trusted_domains_and_runtime_path_are_generic(tmp_path, monkeypatch):
    home = tmp_path / "nexo-home"
    (home / "nexo-email").mkdir(parents=True)
    monkeypatch.setenv("NEXO_HOME", str(home))
    helper = types.ModuleType("nexo_helper")
    helper.call_tool_text = lambda *args, **kwargs: ""
    monkeypatch.setitem(sys.modules, "nexo_helper", helper)
    module = _load_script_module("nexo_email_monitor_test", "nexo-email-monitor.py")

    domains = module._trusted_sender_domains({
        "trusted_domains": ["clients.example.com"],
        "email": "agent@hotel-example.com",
        "operator_email": "owner@hotel-example.com",
    }, ["billing@hotel-example.com"])
    assert "clients.example.com" in domains
    assert "hotel-example.com" in domains

    runtime_path = module._runtime_path("/usr/bin")
    assert "/Users/franciscoc/.local/bin" not in runtime_path
    assert str(home.parent / ".local" / "bin") in runtime_path
    assert "/usr/bin" in runtime_path


def test_email_monitor_defaults_to_neutral_assistant_name(tmp_path, monkeypatch):
    home = tmp_path / "nexo-home"
    (home / "nexo-email").mkdir(parents=True)
    monkeypatch.setenv("NEXO_HOME", str(home))
    helper = types.ModuleType("nexo_helper")
    helper.call_tool_text = lambda *args, **kwargs: ""
    monkeypatch.setitem(sys.modules, "nexo_helper", helper)
    module = _load_script_module("nexo_email_monitor_identity_test", "nexo-email-monitor.py")

    monkeypatch.setattr(module, "get_operator_profile", lambda: {})

    operator_name, assistant_name = module._get_operator_info()
    assert operator_name == "the operator"
    assert assistant_name == "Nova"


def test_email_monitor_processing_prompt_is_catalog_backed_and_generic(tmp_path, monkeypatch):
    home = tmp_path / "nexo-home"
    (home / "nexo-email").mkdir(parents=True)
    monkeypatch.setenv("NEXO_HOME", str(home))
    helper = types.ModuleType("nexo_helper")
    helper.call_tool_text = lambda *args, **kwargs: ""
    monkeypatch.setitem(sys.modules, "nexo_helper", helper)
    module = _load_script_module("nexo_email_monitor_prompt_test", "nexo-email-monitor.py")

    prompt = module.build_processing_prompt(
        config={
            "email": "agent@hotel-example.com",
            "operator_email": "owner@hotel-example.com",
        },
        operator_name="Laura",
        assistant_name="Nova",
        operator_email="owner@hotel-example.com",
        operator_aliases_label="owner@hotel-example.com, laura@hotel-example.com",
        trusted_domains_label="hotel-example.com, clients.example.com",
        send_reply_script=Path("/tmp/nexo-send-reply.py"),
        send_reply_target="owner@hotel-example.com",
        agent_email_label="agent@hotel-example.com",
        extra_instructions_block="Keep replies short and factual.",
        project_atlas_path=Path("/tmp/project-atlas.json"),
        target_emails=[{"message_id": "<abc@example.com>"}],
        needs_interactive=[],
        normal_emails=[{"message_id": "<abc@example.com>"}],
        debt_block="== PENDING EMAIL DEBT ==\n- none",
        routing_rules="No special routing rules.",
        recent_hot_context="Recent memory: supplier thread active.",
    )

    assert prompt.startswith("You are Nova")
    assert "This is your mailbox (agent@hotel-example.com)." in prompt
    assert "Keep replies short and factual." in prompt
    assert "EMAILS ASSIGNED TO THIS SESSION" in prompt
    assert "Francisco" not in prompt
    assert "franciscocp@gmail.com" not in prompt


def test_followup_runner_detects_dynamic_operator_name(tmp_path, monkeypatch):
    home = tmp_path / "nexo-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("NEXO_HOME", str(home))
    module = _load_script_module("nexo_followup_runner_test", "nexo-followup-runner.py")
    monkeypatch.setattr(module, "_classifier_requires_operator_attention", lambda text, operator_name="": "pricing" in text.lower())
    monkeypatch.setattr(module, "_llm_requires_operator_attention", lambda text, operator_name="": None)

    assert module._followup_needs_operator_attention(
        {"description": "Ask Laura about pricing"},
        operator_name="Laura",
    ) is True
    assert module._followup_needs_operator_attention(
        {"status": "needs_decision", "description": "Choose a rollout window"},
        operator_name="Laura",
    ) is True
    assert module._followup_needs_operator_attention(
        {"owner": "waiting", "description": "Waiting for vendor reply"},
        operator_name="Laura",
    ) is False
    assert module._followup_needs_operator_attention(
        {"description": "Verify nightly backups"},
        operator_name="Laura",
    ) is False


def test_followup_runner_uses_llm_fallback_before_legacy_keyword_probe(tmp_path, monkeypatch):
    home = tmp_path / "nexo-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("NEXO_HOME", str(home))
    module = _load_script_module("nexo_followup_runner_llm_test", "nexo-followup-runner.py")

    monkeypatch.setattr(module, "_classifier_requires_operator_attention", lambda text, operator_name="": None)
    monkeypatch.setattr(module, "_llm_requires_operator_attention", lambda text, operator_name="": True)
    monkeypatch.setattr(module, "_fallback_operator_attention_hint", lambda followup: False)

    assert module._followup_needs_operator_attention(
        {"description": "The operator still needs to approve the contract before we proceed."},
        operator_name="Laura",
    ) is True


def test_followup_runner_no_longer_depends_on_bilingual_keyword_lists(tmp_path, monkeypatch):
    home = tmp_path / "nexo-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("NEXO_HOME", str(home))
    module = _load_script_module("nexo_followup_runner_no_keyword_test", "nexo-followup-runner.py")

    monkeypatch.setattr(module, "_classifier_requires_operator_attention", lambda text, operator_name="": None)
    monkeypatch.setattr(module, "_llm_requires_operator_attention", lambda text, operator_name="": None)

    assert module._followup_needs_operator_attention(
        {"description": "Waiting for operator decision"},
        operator_name="Laura",
    ) is False


def test_followup_runner_local_classifier_labels_include_operator_name(tmp_path, monkeypatch):
    home = tmp_path / "nexo-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("NEXO_HOME", str(home))
    module = _load_script_module("nexo_followup_runner_classifier_prompt_test", "nexo-followup-runner.py")

    seen = {}

    class _Result:
        label = ""
        confidence = 0.91

    class _FakeClassifier:
        def __init__(self, confidence_floor=0.72):
            seen["confidence_floor"] = confidence_floor

        def is_available(self):
            return True

        def classify(self, text, labels, multi_label=False):
            seen["text"] = text
            seen["labels"] = tuple(labels)
            seen["multi_label"] = multi_label
            result = _Result()
            result.label = labels[0]
            return result

    fake_module = type("FakeClassifierModule", (), {"LocalZeroShotClassifier": _FakeClassifier})
    monkeypatch.setitem(sys.modules, "classifier_local", fake_module)

    assert module._classifier_requires_operator_attention(
        "Laura needs to approve the quote before we continue",
        operator_name="Laura",
    ) is True
    assert seen["confidence_floor"] == 0.72
    assert "Laura" in seen["labels"][0]
    assert "keyword" not in seen["labels"][0].lower()


def test_followup_runner_prompt_defaults_to_nova_and_stays_english_base(tmp_path, monkeypatch):
    home = tmp_path / "nexo-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("NEXO_HOME", str(home))
    module = _load_script_module("nexo_followup_runner_prompt_test", "nexo-followup-runner.py")

    monkeypatch.setattr(module, "get_operator_profile", lambda: {
        "operator_name": "Laura",
        "assistant_name": "",
        "operator_email": "",
    })
    monkeypatch.setattr(module, "get_send_reply_script_path", lambda **_kwargs: Path("/tmp/nexo-send-reply.py"))
    monkeypatch.setattr(module, "format_operator_extra_instructions_block", lambda _name: "")
    monkeypatch.setattr(module, "get_recent_activity", lambda _hours=24: "Recent run: checked hotel occupancy.")

    prompt = module.build_prompt([
        {
            "id": "NF-1",
            "description": "Review the supplier reply and continue the thread.",
            "priority": "medium",
            "reasoning": "This impacts next week's procurement window.",
            "history_rules": ["Confirm the pricing before purchase."],
            "history": [{"note": "Supplier replied with updated terms."}],
            "recurrence": "daily",
        }
    ])

    assert prompt.startswith("You are Nova running automated followups")
    assert "Context:" in prompt
    assert "Rules:" in prompt
    assert "Recent history:" in prompt
    assert "Recurrence:" in prompt
    assert "Use this to avoid repeating work" in prompt
    assert "Contexto:" not in prompt
    assert "Reglas:" not in prompt
    assert "Historial reciente:" not in prompt
    assert "Recurrencia:" not in prompt


def test_morning_agent_resolves_default_operator_recipient_generically(tmp_path, monkeypatch):
    home = tmp_path / "nexo-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("NEXO_HOME", str(home))
    module = _load_script_module("nexo_morning_agent_recipient_test", "nexo-morning-agent.py")

    monkeypatch.setattr(
        module,
        "get_operator_briefing_recipient_status",
        lambda: {
            "available": True,
            "recipient_email": "owner@hotel-example.com",
            "recipient_label": "Owner",
        },
    )

    recipient = module.resolve_recipient(
        {
            "operator_email": "",
            "operator_accounts": [],
        }
    )
    assert recipient == "owner@hotel-example.com"


def test_morning_agent_prompt_is_generic_and_language_aware(tmp_path, monkeypatch):
    home = tmp_path / "nexo-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("NEXO_HOME", str(home))
    module = _load_script_module("nexo_morning_agent_prompt_test", "nexo-morning-agent.py")

    prompt = module.build_prompt(
        {
            "generated_at": "2026-04-19T07:00:00+02:00",
            "today": "2026-04-19",
            "operator": {
                "name": "Laura",
                "language": "es",
                "email": "owner@hotel-example.com",
            },
            "assistant": {"name": "Nova"},
            "due_reminders": [],
            "active_reminders": [],
            "due_followups": [],
            "active_followups": [],
            "recent_diaries": [],
            "counts": {},
        },
        extra_instructions_block="Keep the note tight and operator-facing.",
    )

    assert "Francisco" not in prompt
    assert "franciscocp@gmail.com" not in prompt
    assert "Use the operator's preferred language: es." in prompt
    assert "Keep the note tight and operator-facing." in prompt
    assert '"subject": "string"' in prompt


def test_morning_agent_contract_requires_operator_recipient(tmp_path, monkeypatch):
    home = tmp_path / "nexo-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("NEXO_HOME", str(home))

    sys.modules.pop("automation_controls", None)
    import automation_controls

    monkeypatch.setattr(
        automation_controls,
        "get_agent_email_account_status",
        lambda: {
            "available": True,
            "reason_code": "",
            "reason": "",
            "eligible_labels": ["agent-primary"],
        },
    )
    monkeypatch.setattr(
        automation_controls,
        "get_operator_briefing_recipient_status",
        lambda: {
            "available": False,
            "reason_code": "missing_operator_recipient",
            "reason": "No default operator recipient is configured yet.",
            "recipient_email": "",
            "recipient_label": "",
        },
    )

    contract = automation_controls.get_script_runtime_contract("morning-agent")
    assert contract["available"] is False
    assert contract["blocked_reason_code"] == "missing_operator_recipient"


def test_core_automation_contracts_expose_product_controls(tmp_path, monkeypatch):
    home = tmp_path / "nexo-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("NEXO_HOME", str(home))

    sys.modules.pop("automation_controls", None)
    import automation_controls

    monkeypatch.setattr(
        automation_controls,
        "get_agent_email_account_status",
        lambda: {
            "available": True,
            "reason_code": "",
            "reason": "",
            "eligible_labels": ["agent-primary"],
        },
    )
    monkeypatch.setattr(
        automation_controls,
        "get_operator_briefing_recipient_status",
        lambda: {
            "available": True,
            "reason_code": "",
            "reason": "",
            "recipient_email": "owner@hotel-example.com",
            "recipient_label": "Owner",
        },
    )

    email_contract = automation_controls.get_script_runtime_contract("email-monitor")
    assert email_contract["toggleable_core"] is True
    assert email_contract["supports_extra_instructions"] is True
    assert email_contract["schedule_configurable"] is True
    assert email_contract["schedule_type"] == "interval"
    assert email_contract["minimum_interval_seconds"] == 60
    assert email_contract["required_roles"] == ["both"]
    assert email_contract["available"] is True

    followup_contract = automation_controls.get_script_runtime_contract("followup-runner")
    assert followup_contract["toggleable_core"] is True
    assert followup_contract["supports_extra_instructions"] is True
    assert followup_contract["schedule_configurable"] is True
    assert followup_contract["schedule_type"] == "interval"
    assert followup_contract["minimum_interval_seconds"] == 300
    assert followup_contract["required_roles"] == ["both"]
    assert followup_contract["available"] is True

    morning_contract = automation_controls.get_script_runtime_contract("morning-agent")
    assert morning_contract["toggleable_core"] is True
    assert morning_contract["supports_extra_instructions"] is True
    assert morning_contract["schedule_configurable"] is True
    assert morning_contract["schedule_type"] == "calendar"
    assert morning_contract["required_roles"] == ["both"]
    assert morning_contract["available"] is True


def test_synthesis_prompt_is_catalog_backed_and_generic(tmp_path, monkeypatch):
    home = tmp_path / "nexo-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("NEXO_HOME", str(home))
    module = _load_script_module("nexo_synthesis_prompt_test", "nexo-synthesis.py")

    prompt = module.render_core_prompt(
        "daily-synthesis",
        data_json='{"changes": ["updated release gate"]}',
        output_file=Path("/tmp/daily-synthesis.md"),
        today_str="2026-04-20",
    )

    assert "FIRST: Call nexo_startup(task='daily synthesis')" in prompt
    assert '{"changes": ["updated release gate"]}' in prompt
    assert "/tmp/daily-synthesis.md" in prompt
    assert "2026-04-20" in prompt


def test_postmortem_prompt_is_catalog_backed_and_generic(tmp_path, monkeypatch):
    home = tmp_path / "nexo-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("NEXO_HOME", str(home))
    module = _load_script_module("nexo_postmortem_prompt_test", "nexo-postmortem-consolidator.py")

    prompt = module.render_core_prompt(
        "postmortem-consolidator",
        date="2026-04-20",
        session_total=8,
        sessions_with_critique=3,
        diaries_json='[{"self_critique":"Skipped verification"}]',
        existing_feedback_count=2,
        existing_feedbacks_json='["feedback_postmortem_verify_before_done"]',
        recent_rules_json='["Always verify before closing."]',
        memory_dir=Path("/tmp/memory"),
        postmortem_daily_file=Path("/tmp/postmortem-daily.md"),
    )

    assert "nightly postmortem consolidation" in prompt
    assert "SESSIONS TODAY: 8 total, 3 with self-critique" in prompt
    assert '/tmp/memory' in prompt
    assert '/tmp/postmortem-daily.md' in prompt


def test_sleep_prompt_is_catalog_backed_and_generic(tmp_path, monkeypatch):
    home = tmp_path / "nexo-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("NEXO_HOME", str(home))
    module = _load_script_module("nexo_sleep_prompt_test", "nexo-sleep.py")

    prompt = module.render_core_prompt(
        "sleep",
        learnings_count=42,
        memory_md_lines=190,
        preferences_count=7,
        feedback_count=12,
        old_observations_count=501,
        tasks_block="TASK 1: consolidate learnings",
        sleep_report_file=Path("/tmp/sleep-report.md"),
    )

    assert "You are NEXO Sleep" in prompt
    assert "- 42 active learnings" in prompt
    assert "TASK 1: consolidate learnings" in prompt
    assert "/tmp/sleep-report.md" in prompt


def test_catchup_prompt_is_catalog_backed_and_generic(tmp_path, monkeypatch):
    home = tmp_path / "nexo-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("NEXO_HOME", str(home))
    module = _load_script_module("nexo_catchup_prompt_test", "nexo-catchup.py")

    prompt = module.render_core_prompt(
        "catchup-assessment",
        ran=4,
        skipped=2,
        state_summary='{"daily-synthesis": "2026-04-20T07:00:00"}',
        assessment_file=Path("/tmp/catchup-assessment.md"),
        now_label="2026-04-20 09:30",
    )

    assert "NEXO Catch-Up system" in prompt
    assert "4 scheduled tasks just ran as catch-up" in prompt
    assert "/tmp/catchup-assessment.md" in prompt
    assert "2026-04-20 09:30" in prompt


def test_immune_prompt_is_catalog_backed_and_generic(tmp_path, monkeypatch):
    home = tmp_path / "nexo-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("NEXO_HOME", str(home))
    module = _load_script_module("nexo_immune_prompt_test", "nexo-immune.py")

    prompt = module.render_core_prompt(
        "immune-triage",
        triage_file=Path("/tmp/immune-triage.md"),
        findings_json='{"counts":{"FAIL":1,"WARN":2},"repairs":["ok"]}',
    )

    assert "NEXO Immune System triage analyst" in prompt
    assert "/tmp/immune-triage.md" in prompt
    assert '"FAIL":1' in prompt
