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
    monkeypatch.setattr(post_tool_use, "_queue_change_log_from_production_mutation", lambda payload, sid: False)
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
        "git push origin main",
        "firebase deploy --project prod",
        "shopify theme push --store germany-vic-shop --theme 123",
        "gh pr merge 42 --merge",
        "az vm create --resource-group rg --name vm1",
        "git push origin --tags",
        "gh release create v7.38.0 dist/app.dmg",
        "rsync -av index.php root@cl105e:/home/site/public_html/index.php",
        "rsync -av app/ vicshop:/home/nexodesk/app/",
        "ssh vicshop 'sed -i s/foo/bar/ /home/site/httpdocs/index.php'",
        "ssh vicshop 'tee /home/nexodesk/app/.env >/dev/null'",
        "gcloud run deploy app --image europe-west1-docker.pkg.dev/proj/app:latest",
        "gcloud dns record-sets transaction execute --zone prod-zone",
        "python manage.py migrate --settings=prod",
        "whmapi1 createacct username=test domain=example.com",
        "curl -X PATCH https://api.cloudflare.com/client/v4/zones/z/dns_records/r",
    ]

    assert all(post_tool_use._is_production_mutation_command(cmd) for cmd in commands)


def test_post_tool_use_domain_error_cascade_prompts_parallel_subagents(monkeypatch, tmp_path):
    from hooks import post_tool_use

    monkeypatch.setattr(post_tool_use, "_production_closeout_dir", lambda: tmp_path)
    sid = "nexo-test-cascade"
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "gcloud builds describe build-1"},
        "tool_response": "ERROR: ResourceExhausted: quota exceeded for cloudbuild.googleapis.com",
    }

    first = post_tool_use.check_domain_error_cascade(payload, sid, now=1000.0)
    second = post_tool_use.check_domain_error_cascade(payload, sid, now=1005.0)

    assert first is None
    assert second is not None
    assert "Cascada detectada" in second
    assert "subagentes en paralelo" in second


def test_post_tool_use_second_support_ticket_in_72h_requires_parallel_sweep(monkeypatch, tmp_path):
    from hooks import post_tool_use

    monkeypatch.setattr(post_tool_use, "_production_closeout_dir", lambda: tmp_path)
    sid = "nexo-test-support-second-ticket"
    first_payload = {
        "tool_name": "nexo_support_ticket_create",
        "tool_input": {
            "subject": "Cloud credits provisioning failed for client A",
            "message": "client A cannot use credits after provisioning",
            "client_message_id": "client-a",
        },
    }
    second_payload = {
        "tool_name": "nexo_support_ticket_create",
        "tool_input": {
            "subject": "Cloud credits provisioning failed for client B",
            "message": "client B sees the same cloud credits provisioning failure",
            "client_message_id": "client-b",
        },
    }

    first = post_tool_use.check_support_ticket_second_failure_sweep(first_payload, sid, now=1000.0)
    second = post_tool_use.check_support_ticket_second_failure_sweep(second_payload, sid, now=1000.0 + 3600)

    assert first is None
    assert second is not None
    assert "Segundo fallo de cliente" in second
    assert "SK-SUPPORT-SECOND-TICKET-PARALLEL-SWEEP" in second


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
            "tool_input": {"command": "gcloud run deploy app --image image:v7.38.0"},
        },
        "nexo-test-prod-clear",
    )
    message = post_tool_use.check_production_change_log_closeout(
        {"tool_name": "nexo_change_log", "tool_input": {"files": "src/server.py"}},
        "nexo-test-prod-clear",
    )

    assert message is None


