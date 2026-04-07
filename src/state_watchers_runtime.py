from __future__ import annotations
"""Runtime evaluation for persistent state watchers."""

import json
import os
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib import error, request


def _nexo_home() -> Path:
    return Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))


def _db_path() -> Path:
    explicit = os.environ.get("NEXO_TEST_DB") or os.environ.get("NEXO_DB")
    if explicit:
        return Path(explicit)
    return _nexo_home() / "data" / "nexo.db"


def _summary_file() -> Path:
    return _nexo_home() / "operations" / "state-watchers-status.json"


def _manifest_file() -> Path:
    return _nexo_home() / "crons" / "manifest.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _parse_dt(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    for candidate in (normalized, normalized + "T00:00:00+00:00"):
        try:
            parsed = datetime.fromisoformat(candidate)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except Exception:
            continue
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.replace(tzinfo=timezone.utc)
        except Exception:
            continue
    return None


def _load_manifest() -> list[dict]:
    manifest_file = _manifest_file()
    if not manifest_file.is_file():
        return []
    try:
        payload = json.loads(manifest_file.read_text())
    except Exception:
        return []
    crons = payload.get("crons")
    return crons if isinstance(crons, list) else []


def _load_last_cron_run(cron_id: str) -> datetime | None:
    db_path = _db_path()
    if not db_path.is_file():
        return None
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT started_at FROM cron_runs WHERE cron_id = ? ORDER BY started_at DESC LIMIT 1",
            (cron_id,),
        ).fetchone()
    except Exception:
        return None
    finally:
        conn.close()
    if not row or not row[0]:
        return None
    return _parse_dt(str(row[0]))


def _evaluate_repo_drift(watcher: dict) -> dict:
    repo_path = Path(str(watcher.get("target") or "")).expanduser()
    if not repo_path.exists():
        return {"health": "critical", "summary": f"repo path missing: {repo_path}", "evidence": [str(repo_path)]}
    try:
        inside = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception as exc:
        return {"health": "critical", "summary": f"git probe failed: {exc}", "evidence": [str(repo_path)]}
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return {"health": "critical", "summary": f"not a git repo: {repo_path}", "evidence": [inside.stderr.strip() or inside.stdout.strip()]}

    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
        timeout=5,
    )
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
        timeout=5,
    )
    config = watcher.get("config") or {}
    expected_branch = str(config.get("expected_branch") or "").strip()
    dirty = bool(status.stdout.strip())
    health = "healthy"
    evidence = [f"branch={branch.stdout.strip() or 'unknown'}", f"dirty={dirty}"]
    summary = f"repo clean at {repo_path}"
    if expected_branch and branch.stdout.strip() and branch.stdout.strip() != expected_branch:
        health = "degraded"
        summary = f"repo branch drifted from {expected_branch}"
        evidence.append(f"expected_branch={expected_branch}")
    if dirty and not bool(config.get("allow_dirty")):
        health = "degraded" if health == "healthy" else health
        summary = f"repo has uncommitted drift at {repo_path}"
        evidence.extend(status.stdout.strip().splitlines()[:5])
    return {"health": health, "summary": summary, "evidence": evidence}


def _cron_threshold_seconds(cron_payload: dict) -> int | None:
    if cron_payload.get("interval_seconds"):
        return max(int(cron_payload["interval_seconds"]) * 2, 900)
    if cron_payload.get("schedule"):
        return 36 * 3600
    if cron_payload.get("run_at_load"):
        return None
    return 24 * 3600


def _evaluate_cron_drift(watcher: dict) -> dict:
    cron_id = str(watcher.get("target") or "").strip()
    manifest = _load_manifest()
    cron_payload = next((item for item in manifest if str(item.get("id") or "").strip() == cron_id), None)
    if not cron_payload:
        return {"health": "critical", "summary": f"cron missing from manifest: {cron_id}", "evidence": [str(_manifest_file())]}
    threshold = _cron_threshold_seconds(cron_payload)
    if threshold is None:
        return {"health": "healthy", "summary": f"cron {cron_id} is run_at_load-only", "evidence": ["no periodic freshness expected"]}
    last_run = _load_last_cron_run(cron_id)
    if not last_run:
        return {"health": "critical", "summary": f"cron {cron_id} has never run", "evidence": [f"threshold_seconds={threshold}"]}
    age = (datetime.now(timezone.utc) - last_run).total_seconds()
    if age > threshold * 2:
        health = "critical"
    elif age > threshold:
        health = "degraded"
    else:
        health = "healthy"
    return {
        "health": health,
        "summary": f"cron {cron_id} freshness checked",
        "evidence": [f"age_seconds={int(age)}", f"threshold_seconds={threshold}", f"last_run={last_run.isoformat()}"],
    }


def _evaluate_api_health(watcher: dict) -> dict:
    url = str(watcher.get("target") or "").strip()
    config = watcher.get("config") or {}
    timeout = float(config.get("timeout_seconds") or 3)
    allowed_min = int(config.get("allowed_min") or 200)
    allowed_max = int(config.get("allowed_max") or 399)
    method = str(config.get("method") or "GET").upper()
    req = request.Request(url, method=method)
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            code = int(getattr(resp, "status", 200))
    except error.HTTPError as exc:
        code = int(exc.code)
    except Exception as exc:
        return {"health": "critical", "summary": f"API health probe failed for {url}", "evidence": [str(exc)]}
    health = "healthy" if allowed_min <= code <= allowed_max else "critical"
    return {
        "health": health,
        "summary": f"API {url} returned {code}",
        "evidence": [f"status_code={code}", f"allowed={allowed_min}-{allowed_max}"],
    }


