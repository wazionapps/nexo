"""Audit stores that persist data without a verified consumer.

The audit is intentionally side-effect free.  It reads SQLite stores, JSON
queues and transcript folders, then emits a producer -> store -> consumer
matrix plus findings for strong "saved but not used" signals.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Iterable, Sequence

try:
    import paths
except Exception:  # pragma: no cover - keeps the module usable in ad-hoc probes.
    paths = None  # type: ignore[assignment]


TERMINAL_LIFECYCLE_STATUSES = {
    "processed",
    "canonical_done",
    "already_processed",
    "rejected",
}

LOCAL_CONTEXT_WRITE_TABLES = (
    "local_assets",
    "local_chunks",
    "local_entities",
    "entity_facts",
    "local_relations",
    "local_embeddings",
)


@dataclass(frozen=True)
class SavedNotUsedConfig:
    """Runtime paths and live-client evidence for the audit."""

    nexo_db_path: Path | None = None
    local_context_db_path: Path | None = None
    email_db_path: Path | None = None
    transcript_roots: tuple[Path, ...] = ()
    desktop_conversations_path: Path | None = None
    desktop_continuity_queue_path: Path | None = None
    cron_spool_dir: Path | None = None
    live_tools: frozenset[str] = frozenset()
    plugin_catalog_tools: frozenset[str] = frozenset()
    local_context_stale_seconds: float = 24 * 60 * 60


@dataclass(frozen=True)
class StoreAuditRow:
    store_id: str
    producer: str
    store: str
    consumer: str
    last_write: str
    last_use: str
    risk: str
    test: str
    status: str = "ok"
    severity: str = "OK"
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SavedNotUsedFinding:
    alert_id: str
    severity: str
    store_id: str
    producer: str
    store: str
    consumer: str
    last_write: str
    last_use: str
    risk: str
    test: str
    evidence: dict[str, Any] = field(default_factory=dict)


def default_config() -> SavedNotUsedConfig:
    home = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
    if paths is not None:
        nexo_db = _safe_path_call(paths.resolve_db_path)
        local_context = Path(os.environ.get("NEXO_LOCAL_CONTEXT_DB", "")) if os.environ.get("NEXO_LOCAL_CONTEXT_DB") else _safe_path_call(lambda: paths.memory_dir() / "local-context.db")
        email_db = _safe_path_call(lambda: paths.nexo_email_dir() / "nexo-email.db")
        cron_spool = _safe_path_call(lambda: paths.operations_dir() / "cron-spool")
    else:
        nexo_db = home / "runtime" / "data" / "nexo.db"
        local_context = home / "runtime" / "memory" / "local-context.db"
        email_db = home / "runtime" / "nexo-email" / "nexo-email.db"
        cron_spool = home / "runtime" / "operations" / "cron-spool"

    transcript_roots = (
        Path.home() / ".claude" / "projects",
        Path.home() / ".codex" / "sessions",
        Path.home() / ".codex" / "archived_sessions",
    )
    desktop_dir = home / "runtime" / "desktop"
    return SavedNotUsedConfig(
        nexo_db_path=nexo_db,
        local_context_db_path=local_context,
        email_db_path=email_db,
        transcript_roots=transcript_roots,
        desktop_conversations_path=desktop_dir / "conversations.json",
        desktop_continuity_queue_path=desktop_dir / "continuity-queue.json",
        cron_spool_dir=cron_spool,
    )


def audit_saved_not_used(config: SavedNotUsedConfig | None = None) -> dict[str, Any]:
    cfg = config or default_config()
    rows: list[StoreAuditRow] = []
    findings: list[SavedNotUsedFinding] = []

    for row, row_findings in (
        _audit_local_context(cfg),
        _audit_memory_pipeline(cfg),
        _audit_session_diary(cfg),
        _audit_followups_reminders(cfg),
        _audit_workflows(cfg),
        _audit_change_log(cfg),
        _audit_continuity_lifecycle(cfg),
        _audit_transcripts(cfg),
        _audit_email_db(cfg),
        _audit_plugins(cfg),
        _audit_cron_spool(cfg),
    ):
        rows.append(row)
        findings.extend(row_findings)

    severity_order = {"P0": 0, "P1": 1, "P2": 2, "OK": 3}
    findings.sort(key=lambda item: (severity_order.get(item.severity, 9), item.store_id, item.alert_id))
    return {
        "ok": not any(item.severity == "P0" for item in findings),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "stores": [asdict(row) for row in rows],
        "findings": [asdict(item) for item in findings],
        "summary": {
            "stores": len(rows),
            "findings": len(findings),
            "p0": sum(1 for item in findings if item.severity == "P0"),
            "p1": sum(1 for item in findings if item.severity == "P1"),
            "p2": sum(1 for item in findings if item.severity == "P2"),
        },
    }


def format_markdown(report: dict[str, Any]) -> str:
    stores = report.get("stores") if isinstance(report, dict) else []
    findings = report.get("findings") if isinstance(report, dict) else []
    lines = [
        "## saved_not_used audit",
        "",
        "| Store | Producer | Consumer | Last write | Last use | Risk | Test | Status |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for item in stores or []:
        lines.append(
            "| {store_id} | {producer} | {consumer} | {last_write} | {last_use} | {risk} | {test} | {severity}/{status} |".format(
                store_id=_md(item.get("store_id")),
                producer=_md(item.get("producer")),
                consumer=_md(item.get("consumer")),
                last_write=_md(item.get("last_write")),
                last_use=_md(item.get("last_use")),
                risk=_md(item.get("risk")),
                test=_md(item.get("test")),
                severity=_md(item.get("severity")),
                status=_md(item.get("status")),
            )
        )

    lines.extend(["", "### Alerts", ""])
    if not findings:
        lines.append("- No saved_not_used alerts.")
    for finding in findings or []:
        lines.append(
            "- {severity} `{alert_id}` in `{store_id}`: {risk} Test: {test}".format(
                severity=_md(finding.get("severity")),
                alert_id=_md(finding.get("alert_id")),
                store_id=_md(finding.get("store_id")),
                risk=_md(finding.get("risk")),
                test=_md(finding.get("test")),
            )
        )
    return "\n".join(lines)


def _audit_local_context(cfg: SavedNotUsedConfig) -> tuple[StoreAuditRow, list[SavedNotUsedFinding]]:
    store = _path_text(cfg.local_context_db_path)
    producer = "nexo-local-index.py -> local_context.api"
    consumer = "nexo_context_router / nexo_local_context / pre_action_context"
    risk = "Local index is written but may not be consulted before answering."
    test = "local_assets/chunks/entities > 0 requires a recent local_context_queries row."
    if not cfg.local_context_db_path or not cfg.local_context_db_path.exists():
        row = _row("local_context", producer, store, consumer, "", "", risk, test, "missing", "P2", {"path_exists": False})
        return row, []

    evidence: dict[str, Any] = {"path_exists": True}
    with _connect(cfg.local_context_db_path) as conn:
        write_counts = {table: _count(conn, table) for table in LOCAL_CONTEXT_WRITE_TABLES}
        query_count = _count(conn, "local_context_queries")
        last_write = _max_many(conn, [(table, ("updated_at", "last_seen_at", "created_at")) for table in LOCAL_CONTEXT_WRITE_TABLES])
        last_use = _max_timestamp(conn, "local_context_queries", ("created_at", "updated_at"))
        evidence.update({"write_counts": write_counts, "query_count": query_count})

    findings: list[SavedNotUsedFinding] = []
    total_writes = sum(write_counts.values())
    status = "ok"
    severity = "OK"
    if total_writes and not query_count:
        status = "saved_not_used"
        severity = "P0"
        findings.append(
            _finding(
                "local_context_saved_not_used",
                "P0",
                "local_context",
                producer,
                store,
                consumer,
                last_write,
                last_use,
                risk,
                test,
                evidence,
            )
        )
    elif total_writes and _is_stale(last_write, last_use, cfg.local_context_stale_seconds):
        status = "stale_use"
        severity = "P1"
        findings.append(
            _finding(
                "local_context_usage_stale",
                "P1",
                "local_context",
                producer,
                store,
                consumer,
                last_write,
                last_use,
                "El indice local tiene escrituras posteriores a la ultima consulta registrada.",
                test,
                evidence,
            )
        )

    legacy_counts = _legacy_local_context_counts(cfg.nexo_db_path)
    if any(legacy_counts.values()):
        evidence["legacy_main_db_counts"] = legacy_counts
        findings.append(
            _finding(
                "local_context_legacy_tables_non_empty",
                "P2",
                "local_context",
                "legacy migration",
                _path_text(cfg.nexo_db_path),
                consumer,
                last_write,
                last_use,
                "Non-empty legacy local_* tables may attract old consumers.",
                "legacy local_* must be empty or marked as compatibility if a live sidecar exists.",
                {"legacy_main_db_counts": legacy_counts},
            )
        )

    return _row("local_context", producer, store, consumer, last_write, last_use, risk, test, status, severity, evidence), findings


def _audit_memory_pipeline(cfg: SavedNotUsedConfig) -> tuple[StoreAuditRow, list[SavedNotUsedFinding]]:
    store_id = "memory_observations_pipeline"
    producer = "record_memory_event"
    consumer = "memory_observation_worker -> nexo_memory_search / nexo_memory_answer"
    risk = "Captured events may fail to become searchable memory."
    test = "memory_events and memory_observation_queue must end in memory_observations."
    row, conn = _open_main_row(cfg, store_id, producer, consumer, risk, test)
    if conn is None:
        return row, []
    with conn:
        events = _count(conn, "memory_events")
        queue = _count(conn, "memory_observation_queue")
        pending = _count_where(conn, "memory_observation_queue", "status NOT IN ('processed','done','skipped')")
        observations = _count(conn, "memory_observations")
        last_write = _max_many(conn, [("memory_events", ("created_at",)), ("memory_observation_queue", ("created_at", "updated_at"))])
        last_use = _max_many(conn, [("memory_observations", ("updated_at", "created_at")), ("memory_observation_queue", ("processed_at",))])
    evidence = {"events": events, "queue": queue, "pending": pending, "observations": observations}
    findings = []
    severity = "OK"
    status = "ok"
    if events and not observations:
        severity = "P1"
        status = "saved_not_projected"
        findings.append(_finding("memory_events_without_observations", "P1", store_id, producer, row.store, consumer, last_write, last_use, risk, test, evidence))
    if pending:
        severity = "P1"
        status = "pending_queue"
        findings.append(_finding("memory_observation_queue_pending", "P1", store_id, producer, row.store, consumer, last_write, last_use, risk, test, evidence))
    return _row(store_id, producer, row.store, consumer, last_write, last_use, risk, test, status, severity, evidence), findings


def _audit_session_diary(cfg: SavedNotUsedConfig) -> tuple[StoreAuditRow, list[SavedNotUsedFinding]]:
    store_id = "session_diary"
    producer = "write_session_diary / lifecycle fallback"
    consumer = "startup briefing / nexo_session_diary_read / continuity resume"
    risk = "Saved diaries may miss continuity if they only remain as historical rows."
    test = "session_diary must have a documented MCP/startup consumer and a readable recent row."
    row, conn = _open_main_row(cfg, store_id, producer, consumer, risk, test)
    if conn is None:
        return row, []
    with conn:
        total = _count(conn, "session_diary")
        last_write = _max_timestamp(conn, "session_diary", ("created_at",))
    evidence = {"rows": total, "consumer_mode": "static_contract"}
    return _row(store_id, producer, row.store, consumer, last_write, "static:nexo_session_diary_read", risk, test, "covered_static", "OK", evidence), []


def _audit_followups_reminders(cfg: SavedNotUsedConfig) -> tuple[StoreAuditRow, list[SavedNotUsedFinding]]:
    store_id = "followups_reminders"
    producer = "followup/reminder create/update"
    consumer = "nexo_reminders / followup-runner / dashboard"
    risk = "Pending items can accumulate without an effective runner or human read path."
    test = "followups/reminders must have item_history consumption or appear in nexo_reminders."
    row, conn = _open_main_row(cfg, store_id, producer, consumer, risk, test)
    if conn is None:
        return row, []
    with conn:
        followups = _count(conn, "followups")
        reminders = _count(conn, "reminders")
        pending = _count_where(conn, "followups", "COALESCE(status,'PENDING') NOT IN ('DONE','DELETED','COMPLETED')")
        pending += _count_where(conn, "reminders", "COALESCE(status,'PENDING') NOT IN ('DONE','DELETED','COMPLETED')")
        last_write = _max_many(conn, [("followups", ("updated_at", "created_at")), ("reminders", ("updated_at", "created_at"))])
        last_use = _max_timestamp(conn, "item_history", ("created_at",))
    evidence = {"followups": followups, "reminders": reminders, "pending": pending}
    return _row(store_id, producer, row.store, consumer, last_write, last_use or "static:nexo_reminders", risk, test, "covered", "OK", evidence), []


def _audit_workflows(cfg: SavedNotUsedConfig) -> tuple[StoreAuditRow, list[SavedNotUsedFinding]]:
    store_id = "workflows"
    producer = "nexo_goal_open / nexo_workflow_open / nexo_workflow_update"
    consumer = "workflow_resume / workflow_replay / daily audit"
    risk = "Open workflows without checkpoints lose real resumability."
    test = "Open workflow_runs must have workflow_checkpoints or updates."
    row, conn = _open_main_row(cfg, store_id, producer, consumer, risk, test)
    if conn is None:
        return row, []
    with conn:
        runs = _count(conn, "workflow_runs")
        goals = _count(conn, "workflow_goals")
        checkpoints = _count(conn, "workflow_checkpoints")
        open_without_checkpoint = _workflow_open_without_checkpoint(conn)
        last_write = _max_many(conn, [("workflow_runs", ("updated_at", "opened_at")), ("workflow_goals", ("updated_at", "opened_at"))])
        last_use = _max_timestamp(conn, "workflow_checkpoints", ("created_at",))
    evidence = {"runs": runs, "goals": goals, "checkpoints": checkpoints, "open_without_checkpoint": open_without_checkpoint}
    findings = []
    severity = "OK"
    status = "ok"
    if open_without_checkpoint:
        severity = "P1"
        status = "open_without_checkpoint"
        findings.append(_finding("workflow_open_without_checkpoint", "P1", store_id, producer, row.store, consumer, last_write, last_use, risk, test, evidence))
    return _row(store_id, producer, row.store, consumer, last_write, last_use, risk, test, status, severity, evidence), findings


def _audit_change_log(cfg: SavedNotUsedConfig) -> tuple[StoreAuditRow, list[SavedNotUsedFinding]]:
    store_id = "change_log"
    producer = "log_change / nexo_task_close"
    consumer = "nexo_change_log / FTS / daily audit / memory export"
    risk = "Saved changes may not appear in future audits if the consumer does not query change_log."
    test = "change_log must have rows with verify and an MCP consumer."
    row, conn = _open_main_row(cfg, store_id, producer, consumer, risk, test)
    if conn is None:
        return row, []
    with conn:
        total = _count(conn, "change_log")
        missing_verify = _count_where(conn, "change_log", "COALESCE(verify,'') = ''")
        last_write = _max_timestamp(conn, "change_log", ("created_at",))
    evidence = {"rows": total, "missing_verify": missing_verify, "consumer_mode": "static_contract"}
    findings = []
    severity = "OK"
    status = "covered_static"
    if missing_verify:
        severity = "P2"
        status = "weak_evidence"
        findings.append(_finding("change_log_missing_verify", "P2", store_id, producer, row.store, consumer, last_write, "static:nexo_change_log", "Change log rows without verify weaken consumption evidence.", test, evidence))
    return _row(store_id, producer, row.store, consumer, last_write, "static:nexo_change_log", risk, test, status, severity, evidence), findings


def _audit_continuity_lifecycle(cfg: SavedNotUsedConfig) -> tuple[StoreAuditRow, list[SavedNotUsedFinding]]:
    store_id = "continuity_lifecycle"
    producer = "Desktop lifecycle bridge / write_continuity_snapshot"
    consumer = "resume-bundle / lifecycle canonical completion / Desktop flush"
    risk = "Local continuity or close events may be written without canonical confirmation."
    test = "continuity_queue must be empty and lifecycle_events must be terminal/canonical_done."
    row, conn = _open_main_row(cfg, store_id, producer, consumer, risk, test)
    pending_lifecycle = 0
    snapshots = 0
    last_db_write = ""
    last_db_use = ""
    if conn is not None:
        with conn:
            snapshots = _count(conn, "continuity_snapshots")
            pending_lifecycle = _pending_lifecycle_count(conn)
            last_db_write = _max_many(conn, [("continuity_snapshots", ("updated_at", "created_at")), ("lifecycle_events", ("created_at",))])
            last_db_use = _max_timestamp(conn, "lifecycle_events", ("canonical_done_at", "processed_at"))

    desktop_queue_count = _json_queue_count(cfg.desktop_continuity_queue_path)
    conversations_count = _json_queue_count(cfg.desktop_conversations_path)
    file_last_write = _max_file_mtime([cfg.desktop_continuity_queue_path, cfg.desktop_conversations_path])
    last_write = _max_value(last_db_write, file_last_write)
    last_use = last_db_use
    evidence = {
        "snapshots": snapshots,
        "pending_lifecycle": pending_lifecycle,
        "desktop_queue_count": desktop_queue_count,
        "desktop_conversations_count": conversations_count,
    }
    findings = []
    severity = "OK"
    status = "ok"
    if pending_lifecycle or desktop_queue_count:
        severity = "P0"
        status = "not_consumed"
        findings.append(_finding("continuity_lifecycle_not_consumed", "P0", store_id, producer, row.store, consumer, last_write, last_use, risk, test, evidence))
    return _row(store_id, producer, row.store, consumer, last_write, last_use, risk, test, status, severity, evidence), findings


def _audit_transcripts(cfg: SavedNotUsedConfig) -> tuple[StoreAuditRow, list[SavedNotUsedFinding]]:
    store_id = "transcripts"
    producer = "Claude/Codex clients JSONL"
    consumer = "nexo_transcript_search / nexo_transcript_read fallback"
    risk = "Short sessions or sessions outside roots may stay invisible when asking about prior work."
    test = "JSONL with >=3 user messages must be in known roots."
    files = _transcript_files(cfg.transcript_roots)
    counts = [_transcript_user_message_count(path) for path in files]
    usable = sum(1 for count in counts if count >= 3)
    short = sum(1 for count in counts if 0 < count < 3)
    last_write = _max_file_mtime(files)
    evidence = {"files": len(files), "usable": usable, "short_ignored": short}
    findings = []
    severity = "OK"
    status = "ok"
    if files and not usable:
        severity = "P1"
        status = "fallback_unusable"
        findings.append(_finding("transcripts_all_below_min_user_messages", "P1", store_id, producer, _roots_text(cfg.transcript_roots), consumer, last_write, "static:nexo_transcript_search", risk, test, evidence))
    elif short:
        severity = "P2"
        status = "partial_coverage"
        findings.append(_finding("transcripts_short_sessions_ignored", "P2", store_id, producer, _roots_text(cfg.transcript_roots), consumer, last_write, "static:nexo_transcript_search", risk, test, evidence))
    return _row(store_id, producer, _roots_text(cfg.transcript_roots), consumer, last_write, "static:nexo_transcript_search", risk, test, status, severity, evidence), findings


def _audit_email_db(cfg: SavedNotUsedConfig) -> tuple[StoreAuditRow, list[SavedNotUsedFinding]]:
    store_id = "email_db"
    producer = "email_sent_events / nexo-email-monitor"
    consumer = "check-context email lookup / local-context email roots / cognitive ingest"
    risk = "Sent or monitored emails may miss continuity if they are not projected to memory."
    test = "sent_email_events must have a direct consumer or memory_events source_type=email_sent."
    store = _path_text(cfg.email_db_path)
    if not cfg.email_db_path or not cfg.email_db_path.exists():
        return _row(store_id, producer, store, consumer, "", "", risk, test, "missing", "P2", {"path_exists": False}), []
    with _connect(cfg.email_db_path) as conn:
        sent = _count(conn, "sent_email_events")
        last_write = _max_timestamp(conn, "sent_email_events", ("sent_at", "created_at"))
    memory_email_events = _main_count_where(cfg.nexo_db_path, "memory_events", "source_type IN ('email_sent','email')")
    evidence = {"sent_email_events": sent, "memory_email_events": memory_email_events}
    findings = []
    severity = "OK"
    status = "ok"
    if sent and not memory_email_events:
        severity = "P2"
        status = "direct_only"
        findings.append(_finding("email_db_without_memory_projection", "P2", store_id, producer, store, consumer, last_write, "direct:check-context", "Email DB has a direct consumer, but no projection to memory_events.", test, evidence))
    return _row(store_id, producer, store, consumer, last_write, "direct:check-context", risk, test, status, severity, evidence), findings


def _audit_plugins(cfg: SavedNotUsedConfig) -> tuple[StoreAuditRow, list[SavedNotUsedFinding]]:
    store_id = "plugins_catalog_live"
    producer = "plugin_loader._update_registry"
    consumer = "MCP live tool list / nexo_plugin_list / system catalog"
    risk = "Catalog may promise tools the client has not loaded."
    test = "tools in the plugins table or catalog must be present in live_tools."
    row, conn = _open_main_row(cfg, store_id, producer, consumer, risk, test)
    catalog_tools = set(cfg.plugin_catalog_tools)
    last_write = ""
    plugin_rows = 0
    if conn is not None:
        with conn:
            plugin_rows = _count(conn, "plugins")
            catalog_tools.update(_plugin_tools_from_db(conn))
            last_write = _max_timestamp(conn, "plugins", ("loaded_at",))
    live_tools = set(cfg.live_tools)
    missing_live = sorted(catalog_tools - live_tools) if live_tools else []
    evidence = {"plugin_rows": plugin_rows, "catalog_tools": len(catalog_tools), "live_tools": len(live_tools), "missing_live": missing_live[:20]}
    findings = []
    severity = "OK"
    status = "ok"
    if catalog_tools and live_tools and missing_live:
        severity = "P0"
        status = "catalog_not_live"
        findings.append(_finding("plugin_catalog_not_live", "P0", store_id, producer, row.store, consumer, last_write, f"live_tools={len(live_tools)}", risk, test, evidence))
    elif catalog_tools and not live_tools:
        severity = "P2"
        status = "live_unknown"
        findings.append(_finding("plugin_live_tools_not_provided", "P2", store_id, producer, row.store, consumer, last_write, "", "There is no live snapshot to compare the plugin catalog.", test, evidence))
    return _row(store_id, producer, row.store, consumer, last_write, f"live_tools={len(live_tools)}" if live_tools else "", risk, test, status, severity, evidence), findings


def _audit_cron_spool(cfg: SavedNotUsedConfig) -> tuple[StoreAuditRow, list[SavedNotUsedFinding]]:
    store_id = "cron_spool"
    producer = "nexo-cron-wrapper.sh"
    consumer = "cron_runs summary / cron spool reconciler / watchdog"
    risk = "Spool JSON or open cron_runs leave automations in an ambiguous state."
    test = "cron-spool must be empty and non-Evolution cron_runs must have ended_at."
    row, conn = _open_main_row(cfg, store_id, producer, consumer, risk, test)
    open_runs = 0
    last_write = ""
    last_use = ""
    if conn is not None:
        with conn:
            open_runs = _count_where(
                conn,
                "cron_runs",
                "(ended_at IS NULL OR exit_code IS NULL) AND COALESCE(cron_id,'') NOT LIKE '%evolution%'",
            )
            last_write = _max_timestamp(conn, "cron_runs", ("started_at",))
            last_use = _max_timestamp(conn, "cron_runs", ("ended_at",))
    spool_files = sorted((cfg.cron_spool_dir or Path()).glob("*.json")) if cfg.cron_spool_dir and cfg.cron_spool_dir.exists() else []
    last_spool = _max_file_mtime(spool_files)
    last_write = _max_value(last_write, last_spool)
    evidence = {"open_runs": open_runs, "spool_files": len(spool_files), "cron_spool_dir": _path_text(cfg.cron_spool_dir)}
    findings = []
    severity = "OK"
    status = "ok"
    if open_runs or spool_files:
        severity = "P1"
        status = "unreconciled"
        findings.append(_finding("cron_spool_unreconciled", "P1", store_id, producer, row.store, consumer, last_write, last_use, risk, test, evidence))
    return _row(store_id, producer, _path_text(cfg.cron_spool_dir), consumer, last_write, last_use, risk, test, status, severity, evidence), findings


def _open_main_row(
    cfg: SavedNotUsedConfig,
    store_id: str,
    producer: str,
    consumer: str,
    risk: str,
    test: str,
) -> tuple[StoreAuditRow, sqlite3.Connection | None]:
    store = _path_text(cfg.nexo_db_path)
    if not cfg.nexo_db_path or not cfg.nexo_db_path.exists():
        return _row(store_id, producer, store, consumer, "", "", risk, test, "missing", "P2", {"path_exists": False}), None
    return _row(store_id, producer, store, consumer, "", "", risk, test), _connect(cfg.nexo_db_path)


def _row(
    store_id: str,
    producer: str,
    store: str,
    consumer: str,
    last_write: Any,
    last_use: Any,
    risk: str,
    test: str,
    status: str = "ok",
    severity: str = "OK",
    evidence: dict[str, Any] | None = None,
) -> StoreAuditRow:
    return StoreAuditRow(
        store_id=store_id,
        producer=producer,
        store=store,
        consumer=consumer,
        last_write=_time_text(last_write),
        last_use=_time_text(last_use),
        risk=risk,
        test=test,
        status=status,
        severity=severity,
        evidence=evidence or {},
    )


def _finding(
    alert_id: str,
    severity: str,
    store_id: str,
    producer: str,
    store: str,
    consumer: str,
    last_write: Any,
    last_use: Any,
    risk: str,
    test: str,
    evidence: dict[str, Any] | None = None,
) -> SavedNotUsedFinding:
    return SavedNotUsedFinding(
        alert_id=alert_id,
        severity=severity,
        store_id=store_id,
        producer=producer,
        store=store,
        consumer=consumer,
        last_write=_time_text(last_write),
        last_use=_time_text(last_use),
        risk=risk,
        test=test,
        evidence=evidence or {},
    )


def _safe_path_call(func) -> Path | None:
    try:
        value = func()
    except Exception:
        return None
    return Path(value) if value else None


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table,)).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    try:
        return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({_quote(table)})").fetchall()}
    except sqlite3.Error:
        return set()


def _count(conn: sqlite3.Connection, table: str) -> int:
    if not _table_exists(conn, table):
        return 0
    try:
        row = conn.execute(f"SELECT COUNT(*) AS total FROM {_quote(table)}").fetchone()
        return int(row["total"] or 0)
    except sqlite3.Error:
        return 0


def _count_where(conn: sqlite3.Connection, table: str, where_sql: str) -> int:
    if not _table_exists(conn, table):
        return 0
    try:
        row = conn.execute(f"SELECT COUNT(*) AS total FROM {_quote(table)} WHERE {where_sql}").fetchone()
        return int(row["total"] or 0)
    except sqlite3.Error:
        return 0


def _main_count_where(path: Path | None, table: str, where_sql: str) -> int:
    if not path or not path.exists():
        return 0
    with _connect(path) as conn:
        return _count_where(conn, table, where_sql)


def _max_timestamp(conn: sqlite3.Connection, table: str, candidates: Sequence[str]) -> str:
    cols = _columns(conn, table)
    for column in candidates:
        if column not in cols:
            continue
        try:
            row = conn.execute(f"SELECT MAX({_quote(column)}) AS value FROM {_quote(table)}").fetchone()
        except sqlite3.Error:
            continue
        if row and row["value"] not in (None, ""):
            return _time_text(row["value"])
    return ""


def _max_many(conn: sqlite3.Connection, specs: Iterable[tuple[str, Sequence[str]]]) -> str:
    return _max_value(*(_max_timestamp(conn, table, cols) for table, cols in specs))


def _max_value(*values: Any) -> str:
    best = ""
    best_epoch = None
    for value in values:
        text = _time_text(value)
        if not text:
            continue
        epoch = _to_epoch(text)
        if best_epoch is None or (epoch is not None and epoch > best_epoch) or (epoch is None and text > best):
            best = text
            best_epoch = epoch
    return best


def _to_epoch(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    try:
        return float(text)
    except ValueError:
        pass
    try:
        normalized = text.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        return None


def _is_stale(last_write: Any, last_use: Any, threshold_seconds: float) -> bool:
    write_epoch = _to_epoch(last_write)
    use_epoch = _to_epoch(last_use)
    if write_epoch is None or use_epoch is None:
        return False
    return write_epoch - use_epoch > threshold_seconds


def _legacy_local_context_counts(path: Path | None) -> dict[str, int]:
    if not path or not path.exists():
        return {}
    tables = ("local_assets", "local_chunks", "local_entities", "local_embeddings", "local_context_queries")
    with _connect(path) as conn:
        return {table: _count(conn, table) for table in tables if _table_exists(conn, table)}


def _workflow_open_without_checkpoint(conn: sqlite3.Connection) -> int:
    if not _table_exists(conn, "workflow_runs"):
        return 0
    if not _table_exists(conn, "workflow_checkpoints"):
        return _count_where(conn, "workflow_runs", "COALESCE(status,'open') NOT IN ('done','closed','completed','cancelled')")
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM workflow_runs wr
            WHERE COALESCE(wr.status,'open') NOT IN ('done','closed','completed','cancelled')
              AND NOT EXISTS (
                SELECT 1 FROM workflow_checkpoints wc WHERE wc.run_id = wr.run_id
              )
            """
        ).fetchone()
        return int(row["total"] or 0)
    except sqlite3.Error:
        return 0


