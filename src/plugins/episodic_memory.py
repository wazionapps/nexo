"""Episodic memory plugin — decisions, session diary, and reasoning traces."""

import datetime
import json
import time
from db import (
    log_decision, update_decision_outcome, search_decisions,
    write_session_diary, read_session_diary,
    log_change, search_changes, update_change_commit,
    recall, get_db,
)


def _cognitive_ingest_safe(content, source_type, source_id="", source_title="", domain=""):
    """Ingest to cognitive STM. Silently fails if cognitive engine unavailable."""
    try:
        import cognitive
        cognitive.ingest(content, source_type, source_id, source_title, domain)
    except Exception:
        pass  # Cognitive is optional — never block operational writes


def handle_decision_log(domain: str, decision: str, alternatives: str = '',
                        based_on: str = '', confidence: str = 'medium',
                        context_ref: str = '', session_id: str = '',
                        review_days: int = 14) -> str:
    """Log a non-trivial decision with reasoning context.

    Args:
        domain: Area (ads, shopify, server, nexo, other)
        decision: What was decided
        alternatives: JSON array or text of options considered and why discarded
        based_on: Data, metrics, or observations that informed this decision
        confidence: high, medium, or low
        context_ref: Related followup/reminder ID (e.g., NF-ADS1, R71)
        session_id: Current session ID (auto-filled if empty)
    """
    valid_domains = {'ads', 'shopify', 'server', 'nexo', 'other'}
    if domain not in valid_domains:
        return f"ERROR: domain debe ser uno de: {', '.join(sorted(valid_domains))}"
    if confidence not in ('high', 'medium', 'low'):
        return f"ERROR: confidence debe ser high, medium, o low"

    sid = session_id or 'unknown'
    result = log_decision(sid, domain, decision, alternatives, based_on, confidence, context_ref)
    if "error" in result:
        return f"ERROR: {result['error']}"
    _cognitive_ingest_safe(
        f"Decision: {decision}. Alternatives: {alternatives}. Based on: {based_on}",
        "decision", f"D{result.get('id','')}", (decision or '')[:80], domain
    )
    conn = get_db()
    due = (datetime.datetime.now() + datetime.timedelta(days=max(1, int(review_days)))).isoformat(timespec='seconds')
    conn.execute(
        "UPDATE decisions SET status = ?, review_due_at = ? WHERE id = ?",
        ("pending_review", due, result["id"])
    )
    conn.commit()
    # KG hook
    try:
        from kg_populate import on_decision_log
        on_decision_log(result["id"], domain, decision)
    except Exception:
        pass
    result = dict(conn.execute("SELECT * FROM decisions WHERE id = ?", (result["id"],)).fetchone())
    due = result.get("review_due_at", "")
    due_str = f" review_due={due}" if due else ""
    return f"Decision #{result['id']} registrada [{domain}] ({confidence}): {decision[:80]}{due_str}"


def handle_decision_outcome(id: int, outcome: str) -> str:
    """Record what actually happened after a past decision.

    Args:
        id: Decision ID number
        outcome: What happened — was the decision correct? What changed?
    """
    result = update_decision_outcome(id, outcome)
    if "error" in result:
        return f"ERROR: {result['error']}"
    conn = get_db()
    conn.execute(
        "UPDATE decisions SET status = 'reviewed', review_due_at = NULL, last_reviewed_at = datetime('now') WHERE id = ?",
        (id,)
    )
    conn.commit()
    return f"Decision #{id} outcome registrado: {outcome[:100]}"


def handle_decision_search(query: str = '', domain: str = '', days: int = 30) -> str:
    """Search past decisions to answer 'why did we do X?'

    Args:
        query: Text to search in decision, alternatives, based_on, outcome
        domain: Filter by area (ads, shopify, server, nexo, other)
        days: Look back N days (default 30)
    """
    valid_domains = {'ads', 'shopify', 'server', 'nexo', 'other'}
    if domain and domain not in valid_domains:
        return f"ERROR: domain debe ser uno de: {', '.join(sorted(valid_domains))}"
    results = search_decisions(query, domain, days)
    if not results:
        scope = f"'{query}'" if query else domain or 'todas'
        return f"Sin decisiones encontradas para {scope} in {days} days."

    lines = [f"DECISIONES ({len(results)}):"]
    for d in results:
        conf = d.get('confidence', '?')
        outcome_str = f" → {d['outcome'][:50]}" if d.get('outcome') else ""
        ref = f" [{d['context_ref']}]" if d.get('context_ref') else ""
        status = d.get('status', 'pending_review')
        review_due = f" due={d['review_due_at']}" if d.get('review_due_at') else ""
        lines.append(f"  #{d['id']} ({d['created_at']}) [{d['domain']}] {conf} [{status}]{ref}{review_due}")
        lines.append(f"    {d['decision'][:120]}")
        if d.get('based_on'):
            lines.append(f"    Basado en: {d['based_on'][:100]}")
        if d.get('alternatives'):
            lines.append(f"    Alternativas: {d['alternatives'][:100]}")
        if outcome_str:
            lines.append(f"    Outcome:{outcome_str}")
    return "\n".join(lines)


