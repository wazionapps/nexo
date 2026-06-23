import json
from pathlib import Path

from plugins import cards


ROOT = Path(__file__).resolve().parents[1]


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_card_match_fetches_protocol_from_authenticated_backend(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append((request, timeout))
        return FakeResponse({"matches": [{"slug": "hacer-presentacion", "protocol": "runtime only"}]})

    monkeypatch.setenv("NEXO_DESKTOP_AUTH_TOKEN", "tok_cards")
    monkeypatch.setattr(cards, "_urlopen", fake_urlopen)

    payload = json.loads(cards.handle_card_match("hacer una presentacion", limit="3", include_protocol="true"))

    assert payload["ok"] is True
    assert payload["matches"][0]["slug"] == "hacer-presentacion"
    request, timeout = calls[0]
    assert timeout == 20
    assert request.full_url == "https://nexo-desktop.com/api/cards/match"
    assert request.get_header("Authorization") == "Bearer tok_cards"
    body = json.loads(request.data.decode("utf-8"))
    assert body["query"] == "hacer una presentacion"
    assert body["limit"] == 3
    assert body["include_protocol"] is True


def test_card_catalog_can_read_desktop_shared_auth_file(monkeypatch, tmp_path):
    calls = []
    shared = tmp_path / "session.json"
    shared.write_text(json.dumps({"token": "tok_shared"}), encoding="utf-8")

    def fake_urlopen(request, timeout):
        calls.append(request)
        return FakeResponse({"cards": [{"slug": "email-profesional"}]})

    monkeypatch.delenv("NEXO_DESKTOP_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("NEXO_CARDS_TOKEN", raising=False)
    monkeypatch.delenv("NEXO_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("NEXO_SHARED_AUTH_FILE", str(shared))
    monkeypatch.setattr(cards, "_urlopen", fake_urlopen)

    payload = json.loads(cards.handle_card_catalog(locale="es"))

    assert payload["ok"] is True
    assert payload["cards"][0]["slug"] == "email-profesional"
    assert calls[0].get_header("Authorization") == "Bearer tok_shared"
    assert "locale=es" in calls[0].full_url


def test_cards_return_auth_error_without_token(monkeypatch):
    monkeypatch.delenv("NEXO_DESKTOP_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("NEXO_CARDS_TOKEN", raising=False)
    monkeypatch.delenv("NEXO_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("NEXO_SHARED_AUTH_FILE", str(Path("/tmp/nexo-missing-auth.json")))
    monkeypatch.setattr(cards, "_shared_auth_candidates", lambda: [Path("/tmp/nexo-missing-auth.json")])

    payload = json.loads(cards.handle_card_match("presentacion"))

    assert payload["ok"] is False
    assert payload["error"]["type"] == "not_authenticated"


def test_card_match_local_mandatory_support_second_ticket_without_token(monkeypatch):
    monkeypatch.delenv("NEXO_DESKTOP_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("NEXO_CARDS_TOKEN", raising=False)
    monkeypatch.delenv("NEXO_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(cards, "_shared_auth_candidates", lambda: [])

    payload = json.loads(
        cards.handle_card_match(
            "segundo ticket de soporte cloud creditos mismo cliente 72h",
            include_protocol="true",
            locale="es",
        )
    )

    assert payload["ok"] is True
    assert payload["matches"][0]["skill_id"] == "SK-SUPPORT-SECOND-TICKET-PARALLEL-SWEEP"
    assert payload["matches"][0]["mandatory"] is True
    assert "subagentes paralelos" in payload["matches"][0]["protocol"]


def test_card_match_merges_local_mandatory_support_second_ticket_with_backend(monkeypatch):
    def fake_urlopen(_request, timeout):
        return FakeResponse({"matches": [{"slug": "backend-card"}]})

    monkeypatch.setenv("NEXO_DESKTOP_AUTH_TOKEN", "tok_cards")
    monkeypatch.setattr(cards, "_urlopen", fake_urlopen)

    payload = json.loads(cards.handle_card_match("support second ticket cloud credits", locale="en"))

    assert payload["ok"] is True
    assert payload["matches"][0]["slug"] == "support-second-ticket-parallel-sweep"
    assert payload["matches"][1]["slug"] == "backend-card"


def test_open_source_brain_does_not_embed_protocol_catalog():
    source = (ROOT / "src" / "plugins" / "cards.py").read_text(encoding="utf-8")
    package_json = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    tool_map = json.loads((ROOT / "tool-enforcement-map.json").read_text(encoding="utf-8"))

    assert "data/protocol-cards" not in source
    assert "Cuando el usuario pida una presentacion" not in source
    assert not (ROOT / "data" / "protocol-cards").exists()
    assert all("data/protocol-cards" not in entry for entry in package_json["files"])
    assert tool_map["tools"]["nexo_card_match"]["source"] == "plugin:cards"
    assert tool_map["tools"]["nexo_card_get"]["source"] == "plugin:cards"
    assert tool_map["tools"]["nexo_card_catalog"]["source"] == "plugin:cards"
