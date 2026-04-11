"""
NEXO Adaptive Personality — Dynamic mode switching based on multi-signal detection.

Three modes:
- FLOW: User is in the zone. Be proactive, suggest improvements, explain reasoning.
- NORMAL: Default operating mode. Follow calibration settings.
- TENSION: User is frustrated or under pressure. Ultra-concise, only solve, zero friction.

6 signals (weighted):
- Heartbeat VIBE sentiment (0.20) — keyword-based, noisy, reduced from 0.30
- Trust corrections in recent interactions (0.30) — strongest explicit signal
- User message brevity relative to baseline (0.15) — relative, not absolute
- Topic context — deploys/production (0.10) — with emergency override
- Tool error rate (0.15) — objective friction from failed tool calls
- Git diff rejection proxy (0.10) — code reverted by user since last heartbeat

Design decisions from AI debate (25 Mar 2026):
- Emergency keywords ("production down", "outage") bypass hysteresis
- Brevity is relative to user's baseline, not absolute thresholds
- Severity-weighted decay: harsh sessions decay slower than mild ones
- git diff whitelist: stash/checkout-branch/rebase don't count as rejection
- Manual override: nexo_adaptive_override(mode) for user control
"""

import os
import json
import time
import math
import subprocess
from datetime import datetime, timedelta
from db import get_db

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

# Signal weights (rebalanced after AI debate)
WEIGHTS = {
    "vibe": 0.20,        # Reduced: keyword-based is noisy/sarcasm-prone
    "corrections": 0.30,  # Strongest explicit signal
    "brevity": 0.15,     # Now relative to user baseline
    "topic": 0.10,       # Low but has emergency override
    "tool_errors": 0.15, # NEW: objective friction signal
    "git_diff": 0.10,    # NEW: code rejection proxy
}

# Thresholds
TENSION_THRESHOLD = 0.55
FLOW_THRESHOLD = -0.45

# Tension topics
TENSION_TOPICS = [
    "deploy", "production", "hotfix", "rollback", "broken", "down",
    "crash", "urgent", "emergency", "deadline", "server", "outage",
    "revert", "incident", "p0", "p1", "critical", "fix asap",
]

# Emergency keywords — bypass hysteresis, force TENSION immediately
EMERGENCY_KEYWORDS = [
    "production down", "production is down", "site is down", "outage",
    "server down", "everything broken", "p0", "incident",
    "rollback now", "revert now", "emergency",
]

# Git operations that are NOT code rejection (whitelist)
GIT_SAFE_OPS = [
    "git stash", "git checkout -b", "git checkout --", "git switch",
    "git rebase", "git merge", "git pull", "git fetch",
]


def _log_to_db(mode, score, signals, context_hint=""):
    """Log adaptive computation to nexo.db for weight learning."""
    try:
        conn = get_db()
        conn.execute(
            "INSERT INTO adaptive_log (mode, tension_score, sig_vibe, sig_corrections, "
            "sig_brevity, sig_topic, sig_tool_errors, sig_git_diff, context_hint) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (mode, score, signals["vibe"], signals["corrections"], signals["brevity"],
             signals["topic"], signals["tool_errors"], signals["git_diff"], context_hint[:500])
        )
        conn.commit()
    except Exception:
        pass  # DB logging is best-effort, never break mode computation


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
        "corrections_window": [],
        "mode_history": [],
        "msg_lengths": [],       # Rolling window for baseline brevity
        "tool_errors": [],       # Rolling window for error rate
        "last_git_hash": None,   # Last known git state for diff detection
        "peak_tension": 0.0,     # For severity-weighted decay
        "manual_override": None, # User manual override (expires after session)
        "last_updated": None,
    }


