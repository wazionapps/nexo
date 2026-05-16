"""Reminders and followups reader — reads from SQLite database."""

from db import get_reminders, get_followups
from datetime import date
from interactive_db import interactive_db_timeout, is_db_busy


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
    warnings: list[str] = []

    with interactive_db_timeout():
        if filter_type in ("due", "all", "completed", "deleted", "history", "any"):
            r = _format_reminders_safe(filter_type, warnings)
            if r:
                parts.append(r)

        if filter_type in ("due", "all", "followups", "completed", "deleted", "history", "any"):
            f = _format_followups_safe(filter_type, warnings)
            if f:
                parts.append(f)

    result = "\n\n".join(parts)
    if warnings:
        prefix = "REMINDERS DEGRADED:\n  " + "\n  ".join(warnings)
        prefix += "\n  Continue with the user request; reminders will catch up shortly."
        return f"{prefix}\n\n{result}" if result else prefix
    return result if result else "No pending reminders."


def _format_reminders_safe(filter_type: str, warnings: list[str]) -> str:
    try:
        return _format_reminders(filter_type)
    except Exception as exc:
        if is_db_busy(exc):
            warnings.append("reminders skipped because the local brain database is busy")
        else:
            warnings.append(f"reminders skipped ({type(exc).__name__})")
        return ""


def _format_followups_safe(filter_type: str, warnings: list[str]) -> str:
    try:
        return _format_followups(filter_type)
    except Exception as exc:
        if is_db_busy(exc):
            warnings.append("followups skipped because the local brain database is busy")
        else:
            warnings.append(f"followups skipped ({type(exc).__name__})")
        return ""


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
        impact = float(r.get("impact_score") or 0)
        impact_tag = f" [impact {impact:.1f}]" if impact > 0 else ""
        status = r.get("status") or ""
        status_tag = f" [{status}]" if status and status != "PENDING" else ""
        lines.append(f"  {nfid} {fecha_display}{due_marker}{pri_tag}{impact_tag}{rec_tag}{status_tag} — {desc[:120]}")

    return "\n".join(lines)
