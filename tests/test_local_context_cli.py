from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI_PY = ROOT / "src" / "cli.py"


def _run_cli(nexo_home: Path, *args: str):
    env = {
        **os.environ,
        "HOME": str(nexo_home),
        "NEXO_HOME": str(nexo_home),
        "NEXO_CODE": str(ROOT / "src"),
        "NEXO_TEST_DB": str(nexo_home / "data" / "nexo.db"),
        "NEXO_COGNITIVE_DB": str(nexo_home / "data" / "cognitive.db"),
        "NEXO_SKIP_COGNITIVE_MODEL_DOWNLOAD": "1",
        "NEXO_SKIP_FS_INDEX": "1",
        "NEXO_SKIP_LEARNING_COGNITIVE_INGEST": "1",
        "NEXO_SKIP_SEMANTIC_SIMILARITY": "1",
    }
    (nexo_home / "data").mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        [sys.executable, str(CLI_PY), *args],
        capture_output=True,
        env=env,
        text=True,
        timeout=20,
    )


def _json(result):
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_local_context_cli_indexes_and_queries(tmp_path):
    nexo_home = tmp_path / "nexo"
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "factura-portatil.txt").write_text(
        "Factura del portatil de Maria para BMW. Total 1200 euros.",
        encoding="utf-8",
    )

    added = _json(_run_cli(nexo_home, "local-context", "roots", "add", str(docs), "--json"))
    assert added["ok"] is True
    assert added["root_path"] == str(docs)

    run = _json(_run_cli(nexo_home, "local-context", "run-once", "--limit", "20", "--process-limit", "20", "--json"))
    assert run["ok"] is True
    assert run["scan"]["seen"] >= 1

    query = _json(_run_cli(nexo_home, "local-context", "query", "factura Maria BMW", "--limit", "5", "--json"))
    assert query["ok"] is True
    assert query["evidence_refs"]
    assert any("factura-portatil" in asset["path"] for asset in query["assets"])

    status = _json(_run_cli(nexo_home, "local-context", "status", "--json"))
    assert status["ok"] is True
    assert status["global"]["files_found"] >= 1


def test_local_context_cli_controls_and_metadata(tmp_path):
    nexo_home = tmp_path / "nexo"
    excluded = tmp_path / "private"
    excluded.mkdir()

    exclusion = _json(_run_cli(nexo_home, "local-context", "exclusions", "add", str(excluded), "--json"))
    assert exclusion["ok"] is True

    exclusions = _json(_run_cli(nexo_home, "local-context", "exclusions", "list", "--json"))
    assert any(row["path"] == str(excluded) for row in exclusions["exclusions"])

    paused = _json(_run_cli(nexo_home, "local-context", "pause", "--json"))
    assert paused["paused"] is True

    resumed = _json(_run_cli(nexo_home, "local-context", "resume", "--json"))
    assert resumed["paused"] is False

    service = _json(_run_cli(nexo_home, "local-context", "service-config", "--platform", "windows", "--json"))
    assert service["kind"] == "scheduled_task"

    models = _json(_run_cli(nexo_home, "local-context", "models", "status", "--json"))
    assert models["ok"] is True
