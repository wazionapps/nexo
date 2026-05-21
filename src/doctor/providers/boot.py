"""Boot tier checks — fast, native, no repair. Target <100ms."""
from __future__ import annotations

import os
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from doctor.models import DoctorCheck, safe_check

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))


def check_db_exists() -> DoctorCheck:
    """Check that the main database file exists and is readable."""
    import paths
    db_path = paths.db_path()
    if db_path.is_file():
        size_kb = db_path.stat().st_size / 1024
        return DoctorCheck(
            id="boot.db_exists",
            tier="boot",
            status="healthy",
            severity="info",
            summary=f"Database exists ({size_kb:.0f} KB)",
            evidence=[str(db_path)],
        )
    return DoctorCheck(
        id="boot.db_exists",
        tier="boot",
        status="critical",
        severity="error",
        summary="Database file not found",
        evidence=[f"Expected: {db_path}"],
        repair_plan=["Run nexo-brain to initialize the database"],
        escalation_prompt="NEXO database missing — server cannot start without it.",
    )


def check_db_integrity(fix: bool = False) -> DoctorCheck:
    """Detect and optionally repair a wiped/corrupt local Brain database."""
    import sqlite3
    import paths
    from db_guard import (
        CRITICAL_TABLES,
        EMPTY_DB_SIZE_BYTES,
        LOCAL_CONTEXT_TABLES,
        MIN_REFERENCE_ROWS,
        PROTECTED_TABLES,
        db_looks_wiped,
        db_row_counts,
        diff_row_counts,
        find_best_hourly_backup,
        find_latest_hourly_backup,
        restore_tables_from_backup,
    )

    db_path = paths.db_path()
    if not db_path.is_file():
        return DoctorCheck(
            id="boot.db_integrity",
            tier="boot",
            status="critical",
            severity="error",
            summary="Database file not found",
            evidence=[str(db_path)],
            repair_plan=["Run nexo-brain to initialize the database"],
        )

    try:
        size_bytes = db_path.stat().st_size
    except OSError:
        size_bytes = -1

    quick_ok = False
    quick_error = ""
    try:
        conn = sqlite3.connect(str(db_path), timeout=2)
        try:
            row = conn.execute("PRAGMA quick_check").fetchone()
            quick_ok = bool(row and str(row[0]).lower() == "ok")
            if not quick_ok:
                quick_error = str(row[0] if row else "quick_check returned no row")
        finally:
            conn.close()
    except Exception as exc:
        quick_error = f"{type(exc).__name__}: {exc}"

    looks_wiped = db_looks_wiped(db_path, PROTECTED_TABLES)
    reference = find_best_hourly_backup(
        paths.backups_dir(),
        min_critical_rows=MIN_REFERENCE_ROWS,
        tables=PROTECTED_TABLES,
    ) or find_latest_hourly_backup(
        paths.backups_dir(),
        min_critical_rows=MIN_REFERENCE_ROWS,
        tables=PROTECTED_TABLES,
    )
    reference_counts = db_row_counts(reference, PROTECTED_TABLES) if reference else {}
    reference_rows = sum(v for v in reference_counts.values() if isinstance(v, int))
    protected_regression = diff_row_counts(db_path, reference, PROTECTED_TABLES) if reference else None
    local_context_db = paths.memory_dir() / "local-context.db"
    local_needs_reference = (
        not local_context_db.is_file()
        or db_looks_wiped(local_context_db, LOCAL_CONTEXT_TABLES)
    )
    local_reference = None
    if local_needs_reference:
        local_reference = (
            find_best_hourly_backup(
                paths.backups_dir(),
                glob="local-context-*.db",
                min_critical_rows=MIN_REFERENCE_ROWS,
                tables=LOCAL_CONTEXT_TABLES,
            )
            or find_latest_hourly_backup(
                paths.backups_dir(),
                glob="local-context-*.db",
                min_critical_rows=MIN_REFERENCE_ROWS,
                tables=LOCAL_CONTEXT_TABLES,
            )
            or find_best_hourly_backup(
                paths.backups_dir(),
                glob="nexo-*.db",
                min_critical_rows=MIN_REFERENCE_ROWS,
                tables=LOCAL_CONTEXT_TABLES,
            )
            or find_latest_hourly_backup(
                paths.backups_dir(),
                glob="nexo-*.db",
                min_critical_rows=MIN_REFERENCE_ROWS,
                tables=LOCAL_CONTEXT_TABLES,
            )
        )
    local_reference_counts = db_row_counts(local_reference, LOCAL_CONTEXT_TABLES) if local_reference else {}
    local_reference_rows = sum(v for v in local_reference_counts.values() if isinstance(v, int))
    local_regression = diff_row_counts(local_context_db, local_reference, LOCAL_CONTEXT_TABLES) if local_reference else None
    recoverable_local_regression = bool(
        local_reference
        and local_regression
        and local_regression.is_wipe()
    )
    recoverable_regression = bool(
        reference
        and protected_regression
        and protected_regression.is_wipe()
        and not looks_wiped
    )
    lower_error = quick_error.lower()
    corrupt_error = any(token in lower_error for token in (
        "database disk image is malformed",
        "file is not a database",
        "malformed",
        "not a database",
    ))
    recoverable_wipe = bool(
        reference
        and looks_wiped
        and (
            size_bytes <= EMPTY_DB_SIZE_BYTES
            or corrupt_error
            or not quick_ok
        )
    )

    if quick_ok and not recoverable_wipe and not recoverable_regression and not recoverable_local_regression:
        if looks_wiped and not reference:
            return DoctorCheck(
                id="boot.db_integrity",
                tier="boot",
                status="healthy",
                severity="info",
                summary="Database is readable and looks like a fresh install",
                evidence=[f"Size: {size_bytes} bytes", "No usable backup with user data found"],
            )
        return DoctorCheck(
            id="boot.db_integrity",
            tier="boot",
            status="healthy",
            severity="info",
            summary="Database integrity OK",
            evidence=[f"Size: {size_bytes} bytes"],
        )

    evidence = [
        f"DB: {db_path}",
        f"Size: {size_bytes} bytes",
        f"quick_check: {'ok' if quick_ok else quick_error or 'not ok'}",
        f"looks_wiped: {looks_wiped}",
    ]
    if reference:
        evidence.append(f"Reference backup: {reference} ({reference_rows} protected rows)")
    if protected_regression:
        evidence.extend(protected_regression.summary_lines())
    if local_reference:
        evidence.append(f"Local memory DB: {local_context_db}")
        evidence.append(f"Local memory reference: {local_reference} ({local_reference_rows} rows)")
    if local_regression:
        evidence.extend(["Local memory regression:", *local_regression.summary_lines()])

    if fix and recoverable_regression:
        report = restore_tables_from_backup(
            reference,
            db_path,
            tables=PROTECTED_TABLES,
            mode="merge_missing",
        )
        if report.get("ok"):
            restored = {
                table: payload
                for table, payload in (report.get("tables") or {}).items()
                if isinstance(payload, dict) and payload.get("status") in {"restored", "merged"}
            }
            restored_rows = sum(int(payload.get("restored") or 0) for payload in restored.values())
            return DoctorCheck(
                id="boot.db_integrity",
                tier="boot",
                status="healthy",
                severity="info",
                summary=f"Database protected tables restored from backup ({restored_rows} rows recovered)",
                evidence=evidence + [f"Restored protected tables: {len(restored)}"],
                fixed=True,
            )
        return DoctorCheck(
            id="boot.db_integrity",
            tier="boot",
            status="critical",
            severity="error",
            summary="Database protected-table repair failed",
            evidence=evidence + [f"Restore errors: {report.get('errors') or []}"],
            repair_plan=["Close NEXO Desktop and run nexo doctor --tier boot --plane database_real --fix"],
        )

    if fix and recoverable_wipe:
        from plugins.recover import recover

        report = recover(source=str(reference), force=True)
        if report.get("ok"):
            final_counts = report.get("final_row_counts") or {}
            restored_rows = sum(v for v in final_counts.values() if isinstance(v, int))
            return DoctorCheck(
                id="boot.db_integrity",
                tier="boot",
                status="healthy",
                severity="info",
                summary=f"Database restored from backup ({restored_rows} protected rows)",
                evidence=evidence + [f"Pre-recover snapshot: {report.get('pre_recover_dir', '')}"],
                fixed=True,
            )
        return DoctorCheck(
            id="boot.db_integrity",
            tier="boot",
            status="critical",
            severity="error",
            summary="Database repair failed",
            evidence=evidence + [f"Recover errors: {report.get('errors') or []}"],
            repair_plan=["Run nexo recover --force --yes, then restart Desktop"],
        )

    if fix and recoverable_local_regression:
        try:
            local_context_db.parent.mkdir(parents=True, exist_ok=True)
            if not local_context_db.exists():
                sqlite3.connect(str(local_context_db)).close()
        except Exception as exc:
            return DoctorCheck(
                id="boot.db_integrity",
                tier="boot",
                status="critical",
                severity="error",
                summary="Local memory sidecar repair failed",
                evidence=evidence + [f"Cannot create local memory DB: {type(exc).__name__}: {exc}"],
                repair_plan=["Close NEXO Desktop and run nexo doctor --tier boot --plane database_real --fix"],
            )
        report = restore_tables_from_backup(local_reference, local_context_db, tables=LOCAL_CONTEXT_TABLES)
        if report.get("ok"):
            restored = {
                table: payload
                for table, payload in (report.get("tables") or {}).items()
                if isinstance(payload, dict) and payload.get("status") == "restored"
            }
            restored_rows = sum(int(payload.get("after") or 0) for payload in restored.values())
            return DoctorCheck(
                id="boot.db_integrity",
                tier="boot",
                status="healthy",
                severity="info",
                summary=f"Local memory sidecar restored from backup ({restored_rows} rows)",
                evidence=evidence + [f"Restored local-memory sidecar tables: {len(restored)}"],
                fixed=True,
            )
        return DoctorCheck(
            id="boot.db_integrity",
            tier="boot",
            status="critical",
            severity="error",
            summary="Local memory sidecar repair failed",
            evidence=evidence + [f"Restore errors: {report.get('errors') or []}"],
            repair_plan=["Close NEXO Desktop and run nexo doctor --tier boot --plane database_real --fix"],
        )

    if recoverable_wipe or recoverable_regression or recoverable_local_regression:
        return DoctorCheck(
            id="boot.db_integrity",
            tier="boot",
            status="critical",
            severity="error",
            summary="Database or local memory appears degraded but a valid backup exists",
            evidence=evidence,
            repair_plan=["Run nexo doctor --tier boot --plane database_real --fix"],
            escalation_prompt="NEXO database needs automatic recovery from backup.",
        )

    status = "critical" if corrupt_error else "degraded"
    severity = "error" if status == "critical" else "warn"
    return DoctorCheck(
        id="boot.db_integrity",
        tier="boot",
        status=status,
        severity=severity,
        summary="Database is not fully readable" if quick_error else "Database integrity is uncertain",
        evidence=evidence,
        repair_plan=["Close NEXO Desktop and run nexo doctor --tier boot --plane database_real --fix"],
    )


