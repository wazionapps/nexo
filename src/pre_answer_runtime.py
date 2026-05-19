"""Runtime wrapper for pre-answer routing across CLI, MCP and Desktop."""

from __future__ import annotations

from typing import Any

from pre_answer_router import DEFAULT_BUDGET_MS, DEFAULT_TOKEN_BUDGET, route_pre_answer


def _as_positive_int(value: Any, fallback: int) -> int:
    try:
        number = int(value)
    except Exception:
        return fallback
    return number if number > 0 else fallback


def _text_from_content(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text = str(block.get("text") or "").strip()
            if text:
                parts.append(text)
    return "\n\n".join(parts).strip()


def extract_query(payload: dict[str, Any]) -> str:
    """Return the user-visible text the router should classify."""

    for key in ("query", "text", "message"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return _text_from_content(payload.get("content"))


def run_pre_answer_route(
    payload: dict[str, Any],
    *,
    source_adapters: dict[str, Any] | None = None,
    telemetry_sink: Any | None = None,
) -> dict[str, Any]:
    """Execute the semantic pre-answer router and persist lightweight usage telemetry."""

    query = extract_query(payload)
    if not query:
        return {
            "ok": False,
            "should_inject": False,
            "error": "query_required",
            "intent": str(payload.get("intent") or "auto"),
        }

    budget_ms = _as_positive_int(payload.get("budget_ms"), DEFAULT_BUDGET_MS)
    token_budget = _as_positive_int(payload.get("token_budget"), DEFAULT_TOKEN_BUDGET)
    route = route_pre_answer(
        query,
        sid=str(payload.get("sid") or payload.get("session_id") or ""),
        conversation_id=str(payload.get("conversation_id") or payload.get("conversationId") or ""),
        intent=str(payload.get("intent") or "auto"),
        area=str(payload.get("area") or ""),
        files=str(payload.get("files") or ""),
        budget_ms=budget_ms,
        token_budget=token_budget,
        current_context=str(payload.get("current_context") or payload.get("currentContext") or ""),
        source_adapters=source_adapters,
        telemetry_sink=telemetry_sink,
    )
    result = route.to_dict()
    result["deadline_ms"] = budget_ms
    result["timed_out"] = result.get("aborted_reason") in {"timeout", "deadline_exhausted", "source_timeout"}
    result["route_used"] = "brain_pre_answer_router"

    try:
        from local_context.usage_events import record_router_usage

        result["usage_event"] = record_router_usage(
            query,
            result,
            client=str(payload.get("source") or payload.get("client") or "unknown"),
            tool="pre_answer_router",
            route_stage="pre_answer",
            intent=str(result.get("intent") or payload.get("intent") or "auto"),
            elapsed_ms=int(float(result.get("elapsed_ms") or 0)),
            deadline_ms=budget_ms,
            used_before_response=True,
        )
    except Exception as exc:
        result["usage_event"] = {
            "ok": False,
            "error": "usage_event_failed",
            "detail": f"{type(exc).__name__}: {exc}",
        }

    return result
