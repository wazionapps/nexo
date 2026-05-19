"""Safe reconciliation plan/apply contract for NEXO automations.

The reconciler never touches LaunchAgents and never deletes spool files. It
only closes retryable stale cron rows and archives terminal spool records when
the dry-run plan proves the action is deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
from typing import Any, Mapping

from automation_supervisor import (
    AutomationSupervisorConfig,
    audit_automation,
    default_config as supervisor_default_config,
    load_job_contracts,
    _is_retryable,
    _normalise_now,
    _spool_cron_id,
)


DEFAULT_SPOOL_STALE_SECONDS = 60 * 60
TERMINAL_SPOOL_STATUSES = {"done", "completed", "failed", "cancelled", "terminal"}


@dataclass(frozen=True)
class AutomationReconcileConfig:
    nexo_db_path: Path | None = None
    manifest_path: Path | None = None
    cron_spool_dir: Path | None = None
    cron_spool_archive_dir: Path | None = None
    now: datetime | None = None
    spool_stale_seconds: int = DEFAULT_SPOOL_STALE_SECONDS


def default_config() -> AutomationReconcileConfig:
    cfg = supervisor_default_config()
    archive = cfg.cron_spool_dir / "archive" if cfg.cron_spool_dir else None
    return AutomationReconcileConfig(
        nexo_db_path=cfg.nexo_db_path,
        manifest_path=cfg.manifest_path,
        cron_spool_dir=cfg.cron_spool_dir,
        cron_spool_archive_dir=archive,
    )


def build_reconciliation_plan(config: AutomationReconcileConfig | None = None) -> dict[str, Any]:
    cfg = config or default_config()
    now = _normalise_now(cfg.now)
    supervisor_cfg = AutomationSupervisorConfig(
        nexo_db_path=cfg.nexo_db_path,
        manifest_path=cfg.manifest_path,
        cron_spool_dir=cfg.cron_spool_dir,
        now=now,
    )
    report = audit_automation(supervisor_cfg)
    contracts, _excluded = load_job_contracts(cfg.manifest_path)
    actions: list[dict[str, Any]] = []

    for row in report.get("open_runs") or []:
        status = str(row.get("status") or "")
        run_id = row.get("run_id")
        cron_id = str(row.get("cron_id") or "")
        if status == "retryable" and run_id is not None:
            actions.append(
                {
                    "action": "close_cron_run",
                    "safe_apply": True,
                    "run_id": run_id,
                    "cron_id": cron_id,
                    "started_at": str(row.get("started_at") or ""),
                    "classification": status,
                    "reason": row.get("reason", ""),
                    "then": "scheduler may retry according to recovery_policy",
                }
            )
        elif status in {"stuck", "abandoned"}:
            actions.append(
                {
                    "action": "manual_review_open_run",
                    "safe_apply": False,
                    "run_id": run_id,
                    "cron_id": cron_id,
                    "classification": status,
                    "reason": row.get("reason", ""),
                }
            )

    spool_items = _classify_spool_items(
        cfg.cron_spool_dir,
        contracts=contracts,
        now=now,
        stale_seconds=cfg.spool_stale_seconds,
    )
    for item in spool_items:
        if item["classification"] == "terminal":
            actions.append(
                {
                    "action": "archive_spool_file",
                    "safe_apply": True,
                    "cron_id": item["cron_id"],
                    "path": item["path"],
                    "content_hash": item.get("content_hash", ""),
                    "classification": "terminal",
                    "reason": item["reason"],
                }
            )
        elif item["classification"] in {"orphaned", "stale", "retryable"}:
            actions.append(
                {
                    "action": "manual_review_spool_file",
                    "safe_apply": False,
                    "cron_id": item["cron_id"],
                    "path": item["path"],
                    "classification": item["classification"],
                    "reason": item["reason"],
                }
            )

    return {
        "ok": True,
        "generated_at": now.isoformat(),
        "dry_run": True,
        "actions": actions,
        "spool_items": spool_items,
        "summary": {
            "actions": len(actions),
            "safe_actions": sum(1 for item in actions if item.get("safe_apply")),
            "manual_actions": sum(1 for item in actions if not item.get("safe_apply")),
            "spool_items": len(spool_items),
        },
    }


def apply_reconciliation_plan(plan: Mapping[str, Any], config: AutomationReconcileConfig | None = None) -> dict[str, Any]:
    cfg = config or default_config()
    now = _normalise_now(cfg.now)
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for action in plan.get("actions") or []:
        if not isinstance(action, Mapping) or not action.get("safe_apply"):
            skipped.append({"action": dict(action) if isinstance(action, Mapping) else action, "reason": "not_safe_apply"})
            continue
        kind = str(action.get("action") or "")
        if kind == "close_cron_run":
            applied.append(_close_cron_run(cfg, action, now=now))
        elif kind == "archive_spool_file":
            applied.append(_archive_spool_file(cfg, action, now=now))
        else:
            skipped.append({"action": dict(action), "reason": "unknown_safe_action"})
    ok = not any(item.get("ok") is False for item in applied)
    return {
        "ok": ok,
        "applied": applied,
        "skipped": skipped,
        "summary": {
            "applied": len(applied),
            "skipped": len(skipped),
            "errors": sum(1 for item in applied if item.get("ok") is False),
        },
    }


def _classify_spool_items(
    spool_dir: Path | None,
    *,
    contracts: Mapping[str, Any],
    now: datetime,
    stale_seconds: int,
) -> list[dict[str, Any]]:
    if spool_dir is None or not spool_dir.exists():
        return []
    result: list[dict[str, Any]] = []
    for path in sorted(spool_dir.glob("*.json")):
        if not path.is_file():
            continue
        payload = _load_json(path)
        cron_id = _spool_cron_id(path, contracts)
        contract = contracts.get(cron_id)
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        age_seconds = int((now - mtime).total_seconds())
        status = str(payload.get("status") or "").strip().lower() if isinstance(payload, dict) else ""
        terminal = bool(isinstance(payload, dict) and payload.get("terminal") is True) or status in TERMINAL_SPOOL_STATUSES
        if contract is None:
            classification = "orphaned"
            reason = "spool item does not match a declared non-Evolution cron"
        elif terminal:
            classification = "terminal"
            reason = "spool item is marked terminal and can be archived"
        elif age_seconds > max(1, int(stale_seconds or DEFAULT_SPOOL_STALE_SECONDS)):
            if _is_retryable(contract):
                classification = "retryable"
                reason = "stale spool item belongs to a retryable/idempotent cron"
            else:
                classification = "stale"
                reason = "stale spool item has no retry contract"
        else:
            classification = "pending"
            reason = "spool item is recent and pending normal processing"
        result.append(
            {
                "cron_id": cron_id,
                "path": str(path),
                "age_seconds": max(age_seconds, 0),
                "classification": classification,
                "reason": reason,
                "content_hash": _file_sha256(path),
            }
        )
    return result


def _close_cron_run(cfg: AutomationReconcileConfig, action: Mapping[str, Any], *, now: datetime) -> dict[str, Any]:
    db_path = cfg.nexo_db_path
    if db_path is None or not db_path.is_file():
        return {"ok": False, "action": "close_cron_run", "error": "db_missing"}
    run_id = action.get("run_id")
    try:
        run_id_int = int(run_id)
    except Exception:
        return {"ok": False, "action": "close_cron_run", "error": "invalid_run_id", "run_id": run_id}
    current = _current_open_run(cfg, run_id_int)
    expected_cron_id = str(action.get("cron_id") or "")
    expected_classification = str(action.get("classification") or "")
    expected_started_at = str(action.get("started_at") or "")
    if not expected_cron_id or not expected_classification or not expected_started_at:
        return {
            "ok": False,
            "action": "close_cron_run",
            "error": "missing_plan_evidence",
            "run_id": run_id_int,
        }
    if not current:
        return {"ok": False, "action": "close_cron_run", "error": "stale_plan_run_not_open", "run_id": run_id_int}
    if (
        str(current.get("cron_id") or "") != expected_cron_id
        or str(current.get("status") or "") != expected_classification
        or str(current.get("started_at") or "") != expected_started_at
        or expected_classification != "retryable"
    ):
        return {
            "ok": False,
            "action": "close_cron_run",
            "error": "stale_plan_run_changed",
            "run_id": run_id_int,
            "expected_cron_id": expected_cron_id,
            "current_cron_id": current.get("cron_id", ""),
            "current_status": current.get("status", ""),
            "current_started_at": current.get("started_at", ""),
        }
    conn = sqlite3.connect(str(db_path), timeout=5)
    try:
        cursor = conn.execute(
            """
            UPDATE cron_runs
            SET ended_at = ?, exit_code = COALESCE(exit_code, 75),
                summary = CASE WHEN COALESCE(summary, '') = '' THEN ? ELSE summary END,
                error = CASE WHEN COALESCE(error, '') = '' THEN ? ELSE error END
            WHERE id = ? AND cron_id = ? AND started_at = ? AND (ended_at IS NULL OR exit_code IS NULL)
            """,
            (
                now.replace(microsecond=0).isoformat(),
                "closed by automation reconciler",
                str(action.get("reason") or "stale retryable run"),
                run_id_int,
                expected_cron_id,
                expected_started_at,
            ),
        )
        conn.commit()
        return {"ok": True, "action": "close_cron_run", "run_id": run_id_int, "rows": cursor.rowcount}
    finally:
        conn.close()


def _archive_spool_file(
    cfg: AutomationReconcileConfig,
    action: Mapping[str, Any],
    *,
    now: datetime,
) -> dict[str, Any]:
    spool_dir = cfg.cron_spool_dir
    archive_dir = cfg.cron_spool_archive_dir or (spool_dir / "archive" if spool_dir else None)
    if spool_dir is None or archive_dir is None:
        return {"ok": False, "action": "archive_spool_file", "error": "spool_missing"}
    source = Path(str(action.get("path") or ""))
    try:
        source_resolved = source.resolve(strict=True)
        spool_resolved = spool_dir.resolve(strict=True)
        source_resolved.relative_to(spool_resolved)
    except Exception:
        return {"ok": False, "action": "archive_spool_file", "error": "unsafe_path", "path": str(source)}
    current = _current_spool_item(cfg, source_resolved, now=now)
    expected_hash = str(action.get("content_hash") or "").strip()
    if not expected_hash:
        return {
            "ok": False,
            "action": "archive_spool_file",
            "error": "missing_plan_evidence",
            "path": str(source),
        }
    if (
        not current
        or current.get("classification") != "terminal"
        or current.get("cron_id") != str(action.get("cron_id") or "")
        or current.get("content_hash") != expected_hash
    ):
        return {
            "ok": False,
            "action": "archive_spool_file",
            "error": "stale_plan_spool_changed",
            "path": str(source),
            "current": current or {},
        }
    dated_dir = archive_dir / now.strftime("%Y%m%d")
    dated_dir.mkdir(parents=True, exist_ok=True)
    target = dated_dir / source.name
    if target.exists():
        target = dated_dir / f"{source.stem}-{int(now.timestamp())}{source.suffix}"
    shutil.move(str(source_resolved), str(target))
    return {"ok": True, "action": "archive_spool_file", "from": str(source), "to": str(target)}


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _current_open_run(cfg: AutomationReconcileConfig, run_id: int) -> dict[str, Any] | None:
    supervisor_cfg = AutomationSupervisorConfig(
        nexo_db_path=cfg.nexo_db_path,
        manifest_path=cfg.manifest_path,
        cron_spool_dir=cfg.cron_spool_dir,
        now=_normalise_now(cfg.now),
    )
    report = audit_automation(supervisor_cfg)
    for row in report.get("open_runs") or []:
        try:
            if int(row.get("run_id")) == run_id:
                return dict(row)
        except Exception:
            continue
    return None


def _current_spool_item(
    cfg: AutomationReconcileConfig,
    source_resolved: Path,
    *,
    now: datetime,
) -> dict[str, Any] | None:
    contracts, _excluded = load_job_contracts(cfg.manifest_path)
    items = _classify_spool_items(
        cfg.cron_spool_dir,
        contracts=contracts,
        now=now,
        stale_seconds=cfg.spool_stale_seconds,
    )
    for item in items:
        try:
            if Path(str(item.get("path") or "")).resolve(strict=True) == source_resolved:
                return dict(item)
        except Exception:
            continue
    return None


def _file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except Exception:
        return ""