def _pending_lifecycle_count(conn: sqlite3.Connection) -> int:
    if not _table_exists(conn, "lifecycle_events"):
        return 0
    cols = _columns(conn, "lifecycle_events")
    status_clause = "COALESCE(delivery_status,'accepted') NOT IN ({})".format(
        ",".join("?" for _ in TERMINAL_LIFECYCLE_STATUSES)
    )
    clauses = [status_clause]
    if {"canonical_dispatched_at", "canonical_done_at"}.issubset(cols):
        clauses.append(
            "((canonical_dispatched_at IS NOT NULL AND canonical_dispatched_at != '') "
            "AND (canonical_done_at IS NULL OR canonical_done_at = ''))"
        )
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS total FROM lifecycle_events WHERE " + " OR ".join(clauses),
            tuple(TERMINAL_LIFECYCLE_STATUSES),
        ).fetchone()
        return int(row["total"] or 0)
    except sqlite3.Error:
        return 0


def _plugin_tools_from_db(conn: sqlite3.Connection) -> set[str]:
    if not _table_exists(conn, "plugins"):
        return set()
    tools: set[str] = set()
    try:
        rows = conn.execute("SELECT tool_names FROM plugins").fetchall()
    except sqlite3.Error:
        return set()
    for row in rows:
        raw = str(row["tool_names"] or "")
        for name in raw.split(","):
            clean = name.strip()
            if clean:
                tools.add(clean)
    return tools


