"""Coordination tools: file tracking, messaging, Q&A."""

from db import (
    track_files, untrack_files, get_all_tracked_files,
    send_message, get_inbox,
    ask_question, answer_question, get_pending_questions, check_answer,
    now_epoch,
)
from tools_sessions import _format_age


def handle_track(sid: str, paths: list[str]) -> str:
    """Track files being edited. Reports conflicts immediately."""
    result = track_files(sid, paths)
    if "error" in result:
        return f"ERROR: {result['error']}"

    lines = [f"Tracked: {', '.join(result['tracked'])}"]

    if result["conflicts"]:
        lines.append("")
        lines.append("FILE CONFLICT DETECTED:")
        for c in result["conflicts"]:
            lines.append(f"  {c['sid']} ({c['task']}):")
            for f in c["files"]:
                lines.append(f"    {f}")
        lines.append("")
        lines.append("STOP — file conflict detected. Do not edit until resolved.")

    return "\n".join(lines)


def handle_untrack(sid: str, paths: list[str] | None = None) -> str:
    """Untrack files. If no paths given, untrack all."""
    untrack_files(sid, paths)
    if paths:
        return f"Untracked: {', '.join(paths)}"
    return "All files released."


def handle_files() -> str:
    """Show all tracked files across sessions."""
    data = get_all_tracked_files()
    if not data:
        return "No tracked files."

    lines = ["TRACKED FILES:"]
    all_paths = {}
    for sid, info in data.items():
        for path in info["files"]:
            all_paths.setdefault(path, []).append(sid)
        lines.append(f"  {sid} ({info['task']}):")
        for path in info["files"]:
            lines.append(f"    {path}")

    conflicts = {p: sids for p, sids in all_paths.items() if len(sids) > 1}
    if conflicts:
        lines.append("")
        lines.append("CONFLICTS:")
        for path, sids in conflicts.items():
            lines.append(f"  {path} -> {', '.join(sids)}")

    return "\n".join(lines)


def handle_send(from_sid: str, to_sid: str, text: str) -> str:
    """Send a message. to_sid='all' for broadcast."""
    msg_id = send_message(from_sid, to_sid, text)
    target = "all sessions" if to_sid == "all" else to_sid
    return f"Message {msg_id} sent to {target}."


def handle_ask(from_sid: str, to_sid: str, question: str) -> str:
    """Create a question to another session (non-blocking)."""
    qid = ask_question(from_sid, to_sid, question)
    return (
        f"Question sent: {qid}\n"
        f"To: {to_sid}\n"
        f"Question: {question}\n\n"
        f"The other session will see the question on their next nexo_heartbeat.\n"
        f"Use nexo_check_answer(qid='{qid}') to check if answered."
    )


def handle_answer(qid: str, answer_text: str) -> str:
    """Answer a pending question."""
    result = answer_question(qid, answer_text)
    if "error" in result:
        return f"ERROR: {result['error']}"
    return f"Answered {qid}: {answer_text}"


def handle_check_answer(qid: str) -> str:
    """Check if a question has been answered."""
    result = check_answer(qid)
    if not result:
        return f"Question {qid} not found."
    if result["status"] == "answered":
        return f"ANSWER for {qid}: {result['answer']}"
    elif result["status"] == "expired":
        return f"Question {qid} expired without answer."
    return f"Question {qid} still pending. Retry in a few seconds."
