from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "build_runtime_benchmark_pack.py"


def _load_module():
    module_name = "build_runtime_benchmark_pack_test"
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(module_name, SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_build_runtime_pack_summarizes_checked_in_runs(tmp_path):
    module = _load_module()
    catalog = tmp_path / "scenario_catalog.json"
    results_dir = tmp_path / "results"
    results_dir.mkdir()

    catalog.write_text(
        json.dumps(
            {
                "catalog_version": "2026-04-10",
                "benchmark": "nexo_runtime_pack",
                "scope_note": "Operator runtime benchmark.",
                "scenarios": [
                    {"id": "s1", "title": "Scenario 1", "detail_markdown": "benchmarks/scenarios/s1.md"},
                    {"id": "s2", "title": "Scenario 2", "detail_markdown": "benchmarks/scenarios/s2.md"},
                ],
            }
        )
    )
    (results_dir / "2026-04-10-example.json").write_text(
        json.dumps(
            {
                "run_id": "2026-04-10-example",
                "title": "Example Run",
                "date": "2026-04-10",
                "grading": "manual_rubric",
                "source_markdown": "benchmarks/results/example.md",
                "baselines": [
                    {
                        "id": "nexo",
                        "label": "NEXO",
                        "scenario_results": {"s1": "pass", "s2": "partial"},
                    },
                    {
                        "id": "static",
                        "label": "Static",
                        "scenario_results": {"s1": "partial", "s2": "fail"},
                    },
                ],
            }
        )
    )

    summary = module.build_runtime_pack(catalog, results_dir)

    assert summary["latest_run"]["run_id"] == "2026-04-10-example"
    assert summary["latest_run"]["baselines"][0]["id"] == "nexo"
    assert summary["latest_run"]["baselines"][0]["score_pct"] == 75.0
    assert summary["latest_run"]["baselines"][1]["score_pct"] == 25.0


def test_render_markdown_includes_runtime_pack_sections():
    module = _load_module()
    markdown = module.render_markdown(
        {
            "generated_at": "2026-04-10T12:00:00+00:00",
            "scope_note": "Operator runtime benchmark.",
            "scenarios": [
                {"id": "s1", "title": "Scenario 1", "category": "memory", "detail_markdown": "benchmarks/scenarios/s1.md"},
            ],
            "latest_run": {
                "run_id": "2026-04-10-example",
                "title": "Example Run",
                "date": "2026-04-10",
                "grading": "manual_rubric",
                "source_markdown": "benchmarks/results/example.md",
                "baselines": [
                    {"label": "NEXO", "score_pct": 75.0, "pass_count": 1, "partial_count": 1, "fail_count": 0},
                ],
            },
        }
    )

    assert "# NEXO Runtime Benchmark Pack" in markdown
    assert "## Scenario catalog" in markdown
    assert "## Latest run" in markdown
    assert "| NEXO | 75.0 | 1 | 1 | 0 |" in markdown
    assert "Grade scale: `pass = 1.0`, `partial = 0.5`, `fail = 0.0`." in markdown
