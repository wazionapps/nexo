from __future__ import annotations

import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def test_combine_system_messages_skips_empty_items():
    from hooks.post_tool_use import _combine_system_messages

    assert _combine_system_messages("", None, "  ") is None
    assert _combine_system_messages("alpha", "", "beta") == "alpha\n\nbeta"


def test_main_emits_protocol_guardrail_output_as_system_message(monkeypatch, capsys):
    from hooks import post_tool_use

    monkeypatch.setattr(post_tool_use, "_read_stdin_json", lambda: {"session_id": "sid-1"})
    monkeypatch.setattr(post_tool_use, "_run_auto_capture", lambda payload: 0)
    monkeypatch.setattr(post_tool_use, "_record", lambda *args, **kwargs: None)
    monkeypatch.setattr(post_tool_use, "_resolve_sid_from_payload", lambda payload: "nexo-1")
    monkeypatch.setattr(post_tool_use, "check_inbox_and_emit_reminder", lambda sid: None)

    def fake_run_step(cmd: list[str], timeout: int) -> tuple[int, str]:
        name = Path(cmd[-1]).name
        if name == "protocol-guardrail.sh":
            return 0, "NEXO DISCIPLINE:\n- PROTOCOL REMINDER: close with nexo_task_close"
        return 0, ""

    monkeypatch.setattr(post_tool_use, "_run_step", fake_run_step)

    rc = post_tool_use.main()
    out = capsys.readouterr().out.strip()

    assert rc == 0
    payload = json.loads(out)
    assert "NEXO DISCIPLINE" in payload["systemMessage"]
    assert "nexo_task_close" in payload["systemMessage"]


def test_main_merges_protocol_and_inbox_messages(monkeypatch, capsys):
    from hooks import post_tool_use

    monkeypatch.setattr(post_tool_use, "_read_stdin_json", lambda: {"session_id": "sid-2"})
    monkeypatch.setattr(post_tool_use, "_run_auto_capture", lambda payload: 0)
    monkeypatch.setattr(post_tool_use, "_record", lambda *args, **kwargs: None)
    monkeypatch.setattr(post_tool_use, "_resolve_sid_from_payload", lambda payload: "nexo-2")
    monkeypatch.setattr(post_tool_use, "check_inbox_and_emit_reminder", lambda sid: "Inbox reminder")
    monkeypatch.setattr(
        post_tool_use,
        "_run_step",
        lambda cmd, timeout: (0, "Protocol closeout reminder" if Path(cmd[-1]).name == "protocol-guardrail.sh" else ""),
    )

    rc = post_tool_use.main()
    out = capsys.readouterr().out.strip()

    assert rc == 0
    payload = json.loads(out)
    assert payload["systemMessage"] == "Protocol closeout reminder\n\nInbox reminder"


def test_post_tool_use_warns_after_production_push_without_change_log(monkeypatch):
    from hooks import post_tool_use

    monkeypatch.setattr(post_tool_use, "_production_closeout_dir", lambda: Path(post_tool_use.os.environ["NEXO_HOME"]) / "runtime" / "operations" / "protocol-closeout")
    message = post_tool_use.check_production_change_log_closeout(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "git push origin main"},
        },
        "nexo-test-prod",
    )

    assert message is not None
    assert "nexo_change_log" in message


def test_post_tool_use_detects_external_webroot_dns_cloudrun_and_vps_mutations():
    from hooks import post_tool_use

    commands = [
        "rsync -av index.php root@cl105e:/home/site/public_html/index.php",
        "rsync -av app/ vicshop:/home/nexodesk/app/",
        "ssh vicshop 'sed -i s/foo/bar/ /home/site/httpdocs/index.php'",
        "ssh vicshop 'tee /home/nexodesk/app/.env >/dev/null'",
        "gcloud run deploy app --image europe-west1-docker.pkg.dev/proj/app:latest",
        "gcloud dns record-sets transaction execute --zone prod-zone",
        "whmapi1 createacct username=test domain=example.com",
        "curl -X PATCH https://api.cloudflare.com/client/v4/zones/z/dns_records/r",
    ]

    assert all(post_tool_use._is_production_mutation_command(cmd) for cmd in commands)


def test_post_tool_use_webroot_canary_creates_rotation_followup(monkeypatch):
    from hooks import post_tool_use

    created = []

    class FakeDb:
        @staticmethod
        def get_followup(_followup_id):
            return None

        @staticmethod
        def create_followup(*args, **kwargs):
            created.append((args, kwargs))
            return {"id": args[0]}

    monkeypatch.setitem(sys.modules, "db", FakeDb)

    payload = {
        "tool_name": "Bash",
        "tool_input": {
            "command": "curl -i https://example.com/config.php.bak",
        },
        "tool_response": (
            "HTTP/2 200\n"
            "OPENAI_API_KEY=sk-proj-redacted\n"
            "DB_PASSWORD=redacted"
        ),
    }

    followup_id = post_tool_use._ensure_webroot_backup_rotation_followup(payload, "sid-webroot")

    assert followup_id.startswith("NF-SECURITY-WEBROOT-BACKUP-ROTATE-")
    assert created
    assert created[0][1]["priority"] == "critical"
    assert "404/403" in created[0][1]["verification"]


def test_post_tool_use_clears_pending_after_change_log(monkeypatch):
    from hooks import post_tool_use

    monkeypatch.setattr(post_tool_use, "_production_closeout_dir", lambda: Path(post_tool_use.os.environ["NEXO_HOME"]) / "runtime" / "operations" / "protocol-closeout")
    post_tool_use.check_production_change_log_closeout(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "npm publish"},
        },
        "nexo-test-prod-clear",
    )
    message = post_tool_use.check_production_change_log_closeout(
        {"tool_name": "nexo_change_log", "tool_input": {"files": "src/server.py"}},
        "nexo-test-prod-clear",
    )

    assert message is None


def test_post_tool_use_queues_change_log_from_task_close(monkeypatch):
    from hooks import post_tool_use

    calls = []

    class FakeQueue:
        @staticmethod
        def enqueue_write(kind, payload, priority="normal"):
            calls.append((kind, payload, priority))
            return {"accepted": True, "writeId": "w1"}

    monkeypatch.setattr(post_tool_use, "_production_closeout_dir", lambda: Path(post_tool_use.os.environ["NEXO_HOME"]) / "runtime" / "operations" / "protocol-closeout")
    monkeypatch.setitem(sys.modules, "mcp_write_queue", FakeQueue)
    post_tool_use.check_production_change_log_closeout(
        {"tool_name": "Bash", "tool_input": {"command": "gcloud builds submit ."}},
        "nexo-test-prod-task-close",
    )
    message = post_tool_use.check_production_change_log_closeout(
        {
            "tool_name": "nexo_task_close",
            "tool_input": {
                "files_changed": "src/server.py",
                "change_summary": "Deploy release",
                "change_why": "Publish verified fix",
                "change_verify": "curl público HTTP 200",
            },
        },
        "nexo-test-prod-task-close",
    )

    assert message is None
    assert calls[0][0] == "change_log"
    assert calls[0][1]["files"] == "src/server.py"
