#!/usr/bin/env python3
"""
NEXO Synthesis Engine — Daily intelligence brief.

Runs every 2 hours via LaunchAgent. Executes ONCE per day (internal gate).
Queries nexo.db + claude-mem.db and writes ~/claude/coordination/daily-synthesis.md

Zero external dependencies beyond stdlib + sqlite3.
"""

import fcntl
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, date, timedelta
from pathlib import Path

# ─── Paths ────────────────────────────────────────────────────────────────────
HOME = Path.home()
CLAUDE_DIR = HOME / "claude"
COORD_DIR = CLAUDE_DIR / "coordination"
NEXO_HOME = os.environ.get("NEXO_HOME", str(Path.home() / ".nexo"))

NEXO_DB = Path(NEXO_HOME) / "nexo.db"
CLAUDE_MEM_DB = HOME / ".claude-mem" / "claude-mem.db"

OUTPUT_FILE = COORD_DIR / "daily-synthesis.md"
SYNTHESIS_LOG = COORD_DIR / "synthesis-log.json"
LAST_RUN_FILE = COORD_DIR / "synthesis-last-run"
LOCK_FILE = COORD_DIR / "synthesis.lock"

TODAY = date.today()
TODAY_STR = TODAY.isoformat()
SEVEN_DAYS_AGO = (TODAY - timedelta(days=7)).isoformat()
TOMORROW = (TODAY + timedelta(days=1)).isoformat()


# ─── Utilities ────────────────────────────────────────────────────────────────

def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def should_run() -> bool:
    """Gate: run at most once per day."""
    if LAST_RUN_FILE.exists():
        last = LAST_RUN_FILE.read_text().strip()
        if last == TODAY_STR:
            log(f"Already ran today ({TODAY_STR}). Skipping.")
            return False
    return True


def mark_done():
    LAST_RUN_FILE.write_text(TODAY_STR)


def acquire_lock():
    lock_fd = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lock_fd
    except BlockingIOError:
        log("Another instance is running. Exiting.")
        sys.exit(0)


def release_lock(lock_fd):
    fcntl.flock(lock_fd, fcntl.LOCK_UN)
    lock_fd.close()
    try:
        LOCK_FILE.unlink()
    except FileNotFoundError:
        pass


def safe_query(db_path: Path, sql: str, params=()) -> list:
    """Run a query against a SQLite DB, return rows or [] on any error."""
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        log(f"Query error on {db_path.name}: {e}")
        return []


def truncate(text: str, max_len: int = 200) -> str:
    if not text:
        return ""
    text = text.strip().replace("\n", " ")
    return text[:max_len] + ("…" if len(text) > max_len else "")


# ─── Section builders ─────────────────────────────────────────────────────────

def section_learnings() -> str:
    rows = safe_query(
        NEXO_DB,
        "SELECT category, title, content, reasoning FROM learnings "
        "WHERE date(created_at, 'unixepoch') = ? ORDER BY created_at DESC",
        (TODAY_STR,),
    )
    if not rows:
        return "No new errors recorded."

    lines = []
    for r in rows:
        cat = r.get("category") or "general"
        title = r.get("title") or ""
        content = truncate(r.get("content") or "", 180)
        lines.append(f"- **[{cat}]** {title}: {content}")
    return "\n".join(lines)


def section_decisions() -> str:
    # decisions table uses columns: domain, decision, alternatives, based_on, outcome
    rows = safe_query(
        NEXO_DB,
        "SELECT domain, decision, alternatives, based_on, outcome FROM decisions "
        "WHERE date(created_at) = ? ORDER BY created_at DESC",
        (TODAY_STR,),
    )
    if not rows:
        return "No decisions recorded."

    lines = []
    for r in rows:
        domain = r.get("domain") or ""
        chosen = truncate(r.get("decision") or "", 160)
        discarded = truncate(r.get("alternatives") or "", 120)
        why = truncate(r.get("based_on") or "", 120)
        outcome = r.get("outcome") or ""

        line = f"- **[{domain}]** Chosen: {chosen}"
        if discarded:
            line += f"\n  Discarded: {discarded}"
        if why:
            line += f"\n  Why: {why}"
        if outcome:
            line += f"\n  Result: {truncate(outcome, 100)}"
        lines.append(line)
    return "\n".join(lines)


