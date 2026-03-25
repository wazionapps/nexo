"""
NEXO Adaptive Personality — Dynamic mode switching based on multi-signal detection.

Three modes:
- FLOW: User is in the zone. Be proactive, suggest improvements, explain reasoning.
- NORMAL: Default operating mode. Follow calibration settings.
- TENSION: User is frustrated or under pressure. Ultra-concise, only solve, zero friction.

Signals used (weighted):
- Heartbeat VIBE sentiment (0.3)
- Trust corrections in recent interactions (0.4)
- User message brevity pattern (0.2)
- Topic context — deploys/production (0.1)

Mode transitions require convergence of multiple signals to avoid false positives.
A single "no, not that" does NOT trigger TENSION.

Inter-session persistence:
- Session end mode is written to session_buffer.jsonl
- Nocturnal reflection decays accumulated tension
- New sessions start at NORMAL unless tension persists from multiple prior sessions
"""

import os
import json
import time
import math
from datetime import datetime, timedelta

NEXO_HOME = os.environ.get("NEXO_HOME", os.path.expanduser("~/.nexo"))
ADAPTIVE_STATE_FILE = os.path.join(NEXO_HOME, "brain", "adaptive_state.json")

# Mode definitions
MODES = {
    "FLOW": {
        "communication_override": "detailed",
        "proactivity_override": "proactive",
        "description": "User in flow. Suggest improvements, explain reasoning.",
    },
    "NORMAL": {
        "communication_override": None,
        "proactivity_override": None,
        "description": "Default mode. Follow calibration settings.",
    },
    "TENSION": {
        "communication_override": "concise",
        "proactivity_override": "reactive",
        "description": "User under pressure. Ultra-concise, only solve, zero friction.",
    },
}

# Signal weights
WEIGHTS = {
    "vibe": 0.3,
    "corrections": 0.4,
    "brevity": 0.2,
    "topic": 0.1,
}

# Thresholds
TENSION_THRESHOLD = 0.55  # Score above this = TENSION
FLOW_THRESHOLD = -0.45    # Score below this = FLOW (negative = positive signals)

# Tension topics (keywords that increase tension probability)
TENSION_TOPICS = [
    "deploy", "production", "hotfix", "rollback", "broken", "down",
    "crash", "urgent", "emergency", "deadline", "server", "outage",
]