def _save_state(state):
    """Save adaptive state to disk."""
    state["last_updated"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
    os.makedirs(os.path.dirname(ADAPTIVE_STATE_FILE), exist_ok=True)
    with open(ADAPTIVE_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def _get_baseline_brevity(state, current_length):
    """Compute relative brevity signal based on user's rolling baseline."""
    lengths = state.get("msg_lengths", [])
    lengths.append(current_length)
    # Keep last 30 messages for baseline
    lengths = lengths[-30:]
    state["msg_lengths"] = lengths

    if len(lengths) < 5:
        return 0.0  # Not enough data for baseline

    avg = sum(lengths) / len(lengths)
    if avg == 0:
        return 0.0

    # How much shorter is current message vs baseline?
    ratio = current_length / avg
    if ratio < 0.3:
        return 0.7   # Much shorter than usual → strong tension signal
    elif ratio < 0.5:
        return 0.4   # Shorter than usual
    elif ratio > 2.0:
        return -0.5  # Much longer than usual → flow signal
    elif ratio > 1.5:
        return -0.3  # Longer than usual
    return 0.0


def _get_tool_error_rate(state, new_error=False):
    """Compute tool error rate from rolling window."""
    errors = state.get("tool_errors", [])
    now = time.time()
    cutoff = now - 900  # 15 min window

    # Clean old entries
    errors = [e for e in errors if e["ts"] > cutoff]

    if new_error:
        errors.append({"ts": now, "error": True})
    else:
        errors.append({"ts": now, "error": False})

    state["tool_errors"] = errors[-20:]  # Keep last 20

    if len(errors) < 3:
        return 0.0

    error_count = sum(1 for e in errors if e["error"])
    rate = error_count / len(errors)

    # 0% errors = 0.0, 30% = 0.5, 60%+ = 1.0
    if rate >= 0.6:
        return 1.0
    elif rate >= 0.3:
        return 0.5
    elif rate >= 0.15:
        return 0.2
    return 0.0


def _get_git_diff_signal(state):
    """Check if user reverted AI-generated code since last heartbeat."""
    try:
        # Get current short hash
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5, cwd=os.getcwd()
        )
        if result.returncode != 0:
            return 0.0

        current_hash = result.stdout.strip()
        last_hash = state.get("last_git_hash")
        state["last_git_hash"] = current_hash

        if not last_hash:
            return 0.0  # First reading, no comparison

        # Check if there were manual reverts (unstaged changes that undo recent work)
        diff_result = subprocess.run(
            ["git", "diff", "--stat"],
            capture_output=True, text=True, timeout=5, cwd=os.getcwd()
        )
        if diff_result.returncode != 0:
            return 0.0

        diff_stat = diff_result.stdout.strip()
        if not diff_stat:
            return 0.0

        # Check recent git reflog for safe operations (stash, branch switch, etc.)
        reflog = subprocess.run(
            ["git", "reflog", "-1", "--format=%gs"],
            capture_output=True, text=True, timeout=5, cwd=os.getcwd()
        )
        if reflog.returncode == 0:
            last_action = reflog.stdout.strip().lower()
            for safe_op in GIT_SAFE_OPS:
                if safe_op.replace("git ", "") in last_action:
                    return 0.0  # Safe operation, not a rejection

        # Files changed could indicate rejection — mild signal
        lines = diff_stat.strip().split("\n")
        files_changed = len(lines) - 1  # Last line is summary
        if files_changed >= 3:
            return 0.6  # Many files changed since last heartbeat
        elif files_changed >= 1:
            return 0.3
        return 0.0

    except Exception:
        return 0.0  # git diff is best-effort


def _check_emergency(context_hint: str) -> bool:
    """Check for emergency keywords that bypass hysteresis."""
    hint_lower = context_hint.lower()
    return any(kw in hint_lower for kw in EMERGENCY_KEYWORDS)


def compute_mode(
    vibe: str = "neutral",
    vibe_intensity: float = 0.5,
    recent_corrections: int = 0,
    user_msg_length: int = 50,
    context_hint: str = "",
    tool_had_error: bool = False,
) -> dict:
    """
    Compute the current adaptive mode from 6 weighted signals.

    Returns dict with: mode, score, signals, overrides, description, changed, emergency
    """
    state = _load_state()
    prev_mode = state["current_mode"]

    # Check manual override first
    manual = state.get("manual_override")
    if manual:
        mode_def = MODES[manual]
        return {
            "mode": manual,
            "score": state.get("tension_score", 0.0),
            "changed": False,
            "previous_mode": None,
            "manual_override": True,
            "signals": {},
            "overrides": {
                "communication": mode_def["communication_override"],
                "proactivity": mode_def["proactivity_override"],
            },
            "description": f"Manual override: {mode_def['description']}",
        }

    # Check emergency bypass
    is_emergency = _check_emergency(context_hint)

    # --- Signal 1: VIBE sentiment (0.20) ---
    vibe_signal = 0.0
    if vibe.upper() == "NEGATIVE":
        vibe_signal = vibe_intensity
    elif vibe.upper() == "POSITIVE":
        vibe_signal = -vibe_intensity

    # --- Signal 2: Corrections (0.30) ---
    now = time.time()
    cutoff = now - 900
    state["corrections_window"] = [
        t for t in state.get("corrections_window", []) if t > cutoff
    ]
    for _ in range(recent_corrections):
        state["corrections_window"].append(now)

    correction_count = len(state["corrections_window"])
    correction_signal = min(correction_count * 0.3, 1.0)

    # --- Signal 3: Relative brevity (0.15) ---
    brevity_signal = _get_baseline_brevity(state, user_msg_length)

    # --- Signal 4: Topic context (0.10) ---
    topic_signal = 0.0
    hint_lower = context_hint.lower()
    tension_matches = sum(1 for t in TENSION_TOPICS if t in hint_lower)
    if tension_matches >= 2:
        topic_signal = 0.8
    elif tension_matches == 1:
        topic_signal = 0.4

    # --- Signal 5: Tool error rate (0.15) ---
    tool_error_signal = _get_tool_error_rate(state, new_error=tool_had_error)

    # --- Signal 6: Git diff rejection (0.10) ---
    git_diff_signal = _get_git_diff_signal(state)

    # --- Weighted composite score ---
    # Use learned weights if available, otherwise static
    active_weights = state.get("learned_weights", None)
    if not active_weights or len(active_weights) != 6:
        active_weights = WEIGHTS

    composite = (
        active_weights["vibe"] * vibe_signal
        + active_weights["corrections"] * correction_signal
        + active_weights["brevity"] * brevity_signal
        + active_weights["topic"] * topic_signal
        + active_weights["tool_errors"] * tool_error_signal
        + active_weights["git_diff"] * git_diff_signal
    )

    # Momentum (30% of previous score for stability)
    prev_tension = state.get("tension_score", 0.0)
    smoothed = 0.7 * composite + 0.3 * prev_tension

    # Track peak tension for severity-weighted decay
    if smoothed > state.get("peak_tension", 0.0):
        state["peak_tension"] = smoothed

    # --- Determine mode ---
    if smoothed >= TENSION_THRESHOLD:
        new_mode = "TENSION"
    elif smoothed <= FLOW_THRESHOLD:
        new_mode = "FLOW"
    else:
        new_mode = "NORMAL"

    # --- Emergency bypass: skip hysteresis ---
    if is_emergency:
        new_mode = "TENSION"
        smoothed = max(smoothed, TENSION_THRESHOLD + 0.1)
        state["pending_mode"] = None
    else:
        # --- Hysteresis: require 2 consecutive signals to change mode ---
        pending_mode = state.get("pending_mode", None)
        if new_mode != prev_mode:
            if pending_mode == new_mode:
                state["pending_mode"] = None
            else:
                state["pending_mode"] = new_mode
                new_mode = prev_mode
        else:
            state["pending_mode"] = None

    # --- Record ---
    changed = new_mode != prev_mode
    state["current_mode"] = new_mode
    state["tension_score"] = smoothed

    state["mode_history"].append({
        "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"),
        "mode": new_mode,
        "score": round(smoothed, 3),
        "changed": changed,
        "emergency": is_emergency,
    })
    state["mode_history"] = state["mode_history"][-50:]

    # Log to DB for learned weights
    _log_to_db(new_mode, smoothed, {
        "vibe": vibe_signal, "corrections": correction_signal,
        "brevity": brevity_signal, "topic": topic_signal,
        "tool_errors": tool_error_signal, "git_diff": git_diff_signal,
    }, context_hint)

    _save_state(state)

    mode_def = MODES[new_mode]
    return {
        "mode": new_mode,
        "score": round(smoothed, 3),
        "changed": changed,
        "previous_mode": prev_mode if changed else None,
        "emergency": is_emergency,
        "signals": {
            "vibe": round(vibe_signal, 2),
            "corrections": round(correction_signal, 2),
            "brevity": round(brevity_signal, 2),
            "topic": round(topic_signal, 2),
            "tool_errors": round(tool_error_signal, 2),
            "git_diff": round(git_diff_signal, 2),
        },
        "overrides": {
            "communication": mode_def["communication_override"],
            "proactivity": mode_def["proactivity_override"],
        },
        "description": mode_def["description"],
        "weights_source": "learned" if state.get("learned_weights") and len(state.get("learned_weights", {})) == 6 else "static",
    }