def section_changes() -> str:
    rows = safe_query(
        NEXO_DB,
        "SELECT files, what_changed, why, risks, affects FROM change_log "
        "WHERE date(created_at) = ? ORDER BY created_at DESC",
        (TODAY_STR,),
    )
    if not rows:
        return "No code changes recorded."

    # Group by "system" (first part of first file path)
    by_system = defaultdict(list)
    for r in rows:
        files_raw = r.get("files") or ""
        # Take first file, extract top-level system name
        first_file = files_raw.split(",")[0].strip()
        parts = [p for p in first_file.replace("\\", "/").split("/") if p and p != "_public"]
        system = parts[0] if parts else "misc"
        by_system[system].append(r)

    lines = []
    for system, entries in by_system.items():
        lines.append(f"**{system}** ({len(entries)} change{'s' if len(entries) > 1 else ''}):")
        for r in entries[:3]:  # cap per system
            what = truncate(r.get("what_changed") or "", 160)
            risks = truncate(r.get("risks") or "", 100)
            lines.append(f"  - {what}")
            if risks:
                lines.append(f"    ⚠ Risks: {risks}")
    return "\n".join(lines)


def section_patterns() -> str:
    # Learnings by category — last 7 days
    learn_rows = safe_query(
        NEXO_DB,
        "SELECT category, title FROM learnings "
        "WHERE date(created_at, 'unixepoch') >= ? ORDER BY created_at DESC",
        (SEVEN_DAYS_AGO,),
    )
    # change_log — last 7 days
    change_rows = safe_query(
        NEXO_DB,
        "SELECT files FROM change_log WHERE date(created_at) >= ?",
        (SEVEN_DAYS_AGO,),
    )

    total_learn = len(learn_rows)
    total_changes = len(change_rows)

    if total_learn < 3 and total_changes < 3:
        return "Insufficient data for pattern analysis (< 7 days)."

    lines = []

    # Categories with most learnings
    if learn_rows:
        cat_counter = Counter(r.get("category") or "general" for r in learn_rows)
        top_cats = cat_counter.most_common(3)
        lines.append(f"**Areas with most errors** (last 7d, {total_learn} learnings):")
        for cat, count in top_cats:
            lines.append(f"  - {cat}: {count} {'error' if count == 1 else 'errors'}")

    # Systems most touched in change_log
    if change_rows:
        sys_counter: Counter = Counter()
        for r in change_rows:
            files_raw = r.get("files") or ""
            for f in files_raw.split(",")[:3]:
                f = f.strip()
                parts = [p for p in f.replace("\\", "/").split("/") if p and p != "_public"]
                if parts:
                    sys_counter[parts[0]] += 1
        top_sys = sys_counter.most_common(3)
        lines.append(f"**Most touched systems** (last 7d, {total_changes} changes):")
        for sys_name, count in top_sys:
            lines.append(f"  - {sys_name}: {count} {'modification' if count == 1 else 'modifications'}")

    # Recurring error patterns — categories with learnings on 3+ different days
    if learn_rows:
        # Get daily breakdown per category
        daily_cats = safe_query(
            NEXO_DB,
            "SELECT category, date(created_at, 'unixepoch') as day "
            "FROM learnings WHERE date(created_at, 'unixepoch') >= ? "
            "GROUP BY category, day",
            (SEVEN_DAYS_AGO,),
        )
        if daily_cats:
            cat_days = Counter(r.get("category") or "general" for r in daily_cats)
            recurring = [(c, d) for c, d in cat_days.items() if d >= 3]
            if recurring:
                lines.append("**Categories with recurring errors** (3+ different days):")
                for cat, days in sorted(recurring, key=lambda x: -x[1]):
                    lines.append(f"  - {cat}: errors on {days} days — weak point")

    return "\n".join(lines) if lines else "No significant patterns detected."


