#!/usr/bin/env bash
# nexo-brain-activation.sh — NF24: Activación Espontánea
# Lee user_model.json, detecta shifts significativos y genera insights
# para el arranque de NEXO. Si no hay nada relevante: exit 0 sin output.

set -euo pipefail

BRAIN_DIR="~/.nexo/brain"
MODEL_FILE="$BRAIN_DIR/user_model.json"
SUMMARIES_DIR="$BRAIN_DIR/daily_summaries"

# --- Guardia: archivo imprescindible ---
if [[ ! -f "$MODEL_FILE" ]]; then
    exit 0
fi

# --- Python inline para parsear JSON y analizar ---
python3 - <<'PYEOF'
import json
import sys
import os
import glob
from datetime import datetime, timedelta

BRAIN_DIR = os.path.expanduser("~/.nexo/brain")
MODEL_FILE = os.path.join(BRAIN_DIR, "user_model.json")
SUMMARIES_DIR = os.path.join(BRAIN_DIR, "daily_summaries")

# ── Cargar modelo ──────────────────────────────────────────────────────────
try:
    with open(MODEL_FILE) as f:
        model = json.load(f)
except Exception:
    sys.exit(0)

insights = []

# ── 1. Analizar evolution_log — últimas 3-5 entradas ─────────────────────
evolution = model.get("evolution_log", [])
recent = evolution[-5:] if len(evolution) >= 5 else evolution

# Detectar entradas de los últimos 3 días
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

# ── 2. Detectar cambios de traits >0.1 entre entradas (si hay histórico) ──
# El modelo actual solo tiene snapshot actual; si en el futuro hay histórico
# en evolution_log con trait deltas, se puede ampliar aquí.
# Detect the most extreme trait as the dominant identity signal.
traits = model.get("traits", {})
if traits:
    dominant = max(traits, key=lambda k: traits[k])
    dominant_val = traits[dominant]
    weakest = min(traits, key=lambda k: traits[k])
    weakest_val = traits[weakest]
    # Solo reportar si es muy extremo (>0.9 o <0.2) para no saturar
    if dominant_val >= 0.9:
        insights.append(f"Trait dominante: {dominant}={dominant_val} (máximo registrado)")
    if weakest_val <= 0.2:
        insights.append(f"Trait en mínimo: {weakest}={weakest_val} (baja tolerancia activa)")

# ── 3. Contradicciones recientes (últimos 3 días) ─────────────────────────
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
    insights.append(f"Contradicción detectada: {c['description'].strip()}")

# ── 4. Foco activo actual ─────────────────────────────────────────────────
current_focus = model.get("current_focus", [])
# Solo incluir si hay focos (y no es el arranque inicial)
if len(current_focus) > 0 and len(insights) == 0:
    # Si no hay otros insights, reportar foco como contexto mínimo
    focus_str = ", ".join(current_focus)
    insights.append(f"Foco actual: {focus_str}")

# ── 5. Goals: cambios active/dormant ─────────────────────────────────────
goals_active = model.get("goals_active", [])
goals_dormant = model.get("goals_dormant", [])
# Si hay goals dormant, mencionarlos (pueden necesitar reactivación)
if goals_dormant:
    insights.append(f"Goal dormant: {goals_dormant[0]}")

# ── Leer último daily summary ─────────────────────────────────────────────
summary_text = ""
try:
    files = sorted(glob.glob(os.path.join(SUMMARIES_DIR, "*.md")))
    if files:
        with open(files[-1]) as f:
            lines = [l.rstrip() for l in f.readlines() if l.strip()]
        # Take up to 3 lines of content (exclude header)
        content_lines = [l for l in lines if not l.startswith("# Resumen")]
        summary_text = " | ".join(content_lines[:3])
except Exception:
    pass

# ── Output ────────────────────────────────────────────────────────────────
# Si no hay insights reales, salir sin output
if not insights and not summary_text:
    sys.exit(0)

# Deduplicar y limitar
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
