"""Tests for ``_extract_runner_guard_paths`` in ``src/agent_runner.py``.

Bug 2026-05-10: every email forwarded by Francisco landed at
``status='needs_interactive'`` because the runner pre-emptive guard saw
``/Users/.../core/scripts/nexo-send-reply.py`` mentioned in the email-monitor
prompt — that path is the reply tool the agent is told to invoke — and
fired the ``runtime-core`` blocking rule. The session aborted with exit 2
before the agent could draft a reply, so no email was ever delivered. Root
cause: the path extractor treated *any* absolute path appearing in the
prompt as an edit target, including the path that came right after a
``python3`` interpreter (a subprocess execution, not an edit).

These tests pin the new behaviour: paths that appear immediately after a
known interpreter must be excluded from the guard list. Mentions of the
same path in *non*-execution context (e.g. an "Edit X" instruction) must
still be captured.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


SRC = Path(__file__).resolve().parent.parent / "src" / "agent_runner.py"


@pytest.fixture
def runner_module(monkeypatch):
    src_dir = SRC.parent
    if str(src_dir) not in sys.path:
        monkeypatch.syspath_prepend(str(src_dir))
    spec = importlib.util.spec_from_file_location("agent_runner_under_test", str(SRC))
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        pytest.skip(f"agent_runner.py could not load in test env: {exc}")
    return module


def test_path_after_python_is_excluded(runner_module, tmp_path):
    prompt = (
        "Reply through the runtime helper:\n"
        "/Users/franciscoc/.nexo/runtime/python/bin/python3 "
        "/Users/franciscoc/.nexo/core/scripts/nexo-send-reply.py "
        "--to client@example.com --subject 'Re: x' --body-file /tmp/r.txt\n"
    )
    paths = runner_module._extract_runner_guard_paths(prompt, tmp_path)
    assert "/Users/franciscoc/.nexo/core/scripts/nexo-send-reply.py" not in paths, (
        "the script that follows `python3` is invoked, not edited — must NOT trip the guard"
    )


def test_send_reply_body_file_after_flag_is_still_captured(runner_module, tmp_path):
    prompt = (
        "/Users/franciscoc/.nexo/runtime/python/bin/python3 "
        "/Users/franciscoc/.nexo/core/scripts/nexo-send-reply.py "
        "--to x --body-file /tmp/nexo-reply.txt\n"
    )
    paths = runner_module._extract_runner_guard_paths(prompt, tmp_path)
    assert "/tmp/nexo-reply.txt" in paths, (
        "the body file (which the agent does need to write) must still go through the guard"
    )


def test_path_after_node_is_excluded(runner_module, tmp_path):
    prompt = "node /opt/myapp/cli.js --check\n"
    paths = runner_module._extract_runner_guard_paths(prompt, tmp_path)
    assert "/opt/myapp/cli.js" not in paths


def test_path_after_npx_is_excluded(runner_module, tmp_path):
    prompt = "npx /opt/tools/run.js arg1\n"
    paths = runner_module._extract_runner_guard_paths(prompt, tmp_path)
    assert "/opt/tools/run.js" not in paths


def test_path_in_edit_instruction_is_kept(runner_module, tmp_path):
    prompt = "Open /Users/franciscoc/repo/src/feature.py and add a validation step.\n"
    paths = runner_module._extract_runner_guard_paths(prompt, tmp_path)
    assert "/Users/franciscoc/repo/src/feature.py" in paths


def test_mixed_exec_and_edit_only_keeps_edit(runner_module, tmp_path):
    prompt = (
        "First, edit /Users/franciscoc/repo/src/feature.py to add the new flag.\n"
        "Then run python3 /Users/franciscoc/.nexo/core/scripts/nexo-send-reply.py "
        "--to x --body-file /tmp/r.txt\n"
    )
    paths = runner_module._extract_runner_guard_paths(prompt, tmp_path)
    assert "/Users/franciscoc/repo/src/feature.py" in paths
    assert "/Users/franciscoc/.nexo/core/scripts/nexo-send-reply.py" not in paths
    assert "/tmp/r.txt" in paths


def test_email_monitor_template_does_not_trigger_runtime_core(runner_module, tmp_path):
    """Reproducción directa del bug del 2026-05-10: prompt del email-monitor
    rendereado debe NO meter ``nexo-send-reply.py`` en la lista de paths del
    guard, para que la regla ``runtime-core`` no aborte la sesión.
    """
    prompt = (
        "== SEND VIA `nexo-send-reply.py` ==\n"
        "/Users/franciscoc/.nexo/runtime/python/bin/python3 "
        "/Users/franciscoc/.nexo/core/scripts/nexo-send-reply.py "
        "--to maria@canarirural.com --subject 'Re: Pendientes' "
        "--in-reply-to '<msgid>' --body-file /tmp/nexo-reply.txt "
        "--quote-file /tmp/nexo-quote.txt --thread-file /tmp/nexo-thread.txt\n"
    )
    paths = runner_module._extract_runner_guard_paths(prompt, tmp_path)
    runtime_core_hits = [p for p in paths if "/.nexo/core/" in p]
    assert runtime_core_hits == [], (
        f"runtime-core paths leaked into the guard list: {runtime_core_hits}"
    )
