#!/usr/bin/env python3
"""
NEXO Post-Mortem Consolidator — Daily behavioral learning extraction.

Runs daily at 23:30 via LaunchAgent. Reads all session diaries from today,
extracts self_critique entries, identifies RECURRING behavioral patterns,
and writes permanent rules to memory files so they survive forever.

Three layers:
1. Session → self_critique field in session_diary (captured at session end)
2. Daily → this script consolidates all critiques from today
3. Permanent → writes to feedback_*.md files + MEMORY.md index

Only creates permanent memory for patterns that:
- Appear 2+ times in the same day, OR
- Appear in 3+ different days (checked against history), OR
- the user explicitly corrected NEXO (user_signals contains correction keywords)
"""

import json
import os
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, date, timedelta
from pathlib import Path

# Add nexo-mcp to path for cognitive engine
sys.path.insert(0, str(Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))))

HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
NEXO_DB = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo"))) / "nexo.db"
SESSION_BUFFER = HOME / "claude" / "brain" / "session_buffer.jsonl"
MEMORY_DIR = HOME / "brain"
MEMORY_INDEX = MEMORY_DIR / "MEMORY.md"
CONSOLIDATION_LOG = HOME / "claude" / "logs" / "postmortem-consolidation.log"
HISTORY_FILE = HOME / "claude" / "coordination" / "postmortem-history.json"

TODAY = date.today()
TODAY_STR = TODAY.isoformat()

CORRECTION_KEYWORDS = [
    "corrig", "frustrad", "no lo entiend", "exig", "repet",
    "no debería", "por qué no", "otra vez", "ya te dije",
    "cansando", "siempre espera", "no te adelant", "reactivo",
    "no haces", "error", "mal", "fallo", "irritad"
]


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    CONSOLIDATION_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(CONSOLIDATION_LOG, "a") as f:
        f.write(line + "\n")


def get_today_diaries() -> list[dict]:
    """Get all session diaries from today with self_critique."""
    if not NEXO_DB.exists():
        return []
    conn = sqlite3.connect(str(NEXO_DB))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, session_id, summary, self_critique, user_signals, mental_state, domain, created_at "
        "FROM session_diary WHERE date(created_at) = ? ORDER BY created_at",
        (TODAY_STR,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_historical_critiques(days: int = 30) -> list[dict]:
    """Get self_critique from the last N days for pattern detection."""
    if not NEXO_DB.exists():
        return []
    since = (TODAY - timedelta(days=days)).isoformat()
    conn = sqlite3.connect(str(NEXO_DB))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT self_critique, user_signals, created_at "
        "FROM session_diary WHERE date(created_at) >= ? AND self_critique != '' "
        "ORDER BY created_at",
        (since,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def has_correction_signals(signals: str) -> bool:
    """Check if user_signals indicate corrections."""
    if not signals:
        return False
    lower = signals.lower()
    return any(kw in lower for kw in CORRECTION_KEYWORDS)


def extract_actionable_rules(critiques: list[str]) -> list[str]:
    """Extract concrete, actionable rules from self-critique text."""
    rules = []
    for critique in critiques:
        if not critique or critique.strip().lower().startswith("sin autocrítica"):
            continue
        # Each non-empty critique is a potential rule
        # Clean up and normalize
        for line in critique.split("\n"):
            line = line.strip().lstrip("- ").strip()
            if len(line) > 20 and not line.lower().startswith("sin "):
                rules.append(line)
    return rules


def load_history() -> dict:
    """Load consolidation history to detect recurring patterns."""
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text())
        except Exception:
            return {"days": {}, "permanent_rules": []}
    return {"days": {}, "permanent_rules": []}


def save_history(history: dict):
    """Save consolidation history."""
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Keep last 90 days
    cutoff = (TODAY - timedelta(days=90)).isoformat()
    history["days"] = {k: v for k, v in history["days"].items() if k >= cutoff}
    HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2))


def rule_already_permanent(rule: str, history: dict) -> bool:
    """Check if a similar rule is already in permanent memory."""
    rule_lower = rule.lower()
    for existing in history.get("permanent_rules", []):
        # Simple similarity: if >60% of words overlap
        existing_words = set(existing.lower().split())
        rule_words = set(rule_lower.split())
        if not rule_words:
            return True
        overlap = len(existing_words & rule_words) / len(rule_words)
        if overlap > 0.6:
            return True
    return False


