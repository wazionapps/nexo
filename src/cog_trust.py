"""NEXO Cognitive — Trust scoring, sentiment detection, dissonance."""
import re
import numpy as np
from datetime import datetime, timedelta


def _get_db():
    import cognitive
    return cognitive._get_db()


def _embed(text):
    import cognitive
    return cognitive.embed(text)


def _cosine_similarity(a, b):
    import cognitive
    return cognitive.cosine_similarity(a, b)


def _blob_to_array(blob):
    import cognitive
    return cognitive._blob_to_array(blob)


# Sentiment detection keywords
POSITIVE_SIGNALS = {
    "gracias", "genial", "perfecto", "bien", "excelente", "bueno", "me gusta",
    "correcto", "sí", "dale", "hazlo", "adelante", "ok", "vale", "great",
    "nice", "good", "exactly", "buen trabajo", "bien hecho", "fenomenal",
}
NEGATIVE_SIGNALS = {
    "no", "mal", "otra vez", "ya te dije", "frustr", "error", "fallo",
    "cansad", "siempre", "nunca", "por qué no", "no funciona", "roto",
    "no sirve", "horrible", "desastre", "qué coño", "joder", "mierda",
    "hostia", "me cago", "irritad", "harto",
    "broken", "nothing works", "doesn't work", "not working", "fix it",
    "wrong", "failed", "failing", "annoying", "frustrated", "damn", "shit",
    "wtf", "terrible", "useless", "stupid", "hate", "worst", "sucks",
    "again",
}
URGENCY_SIGNALS = {
    "rápido", "ya", "ahora", "urgente", "asap", "inmediatamente", "corre",
}

# Trust score events — default deltas (overridable via trust_event_config table)
_DEFAULT_TRUST_EVENTS = {
    # Positive
    "explicit_thanks": +3,
    "delegation": +2,        # Francisco delegates new task without micromanaging
    "paradigm_shift": +2,    # Francisco teaches, NEXO learns
    "sibling_detected": +3,  # NEXO avoided context error on its own
    "proactive_action": +2,  # NEXO did something useful without being asked
    # Negative
    "correction": -3,        # Francisco corrects NEXO
    "repeated_error": -7,    # Error on something NEXO already had a learning for
    "override": -5,          # NEXO's memory was wrong
    "correction_fatigue": -10, # Same memory corrected 3+ times
    "forgot_followup": -4,   # Forgot to mark followup or execute it
}

# Lazy-loaded from DB (trust_event_config table overrides defaults)
_trust_events_cache = None
_trust_events_cache_ts = 0

# Auto-detection patterns for trust events from user text
# Each pattern: (event_name, keywords/phrases that trigger it, min_matches)
TRUST_AUTO_PATTERNS = {
    "explicit_thanks": {
        "patterns": [
            "gracias", "buen trabajo", "bien hecho", "perfecto", "genial",
            "excelente", "fenomenal", "great job", "nice work", "thank",
            "thanks", "awesome", "amazing", "love it", "me encanta",
        ],
        "min_matches": 1,
    },
    "correction": {
        "patterns": [
            "ya te dije", "ya te lo dije", "otra vez", "te he dicho",
            "no es así", "eso no", "mal", "incorrecto", "equivocado",
            "no no no", "that's wrong", "te aviso", "te avisé",
            "2ª vez", "segunda vez", "te lo repito",
        ],
        "min_matches": 1,
    },
    "repeated_error": {
        "patterns": [
            "otra vez lo mismo", "siempre igual", "ya te lo dije antes",
            "cuántas veces", "no aprendes", "same mistake", "again the same",
            "ya van", "es la 2", "es la 3", "ya te avisé",
        ],
        "min_matches": 1,
    },
    "delegation": {
        "patterns": [
            "encárgate", "hazlo tú", "dale tú", "te lo dejo",
            "manéjalo", "resuélvelo", "handle it", "take care of",
            "you decide", "tú decides", "lo que veas", "como veas",
        ],
        "min_matches": 1,
    },
}