def _load_state():
    """Load adaptive state from disk."""
    if os.path.exists(ADAPTIVE_STATE_FILE):
        try:
            with open(ADAPTIVE_STATE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {
        "current_mode": "NORMAL",
        "tension_score": 0.0,
        "flow_score": 0.0,
        "corrections_window": [],
        "mode_history": [],
        "last_updated": None,
    }


def _save_state(state):
    """Save adaptive state to disk."""
    state["last_updated"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
    os.makedirs(os.path.dirname(ADAPTIVE_STATE_FILE), exist_ok=True)
    with open(ADAPTIVE_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def compute_mode(
    vibe: str = "neutral",
    vibe_intensity: float = 0.5,
    recent_corrections: int = 0,
    user_msg_length: int = 50,
    context_hint: str = "",
) -> dict:
    """
    Compute the current adaptive mode from multiple signals.

    Returns dict with: mode, score, signals, overrides, description, changed
    """
    state = _load_state()
    prev_mode = state["current_mode"]

    # --- Signal 1: VIBE sentiment (0.3) ---
    vibe_signal = 0.0
    if vibe.upper() == "NEGATIVE":
        vibe_signal = vibe_intensity  # 0.0 to 1.0
    elif vibe.upper() == "POSITIVE":
        vibe_signal = -vibe_intensity  # Negative = toward FLOW

    # --- Signal 2: Corrections (0.4) ---
    # Track corrections in a sliding window (last 15 minutes)
    now = time.time()
    cutoff = now - 900  # 15 min window
    state["corrections_window"] = [
        t for t in state.get("corrections_window", []) if t > cutoff
    ]
    # Add new corrections
    for _ in range(recent_corrections):
        state["corrections_window"].append(now)

    correction_count = len(state["corrections_window"])
    # 0 corrections = 0.0, 1 = 0.3, 2 = 0.6, 3+ = 1.0
    correction_signal = min(correction_count * 0.3, 1.0)

    # --- Signal 3: Message brevity (0.2) ---
    # Short messages (< 15 chars) suggest tension, long (> 100) suggest flow
    brevity_signal = 0.0
    if user_msg_length < 15:
        brevity_signal = 0.5
    elif user_msg_length < 5:
        brevity_signal = 1.0
    elif user_msg_length > 100:
        brevity_signal = -0.3
    elif user_msg_length > 200:
        brevity_signal = -0.6

    # --- Signal 4: Topic context (0.1) ---
    topic_signal = 0.0
    hint_lower = context_hint.lower()
    tension_matches = sum(1 for t in TENSION_TOPICS if t in hint_lower)
    if tension_matches >= 2:
        topic_signal = 0.8
    elif tension_matches == 1:
        topic_signal = 0.4

    # --- Weighted composite score ---
    # Positive = toward TENSION, Negative = toward FLOW
    composite = (
        WEIGHTS["vibe"] * vibe_signal
        + WEIGHTS["corrections"] * correction_signal
        + WEIGHTS["brevity"] * brevity_signal
        + WEIGHTS["topic"] * topic_signal
    )

    # Apply momentum (30% of previous score carries over for stability)
    prev_tension = state.get("tension_score", 0.0)
    smoothed = 0.7 * composite + 0.3 * prev_tension

    # --- Determine mode ---
    if smoothed >= TENSION_THRESHOLD:
        new_mode = "TENSION"
    elif smoothed <= FLOW_THRESHOLD:
        new_mode = "FLOW"
    else:
        new_mode = "NORMAL"

    # --- Hysteresis: require 2 consecutive signals to change mode ---
    # Don't switch from NORMAL to TENSION on a single reading
    pending_mode = state.get("pending_mode", None)
    if new_mode != prev_mode:
        if pending_mode == new_mode:
            # Second consecutive signal in same direction — commit the change
            state["pending_mode"] = None
        else:
            # First signal — mark as pending, keep current mode
            state["pending_mode"] = new_mode
            new_mode = prev_mode
    else:
        state["pending_mode"] = None

    # --- Record ---
    changed = new_mode != prev_mode
    state["current_mode"] = new_mode
    state["tension_score"] = smoothed

    # Mode history (keep last 50)
    state["mode_history"].append({
        "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"),
        "mode": new_mode,
        "score": round(smoothed, 3),
        "changed": changed,
    })
    state["mode_history"] = state["mode_history"][-50:]

    _save_state(state)

    mode_def = MODES[new_mode]
    return {
        "mode": new_mode,
        "score": round(smoothed, 3),
        "changed": changed,
        "previous_mode": prev_mode if changed else None,
        "signals": {
            "vibe": round(vibe_signal, 2),
            "corrections": round(correction_signal, 2),
            "brevity": round(brevity_signal, 2),
            "topic": round(topic_signal, 2),
        },
        "overrides": {
            "communication": mode_def["communication_override"],
            "proactivity": mode_def["proactivity_override"],
        },
        "description": mode_def["description"],
    }


def decay_tension(gamma: float = 0.15):
    """
    Inter-session tension decay. Called by nocturnal processes.

    A(t) = A_peak * e^(-gamma * hours_since_last_session)

    Default gamma=0.15 means tension halves every ~4.6 hours.
    After 24h of no sessions, tension drops to ~3% of peak.
    """
    state = _load_state()
    last_updated = state.get("last_updated")
    if not last_updated:
        return

    try:
        last_dt = datetime.strptime(last_updated, "%Y-%m-%dT%H:%M:%S")
    except (ValueError, TypeError):
        return

    hours_elapsed = (datetime.utcnow() - last_dt).total_seconds() / 3600

    if hours_elapsed < 1:
        return  # Don't decay within the same hour

    old_tension = state.get("tension_score", 0.0)
    new_tension = old_tension * math.exp(-gamma * hours_elapsed)

    # If tension decayed below threshold, reset to NORMAL
    if abs(new_tension) < 0.1:
        new_tension = 0.0
        state["current_mode"] = "NORMAL"
        state["pending_mode"] = None

    state["tension_score"] = round(new_tension, 4)
    _save_state(state)

    return {
        "old_tension": round(old_tension, 4),
        "new_tension": round(new_tension, 4),
        "hours_elapsed": round(hours_elapsed, 1),
        "mode": state["current_mode"],
    }


def reset_session():
    """
    Reset adaptive state for a new session.
    Keeps tension_score (for inter-session continuity) but clears window.
    """
    state = _load_state()
    state["corrections_window"] = []
    state["pending_mode"] = None

    # If tension is low, reset to NORMAL
    if abs(state.get("tension_score", 0.0)) < TENSION_THRESHOLD:
        state["current_mode"] = "NORMAL"

    _save_state(state)
    return state["current_mode"]


# --- MCP Tool handlers ---

def handle_adaptive_mode(
    vibe: str = "",
    vibe_intensity: float = 0.5,
    corrections: int = 0,
    msg_length: int = 50,
    context: str = "",
) -> str:
    """Get or compute the current adaptive personality mode.

    Call without args to get current mode. Call with signals to update.
    Returns: mode (FLOW/NORMAL/TENSION), score, signals breakdown, and any overrides.
    """
    if not vibe and corrections == 0:
        # Just return current state
        state = _load_state()
        mode = state.get("current_mode", "NORMAL")
        mode_def = MODES[mode]
        return json.dumps({
            "mode": mode,
            "score": state.get("tension_score", 0.0),
            "overrides": {
                "communication": mode_def["communication_override"],
                "proactivity": mode_def["proactivity_override"],
            },
            "description": mode_def["description"],
        }, indent=2)

    result = compute_mode(
        vibe=vibe,
        vibe_intensity=vibe_intensity,
        recent_corrections=corrections,
        user_msg_length=msg_length,
        context_hint=context,
    )
    return json.dumps(result, indent=2)


def handle_adaptive_history(last_n: int = 10) -> str:
    """View recent adaptive mode transitions and score history.

    Args:
        last_n: Number of recent entries to show (default 10).
    """
    state = _load_state()
    history = state.get("mode_history", [])[-last_n:]
    return json.dumps({
        "current_mode": state.get("current_mode", "NORMAL"),
        "tension_score": state.get("tension_score", 0.0),
        "corrections_in_window": len(state.get("corrections_window", [])),
        "history": history,
    }, indent=2)


def handle_adaptive_decay() -> str:
    """Manually trigger inter-session tension decay. Normally runs automatically during nocturnal processes."""
    result = decay_tension()
    if result:
        return json.dumps(result, indent=2)
    return "No decay needed (no previous state or too recent)."


def handle_adaptive_reset() -> str:
    """Reset adaptive mode for a fresh session start. Keeps inter-session tension but clears correction window."""
    mode = reset_session()
    return f"Session reset. Starting mode: {mode}"


# Plugin registration
TOOLS = [
    (handle_adaptive_mode, "nexo_adaptive_mode", "Get or compute adaptive personality mode (FLOW/NORMAL/TENSION)"),
    (handle_adaptive_history, "nexo_adaptive_history", "View recent adaptive mode transitions"),
    (handle_adaptive_decay, "nexo_adaptive_decay", "Trigger inter-session tension decay"),
    (handle_adaptive_reset, "nexo_adaptive_reset", "Reset adaptive state for new session"),
]
