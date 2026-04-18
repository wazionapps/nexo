"""R23m — email/message duplicated within recent window.

Pure decision module. Part of Fase D2 (soft).

Fires when the operator is about to send an email or DM whose body
shares >= 90% similarity with a message sent in the last 15 minutes
to the same thread/recipient. Prevents double-posting when a follow-
up tool call fires twice by accident.
"""
from __future__ import annotations

import time


INJECTION_PROMPT_TEMPLATE = (
    "R23m duplicate message: the outbound to '{thread}' is "
    "{similarity}% identical to one sent {age_sec}s ago. Confirm "
    "this is intentional before sending — most duplicate sends are "
    "re-runs of the same tool call."
)

DEFAULT_WINDOW_SEC = 15 * 60
DEFAULT_SIM_THRESHOLD = 0.90


def _jaccard_tokens(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    tokens_a = set(a.lower().split())
    tokens_b = set(b.lower().split())
    if not tokens_a or not tokens_b:
        return 0.0
    inter = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(inter) / len(union)


def should_inject_r23m(
    tool_name: str,
    tool_input,
    *,
    recent_messages: list[dict],
    now_ts: float | None = None,
    window_sec: int = DEFAULT_WINDOW_SEC,
    threshold: float = DEFAULT_SIM_THRESHOLD,
) -> tuple[bool, str]:
    if tool_name not in {"nexo_send", "nexo_email_send", "gmail_send"}:
        return False, ""
    if not isinstance(tool_input, dict):
        return False, ""
    body = str(tool_input.get("body") or tool_input.get("content") or "")
    thread = str(tool_input.get("to") or tool_input.get("thread") or tool_input.get("recipient") or "")
    if not body or not thread:
        return False, ""
    cutoff = (now_ts or time.time()) - window_sec
    for msg in reversed(recent_messages or []):
        if float(msg.get("ts") or 0) < cutoff:
            continue
        if str(msg.get("thread") or "").lower() != thread.lower():
            continue
        sim = _jaccard_tokens(body, str(msg.get("body") or ""))
        if sim >= threshold:
            age = int((now_ts or time.time()) - float(msg.get("ts") or 0))
            # Normalize similarity to percent int so the JS twin (Math.round)
            # and Python produce identical strings. The template stays
            # byte-for-byte identical between engines.
            prompt = INJECTION_PROMPT_TEMPLATE.format(
                thread=thread,
                similarity=int(sim * 100 + 0.5),  # half-up to match JS Math.round (parity)
                age_sec=max(age, 0),
            )
            return True, prompt
    return False, ""
