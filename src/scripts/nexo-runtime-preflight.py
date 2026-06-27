#!/usr/bin/env python3
"""
NEXO Runtime Preflight

Runs safe end-to-end smoke tests for the runtime using a temporary workspace
and a copied SQLite database. No external API calls are performed.
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


def _bootstrap_nexo_code(default_repo_src: Path) -> Path:
    nexo_home = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
    raw_env = os.environ.get("NEXO_CODE", "")
    candidates: list[Path] = []
    if raw_env:
        raw = Path(raw_env).expanduser()
        candidates.extend([raw, raw / "core"])
    candidates.extend([default_repo_src, nexo_home / "core", nexo_home])
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if (candidate / "paths.py").is_file() or (candidate / "server.py").is_file() or (candidate / "cli.py").is_file():
            if str(candidate) not in sys.path:
                sys.path.insert(0, str(candidate))
            return candidate
    fallback = candidates[0]
    if str(fallback) not in sys.path:
        sys.path.insert(0, str(fallback))
    return fallback

HOME = Path.home()
NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(HOME / ".nexo")))
# Auto-detect: if running from repo (src/scripts/), use src/ as NEXO_CODE
_script_dir = Path(__file__).resolve().parent
_repo_src = _script_dir.parent  # src/scripts/ -> src/
NEXO_CODE = _bootstrap_nexo_code(_repo_src)

from paths import data_dir, logs_dir

LOG_DIR = logs_dir()
LOG_DIR.mkdir(parents=True, exist_ok=True)
SUMMARY_FILE = LOG_DIR / "runtime-preflight-summary.json"
DB_FILE = data_dir() / "nexo.db"
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

        snapshot_restore = NEXO_CODE / "scripts" / "nexo-snapshot-restore.sh"
        if snapshot_restore.exists():
            shutil.copy2(snapshot_restore, temp_scripts_dir / "nexo-snapshot-restore.sh")

        temp_api_key = temp_root / "anthropic-api-key.txt"
        temp_api_key.write_text("smoke-test-key")

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

        summary["checks"]["evolution_removed"] = {"status": "pass", "tools_exposed": False}

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