def _evaluate_environment_drift(watcher: dict) -> dict:
    config = watcher.get("config") or {}
    mode = str(config.get("mode") or "env_var").strip().lower()
    target = str(watcher.get("target") or "").strip()
    if mode == "env_var":
        current = os.environ.get(target, "")
        expected = str(config.get("expected_value") or "").strip()
        if not current:
            return {"health": "critical", "summary": f"env var missing: {target}", "evidence": [target]}
        if expected and current != expected:
            return {"health": "degraded", "summary": f"env var drift: {target}", "evidence": [f"expected={expected}", f"current={current}"]}
        return {"health": "healthy", "summary": f"env var present: {target}", "evidence": [target]}
    path = Path(target).expanduser()
    if mode == "dir_exists":
        healthy = path.is_dir()
    else:
        healthy = path.exists()
    return {
        "health": "healthy" if healthy else "critical",
        "summary": f"{mode} {'OK' if healthy else 'missing'} for {path}",
        "evidence": [str(path)],
    }


def _evaluate_expiry(watcher: dict) -> dict:
    config = watcher.get("config") or {}
    due_at = _parse_dt(str(config.get("due_at") or watcher.get("target") or ""))
    if not due_at:
        return {"health": "critical", "summary": f"expiry watcher missing due_at: {watcher.get('watcher_id')}", "evidence": []}
    warn_days = int(config.get("warn_days") or 21)
    critical_days = int(config.get("critical_days") or 7)
    remaining = due_at - datetime.now(timezone.utc)
    days = remaining.total_seconds() / 86400
    if days <= critical_days:
        health = "critical"
    elif days <= warn_days:
        health = "degraded"
    else:
        health = "healthy"
    return {
        "health": health,
        "summary": f"expiry watcher '{watcher.get('title')}' due in {days:.1f} days",
        "evidence": [f"due_at={due_at.isoformat()}", f"warn_days={warn_days}", f"critical_days={critical_days}"],
    }


def evaluate_state_watcher(watcher: dict) -> dict:
    watcher_type = str(watcher.get("watcher_type") or "").strip().lower()
    if watcher_type == "repo_drift":
        result = _evaluate_repo_drift(watcher)
    elif watcher_type == "cron_drift":
        result = _evaluate_cron_drift(watcher)
    elif watcher_type == "api_health":
        result = _evaluate_api_health(watcher)
    elif watcher_type == "environment_drift":
        result = _evaluate_environment_drift(watcher)
    elif watcher_type == "expiry":
        result = _evaluate_expiry(watcher)
    else:
        result = {"health": "critical", "summary": f"unsupported watcher type: {watcher_type}", "evidence": []}
    return {
        "watcher_id": watcher.get("watcher_id", ""),
        "title": watcher.get("title", ""),
        "watcher_type": watcher_type,
        "target": watcher.get("target", ""),
        "severity": watcher.get("severity", "warn"),
        "checked_at": _now_iso(),
        **result,
    }


def _list_watchers(*, status: str) -> list[dict]:
    db_path = _db_path()
    if not db_path.is_file():
        return []
    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row
        clauses = []
        params = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        try:
            rows = conn.execute(
                f"""SELECT watcher_id, watcher_type, title, target, severity, status, config, last_health, last_result, last_checked_at
                    FROM state_watchers
                    {where}
                    ORDER BY updated_at DESC, watcher_id DESC""",
                tuple(params),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    finally:
        conn.close()
    watchers = []
    for row in rows:
        watcher = dict(row)
        try:
            watcher["config"] = json.loads(watcher.get("config") or "{}")
        except Exception:
            watcher["config"] = {}
        watchers.append(watcher)
    return watchers


def _persist_result(result: dict) -> None:
    db_path = _db_path()
    if not db_path.is_file():
        return
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """UPDATE state_watchers
               SET last_health = ?, last_result = ?, last_checked_at = ?, updated_at = datetime('now')
               WHERE watcher_id = ?""",
            (
                result["health"],
                json.dumps(result, ensure_ascii=False),
                result["checked_at"],
                result["watcher_id"],
            ),
        )
        conn.commit()
    finally:
        conn.close()


def run_state_watchers(*, persist: bool = True, status: str = "active") -> dict:
    watchers = _list_watchers(status=status)
    results = [evaluate_state_watcher(watcher) for watcher in watchers]
    counts = {"healthy": 0, "degraded": 0, "critical": 0, "unknown": 0}
    for result in results:
        counts[result["health"]] = counts.get(result["health"], 0) + 1
        if persist:
            _persist_result(result)
    summary = {
        "generated_at": _now_iso(),
        "watcher_count": len(results),
        "counts": counts,
        "watchers": results,
    }
    if persist:
        summary_file = _summary_file()
        summary_file.parent.mkdir(parents=True, exist_ok=True)
        summary_file.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    return summary
