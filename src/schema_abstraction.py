"""Ola 4 — SCHEMA-ABSTRACTION: distill recurring incident archetypes into
reusable diagnostic templates that prime the COMPLETE diagnosis instantly.

Francisco's ask, in his words: when the SAME class of incident keeps coming back
(the canonical one being "cron exit 0 but the tool failed in SILENCE" — the
wrapper swallowed the error with ``|| echo {}`` and review badges stayed frozen
for three weeks), the system should not re-diagnose it from scratch every time.
It should already KNOW the diagnosis: "check the cron actually executed the tool
and did not swallow the error". That primed checklist is a *diagnostic template*.

This module sits on top of the existing failure-prevention substrate (~70% built):

  * ``failure_prevention_cases`` — the per-incident ledger. Today ``failure_uid``
    is hashed on the EXACT normalized symptom (``failure_prevention._stable_uid``),
    so two differently-worded reports of the same archetype create separate UIDs
    and separate frequency counters — there is NO clustering. That gap is exactly
    what this module closes.
  * ``self_error_detector`` — already the prototype of the silent-failure /
    "shipped but a step was missing" archetype, and the family we start with.
  * ``learnings`` with ``source_authority='code_test_evidence'`` — the learnings
    the self-error detector fires.

What this module adds, and ONLY this (narrow slice, precision-first):

  1. CLUSTERING — group incidents by symptom similarity (NOT exact-hash), reusing
     the resolver's own ``candidate_similarity`` math so we stay in lockstep with
     dedup/merge thresholds used everywhere else.
  2. DISTILLATION — when a cluster of the SAME archetype reaches the recurrence
     threshold (``MIN_CLUSTER_SIZE`` distinct incidents, high confidence), mint a
     diagnostic template: {archetype, symptom pattern, complete diagnosis steps,
     prevention}. Idempotent: deduped by a stable ``archetype_key``.
  3. (injection lives in ``db/_hot_context.build_pre_action_context``; this module
     exposes ``match_templates_for_action`` which that injection point calls.)

Anti-noise contract — PRECISION OVER RECALL (Francisco hates spurious templates)
--------------------------------------------------------------------------------
A spurious template is strictly worse than none. Therefore:
  * A template is minted ONLY from a cluster of >= ``MIN_CLUSTER_SIZE`` DISTINCT
    incidents (distinct ``failure_uid``) of the SAME archetype, above
    ``MIN_TEMPLATE_CONFIDENCE``.
  * One-off incidents, disparate symptoms, and below-threshold clusters mint
    NOTHING (no active template; at most an internal cluster observation).
  * We start with the single archetype the self-error detector already covers
    (``silent_failure``), not a general pattern engine.
  * Cross-area symptom collisions are kept apart by an area gate, mirroring the
    self-error detector.

Everything here is best-effort and non-authoritative: templates are GUIDANCE,
they never block an action, and they never touch high-authority rules. The
distiller is idempotent and safe to re-run (deep-sleep phase or on demand).
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from typing import Any

from db import get_db
from failure_prevention import redact_value

POLICY_VERSION = "schema_abstraction.v1"

# ── Recurrence / precision tunables (conservative by design) ──────────────
# A genuinely recurring class needs at least this many DISTINCT incidents.
MIN_CLUSTER_SIZE = 3
# Two incidents join the same cluster only at/above this symptom similarity.
# Note: incidents are already gated into the SAME (archetype, area) bucket by
# objective markers before this threshold applies, so the threshold only has to
# separate genuinely different sub-symptoms WITHIN one archetype — not provide
# the precision (the archetype/area gate does that). We mirror the resolver's
# own relatedness floor (``find_similar_learnings`` keeps matches > 0.3) so
# paraphrases of the same incident cluster while unrelated text does not.
CLUSTER_SIMILARITY_THRESHOLD = 0.32
# A cluster only mints an ACTIVE template at/above this confidence. Confidence
# rises with incident count and intra-cluster cohesion.
MIN_TEMPLATE_CONFIDENCE = 0.7
# An action only gets a template injected on a CLEAR archetype match.
INJECT_MATCH_THRESHOLD = 0.55


# ── Archetype taxonomy (start narrow: only the silent-failure family) ─────
# Each archetype is a recognizable incident SHAPE, independent of wording. We
# detect it from objective lexical markers in the symptom/missed-signal text.
# The silent_failure archetype is the one the self_error_detector already covers
# ("cron exit 0 but the tool failed silently" / "shipped but a step was missing").
_SILENT_FAILURE_MARKERS = [
    # exit-0-but-failed / swallowed-error shape
    re.compile(r"\b(?:exit(?:ed)?\s*0|exit\s*code\s*0|returned?\s*0|status\s*0)\b", re.IGNORECASE),
    re.compile(r"\b(?:silent(?:ly)?|in\s+silence|swallow(?:ed|ing)?|suppress(?:ed|ing)?|masked?|hid(?:den)?)\b", re.IGNORECASE),
    re.compile(r"\|\|\s*(?:echo|true|:)\b", re.IGNORECASE),  # `|| echo {}` / `|| true`
    re.compile(r"\b(?:no\s+(?:error|alert|alarm|warning)|without\s+(?:error|alert|failing))\b", re.IGNORECASE),
    # shipped-but-a-step-missing shape (the self-error archetype)
    re.compile(r"\b(?:forgot|forgotten|missed|omitted|never\s+(?:created|added|set\s*up|configured|ran|deployed))\b", re.IGNORECASE),
    re.compile(r"\b(?:was\s+(?:never|not)\s+(?:created|added|configured|deployed|wired|registered|executed))\b", re.IGNORECASE),
    re.compile(r"\b(?:missing\s+(?:the\s+)?(?:cron|step|trigger|hook|migration|index|webhook|deploy|alert))\b", re.IGNORECASE),
    re.compile(r"\b(?:ran\s+but\s+(?:did\s*n.?t|never)|appeared\s+(?:to\s+)?(?:work|succeed)\s+but)\b", re.IGNORECASE),
    re.compile(r"\b(?:cron|scheduled|wrapper|launchd|launchagent)\b.{0,60}\b(?:fail|fall|pet|empty|vac)", re.IGNORECASE),
    # Spanish
    re.compile(r"\b(?:silenci|trag(?:ó|aba|a)|tap(?:ó|aba)|enmascar|ocult)", re.IGNORECASE),
    re.compile(r"\b(?:olvid[éeè]|falt[óoa]ba?|no\s+(?:se\s+)?(?:cre[óo]|configur[óo]|despleg[óo]|registr[óo]|ejecut[óo]))\b", re.IGNORECASE),
    re.compile(r"\b(?:corr[íi]a?\s+pero|parec[íi]a\s+(?:que\s+)?(?:funcionaba|iba\s+bien)\s+pero)\b", re.IGNORECASE),
]

ARCHETYPES: dict[str, dict[str, Any]] = {
    "silent_failure": {
        "label": "Silent failure — the job reported success but the real work did not happen",
        "markers": _SILENT_FAILURE_MARKERS,
        # The COMPLETE diagnosis, primed instantly. This is the load-bearing
        # payload: when the archetype reappears, prime these checks first.
        "diagnosis_steps": [
            "Confirm the scheduled job (cron/launchd/wrapper) ACTUALLY executed the underlying tool — not just that the scheduler ran. Check the tool's own log/output, not the wrapper exit code.",
            "Verify the exit code is real: a wrapper that ends with `|| echo {}` / `|| true` / a swallowed exception will exit 0 even when the tool crashed. Grep the command for error-swallowing constructs.",
            "Compare the produced artifact/output against last-known-good. A silent failure typically leaves a STALE or EMPTY result (frozen badges, empty scrape, unchanged file) while everything 'looks' green.",
            "Check that every required side artifact actually exists and ran: the cron entry, the deploy, the webhook, the migration, the browser/runtime dependency. Code landing is necessary, not sufficient.",
            "Confirm there is an ALERT path that escalates on a missing/empty source, so the next occurrence is detected by an alarm, not by the operator noticing weeks later.",
        ],
        "prevention": (
            "For any scheduled/automated path, do not trust the wrapper exit code: assert the tool ran "
            "and produced a fresh, non-empty result, and escalate (email/alert) when a source is missing — "
            "never let an error be swallowed by `|| echo`/`|| true` or a bare exception handler."
        ),
        # Tokens used to match a CURRENT action against this archetype.
        "match_tokens": [
            "cron", "launchd", "launchagent", "scheduled", "wrapper", "scrape",
            "exit", "silent", "swallow", "echo", "deploy", "webhook", "trigger",
            "freeze", "frozen", "stale", "empty", "alert", "health",
        ],
    },
}


def _now() -> float:
    return time.time()


def _stable_uid(*parts: object) -> str:
    payload = "\0".join(str(part or "") for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load_json(value: str, default: Any) -> Any:
    try:
        return json.loads(value or "")
    except Exception:
        return default


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (table,),
    ).fetchone()
    return bool(row)


def _ensure_tables(conn: sqlite3.Connection) -> None:
    if _table_exists(conn, "diagnostic_templates") and _table_exists(conn, "failure_prevention_cases"):
        return
    from db._schema import run_migrations

    run_migrations(conn)


def _normalize(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def classify_archetype(text: str) -> str:
    """Return the archetype key whose markers the text matches, or "".

    A text belongs to an archetype when it hits at least one objective marker.
    Deterministic and pure. Ambiguity (no marker) → "" (no archetype), so the
    incident never seeds a template on its own.
    """
    clean = str(text or "")
    if not clean.strip():
        return ""
    for key, spec in ARCHETYPES.items():
        for marker in spec["markers"]:
            if marker.search(clean):
                return key
    return ""


# ── Incident harvesting ───────────────────────────────────────────────────


def _case_symptom_text(case_row: sqlite3.Row) -> str:
    """Reconstruct the searchable symptom text from a failure case row.

    The free-text fields are stored as field-evidence JSON ({"value_redacted":..}).
    We concatenate symptom + missed_signal + root_cause + corrective_action so
    similarity reflects the whole incident shape, not just the headline.
    """
    parts: list[str] = []
    for col in ("symptom_json", "missed_signal_json", "root_cause_json", "corrective_action_json"):
        field = _load_json(str(case_row[col] or ""), {})
        if isinstance(field, dict):
            parts.append(str(field.get("value_redacted") or ""))
        else:
            parts.append(str(field or ""))
    return " ".join(p for p in parts if p).strip()


def harvest_incidents(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Collect candidate incidents from the substrate (no mutation).

    Two sources, unified into a common shape ``{uid, archetype, area, text}``:
      * ``failure_prevention_cases`` (the per-incident ledger).
      * self-error learnings (``source_authority='code_test_evidence'``) — these
        ARE silent-failure incidents the detector already captured.

    Only incidents that classify into a known archetype are returned; everything
    else is dropped here (anti-noise at the source).
    """
    incidents: list[dict[str, Any]] = []
    seen: set[str] = set()

    # Source 1: failure_prevention_cases (skip already-resolved/false-positive).
    rows = conn.execute(
        """
        SELECT failure_uid, area, failure_type, privacy_level,
               symptom_json, missed_signal_json, root_cause_json, corrective_action_json
          FROM failure_prevention_cases
         WHERE status NOT IN ('rejected','false_positive','expired')
        """
    ).fetchall()
    for row in rows:
        text = _case_symptom_text(row)
        archetype = classify_archetype(text)
        if not archetype:
            continue
        uid = f"case:{row['failure_uid']}"
        if uid in seen:
            continue
        seen.add(uid)
        incidents.append(
            {
                "uid": uid,
                "archetype": archetype,
                "area": _normalize(row["area"]),
                "text": text,
                "privacy_level": str(row["privacy_level"] or "normal"),
            }
        )

    # Source 2: self-error learnings (objective, code/ledger-derived).
    #
    # The persisted ``learnings`` table does NOT carry a source_authority column
    # (that value is consumed by the resolver/cognitive ingest, not stored on the
    # row), so we cannot filter on it. The self-error detector tags its learnings
    # with a distinctive ``reasoning`` marker ("Auto-detected by the self-error
    # detector ...") and a ``prevention``. We harvest those defensively, querying
    # only columns that actually exist in this schema revision.
    if _table_exists(conn, "learnings"):
        cols = {row[1] for row in conn.execute("PRAGMA table_info(learnings)").fetchall()}
        has_reasoning = "reasoning" in cols
        has_prevention = "prevention" in cols
        has_status = "status" in cols
        select_cols = ["id", "category", "title", "content"]
        if has_prevention:
            select_cols.append("prevention")
        if has_reasoning:
            select_cols.append("reasoning")
        where = "WHERE COALESCE(status,'active') = 'active'" if has_status else ""
        lrows = conn.execute(
            f"SELECT {', '.join(select_cols)} FROM learnings {where}"
        ).fetchall()
        for row in lrows:
            keys = row.keys()
            reasoning = str(row["reasoning"]) if "reasoning" in keys and row["reasoning"] else ""
            # Only treat a learning as a self-error INCIDENT when it is objectively
            # one (the detector's marker). Generic learnings are not incidents.
            if "self-error detector" not in reasoning.lower():
                continue
            prevention = str(row["prevention"]) if "prevention" in keys and row["prevention"] else ""
            text = f"{row['title']} {row['content']} {prevention}".strip()
            archetype = classify_archetype(text)
            if not archetype:
                continue
            uid = f"learning:{row['id']}"
            if uid in seen:
                continue
            seen.add(uid)
            incidents.append(
                {
                    "uid": uid,
                    "archetype": archetype,
                    "area": _normalize(row["category"]),
                    "text": text,
                    "privacy_level": "normal",
                }
            )

    return incidents


