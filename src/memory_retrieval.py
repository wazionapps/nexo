from __future__ import annotations
"""Evidence-first retrieval for Memory Observations v2."""

import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from db import (
    build_pre_action_context,
    get_memory_observations_by_uids,
    list_memory_events,
    list_memory_observations,
    process_memory_observation_queue,
    search_memory_observations_fts,
    vector_scan_observations,
)

# Weight for the semantic (vector) signal when fused with the lexical/FTS score.
# A strong paraphrase match (high cosine) can carry an observation that the
# token-overlap score missed entirely, while still ranking below an exact
# lexical hit on the same query.
_VECTOR_FUSION_WEIGHT = 0.85
# Minimum cosine for a semantic-only candidate to survive the relaxed filter.
# Below this, a vector "match" is noise and must not resurrect an observation
# that the lexical path already rejected.
_VECTOR_MIN_SCORE = 0.30


def _tokens(text: str) -> set[str]:
    return {
        token.lower()
        for token in re.findall(r"[a-zA-Z0-9]{3,}", text or "")
        if len(token) >= 3
    }


def _score(query: str, text: str, base: float = 0.0) -> float:
    query_tokens = _tokens(query)
    if not query_tokens:
        return base
    haystack = _tokens(text)
    if not haystack:
        return 0.0
    overlap = query_tokens & haystack
    if not overlap:
        return 0.0
    return min(1.0, base + len(overlap) / max(1, len(query_tokens)))


def _model_is_warm() -> bool:
    """True only when embedding the query will NOT trigger a cold model load."""
    try:
        import cognitive._core as cog
    except Exception:
        return False
    try:
        if cog._model_download_disabled():
            return True
    except Exception:
        return False
    return getattr(cog, "_model", None) is not None


def _maybe_query_embedding(query: str):
    """Embed the query ONCE for semantic fusion, or return None.

    CRITICAL latency guard: this never loads a cold model. It returns None
    (degrading to the FTS/token path) unless the deterministic offline fallback
    is active or the real model is already warm in-process. Any failure also
    yields None.
    """
    clean = (query or "").strip()
    if not clean:
        return None
    if not _model_is_warm():
        return None
    try:
        import cognitive._core as cog

        return cog.embed(clean)
    except Exception:
        return None


def _project_hint_values(project_hint: str = "") -> set[str]:
    clean = (project_hint or "").strip()
    if not clean:
        return set()
    lowered = clean.lower()
    values = {lowered}
    if "/" in clean or "\\" in clean:
        path = Path(clean)
        values.update(part.lower() for part in path.parts if part and part not in {"/", "\\"})
        if path.name:
            values.add(path.name.lower())
    return {value for value in values if value}


def _project_matches(project_key: str = "", project_hint: str = "") -> bool:
    hint_values = _project_hint_values(project_hint)
    if not hint_values:
        return True
    key_values = _project_hint_values(project_key)
    if not key_values:
        return True
    return bool(hint_values & key_values)


def _parse_time_range(value: str = "") -> tuple[float | None, float | None, str]:
    clean = (value or "").strip().lower()
    now = datetime.now()
    if clean in {"today", "hoy"}:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        return start.timestamp(), end.timestamp(), "today"
    if clean in {"yesterday", "ayer"}:
        end = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start = end - timedelta(days=1)
        return start.timestamp(), end.timestamp(), "yesterday"
    if clean in {"anteayer", "day before yesterday"}:
        end = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
        start = end - timedelta(days=1)
        return start.timestamp(), end.timestamp(), "day_before_yesterday"
    match = re.fullmatch(r"last\s+(\d+)\s*(h|hour|hours|d|day|days)", clean)
    if match:
        amount = int(match.group(1))
        unit = match.group(2)
        delta = timedelta(hours=amount) if unit.startswith("h") else timedelta(days=amount)
        return (now - delta).timestamp(), now.timestamp(), clean

    # Operator bug (session ff78ff94, 11-jun): absolute values silently fell
    # through to (None, None, "") which DISABLED the filter — asking for a
    # specific past day returned the most recent events instead. Support ISO
    # dates, ISO ranges (date end is inclusive: bound = next midnight), ISO
    # datetimes, and epoch seconds / epoch ranges.
    def _point(text, *, end_of_day=False):
        text = text.strip()
        if re.fullmatch(r"\d{9,}(\.\d+)?", text):
            return float(text), False
        try:
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
                day = datetime.fromisoformat(text)
                if end_of_day:
                    return (day + timedelta(days=1)).timestamp(), True
                return day.timestamp(), True
            return datetime.fromisoformat(text).timestamp(), False
        except ValueError:
            return None, False

    if ".." in clean:
        left, _, right = clean.partition("..")
        start_ts, _ = _point(left)
        end_ts, _ = _point(right, end_of_day=True)
        if start_ts is not None and end_ts is not None and end_ts > start_ts:
            return start_ts, end_ts, f"range:{clean}"
        return None, None, ""

    point_ts, is_date = _point(clean)
    if point_ts is not None:
        if is_date:
            return point_ts, point_ts + 86400, f"day:{clean}"
        # Single datetime/epoch: a one-hour window centred forward.
        return point_ts, point_ts + 3600, f"at:{clean}"
    return None, None, ""


