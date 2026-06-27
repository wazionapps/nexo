from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from importlib.util import module_from_spec, spec_from_file_location


REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_SRC = REPO_ROOT / "src"

if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))


def _reload_modules(monkeypatch, home: Path):
    monkeypatch.setenv("NEXO_HOME", str(home))
    import paths
    import product_mode
    import plugins.evolution as evolution_plugin

    importlib.reload(paths)
    importlib.reload(product_mode)
    importlib.reload(evolution_plugin)
    return product_mode, evolution_plugin


def test_plugins_evolution_surface_stays_available_when_desktop_managed(tmp_path, monkeypatch):
    home = tmp_path / "nexo-home"
    (home / "config").mkdir(parents=True, exist_ok=True)
    (home / "brain").mkdir(parents=True, exist_ok=True)
    (home / "config" / "product-mode.json").write_text(json.dumps({
        "desktop_managed": True,
        "product_mode": "desktop_closed_product",
    }))
    (home / "brain" / "evolution-objective.json").write_text(json.dumps({
        "evolution_enabled": True,
        "dimensions": {"autonomy": {"current": 80, "target": 90}},
    }))

    _, evolution_plugin = _reload_modules(monkeypatch, home)

    monkeypatch.setattr(evolution_plugin, "get_latest_metrics", lambda: {"autonomy": {"score": 90, "delta": 1}})
    monkeypatch.setattr(evolution_plugin, "get_evolution_history", lambda limit: [{
        "id": 1,
        "classification": "propose",
        "dimension": "autonomy",
        "proposal": "Improve support ticket routing",
        "status": "support_ticket_created",
        "test_result": "",
        "impact": 0,
    }])
    updates = []
    monkeypatch.setattr(evolution_plugin, "update_evolution_log_status", lambda *args, **kwargs: updates.append((args, kwargs)))

    assert "EVOLUTION STATUS" in evolution_plugin.handle_evolution_status()
    assert "EVOLUTION HISTORY" in evolution_plugin.handle_evolution_history()
    assert evolution_plugin.handle_evolution_propose().startswith("Evolution cycle queued.")
    assert evolution_plugin.handle_evolution_approve(1, "ok") == "Proposal #1 APPROVED. Will be applied in next Evolution cycle."
    assert evolution_plugin.handle_evolution_reject(1, "no") == "Proposal #1 REJECTED. Reason: no"
    assert len(updates) == 2


def test_standalone_evolution_runner_forces_support_mode_when_desktop_managed(tmp_path, monkeypatch):
    home = tmp_path / "nexo-home"
    (home / "config").mkdir(parents=True, exist_ok=True)
    (home / "brain").mkdir(parents=True, exist_ok=True)
    (home / "config" / "product-mode.json").write_text(json.dumps({
        "desktop_managed": True,
        "product_mode": "desktop_closed_product",
    }))
    (home / "brain" / "evolution-objective.json").write_text(json.dumps({
        "evolution_enabled": True,
    }))

    monkeypatch.setenv("NEXO_HOME", str(home))

    import paths
    import product_mode

    importlib.reload(paths)
    importlib.reload(product_mode)

    runner_path = REPO_SRC / "scripts" / "nexo-evolution-run.py"
    spec = spec_from_file_location("nexo_evolution_run", runner_path)
    module = module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)

    logs: list[str] = []
    saved: list[dict] = []
    db_path = home / "data" / "nexo.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.touch()
    monkeypatch.setattr(module, "log", lambda message: logs.append(str(message)))
    monkeypatch.setattr(module, "NEXO_DB", db_path)
    monkeypatch.setattr(module, "load_objective", lambda: {"evolution_enabled": True, "evolution_mode": "auto", "total_evolutions": 0})
    monkeypatch.setattr(module, "save_objective", lambda objective: saved.append(dict(objective)))
    monkeypatch.setattr(module, "dry_run_restore_test", lambda: True)
    monkeypatch.setattr(module, "_apply_accepted_proposals", lambda *args, **kwargs: {"attempted": 0, "applied": 0, "rolled_back": 0, "blocked": 0, "skipped": 0, "failed": 0})
    monkeypatch.setattr(module, "get_week_data", lambda db_path: {})
    monkeypatch.setattr(module, "build_evolution_prompt", lambda week_data, objective: "prompt")
    monkeypatch.setattr(module, "verify_claude_cli", lambda: False)

    module.run()

    assert any("Desktop-managed install: forcing Evolution support-ticket mode." in line for line in logs)
    assert saved[0]["evolution_mode"] == "support_ticket"
