"""Provider circuit breaker — Fase 1.6 (SPEC-FIABILIDAD-FASES-2026-06).

Incident (2026-06-10, operator report): when the selected engine (Claude or
Codex) is unavailable — credits exhausted, rate limited, auth expired — every
headless cron (email-monitor, deep-sleep, evolution, catch-up, followups…)
still launched a session that died mid-flight, burned its retry budget, then
escalated to the operator by email (in English, regardless of the configured
language). Work was lost or degraded to manual across the whole system.

This module gives the single launch path (agent_runner.run_automation_prompt)
a shared, persisted circuit breaker:

- ``check_provider_available(backend)``  — gate BEFORE launching.
- ``classify_session_failure(...)``      — map a dead session to a cause.
- ``record_session_outcome(backend, …)`` — close on success, open on
  classified failures (credits/rate-limit/auth open immediately; generic
  failures only after N consecutive).
- ``should_notify_operator(backend)``    — True exactly once per opening, so
  the operator gets ONE notice instead of one per queued item.

State lives in ``$NEXO_HOME/runtime/data/provider-circuit-breaker.json`` so
every cron process shares the same view. Writes are atomic (tmp + replace).
The breaker FAILS OPEN on its own errors: a broken state file must never
block automations.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

# Failure classes that open the breaker on FIRST sight: retrying cannot help
# until the underlying condition clears.
HARD_OPEN_REASONS = {"credits", "rate_limit", "auth"}

# Generic failures (network blips, crashes) need this many consecutive hits
# before the breaker opens — one flaky session must not pause the fleet.
GENERIC_OPEN_THRESHOLD = 3

# How long the breaker stays open before allowing ONE half-open probe call.
DEFAULT_RETRY_AFTER_S = {
    "credits": 30 * 60,      # credit top-ups/renewals are slow; probe every 30m
    "rate_limit": 15 * 60,   # unless the provider told us a reset time
    "auth": 60 * 60,         # needs operator action; probe hourly anyway
    "generic": 10 * 60,
}

_FAILURE_PATTERNS = (
    ("credits", re.compile(
        r"credit balance is too low|insufficient[_ ]quota|exceeded your current quota"
        r"|billing hard limit|out of credits|usage limit reached|hit your usage limit"
        r"|purchase more credits|plan limits",
        re.I)),
    ("rate_limit", re.compile(
        r"rate[_ -]?limit|too many requests|\b429\b|overloaded[_ ]error|\b529\b"
        r"|server overloaded|capacity constraints",
        re.I)),
    ("auth", re.compile(
        r"authentication[_ ]error|\b401\b|unauthorized|oauth token (has )?expired"
        r"|invalid api key|api key not (found|valid)|please run /login|token_revoked",
        re.I)),
)


def _state_path() -> Path:
    base = Path(os.environ.get("NEXO_HOME") or (Path.home() / ".nexo"))
    return base / "runtime" / "data" / "provider-circuit-breaker.json"


def _now() -> float:
    return time.time()


def _load_state() -> dict:
    try:
        raw = _state_path().read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    try:
        path = _state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        pass  # the breaker must never break the caller


def _entry(state: dict, backend: str) -> dict:
    entry = state.get(backend)
    if not isinstance(entry, dict):
        entry = {}
        state[backend] = entry
    return entry


class ProviderTemporarilyUnavailableError(RuntimeError):
    """Selected provider is up for maintenance by reality (credits/rate/auth).

    Callers should QUEUE/DEFER their work without burning retry budgets; the
    breaker re-probes automatically once ``retry_after`` passes.
    """

    def __init__(self, backend: str, reason: str, retry_after_ts: float | None):
        self.backend = backend
        self.reason = reason
        self.retry_after_ts = retry_after_ts
        wait = ""
        if retry_after_ts:
            wait = f"; next probe after {time.strftime('%H:%M', time.localtime(retry_after_ts))}"
        super().__init__(
            f"provider '{backend}' temporarily unavailable (reason: {reason}){wait}. "
            "Work should be queued, not retried blindly."
        )


def classify_session_failure(returncode: int | None, stdout: str = "", stderr: str = "") -> str | None:
    """Map a finished/dead session to a failure class, or None if it looks fine.

    Only classifies KNOWN unavailability shapes; an exit code != 0 with no
    matching pattern returns "generic" so the threshold logic decides.
    A zero return code returns None.
    """
    if returncode == 0:
        return None
    haystack = f"{stdout or ''}\n{stderr or ''}"
    for reason, pattern in _FAILURE_PATTERNS:
        if pattern.search(haystack):
            return reason
    return "generic"


def check_provider_available(backend: str) -> tuple[bool, dict]:
    """Gate to call BEFORE launching the provider.

    Returns (True, entry) when closed — or when open but past retry_after, in
    which case the caller's attempt IS the half-open probe (its outcome will
    close or re-open the breaker via record_session_outcome).
    Returns (False, entry) while open and inside the wait window.
    """
    state = _load_state()
    entry = _entry(state, backend)
    if entry.get("state") != "open":
        return True, entry
    retry_after = float(entry.get("retry_after") or 0)
    if retry_after and _now() >= retry_after:
        entry["half_open_probe_at"] = _now()
        _save_state(state)
        return True, entry
    return False, entry


def raise_if_unavailable(backend: str) -> None:
    ok, entry = check_provider_available(backend)
    if ok:
        return
    raise ProviderTemporarilyUnavailableError(
        backend,
        str(entry.get("reason") or "unknown"),
        float(entry.get("retry_after") or 0) or None,
    )


def record_session_outcome(
    backend: str,
    *,
    ok: bool,
    reason: str | None = None,
    retry_after_s: float | None = None,
) -> dict:
    """Update the breaker after a session finished (or died).

    ``reason`` should come from classify_session_failure. ``retry_after_s``
    lets callers honour a provider-reported reset time.
    """
    state = _load_state()
    entry = _entry(state, backend)
    if ok:
        was_open = entry.get("state") == "open"
        state[backend] = {
            "state": "closed",
            "consecutive_failures": 0,
            "closed_at": _now(),
            "recovered_from": entry.get("reason") if was_open else None,
            # The pause email promises "another notice when work resumes":
            # arm that notice only when the OPENING was actually notified.
            "resume_notice_pending": bool(was_open and entry.get("operator_notified_at")),
        }
        _save_state(state)
        return state[backend]

    failure_reason = reason or "generic"
    consecutive = int(entry.get("consecutive_failures") or 0) + 1
    entry["consecutive_failures"] = consecutive
    should_open = failure_reason in HARD_OPEN_REASONS or consecutive >= GENERIC_OPEN_THRESHOLD
    if should_open:
        wait = retry_after_s if retry_after_s else DEFAULT_RETRY_AFTER_S.get(failure_reason, DEFAULT_RETRY_AFTER_S["generic"])
        already_open = entry.get("state") == "open"
        entry.update({
            "state": "open",
            "reason": failure_reason,
            "opened_at": entry.get("opened_at") if already_open else _now(),
            "retry_after": _now() + float(wait),
        })
        if not already_open:
            entry["operator_notified_at"] = None
    _save_state(state)
    return entry


def should_notify_operator_resumed(backend: str) -> bool:
    """True exactly once after a notified opening closes (engine resumed).

    The pause notice tells the operator "you will get another notice when
    work resumes" — this is that notice's gate. Clears the flag on read.
    """
    state = _load_state()
    entry = _entry(state, backend)
    if entry.get("state") == "closed" and entry.get("resume_notice_pending"):
        entry["resume_notice_pending"] = False
        _save_state(state)
        return True
    return False


def should_notify_operator(backend: str) -> bool:
    """True exactly once per opening — callers use it to send ONE notice."""
    state = _load_state()
    entry = _entry(state, backend)
    if entry.get("state") != "open":
        return False
    if entry.get("operator_notified_at"):
        return False
    entry["operator_notified_at"] = _now()
    _save_state(state)
    return True


def breaker_status() -> dict:
    """Read-only snapshot for doctors/diagnostics."""
    return _load_state()