def _within_range(ts: Any, start: float | None, end: float | None) -> bool:
    if start is None and end is None:
        return True
    try:
        value = float(ts or 0)
    except Exception:
        return False
    if start is not None and value < start:
        return False
    if end is not None and value >= end:
        return False
    return True


def _event_to_candidate(item: dict, query: str) -> dict:
    paths = item.get("file_paths") or []
    text = " ".join(
        str(part)
        for part in [
            item.get("event_uid"),
            item.get("event_type"),
            item.get("source_type"),
            item.get("source_id"),
            item.get("tool_name"),
            " ".join(paths),
            item.get("raw_ref"),
            item.get("metadata"),
        ]
    )
    score = _score(query, text, base=0.15)
    return {
        "kind": "event",
        "id": item.get("id"),
        "uid": item.get("event_uid"),
        "type": item.get("event_type"),
        "subject": item.get("source_id") or item.get("tool_name") or item.get("event_uid"),
        "summary": f"{item.get('event_type')} from {item.get('source_type')}:{item.get('source_id')}".rstrip(":"),
        "created_at": item.get("created_at"),
        "score": round(score, 4),
        "evidence_refs": [f"memory_event:{item.get('event_uid')}"] + ([item.get("raw_ref")] if item.get("raw_ref") else []),
        "source": item,
    }


def _observation_to_candidate(item: dict, query: str) -> dict:
    text = " ".join(
        str(part)
        for part in [
            item.get("observation_uid"),
            item.get("observation_type"),
            item.get("subject"),
            item.get("summary"),
            item.get("facts"),
            item.get("entities"),
        ]
    )
    score = _score(query, text, base=float(item.get("salience") or 0.0) * 0.25)
    return {
        "kind": "observation",
        "id": item.get("id"),
        "uid": item.get("observation_uid"),
        "type": item.get("observation_type"),
        "subject": item.get("subject"),
        "summary": item.get("summary"),
        "created_at": item.get("created_at"),
        "score": round(score, 4),
        "evidence_refs": item.get("evidence_refs") or [],
        "source": item,
    }


