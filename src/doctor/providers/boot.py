"""Boot tier checks — fast, native, no repair. Target <100ms."""
from __future__ import annotations

import os
import shutil
import sys
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


def check_disk_space() -> DoctorCheck:
    """Check disk free space on NEXO_HOME partition."""
    try:
        usage = shutil.disk_usage(str(NEXO_HOME))
        free_gb = usage.free / (1024 ** 3)
        pct_free = (usage.free / usage.total) * 100

        if free_gb < 1:
            return DoctorCheck(
                id="boot.disk_space",
                tier="boot",
                status="critical",
                severity="error",
                summary=f"Very low disk space: {free_gb:.1f} GB free ({pct_free:.0f}%)",
                evidence=[f"Total: {usage.total / (1024**3):.0f} GB, Free: {free_gb:.1f} GB"],
                repair_plan=["Free up disk space — NEXO needs at least 1 GB for normal operation"],
                escalation_prompt="Disk space critically low — backups and logs may fail.",
            )
        elif free_gb < 5:
            return DoctorCheck(
                id="boot.disk_space",
                tier="boot",
                status="degraded",
                severity="warn",
                summary=f"Low disk space: {free_gb:.1f} GB free ({pct_free:.0f}%)",
                evidence=[f"Total: {usage.total / (1024**3):.0f} GB, Free: {free_gb:.1f} GB"],
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