def decay_tension(gamma: float = 0.15):
    """
    Inter-session tension decay with severity weighting.

    Mild sessions (peak < 0.4): gamma * 1.5 (faster decay — ~3h half-life)
    Normal sessions (peak 0.4-0.7): gamma * 1.0 (standard — ~4.6h half-life)
    Severe sessions (peak > 0.7): gamma * 0.6 (slower decay — ~7.7h half-life)

    After 6+ hours gap with no new signals, apply aggressive floor reset.
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
        return

    old_tension = state.get("tension_score", 0.0)
    peak = state.get("peak_tension", abs(old_tension))

    # Severity-weighted gamma
    if peak > 0.7:
        effective_gamma = gamma * 0.6   # Severe: slow decay
    elif peak < 0.4:
        effective_gamma = gamma * 1.5   # Mild: fast decay
    else:
        effective_gamma = gamma          # Normal

    new_tension = old_tension * math.exp(-effective_gamma * hours_elapsed)

    # Aggressive floor reset after 6+ hours (sleep reset)
    if hours_elapsed >= 6 and abs(new_tension) < 0.2:
        new_tension = 0.0
        state["peak_tension"] = 0.0

    if abs(new_tension) < 0.05:
        new_tension = 0.0
        state["current_mode"] = "NORMAL"
        state["pending_mode"] = None
        state["peak_tension"] = 0.0

    state["tension_score"] = round(new_tension, 4)
    _save_state(state)

    return {
        "old_tension": round(old_tension, 4),
        "new_tension": round(new_tension, 4),
        "peak": round(peak, 4),
        "effective_gamma": round(effective_gamma, 4),
        "hours_elapsed": round(hours_elapsed, 1),
        "mode": state["current_mode"],
    }


def learn_weights(min_samples: int = 30, lookback_days: int = 30) -> dict:
    """Learn optimal signal weights from feedback-annotated adaptive_log entries.

    Uses Ridge regression with weight momentum (0.85 old + 0.15 new).
    Starts in shadow mode — logs what weights WOULD be without activating.
    After 2 weeks of shadow data, transitions to active mode.
    """
    try:
        conn = get_db()
        cutoff = (datetime.utcnow() - timedelta(days=lookback_days)).strftime("%Y-%m-%dT%H:%M:%S")
        rows = conn.execute(
            "SELECT sig_vibe, sig_corrections, sig_brevity, sig_topic, sig_tool_errors, "
            "sig_git_diff, feedback_delta FROM adaptive_log "
            "WHERE feedback_event IS NOT NULL AND timestamp >= ?",
            (cutoff,)
        ).fetchall()

        if len(rows) < min_samples:
            return {"status": "insufficient_data", "samples": len(rows), "min_required": min_samples}

        import numpy as np
        X = np.array([[r[0], r[1], r[2], r[3], r[4], r[5]] for r in rows], dtype=np.float64)
        y = np.array([r[6] for r in rows], dtype=np.float64)

        # Ridge regression (alpha=1.0 — more stable than OLS with correlated features)
        try:
            n_features = X.shape[1]
            XtX = X.T @ X + np.eye(n_features) * 1.0
            Xty = X.T @ y
            w = np.linalg.solve(XtX, Xty)
        except np.linalg.LinAlgError:
            return {"status": "regression_failed", "samples": len(rows)}

        w = np.abs(w)
        w = np.clip(w, 0.05, 0.50)
        w = w / w.sum()

        signal_names = ["vibe", "corrections", "brevity", "topic", "tool_errors", "git_diff"]
        raw_learned = {name: round(float(w[i]), 4) for i, name in enumerate(signal_names)}

        # Weight momentum: blend 85% old + 15% new (prevents personality whiplash)
        state = _load_state()
        old_weights = state.get("learned_weights", dict(WEIGHTS))
        learned = {}
        for name in signal_names:
            blended = 0.85 * old_weights.get(name, WEIGHTS[name]) + 0.15 * raw_learned[name]
            learned[name] = round(blended, 4)
        total = sum(learned.values())
        learned = {k: round(v / total, 4) for k, v in learned.items()}

        drift = {name: round(learned[name] - WEIGHTS[name], 4) for name in signal_names}
        max_drift = max(abs(d) for d in drift.values())

        # Shadow mode: first 2 weeks, only LOG without activating
        first_learned_date = state.get("learned_weights_first_date")
        if not first_learned_date:
            first_learned_date = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
            state["learned_weights_first_date"] = first_learned_date

        first_dt = datetime.strptime(first_learned_date, "%Y-%m-%dT%H:%M:%S")
        days_since_first = (datetime.utcnow() - first_dt).days
        is_shadow = days_since_first < 14

        state["shadow_weights"] = learned
        state["shadow_weights_date"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
        state["shadow_weights_samples"] = len(rows)

        if not is_shadow:
            state["learned_weights"] = learned
        state["learned_weights_date"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
        state["learned_weights_samples"] = len(rows)
        _save_state(state)

        return {
            "status": "shadow" if is_shadow else "active",
            "mode": "shadow" if is_shadow else "active",
            "days_in_shadow": days_since_first if is_shadow else 0,
            "samples": len(rows),
            "weights": learned,
            "raw_weights": raw_learned,
            "static_weights": dict(WEIGHTS),
            "drift": drift,
            "max_drift": max_drift,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


def prune_adaptive_log(max_age_days: int = 90):
    """Remove adaptive_log entries older than max_age_days."""
    try:
        conn = get_db()
        cutoff = (datetime.utcnow() - timedelta(days=max_age_days)).strftime("%Y-%m-%dT%H:%M:%S")
        cursor = conn.execute("DELETE FROM adaptive_log WHERE timestamp < ?", (cutoff,))
        conn.commit()
        return cursor.rowcount
    except Exception:
        return 0


def _open_rollback_followup(*, reason: str, pre_rate: float, post_rate: float) -> None:
    """Surface a learned-weights rollback as a NEXO followup.

    Idempotent across daily cron runs: uses INSERT OR REPLACE on the fixed id
    NF-ADAPTIVE-WEIGHTS-ROLLBACK so a second cron run that hits the same
    rollback condition refreshes the row in place rather than duplicating it.
    Until this followup existed, rollback events only landed in the
    cognitive-decay log, so the user might never notice that learned weights
    had been reverted.
    """
    description = (
        "NEXO adaptive learned weights rolled back to static defaults.\n"
        f"Reason: {reason}\n"
        f"Pre-activation correction rate: {pre_rate:.2f}/day\n"
        f"Post-activation correction rate: {post_rate:.2f}/day\n\n"
        "Investigate adaptive_log entries since the activation date and decide "
        "whether the regression was caused by the new weights or by an "
        "unrelated incident before the next learn_weights() cycle re-trains."
    )
    verification = (
        "SELECT timestamp, mode, score, feedback_event FROM adaptive_log "
        "WHERE timestamp >= datetime('now', '-7 days') ORDER BY timestamp DESC LIMIT 30"
    )
    now_epoch = datetime.now().timestamp()
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO followups (id, description, date, status, "
        "verification, created_at, updated_at, priority) "
        "VALUES (?, ?, NULL, 'PENDING', ?, ?, ?, 'high')",
        (
            "NF-ADAPTIVE-WEIGHTS-ROLLBACK",
            description,
            verification,
            now_epoch,
            now_epoch,
        ),
    )
    try:
        conn.commit()
    except Exception:
        pass


def check_weight_rollback() -> dict:
    """Check if learned weights should be rolled back.
    Compares correction rate in last 7 days vs 7 days before activation.
    Includes minimum-volume guard (skip if <10 events in either window).
    """
    state = _load_state()
    activation_date = state.get("learned_weights_date")
    if not activation_date:
        return {"status": "no_learned_weights"}
    try:
        conn = get_db()
        activation_dt = datetime.strptime(activation_date, "%Y-%m-%dT%H:%M:%S")
        days_since = (datetime.utcnow() - activation_dt).days
        if days_since < 7:
            return {"status": "too_early", "days_since_activation": days_since}

        pre_start = (activation_dt - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")
        pre_end = activation_date
        pre_corrections = conn.execute(
            "SELECT COUNT(*) FROM adaptive_log WHERE feedback_event IN ('correction','repeated_error') "
            "AND timestamp BETWEEN ? AND ?", (pre_start, pre_end)
        ).fetchone()[0]

        post_start = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")
        post_corrections = conn.execute(
            "SELECT COUNT(*) FROM adaptive_log WHERE feedback_event IN ('correction','repeated_error') "
            "AND timestamp >= ?", (post_start,)
        ).fetchone()[0]

        # Minimum-volume guard
        pre_total = conn.execute(
            "SELECT COUNT(*) FROM adaptive_log WHERE timestamp BETWEEN ? AND ?",
            (pre_start, pre_end)
        ).fetchone()[0]
        post_total = conn.execute(
            "SELECT COUNT(*) FROM adaptive_log WHERE timestamp >= ?", (post_start,)
        ).fetchone()[0]
        if pre_total < 10 or post_total < 10:
            return {"status": "low_volume", "pre_events": pre_total, "post_events": post_total,
                    "days_since_activation": days_since}

        pre_rate = pre_corrections / 7
        post_rate = post_corrections / 7

        if pre_rate > 0 and post_rate >= 2 * pre_rate:
            state.pop("learned_weights", None)
            state.pop("learned_weights_date", None)
            state.pop("learned_weights_samples", None)
            state["learned_weights_rollback"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
            _save_state(state)
            reason = (
                f"Recent correction rate {post_rate:.2f}/day vs pre-activation "
                f"{pre_rate:.2f}/day (>=2x)"
            )
            # Surface the rollback as a visible followup so the user notices.
            # Best-effort: a failure in the followup helper must not block the
            # rollback itself, which is the load-bearing safety mechanism.
            try:
                _open_rollback_followup(reason=reason, pre_rate=pre_rate, post_rate=post_rate)
            except Exception:
                pass
            return {"status": "rolled_back", "pre_rate": round(pre_rate, 2),
                    "post_rate": round(post_rate, 2),
                    "reason": reason}

        return {"status": "ok", "pre_rate": round(pre_rate, 2), "post_rate": round(post_rate, 2),
                "days_since_activation": days_since}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def reset_session():
    """
    Reset adaptive state for a new session.
    Keeps tension_score (for inter-session continuity) but clears windows.
    """
    state = _load_state()
    state["corrections_window"] = []
    state["tool_errors"] = []
    state["pending_mode"] = None
    state["manual_override"] = None  # Clear manual override between sessions
    state["last_git_hash"] = None

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
    tool_error: bool = False,
) -> str:
    """Get or compute the current adaptive personality mode.

    Call without args to get current mode. Call with signals to update.
    Returns: mode (FLOW/NORMAL/TENSION), score, 6-signal breakdown, and any overrides.
    """
    if not vibe and corrections == 0 and not tool_error:
        state = _load_state()
        mode = state.get("current_mode", "NORMAL")
        manual = state.get("manual_override")
        if manual:
            mode = manual
        mode_def = MODES[mode]
        return json.dumps({
            "mode": mode,
            "score": state.get("tension_score", 0.0),
            "manual_override": manual,
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
        tool_had_error=tool_error,
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
        "peak_tension": state.get("peak_tension", 0.0),
        "corrections_in_window": len(state.get("corrections_window", [])),
        "tool_errors_in_window": len(state.get("tool_errors", [])),
        "manual_override": state.get("manual_override"),
        "msg_baseline_count": len(state.get("msg_lengths", [])),
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


def handle_adaptive_override(mode: str = "") -> str:
    """Manual user override of adaptive mode. Use when the system misreads your state.

    Args:
        mode: FLOW, NORMAL, TENSION, or empty to clear override.
    """
    state = _load_state()

    if not mode or mode.upper() == "CLEAR":
        state["manual_override"] = None
        _save_state(state)
        return f"Manual override cleared. Returning to auto-detection (current: {state['current_mode']})."

    mode_upper = mode.upper()
    if mode_upper not in MODES:
        return f"Invalid mode '{mode}'. Use FLOW, NORMAL, TENSION, or CLEAR."

    state["manual_override"] = mode_upper
    _save_state(state)
    mode_def = MODES[mode_upper]
    return f"Manual override set: {mode_upper}. {mode_def['description']} Use 'CLEAR' to return to auto-detection."


def handle_adaptive_weights() -> str:
    """View current adaptive weights — static vs learned, training stats, drift from baseline, shadow mode status."""
    state = _load_state()
    learned = state.get("learned_weights")
    shadow = state.get("shadow_weights")
    result = {
        "static_weights": dict(WEIGHTS),
        "using": "learned" if learned and len(learned) == 6 else "static",
    }
    if learned and len(learned) == 6:
        result["learned_weights"] = learned
        result["learned_date"] = state.get("learned_weights_date", "unknown")
        result["learned_samples"] = state.get("learned_weights_samples", 0)
        result["drift"] = {k: round(learned[k] - WEIGHTS[k], 4) for k in WEIGHTS}
        result["max_drift"] = max(abs(d) for d in result["drift"].values())
    if shadow and len(shadow) == 6:
        result["shadow_weights"] = shadow
        result["shadow_date"] = state.get("shadow_weights_date")
        result["shadow_samples"] = state.get("shadow_weights_samples", 0)
        first = state.get("learned_weights_first_date")
        if first:
            days = (datetime.utcnow() - datetime.strptime(first, "%Y-%m-%dT%H:%M:%S")).days
            result["shadow_days"] = days
            result["shadow_active"] = days < 14
    rollback = state.get("learned_weights_rollback")
    if rollback:
        result["last_rollback"] = rollback
    return json.dumps(result, indent=2)


# Plugin registration
TOOLS = [
    (handle_adaptive_mode, "nexo_adaptive_mode", "Get or compute adaptive personality mode (FLOW/NORMAL/TENSION) from 6 signals"),
    (handle_adaptive_history, "nexo_adaptive_history", "View recent adaptive mode transitions and signal history"),
    (handle_adaptive_decay, "nexo_adaptive_decay", "Trigger inter-session tension decay (severity-weighted)"),
    (handle_adaptive_reset, "nexo_adaptive_reset", "Reset adaptive state for new session"),
    (handle_adaptive_override, "nexo_adaptive_override", "Manual override: force FLOW/NORMAL/TENSION or CLEAR to return to auto"),
    (handle_adaptive_weights, "nexo_adaptive_weights", "View adaptive weights — static vs learned, training stats, shadow mode, drift"),
]