def check_required_dirs() -> DoctorCheck:
    """Check that required NEXO_HOME directories exist (post-F0.6 layout
    or pre-F0.6 fallback)."""
    import paths
    # Each required dir maps to one of the new locations OR the legacy fallback.
    required_helpers = [
        ("data", paths.data_dir()),
        ("scripts", paths.core_scripts_dir()),
        ("plugins", paths.core_plugins_dir()),
        ("crons", paths.crons_dir()),
        ("hooks", paths.core_hooks_dir()),
        ("coordination", paths.coordination_dir()),
        ("operations", paths.operations_dir()),
        ("logs", paths.logs_dir()),
    ]
    missing = [name for (name, path) in required_helpers if not path.is_dir()]

    if not missing:
        return DoctorCheck(
            id="boot.required_dirs",
            tier="boot",
            status="healthy",
            severity="info",
            summary=f"All {len(required_helpers)} required directories present",
        )

    return DoctorCheck(
        id="boot.required_dirs",
        tier="boot",
        status="degraded" if len(missing) < 3 else "critical",
        severity="warn" if len(missing) < 3 else "error",
        summary=f"{len(missing)} required director{'y' if len(missing) == 1 else 'ies'} missing",
        evidence=[f"Missing: {d}" for d in missing],
        repair_plan=[f"mkdir -p {dict(required_helpers)[d]}" for d in missing],
    )


