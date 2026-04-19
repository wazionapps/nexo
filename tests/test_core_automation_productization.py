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


def test_followup_runner_detects_dynamic_operator_name(tmp_path, monkeypatch):
    home = tmp_path / "nexo-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("NEXO_HOME", str(home))
    module = _load_script_module("nexo_followup_runner_test", "nexo-followup-runner.py")

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

    monkeypatch.setattr(module, "_classifier_requires_operator_attention", lambda text: None)
    monkeypatch.setattr(module, "_llm_requires_operator_attention", lambda text: True)
    monkeypatch.setattr(module, "_legacy_operator_attention_hint", lambda text, operator_name="": False)

    assert module._followup_needs_operator_attention(
        {"description": "The operator still needs to approve the contract before we proceed."},
        operator_name="Laura",
    ) is True


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
