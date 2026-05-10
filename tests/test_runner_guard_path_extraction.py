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


def test_trailing_slash_is_stripped_from_directory_paths(runner_module, tmp_path):
    """Bug 2026-05-10 (followup-runner v8): the prompt mentioned ``/tmp/`` as
    a working directory. The extractor used to keep the literal ``/tmp/``
    and pass it to ``handle_guard_check`` → ``open('/tmp/')`` →
    ``IsADirectoryError`` not in the except list → the entire pre-emptive
    guard crashed and the runner aborted every hourly run with
    ``Runner guard unavailable: [Errno 21] Is a directory: '/tmp/'``.

    The fix has two layers: the extractor strips the trailing slash
    (so the guard never sees ``/tmp/`` again), and ``handle_guard_check``
    silently skips paths that resolve to a real directory on disk (so even
    a bare ``/tmp`` does not crash the schema scan). This test pins the
    extractor side: the literal ``/tmp/`` form must NOT survive into the
    guard's file list.
    """
    prompt = "Save the body to /tmp/ and reply via the helper.\n"
    paths = runner_module._extract_runner_guard_paths(prompt, tmp_path)
    assert "/tmp/" not in paths, "trailing-slash form must be normalised away"


def test_runner_guard_is_advisory_never_blocks(runner_module, tmp_path, monkeypatch):
    """7.17.0 contract: ``_run_headless_runner_guard`` is observational, not
    enforcing. Even when ``handle_guard_check`` returns blocking rules
    (runtime-core, file-conditioned learnings, anything), the pre-emptive
    guard reports ``blocked=False`` and lets the run start. The
    PreToolUse hook is the authoritative gate at write time; the
    pre-emptive layer's heuristic over prompt text must not abort runs.

    Without this contract, every email-monitor / followup-runner /
    Deep Sleep synth / postmortem-consolidation cycle was vulnerable to
    aborting with exit 2 whenever any learning's ``applies_to`` happened
    to match a path the prompt mentioned in passing.
    """
    def fake_handle_guard_check(**kwargs):
        return (
            "BLOCKING RULES (resolve BEFORE writing):\n"
            "  #99 [FILE RULE:/some/path]: A made-up conditioned learning that\n"
            "      would have aborted the runner under the pre-7.17.0 contract.\n"
        )

    fake_module = type("M", (), {"handle_guard_check": fake_handle_guard_check})
    monkeypatch.setitem(sys.modules, "plugins.guard", fake_module)

    result = runner_module._run_headless_runner_guard(
        caller="email-monitor",
        cwd=tmp_path,
        prompt="Edit /some/path to update something.\n",
        allowed_tools="Bash,Edit,Write",
    )
    assert result["blocked"] is False, "advisory contract: pre-emptive guard must never block"
    assert result.get("advisory") is True
    assert "BLOCKING RULES" in (result.get("summary") or ""), (
        "the advisory summary must still surface the blocking rules text for observability"
    )


def test_runner_guard_is_advisory_even_when_unavailable(runner_module, tmp_path, monkeypatch):
    """If ``handle_guard_check`` raises (eg. DB locked, disk full, an
    unhandled OSError), the pre-emptive guard MUST still let the run start.
    The PreToolUse hook is what enforces protection at write time.
    """
    def boom(**kwargs):
        raise RuntimeError("simulated guard failure (eg. db locked)")

    fake_module = type("M", (), {"handle_guard_check": boom})
    monkeypatch.setitem(sys.modules, "plugins.guard", fake_module)

    result = runner_module._run_headless_runner_guard(
        caller="email-monitor",
        cwd=tmp_path,
        prompt="Edit /some/path to update something.\n",
        allowed_tools="Bash,Edit,Write",
    )
    assert result["blocked"] is False
    assert "Runner guard unavailable" in (result.get("summary") or "")


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
