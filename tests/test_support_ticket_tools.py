from __future__ import annotations

import json

import tools_api_call


def test_support_ticket_create_calls_real_support_endpoint(monkeypatch):
    calls = []

    def fake_api_call(method, path, body_json="", idempotency_key="", headers_json="", base_url=""):
        calls.append((method, path, body_json, idempotency_key, headers_json, base_url))
        return "HTTP 201 POST /api/support/tickets"

    monkeypatch.setattr(tools_api_call, "handle_api_call", fake_api_call)

    result = tools_api_call.handle_support_ticket_create(
        "Fallo Email NEXO",
        "El agente no encuentra el ticket real.",
        "urgent",
    )

    assert result.startswith("HTTP 201")
    assert calls[0][0] == "POST"
    assert calls[0][1] == "/api/support/tickets"
    payload = json.loads(calls[0][2])
    assert payload == {
        "subject": "Fallo Email NEXO",
        "message": "El agente no encuentra el ticket real.",
        "priority": "urgent",
    }


def test_support_ticket_list_and_read_use_real_support_routes(monkeypatch):
    calls = []

    def fake_api_call(method, path, body_json="", idempotency_key="", headers_json="", base_url=""):
        calls.append((method, path))
        return f"HTTP 200 {method} {path}"

    monkeypatch.setattr(tools_api_call, "handle_api_call", fake_api_call)

    tools_api_call.handle_support_ticket_list("waiting customer", 250)
    tools_api_call.handle_support_ticket_read("ticket/18")

    assert calls[0] == ("GET", "/api/support/tickets?status=waiting+customer&limit=100")
    assert calls[1] == ("GET", "/api/support/tickets/ticket%2F18")


def test_support_ticket_create_validates_required_fields(monkeypatch):
    monkeypatch.setattr(tools_api_call, "handle_api_call", lambda *args, **kwargs: "should-not-call")

    assert tools_api_call.handle_support_ticket_create("", "body").startswith("ERROR:")
    assert tools_api_call.handle_support_ticket_create("subject", "").startswith("ERROR:")
