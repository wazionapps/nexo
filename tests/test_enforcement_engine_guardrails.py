from __future__ import annotations

import types


def test_capability_denial_without_reality_check_enqueues_prompt(monkeypatch):
    import enforcement_engine

    engine = enforcement_engine.HeadlessEnforcer()
    monkeypatch.setattr(engine, "_guardian_rule_mode", lambda _rule: "hard")
    engine.on_assistant_text("No se puede hacer eso; no existe esa capacidad.", declared_detector=lambda _text: False)

    assert any(item.get("tag") == "r34:capability-denial-without-reality-check" for item in engine.injection_queue)


def test_capability_denial_after_catalog_check_is_allowed(monkeypatch):
    import enforcement_engine

    engine = enforcement_engine.HeadlessEnforcer()
    engine.tools_called.add("nexo_system_catalog")
    monkeypatch.setattr(engine, "_guardian_rule_mode", lambda _rule: "hard")
    engine.on_assistant_text("No se puede hacer eso; no existe esa capacidad.", declared_detector=lambda _text: False)

    assert not any(item.get("tag") == "r34:capability-denial-without-reality-check" for item in engine.injection_queue)


def test_r23g_creates_security_followup(monkeypatch):
    import enforcement_engine

    created = []
    fake_db = types.SimpleNamespace(
        get_followup=lambda _id: None,
        create_followup=lambda *args, **kwargs: created.append((args, kwargs)) or {"id": args[0]},
    )
    monkeypatch.setitem(__import__("sys").modules, "db", fake_db)

    engine = enforcement_engine.HeadlessEnforcer()
    monkeypatch.setattr(engine, "_guardian_rule_mode", lambda _rule: "hard")
    engine._check_r23g("Bash", {"command": "echo $OPENAI_API_KEY > /tmp/key.txt"})

    assert created
    followup_id = created[0][0][0]
    payload = created[0][1]
    assert followup_id.startswith("NF-SECURITY-EXPOSED-CREDENTIAL-")
    assert payload["priority"] == "critical"
    assert payload["owner"] == "agent"
    assert "HTTP 401" in payload["verification"]


def test_first_response_jargon_enqueues_rewrite(monkeypatch):
    import enforcement_engine

    engine = enforcement_engine.HeadlessEnforcer()
    engine.on_user_message("dime cómo va")
    monkeypatch.setattr(engine, "_guardian_rule_mode", lambda _rule: "hard")

    engine.on_assistant_text("El guard_check y el cortex salieron bien.", declared_detector=lambda _text: False)

    assert any(item.get("tag") == "r26:first-response-jargon" for item in engine.injection_queue)


def test_execute_before_ask_enqueues_after_clear_imperative(monkeypatch):
    import enforcement_engine

    engine = enforcement_engine.HeadlessEnforcer()
    engine.on_user_message("hazlo ya con sentido común")
    monkeypatch.setattr(engine, "_guardian_rule_mode", lambda _rule: "hard")

    engine.on_assistant_text("Tengo dos decisiones: ¿quieres que lo ejecute o prefieres esperar?", declared_detector=lambda _text: False)

    assert any(item.get("tag") == "r35:execute-before-ask" for item in engine.injection_queue)


def test_production_mutation_requires_change_trace_before_close(monkeypatch):
    import enforcement_engine

    engine = enforcement_engine.HeadlessEnforcer()
    monkeypatch.setattr(engine, "_guardian_rule_mode", lambda _rule: "hard")

    engine.on_tool_call("Bash", {"command": "git push origin main"})
    engine.on_tool_call("nexo_task_close", {"task_id": "PT-1", "outcome": "done", "evidence": "deploy succeeded"})

    assert any(item.get("tag") == "r36:production-change-log" for item in engine.injection_queue)


def test_production_mutation_detects_external_surfaces(monkeypatch):
    import enforcement_engine

    engine = enforcement_engine.HeadlessEnforcer()

    commands = [
        "scp app.php root@server:/home/acct/public_html/app.php",
        "ssh vicshop 'cp /tmp/app.php /home/acct/httpdocs/app.php'",
        "gcloud run services update api --image image:latest",
        "gcloud dns record-sets transaction execute --zone prod",
        "whmapi1 createacct username=test domain=example.com",
        "curl -X DELETE https://api.cloudflare.com/client/v4/zones/z/dns_records/r",
    ]

    assert all(engine._production_mutation_summary("Bash", {"command": cmd}) for cmd in commands)


def test_production_mutation_close_with_change_trace_is_allowed(monkeypatch):
    import enforcement_engine

    engine = enforcement_engine.HeadlessEnforcer()
    monkeypatch.setattr(engine, "_guardian_rule_mode", lambda _rule: "hard")

    engine.on_tool_call("Bash", {"command": "git push origin main"})
    engine.on_tool_call(
        "nexo_task_close",
        {
            "task_id": "PT-1",
            "outcome": "done",
            "evidence": "deploy succeeded",
            "files_changed": "src/server.py",
            "change_summary": "Deploy product fix",
        },
    )

    assert not any(item.get("tag") == "r36:production-change-log" for item in engine.injection_queue)