def handle_memory_review_queue(days: int = 0) -> str:
    """Show decisions and learnings that are due for review.

    Args:
        days: Include items due within N future days (default only overdue/today)
    """
    conn = get_db()
    now_epoch = time.time() + (max(0, int(days)) * 86400)
    now_iso = (datetime.datetime.now() + datetime.timedelta(days=max(0, int(days)))).isoformat(timespec='seconds')
    learnings = [dict(r) for r in conn.execute(
        "SELECT * FROM learnings WHERE review_due_at IS NOT NULL AND status != 'superseded' AND review_due_at <= ? ORDER BY review_due_at ASC, updated_at DESC",
        (now_epoch,)
    ).fetchall()]
    decisions = [dict(r) for r in conn.execute(
        "SELECT * FROM decisions WHERE review_due_at IS NOT NULL AND status != 'reviewed' AND review_due_at <= ? ORDER BY review_due_at ASC, created_at DESC",
        (now_iso,)
    ).fetchall()]
    if not learnings and not decisions:
        return f"No memory reviews due within {days} day(s)."

    lines = [f"MEMORY REVIEW QUEUE (days={days}):"]
    if decisions:
        lines.append(f"  Decisions ({len(decisions)}):")
        for d in decisions[:10]:
            lines.append(f"    #{d['id']} [{d.get('domain','other')}] {d['decision'][:90]}")
            if d.get("review_due_at"):
                lines.append(f"      due={d['review_due_at']} status={d.get('status','pending_review')}")
    if learnings:
        lines.append(f"  Learnings ({len(learnings)}):")
        for l in learnings[:10]:
            lines.append(f"    #{l['id']} [{l.get('category','general')}] {l['title'][:90]}")
            due = l.get("review_due_at")
            due_str = f"{due:.0f}" if isinstance(due, (int, float)) and due else str(due or "")
            lines.append(f"      due={due_str} status={l.get('status','active')}")
            if l.get("prevention"):
                lines.append(f"      prevention={str(l['prevention'])[:100]}")
    return "\n".join(lines)


def handle_session_diary_write(decisions: str, summary: str,
                                discarded: str = '', pending: str = '',
                                context_next: str = '', mental_state: str = '',
                                user_signals: str = '',
                                domain: str = '',
                                session_id: str = '',
                                self_critique: str = '') -> str:
    """Write session diary entry at end of session. OBLIGATORIO antes de cerrar.

    Args:
        decisions: What was decided and why (JSON array or structured text)
        summary: 2-3 line summary of the session
        discarded: Options/approaches considered but rejected, and why
        pending: Items left unresolved, with doubt level
        context_next: What the next session should know to continue effectively
        mental_state: Internal state to transfer — thread of thought, tone, observations not yet shared, momentum. Written in first person as NEXO.
        user_signals: Observable signals from User during session — response speed (fast='s' vs detailed explanations), tone (direct, frustrated, exploratory, excited), corrections given, topics he initiated vs topics NEXO initiated. Factual observations only, not interpretations.
        domain: Project context: project-a, project-b, nexo, server, other
        session_id: Current session ID
        self_critique: OBLIGATORIO. Post-mortem honesto: ¿Qué debí hacer proactivamente? ¿User tuvo que pedirme algo que yo debería haber detectado? ¿Repetí errores conocidos? ¿Qué regla concreta evitaría la repetición? Si sesión limpia: 'Sin autocrítica — sesión limpia.'
    """
    sid = session_id or 'unknown'
    # Clean up draft — manual diary supersedes it
    from db import delete_diary_draft
    delete_diary_draft(sid)
    result = write_session_diary(sid, decisions, summary, discarded, pending, context_next, mental_state, domain=domain, user_signals=user_signals, self_critique=self_critique)
    if "error" in result:
        return f"ERROR: {result['error']}"
    _cognitive_ingest_safe(summary, "diary", f"diary#{result.get('id','')}", f"Session {sid} summary", domain)
    if self_critique and self_critique.strip():
        _cognitive_ingest_safe(self_critique, "critique", f"diary#{result.get('id','')}", f"Session {sid} critique", domain)
    if mental_state and mental_state.strip():
        _cognitive_ingest_safe(mental_state, "mental_state", f"diary#{result.get('id','')}", f"Session {sid} state", domain)
    domain_str = f" [{domain}]" if domain else ""
    msg = f"Diario sesión #{result['id']}{domain_str} guardado: {summary[:80]}"

    # Trust score & sentiment summary for session diary
    try:
        import cognitive
        trust = cognitive.get_trust_score()
        history = cognitive.get_trust_history(days=1)
        net = history.get("net_change", 0)
        sentiment_dist = history.get("sentiment_distribution", {})
        vibe = max(sentiment_dist, key=lambda k: sentiment_dist[k]["count"]) if sentiment_dist else "neutral"
        msg += f"\nScore: {trust:.0f}/100 ({net:+.0f}) | Vibe: {vibe}"
    except Exception:
        pass

    # Episodic memory audit — warn about gaps
    warnings = []
    conn = __import__('db').get_db()
    orphan_changes = conn.execute(
        "SELECT COUNT(*) FROM change_log WHERE (commit_ref IS NULL OR commit_ref = '')"
    ).fetchone()[0]
    if orphan_changes > 0:
        warnings.append(f"{orphan_changes} changes sin commit_ref")
    orphan_decisions = conn.execute(
        "SELECT COUNT(*) FROM decisions WHERE (outcome IS NULL OR outcome = '') AND created_at < datetime('now', '-7 days')"
    ).fetchone()[0]
    if orphan_decisions > 0:
        warnings.append(f"{orphan_decisions} decisions >7d sin outcome")
    if warnings:
        msg += "\n⚠ EPISODIC GAPS: " + " | ".join(warnings) + " — resolver antes de cerrar sesión."

    return msg


