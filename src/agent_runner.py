from __future__ import annotations

"""Terminal client launchers and headless automation backend runner."""

import json
import os
import shlex
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib

from client_preferences import (
    BACKEND_NONE,
    CLIENT_CLAUDE_CODE,
    CLIENT_CODEX,
    TERMINAL_CLIENT_KEYS,
    load_client_preferences,
    normalize_client_key,
    resolve_automation_backend,
    resolve_automation_task_profile,
    resolve_client_runtime_profile,
    resolve_terminal_client,
)


NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
CLAUDE_LEGACY_MODEL_HINTS = {"opus", "sonnet"}
MODEL_PRICING_USD_PER_1M = {
    # Pricing snapshot used only when the backend does not return explicit cost.
    # Codex model names map to the current GPT-5 family pricing.
    "gpt-5.4": {"input": 1.25, "cached_input": 0.125, "output": 10.0},
    "gpt-5.4-mini": {"input": 0.25, "cached_input": 0.025, "output": 2.0},
}
INTERACTIVE_STARTUP_PROMPT = (
    "Start as NEXO for this session now. Use the managed bootstrap already installed "
    "for this client, run nexo_startup and nexo_heartbeat for this first turn, then "
    "reply with one concise startup status in the user's language."
)


class AgentRunnerError(RuntimeError):
    """Base exception for runner failures."""


class TerminalClientUnavailableError(AgentRunnerError):
    """Raised when the requested interactive client cannot be launched."""


class AutomationBackendUnavailableError(AgentRunnerError):
    """Raised when the configured automation backend is unavailable."""


def _canonical_pricing_model(model: str) -> str:
    lowered = str(model or "").strip().lower()
    lowered = lowered.split("[", 1)[0]
    aliases = {
        "gpt-5": "gpt-5.4",
        "gpt-5.4": "gpt-5.4",
        "gpt-5-mini": "gpt-5.4-mini",
        "gpt-5.4-mini": "gpt-5.4-mini",
    }
    return aliases.get(lowered, lowered)


def _estimate_openai_cost_usd(model: str, *, input_tokens: int, cached_input_tokens: int, output_tokens: int) -> tuple[float | None, str]:
    pricing = MODEL_PRICING_USD_PER_1M.get(_canonical_pricing_model(model))
    if not pricing:
        return None, "pricing_unavailable"
    total = 0.0
    total += (max(0, int(input_tokens or 0)) / 1_000_000.0) * pricing["input"]
    total += (max(0, int(cached_input_tokens or 0)) / 1_000_000.0) * pricing["cached_input"]
    total += (max(0, int(output_tokens or 0)) / 1_000_000.0) * pricing["output"]
    return round(total, 6), "pricing_snapshot"


def _safe_json_loads(raw: str) -> dict | list | None:
    try:
        return json.loads(raw)
    except Exception:
        return None


def _extract_claude_telemetry(raw_stdout: str, *, requested_output_format: str) -> tuple[str, dict]:
    payload = _safe_json_loads(raw_stdout) if str(raw_stdout or "").strip().startswith("{") else None
    if not isinstance(payload, dict):
        return raw_stdout or "", {
            "telemetry_source": "missing",
            "cost_source": "missing",
            "usage": {},
            "warnings": ["backend did not return parseable JSON telemetry"],
        }

    result_payload = payload.get("result", "")
    if requested_output_format and requested_output_format.lower() == "json" and not isinstance(result_payload, str):
        final_stdout = json.dumps(result_payload, ensure_ascii=False)
    else:
        final_stdout = result_payload if isinstance(result_payload, str) else json.dumps(result_payload, ensure_ascii=False)

    usage = payload.get("usage") or {}
    model_usage = payload.get("modelUsage") or {}
    explicit_cost = payload.get("total_cost_usd")
    if explicit_cost is None and isinstance(model_usage, dict):
        explicit_cost = sum(
            float((item or {}).get("costUSD") or 0.0)
            for item in model_usage.values()
            if isinstance(item, dict)
        )

    return final_stdout, {
        "telemetry_source": "claude_json",
        "cost_source": "backend",
        "usage": {
            "input_tokens": int(usage.get("input_tokens") or 0),
            "cached_input_tokens": int(usage.get("cache_read_input_tokens") or 0),
            "output_tokens": int(usage.get("output_tokens") or 0),
        },
        "total_cost_usd": float(explicit_cost) if explicit_cost is not None else None,
        "raw": payload,
        "warnings": [],
    }


