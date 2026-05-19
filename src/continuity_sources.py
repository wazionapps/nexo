from __future__ import annotations

"""Intent-based ordering for operational continuity sources."""

from dataclasses import dataclass
from typing import Callable, Iterable


SourceProvider = Callable[[str, int], Iterable[dict]]


INTENT_SOURCE_PLAN: dict[str, list[str]] = {
    "prior_work": ["recent", "tasks", "workflows", "change_log", "diary", "memory", "transcripts"],
    "file_location": ["project_atlas", "artifact_registry", "change_log", "local_context", "transcripts"],
    "modify_existing": ["project_atlas", "change_log", "workflows", "tasks", "local_context", "transcripts"],
    "memory_question": ["memory", "diary", "recent", "followups", "transcripts", "local_context"],
    "identity_authorship": ["recent", "change_log", "diary", "transcripts", "continuity_snapshots"],
    "schedule_commitment": ["followups", "reminders", "workflows", "diary", "email", "transcripts"],
    "runtime_diagnosis": ["project_atlas", "system_catalog", "change_log", "tasks", "workflows"],
}

DEFAULT_SOURCE_PLAN = ["recent", "memory", "diary", "transcripts"]
FALLBACK_SOURCES = {"transcripts", "local_context"}


@dataclass(frozen=True)
class ContinuityRecord:
    source: str
    title: str
    summary: str = ""
    evidence_ref: str = ""
    timestamp: float = 0.0
    score: float = 0.0

    @classmethod
    def from_mapping(cls, source: str, row: dict) -> "ContinuityRecord":
        return cls(
            source=source,
            title=str(row.get("title") or row.get("name") or row.get("id") or source),
            summary=str(row.get("summary") or row.get("body") or row.get("text") or ""),
            evidence_ref=str(row.get("evidence_ref") or row.get("ref") or row.get("path") or ""),
            timestamp=float(row.get("timestamp") or row.get("ts") or row.get("created_at_ts") or 0.0),
            score=float(row.get("score") or row.get("_score") or 0.0),
        )


def source_plan_for_intent(intent: str) -> list[str]:
    clean_intent = str(intent or "").strip().lower().replace("-", "_").replace("/", "_")
    return list(INTENT_SOURCE_PLAN.get(clean_intent, DEFAULT_SOURCE_PLAN))


def rank_records(records: Iterable[ContinuityRecord]) -> list[ContinuityRecord]:
    return sorted(
        records,
        key=lambda record: (
            record.source in FALLBACK_SOURCES,
            -record.score,
            -record.timestamp,
            record.source,
        ),
    )


def build_continuity_bundle(
    *,
    intent: str,
    query: str,
    providers: dict[str, SourceProvider],
    limit_per_source: int = 3,
    max_records: int = 8,
) -> dict:
    plan = source_plan_for_intent(intent)
    consulted: list[str] = []
    skipped: list[str] = []
    records: list[ContinuityRecord] = []
    for source in plan:
        provider = providers.get(source)
        if provider is None:
            skipped.append(source)
            continue
        consulted.append(source)
        try:
            rows = list(provider(query, limit_per_source))
        except Exception as exc:
            records.append(ContinuityRecord(
                source=source,
                title=f"{source} unavailable",
                summary=f"{type(exc).__name__}: {exc}",
                score=-1,
            ))
            continue
        records.extend(ContinuityRecord.from_mapping(source, row) for row in rows[:limit_per_source])

    ranked = rank_records(records)[: max(1, int(max_records or 8))]
    return {
        "intent": intent,
        "query": query,
        "plan": plan,
        "consulted": consulted,
        "skipped": skipped,
        "records": [record.__dict__ for record in ranked],
        "has_evidence": any(record.score >= 0 for record in ranked),
    }