def handle_session_diary_read(session_id: str = '', last_n: int = 3, last_day: bool = False,
                               domain: str = '') -> str:
    """Read recent session diaries for context continuity.

    Args:
        session_id: Specific session ID to read (optional)
        last_n: Number of recent entries to return (default 3)
        last_day: If true, returns ALL entries from the most recent day (multi-terminal aware). Use this at startup.
        domain: Filter by project context: project-a, project-b, nexo, server, other
    """
    results = read_session_diary(session_id, last_n, last_day, domain)
    if not results:
        return "Sin entradas en el diario de sesiones."

    lines = [f"DIARIO DE SESIONES ({len(results)}):"]
    for d in results:
        domain_label = f" [{d['domain']}]" if d.get('domain') else ""
        lines.append(f"\n  --- Sesión {d['session_id']}{domain_label} ({d['created_at']}) ---")
        lines.append(f"  Resumen: {d['summary']}")
        if d.get('decisions'):
            lines.append(f"  Decisiones: {d['decisions'][:200]}")
        if d.get('discarded'):
            lines.append(f"  Descartado: {d['discarded'][:150]}")
        if d.get('pending'):
            lines.append(f"  Pendiente: {d['pending'][:150]}")
        if d.get('context_next'):
            lines.append(f"  Para siguiente sesión: {d['context_next'][:200]}")
        if d.get('mental_state'):
            lines.append(f"  Estado mental: {d['mental_state'][:300]}")
        if d.get('user_signals'):
            lines.append(f"  Señales User: {d['user_signals'][:300]}")
    return "\n".join(lines)


def handle_change_log(files: str, what_changed: str, why: str,
                      triggered_by: str = '', affects: str = '',
                      risks: str = '', verify: str = '',
                      commit_ref: str = '', session_id: str = '') -> str:
    """Log a code/config change with full context. OBLIGATORIO after every edit to production code.

    Args:
        files: File path(s) modified (comma-separated if multiple)
        what_changed: What was modified — functions, lines, behavior change
        why: WHY this change was needed — the root cause, not just "fix bug"
        triggered_by: What triggered this — bug report, metric, User's request, followup ID
        affects: What systems/users/flows this change impacts
        risks: What could go wrong — regressions, edge cases, dependencies
        verify: How to verify this works — what to check, followup ID if created
        commit_ref: Git commit hash (can be added later with nexo_change_commit)
        session_id: Current session ID
    """
    if not files or not what_changed or not why:
        return "ERROR: files, what_changed, y why son obligatorios"
    sid = session_id or 'unknown'
    result = log_change(sid, files, what_changed, why, triggered_by, affects, risks, verify, commit_ref)
    if "error" in result:
        return f"ERROR: {result['error']}"
    _cognitive_ingest_safe(
        f"{what_changed}. Why: {why}",
        "change", f"C{result.get('id','')}", (what_changed or '')[:80], ""
    )
    change_id = result['id']
    # KG hook
    try:
        from kg_populate import on_change_log
        on_change_log(change_id, files, "")
    except Exception:
        pass
    msg = f"Change #{change_id} registrado: {files[:60]} — {what_changed[:60]}"
    if not commit_ref:
        msg += f"\n⚠ SIN COMMIT. Usa nexo_change_commit({change_id}, 'hash') después del push, o 'server-direct' si fue edición directa en servidor."
    return msg