def _disk_recovery_state_file(paths_module) -> Path:
    return paths_module.runtime_state_dir() / "disk-recovery-state.json"


def _read_disk_recovery_state(paths_module) -> dict:
    try:
        path = _disk_recovery_state_file(paths_module)
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _write_disk_recovery_state(paths_module, payload: dict) -> None:
    try:
        path = _disk_recovery_state_file(paths_module)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    except Exception:
        pass


def _post_disk_recovery_sweep(paths_module, *, reason: str, free_bytes: int) -> dict:
    candidates = [
        Path(__file__).resolve().parents[2] / "scripts" / "post_disk_recovery_sweep.py",
        paths_module.core_scripts_dir() / "post_disk_recovery_sweep.py",
    ]
    script = next((candidate for candidate in candidates if candidate.is_file()), None)
    if script is None:
        return {"ok": False, "skipped": True, "reason": "script_missing"}
    try:
        proc = subprocess.run(
            [
                sys.executable,
                str(script),
                "--reason",
                reason,
                "--json",
                "--network-window-seconds",
                "0",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "free_bytes": free_bytes,
            "stdout": proc.stdout[-1000:],
            "stderr": proc.stderr[-1000:],
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "free_bytes": free_bytes}


def check_disk_space() -> DoctorCheck:
    """Check disk free space after silently purging NEXO-owned backups."""
    import paths

    try:
        state = _read_disk_recovery_state(paths)
        floor = int(paths.backup_min_free_bytes())
        critical_floor = 1 * 1024 ** 3
        usage_before = shutil.disk_usage(str(paths.home()))
        cleanup_report = None

        if usage_before.free < floor:
            cleanup_report = paths.aggressive_runtime_backup_prune(
                min_free_bytes=floor,
                reason="doctor_boot_disk_space",
            )

        usage = shutil.disk_usage(str(paths.home()))
        free_gb = usage.free / (1024 ** 3)
        pct_free = (usage.free / usage.total) * 100
        evidence = [f"Total: {usage.total / (1024**3):.0f} GB, Free: {free_gb:.1f} GB"]
        if cleanup_report:
            evidence.append(f"NEXO backup self-cleanup: {cleanup_report.get('steps')}")

        if usage.free < floor:
            _write_disk_recovery_state(paths, {
                "low": True,
                "free_bytes": int(usage.free),
                "threshold_bytes": floor,
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            })
            status = "critical" if usage.free < critical_floor else "degraded"
            severity = "error" if status == "critical" else "warn"
            return DoctorCheck(
                id="boot.disk_space",
                tier="boot",
                status=status,
                severity=severity,
                summary=f"Disk almost full ({free_gb:.1f} GB free). NEXO has already cleaned up its own backups. Review personal files to free space.",
                evidence=evidence,
                repair_plan=["Open the user folder and review personal files to free space"],
                escalation_prompt=f"Disk almost full ({free_gb:.1f} GB free). NEXO has already cleaned up its own backups. Review personal files to free space.",
            )

        if usage_before.free < floor or state.get("low"):
            sweep = _post_disk_recovery_sweep(
                paths,
                reason="doctor_disk_low_to_ok",
                free_bytes=int(usage.free),
            )
            _write_disk_recovery_state(paths, {
                "low": False,
                "free_bytes": int(usage.free),
                "threshold_bytes": floor,
                "last_recovery_sweep": sweep,
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            })
            return DoctorCheck(
                id="boot.disk_space",
                tier="boot",
                status="healthy",
                severity="info",
                summary=f"Disk space recovered after NEXO backup self-cleanup: {free_gb:.1f} GB free ({pct_free:.0f}%)",
                evidence=evidence + [f"Post-disk recovery sweep: {sweep}"],
                fixed=True,
            )

        return DoctorCheck(
            id="boot.disk_space",
            tier="boot",
            status="healthy",
            severity="info",
            summary=f"Disk space OK: {free_gb:.0f} GB free ({pct_free:.0f}%)",
        )
    except Exception as e:
        return DoctorCheck(
            id="boot.disk_space",
            tier="boot",
            status="degraded",
            severity="warn",
            summary=f"Could not check disk space: {e}",
        )


def check_wrapper_scripts() -> DoctorCheck:
    """Check that cron wrapper script exists."""
    import paths
    wrapper = paths.core_scripts_dir() / "nexo-cron-wrapper.sh"
    if wrapper.is_file():
        return DoctorCheck(
            id="boot.wrapper_scripts",
            tier="boot",
            status="healthy",
            severity="info",
            summary="Cron wrapper script present",
        )
    return DoctorCheck(
        id="boot.wrapper_scripts",
        tier="boot",
        status="degraded",
        severity="warn",
        summary="Cron wrapper script missing",
        evidence=[f"Expected: {wrapper}"],
        repair_plan=["Run nexo-brain to reinstall wrapper scripts"],
    )


def check_python_runtime() -> DoctorCheck:
    """Check Python interpreter is suitable."""
    version = sys.version_info
    if version >= (3, 10):
        return DoctorCheck(
            id="boot.python_runtime",
            tier="boot",
            status="healthy",
            severity="info",
            summary=f"Python {version.major}.{version.minor}.{version.micro}",
        )
    return DoctorCheck(
        id="boot.python_runtime",
        tier="boot",
        status="degraded",
        severity="warn",
        summary=f"Python {version.major}.{version.minor} — 3.10+ recommended",
        evidence=[sys.executable],
    )


CRITICAL_CONFIG_FILES = (
    ("schedule.json", ("config", "schedule.json")),
    ("optionals.json", ("config", "optionals.json")),
    ("crons/manifest.json", ("crons", "manifest.json")),
)


def check_config_parse() -> DoctorCheck:
    """Validate that critical JSON config files parse correctly."""
    import json

    errors: list[str] = []
    checked: list[str] = []

    for label, relative in CRITICAL_CONFIG_FILES:
        path = NEXO_HOME.joinpath(*relative)
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text())
        except Exception as exc:
            errors.append(f"{label}: {exc}")
            continue
        if not isinstance(data, dict):
            errors.append(f"{label}: expected JSON object, got {type(data).__name__}")
            continue
        checked.append(label)

    if errors:
        return DoctorCheck(
            id="boot.config_parse",
            tier="boot",
            status="degraded",
            severity="warn",
            summary=f"{len(errors)} config file parse error" + ("s" if len(errors) != 1 else ""),
            evidence=errors,
            repair_plan=["Fix JSON syntax in the listed config files, or delete them to fall back to defaults"],
        )

    if not checked:
        return DoctorCheck(
            id="boot.config_parse",
            tier="boot",
            status="healthy",
            severity="info",
            summary="No config files present (using defaults)",
        )

    return DoctorCheck(
        id="boot.config_parse",
        tier="boot",
        status="healthy",
        severity="info",
        summary=f"{len(checked)} config file" + ("s" if len(checked) != 1 else "") + " parse OK",
        evidence=checked,
    )


