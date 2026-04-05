from __future__ import annotations

"""Terminal client launchers and headless automation backend runner."""

import json
import os
import shlex
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path

from client_preferences import (
    BACKEND_NONE,
    CLIENT_CLAUDE_CODE,
    CLIENT_CODEX,
    TERMINAL_CLIENT_KEYS,
    load_client_preferences,
    resolve_automation_backend,
    resolve_client_runtime_profile,
    resolve_terminal_client,
)


NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
CLAUDE_LEGACY_MODEL_HINTS = {"opus", "sonnet"}


class AgentRunnerError(RuntimeError):
    """Base exception for runner failures."""


class TerminalClientUnavailableError(AgentRunnerError):
    """Raised when the requested interactive client cannot be launched."""


class AutomationBackendUnavailableError(AgentRunnerError):
    """Raised when the configured automation backend is unavailable."""


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
    # `--full-auto` already expands to a compatible sandboxed approval policy.
    return ["--full-auto"]


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
        cmd.extend(["--dangerously-skip-permissions", target_path])
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
    return subprocess.run(cmd, env=launch_env)


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


def _build_codex_prompt(
    prompt: str,
    *,
    output_format: str = "",
    append_system_prompt: str = "",
    allowed_tools: str = "",
) -> str:
    instructions: list[str] = []
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

    cwd_path = Path(cwd).expanduser().resolve() if cwd else Path.cwd()
    run_env = _headless_env(env)
    extra_args = list(extra_args or [])
    resolved_model, resolved_effort = _resolve_runtime_model_and_effort(
        selected_backend,
        model=model,
        reasoning_effort=reasoning_effort,
        preferences=prefs,
    )

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
        if output_format:
            cmd.extend(["--output-format", output_format])
        if append_system_prompt:
            cmd.extend(["--append-system-prompt", append_system_prompt])
        if allowed_tools:
            cmd.extend(["--allowedTools", allowed_tools])
        cmd.extend(extra_args)
        return subprocess.run(
            cmd,
            cwd=str(cwd_path),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=run_env,
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
            stdout = output_path.read_text() if output_path.exists() else (result.stdout or "")
            return subprocess.CompletedProcess(
                cmd,
                result.returncode,
                stdout,
                result.stderr,
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
