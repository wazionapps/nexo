"""Reminders and followups reader — reads from SQLite database."""

from db import get_reminders, get_followups
from datetime import date


def _is_due(date_str: str) -> bool:
    """Check if a date string is today or in the past."""
    if not date_str:
        return False
    try:
        d = date.fromisoformat(date_str.strip()[:10])
        return d <= date.today()
    except (ValueError, IndexError):
        return False


def handle_reminders(filter_type: str = "due") -> str:
    """Read reminders and followups from SQLite, return relevant ones.

    Args:
        filter_type: 'due', 'all', 'followups', 'completed', 'deleted', 'history', 'any'
    """
    parts = []

    if filter_type in ("due", "all", "completed", "deleted", "history", "any"):
        r = _format_reminders(filter_type)
        if r:
            parts.append(r)

    if filter_type in ("due", "all", "followups", "completed", "deleted", "history", "any"):
        f = _format_followups(filter_type)
        if f:
            parts.append(f)

    result = "\n\n".join(parts)
    return result if result else "No pending reminders."


def _format_reminders(filter_type: str) -> str:
    """Format reminders from database."""
    rows = get_reminders(filter_type)
    if not rows:
        return ""

    lines = ["REMINDERS:"]
    for r in rows:
        rid = r.get("id", "?")
        fecha = r.get("date") or ""
        desc = r.get("description", "")
        status = r.get("status", "")
        desc = desc.replace("**", "")
        due_marker = " [DUE]" if _is_due(fecha) else ""
        fecha_display = f"({fecha})" if fecha else "(—)"
        status_tag = f" [{status}]" if status and status != "PENDING" else ""
        lines.append(f"  {rid} {fecha_display}{due_marker}{status_tag} — {desc[:120]}")
        if "RECURRENTE" in status.upper():
            lines.append(f"    Status: {status}")

    return "\n".join(lines)


def _format_followups(filter_type: str) -> str:
    """Format followups from database."""
    rows = get_followups(filter_type)
    if not rows:
        return ""

    lines = ["FOLLOWUPS NEXO:"]
    # Sort by priority: critical first, then high, medium, low
    pri_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    rows = sorted(rows, key=lambda r: pri_order.get(r.get("priority") or "medium", 2))
    for r in rows:
        nfid = r.get("id", "?")
        fecha = r.get("date") or ""
        desc = r.get("description", "")
        desc = desc.replace("**", "")
        due_marker = " [DUE]" if _is_due(fecha) else ""
        fecha_display = f"({fecha})" if fecha else "(—)"
        rec = r.get("recurrence") or ""
        rec_tag = f" [♻️ {rec}]" if rec else ""
        pri = r.get("priority") or "medium"
        pri_icon = {"critical": "🔴", "high": "🟠", "medium": "", "low": "⚪"}.get(pri, "")
        pri_tag = f" {pri_icon}" if pri_icon else ""
        status = r.get("status") or ""
        status_tag = f" [{status}]" if status and status != "PENDING" else ""
        lines.append(f"  {nfid} {fecha_display}{due_marker}{pri_tag}{rec_tag}{status_tag} — {desc[:120]}")

    return "\n".join(lines)