def check_core_dev_packaged_install() -> DoctorCheck:
    """Warn when ``~/.nexo/core-dev/`` exists on a packaged (non-dev) install.

    Contract (see ``docs/f06-layout-contract.md`` §3): ``core-dev/`` is a
    developer opt-in and MUST be absent on production installs. Its presence
    on a packaged install is almost always a leftover from a dev environment
    that was later repackaged, and silently keeps parallel code paths
    discoverable through ``_classify_script_dir``. Doctor surfaces it so the
    operator can confirm and remove.
    """
    import paths
    core_dev = paths.core_dev_dir()
    if not core_dev.exists():
        return DoctorCheck(
            id="boot.core_dev_absent_on_packaged",
            tier="boot",
            status="healthy",
            severity="info",
            summary="core-dev/ absent (expected on packaged installs)",
        )
    is_packaged = paths.core_dir().is_dir() and not (NEXO_HOME / "src").is_dir()
    if not is_packaged:
        return DoctorCheck(
            id="boot.core_dev_absent_on_packaged",
            tier="boot",
            status="healthy",
            severity="info",
            summary="core-dev/ present on a dev install (contract allows this)",
        )
    try:
        payload = [p.name for p in core_dev.iterdir()][:5]
    except OSError:
        payload = []
    return DoctorCheck(
        id="boot.core_dev_absent_on_packaged",
        tier="boot",
        status="degraded",
        severity="warn",
        summary="core-dev/ present on a packaged install — contract forbids this",
        evidence=[f"Location: {core_dev}"] + [f"Entry: {n}" for n in payload],
        repair_plan=[
            f"Confirm with operator, then: rm -rf {core_dev}",
        ],
    )


