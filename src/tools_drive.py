from __future__ import annotations
"""NEXO Drive/Curiosity — autonomous investigation signals.

Public MCP tool handlers + internal detection logic that feeds from
heartbeat, task_close, and diary consolidation.
"""

import json
import os
import re
import subprocess
import time
import unicodedata

from db import (
    create_drive_signal, reinforce_drive_signal, get_drive_signals,
    get_drive_signal, update_drive_signal_status, decay_drive_signals,
    find_similar_drive_signal, drive_signal_stats,
)


# ── Semantic signal detection ────────────────────────────────────────

# Primary path: concept-level semantic scoring with multilingual cue families.
# Regex remains as explicit fallback only when the semantic scorer cannot
# separate the classes with enough confidence.

_SEMANTIC_THRESHOLD = 0.75
_SEMANTIC_MARGIN = 0.15
_LLM_MIN_TEXT_CHARS = int(os.environ.get("NEXO_DRIVE_LLM_MIN_CHARS", "24"))
_LLM_TIMEOUT_SECONDS = int(os.environ.get("NEXO_DRIVE_LLM_TIMEOUT", "20"))
_LLM_CONFIDENCE_THRESHOLD = float(os.environ.get("NEXO_DRIVE_LLM_CONFIDENCE", "0.62"))
_LLM_CACHE_TTL_SECONDS = int(os.environ.get("NEXO_DRIVE_LLM_CACHE_TTL", "21600"))
_LLM_ALLOWED_LABELS = {"anomaly", "pattern", "gap", "opportunity", "none"}
_LLM_CLASSIFICATION_CACHE: dict[str, dict] = {}

_SIGNAL_CUES = {
    "anomaly": {
        "metric": (
            "cpc", "ctr", "roas", "conversion", "conversiones", "revenue",
            "ingresos", "traffic", "trafico", "latency", "latencia",
            "error", "erro", "fehler", "failure", "fallo", "falla",
            "incident", "incidente", "kpi", "metric", "metrica",
        ),
        "change": (
            "subio*", "bajo*", "cayo*", "aumento*", "disminu*", "crecio*",
            "drop*", "spik*", "jump*", "rose", "fell", "grew", "surg*",
            "subiu*", "caiu*", "baixou*", "aumentou*", "stieg*", "fiel*",
            "gesunk*", "anstieg*", "einbruch*", "regression*",
        ),
        "unexpected": (
            "inesperad*", "unexpected*", "anom*", "raro*", "weird",
            "strange", "estranh*", "seltsam*", "ungewohn*", "anomalia*",
            "outlier*", "desviacion*", "abweich*",
        ),
        "degradation": (
            "degrad*", "timeout*", "slow*", "lento*", "caida*", "degraded",
            "down", "outage", "rot*", "broken", "rompio*", "broke",
            "schlecht*", "falha*", "incidencia*",
        ),
    },
    "pattern": {
        "recurrence": (
            "otra vez", "de nuevo", "again", "again and again", "recurr*",
            "repe*", "keeps happ*", "siempre pasa", "vuelve a pasar",
            "sempre", "sempre que", "de novo", "wieder", "immer wieder",
            "wiederholt*", "stuck in a loop", "reincid*",
        ),
        "cadence": (
            "cada vez que", "every time", "whenever", "cada semana",
            "cada mes", "once more", "toda vez que", "jedes mal",
            "wann immer", "all the time", "constantemente",
        ),
        "same_issue": (
            "mismo problema", "mismo error", "same problem", "same issue",
            "same error", "lo mismo", "same thing", "same blocker",
            "mesmo problema", "gleiches problem", "gleicher fehler",
        ),
    },
    "gap": {
        "uncertainty": (
            "no se como", "no entiendo", "no tengo claro", "unclear how",
            "dont know how", "not sure how", "i do not know how",
            "sem saber como", "nao sei como", "ich weiss nicht wie",
            "ich weiß nicht wie", "unklar wie", "blocked by not knowing",
        ),
        "missing_knowledge": (
            "falta documentacion", "missing docs", "missing documentation",
            "undocumented", "not documented", "sin documentar", "sin guia",
            "no hay runbook", "no hay playbook", "sem documentacao",
            "fehlt dokumentation", "kein runbook", "unknown process",
        ),
        "blocked_execution": (
            "bloqueado porque", "blocked because", "cannot proceed",
            "no puedo seguir", "cant continue", "nao consigo avanzar",
            "komme nicht weiter", "stuck because",
        ),
    },
    "opportunity": {
        "benchmark_gap": (
            "media del sector", "industry average", "below peers",
            "por debajo", "underperform*", "lagging", "low compared",
            "abaixo do benchmark", "unter benchmark", "unter dem schnitt",
        ),
        "improvement": (
            "automatiz*", "optimiz*", "mejor*", "improv*", "streamlin*",
            "simplif*", "scale*", "accelerat*", "reduce manual",
            "automat*", "melhor*", "verbesser*", "effizien*",
        ),
        "potential": (
            "podriamos", "se podria", "could", "we could", "opportunity",
            "worth exploring", "room to", "potencial", "oportunidade",
            "chance to", "could unlock", "konnten", "man koennte",
        ),
    },
}