def memory_search(
    query: str,
    *,
    project_hint: str = "",
    time_range: str = "",
    depth: str = "brief",
    limit: int = 10,
    process_queue: bool = True,
) -> dict:
    """Search observations first, then raw events, with evidence refs."""
    if process_queue:
        process_memory_observation_queue(limit=50)
    start, end, resolved_range = _parse_time_range(time_range or query)
    clean_query = (query or "").strip()
    max_items = max(1, min(int(limit or 10), 50))

    observations_by_uid: dict[str, dict] = {}
    for item in search_memory_observations_fts(
        clean_query,
        project_key="",
        limit=max_items * 3,
        start_ts=start,
        end_ts=end,
    ):
        uid = item.get("observation_uid") or f"id:{item.get('id')}"
        observations_by_uid[uid] = item
    for item in list_memory_observations(
        query=clean_query,
        project_key="",
        limit=max_items * 3,
        start_ts=start,
        end_ts=end,
    ):
        uid = item.get("observation_uid") or f"id:{item.get('id')}"
        observations_by_uid.setdefault(uid, item)
    # Semantic fusion: embed the query ONCE (only when a model is already warm —
    # never trigger a cold model load on this latency path) and run a bounded
    # vector scan over precomputed observation embeddings. Paraphrases that the
    # lexical/FTS path missed are pulled in here.
    vector_scores: dict[str, float] = {}
    if clean_query:
        query_vector = _maybe_query_embedding(clean_query)
        if query_vector is not None:
            for hit in vector_scan_observations(
                query_vector,
                limit=max_items * 3,
                start_ts=start,
                end_ts=end,
                min_score=_VECTOR_MIN_SCORE,
            ):
                uid = hit.get("observation_uid")
                if uid:
                    vector_scores[uid] = float(hit.get("vector_score") or 0.0)
            # Materialise semantic-only observations the lexical scan did not see.
            missing_uids = [uid for uid in vector_scores if uid not in observations_by_uid]
            if missing_uids:
                for uid, item in get_memory_observations_by_uids(missing_uids).items():
                    observations_by_uid.setdefault(uid, item)

    observations = list(observations_by_uid.values())
    events = list_memory_events(
        query=clean_query,
        project_key="",
        limit=max_items * 3,
        start_ts=start,
        end_ts=end,
    )

    candidates = []
    for item in observations:
        if not _within_range(item.get("created_at"), start, end):
            continue
        if not _project_matches(item.get("project_key") or "", project_hint):
            continue
        candidate = _observation_to_candidate(item, clean_query)
        uid = item.get("observation_uid") or f"id:{item.get('id')}"
        vector_score = vector_scores.get(uid, 0.0)
        if vector_score > 0:
            # Fuse: keep the higher of the lexical score and the weighted vector
            # signal so a strong paraphrase survives while exact lexical hits
            # still outrank weak semantic ones.
            fused = max(float(candidate.get("score") or 0.0), _VECTOR_FUSION_WEIGHT * vector_score)
            candidate["score"] = round(fused, 4)
            candidate["vector_score"] = round(vector_score, 4)
        candidates.append(candidate)
    candidates.extend(
        _event_to_candidate(item, clean_query)
        for item in events
        if _within_range(item.get("created_at"), start, end)
        and _project_matches(item.get("project_key") or "", project_hint)
    )

    if clean_query:
        # Relaxed filter: a candidate survives if it has a positive lexical score
        # OR a qualifying semantic (vector) match. Previously the hard score>0
        # filter dropped semantic-only paraphrase hits before they could rank.
        candidates = [
            item
            for item in candidates
            if item.get("score", 0) > 0 or item.get("vector_score", 0) > 0
        ]
    candidates.sort(key=lambda item: (item.get("score", 0), item.get("created_at") or 0), reverse=True)
    candidates = candidates[:max_items]

    hot_context = None
    if depth in {"timeline", "evidence", "raw"}:
        try:
            hot_context = build_pre_action_context(query=clean_query, hours=168, limit=5)
        except Exception:
            hot_context = None

    return {
        "query": clean_query,
        "project_hint": project_hint,
        "time_range": resolved_range or time_range,
        "depth": depth,
        "count": len(candidates),
        "candidates": candidates,
        "hot_context": hot_context,
        "has_evidence": any(item.get("evidence_refs") for item in candidates),
    }


def memory_timeline(query: str = "", *, project_hint: str = "", time_range: str = "", limit: int = 20) -> dict:
    result = memory_search(
        query=query,
        project_hint=project_hint,
        time_range=time_range,
        depth="timeline",
        limit=limit,
    )
    result["candidates"].sort(key=lambda item: item.get("created_at") or 0)
    return result


def format_memory_search(result: dict) -> str:
    candidates = result.get("candidates") or []
    if not candidates:
        return "There is not enough evidence in Memory Observations for that query."
    lines = [f"MEMORY SEARCH ({len(candidates)}) — {result.get('query') or '(no query)'}"]
    for item in candidates:
        refs = item.get("evidence_refs") or []
        refs_note = f" refs={', '.join(refs[:3])}" if refs else " refs=none"
        lines.append(
            f"- [{item.get('score'):.2f}] {item.get('kind')}:{item.get('type')} "
            f"{item.get('subject') or '-'} — {item.get('summary')}{refs_note}"
        )
    return "\n".join(lines)


def answer_memory_question(query: str, *, project_hint: str = "", time_range: str = "", limit: int = 5) -> str:
    result = memory_search(
        query=query,
        project_hint=project_hint,
        time_range=time_range,
        depth="evidence",
        limit=limit,
    )
    candidates = result.get("candidates") or []
    evidence_candidates = [item for item in candidates if item.get("evidence_refs")]
    if not evidence_candidates:
        return "There is not enough evidence in new memory to answer that without inventing."
    lines = ["Evidence-based answer:"]
    for item in evidence_candidates[:limit]:
        refs = item.get("evidence_refs") or []
        refs_note = ", ".join(refs[:3]) if refs else "no refs"
        lines.append(f"- {item.get('summary')} ({refs_note})")
    return "\n".join(lines)
