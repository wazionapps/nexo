"""Product-mode contracts shared by Brain and Desktop."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import paths


DESKTOP_PRODUCT_ENV = "NEXO_DESKTOP_MANAGED"
ALLOW_CORE_WRITES_ENV = "NEXO_ALLOW_CORE_WRITES"
PRODUCT_MODE_FILENAME = "product-mode.json"
DESKTOP_PRODUCT_MODE = "desktop_closed_product"
DESKTOP_DISABLED_FEATURES = ("evolution",)
DESKTOP_EVOLUTION_DISABLED_REASON = "Disabled by NEXO Desktop product contract"


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _resolved_user_home(home: Path | None = None) -> tuple[Path, bool]:
    if home is not None:
        return Path(home).expanduser(), True

    env_home = Path(os.environ.get("HOME", str(Path.home()))).expanduser()
    raw_nexo_home = str(os.environ.get("NEXO_HOME", "") or "").strip()
    if not raw_nexo_home:
        return env_home, False

    nexo_home = Path(raw_nexo_home).expanduser()
    candidate = nexo_home.parent
    try:
        explicit = candidate.resolve(strict=False) != env_home.resolve(strict=False)
    except Exception:
        explicit = True
    return (candidate if explicit else env_home), explicit


def product_mode_path() -> Path:
    return paths.config_dir() / PRODUCT_MODE_FILENAME


def _desktop_install_markers(home: Path | None = None, *, include_global_markers: bool = True) -> list[Path]:
    base = Path(home) if home is not None else _resolved_user_home()[0]
    markers: list[Path] = [
        base / "Applications" / "NEXO Desktop.app",
        base / "Library" / "Application Support" / "NEXO Desktop",
        base / ".local" / "share" / "NEXO Desktop",
        base / ".config" / "NEXO Desktop",
    ]
    if include_global_markers:
        markers.insert(0, Path("/Applications/NEXO Desktop.app"))
    if os.name == "nt":
        local = Path(os.environ.get("LOCALAPPDATA", str(base / "AppData" / "Local")))
        roaming = Path(os.environ.get("APPDATA", str(base / "AppData" / "Roaming")))
        markers.extend(
            [
                local / "Programs" / "NEXO Desktop",
                roaming / "NEXO Desktop",
            ]
        )
    return markers


def desktop_product_install_detected(home: Path | None = None) -> bool:
    resolved_home, explicit_home = _resolved_user_home(home)
    for candidate in _desktop_install_markers(
        resolved_home,
        include_global_markers=not explicit_home,
    ):
        try:
            if candidate.exists():
                return True
        except Exception:
            continue
    return False


def load_product_mode() -> dict[str, Any]:
    path = product_mode_path()
    try:
        raw = json.loads(path.read_text())
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def save_product_mode(payload: dict[str, Any]) -> Path:
    path = product_mode_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    tmp_path.replace(path)
    return path


def desktop_product_requested() -> bool:
    if str(os.environ.get(DESKTOP_PRODUCT_ENV, "")).strip() == "1":
        return True
    payload = load_product_mode()
    if payload.get("desktop_managed") is True:
        return True
    if str(payload.get("product_mode") or "").strip().lower() == DESKTOP_PRODUCT_MODE:
        return True
    return desktop_product_install_detected()


def mark_desktop_product_managed(*, source: str = "desktop") -> dict[str, Any]:
    existing = load_product_mode()
    payload = dict(existing) if isinstance(existing, dict) else {}
    payload.update(
        {
            "desktop_managed": True,
            "product_mode": DESKTOP_PRODUCT_MODE,
            "disabled_features": list(DESKTOP_DISABLED_FEATURES),
            "updated_at": _now_iso(),
            "source": str(source or "desktop").strip() or "desktop",
        }
    )
    if not payload.get("created_at"):
        payload["created_at"] = payload["updated_at"]
    save_product_mode(payload)
    return payload


def _default_objective_payload() -> dict[str, Any]:
    return {
        "objective": "Improve operational excellence and reduce repeated errors",
        "focus_areas": ["error_prevention", "proactivity", "memory_quality"],
        "evolution_enabled": False,
        "evolution_mode": "review",
        "dimensions": {
            "episodic_memory": {"current": 0, "target": 90},
            "autonomy": {"current": 0, "target": 80},
            "proactivity": {"current": 0, "target": 70},
            "self_improvement": {"current": 0, "target": 60},
            "agi": {"current": 0, "target": 20},
        },
        "total_evolutions": 0,
        "consecutive_failures": 0,
        "created_at": _now_iso(),
    }


def _normalize_objective(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        from evolution_cycle import normalize_objective

        return normalize_objective(payload)
    except Exception:
        return payload


def load_evolution_objective() -> tuple[Path, dict[str, Any]]:
    objective_path = paths.brain_dir() / "evolution-objective.json"
    try:
        raw = json.loads(objective_path.read_text())
        payload = raw if isinstance(raw, dict) else {}
    except Exception:
        payload = {}
    if not payload:
        payload = _default_objective_payload()
    return objective_path, _normalize_objective(payload)


def enforce_desktop_product_contract(*, source: str = "desktop") -> dict[str, Any]:
    if not desktop_product_requested():
        return {"applied": False, "reason": "desktop_not_requested"}

    mode_payload = mark_desktop_product_managed(source=source)
    objective_path, objective = load_evolution_objective()
    changed_objective = (
        bool(objective.get("evolution_enabled", True))
        or str(objective.get("disabled_reason") or "") != DESKTOP_EVOLUTION_DISABLED_REASON
    )

    objective["evolution_enabled"] = False
    objective["disabled_reason"] = DESKTOP_EVOLUTION_DISABLED_REASON
    objective["disabled_by"] = "desktop_product"
    objective["desktop_managed"] = True
    if not objective.get("created_at"):
        objective["created_at"] = _now_iso()

    objective_path.parent.mkdir(parents=True, exist_ok=True)
    objective_path.write_text(json.dumps(objective, indent=2, ensure_ascii=False) + "\n")
    return {
        "applied": True,
        "mode_path": str(product_mode_path()),
        "objective_path": str(objective_path),
        "changed_objective": changed_objective,
        "mode": mode_payload,
    }


def is_cron_blocked(cron_id: str | None) -> bool:
    clean = str(cron_id or "").strip().lower()
    return clean == "evolution" and desktop_product_requested()


def filter_blocked_crons(crons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [dict(cron) for cron in crons if not is_cron_blocked(cron.get("id"))]


def core_writes_allowed() -> bool:
    return str(os.environ.get(ALLOW_CORE_WRITES_ENV, "")).strip() == "1"


def is_protected_runtime_core_path(file_path: str | os.PathLike[str] | None) -> bool:
    raw = str(file_path or "").strip()
    if not raw:
        return False
    try:
        expanded = os.path.expandvars(raw)
        candidate = Path(expanded).expanduser().resolve(strict=False)
        candidate.relative_to(paths.core_dir().resolve(strict=False))
        return True
    except Exception:
        return False
