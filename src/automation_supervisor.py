"""Side-effect free supervisor for NEXO automations.

The supervisor reconciles the cron manifest, LaunchAgent inventory,
``cron_runs`` open rows and the cron-spool directory.  It deliberately reports
instead of repairing so tests and future wiring can run without touching live
LaunchAgents or production cron state.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Iterable, Mapping

try:
    import paths
except Exception:  # pragma: no cover - keeps ad-hoc imports usable.
    paths = None  # type: ignore[assignment]


DEFAULT_STUCK_AFTER_SECONDS = 60 * 60
DEFAULT_SPOOL_WARN_THRESHOLD = 0
TERMINAL_SEVERITIES = {"P0", "P1", "P2"}


@dataclass(frozen=True)
class AutomationSupervisorConfig:
    nexo_db_path: Path | None = None
    manifest_path: Path | None = None
    cron_spool_dir: Path | None = None
    launchagent_labels: frozenset[str] | None = None
    now: datetime | None = None
    default_stuck_after_seconds: int = DEFAULT_STUCK_AFTER_SECONDS
    spool_warn_threshold: int = DEFAULT_SPOOL_WARN_THRESHOLD


@dataclass(frozen=True)
class JobContract:
    cron_id: str
    launchagent_label: str
    run_type: str
    sla_seconds: int
    recovery_policy: str
    idempotent: bool
    open_run_allowed: bool
    source: str


@dataclass(frozen=True)
class OpenRunClassification:
    run_id: int | None
    cron_id: str
    started_at: str
    age_seconds: int | None
    status: str
    severity: str
    reason: str
    recovery_action: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LaunchAgentClassification:
    cron_id: str
    launchagent_label: str
    status: str
    severity: str
    reason: str


@dataclass(frozen=True)
class CronSpoolClassification:
    cron_id: str
    files: int
    oldest_path: str
    oldest_mtime: str
    status: str
    severity: str
    reason: str


@dataclass(frozen=True)
class EvolutionPolicyClassification:
    status: str
    severity: str
    reason: str
    launchagent_label: str = "com.nexo.evolution"
    desktop_managed: bool = False


def default_config() -> AutomationSupervisorConfig:
    home = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
    if paths is not None:
        nexo_db = _safe_path_call(paths.resolve_db_path)
        manifest = _safe_path_call(lambda: paths.crons_dir() / "manifest.json")
        spool = _safe_path_call(lambda: paths.operations_dir() / "cron-spool")
    else:
        nexo_db = home / "runtime" / "data" / "nexo.db"
        manifest = home / "runtime" / "crons" / "manifest.json"
        spool = home / "runtime" / "operations" / "cron-spool"
    return AutomationSupervisorConfig(
        nexo_db_path=nexo_db,
        manifest_path=manifest,
        cron_spool_dir=spool,
    )


def audit_automation(config: AutomationSupervisorConfig | None = None) -> dict[str, Any]:
    cfg = config or default_config()
    now = _normalise_now(cfg.now)
    contracts, excluded = load_job_contracts(
        cfg.manifest_path,
        default_stuck_after_seconds=cfg.default_stuck_after_seconds,
    )
    open_runs = classify_open_runs(
        cfg.nexo_db_path,
        contracts=contracts,
        now=now,
        default_stuck_after_seconds=cfg.default_stuck_after_seconds,
    )
    launchagents = classify_launchagents(contracts, cfg.launchagent_labels)
    cron_spool = classify_cron_spool(
        cfg.cron_spool_dir,
        contracts=contracts,
        now=now,
        warn_threshold=cfg.spool_warn_threshold,
    )
    evolution = classify_evolution_policy(cfg.manifest_path, cfg.launchagent_labels)
    findings = _collect_findings(open_runs, launchagents, cron_spool, evolution)

    return {
        "ok": not any(item.get("severity") in TERMINAL_SEVERITIES for item in findings),
        "generated_at": now.isoformat(),
        "jobs": [asdict(item) for item in contracts.values()],
        "open_runs": [asdict(item) for item in open_runs],
        "launchagents": [asdict(item) for item in launchagents],
        "cron_spool": [asdict(item) for item in cron_spool],
        "evolution": asdict(evolution),
        "findings": findings,
        "summary": {
            "jobs": len(contracts),
            "open_runs": len(open_runs),
            "launchagents_checked": cfg.launchagent_labels is not None,
            "cron_spool_jobs": len(cron_spool),
            "findings": len(findings),
            "p0": sum(1 for item in findings if item.get("severity") == "P0"),
            "p1": sum(1 for item in findings if item.get("severity") == "P1"),
            "p2": sum(1 for item in findings if item.get("severity") == "P2"),
            "excluded_jobs": sorted(excluded),
            "evolution_status": evolution.status,
        },
    }


def load_job_contracts(
    manifest_path: Path | None,
    *,
    default_stuck_after_seconds: int = DEFAULT_STUCK_AFTER_SECONDS,
) -> tuple[dict[str, JobContract], set[str]]:
    manifest = _load_json(manifest_path, default={"crons": []})
    entries = manifest.get("crons") if isinstance(manifest, Mapping) else []
    contracts: dict[str, JobContract] = {}
    excluded: set[str] = set()
    if not isinstance(entries, list):
        return contracts, excluded

    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        cron_id = str(entry.get("id") or "").strip()
        if not cron_id:
            continue
        run_type = str(entry.get("run_type") or _infer_run_type(entry)).strip() or "scheduled"
        open_run_allowed = bool(entry.get("open_run_allowed") or entry.get("allow_open_run") or run_type == "daemon")
        sla_seconds = _coerce_int(
            entry.get("sla_seconds")
            or entry.get("stuck_after_seconds")
            or _infer_sla_seconds(entry, run_type, default_stuck_after_seconds),
            default_stuck_after_seconds,
        )
        contracts[cron_id] = JobContract(
            cron_id=cron_id,
            launchagent_label=str(entry.get("launchagent_label") or f"com.nexo.{cron_id}"),
            run_type=run_type,
            sla_seconds=max(1, sla_seconds),
            recovery_policy=str(entry.get("recovery_policy") or ""),
            idempotent=bool(entry.get("idempotent")),
            open_run_allowed=open_run_allowed,
            source=str(manifest_path or ""),
        )
    return contracts, excluded


def classify_open_runs(
    db_path: Path | None,
    *,
    contracts: Mapping[str, JobContract],
    now: datetime | None = None,
    default_stuck_after_seconds: int = DEFAULT_STUCK_AFTER_SECONDS,
) -> list[OpenRunClassification]:
    current = _normalise_now(now)
    rows = _load_open_cron_rows(db_path)
    classifications: list[OpenRunClassification] = []
    for row in rows:
        cron_id = str(row.get("cron_id") or "")
        started_at = str(row.get("started_at") or "")
        started = _parse_timestamp(started_at)
        age_seconds = int((current - started).total_seconds()) if started is not None else None
        contract = contracts.get(cron_id)
        if contract is None:
            classifications.append(
                OpenRunClassification(
                    run_id=_coerce_optional_int(row.get("id")),
                    cron_id=cron_id,
                    started_at=started_at,
                    age_seconds=age_seconds,
                    status="abandoned",
                    severity="P1",
                    reason="cron_runs row is open but cron_id is not declared in the non-Evolution manifest",
                    recovery_action="reconcile row manually before relaunching an unknown automation",
                    evidence=_row_evidence(row, contract=None),
                )
            )
            continue
        if age_seconds is None:
            classifications.append(
                OpenRunClassification(
                    run_id=_coerce_optional_int(row.get("id")),
                    cron_id=cron_id,
                    started_at=started_at,
                    age_seconds=None,
                    status="abandoned",
                    severity="P1",
                    reason="cron_runs row has an unparsable started_at timestamp",
                    recovery_action="inspect the row and close or rewrite it with a valid timestamp",
                    evidence=_row_evidence(row, contract=contract),
                )
            )
            continue
        if contract.open_run_allowed:
            classifications.append(
                OpenRunClassification(
                    run_id=_coerce_optional_int(row.get("id")),
                    cron_id=cron_id,
                    started_at=started_at,
                    age_seconds=age_seconds,
                    status="running",
                    severity="OK",
                    reason=f"{contract.run_type} job allows an open cron_runs row",
                    recovery_action="none",
                    evidence=_row_evidence(row, contract=contract),
                )
            )
            continue
        if age_seconds <= contract.sla_seconds:
            classifications.append(
                OpenRunClassification(
                    run_id=_coerce_optional_int(row.get("id")),
                    cron_id=cron_id,
                    started_at=started_at,
                    age_seconds=age_seconds,
                    status="running",
                    severity="OK",
                    reason=f"open row is within SLA ({contract.sla_seconds}s)",
                    recovery_action="wait for normal completion",
                    evidence=_row_evidence(row, contract=contract),
                )
            )
            continue
        if _is_retryable(contract):
            classifications.append(
                OpenRunClassification(
                    run_id=_coerce_optional_int(row.get("id")),
                    cron_id=cron_id,
                    started_at=started_at,
                    age_seconds=age_seconds,
                    status="retryable",
                    severity="P1",
                    reason=f"SLA exceeded ({age_seconds}s > {contract.sla_seconds}s) and recovery policy permits retry",
                    recovery_action=f"close stale row, then retry via {contract.recovery_policy or 'idempotent replay'}",
                    evidence=_row_evidence(row, contract=contract),
                )
            )
        else:
            classifications.append(
                OpenRunClassification(
                    run_id=_coerce_optional_int(row.get("id")),
                    cron_id=cron_id,
                    started_at=started_at,
                    age_seconds=age_seconds,
                    status="stuck",
                    severity="P1",
                    reason=f"SLA exceeded ({age_seconds}s > {contract.sla_seconds}s) without retry contract",
                    recovery_action="inspect process/logs before closing row or relaunching",
                    evidence=_row_evidence(row, contract=contract),
                )
            )
    return sorted(classifications, key=lambda item: (item.severity != "P1", item.status, item.cron_id, item.run_id or 0))


def classify_launchagents(
    contracts: Mapping[str, JobContract],
    launchagent_labels: frozenset[str] | set[str] | list[str] | tuple[str, ...] | None,
) -> list[LaunchAgentClassification]:
    if launchagent_labels is None:
        return []
    labels = {str(item) for item in launchagent_labels}
    results: list[LaunchAgentClassification] = []
    for contract in contracts.values():
        if contract.launchagent_label in labels:
            results.append(
                LaunchAgentClassification(
                    cron_id=contract.cron_id,
                    launchagent_label=contract.launchagent_label,
                    status="loaded",
                    severity="OK",
                    reason="expected non-Evolution LaunchAgent is present in supplied inventory",
                )
            )
        else:
            results.append(
                LaunchAgentClassification(
                    cron_id=contract.cron_id,
                    launchagent_label=contract.launchagent_label,
                    status="missing",
                    severity="P1",
                    reason="expected non-Evolution LaunchAgent is absent from supplied inventory",
                )
            )
    return sorted(results, key=lambda item: (item.severity != "P1", item.cron_id))


def classify_cron_spool(
    spool_dir: Path | None,
    *,
    contracts: Mapping[str, JobContract],
    now: datetime | None = None,
    warn_threshold: int = DEFAULT_SPOOL_WARN_THRESHOLD,
) -> list[CronSpoolClassification]:
    _normalise_now(now)
    if spool_dir is None or not spool_dir.exists():
        return []
    files = sorted(path for path in spool_dir.glob("*.json") if path.is_file())
    grouped: dict[str, list[Path]] = {}
    for path in files:
        cron_id = _spool_cron_id(path, contracts)
        grouped.setdefault(cron_id, []).append(path)

    results: list[CronSpoolClassification] = []
    for cron_id, paths_for_job in grouped.items():
        oldest = min(paths_for_job, key=lambda item: item.stat().st_mtime)
        severity = "P1" if len(paths_for_job) > warn_threshold else "OK"
        status = "unreconciled" if severity == "P1" else "ok"
        reason = (
            f"{len(paths_for_job)} cron-spool JSON file(s) waiting for reconciliation"
            if severity == "P1"
            else "cron-spool count is within threshold"
        )
        results.append(
            CronSpoolClassification(
                cron_id=cron_id,
                files=len(paths_for_job),
                oldest_path=str(oldest),
                oldest_mtime=datetime.fromtimestamp(oldest.stat().st_mtime, tz=timezone.utc).isoformat(),
                status=status,
                severity=severity,
                reason=reason,
            )
        )
    return sorted(results, key=lambda item: (item.severity != "P1", item.cron_id))


def classify_evolution_policy(
    manifest_path: Path | None,
    launchagent_labels: frozenset[str] | set[str] | list[str] | tuple[str, ...] | None,
) -> EvolutionPolicyClassification:
    manifest = _load_json(manifest_path, default={"crons": []})
    entries = manifest.get("crons") if isinstance(manifest, Mapping) else []
    evolution_entry = None
    if isinstance(entries, list):
        for entry in entries:
            if isinstance(entry, Mapping) and _is_evolution(str(entry.get("id") or "")):
                evolution_entry = entry
                break
    if not evolution_entry:
        return EvolutionPolicyClassification(
            status="missing",
            severity="P1",
            reason="Evolution is enabled by product policy but missing from the cron manifest",
        )
    label = str(evolution_entry.get("launchagent_label") or "com.nexo.evolution")
    if launchagent_labels is None:
        return EvolutionPolicyClassification(
            status="unknown",
            severity="P2",
            reason="Evolution is declared, but LaunchAgent inventory was not supplied",
            launchagent_label=label,
        )
    labels = {str(item) for item in launchagent_labels}
    if label in labels:
        return EvolutionPolicyClassification(
            status="enabled_and_loaded",
            severity="OK",
            reason="Evolution is declared and loaded in the supplied inventory",
            launchagent_label=label,
        )
    return EvolutionPolicyClassification(
        status="enabled_but_not_loaded",
        severity="P1",
        reason="Evolution is declared but absent from the supplied inventory",
        launchagent_label=label,
    )


def format_markdown(report: Mapping[str, Any]) -> str:
    summary = report.get("summary") if isinstance(report, Mapping) else {}
    findings = report.get("findings") if isinstance(report, Mapping) else []
    evolution = report.get("evolution") if isinstance(report.get("evolution"), Mapping) else {}
    lines = [
        "### Automation supervisor",
        "",
        "| Area | Result |",
        "|---|---|",
        f"| Jobs | {summary.get('jobs', 0)} |",
        f"| Classified open runs | {summary.get('open_runs', 0)} |",
        f"| Cron-spool jobs with JSON | {summary.get('cron_spool_jobs', 0)} |",
        f"| P1 findings | {summary.get('p1', 0)} |",
        f"| Excluded jobs | {', '.join(summary.get('excluded_jobs') or []) or 'none'} |",
        f"| Evolution policy | {evolution.get('status', 'unknown')} |",
    ]
    lines.extend(["", "| Finding | Severity | Reason |", "|---|---|---|"])
    for item in findings or []:
        lines.append(
            "| {kind}:{key} | {severity} | {reason} |".format(
                kind=_md(item.get("kind")),
                key=_md(item.get("key")),
                severity=_md(item.get("severity")),
                reason=_md(item.get("reason")),
            )
        )
    if not findings:
        lines.append("| none | OK | No pending open rows/spool/LaunchAgents in fixtures |")
    return "\n".join(lines)


def _collect_findings(
    open_runs: Iterable[OpenRunClassification],
    launchagents: Iterable[LaunchAgentClassification],
    cron_spool: Iterable[CronSpoolClassification],
    evolution: EvolutionPolicyClassification | None = None,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for item in open_runs:
        if item.severity != "OK":
            findings.append({"kind": "open_run", "key": f"{item.cron_id}:{item.run_id}", **asdict(item)})
    for item in launchagents:
        if item.severity != "OK":
            findings.append({"kind": "launchagent", "key": item.launchagent_label, **asdict(item)})
    for item in cron_spool:
        if item.severity != "OK":
            findings.append({"kind": "cron_spool", "key": item.cron_id, **asdict(item)})
    if evolution is not None and evolution.severity != "OK":
        findings.append({"kind": "evolution", "key": evolution.launchagent_label, **asdict(evolution)})
    severity_order = {"P0": 0, "P1": 1, "P2": 2, "OK": 3}
    return sorted(findings, key=lambda item: (severity_order.get(str(item.get("severity")), 9), str(item.get("kind")), str(item.get("key"))))


def _load_open_cron_rows(db_path: Path | None) -> list[dict[str, Any]]:
    if db_path is None or not db_path.is_file():
        return []
    conn = None
    try:
        uri = db_path.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, timeout=2, uri=True)
        conn.row_factory = sqlite3.Row
        table = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='cron_runs'").fetchone()
        if table is None:
            return []
        rows = conn.execute(
            """
            SELECT id, cron_id, started_at, ended_at, exit_code, summary, error, duration_secs
            FROM cron_runs
            WHERE ended_at IS NULL OR exit_code IS NULL
            ORDER BY started_at ASC, id ASC
            """
        ).fetchall()
        return [dict(row) for row in rows]
    except sqlite3.Error:
        return []
    finally:
        if conn is not None:
            conn.close()


def _spool_cron_id(path: Path, contracts: Mapping[str, JobContract]) -> str:
    data = _load_json(path, default={})
    if isinstance(data, Mapping):
        for key in ("cron_id", "job_id", "id", "name"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    stem = path.stem
    for cron_id in sorted(contracts, key=len, reverse=True):
        if stem == cron_id or stem.startswith(f"{cron_id}-") or stem.startswith(f"{cron_id}_"):
            return cron_id
    return stem


def _row_evidence(row: Mapping[str, Any], *, contract: JobContract | None) -> dict[str, Any]:
    evidence = {
        "ended_at": row.get("ended_at"),
        "exit_code": row.get("exit_code"),
        "summary": row.get("summary") or "",
        "error": row.get("error") or "",
    }
    if contract is not None:
        evidence.update(
            {
                "run_type": contract.run_type,
                "sla_seconds": contract.sla_seconds,
                "recovery_policy": contract.recovery_policy,
                "idempotent": contract.idempotent,
                "launchagent_label": contract.launchagent_label,
            }
        )
    return evidence


def _is_retryable(contract: JobContract) -> bool:
    policy = contract.recovery_policy.lower()
    return contract.idempotent or policy in {"catchup", "restart", "retry", "replay"}


def _infer_run_type(entry: Mapping[str, Any]) -> str:
    if entry.get("daemon") or entry.get("open_run_allowed") or entry.get("allow_open_run"):
        return "daemon"
    if entry.get("schedule") or entry.get("schedule_strategy"):
        return "scheduled"
    if entry.get("interval_seconds"):
        return "scheduled"
    return "oneshot"


def _infer_sla_seconds(entry: Mapping[str, Any], run_type: str, default: int) -> int:
    if run_type == "daemon":
        return default
    interval = _coerce_optional_int(entry.get("interval_seconds"))
    if interval is not None:
        return max(default, interval * 2)
    return default


def _is_evolution(cron_id: str) -> bool:
    return "evolution" in cron_id.lower()


def _load_json(path: Path | None, *, default: Any) -> Any:
    if path is None or not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _normalise_now(now: datetime | None) -> datetime:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _parse_timestamp(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    for candidate in (text, text.replace(" ", "T")):
        try:
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            continue
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _coerce_int(value: Any, default: int) -> int:
    result = _coerce_optional_int(value)
    return default if result is None else result


def _coerce_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_path_call(fn: Any) -> Path | None:
    try:
        value = fn()
        return Path(value) if value is not None else None
    except Exception:
        return None


def _md(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")
