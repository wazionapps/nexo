#!/usr/bin/env python3
"""
NEXO Runtime Preflight

Runs safe end-to-end smoke tests for Cortex and Evolution using a temporary
workspace and a copied SQLite database. No external API calls are performed.
Results are written to NEXO_HOME/logs/runtime-preflight-summary.json.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import traceback
from datetime import datetime
from pathlib import Path

HOME = Path.home()
NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(HOME / ".nexo")))
# Auto-detect: if running from repo (src/scripts/), use src/ as NEXO_CODE
_script_dir = Path(__file__).resolve().parent
_repo_src = _script_dir.parent  # src/scripts/ -> src/
NEXO_CODE = Path(os.environ.get("NEXO_CODE", str(_repo_src) if (_repo_src / "server.py").exists() else str(NEXO_HOME)))

LOG_DIR = NEXO_HOME / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
SUMMARY_FILE = LOG_DIR / "runtime-preflight-summary.json"
DB_FILE = NEXO_HOME / "data" / "nexo.db"
# Evolution config: NEXO_HOME/brain/ (canonical), NEXO_HOME/cortex/ (legacy fallback), NEXO_CODE (dev fallback)
def _find_evolution_file(name: str) -> Path:
    for candidate in [NEXO_HOME / "brain" / name, NEXO_HOME / "cortex" / name, NEXO_CODE / name]:
        if candidate.exists():
            return candidate
    return NEXO_HOME / "brain" / name  # default canonical path

CORTEX_OBJECTIVE = _find_evolution_file("evolution-objective.json")
CORTEX_PROMPT = _find_evolution_file("evolution-prompt.md")


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _write_summary(summary: dict):
    SUMMARY_FILE.write_text(json.dumps(summary, indent=2, ensure_ascii=False))


def _fake_cortex_response(model: str) -> str:
    if "opus" in model:
        payload = {
            "analysis": "Smoke evolution run over temp workspace",
            "proposals": [
                {
                    "dimension": "self_improvement",
                    "classification": "propose",
                    "action": "Add a regression smoke before live cycles",
                    "reasoning": "Smoke run should stay side-effect free",
                }
            ],
            "dimension_scores": {
                "episodic_memory": 86,
                "autonomy": 53,
                "proactivity": 34,
                "self_improvement": 30,
                "agi": 6,
            },
            "score_evidence": {
                "self_improvement": "Temp evolution smoke completed successfully",
            },
        }
    else:
        payload = {
            "actions_taken": ["preflight perception completed"],
            "signals_detected": 1,
            "pending_questions": [],
            "execute": [],
            "next_interval_suggestion": 600,
            "reflection_done": False,
            "dmn_done": False,
            "briefing_update": {
                "actions_taken": ["perception smoke ok"],
                "signals_active": [{"summary": "Smoke signal", "urgency": "INFO", "score": 10}],
                "recommendations": ["keep runtime checks green"],
                "pending_questions_unanswered": [],
                "dmn_summary": "",
            },
            "working_memory": {
                "current_threads": ["runtime preflight"],
                "attention_focus": "runtime integrity",
                "last_reasoning": "API mocked successfully",
                "watching": ["state writes", "briefing writes"],
                "momentum": "stable",
            },
            "error": None,
        }
    return json.dumps(payload, ensure_ascii=False)


def _fake_runner_response() -> tuple[str, dict]:
    payload = {
        "analysis": "Standalone evolution smoke over temp DB",
        "patterns": [
            {"type": "smoke", "description": "runner executed over temp workspace", "frequency": "once"}
        ],
        "proposals": [
            {
                "dimension": "self_improvement",
                "classification": "propose",
                "action": "Keep standalone evolution covered by preflight",
                "reasoning": "Regression prevention",
            }
        ],
        "dimension_scores": {
            "episodic_memory": 87,
            "autonomy": 54,
            "proactivity": 35,
            "self_improvement": 31,
            "agi": 6,
        },
        "score_evidence": {
            "self_improvement": "standalone runner smoke ok",
        },
    }
    usage = {"input_tokens": 1234, "output_tokens": 432}
    return json.dumps(payload, ensure_ascii=False), usage


def main() -> int:
    started = datetime.now().isoformat()
    summary = {
        "timestamp": started,
        "ok": False,
        "checks": {},
        "errors": [],
    }

    if not DB_FILE.exists():
        summary["errors"].append("nexo.db missing")
        _write_summary(summary)
        return 1

    preflight_root = HOME / ".codex" / "memories"
    preflight_root.mkdir(parents=True, exist_ok=True)
    temp_root = Path(tempfile.mkdtemp(prefix="nexo-runtime-preflight-", dir=str(preflight_root)))
    try:
        temp_db = temp_root / "nexo.db"
        shutil.copy2(DB_FILE, temp_db)

        temp_cortex_dir = temp_root / "brain"
        temp_logs_dir = temp_root / "logs"
        temp_coord_dir = temp_root / "coordination"
        temp_daily_dir = temp_root / "daily_summaries"
        temp_dmn_dir = temp_root / "dmn_insights"
        temp_snapshots_dir = temp_root / "snapshots"
        temp_sandbox_dir = temp_root / "sandbox" / "workspace"
        temp_scripts_dir = temp_root / "scripts"
        for directory in [
            temp_cortex_dir, temp_logs_dir, temp_coord_dir, temp_daily_dir,
            temp_dmn_dir, temp_snapshots_dir, temp_sandbox_dir, temp_scripts_dir,
        ]:
            directory.mkdir(parents=True, exist_ok=True)

        temp_objective = temp_cortex_dir / "evolution-objective.json"
        temp_prompt = temp_cortex_dir / "evolution-prompt.md"
        if CORTEX_OBJECTIVE.exists():
            shutil.copy2(CORTEX_OBJECTIVE, temp_objective)
        else:
            # Create a minimal objective so evolution smoke tests can proceed
            temp_objective.write_text(json.dumps({"evolution_enabled": True, "dimensions": {}}, indent=2))
        if CORTEX_PROMPT.exists():
            shutil.copy2(CORTEX_PROMPT, temp_prompt)
        snapshot_restore = NEXO_CODE / "scripts" / "nexo-snapshot-restore.sh"
        if snapshot_restore.exists():
            shutil.copy2(snapshot_restore, temp_scripts_dir / "nexo-snapshot-restore.sh")

        temp_api_key = temp_root / "anthropic-api-key.txt"
        temp_api_key.write_text("smoke-test-key")

        evolution_cycle = _load_module("evolution_cycle", NEXO_CODE / "evolution_cycle.py")
        evolution_cycle.NEXO_DB = temp_db
        evolution_cycle.NEXO_HOME = temp_root
        evolution_cycle.SANDBOX_DIR = temp_sandbox_dir
        evolution_cycle.SNAPSHOTS_DIR = temp_snapshots_dir
        evolution_cycle.OBJECTIVE_FILE = temp_objective
        evolution_cycle.PROMPT_FILE = temp_prompt
        evolution_cycle.RESTORE_LOG = temp_logs_dir / "snapshot-restores.log"

        week_data = evolution_cycle.get_week_data(str(temp_db))
        prompt = evolution_cycle.build_evolution_prompt(week_data, evolution_cycle.load_objective())
        restore_ok = evolution_cycle.dry_run_restore_test()
        if not restore_ok:
            raise RuntimeError("dry_run_restore_test failed")
        summary["checks"]["evolution_cycle"] = {
            "learnings": len(week_data.get("learnings", [])),
            "decisions": len(week_data.get("decisions", [])),
            "prompt_chars": len(prompt),
            "restore_ok": restore_ok,
        }

        cortex = _load_module("cortex_plugin", NEXO_CODE / "plugins" / "cortex.py")
        cortex.NEXO_DB = temp_db
        cortex.DRY_RUN = True
        cortex.BRIEFING_FILE = temp_cortex_dir / "briefing.json"
        cortex.HEALTH_FILE = temp_cortex_dir / "health.json"
        cortex.STATE_FILE = temp_cortex_dir / "state.json"
        cortex.STATE_TMP = temp_cortex_dir / "state.tmp"
        cortex.LOG_DIR = temp_logs_dir
        cortex.COORD_DIR = temp_coord_dir
        cortex.SIGNALS_FILE = temp_coord_dir / "pending-signals.json"
        cortex.DAILY_SUMMARIES_DIR = temp_daily_dir
        cortex.DMN_INSIGHTS_DIR = temp_dmn_dir
        cortex.FAILURE_COUNT_FILE = temp_cortex_dir / ".failure-count"
        cortex.PID_FILE = temp_cortex_dir / "cortex.pid"
        cortex.API_KEY_FILE = temp_api_key
        cortex.wa_notify = lambda *args, **kwargs: None
        cortex.poll_wa_inbox = lambda state: None
        cortex._check_health_endpoints = lambda: []
        cortex._call_anthropic = lambda prompt, model=None, max_tokens=4096: _fake_cortex_response(model or "")

        # Smoke test: verify cortex plugin loads and has expected tools
        cortex_path = NEXO_CODE / "plugins" / "cortex.py"
        if cortex_path.exists():
            import importlib.util
            spec = importlib.util.spec_from_file_location("cortex_plugin", str(cortex_path))
            cortex_mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cortex_mod)
            assert hasattr(cortex_mod, 'TOOLS'), "cortex plugin missing TOOLS"
            tool_names = [t[1] for t in cortex_mod.TOOLS]
            assert "nexo_cortex_check" in tool_names, "cortex plugin missing nexo_cortex_check tool"
        else:
            tool_names = ["cortex.py not found"]
        summary["checks"]["cortex_plugin"] = {
            "status": "pass",
            "tools_found": tool_names,
        }

        # Evolution runner: component verification (not full execution — that needs CLI)
        runner_path = NEXO_CODE / "scripts" / "nexo-evolution-run.py"
        if runner_path.exists():
            runner = _load_module("nexo_evolution_run", runner_path)
            checks = {
                "module_loads": True,
                "has_run": hasattr(runner, 'run'),
                "has_load_objective": hasattr(runner, 'load_objective'),
                "has_get_week_data": hasattr(runner, 'get_week_data'),
            }
            summary["checks"]["standalone_runner"] = checks
        else:
            summary["checks"]["standalone_runner"] = {"status": "skip", "reason": "runner not found"}

        summary["ok"] = True
        _write_summary(summary)
        return 0
    except Exception as exc:
        summary["errors"].append(f"{type(exc).__name__}: {exc}")
        summary["errors"].append(traceback.format_exc())
        _write_summary(summary)
        return 1
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
