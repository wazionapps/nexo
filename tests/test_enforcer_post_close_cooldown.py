from __future__ import annotations


def _protocol_tags(enforcer):
    return [
        item["tag"] for item in enforcer.injection_queue
        if not str(item.get("tag", "")).startswith("r18:")
    ]


def test_task_close_starts_cooldown_and_clears_protocol_injections(monkeypatch):
    monkeypatch.setenv("NEXO_ENFORCER_POST_CLOSE_COOLDOWN_SECONDS", "60")

    from enforcement_engine import HeadlessEnforcer

    enforcer = HeadlessEnforcer()
    enforcer.injection_queue.append({
        "prompt": "Execute nexo_task_open before more work.",
        "tag": "start:nexo_task_open",
        "at": 1.0,
        "rule_id": "on_session_start",
    })

    enforcer.on_tool_call("nexo_task_close")

    assert enforcer._post_close_cooldown_active() is True
    assert _protocol_tags(enforcer) == []

    enforcer._enqueue(
        "Execute nexo_smart_startup and nexo_task_open.",
        "periodic_msg:nexo_smart_startup",
        rule_id="periodic_by_messages",
    )
    assert _protocol_tags(enforcer) == []

    enforcer._enqueue(
        "Review deployment target before editing.",
        "R23e_force_push_main",
        rule_id="R23e_force_push_main",
    )
    assert _protocol_tags(enforcer) == ["R23e_force_push_main"]


def test_post_close_cooldown_suppresses_periodic_reinjection(monkeypatch):
    monkeypatch.setenv("NEXO_ENFORCER_POST_CLOSE_COOLDOWN_SECONDS", "60")

    from enforcement_engine import HeadlessEnforcer

    enforcer = HeadlessEnforcer()
    enforcer._on_start = [
        {
            "tool": "nexo_task_open",
            "rule": {"threshold": 1},
            "enf": {"inject_prompt": "Execute nexo_task_open."},
        }
    ]
    enforcer.user_message_count = 3

    enforcer.on_tool_call("nexo_task_close")
    enforcer.check_periodic()

    assert _protocol_tags(enforcer) == []


def test_nexo_stop_is_terminal_for_headless_periodic_checks(monkeypatch):
    monkeypatch.setenv("NEXO_ENFORCER_POST_CLOSE_COOLDOWN_SECONDS", "60")

    from enforcement_engine import HeadlessEnforcer

    enforcer = HeadlessEnforcer()
    enforcer._on_start = [
        {
            "tool": "nexo_task_open",
            "rule": {"threshold": 1},
            "enf": {"inject_prompt": "Execute nexo_task_open."},
        }
    ]
    enforcer._conditional = [
        {
            "tool": "nexo_task_open",
            "rule": {"threshold": 1},
            "enf": {"inject_prompt": "Execute nexo_task_open before more work."},
        }
    ]
    enforcer.user_message_count = 3
    enforcer._conditional_counters["nexo_task_open"] = 3

    enforcer.on_tool_call("nexo_stop")
    enforcer.check_periodic()

    assert enforcer._session_stopped is True
    assert enforcer._post_close_cooldown_active() is True
    assert _protocol_tags(enforcer) == []