def _extract_codex_telemetry(stream_stdout: str, *, final_stdout: str, model: str) -> tuple[str, dict]:
    usage_payload: dict = {}
    raw_events: list[dict] = []
    for line in str(stream_stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        payload = _safe_json_loads(line)
        if not isinstance(payload, dict):
            continue
        raw_events.append(payload)
        if payload.get("type") == "turn.completed" and isinstance(payload.get("usage"), dict):
            usage_payload = payload["usage"]

    usage = {
        "input_tokens": int(usage_payload.get("input_tokens") or 0),
        "cached_input_tokens": int(usage_payload.get("cached_input_tokens") or 0),
        "output_tokens": int(usage_payload.get("output_tokens") or 0),
    }
    total_cost_usd = usage_payload.get("total_cost_usd")
    cost_source = "backend" if total_cost_usd is not None else "missing"
    warnings: list[str] = []
    if total_cost_usd is None:
        estimated_cost, estimated_source = _estimate_openai_cost_usd(
            model,
            input_tokens=usage["input_tokens"],
            cached_input_tokens=usage["cached_input_tokens"],
            output_tokens=usage["output_tokens"],
        )
        total_cost_usd = estimated_cost
        cost_source = estimated_source
        if estimated_cost is None:
            warnings.append(f"no pricing snapshot available for model `{model}`")

    if not usage_payload:
        warnings.append("backend did not return usage telemetry")

    return final_stdout, {
        "telemetry_source": "codex_jsonl",
        "cost_source": cost_source,
        "usage": usage,
        "total_cost_usd": float(total_cost_usd) if total_cost_usd is not None else None,
        "raw": raw_events[-8:],
        "warnings": warnings,
    }


def _append_stderr(stderr: str, message: str) -> str:
    bits = [part for part in [str(stderr or "").rstrip(), str(message or "").strip()] if part]
    if not bits:
        return ""
    return "\n".join(bits) + "\n"


def _record_automation_run(
    *,
    backend: str,
    task_profile: str,
    model: str,
    reasoning_effort: str,
    cwd: Path,
    output_format: str,
    prompt: str,
    returncode: int,
    duration_ms: int,
    telemetry: dict,
) -> tuple[bool, str]:
    try:
        from db._core import get_db
    except Exception as exc:
        return False, f"automation telemetry unavailable: {exc}"

    try:
        conn = get_db()
        usage = telemetry.get("usage") or {}
        conn.execute(
            """
            INSERT INTO automation_runs (
                backend, task_profile, model, reasoning_effort, cwd, output_format,
                prompt_chars, returncode, duration_ms,
                input_tokens, cached_input_tokens, output_tokens,
                total_cost_usd, telemetry_source, cost_source, status, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                backend,
                task_profile or "default",
                model,
                reasoning_effort,
                str(cwd),
                output_format or "text",
                len(prompt or ""),
                int(returncode),
                int(duration_ms),
                int(usage.get("input_tokens") or 0),
                int(usage.get("cached_input_tokens") or 0),
                int(usage.get("output_tokens") or 0),
                telemetry.get("total_cost_usd"),
                telemetry.get("telemetry_source", ""),
                telemetry.get("cost_source", ""),
                "ok" if int(returncode) == 0 else "failed",
                json.dumps(
                    {
                        "warnings": telemetry.get("warnings") or [],
                        "raw": telemetry.get("raw") or {},
                    },
                    ensure_ascii=False,
                ),
            ),
        )
        conn.commit()
        return True, ""
    except Exception as exc:
        return False, f"automation telemetry unavailable: {exc}"


def _resolve_claude_cli() -> str:
    saved = NEXO_HOME / "config" / "claude-cli-path"
    if saved.exists():
        candidate = saved.read_text().strip()
        if candidate and Path(candidate).exists():
            return candidate
    env_path = os.environ.get("CLAUDE_BIN", "").strip()
    if env_path and Path(env_path).exists():
        return env_path
    discovered = shutil.which("claude")
    if discovered:
        return discovered
    for candidate in (
        Path.home() / ".local" / "bin" / "claude",
        Path.home() / ".npm-global" / "bin" / "claude",
        Path("/usr/local/bin/claude"),
    ):
        if candidate.exists():
            return str(candidate)
    return ""


def _resolve_codex_cli() -> str:
    env_path = os.environ.get("CODEX_BIN", "").strip()
    if env_path and Path(env_path).exists():
        return env_path
    return shutil.which("codex") or ""


def _codex_config_path() -> Path:
    return Path.home() / ".codex" / "config.toml"


def _headless_env(env: dict | None = None) -> dict:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    merged["NEXO_HEADLESS"] = "1"
    merged["NEXO_AUTOMATION"] = "1"
    merged.pop("CLAUDECODE", None)
    merged.pop("CLAUDE_CODE", None)
    return merged


def _load_client_bootstrap_prompt(client: str) -> str:
    try:
        from bootstrap_docs import load_bootstrap_prompt
    except Exception:
        return ""
    return load_bootstrap_prompt(client, nexo_home=NEXO_HOME, user_home=Path.home())


def _codex_managed_initial_messages_enabled() -> bool:
    config_path = _codex_config_path()
    if not config_path.is_file():
        return False
    try:
        payload = tomllib.loads(config_path.read_text())
    except Exception:
        return False
    return bool(
        payload.get("nexo", {})
        .get("codex", {})
        .get("bootstrap_managed")
    )


def _codex_initial_messages_config(prompt_text: str) -> str:
    return f'initial_messages=[{{role="system",content={json.dumps(prompt_text, ensure_ascii=False)}}}]'


def _codex_interactive_launch_flags() -> list[str]:
    return ["--sandbox", "danger-full-access", "--ask-for-approval", "never"]


def _interactive_startup_prompt(client: str) -> str:
    client_key = normalize_client_key(client)
    if client_key in {CLIENT_CLAUDE_CODE, CLIENT_CODEX}:
        return INTERACTIVE_STARTUP_PROMPT
    return ""


def _interactive_target_cwd(target: str | os.PathLike[str]) -> str:
    candidate = Path(target).expanduser()
    if candidate.exists() and candidate.is_file():
        candidate = candidate.parent
    try:
        resolved = candidate.resolve()
    except Exception:
        resolved = candidate
    if resolved.exists():
        return str(resolved)
    return str(Path.cwd())


def build_interactive_client_command(
    *,
    target: str | os.PathLike[str],
    client: str | None = None,
    preferences: dict | None = None,
) -> tuple[str, list[str]]:
    prefs = preferences or load_client_preferences()
    selected = resolve_terminal_client(client, preferences=prefs)
    target_path = str(Path(target).expanduser())
    profile = resolve_client_runtime_profile(selected, preferences=prefs)
    startup_prompt = _interactive_startup_prompt(selected)

    if selected == CLIENT_CLAUDE_CODE:
        claude_bin = _resolve_claude_cli()
        if not claude_bin:
            raise TerminalClientUnavailableError(
                "Claude Code launcher not found in PATH. Install `claude` first."
            )
        cmd = [claude_bin]
        if profile["model"]:
            cmd.extend(["--model", profile["model"]])
        if profile["reasoning_effort"]:
            cmd.extend(["--effort", profile["reasoning_effort"]])
        cmd.append("--dangerously-skip-permissions")
        if startup_prompt:
            cmd.append(startup_prompt)
        return selected, cmd

    if selected == CLIENT_CODEX:
        codex_bin = _resolve_codex_cli()
        if not codex_bin:
            raise TerminalClientUnavailableError(
                "Codex launcher not found in PATH. Install `codex` first or reconfigure NEXO."
            )
        cmd = [codex_bin, *_codex_interactive_launch_flags()]
        bootstrap_prompt = _load_client_bootstrap_prompt(CLIENT_CODEX)
        if bootstrap_prompt and not _codex_managed_initial_messages_enabled():
            cmd.extend(["-c", _codex_initial_messages_config(bootstrap_prompt)])
        if profile["model"]:
            cmd.extend(["-m", profile["model"]])
        if profile["reasoning_effort"]:
            cmd.extend(["-c", f'model_reasoning_effort="{profile["reasoning_effort"]}"'])
        cmd.extend(["-C", target_path])
        if startup_prompt:
            cmd.append(startup_prompt)
        return selected, cmd

    raise TerminalClientUnavailableError(f"Unsupported terminal client: {selected}")


def launch_interactive_client(
    *,
    target: str | os.PathLike[str],
    client: str | None = None,
    env: dict | None = None,
    preferences: dict | None = None,
) -> subprocess.CompletedProcess:
    _, cmd = build_interactive_client_command(target=target, client=client, preferences=preferences)
    launch_env = os.environ.copy()
    if env:
        launch_env.update(env)
    return subprocess.run(cmd, env=launch_env, cwd=_interactive_target_cwd(target))


def build_followup_terminal_shell_command(
    followup_reference: str,
    *,
    client: str | None = None,
    preferences: dict | None = None,
    cwd: str | os.PathLike[str] | None = None,
) -> tuple[str, str]:
    prefs = preferences or load_client_preferences()
    selected = resolve_terminal_client(client, preferences=prefs)
    profile = resolve_client_runtime_profile(selected, preferences=prefs)
    prompt = f"NEXO: execute followup from file $(cat {followup_reference})"

    if selected == CLIENT_CLAUDE_CODE:
        claude_bin = _resolve_claude_cli()
        if not claude_bin:
            raise TerminalClientUnavailableError(
                "Claude Code launcher not found in PATH. Install `claude` first."
            )
        cmd = [claude_bin]
        if profile["model"]:
            cmd.extend(["--model", profile["model"]])
        if profile["reasoning_effort"]:
            cmd.extend(["--effort", profile["reasoning_effort"]])
        cmd.extend(["--dangerously-skip-permissions", prompt])
        return selected, shlex.join(cmd)

    if selected == CLIENT_CODEX:
        codex_bin = _resolve_codex_cli()
        if not codex_bin:
            raise TerminalClientUnavailableError(
                "Codex launcher not found in PATH. Install `codex` first or reconfigure NEXO."
            )
        target_cwd = str(Path(cwd).expanduser()) if cwd else str(Path.home())
        cmd = [codex_bin, *_codex_interactive_launch_flags()]
        bootstrap_prompt = _load_client_bootstrap_prompt(CLIENT_CODEX)
        if bootstrap_prompt and not _codex_managed_initial_messages_enabled():
            cmd.extend(["-c", _codex_initial_messages_config(bootstrap_prompt)])
        if profile["model"]:
            cmd.extend(["-m", profile["model"]])
        if profile["reasoning_effort"]:
            cmd.extend(["-c", f'model_reasoning_effort="{profile["reasoning_effort"]}"'])
        cmd.extend(["-C", target_cwd, prompt])
        return selected, shlex.join(cmd)

    raise TerminalClientUnavailableError(f"Unsupported terminal client: {selected}")


def _resolve_runtime_model_and_effort(
    client: str,
    *,
    model: str | None = None,
    reasoning_effort: str | None = None,
    preferences: dict | None = None,
) -> tuple[str, str]:
    profile = resolve_client_runtime_profile(client, preferences=preferences)
    requested_model = str(model or "").strip()
    requested_effort = str(reasoning_effort or "").strip().lower()

    if client == CLIENT_CODEX:
        if not requested_model or requested_model.lower() in CLAUDE_LEGACY_MODEL_HINTS:
            requested_model = profile["model"]
    elif client == CLIENT_CLAUDE_CODE:
        if not requested_model or requested_model.lower() in CLAUDE_LEGACY_MODEL_HINTS:
            requested_model = profile["model"]
    elif not requested_model:
        requested_model = profile["model"]

    if not requested_effort:
        requested_effort = profile["reasoning_effort"]

    return requested_model, requested_effort


def _backend_is_available(backend: str) -> bool:
    if backend == CLIENT_CLAUDE_CODE:
        return bool(_resolve_claude_cli())
    if backend == CLIENT_CODEX:
        return bool(_resolve_codex_cli())
    return False


def _resolve_available_backend(selected_backend: str, *, preferences: dict | None = None) -> str:
    if _backend_is_available(selected_backend):
        return selected_backend
    prefs = preferences or load_client_preferences()
    preferred = resolve_automation_backend(preferences=prefs)
    for candidate in (preferred, CLIENT_CLAUDE_CODE, CLIENT_CODEX):
        if candidate == selected_backend or candidate == BACKEND_NONE:
            continue
        if _backend_is_available(candidate):
            return candidate
    return selected_backend


def _build_codex_prompt(
    prompt: str,
    *,
    output_format: str = "",
    append_system_prompt: str = "",
    allowed_tools: str = "",
) -> str:
    protocol_contract = (
        "NEXO PROTOCOL (MANDATORY):\n"
        "- Before non-trivial analyze/edit/execute/delegate work, call `nexo_task_open(...)`. "
        "If that tool is unavailable, call `nexo_guard_check(...)` and `nexo_cortex_check(...)` first.\n"
        "- For long multi-step or cross-session work, call `nexo_workflow_open(...)` and keep it updated with "
        "`nexo_workflow_update(...)` so resume/replay use durable state instead of guesswork.\n"
        "- Before diagnosing NEXO, explicitly fix the plane first: `product_public`, `runtime_personal`, `installation_live`, `database_real`, or `cooperator`. "
        "Do not mix planes inside the same diagnosis.\n"
        "- If a target file has conditioned learnings or blocking guard rules, review them before any read/edit/delete step, and acknowledge guard before any edit/delete step.\n"
        "- Do not claim done without explicit verification evidence. Close with `nexo_task_close(...)`; if unavailable, capture the change log and state the evidence explicitly.\n"
        "- When a correction changes the canonical rule, capture or supersede the learning instead of leaving contradictory active rules behind."
    )
    instructions: list[str] = []
    instructions.append(protocol_contract)
    if append_system_prompt:
        instructions.append(f"SYSTEM INSTRUCTIONS:\n{append_system_prompt}")
    if output_format and output_format.lower() == "text":
        instructions.append("FINAL RESPONSE FORMAT: plain text only.")
    elif output_format:
        instructions.append(f"FINAL RESPONSE FORMAT: {output_format}.")
    if allowed_tools:
        instructions.append(
            "TOOLING SCOPE: Prefer to stay within capabilities equivalent to "
            f"{allowed_tools} unless that would make the task fail."
        )
    if instructions:
        return "\n\n".join([*instructions, prompt])
    return prompt


def run_automation_prompt(
    prompt: str,
    *,
    backend: str | None = None,
    task_profile: str = "",
    cwd: str | os.PathLike[str] | None = None,
    env: dict | None = None,
    model: str = "",
    reasoning_effort: str = "",
    timeout: int = 300,
    output_format: str = "",
    append_system_prompt: str = "",
    allowed_tools: str = "",
    extra_args: list[str] | tuple[str, ...] | None = None,
) -> subprocess.CompletedProcess:
    prefs = load_client_preferences()
    selected_backend = backend or resolve_automation_backend(preferences=prefs)
    if selected_backend == BACKEND_NONE:
        raise AutomationBackendUnavailableError("Automation backend is disabled in config.")

    if task_profile:
        profile = resolve_automation_task_profile(task_profile, preferences=prefs)
        selected_backend = profile["backend"] or selected_backend
        if not model:
            model = profile["model"]
        if not reasoning_effort:
            reasoning_effort = profile["reasoning_effort"]
    selected_backend = _resolve_available_backend(selected_backend, preferences=prefs)

    cwd_path = Path(cwd).expanduser().resolve() if cwd else Path.cwd()
    run_env = _headless_env(env)
    extra_args = list(extra_args or [])
    requested_output_format = output_format or "text"
    resolved_model, resolved_effort = _resolve_runtime_model_and_effort(
        selected_backend,
        model=model,
        reasoning_effort=reasoning_effort,
        preferences=prefs,
    )
    started_at = time.perf_counter()

    if selected_backend == CLIENT_CLAUDE_CODE:
        claude_bin = _resolve_claude_cli()
        if not claude_bin:
            raise AutomationBackendUnavailableError(
                "Claude Code automation backend selected but `claude` is not installed."
            )
        cmd = [claude_bin, "-p", prompt]
        if resolved_model:
            cmd.extend(["--model", resolved_model])
        if resolved_effort:
            cmd.extend(["--effort", resolved_effort])
        cmd.extend(["--output-format", "json"])
        if append_system_prompt:
            cmd.extend(["--append-system-prompt", append_system_prompt])
        if allowed_tools:
            cmd.extend(["--allowedTools", allowed_tools])
        cmd.extend(extra_args)
        result = subprocess.run(
            cmd,
            cwd=str(cwd_path),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=run_env,
        )
        final_stdout, telemetry = _extract_claude_telemetry(
            result.stdout or "",
            requested_output_format=requested_output_format,
        )
        recorded, record_error = _record_automation_run(
            backend=selected_backend,
            task_profile=task_profile,
            model=resolved_model,
            reasoning_effort=resolved_effort,
            cwd=cwd_path,
            output_format=requested_output_format,
            prompt=prompt,
            returncode=result.returncode,
            duration_ms=int((time.perf_counter() - started_at) * 1000),
            telemetry=telemetry,
        )
        stderr = result.stderr or ""
        if not recorded:
            stderr = _append_stderr(stderr, record_error)
        return subprocess.CompletedProcess(
            cmd,
            result.returncode,
            final_stdout,
            stderr,
        )

    if selected_backend == CLIENT_CODEX:
        codex_bin = _resolve_codex_cli()
        if not codex_bin:
            raise AutomationBackendUnavailableError(
                "Codex automation backend selected but `codex` is not installed."
            )
        with tempfile.TemporaryDirectory(prefix="nexo-codex-") as tmpdir:
            output_path = Path(tmpdir) / "last-message.txt"
            cmd = [
                codex_bin,
                "exec",
                "--skip-git-repo-check",
                "--dangerously-bypass-approvals-and-sandbox",
                "--ephemeral",
                "--json",
                "-C",
                str(cwd_path),
                "-o",
                str(output_path),
            ]
            bootstrap_prompt = _load_client_bootstrap_prompt(CLIENT_CODEX)
            if bootstrap_prompt and not _codex_managed_initial_messages_enabled():
                cmd.extend(["-c", _codex_initial_messages_config(bootstrap_prompt)])
            if resolved_model:
                cmd.extend(["-m", resolved_model])
            if resolved_effort:
                cmd.extend(["-c", f'model_reasoning_effort="{resolved_effort}"'])
            cmd.extend(extra_args)
            cmd.append(
                _build_codex_prompt(
                    prompt,
                    output_format=output_format,
                    append_system_prompt=append_system_prompt,
                    allowed_tools=allowed_tools,
                )
            )
            result = subprocess.run(
                cmd,
                cwd=str(cwd_path),
                capture_output=True,
                text=True,
                timeout=timeout,
                env=run_env,
            )
            raw_stdout = result.stdout or ""
            stdout = output_path.read_text() if output_path.exists() else raw_stdout
            final_stdout, telemetry = _extract_codex_telemetry(
                raw_stdout,
                final_stdout=stdout,
                model=resolved_model,
            )
            recorded, record_error = _record_automation_run(
                backend=selected_backend,
                task_profile=task_profile,
                model=resolved_model,
                reasoning_effort=resolved_effort,
                cwd=cwd_path,
                output_format=requested_output_format,
                prompt=prompt,
                returncode=result.returncode,
                duration_ms=int((time.perf_counter() - started_at) * 1000),
                telemetry=telemetry,
            )
            stderr = result.stderr or ""
            if not recorded:
                stderr = _append_stderr(stderr, record_error)
            return subprocess.CompletedProcess(
                cmd,
                result.returncode,
                final_stdout,
                stderr,
            )

    raise AutomationBackendUnavailableError(f"Unsupported automation backend: {selected_backend}")


def probe_automation_backend(
    *,
    backend: str | None = None,
    cwd: str | os.PathLike[str] | None = None,
    timeout: int = 60,
) -> dict:
    selected_backend = backend or resolve_automation_backend()
    if selected_backend == BACKEND_NONE:
        return {
            "ok": False,
            "backend": BACKEND_NONE,
            "reason": "automation disabled in config",
        }
    try:
        result = run_automation_prompt(
            "Reply exactly OK.",
            backend=selected_backend,
            cwd=cwd,
            timeout=timeout,
            output_format="text",
        )
    except AutomationBackendUnavailableError as exc:
        return {
            "ok": False,
            "backend": selected_backend,
            "reason": str(exc),
        }
    output = (result.stdout or "").strip()
    return {
        "ok": result.returncode == 0 and "OK" in output,
        "backend": selected_backend,
        "returncode": result.returncode,
        "stdout": output,
        "stderr": (result.stderr or "").strip(),
    }