def test_post_tool_use_release_requires_production_change_log_refs(monkeypatch, tmp_path):
    from hooks import post_tool_use

    monkeypatch.setattr(post_tool_use, "_production_closeout_dir", lambda: tmp_path)
    monkeypatch.setattr(post_tool_use, "_queue_change_log_from_production_mutation", lambda payload, sid: False)
    sid = "nexo-test-release-refs"

    first = post_tool_use.check_production_change_log_closeout(
        {"tool_name": "Bash", "tool_input": {"command": "git push origin --tags"}},
        sid,
    )
    assert first is not None
    assert "scope=production" in first

    still_blocked = post_tool_use.check_production_change_log_closeout(
        {"tool_name": "nexo_change_log", "tool_input": {"files": "src/server.py", "scope": "local"}},
        sid,
    )
    assert still_blocked is not None
    assert (tmp_path / "change-log-required-nexo-test-release-refs.json").exists()

    cleared = post_tool_use.check_production_change_log_closeout(
        {
            "tool_name": "nexo_change_log",
            "tool_input": {
                "files": "src/server.py",
                "scope": "production",
                "commit_ref": "6998385ef506",
                "tag": "v7.38.0",
                "release_url": "https://github.com/acme/app/releases/tag/v7.38.0",
            },
        },
        sid,
    )
    assert cleared is None
    assert not (tmp_path / "change-log-required-nexo-test-release-refs.json").exists()


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
    monkeypatch.setattr(post_tool_use, "_queue_change_log_from_production_mutation", lambda payload, sid: False)
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


def test_post_tool_use_auto_queues_change_log_from_production_mutation(monkeypatch, tmp_path):
    from hooks import post_tool_use

    calls = []

    class FakeQueue:
        @staticmethod
        def enqueue_write(kind, payload, priority="normal"):
            calls.append((kind, payload, priority))
            return {"accepted": True, "writeId": "w-auto"}

    monkeypatch.setattr(post_tool_use, "_production_closeout_dir", lambda: tmp_path)
    monkeypatch.setitem(sys.modules, "mcp_write_queue", FakeQueue)

    message = post_tool_use.check_production_change_log_closeout(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "gcloud run deploy app --image image:v7.38.0"},
            "tool_response": "Deploying... Done. Service URL: https://app.example.com",
        },
        "nexo-test-auto-change-log",
    )

    assert message is None
    assert calls[0][0] == "change_log"
    assert calls[0][1]["session_id"] == "nexo-test-auto-change-log"
    assert "v7.38.0" in calls[0][1]["what_changed"]
    assert "gcloud run deploy" in calls[0][1]["why"]
    assert calls[0][2] == "high"
    assert not (tmp_path / "change-log-required-nexo-test-auto-change-log.json").exists()


def test_post_tool_use_keeps_pending_when_auto_change_log_queue_unavailable(monkeypatch, tmp_path):
    from hooks import post_tool_use

    monkeypatch.setattr(post_tool_use, "_production_closeout_dir", lambda: tmp_path)
    monkeypatch.setattr(post_tool_use, "_queue_change_log_from_production_mutation", lambda payload, sid: False)

    message = post_tool_use.check_production_change_log_closeout(
        {"tool_name": "Bash", "tool_input": {"command": "npm publish"}},
        "nexo-test-auto-change-log-down",
    )

    assert message is not None
    assert "nexo_change_log" in message
    assert (tmp_path / "change-log-required-nexo-test-auto-change-log-down.json").exists()


def test_post_tool_use_prompts_learning_after_bugfix_edit(monkeypatch, tmp_path):
    from hooks import post_tool_use

    monkeypatch.setattr(post_tool_use, "_production_closeout_dir", lambda: tmp_path)
    sid = "nexo-test-learning-capture"
    message = post_tool_use.check_learning_capture_closeout(
        {
            "tool_name": "Edit",
            "tool_input": {
                "file_path": "/Users/franciscoc/Documents/_PhpstormProjects/nexo/src/plugins/protocol.py",
                "old_string": "return False",
                "new_string": "return True  # fix regression",
            },
        },
        sid,
    )

    assert message is not None
    assert "nexo_learning_add" in message
    assert (tmp_path / f"learning-required-{sid}.json").exists()


