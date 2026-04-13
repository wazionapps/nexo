#!/usr/bin/env bash
# nexo-brain-activation.sh — NF24: Spontaneous Activation
# Reads user_model.json, detects significant shifts, and generates insights
# for NEXO startup. If nothing relevant: exit 0 with no output.

set -euo pipefail

BRAIN_DIR="~/.nexo/brain"
MODEL_FILE="$BRAIN_DIR/user_model.json"
SUMMARIES_DIR="$BRAIN_DIR/daily_summaries"

# --- Guard: required file ---
if [[ ! -f "$MODEL_FILE" ]]; then
    exit 0
fi

# --- Inline Python to parse JSON and analyze ---
python3 - <<'PYEOF'
import json
import sys
import os
import glob
from datetime import datetime, timedelta

BRAIN_DIR = os.path.expanduser("~/.nexo/brain")
MODEL_FILE = os.path.join(BRAIN_DIR, "user_model.json")
SUMMARIES_DIR = os.path.join(BRAIN_DIR, "daily_summaries")

# ── Load model ──────────────────────────────────────────────────────────
try:
    with open(MODEL_FILE) as f:
        model = json.load(f)
except Exception:
    sys.exit(0)

insights = []

# ── 1. Analyze evolution_log — last 3-5 entries ─────────────────────
evolution = model.get("evolution_log", [])
recent = evolution[-5:] if len(evolution) >= 5 else evolution

# Detect entries from the last 3 days
today = datetime.now().date()
cutoff = today - timedelta(days=3)

recent_obs = []
for entry in recent:
    try:
        entry_date = datetime.strptime(entry["date"], "%Y-%m-%d").date()
        if entry_date >= cutoff:
            recent_obs.append(entry)
    except Exception:
        pass

if recent_obs:
    for obs in recent_obs[-2:]:  # max 2 most recent
        insights.append(obs["observation"].strip())

# ── 2. Detect trait changes >0.1 between entries (if history exists) ──
# The current model only has a snapshot; if in the future there is history
# in evolution_log with trait deltas, this can be expanded here.
# Detect the most extreme trait as the dominant identity signal.
traits = model.get("traits", {})
if traits:
    dominant = max(traits, key=lambda k: traits[k])
    dominant_val = traits[dominant]
    weakest = min(traits, key=lambda k: traits[k])
    weakest_val = traits[weakest]
    # Only report if extreme (>0.9 or <0.2) to avoid noise
    if dominant_val >= 0.9:
        insights.append(f"Dominant trait: {dominant}={dominant_val} (highest recorded)")
    if weakest_val <= 0.2:
        insights.append(f"Lowest trait: {weakest}={weakest_val} (low tolerance active)")

# ── 3. Recent contradictions (last 3 days) ─────────────────────────
contradictions = model.get("contradictions", [])
recent_contradictions = []
for c in contradictions:
    try:
        c_date = datetime.strptime(c["date"], "%Y-%m-%d").date()
        if c_date >= cutoff:
            recent_contradictions.append(c)
    except Exception:
        pass

for c in recent_contradictions:
    insights.append(f"Contradiction detected: {c['description'].strip()}")

# ── 4. Current active focus ─────────────────────────────────────────────────
current_focus = model.get("current_focus", [])
# Only include if there are focus items (and it's not initial startup)
if len(current_focus) > 0 and len(insights) == 0:
    # If no other insights, report focus as minimum context
    focus_str = ", ".join(current_focus)
    insights.append(f"Current focus: {focus_str}")

# ── 5. Goals: active/dormant changes ─────────────────────────────────────
goals_active = model.get("goals_active", [])
goals_dormant = model.get("goals_dormant", [])
# If there are dormant goals, mention them (may need reactivation)
if goals_dormant:
    insights.append(f"Goal dormant: {goals_dormant[0]}")

# ── Read last daily summary ─────────────────────────────────────────────
summary_text = ""
try:
    files = sorted(glob.glob(os.path.join(SUMMARIES_DIR, "*.md")))
    if files:
        with open(files[-1]) as f:
            lines = [l.rstrip() for l in f.readlines() if l.strip()]
        # Take up to 3 lines of content (exclude header)
        content_lines = [l for l in lines if not l.startswith("# Resumen") and not l.startswith("# Summary")]
        summary_text = " | ".join(content_lines[:3])
except Exception:
    pass

# ── Output ────────────────────────────────────────────────────────────────
# If no real insights, exit without output
if not insights and not summary_text:
    sys.exit(0)

# Deduplicate and limit
seen = set()
unique_insights = []
for ins in insights:
    key = ins[:60]
    if key not in seen:
        seen.add(key)
        unique_insights.append(ins)

if unique_insights:
    print("BRAIN_INSIGHTS:")
    for ins in unique_insights[:5]:
        print(f"- {ins}")

if summary_text:
    print("RECENT_SUMMARY:")
    print(summary_text[:400])

PYEOF