def section_manana() -> str:
    lines = []

    # Reminders due <= tomorrow, PENDIENTE
    rem_rows = safe_query(
        NEXO_DB,
        "SELECT id, date, description, category FROM reminders "
        "WHERE status LIKE 'PENDIENTE%' AND date IS NOT NULL AND date <= ? "
        "ORDER BY date ASC",
        (TOMORROW,),
    )
    if rem_rows:
        lines.append("### Overdue/tomorrow reminders")
        for r in rem_rows:
            d = r.get("date") or ""
            cat = r.get("category") or ""
            desc = truncate(r.get("description") or "", 150)
            overdue = " ⚠ OVERDUE" if d and d < TODAY_STR else ""
            lines.append(f"- [{d}]{overdue} {desc}" + (f" ({cat})" if cat else ""))
    else:
        lines.append("### Reminders\nNone overdue or due tomorrow.")

    # Followups due <= tomorrow, PENDIENTE
    fol_rows = safe_query(
        NEXO_DB,
        "SELECT id, date, description FROM followups "
        "WHERE status = 'PENDIENTE' AND date IS NOT NULL AND date <= ? "
        "ORDER BY date ASC",
        (TOMORROW,),
    )
    if fol_rows:
        lines.append("### Overdue/tomorrow followups")
        for r in fol_rows:
            d = r.get("date") or ""
            desc = truncate(r.get("description") or "", 150)
            overdue = " ⚠ OVERDUE" if d and d < TODAY_STR else ""
            lines.append(f"- [{d}]{overdue} {desc}")
    else:
        lines.append("### Followups\nNone overdue or due tomorrow.")

    # Last 3 session diary entries — pending + next_session_context
    diary_rows = safe_query(
        NEXO_DB,
        "SELECT domain, pending, context_next, created_at FROM session_diary "
        "ORDER BY created_at DESC LIMIT 3",
    )
    if diary_rows:
        lines.append("### Active context (recent sessions)")
        for r in diary_rows:
            domain = r.get("domain") or "general"
            pending = truncate(r.get("pending") or "", 200)
            nxt = truncate(r.get("context_next") or "", 200)
            ts = r.get("created_at") or ""
            if pending or nxt:
                lines.append(f"**[{domain}]** ({ts[:16]}):")
                if pending:
                    lines.append(f"  Pending: {pending}")
                if nxt:
                    lines.append(f"  For next session: {nxt}")

    return "\n".join(lines) if lines else "No items for tomorrow."


def section_autoevaluacion() -> str:
    diary_rows = safe_query(
        NEXO_DB,
        "SELECT mental_state, user_signals, self_critique, summary, created_at FROM session_diary "
        "WHERE date(created_at) = ? ORDER BY created_at DESC",
        (TODAY_STR,),
    )
    if not diary_rows:
        return "No session diaries recorded today."

    lines = []

    # Self-critique section (NEW — most important)
    all_critiques = []
    for r in diary_rows:
        sc = r.get("self_critique") or ""
        if sc.strip() and not sc.strip().lower().startswith("no self-critique"):
            all_critiques.append(truncate(sc, 300))

    if all_critiques:
        lines.append(f"**SELF-CRITIQUES ({len(all_critiques)} sessions with detected failures):**")
        for c in all_critiques[:5]:
            lines.append(f"  - {c}")
        lines.append("**ACTION:** These self-critiques should inform tomorrow's behavior. If a pattern repeats 3+ days, the nightly consolidator will promote it to permanent memory.")
        lines.append("")

    # user_signals patterns
    all_signals = []
    mental_states = []
    for r in diary_rows:
        sig = r.get("user_signals") or ""
        if sig.strip():
            all_signals.append(truncate(sig, 200))
        ms = r.get("mental_state") or ""
        if ms.strip():
            mental_states.append(truncate(ms, 200))

    if user_signals_text := "\n".join(f"  - {s}" for s in all_signals[:3] if s):
        lines.append(f"**User signals:**\n{user_signals_text}")

    if mental_states:
        lines.append(f"**Session mental states:**")
        for ms in mental_states[:2]:
            lines.append(f"  - {ms}")

    # Derive what to do differently based on signal analysis
    if all_signals:
        # Detect repeated corrections
        correction_words = ["corrig", "frustrat", "don't understand", "demand", "repeat",
                           "shouldn't", "why not", "again", "tiring",
                           "always wait", "reactive", "not proactive"]
        correction_count = sum(
            1 for s in all_signals
            if any(w in s.lower() for w in correction_words)
        )
        if correction_count >= 2:
            lines.append(f"**ALERT:** User corrected {correction_count} times today — review what is repeating.")
        lines.append("**For tomorrow:** Review previous signals before acting.")
    elif not diary_rows:
        lines.append("**For tomorrow:** Remember to write diary before closing session.")

    # Check for postmortem daily summary
    postmortem_file = COORD_DIR / "postmortem-daily.md"
    if postmortem_file.exists():
        pm_content = postmortem_file.read_text().strip()
        if "Promovido a memoria permanente" in pm_content:
            lines.append("")
            lines.append("**NEW PERMANENT RULES (generated last night by the consolidator):**")
            for line in pm_content.split("\n"):
                if line.startswith("- ") and "Promovido" not in line:
                    lines.append(f"  {line}")

    return "\n".join(lines) if lines else "No self-evaluation data."