def test_post_tool_use_blocks_task_close_until_learning_trace(monkeypatch, tmp_path):
    from hooks import post_tool_use

    monkeypatch.setattr(post_tool_use, "_production_closeout_dir", lambda: tmp_path)
    sid = "nexo-test-learning-close"
    file_path = "/Users/franciscoc/Documents/_PhpstormProjects/nexo/src/plugins/protocol.py"
    post_tool_use.check_learning_capture_closeout(
        {
            "tool_name": "Edit",
            "tool_input": {
                "file_path": file_path,
                "old_string": "bug",
                "new_string": "fixed bug",
            },
        },
        sid,
    )

    blocked = post_tool_use.check_learning_capture_closeout(
        {
            "tool_name": "nexo_task_close",
            "tool_input": {
                "files_changed": file_path,
                "change_summary": "Fix protocol regression",
                "evidence": "pytest ok",
            },
        },
        sid,
    )
    allowed = post_tool_use.check_learning_capture_closeout(
        {
            "tool_name": "nexo_task_close",
            "tool_input": {
                "files_changed": file_path,
                "change_summary": "Fix protocol regression",
                "learning_title": "Capture protocol bugfix learnings",
                "learning_content": "Medium-impact edits that fix regressions need a reusable learning.",
            },
        },
        sid,
    )

    assert blocked is not None
    assert "learning_title" in blocked
    assert allowed is None
    assert not (tmp_path / f"learning-required-{sid}.json").exists()


def test_post_tool_use_auto_queues_guard_check_after_edit(monkeypatch):
    from hooks import post_tool_use

    calls = []

    class FakeQueue:
        @staticmethod
        def enqueue_write(kind, payload, priority="normal", wait=False, timeout_ms=3000):
            calls.append((kind, payload, priority, wait, timeout_ms))
            return {"accepted": True, "status": "committed", "writeId": "w-guard"}

    monkeypatch.setitem(sys.modules, "mcp_write_queue", FakeQueue)
    monkeypatch.setattr(post_tool_use, "_resolve_area_from_atlas", lambda files: "nexo")
    monkeypatch.setattr(post_tool_use, "_recent_guard_check_exists", lambda sid, files, area, now=None: False)

    message = post_tool_use._queue_auto_guard_check(
        {
            "tool_name": "Edit",
            "tool_input": {
                "file_path": "/Users/franciscoc/Documents/_PhpstormProjects/nexo/src/server.py",
                "old_string": "old",
                "new_string": "new",
            },
        },
        "nexo-test-auto-guard",
    )

    assert message is None
    assert calls[0][0] == "guard_check"
    assert calls[0][1]["files"].endswith("/src/server.py")
    assert calls[0][1]["area"] == "nexo"
    assert calls[0][2] == "high"
    assert calls[0][3] is True


def test_post_tool_use_auto_guard_respects_recent_area_check(monkeypatch):
    from hooks import post_tool_use

    calls = []

    class FakeQueue:
        @staticmethod
        def enqueue_write(*args, **kwargs):
            calls.append((args, kwargs))
            return {"accepted": True, "status": "committed"}

    monkeypatch.setitem(sys.modules, "mcp_write_queue", FakeQueue)
    monkeypatch.setattr(post_tool_use, "_resolve_area_from_atlas", lambda files: "nexo")
    monkeypatch.setattr(post_tool_use, "_recent_guard_check_exists", lambda sid, files, area, now=None: True)

    message = post_tool_use._queue_auto_guard_check(
        {
            "tool_name": "Write",
            "tool_input": {"file_path": "/Users/franciscoc/Documents/_PhpstormProjects/nexo/src/server.py"},
        },
        "nexo-test-auto-guard-recent",
    )

    assert message is None
    assert calls == []


def test_post_tool_use_auto_guard_records_debt_on_unknown_tool(monkeypatch):
    from hooks import post_tool_use

    debts = []

    class FakeQueue:
        @staticmethod
        def enqueue_write(kind, payload, priority="normal", wait=False, timeout_ms=3000):
            return {"accepted": True, "status": "dead_letter", "last_error": "Unknown tool: nexo_guard_check"}

    class FakeDb:
        @staticmethod
        def create_protocol_debt(session_id, debt_type, *, severity="warn", task_id="", evidence=""):
            debts.append((session_id, debt_type, severity, evidence))
            return {"id": 1}

    monkeypatch.setitem(sys.modules, "mcp_write_queue", FakeQueue)
    monkeypatch.setitem(sys.modules, "db", FakeDb)
    monkeypatch.setattr(post_tool_use, "_resolve_area_from_atlas", lambda files: "nexo")
    monkeypatch.setattr(post_tool_use, "_recent_guard_check_exists", lambda sid, files, area, now=None: False)

    message = post_tool_use._queue_auto_guard_check(
        {
            "tool_name": "Edit",
            "tool_input": {
                "file_path": "/Users/franciscoc/Documents/_PhpstormProjects/nexo/src/server.py",
                "old_string": "old",
                "new_string": "new",
            },
        },
        "nexo-test-auto-guard-unknown",
    )

    assert message is not None
    assert debts[0][0] == "nexo-test-auto-guard-unknown"
    assert debts[0][1] == "auto_guard_check_failed"
    assert debts[0][2] == "error"
    assert "Unknown tool" in debts[0][3]