_SIGNAL_FAMILY_WEIGHTS = {
    "anomaly": {"metric": 0.28, "change": 0.38, "unexpected": 0.30, "degradation": 0.28},
    "pattern": {"recurrence": 0.36, "cadence": 0.34, "same_issue": 0.34},
    "gap": {"uncertainty": 0.78, "missing_knowledge": 0.52, "blocked_execution": 0.36},
    "opportunity": {"benchmark_gap": 0.78, "improvement": 0.38, "potential": 0.32},
}

_FALLBACK_PATTERNS = {
    "anomaly": (
        re.compile(r"\b(subió|bajó|cayó|dropped|spiked|jumped)\b.*\b\d+%", re.I),
        re.compile(r"\b(inesperado|unexpected|anomal|raro|weird|strange)\b", re.I),
    ),
    "pattern": (
        re.compile(r"\b(otra vez|again|de nuevo|siempre pasa|keeps happening|recurring)\b", re.I),
        re.compile(r"\b(cada vez que|every time|whenever)\b", re.I),
    ),
    "gap": (
        re.compile(r"\b(no sé cómo|don'?t know how|no entiendo|unclear how)\b", re.I),
        re.compile(r"\b(falta documentación|missing docs|undocumented)\b", re.I),
    ),
    "opportunity": (
        re.compile(r"\b(benchmark|media del sector|industry average)\b.*\b(bajo|low|por debajo|below)\b", re.I),
        re.compile(r"\b(podríamos|could|se podría|we could|opportunity)\b.*\b(automatiz|improve|mejorar|optimiz)\b", re.I),
    ),
}