def _json_queue_count(path: Path | None) -> int:
    if not path or not path.exists():
        return 0
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return 0
    if not text:
        return 0
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return sum(1 for line in text.splitlines() if line.strip())
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        for key in ("items", "queue", "events", "conversations"):
            value = data.get(key)
            if isinstance(value, list):
                return len(value)
            if isinstance(value, dict):
                return len(value)
        return 1 if data else 0
    return 0


def _transcript_files(roots: Sequence[Path]) -> list[Path]:
    files: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        if not root or not root.exists():
            continue
        for path in sorted(root.rglob("*.jsonl")):
            key = str(path.resolve(strict=False))
            if key in seen:
                continue
            seen.add(key)
            files.append(path)
    return files


def _transcript_user_message_count(path: Path) -> int:
    count = 0
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if _line_is_user_message(line):
                    count += 1
    except OSError:
        return 0
    return count


def _line_is_user_message(line: str) -> bool:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return False
    if payload.get("type") == "user":
        return True
    if payload.get("role") == "user":
        return True
    message = payload.get("message")
    if isinstance(message, dict) and message.get("role") == "user":
        return True
    item = payload.get("item")
    if isinstance(item, dict) and item.get("role") == "user":
        return True
    return False


def _max_file_mtime(paths_: Iterable[Path | None]) -> str:
    values = []
    for path in paths_:
        if path and path.exists():
            try:
                values.append(path.stat().st_mtime)
            except OSError:
                pass
    return _max_value(*values)


def _quote(identifier: str) -> str:
    return '"' + str(identifier).replace('"', '""') + '"'


def _time_text(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, (int, float)):
        if value > 10_000_000:
            try:
                return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
            except (OverflowError, OSError, ValueError):
                return str(value)
        return str(value)
    return str(value)


def _path_text(path: Path | None) -> str:
    return str(path) if path else ""


def _roots_text(roots: Sequence[Path]) -> str:
    return ", ".join(str(root) for root in roots)


def _md(value: Any) -> str:
    text = str(value if value is not None else "")
    return text.replace("|", "\\|").replace("\n", " ")[:240]


def main() -> int:
    print(format_markdown(audit_saved_not_used()))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