def test_post_tool_use_blocks_task_close_when_trace_missing_guard(monkeypatch):
    from hooks import post_tool_use

    monkeypatch.setattr(post_tool_use, "_production_closeout_dir", lambda: Path(post_tool_use.os.environ["NEXO_HOME"]) / "runtime" / "operations" / "protocol-closeout")
    sid = "nexo-trace-missing-guard"
    post_tool_use.check_post_change_trace_closeout(
        {
            "tool_name": "Edit",
            "tool_input": {
                "file_path": "/Users/franciscoc/Documents/_PhpstormProjects/nexo/src/server.py",
                "old_string": "old",
                "new_string": "new",
            },
        },
        sid,
    )

    message = post_tool_use.check_post_change_trace_closeout(
        {
            "tool_name": "nexo_task_close",
            "tool_input": {
                "files_changed": "/Users/franciscoc/Documents/_PhpstormProjects/nexo/src/server.py",
                "change_summary": "Cambio verificado",
                "change_why": "Prueba",
            },
        },
        sid,
    )

    assert message is not None
    assert "guardias ejecutados" in message


def test_post_tool_use_allows_task_close_with_guard_and_change_trace(monkeypatch):
    from hooks import post_tool_use

    monkeypatch.setattr(post_tool_use, "_production_closeout_dir", lambda: Path(post_tool_use.os.environ["NEXO_HOME"]) / "runtime" / "operations" / "protocol-closeout")
    sid = "nexo-trace-complete"
    file_path = "/Users/franciscoc/Documents/_PhpstormProjects/nexo/src/server.py"
    post_tool_use.check_post_change_trace_closeout(
        {"tool_name": "nexo_guard_check", "tool_input": {"files": file_path}},
        sid,
    )
    post_tool_use.check_post_change_trace_closeout(
        {"tool_name": "Edit", "tool_input": {"file_path": file_path, "old_string": "old", "new_string": "new"}},
        sid,
    )
    post_tool_use.check_post_change_trace_closeout(
        {"tool_name": "nexo_change_log", "tool_input": {"files": file_path}},
        sid,
    )

    message = post_tool_use.check_post_change_trace_closeout(
        {
            "tool_name": "nexo_task_close",
            "tool_input": {
                "files_changed": file_path,
                "change_summary": "Cambio verificado",
                "change_why": "Prueba",
                "change_verify": "pytest ok",
            },
        },
        sid,
    )

    assert message is None


def test_post_tool_use_warns_shared_mutation_without_scope_checklist():
    from hooks import post_tool_use

    message = post_tool_use.check_shared_scope_closeout(
        {
            "tool_name": "Edit",
            "tool_input": {
                "file_path": "/Users/franciscoc/Documents/_PhpstormProjects/nexo/src/plugins/protocol.py",
                "old_string": "old",
                "new_string": "new",
            },
        }
    )

    assert message is not None
    assert "conversación afectada" in message
    assert "tenant/tienda" in message
    assert "estado de deploy" in message


def test_post_tool_use_accepts_shared_mutation_with_scope_checklist():
    from hooks import post_tool_use

    message = post_tool_use.check_shared_scope_closeout(
        {
            "tool_name": "Edit",
            "tool_input": {
                "file_path": "/Users/franciscoc/Documents/_PhpstormProjects/nexo/src/plugins/protocol.py",
                "scope_context": (
                    "conversación: NF-DS-ED3253EC; tenant/tienda: N/A; "
                    "idiomas: es; entorno: producto fuente; superficie: API/UI; "
                    "deploy: no publicado todavía"
                ),
                "old_string": "old",
                "new_string": "new",
            },
        }
    )

    assert message is None