def write_permanent_rule(rule_title: str, rule_content: str, source_critiques: list[str]):
    """Write a new permanent feedback memory file."""
    # Generate filename
    slug = re.sub(r'[^a-z0-9]+', '_', rule_title.lower())[:50].strip('_')
    filename = f"feedback_postmortem_{slug}.md"
    filepath = MEMORY_DIR / filename

    if filepath.exists():
        log(f"  File already exists: {filename}, skipping")
        return None

    content = f"""---
name: {rule_title}
description: Regla de comportamiento extraída de autocrítica post-mortem — patrón recurrente detectado
type: feedback
---

{rule_content}

**Why:** Patrón detectado en múltiples sesiones donde NEXO falló en este aspecto. the user no debería tener que corregir lo mismo dos veces.

**How to apply:** Verificar esta regla al inicio de cada sesión y antes de presentar trabajo como completado.

**Evidencia (autocríticas originales):**
"""
    for i, critique in enumerate(source_critiques[:3], 1):
        content += f"- Sesión {i}: {critique[:200]}\n"

    filepath.write_text(content)
    log(f"  Written permanent rule: {filename}")
    return filename


def process_sensory_register():
    """
    Sensory Register — Atkinson-Shiffrin Layer 1.
    Reads today's session_buffer events, embeds them, compares against LTM
    to detect recurring patterns. Ingests meaningful events into STM as 'sensory'.
    """
    log("--- Sensory Register processing ---")

    if not SESSION_BUFFER.exists():
        log("  No session_buffer.jsonl found, skipping sensory processing")
        return

    # Read today's events from session_buffer
    today_events = []
    try:
        with open(SESSION_BUFFER, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    ts = event.get("ts", "")
                    if ts.startswith(TODAY_STR):
                        today_events.append(event)
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        log(f"  Error reading session_buffer: {e}")
        return

    if not today_events:
        log("  No events from today in session_buffer")
        return

    log(f"  Found {len(today_events)} events from today")

    # Import cognitive engine
    try:
        import cognitive
    except ImportError as e:
        log(f"  Cannot import cognitive engine: {e}")
        return

    # Process events — only embed meaningful ones (not hook-fallback noise)
    ingested = 0
    pattern_flags = []

    for event in today_events:
        tasks = event.get("tasks", [])
        decisions = event.get("decisions", [])
        errors = event.get("errors_resolved", [])
        user_var = event.get("user_patterns", [])
        critique = event.get("self_critique", "")
        source = event.get("source", "")

        # Skip empty hook-fallback events
        if source == "hook-fallback" and not decisions and not errors and not user_pats:
            # Still embed if there are meaningful tasks (not just tool lists)
            task_str = " ".join(tasks) if tasks else ""
            if len(task_str) < 50 or "," in task_str:  # tool lists have commas
                continue

        # Build content for embedding
        parts = []
        if tasks:
            parts.append(f"Tasks: {'; '.join(tasks[:5])}")
        if decisions:
            parts.append(f"Decisions: {'; '.join(str(d) for d in decisions[:3])}")
        if errors:
            parts.append(f"Errors resolved: {'; '.join(str(e) for e in errors[:3])}")
        if user_pats:
            parts.append(f"the user patterns: {'; '.join(str(p) for p in user_pats[:3])}")
        if critique and "hook-fallback" not in critique:
            parts.append(f"Self-critique: {critique[:200]}")

        content = " | ".join(parts)
        if not content or len(content) < 20:
            continue

        # Embed and check against LTM for patterns
        try:
            vec = cognitive.embed(content)
            patterns = cognitive.detect_patterns(vec, threshold=0.65)

            if patterns:
                pattern_flags.append({
                    "event_ts": event.get("ts", ""),
                    "content": content[:200],
                    "matches": patterns[:3],
                })

            # Ingest into STM as sensory
            domain = ""
            if any(w in content.lower() for w in ["frontend", "extension", "ui"]):
                domain = "frontend"
            elif any(w in content.lower() for w in ["ecommerce", "shop", "store"]):
                domain = "ecommerce"
            elif any(w in content.lower() for w in ["nexo", "cognitive", "guard"]):
                domain = "nexo"
            elif any(w in content.lower() for w in ["client", "customer"]):
                domain = "client"

            cognitive.ingest_sensory(
                content=content,
                source_id=f"buffer#{event.get('ts', '')}",
                domain=domain,
                created_at=event.get("ts", "")
            )
            ingested += 1
        except Exception as e:
            log(f"  Error embedding event: {e}")
            continue

    log(f"  Ingested {ingested} sensory events into STM")

    # Report pattern matches (potential recurring behaviors)
    if pattern_flags:
        log(f"  PATTERN ALERT: {len(pattern_flags)} events matched existing LTM memories (potential repetitions)")
        for pf in pattern_flags[:5]:
            best = pf["matches"][0]
            log(f"    [{best['score']:.2f}] Event: {pf['content'][:80]}...")
            log(f"           Matches LTM {best['source_type']}: {best['content'][:80]}...")

    # Archive: compress old events from buffer (>48h)
    archive_sensory_buffer()

    return {"ingested": ingested, "patterns": len(pattern_flags)}


def archive_sensory_buffer():
    """Move events older than 48h from session_buffer to daily archive files."""
    if not SESSION_BUFFER.exists():
        return

    cutoff = (datetime.now() - timedelta(hours=48)).isoformat()
    archive_dir = HOME / "claude" / "brain" / "session_archive"
    archive_dir.mkdir(parents=True, exist_ok=True)

    keep_lines = []
    archived_by_day = {}

    try:
        with open(SESSION_BUFFER, "r") as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    event = json.loads(stripped)
                    ts = event.get("ts", "")
                    if ts < cutoff:
                        day = ts[:10]
                        archived_by_day.setdefault(day, []).append(stripped)
                    else:
                        keep_lines.append(stripped)
                except json.JSONDecodeError:
                    keep_lines.append(stripped)

        # Write archived events to daily files
        total_archived = 0
        for day, lines in archived_by_day.items():
            archive_file = archive_dir / f"{day}.jsonl"
            with open(archive_file, "a") as f:
                for line in lines:
                    f.write(line + "\n")
            total_archived += len(lines)

        # Rewrite buffer with only recent events
        if total_archived > 0:
            with open(SESSION_BUFFER, "w") as f:
                for line in keep_lines:
                    f.write(line + "\n")
            log(f"  Archived {total_archived} events (>48h) to session_archive/")
        else:
            log(f"  No events to archive (all within 48h)")

    except Exception as e:
        log(f"  Error archiving sensory buffer: {e}")


def analyze_force_events():
    """Analyze --force dissonance resolutions from today.

    When the user uses --force, NEXO obeyed without discussion. The nocturnal
    process must now ask: was the old memory wrong, or was the user taking
    conscious technical debt?

    If a --force exception targets the same memory multiple times → it's probably
    a paradigm shift, not an exception. Flag for morning review.
    """
    log("--- Force event analysis ---")

    try:
        import cognitive
    except ImportError:
        log("  Cannot import cognitive engine, skipping")
        return

    db = cognitive._get_db()
    today_forces = db.execute(
        """SELECT memory_id, context, created_at
           FROM memory_corrections
           WHERE correction_type = 'exception'
             AND context LIKE '%[FORCE]%'
             AND date(created_at) = ?
           ORDER BY created_at""",
        (TODAY_STR,)
    ).fetchall()

    if not today_forces:
        log("  No --force events today")
        return

    log(f"  {len(today_forces)} --force events today")

    # Count how many times each memory was force-overridden
    from collections import Counter
    memory_counts = Counter(r["memory_id"] for r in today_forces)

    for mem_id, count in memory_counts.most_common():
        mem = db.execute(
            "SELECT content, source_type, strength, domain FROM ltm_memories WHERE id = ?",
            (mem_id,)
        ).fetchone()
        if not mem:
            continue

        # Check total force-overrides for this memory (all time)
        total_overrides = db.execute(
            "SELECT COUNT(*) FROM memory_corrections WHERE memory_id = ? AND context LIKE '%[FORCE]%'",
            (mem_id,)
        ).fetchone()[0]

        if total_overrides >= 3:
            log(f"  PARADIGM SHIFT CANDIDATE: LTM #{mem_id} force-overridden {total_overrides}x total")
            log(f"    Content: {mem['content'][:120]}")
            log(f"    Action: Decaying strength from {mem['strength']:.2f} to 0.3")
            # Auto-decay — if it's been overridden 3+ times, the user clearly disagrees
            db.execute(
                "UPDATE ltm_memories SET strength = 0.3, tags = CASE WHEN tags LIKE '%paradigm_candidate%' THEN tags ELSE tags || ',paradigm_candidate' END WHERE id = ?",
                (mem_id,)
            )
        elif count >= 2:
            log(f"  WATCH: LTM #{mem_id} force-overridden {count}x today ({total_overrides}x total)")
            log(f"    Content: {mem['content'][:120]}")
        else:
            log(f"  OK: LTM #{mem_id} force-overridden once (total: {total_overrides})")

    db.commit()


def main():
    log("=== NEXO Post-Mortem Consolidator starting ===")

    diaries = get_today_diaries()
    if not diaries:
        log("No session diaries today. Nothing to consolidate.")
        return

    log(f"Found {len(diaries)} session diaries today.")

    # Collect critiques and signals
    today_critiques = []
    correction_critiques = []

    for d in diaries:
        critique = d.get("self_critique") or ""
        signals = d.get("user_signals") or ""

        if critique and not critique.strip().lower().startswith("sin autocrítica"):
            today_critiques.append(critique)

        if has_correction_signals(signals):
            correction_critiques.append({
                "critique": critique,
                "signals": signals,
                "domain": d.get("domain", ""),
            })

    log(f"  {len(today_critiques)} non-trivial critiques, {len(correction_critiques)} with correction signals")

    if not today_critiques and not correction_critiques:
        log("All sessions clean. Nothing to consolidate.")
        return

    # Load history
    history = load_history()

    # Save today's rules to history
    today_rules = extract_actionable_rules(today_critiques)
    history["days"][TODAY_STR] = {
        "rules": today_rules,
        "corrections": len(correction_critiques),
        "total_sessions": len(diaries),
    }

    # Detect patterns that should become permanent
    new_permanent = []

    # Pattern 1: Same critique appears 2+ times TODAY
    if len(today_rules) >= 2:
        # Simple word-bag similarity between rules
        for i, rule in enumerate(today_rules):
            for j, other in enumerate(today_rules):
                if i >= j:
                    continue
                words_i = set(rule.lower().split())
                words_j = set(other.lower().split())
                if words_i and words_j:
                    overlap = len(words_i & words_j) / min(len(words_i), len(words_j))
                    if overlap > 0.5 and not rule_already_permanent(rule, history):
                        new_permanent.append({
                            "title": f"Patrón repetido: {rule[:60]}",
                            "content": f"Detectado 2+ veces en el mismo día:\n- {rule}\n- {other}",
                            "sources": [rule, other],
                        })

    # Pattern 2: Rule appears across 3+ different days
    all_historical_rules = []
    for day_str, day_data in history["days"].items():
        if day_str == TODAY_STR:
            continue
        for rule in day_data.get("rules", []):
            all_historical_rules.append((day_str, rule))

    for today_rule in today_rules:
        matching_days = set()
        today_words = set(today_rule.lower().split())
        for hist_day, hist_rule in all_historical_rules:
            hist_words = set(hist_rule.lower().split())
            if today_words and hist_words:
                overlap = len(today_words & hist_words) / min(len(today_words), len(hist_words))
                if overlap > 0.4:
                    matching_days.add(hist_day)

        if len(matching_days) >= 2 and not rule_already_permanent(today_rule, history):  # 2 historical + today = 3
            new_permanent.append({
                "title": f"Patrón recurrente ({len(matching_days)+1} días): {today_rule[:50]}",
                "content": f"Detectado en {len(matching_days)+1} días diferentes:\n- Hoy: {today_rule}\n- Días previos: {', '.join(sorted(matching_days)[:5])}",
                "sources": [today_rule],
            })

    # Pattern 3: the user corrected AND there's a critique → always promote
    for cc in correction_critiques:
        critique = cc.get("critique", "")
        if critique and not rule_already_permanent(critique, history):
            new_permanent.append({
                "title": f"Corrección the user: {critique[:50]}",
                "content": f"the user corrigió explícitamente este comportamiento.\nSeñales: {cc['signals'][:200]}\nAutocrítica: {critique[:300]}",
                "sources": [critique],
            })

    # Write permanent rules
    if new_permanent:
        log(f"Promoting {len(new_permanent)} patterns to permanent memory:")
        for rule in new_permanent:
            filename = write_permanent_rule(rule["title"], rule["content"], rule["sources"])
            if filename:
                history.setdefault("permanent_rules", []).append(rule["title"])
    else:
        log("No patterns qualify for permanent promotion today.")

    # Write daily summary to synthesis
    summary_file = HOME / "claude" / "coordination" / "postmortem-daily.md"
    summary_lines = [
        f"# Post-Mortem Daily — {TODAY_STR}",
        f"Sesiones: {len(diaries)} | Autocríticas: {len(today_critiques)} | Correcciones the user: {len(correction_critiques)}",
        "",
    ]
    if today_critiques:
        summary_lines.append("## Autocríticas del día")
        for c in today_critiques:
            summary_lines.append(f"- {c[:200]}")
        summary_lines.append("")
    if new_permanent:
        summary_lines.append("## Promovido a memoria permanente")
        for r in new_permanent:
            summary_lines.append(f"- {r['title']}")
    else:
        summary_lines.append("## Nada promovido hoy")

    summary_file.write_text("\n".join(summary_lines))
    log(f"Written daily summary: {summary_file}")

    save_history(history)

    # Phase 2: Sensory Register processing
    try:
        process_sensory_register()
    except Exception as e:
        log(f"Sensory register processing failed: {e}")

    # Phase 3: Analyze --force dissonance events from today
    try:
        analyze_force_events()
    except Exception as e:
        log(f"Force event analysis failed: {e}")

    # Register successful run for catch-up
    try:
        state_file = HOME / "claude" / "operations" / ".catchup-state.json"
        state = json.loads(state_file.read_text()) if state_file.exists() else {}
        state["postmortem"] = datetime.now().isoformat()
        state_file.write_text(json.dumps(state, indent=2))
    except Exception:
        pass

    log("=== Consolidation complete ===")


if __name__ == "__main__":
    main()