def _normalize_text(text: str) -> str:
    lowered = (text or "").lower().replace("ß", "ss").replace("'", "")
    lowered = unicodedata.normalize("NFKD", lowered)
    lowered = "".join(ch for ch in lowered if not unicodedata.combining(ch))
    lowered = re.sub(r"[^a-z0-9%+\s]", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def _extract_json_object(raw: str) -> dict | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else None
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        payload = json.loads(text[start : end + 1])
        return payload if isinstance(payload, dict) else None
    except json.JSONDecodeError:
        return None


def _tokenize(text: str) -> list[str]:
    return [token for token in text.split() if token]


def _matches_cue(cue: str, text_norm: str, tokens: list[str]) -> bool:
    cue_norm = _normalize_text(cue)
    if not cue_norm:
        return False
    if cue_norm.endswith("*"):
        stem = cue_norm[:-1]
        return bool(stem) and any(token.startswith(stem) for token in tokens)
    if " " in cue_norm:
        return cue_norm in text_norm
    return cue_norm in tokens or any(token.startswith(cue_norm) for token in tokens if len(cue_norm) >= 5)


def _has_numeric_signal(tokens: list[str]) -> bool:
    for token in tokens:
        raw = token.rstrip("%")
        try:
            float(raw)
            return True
        except ValueError:
            continue
    return False


def _semantic_signal_scores(text: str) -> dict[str, float]:
    text_norm = _normalize_text(text)
    tokens = _tokenize(text_norm)
    if not tokens:
        return {}

    numeric_signal = _has_numeric_signal(tokens)
    scores = {signal_type: 0.0 for signal_type in _SIGNAL_CUES}
    family_hits: dict[str, set[str]] = {signal_type: set() for signal_type in _SIGNAL_CUES}

    for signal_type, families in _SIGNAL_CUES.items():
        weights = _SIGNAL_FAMILY_WEIGHTS[signal_type]
        for family_name, cues in families.items():
            matches = [cue for cue in cues if _matches_cue(cue, text_norm, tokens)]
            if not matches:
                continue
            family_hits[signal_type].add(family_name)
            bonus = min(0.12, 0.04 * max(0, len(matches) - 1))
            scores[signal_type] += weights[family_name] + bonus

    anomaly_hits = family_hits["anomaly"]
    if "metric" in anomaly_hits and "change" in anomaly_hits:
        scores["anomaly"] += 0.22
    if numeric_signal and ("change" in anomaly_hits or "metric" in anomaly_hits):
        scores["anomaly"] += 0.14
    if "unexpected" in anomaly_hits and ("change" in anomaly_hits or "degradation" in anomaly_hits):
        scores["anomaly"] += 0.12
    if "unexpected" in anomaly_hits and "metric" in anomaly_hits:
        scores["anomaly"] += 0.10

    pattern_hits = family_hits["pattern"]
    if "recurrence" in pattern_hits and ("cadence" in pattern_hits or "same_issue" in pattern_hits):
        scores["pattern"] += 0.18

    gap_hits = family_hits["gap"]
    if "uncertainty" in gap_hits and ("missing_knowledge" in gap_hits or "blocked_execution" in gap_hits):
        scores["gap"] += 0.18

    opportunity_hits = family_hits["opportunity"]
    if "benchmark_gap" in opportunity_hits:
        scores["opportunity"] += 0.16
    if "improvement" in opportunity_hits and "potential" in opportunity_hits:
        scores["opportunity"] += 0.18

    return scores


def _llm_cache_key(text: str) -> str:
    return _normalize_text(text)[:1200]


def _llm_classify_signal(text: str) -> dict:
    text_norm = _normalize_text(text)
    if len(text_norm) < _LLM_MIN_TEXT_CHARS:
        return {"available": False, "label": None, "reason": "text_too_short"}

    cache_key = _llm_cache_key(text)
    now = time.time()
    cached = _LLM_CLASSIFICATION_CACHE.get(cache_key)
    if cached and cached.get("expires_at", 0) > now:
        return {k: v for k, v in cached.items() if k != "expires_at"}

    try:
        from agent_runner import AutomationBackendUnavailableError, run_automation_prompt
    except Exception as exc:
        return {"available": False, "label": None, "reason": f"runner_unavailable:{exc}"}

    json_system_prompt = (
        "You classify operational text into one of exactly five labels: "
        "anomaly, pattern, gap, opportunity, none. "
        "Return ONLY a valid JSON object with keys: label, confidence, reason. "
        "confidence must be a number from 0 to 1. "
        "Use anomaly for unexpected changes/degradation, pattern for recurrence, "
        "gap for missing knowledge/documentation/blocker, opportunity for improvement/automation/benchmark gaps, "
        "none when the text is normal progress without a useful signal."
    )
    prompt = (
        "Classify this NEXO Drive signal candidate.\n\n"
        f"TEXT:\n{text.strip()[:3000]}\n\n"
        "Return JSON only."
    )

    try:
        result = run_automation_prompt(
            prompt,
            task_profile="fast",
            timeout=_LLM_TIMEOUT_SECONDS,
            output_format="text",
            append_system_prompt=json_system_prompt,
        )
    except (AutomationBackendUnavailableError, subprocess.TimeoutExpired) as exc:
        return {"available": False, "label": None, "reason": f"automation_unavailable:{exc}"}
    except Exception as exc:
        return {"available": False, "label": None, "reason": f"automation_error:{exc}"}

    if result.returncode != 0:
        return {"available": False, "label": None, "reason": f"automation_returncode:{result.returncode}"}

    parsed = _extract_json_object(result.stdout)
    if not parsed:
        return {"available": False, "label": None, "reason": "invalid_json"}

    label = str(parsed.get("label", "") or "").strip().lower()
    if label not in _LLM_ALLOWED_LABELS:
        return {"available": False, "label": None, "reason": "invalid_label"}

    try:
        confidence = float(parsed.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0

    classification = {
        "available": True,
        "label": None if label == "none" else label,
        "confidence": confidence,
        "reason": str(parsed.get("reason", "") or ""),
        "source": "llm",
    }
    _LLM_CLASSIFICATION_CACHE[cache_key] = {
        **classification,
        "expires_at": now + _LLM_CACHE_TTL_SECONDS,
    }
    return classification


def _regex_fallback_classify(text: str) -> str | None:
    for signal_type, patterns in _FALLBACK_PATTERNS.items():
        if any(pattern.search(text) for pattern in patterns):
            return signal_type
    return None


def _classify_signal(text: str) -> str | None:
    """Classify text into a signal type, or None if nothing interesting."""
    llm_result = _llm_classify_signal(text)
    if llm_result.get("available"):
        confidence = float(llm_result.get("confidence", 0.0) or 0.0)
        label = llm_result.get("label")
        if label is None and confidence >= _LLM_CONFIDENCE_THRESHOLD:
            return None
        if isinstance(label, str) and confidence >= _LLM_CONFIDENCE_THRESHOLD:
            return label

    scores = _semantic_signal_scores(text)
    if scores:
        ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        winner, winner_score = ordered[0]
        runner_up = ordered[1][1] if len(ordered) > 1 else 0.0
        if winner_score >= _SEMANTIC_THRESHOLD and (winner_score - runner_up) >= _SEMANTIC_MARGIN:
            return winner
        if winner_score >= 0.35:
            return None
    return _regex_fallback_classify(text)


def _infer_area(text: str) -> str:
    """Infer operational area from text keywords."""
    text_lower = text.lower()
    area_keywords = {
        "shopify": ["shopify", "tienda", "pedido", "producto", "sku"],
        "google-ads": ["google ads", "campaña", "campaign", "cpc", "pmax", "roas", "gads"],
        "meta-ads": ["meta ads", "facebook", "instagram", "pixel", "capi"],
        "wazion": ["wazion", "whatsapp", "wa ", "baileys"],
        "nexo": ["nexo", "brain", "mcp", "cognitive"],
        "canaririural": ["canarirural", "canari", "reserva", "hospedaje", "alojamiento", "propietario"],
        "seo": ["seo", "search console", "indexación", "ranking"],
        "email": ["email", "correo", "inbox", "smtp"],
    }
    for area, keywords in area_keywords.items():
        for kw in keywords:
            if kw in text_lower:
                return area
    return ""


def detect_drive_signal(
    context_hint: str,
    source: str,
    source_id: str = "",
    area: str = "",
) -> dict | None:
    """Analyze text for interesting signals. Creates or reinforces.

    Called internally from heartbeat and task_close. Not a public MCP tool.
    Returns the signal dict if created/reinforced, None otherwise.
    """
    if not context_hint or len(context_hint.strip()) < 15:
        return None

    signal_type = _classify_signal(context_hint)
    if not signal_type:
        return None

    inferred_area = area or _infer_area(context_hint)

    # Check for similar existing signal
    existing = find_similar_drive_signal(context_hint, inferred_area)
    if existing:
        result = reinforce_drive_signal(existing["id"], context_hint[:500])
        return result if result.get("ok") else None

    # Create new
    result = create_drive_signal(
        signal_type=signal_type,
        source=source,
        source_id=source_id,
        area=inferred_area,
        summary=context_hint[:300],
    )
    return result if result.get("ok") else None


# ── Public MCP tool handlers ─────────────────────────────────────────

def handle_drive_signals(
    status: str = "",
    area: str = "",
    limit: int = 20,
) -> str:
    """List drive signals, optionally filtered by status and area."""
    signals = get_drive_signals(
        status=status or None,
        area=area or None,
        limit=limit,
    )
    if not signals:
        return "No drive signals found."

    stats = drive_signal_stats()
    lines = [
        f"DRIVE SIGNALS ({len(signals)} shown, {stats['total']} total):",
        f"  By status: {json.dumps(stats.get('by_status', {}), ensure_ascii=False)}",
        "",
    ]
    for s in signals:
        evidence_count = 0
        try:
            evidence_count = len(json.loads(s.get("evidence") or "[]"))
        except (json.JSONDecodeError, TypeError):
            pass
        tension_bar = "█" * int(float(s.get("tension", 0)) * 10)
        lines.append(
            f"  [{s['id']}] {s['status'].upper()} {tension_bar} "
            f"t={s['tension']:.2f} ({s['signal_type']}) "
            f"{'[' + s['area'] + '] ' if s.get('area') else ''}"
            f"{s['summary'][:80]}"
            f" ({evidence_count} obs, decay={s.get('decay_rate', 0.05):.2f})"
        )
    return "\n".join(lines)


def handle_drive_reinforce(signal_id: int, observation: str) -> str:
    """Manually reinforce a drive signal with a new observation."""
    if not observation.strip():
        return "ERROR: observation cannot be empty"
    result = reinforce_drive_signal(signal_id, observation)
    if not result.get("ok"):
        return f"ERROR: {result.get('error', 'unknown')}"
    return (
        f"Signal #{signal_id} reinforced: "
        f"tension {result['old_tension']:.2f} → {result['new_tension']:.2f}, "
        f"status {result['old_status']} → {result['new_status']}, "
        f"{result['evidence_count']} observations total"
    )


def handle_drive_act(signal_id: int, outcome: str) -> str:
    """Mark a drive signal as investigated with an outcome."""
    if not outcome.strip():
        return "ERROR: outcome cannot be empty"
    result = update_drive_signal_status(signal_id, "acted", outcome)
    if not result.get("ok"):
        return f"ERROR: {result.get('error', 'unknown')}"
    return f"Signal #{signal_id} marked as ACTED. Outcome recorded."


def handle_drive_dismiss(signal_id: int, reason: str) -> str:
    """Dismiss a drive signal with a reason (archived, not deleted)."""
    if not reason.strip():
        return "ERROR: reason cannot be empty"
    result = update_drive_signal_status(signal_id, "dismissed", reason)
    if not result.get("ok"):
        return f"ERROR: {result.get('error', 'unknown')}"
    return f"Signal #{signal_id} dismissed. Reason: {reason}"