def get_trust_events() -> dict:
    """Get trust events with deltas. DB overrides take priority over defaults."""
    global _trust_events_cache, _trust_events_cache_ts
    import time
    now = time.time()
    # Cache for 60s to avoid constant DB reads
    if _trust_events_cache is not None and (now - _trust_events_cache_ts) < 60:
        return _trust_events_cache

    events = dict(_DEFAULT_TRUST_EVENTS)
    try:
        db = _get_db()
        db.execute("""
            CREATE TABLE IF NOT EXISTS trust_event_config (
                event TEXT PRIMARY KEY,
                delta REAL NOT NULL,
                description TEXT DEFAULT '',
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)
        rows = db.execute("SELECT event, delta FROM trust_event_config").fetchall()
        for r in rows:
            events[r[0]] = r[1]
    except Exception:
        pass
    _trust_events_cache = events
    _trust_events_cache_ts = now
    return events


def auto_detect_trust_events(text: str) -> list[dict]:
    """Detect trust events from user text. Returns list of {event, delta, reason}.

    Called automatically by heartbeat. Only fires once per event per heartbeat
    to avoid double-counting.
    """
    if not text or len(text.strip()) < 5:
        return []

    text_lower = text.lower()
    events = get_trust_events()
    detected = []

    for event_name, config in TRUST_AUTO_PATTERNS.items():
        matches = [p for p in config["patterns"] if p in text_lower]
        if len(matches) >= config["min_matches"]:
            delta = events.get(event_name, _DEFAULT_TRUST_EVENTS.get(event_name, 0))
            detected.append({
                "event": event_name,
                "delta": delta,
                "reason": f"auto-detected: {', '.join(matches[:3])}",
            })

    # Priority: if repeated_error detected, remove correction (it's a superset)
    event_names = {d["event"] for d in detected}
    if "repeated_error" in event_names and "correction" in event_names:
        detected = [d for d in detected if d["event"] != "correction"]
    # If explicit_thanks and delegation both detected, keep both (they're independent)

    return detected


def detect_dissonance(new_instruction: str, min_score: float = 0.65) -> list[dict]:
    """Detect cognitive dissonance: find LTM memories that contradict a new instruction.

    When Francisco gives a new instruction that conflicts with established LTM memories
    (strength > 0.8), this function surfaces the conflict so NEXO can verbalize it
    rather than silently obeying or silently resisting.

    Args:
        new_instruction: The new instruction or preference from Francisco
        min_score: Minimum cosine similarity to consider as potential conflict

    Returns:
        List of conflicting memories with their strength and content
    """
    db = _get_db()
    query_vec = _embed(new_instruction[:500])
    if np.linalg.norm(query_vec) == 0:
        return []

    rows = db.execute(
        "SELECT id, content, embedding, source_type, domain, strength, access_count FROM ltm_memories WHERE is_dormant = 0 AND strength > 0.8"
    ).fetchall()

    conflicts = []
    for row in rows:
        vec = _blob_to_array(row["embedding"])
        score = _cosine_similarity(query_vec, vec)
        if score >= min_score:
            conflicts.append({
                "memory_id": row["id"],
                "content": row["content"],
                "source_type": row["source_type"],
                "domain": row["domain"],
                "strength": row["strength"],
                "access_count": row["access_count"],
                "similarity": round(score, 3),
            })

    conflicts.sort(key=lambda x: x["similarity"], reverse=True)
    return conflicts[:5]


def resolve_dissonance(memory_id: int, resolution: str, context: str = "") -> str:
    """Resolve a cognitive dissonance by applying Francisco's decision.

    Args:
        memory_id: The LTM memory that conflicts with the new instruction
        resolution: One of:
            - 'paradigm_shift': Francisco changed his mind permanently. Decay old memory,
              new instruction becomes the standard.
            - 'exception': This is a one-time override. Keep old memory as standard.
            - 'override': Old memory was wrong. Mark as corrupted and decay to dormant.

    Returns:
        Status message
    """
    db = _get_db()
    row = db.execute("SELECT * FROM ltm_memories WHERE id = ?", (memory_id,)).fetchone()
    if not row:
        return f"Memory #{memory_id} not found."

    now = datetime.utcnow().isoformat()

    if resolution == "paradigm_shift":
        # Instant decay to 0.3, will naturally fade. New instruction takes over.
        db.execute(
            "UPDATE ltm_memories SET strength = 0.3, last_accessed = ? WHERE id = ?",
            (now, memory_id)
        )
        msg = f"Paradigm shift: Memory #{memory_id} decayed to 0.3. New standard will replace it."

    elif resolution == "exception":
        # Keep memory as-is, just log the exception
        msg = f"Exception noted: Memory #{memory_id} remains standard. One-time override applied."

    elif resolution == "override":
        # Memory was wrong — mark as corrupted/dormant
        db.execute(
            "UPDATE ltm_memories SET strength = 0.05, is_dormant = 1, last_accessed = ? WHERE id = ?",
            (now, memory_id)
        )
        msg = f"Override: Memory #{memory_id} marked corrupted and dormant."

    else:
        return f"Unknown resolution: {resolution}. Use 'paradigm_shift', 'exception', or 'override'."

    # Log the correction
    db.execute(
        "INSERT INTO memory_corrections (memory_id, store, correction_type, context) VALUES (?, 'ltm', ?, ?)",
        (memory_id, resolution, context[:500])
    )
    db.commit()

    return msg


def check_correction_fatigue() -> list[dict]:
    """Find memories corrected 3+ times in the last 7 days — mark as 'under review'.

    These memories are unreliable: Francisco keeps overriding them, suggesting
    the memory itself may be wrong or outdated.

    Returns:
        List of memories that should be flagged as unreliable
    """
    db = _get_db()
    cutoff = (datetime.utcnow() - timedelta(days=7)).isoformat()

    rows = db.execute("""
        SELECT memory_id, COUNT(*) as correction_count,
               GROUP_CONCAT(correction_type) as types
        FROM memory_corrections
        WHERE created_at >= ? AND store = 'ltm'
        GROUP BY memory_id
        HAVING COUNT(*) >= 3
    """, (cutoff,)).fetchall()

    fatigued = []
    for row in rows:
        mem = db.execute(
            "SELECT content, strength, source_type, domain FROM ltm_memories WHERE id = ?",
            (row["memory_id"],)
        ).fetchone()
        if mem:
            fatigued.append({
                "memory_id": row["memory_id"],
                "corrections_7d": row["correction_count"],
                "types": row["types"],
                "content": mem["content"][:200],
                "strength": mem["strength"],
                "source_type": mem["source_type"],
                "domain": mem["domain"],
            })

            # Auto-mark as under review: decay strength to 0.2
            db.execute(
                "UPDATE ltm_memories SET strength = MIN(strength, 0.2), tags = CASE WHEN tags LIKE '%under_review%' THEN tags ELSE tags || ',under_review' END WHERE id = ?",
                (row["memory_id"],)
            )

    if fatigued:
        db.commit()

    return fatigued


def detect_sentiment(text: str) -> dict:
    """Analyze Francisco's text for sentiment signals.

    Returns detected sentiment, intensity, and action guidance for NEXO.
    Not a model — keyword + heuristic based. Fast and deterministic.
    """
    if not text:
        return {"sentiment": "neutral", "intensity": 0.5, "signals": [], "guidance": ""}

    text_lower = text.lower()
    words = set(text_lower.split())

    positive_hits = [s for s in POSITIVE_SIGNALS if s in text_lower]
    negative_hits = [s for s in NEGATIVE_SIGNALS if s in text_lower]
    urgency_hits = [s for s in URGENCY_SIGNALS if s in text_lower]

    # Heuristics
    is_short = len(text) < 30
    has_caps = any(c.isupper() for c in text[1:]) if len(text) > 1 else False  # ignore first char
    has_exclamation = "!" in text
    all_caps_words = sum(1 for w in text.split() if w.isupper() and len(w) > 1)

    # Score
    pos_score = len(positive_hits)
    neg_score = len(negative_hits)

    # Caps/short boost negative
    if all_caps_words >= 2:
        neg_score += 2
    if is_short and neg_score > 0:
        neg_score += 1  # Short + negative = terse frustration

    if urgency_hits:
        neg_score += 1  # Urgency often means something is wrong

    # Determine sentiment
    if neg_score > pos_score and neg_score >= 1:
        sentiment = "negative"
        intensity = min(1.0, 0.3 + neg_score * 0.15)
        if intensity > 0.7:
            guidance = "MODE: Ultra-conciso. Cero explicaciones. Resolver y mostrar resultado."
        else:
            guidance = "MODE: Conciso. Menos contexto, más acción directa."
    elif pos_score > neg_score and pos_score >= 1:
        sentiment = "positive"
        intensity = min(1.0, 0.3 + pos_score * 0.15)
        guidance = "MODE: Normal. Buen momento para proponer ideas de backlog o mejoras."
    elif urgency_hits:
        sentiment = "urgent"
        intensity = 0.8
        guidance = "MODE: Acción inmediata. Sin preámbulos."
    else:
        sentiment = "neutral"
        intensity = 0.5
        guidance = ""

    return {
        "sentiment": sentiment,
        "intensity": round(intensity, 2),
        "signals": positive_hits + negative_hits + urgency_hits,
        "guidance": guidance,
    }


def log_sentiment(text: str) -> dict:
    """Detect and log Francisco's sentiment. Returns the detection result."""
    result = detect_sentiment(text)
    if result["sentiment"] != "neutral":
        db = _get_db()
        db.execute(
            "INSERT INTO sentiment_log (sentiment, intensity, signals) VALUES (?, ?, ?)",
            (result["sentiment"], result["intensity"], ",".join(result["signals"]))
        )
        db.commit()
    return result


def get_trust_score() -> float:
    """Get current trust score. Starts at 50, range 0-100."""
    db = _get_db()
    row = db.execute("SELECT score FROM trust_score ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        # Initialize
        db.execute(
            "INSERT INTO trust_score (score, event, delta, context) VALUES (50, 'init', 0, 'Initial trust score')"
        )
        db.commit()
        return 50.0
    return row[0]


def _annotate_adaptive_log(event: str, delta: float):
    """Retroactively annotate the most recent adaptive_log entry with trust feedback."""
    try:
        from db import get_db
        conn = get_db()
        conn.execute(
            "UPDATE adaptive_log SET feedback_event = ?, feedback_delta = ?, "
            "feedback_ts = datetime('now') "
            "WHERE id = (SELECT id FROM adaptive_log "
            "WHERE feedback_event IS NULL "
            "AND timestamp >= datetime('now', '-5 minutes') "
            "ORDER BY id DESC LIMIT 1)",
            (event, int(delta))
        )
        conn.commit()
    except Exception:
        pass


def adjust_trust(event: str, context: str = "", custom_delta: float = None) -> dict:
    """Adjust trust score based on an event.

    Args:
        event: Event type from TRUST_EVENTS or custom
        context: Description of what happened
        custom_delta: Override the default point value

    Returns:
        Dict with old_score, delta, new_score, event
    """
    db = _get_db()
    old_score = get_trust_score()

    events = get_trust_events()
    delta = custom_delta if custom_delta is not None else events.get(event, 0)
    if delta == 0 and custom_delta is None:
        return {"old_score": old_score, "delta": 0, "new_score": old_score, "event": event, "error": "unknown event"}

    new_score = max(0.0, min(100.0, old_score + delta))

    db.execute(
        "INSERT INTO trust_score (score, event, delta, context) VALUES (?, ?, ?, ?)",
        (new_score, event, delta, context[:500])
    )
    db.commit()

    # Annotate adaptive log for learned weights
    _annotate_adaptive_log(event, delta)

    # Somatic event logging for repeated_error events (append-only in nexo.db)
    if event == "repeated_error" and context:
        try:
            from db import get_db as get_nexo_db
            area = context.split(":")[0].strip() if ":" in context else "unknown"
            get_nexo_db().execute(
                "INSERT INTO somatic_events (target, target_type, event_type, delta, source) VALUES (?, ?, ?, ?, ?)",
                (area, "area", "repeated_error", 0.20, f"trust:{event}")
            )
            get_nexo_db().commit()
        except Exception:
            pass

    return {
        "old_score": round(old_score, 1),
        "delta": delta,
        "new_score": round(new_score, 1),
        "event": event,
    }


def get_trust_history(days: int = 7) -> dict:
    """Get trust score history and sentiment summary."""
    db = _get_db()
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()

    # Trust events
    events = db.execute(
        "SELECT event, delta, score, context, created_at FROM trust_score WHERE created_at >= ? ORDER BY id",
        (cutoff,)
    ).fetchall()

    # Sentiment distribution
    sentiments = db.execute(
        "SELECT sentiment, COUNT(*) as cnt, AVG(intensity) as avg_int FROM sentiment_log WHERE created_at >= ? GROUP BY sentiment",
        (cutoff,)
    ).fetchall()

    current = get_trust_score()
    start_score = events[0]["score"] - events[0]["delta"] if events else current

    return {
        "current_score": round(current, 1),
        "period_start_score": round(start_score, 1),
        "net_change": round(current - start_score, 1),
        "events": [{"event": e["event"], "delta": e["delta"], "score": round(e["score"], 1), "context": e["context"][:100], "at": e["created_at"]} for e in events],
        "sentiment_distribution": {s["sentiment"]: {"count": s["cnt"], "avg_intensity": round(s["avg_int"], 2)} for s in sentiments},
    }