def check_dashboard_desktop_contract() -> DoctorCheck:
    """Flag Dashboard LaunchAgent contradicting Desktop product surface.

    Contract (see ``docs/f06-layout-contract.md`` §4):
      - Terminal-only install → ``com.nexo.dashboard`` loaded.
      - Desktop-managed install → ``com.nexo.dashboard`` unloaded.
    Both signals disagreeing with the chosen product mode is a warn.
    """
    if sys.platform != "darwin":
        return DoctorCheck(
            id="boot.dashboard_desktop_contract",
            tier="boot",
            status="healthy",
            severity="info",
            summary="Non-darwin host — dashboard LaunchAgent contract does not apply",
        )
    agent_path = Path.home() / "Library" / "LaunchAgents" / "com.nexo.dashboard.plist"
    agent_installed = agent_path.exists()
    try:
        from product_mode import enforce_desktop_product_contract  # type: ignore
        desktop_contract = bool(enforce_desktop_product_contract())
    except Exception:
        desktop_contract = False

    if desktop_contract and agent_installed:
        return DoctorCheck(
            id="boot.dashboard_desktop_contract",
            tier="boot",
            status="degraded",
            severity="warn",
            summary="Desktop product surface active but standalone dashboard LaunchAgent is installed",
            evidence=[f"Plist: {agent_path}"],
            repair_plan=[
                f"launchctl unload {agent_path}",
                f"rm {agent_path}",
            ],
        )
    if not desktop_contract and not agent_installed:
        return DoctorCheck(
            id="boot.dashboard_desktop_contract",
            tier="boot",
            status="degraded",
            severity="warn",
            summary="Terminal-only install without a dashboard LaunchAgent",
            evidence=["Expected plist missing: com.nexo.dashboard.plist"],
            repair_plan=["nexo update  # re-materialize com.nexo.dashboard"],
        )
    return DoctorCheck(
        id="boot.dashboard_desktop_contract",
        tier="boot",
        status="healthy",
        severity="info",
        summary="Dashboard LaunchAgent state matches the product surface contract",
    )