# ── Clustering ──────────────────────────────────────────────────────────


def _similarity(text_a: str, text_b: str) -> float:
    """Symptom similarity using the resolver's own math.

    Imported lazily so the conftest's repo-import isolation can reload the
    resolver against the temp DB before this is first called.
    """
    from learning_resolver import candidate_similarity

    return float(candidate_similarity(text_a, text_b))


def cluster_incidents(incidents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Greedy single-link clustering within each (archetype, area).

    Returns clusters ``{archetype, area, members:[incident...], cohesion}``.
    Two incidents join the same cluster when their symptom similarity is at/above
    ``CLUSTER_SIMILARITY_THRESHOLD``. The area gate keeps cross-project symptom
    collisions apart (mirrors the self-error detector's same-area requirement).

    Deterministic: members are processed in a stable (area, uid) order.
    """
    clusters: list[dict[str, Any]] = []
    # Group by (archetype, area) first so clusters never cross archetype/area.
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for inc in sorted(incidents, key=lambda i: (i["archetype"], i["area"], i["uid"])):
        buckets.setdefault((inc["archetype"], inc["area"]), []).append(inc)

    for (archetype, area), members in buckets.items():
        local: list[dict[str, Any]] = []
        for inc in members:
            placed = False
            for cluster in local:
                # Single-link: join if similar to ANY existing member.
                if any(
                    _similarity(inc["text"], other["text"]) >= CLUSTER_SIMILARITY_THRESHOLD
                    for other in cluster["members"]
                ):
                    cluster["members"].append(inc)
                    placed = True
                    break
            if not placed:
                local.append({"archetype": archetype, "area": area, "members": [inc]})
        clusters.extend(local)

    # Compute cohesion (mean pairwise similarity) for confidence scoring.
    for cluster in clusters:
        members = cluster["members"]
        if len(members) < 2:
            cluster["cohesion"] = 1.0 if members else 0.0
            continue
        sims: list[float] = []
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                sims.append(_similarity(members[i]["text"], members[j]["text"]))
        cluster["cohesion"] = round(sum(sims) / len(sims), 3) if sims else 0.0
    return clusters


def _archetype_key(archetype: str, area: str) -> str:
    return f"{archetype}|{_normalize(area)}"


def _cluster_confidence(cluster: dict[str, Any]) -> float:
    """Confidence that this cluster is a genuine recurring archetype.

    Rises with distinct-incident count (recurrence) and intra-cluster cohesion
    (the members really are the same shape). Capped at 0.95 — never certainty.
    """
    n = len({m["uid"] for m in cluster["members"]})
    if n < MIN_CLUSTER_SIZE:
        return 0.0
    # Reaching MIN_CLUSTER_SIZE distinct incidents that ALL classify into the
    # same objectively-marked archetype in the same area is itself the genuine
    # recurrence signal (not mere wording overlap) — that is where the precision
    # comes from. So confidence is recurrence-led (a qualifying cluster starts at
    # the template floor and rises with extra incidents), with intra-cluster
    # cohesion only as a small positive bonus. Recurrence: n=3 → 0.70, then +0.05
    # per extra incident; cohesion adds up to +0.15. Capped at 0.95 (never certain).
    recurrence = 0.70 + 0.05 * (n - MIN_CLUSTER_SIZE)
    cohesion = float(cluster.get("cohesion") or 0.0)
    confidence = round(min(0.95, recurrence + 0.15 * cohesion), 3)
    return confidence


# ── Distillation ──────────────────────────────────────────────────────────


def _build_symptom_pattern(cluster: dict[str, Any]) -> str:
    """A short, redacted human description of the shared symptom."""
    sample = cluster["members"][0]["text"]
    return redact_value(sample)[:300]


def distill_template_payload(cluster: dict[str, Any]) -> dict[str, Any] | None:
    """Turn a qualifying cluster into a diagnostic-template payload, or None.

    Returns None for clusters below the recurrence/confidence threshold — the
    anti-noise gate. The payload mirrors the archetype's primed diagnosis.
    """
    archetype = cluster["archetype"]
    spec = ARCHETYPES.get(archetype)
    if spec is None:
        return None
    member_uids = sorted({m["uid"] for m in cluster["members"]})
    if len(member_uids) < MIN_CLUSTER_SIZE:
        return None
    confidence = _cluster_confidence(cluster)
    if confidence < MIN_TEMPLATE_CONFIDENCE:
        return None
    area = cluster["area"]
    archetype_key = _archetype_key(archetype, area)
    return {
        "template_uid": _stable_uid(POLICY_VERSION, archetype_key),
        "archetype": archetype,
        "archetype_key": archetype_key,
        "failure_type": "tool" if archetype == "silent_failure" else "other",
        "area": area,
        "symptom_pattern": _build_symptom_pattern(cluster),
        "diagnosis_steps": list(spec["diagnosis_steps"]),
        "prevention": spec["prevention"],
        "match_tokens": list(spec["match_tokens"]),
        "member_uids": member_uids,
        "incident_count": len(member_uids),
        "confidence": confidence,
        "label": spec["label"],
    }


def _upsert_template(conn: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    """Idempotent insert/refresh of a diagnostic template by template_uid.

    On re-run with new members the row is REFRESHED (member set + count grow),
    never duplicated. A retired template is NOT silently re-activated here.
    """
    now = _now()
    existing = conn.execute(
        "SELECT id, status, member_uids_json FROM diagnostic_templates WHERE template_uid = ?",
        (payload["template_uid"],),
    ).fetchone()
    if existing is None:
        conn.execute(
            """
            INSERT INTO diagnostic_templates (
                template_uid, policy_version, archetype, archetype_key,
                failure_type, area, symptom_pattern, diagnosis_steps_json,
                prevention, match_tokens_json, member_uids_json, incident_count,
                confidence, status, privacy_level, created_at, updated_at,
                metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 'normal', ?, ?, ?)
            """,
            (
                payload["template_uid"],
                POLICY_VERSION,
                payload["archetype"],
                payload["archetype_key"],
                payload["failure_type"],
                payload["area"],
                payload["symptom_pattern"],
                _json(payload["diagnosis_steps"]),
                payload["prevention"],
                _json(payload["match_tokens"]),
                _json(payload["member_uids"]),
                payload["incident_count"],
                payload["confidence"],
                now,
                now,
                _json({"label": payload["label"]}),
            ),
        )
        conn.commit()
        return {"created": True, "refreshed": False, "template_uid": payload["template_uid"]}

    # Refresh: only update mutable fields; keep created_at + status.
    prior_members = set(_load_json(str(existing["member_uids_json"] or ""), []))
    new_members = set(payload["member_uids"])
    changed = new_members != prior_members
    conn.execute(
        """
        UPDATE diagnostic_templates
           SET symptom_pattern = ?, diagnosis_steps_json = ?, prevention = ?,
               match_tokens_json = ?, member_uids_json = ?, incident_count = ?,
               confidence = ?, updated_at = ?
         WHERE template_uid = ?
        """,
        (
            payload["symptom_pattern"],
            _json(payload["diagnosis_steps"]),
            payload["prevention"],
            _json(payload["match_tokens"]),
            _json(payload["member_uids"]),
            payload["incident_count"],
            payload["confidence"],
            now,
            payload["template_uid"],
        ),
    )
    conn.commit()
    return {"created": False, "refreshed": changed, "template_uid": payload["template_uid"]}


def distill_templates(conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Full distillation pass: harvest → cluster → distill → idempotent upsert.

    Safe to re-run (deep-sleep phase or on demand). Returns a report. Never
    raises on malformed substrate; never blocks anything.
    """
    conn = conn or get_db()
    _ensure_tables(conn)
    incidents = harvest_incidents(conn)
    clusters = cluster_incidents(incidents)

    created = 0
    refreshed = 0
    minted: list[dict[str, Any]] = []
    skipped_low: list[dict[str, Any]] = []
    for cluster in clusters:
        payload = distill_template_payload(cluster)
        if payload is None:
            n = len({m["uid"] for m in cluster["members"]})
            skipped_low.append(
                {
                    "archetype": cluster["archetype"],
                    "area": cluster["area"],
                    "incident_count": n,
                    "reason": "below_min_cluster_size" if n < MIN_CLUSTER_SIZE else "below_min_confidence",
                }
            )
            continue
        result = _upsert_template(conn, payload)
        if result["created"]:
            created += 1
        elif result["refreshed"]:
            refreshed += 1
        minted.append(
            {
                "template_uid": payload["template_uid"],
                "archetype": payload["archetype"],
                "area": payload["area"],
                "incident_count": payload["incident_count"],
                "confidence": payload["confidence"],
            }
        )

    return {
        "ok": True,
        "incidents": len(incidents),
        "clusters": len(clusters),
        "templates_created": created,
        "templates_refreshed": refreshed,
        "templates_minted": minted,
        "skipped_low_signal": skipped_low,
    }


# ── Matching / injection support ──────────────────────────────────────────


def _tokenize(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9_]+", _normalize(text)) if len(t) > 2}


def match_templates_for_action(
    *,
    query: str = "",
    area: str = "",
    files: str = "",
    conn: sqlite3.Connection | None = None,
    limit: int = 1,
) -> list[dict[str, Any]]:
    """Return active templates whose archetype CLEARLY matches the current action.

    Used by ``build_pre_action_context`` to PRIME the diagnosis. Precision-first:
    a template matches only when (a) the action text classifies into the SAME
    archetype AND (b) token overlap with the template's match tokens clears
    ``INJECT_MATCH_THRESHOLD``. A vague/unrelated action matches nothing.
    """
    conn = conn or get_db()
    if not _table_exists(conn, "diagnostic_templates"):
        return []
    action_text = " ".join(str(p or "") for p in (query, files)).strip()
    if not action_text:
        return []
    action_archetype = classify_archetype(action_text)
    action_tokens = _tokenize(action_text)
    if not action_tokens:
        return []
    clean_area = _normalize(area)

    rows = conn.execute(
        """
        SELECT template_uid, archetype, archetype_key, area, failure_type,
               symptom_pattern, diagnosis_steps_json, prevention,
               match_tokens_json, incident_count, confidence, metadata_json
          FROM diagnostic_templates
         WHERE status = 'active'
        """
    ).fetchall()

    scored: list[tuple[float, dict[str, Any]]] = []
    for row in rows:
        match_tokens = set(_load_json(str(row["match_tokens_json"] or ""), []))
        if not match_tokens:
            continue
        overlap = action_tokens & match_tokens
        token_score = len(overlap) / max(1, min(len(action_tokens), len(match_tokens)))
        # Archetype agreement is a HARD precondition (precision-first contract).
        # The action must classify into the SAME archetype as the template before
        # token overlap is even considered. Token overlap alone NEVER qualifies:
        # real actions like "deploy the webhook trigger", "add a health alert to
        # the deploy", or "set up deploy alert health monitoring" share tokens
        # (deploy/webhook/trigger/alert/health) with the silent-failure archetype
        # but are NOT silent-failure incidents — they must not over-fire an
        # injection. Francisco's rule: a spurious template is worse than none.
        archetype_match = bool(action_archetype) and action_archetype == row["archetype"]
        if not archetype_match:
            continue
        # Area must not contradict (empty area on either side is permissive).
        area_ok = (not clean_area) or (not row["area"]) or clean_area == row["area"]
        if not area_ok:
            continue
        # Archetype agreed: token overlap then has to clear the inject threshold
        # (some concrete shared vocabulary, not a bare archetype guess). The
        # archetype bonus keeps the strongest signal weighted highest.
        score = token_score + 0.4
        qualifies = bool(overlap) and score >= INJECT_MATCH_THRESHOLD
        if not qualifies:
            continue
        scored.append(
            (
                round(score, 3),
                {
                    "template_uid": row["template_uid"],
                    "archetype": row["archetype"],
                    "area": row["area"],
                    "failure_type": row["failure_type"],
                    "label": _load_json(str(row["metadata_json"] or ""), {}).get("label", ""),
                    "symptom_pattern": row["symptom_pattern"],
                    "diagnosis_steps": _load_json(str(row["diagnosis_steps_json"] or ""), []),
                    "prevention": row["prevention"],
                    "incident_count": int(row["incident_count"] or 0),
                    "confidence": float(row["confidence"] or 0.0),
                    "match_score": round(score, 3),
                    "matched_tokens": sorted(overlap),
                },
            )
        )

    scored.sort(key=lambda pair: (pair[0], pair[1]["confidence"]), reverse=True)
    return [item for _, item in scored[: max(1, int(limit or 1))]]


def list_templates(*, status: str = "active", limit: int = 50, conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    conn = conn or get_db()
    if not _table_exists(conn, "diagnostic_templates"):
        return []
    clauses: list[str] = []
    params: list[Any] = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM diagnostic_templates {where} ORDER BY confidence DESC, updated_at DESC LIMIT ?",
        params + [max(1, int(limit or 50))],
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        data = dict(row)
        for key in ("diagnosis_steps_json", "match_tokens_json", "member_uids_json", "metadata_json"):
            default = [] if key.endswith("s_json") and key != "metadata_json" else {}
            data[key[:-5]] = _load_json(str(data.pop(key) or ""), default)
        out.append(data)
    return out


def retire_template(template_uid: str, *, reason: str = "", conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Retire a template (lifecycle). Guidance only — never deletes incidents."""
    conn = conn or get_db()
    if not _table_exists(conn, "diagnostic_templates"):
        return {"ok": False, "error": "diagnostic_templates_table_missing"}
    now = _now()
    cur = conn.execute(
        "UPDATE diagnostic_templates SET status='retired', retired_at=?, retired_reason=?, updated_at=? WHERE template_uid=? AND status='active'",
        (now, redact_value(reason)[:240], now, str(template_uid or "").strip()),
    )
    conn.commit()
    return {"ok": True, "retired": cur.rowcount > 0, "template_uid": template_uid}


def format_templates_for_injection(templates: list[dict[str, Any]]) -> str:
    """Render matched templates as a primed-diagnosis block for pre-action context."""
    if not templates:
        return ""
    lines = ["PRIMED DIAGNOSIS (recurring incident archetype matched — diagnose this FIRST):"]
    for tpl in templates:
        head = tpl.get("label") or tpl.get("archetype") or "archetype"
        lines.append(
            f"- [{tpl.get('archetype')}] {head} "
            f"(seen {tpl.get('incident_count')}x, conf={tpl.get('confidence')})"
        )
        for step in (tpl.get("diagnosis_steps") or [])[:5]:
            lines.append(f"    • {step}")
        prevention = tpl.get("prevention")
        if prevention:
            lines.append(f"    ⇒ prevent: {prevention}")
    return "\n".join(lines)


__all__ = [
    "POLICY_VERSION",
    "MIN_CLUSTER_SIZE",
    "MIN_TEMPLATE_CONFIDENCE",
    "ARCHETYPES",
    "classify_archetype",
    "harvest_incidents",
    "cluster_incidents",
    "distill_template_payload",
    "distill_templates",
    "match_templates_for_action",
    "list_templates",
    "retire_template",
    "format_templates_for_injection",
]
