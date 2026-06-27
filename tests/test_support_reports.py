from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_SRC = REPO_ROOT / "src"

if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


def test_evolution_support_ticket_is_anonymized(monkeypatch):
    import support_reports
    import tools_api_call

    calls = []

    def fake_create(subject, message, priority="normal", client_message_id="", origin="desktop"):
        calls.append(
            {
                "subject": subject,
                "message": message,
                "priority": priority,
                "client_message_id": client_message_id,
                "origin": origin,
            }
        )
        return "HTTP 201 POST /api/support/tickets\n{}"

    monkeypatch.setattr(tools_api_call, "handle_support_ticket_create", fake_create)

    result = support_reports.create_evolution_support_ticket(
        cycle_num=8,
        analysis=(
            "Pattern from /Users/franciscoc/client/private and user@example.com "
            "at https://client.example/private token=sk-testsecret123456"
        ),
        proposals=[
            {
                "dimension": "self_improvement",
                "scope": "public",
                "classification": "auto",
                "impact": "critical",
                "action": "Add a support workflow for /Users/franciscoc/client/project",
                "reasoning": "Repeated failures mention user@example.com and https://client.example/private",
            }
        ],
        queued_candidates=[
            {
                "title": "Port private fix",
                "reasoning": "Found in /home/alice/private with ghp_secretsecretsecret",
                "files_changed": ["src/plugins/protocol.py"],
            }
        ],
        dedupe_key="evolution-test",
    )

    assert result["success"] is True
    assert calls[0]["origin"] == "auto_incident"
    assert calls[0]["priority"] == "high"
    assert calls[0]["client_message_id"] == "evolution-test"
    outbound = calls[0]["message"]
    assert "/Users/franciscoc" not in outbound
    assert "/home/alice" not in outbound
    assert "user@example.com" not in outbound
    assert "https://client.example/private" not in outbound
    assert "sk-testsecret123456" not in outbound
    assert "ghp_secretsecretsecret" not in outbound
    assert "[redacted-path]" in outbound
    assert "[redacted-email]" in outbound
    assert "[redacted-url]" in outbound
    assert "[redacted-secret]" in outbound