def check_f06_migration_consistency() -> DoctorCheck:
    """Detect half-migrated F0.6 installs.

    Contract (``docs/f06-layout-contract.md`` §6 rule 5):
      - F0.6 marker + legacy runtime dirs populated → half-migration.
      - No marker but canonical ``core/`` already populated → half-migration.
      - Marker F0.6 with no legacy residue → healthy.
      - No marker, no canonical ``core/``, pure legacy layout → healthy
        (pre-F0.6 install waiting for ``nexo update``).

    Half-migration is the scenario where ``paths.coordination_dir()`` (and
    siblings) silently fall back to the legacy path on an install that
    *should* be on F0.6. Doctor surfaces it so ``nexo update`` can be
    asked to finish the job instead of the operator discovering later that
    half their state lives in the wrong place.
    """
    import paths
    marker = NEXO_HOME / ".structure-version"
    marker_text = ""
    if marker.is_file():
        try:
            marker_text = marker.read_text().strip().upper().split()[0]
        except (OSError, IndexError):
            marker_text = ""
    is_f06_marked = marker_text.startswith("F0.6")

    core_dir = paths.core_dir()
    core_populated = core_dir.is_dir() and any(core_dir.iterdir()) if core_dir.exists() else False

    # Legacy runtime dirs that MUST be gone (or be symlinks into canonical F0.6)
    # once the migration has finished physically.
    legacy_runtime_names = ("coordination", "data", "logs", "operations")
    legacy_stragglers: list[str] = []
    for name in legacy_runtime_names:
        legacy_path = NEXO_HOME / name
        if not legacy_path.exists():
            continue
        if legacy_path.is_symlink():
            # A symlink pointing at the canonical runtime/<name> is the
            # compat shim contract; that is acceptable.
            continue
        try:
            has_content = any(legacy_path.iterdir())
        except OSError:
            has_content = False
        if has_content:
            legacy_stragglers.append(name)

    if is_f06_marked and legacy_stragglers:
        return DoctorCheck(
            id="boot.f06_migration_consistency",
            tier="boot",
            status="critical",
            severity="error",
            summary="Half-migrated F0.6 install: marker present but legacy runtime dirs still populated",
            evidence=[f"Marker: {marker_text}"] + [f"Legacy with content: {NEXO_HOME / n}" for n in legacy_stragglers],
            repair_plan=[
                "nexo update   # finish the F0.6 migration",
                "# if update refuses, inspect manifest and consider: nexo rollback f06",
            ],
        )
    if (not is_f06_marked) and core_populated:
        return DoctorCheck(
            id="boot.f06_migration_consistency",
            tier="boot",
            status="critical",
            severity="error",
            summary="Half-migrated F0.6 install: core/ populated but marker absent",
            evidence=[f"Marker: {marker_text or '(absent)'}", f"core/ path: {core_dir}"],
            repair_plan=[
                "nexo update   # re-run migration to write the marker",
            ],
        )
    return DoctorCheck(
        id="boot.f06_migration_consistency",
        tier="boot",
        status="healthy",
        severity="info",
        summary=(
            f"F0.6 marker consistent with layout (marker={marker_text or 'absent'}, "
            f"legacy_stragglers={len(legacy_stragglers)})"
        ),
    )


def run_boot_checks(fix: bool = False) -> list[DoctorCheck]:
    """Run all boot-tier checks."""
    checks = [
        safe_check(check_db_exists),
        safe_check(check_db_integrity, fix=fix),
        safe_check(check_required_dirs),
        safe_check(check_disk_space),
        safe_check(check_wrapper_scripts),
        safe_check(check_python_runtime),
        safe_check(check_config_parse),
        safe_check(check_core_dev_packaged_install),
        safe_check(check_dashboard_desktop_contract),
        safe_check(check_f06_migration_consistency),
    ]

    if fix:
        for check in checks:
            if check.id == "boot.required_dirs" and check.status != "healthy":
                # Deterministic fix: create missing directories
                for plan in check.repair_plan:
                    if plan.startswith("mkdir"):
                        dir_path = plan.split("mkdir -p ")[-1]
                        Path(dir_path).mkdir(parents=True, exist_ok=True)
                check.fixed = True
                check.status = "healthy"
                check.summary += " (fixed)"

    return checks
