from __future__ import annotations

import importlib
import json
from pathlib import Path


def _write_claude_session(path: Path) -> None:
    rows = [
        {
            "type": "user",
            "uuid": "u1",
            "message": {"content": "Tengo problemas con el WiFi de DIGI en casa."},
        },
        {
            "type": "message",
            "message": {"content": [{"type": "text", "text": "Vamos a revisar router, ONT y soporte de DIGI."}]},
        },
        {
            "type": "user",
            "uuid": "u2",
            "message": {"content": "El WiFi se cae varias veces al dia."},
        },
        {
            "type": "message",
            "message": {"content": [{"type": "text", "text": "Apunto que el fallo afecta a la red WiFi, no solo a un dispositivo."}]},
        },
        {
            "type": "user",
            "uuid": "u3",
            "message": {"content": "Quiero recordar esta conversacion si vuelve a pasar."},
        },
        {
            "type": "message",
            "message": {"content": [{"type": "text", "text": "Usaremos transcript fallback si hot context no basta."}]},
        },
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")


def test_transcript_search_and_read(monkeypatch, tmp_path):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))

    transcript_path = home / ".claude" / "projects" / "wifi" / "session-wifi.jsonl"
    _write_claude_session(transcript_path)

    import transcript_utils
    import tools_transcripts

    importlib.reload(transcript_utils)
    importlib.reload(tools_transcripts)

    search_text = tools_transcripts.handle_transcript_search("wifi digi", hours=24, limit=5)
    assert "TRANSCRIPTS (1)" in search_text
    assert "session-wifi.jsonl" in search_text
    assert "digi" in search_text.lower()

    read_text = tools_transcripts.handle_transcript_read(session_ref="claude_code:session-wifi.jsonl", max_messages=10)
    assert "TRANSCRIPT claude_code:session-wifi.jsonl" in read_text
    assert "Tengo problemas con el WiFi de DIGI" in read_text
    assert "router, ONT y soporte de DIGI" in read_text