def handle_change_search(query: str = '', files: str = '', days: int = 30) -> str:
    """Search past code changes — answers 'what did we change in X?' or 'why did we touch Y?'

    Args:
        query: Text to search in what_changed, why, affects, triggered_by
        files: Filter by file path (partial match)
        days: Look back N days (default 30)
    """
    results = search_changes(query, files, days)
    if not results:
        scope = f"'{query}'" if query else files or 'todos'
        return f"No changes found for {scope} in {days} days."

    lines = [f"CAMBIOS ({len(results)}):"]
    for c in results:
        commit = f" [{c['commit_ref'][:8]}]" if c.get('commit_ref') else ""
        lines.append(f"  #{c['id']} ({c['created_at']}){commit}")
        lines.append(f"    Archivos: {c['files'][:100]}")
        lines.append(f"    Qué: {c['what_changed'][:120]}")
        lines.append(f"    Por qué: {c['why'][:120]}")
        if c.get('triggered_by'):
            lines.append(f"    Trigger: {c['triggered_by'][:80]}")
        if c.get('affects'):
            lines.append(f"    Afecta: {c['affects'][:80]}")
        if c.get('risks'):
            lines.append(f"    Riesgos: {c['risks'][:80]}")
    return "\n".join(lines)


def handle_change_commit(id: int, commit_ref: str) -> str:
    """Link a change log entry to its git commit hash after committing.

    Args:
        id: Change log entry ID
        commit_ref: Git commit hash
    """
    result = update_change_commit(id, commit_ref)
    if "error" in result:
        return f"ERROR: {result['error']}"
    return f"Change #{id} vinculado a commit {commit_ref[:8]}"


def handle_recall(query: str, days: int = 30) -> str:
    """Search across ALL memory — changes, decisions, learnings, followups, diary. One query to find anything.

    Args:
        query: Text to search across all memory tables
        days: Look back N days (default 30)
    """
    results = recall(query, days)
    if not results:
        return f"Sin resultados para '{query}' en los últimos {days} días."

    # v1.2: Passive rehearsal — strengthen matching cognitive memories
    try:
        import cognitive
        for r in results[:5]:
            title = str(r.get('title', ''))
            snippet = str(r.get('snippet', ''))
            cognitive.rehearse_by_content(f"{title} {snippet[:200]}")
    except Exception:
        pass

    SOURCE_LABELS = {
        'change_log': '[CAMBIO]',
        'change':     '[CAMBIO]',
        'decision':   '[DECISIÓN]',
        'learning':   '[LEARNING]',
        'followup':   '[FOLLOWUP]',
        'diary':      '[DIARIO]',
        'entity':     '[ENTIDAD]',
        'file':       '[ARCHIVO]',
        'code':       '[CÓDIGO]',
    }

    lines = [f"RECALL '{query}' — {len(results)} resultado(s):"]
    for r in results:
        source = r.get('source', '?')
        label = SOURCE_LABELS.get(source, f"[{source.upper()}]")
        sid = r.get('source_id', r.get('id', '?'))
        title = str(r.get('title', ''))[:120]
        snippet = str(r.get('snippet', ''))[:200].strip(' |')
        cat = r.get('category', '')
        cat_str = f" ({cat})" if cat else ''
        lines.append(f"\n  {label} #{sid}{cat_str}")
        lines.append(f"    {title}")
        if snippet:
            lines.append(f"    {snippet}")
    if len(results) < 5:
        lines.append(f"\n  💡 Solo {len(results)} resultados en NEXO. Para historial más profundo, busca también en claude-mem: mcp__plugin_claude-mem_mcp-search__search")
    return "\n".join(lines)


TOOLS = [
    (handle_change_log, "nexo_change_log", "Log a code/config change with full context: what, why, trigger, affects, risks"),
    (handle_change_search, "nexo_change_search", "Search past code changes — answers 'what did we change in X?' or 'why did we touch Y?'"),
    (handle_change_commit, "nexo_change_commit", "Link a change log entry to its git commit hash"),
    (handle_decision_log, "nexo_decision_log", "Log a non-trivial decision with reasoning, alternatives, and evidence"),
    (handle_decision_outcome, "nexo_decision_outcome", "Record what actually happened after a past decision"),
    (handle_decision_search, "nexo_decision_search", "Search past decisions — answers 'why did we do X?'"),
    (handle_memory_review_queue, "nexo_memory_review_queue", "Show decisions and learnings that are due for review"),
    (handle_session_diary_write, "nexo_session_diary_write", "Write end-of-session diary with decisions, discards, and context for next session"),
    (handle_session_diary_read, "nexo_session_diary_read", "Read recent session diaries for context continuity"),
    (handle_recall, "nexo_recall", "Search across ALL NEXO memory — changes, decisions, learnings, followups, diary, entities, .md files, code files. For deep historical context (older sessions, past work), also search claude-mem (mcp__plugin_claude-mem_mcp-search__search)."),
]