def section_user_observer() -> str:
    """Track user's patterns: forgotten ideas, abandoned topics, recurring requests."""
    lines = []

    # 1. Reminders without dates (ideas that accumulate without agenda)
    no_date = safe_query(
        NEXO_DB,
        "SELECT id, description FROM reminders "
        "WHERE date IS NULL AND status LIKE 'PENDIENTE%' ORDER BY rowid",
    )
    if no_date:
        lines.append(f"**Ideas sin agenda:** {len(no_date)} reminders sin fecha")
        # Show oldest 3 as examples
        for r in no_date[:3]:
            desc = truncate(r.get("description") or "", 80)
            lines.append(f"  - {r.get('id')}: {desc}")
        if len(no_date) > 3:
            lines.append(f"  - ... and {len(no_date) - 3} more")

    # 2. Followups waiting on user or external responses
    waiting = safe_query(
        NEXO_DB,
        "SELECT id, description, date FROM followups "
        "WHERE status = 'PENDIENTE' "
        "AND (description LIKE '%respuesta%' "
        "     OR description LIKE '%preguntar%' OR description LIKE '%confirme%' "
        "     OR description LIKE '%decidió%') "
        "ORDER BY date",
    )
    if waiting:
        lines.append(f"**Waiting for user or third-party response/decision:** {len(waiting)}")
        for r in waiting[:5]:
            d = r.get("date") or "no date"
            desc = truncate(r.get("description") or "", 100)
            lines.append(f"  - {r.get('id')} ({d}): {desc}")

    # 3. Overdue reminders that keep getting postponed (same reminder, multiple updates)
    # Detect by looking at reminders with dates far past
    stale = safe_query(
        NEXO_DB,
        "SELECT id, description, date FROM reminders "
        "WHERE status LIKE 'PENDIENTE%' AND date IS NOT NULL AND date < ? "
        "ORDER BY date ASC LIMIT 5",
        (TODAY_STR,),
    )
    if stale:
        lines.append(f"**Overdue reminders not attended:**")
        for r in stale:
            desc = truncate(r.get("description") or "", 80)
            lines.append(f"  - {r.get('id')} (overdue since {r.get('date')}): {desc}")

    if not lines:
        return "No observations on user patterns."

    return "\n".join(lines)


# ─── Log history ──────────────────────────────────────────────────────────────

def append_synthesis_log(entry: dict):
    log_data = []
    if SYNTHESIS_LOG.exists():
        try:
            log_data = json.loads(SYNTHESIS_LOG.read_text())
        except Exception:
            log_data = []
    log_data.append(entry)
    # Keep last 30 entries
    log_data = log_data[-30:]
    SYNTHESIS_LOG.write_text(json.dumps(log_data, ensure_ascii=False, indent=2))


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    log("NEXO Synthesis Engine starting.")

    if not should_run():
        sys.exit(0)

    lock_fd = acquire_lock()

    try:
        COORD_DIR.mkdir(parents=True, exist_ok=True)

        now = datetime.now()
        ts = now.strftime("%Y-%m-%d %H:%M")
        log("Querying databases...")

        s_learnings = section_learnings()
        s_decisions = section_decisions()
        s_changes = section_changes()
        s_patterns = section_patterns()
        s_manana = section_manana()
        s_autoeval = section_autoevaluacion()
        s_user_obs = section_user_observer()

        md = f"""# NEXO Daily Synthesis — {TODAY_STR}
Generated at {ts}

## Errors and Lessons (today)
{s_learnings}

## Decisions Made
{s_decisions}

## Systems Touched
{s_changes}

## Patterns Detected
{s_patterns}

## User — Observations
{s_user_obs}

## Tomorrow
{s_manana}

## Self-Evaluation
{s_autoeval}
"""

        OUTPUT_FILE.write_text(md, encoding="utf-8")
        log(f"Written: {OUTPUT_FILE}")

        line_count = len(md.splitlines())
        log(f"Output: {line_count} lines.")

        # Log history
        append_synthesis_log({
            "date": TODAY_STR,
            "generated_at": ts,
            "lines": line_count,
            "learnings_today": s_learnings.count("\n- ") + (1 if s_learnings.startswith("- ") else 0),
        })

        mark_done()
        log("Done.")

    except Exception as e:
        log(f"Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        release_lock(lock_fd)


if __name__ == "__main__":
    main()
