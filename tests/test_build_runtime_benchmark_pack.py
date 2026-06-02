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
    cases_dir = tmp_path / "cases"
    results_dir = tmp_path / "results"
    cases_dir.mkdir()
    results_dir.mkdir()

    specs = [f"{index:02d}" for index in range(1, 11)]
    catalog.write_text(
        json.dumps(
            {
                "catalog_version": "2026-04-10",
                "benchmark": "nexo_runtime_pack",
                "scope_note": "Operator runtime benchmark.",
                "scenarios": [
                    {"id": "s1", "title": "Scenario 1", "detail_markdown": "benchmarks/scenarios/s1.md"},
                    {"id": "s2", "title": "Scenario 2", "detail_markdown": "benchmarks/scenarios/s2.md"},
                    {"id": "s3", "title": "Scenario 3", "detail_markdown": "benchmarks/scenarios/s3.md"},
                ],
            }
        )
    )
    (cases_dir / "cases.json").write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_id": f"case_{spec}",
                        "version": "1",
                        "scenario_id": "s1",
                        "category": "memory",
                        "spec_refs": [spec],
                        "weight": 1.0,
                        "fixture_kind": "synthetic",
                        "fixture_hash": f"hash-{spec}",
                        "setup_seed": f"seed-{spec}",
                        "prompt": f"Prompt for {spec}",
                        "scorer": "exact_refs",
                        "rubric": ["has expected refs"],
                    }
                    for spec in specs
                ]
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
                "notes": ["Subset run to prove mixed catalog support."],
                "scenarios": ["s1", "s2"],
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

    summary = module.build_runtime_pack(catalog, results_dir, cases_dir=cases_dir, profile="release")

    assert summary["latest_run"]["run_id"] == "2026-04-10-example"
    assert summary["latest_run"]["scenario_count"] == 2
    assert summary["latest_run"]["scenario_ids"] == ["s1", "s2"]
    assert summary["catalog_scenario_count"] == 3
    assert summary["latest_run"]["baselines"][0]["id"] == "nexo"
    assert summary["latest_run"]["baselines"][0]["score_pct"] == 75.0
    assert summary["latest_run"]["baselines"][1]["score_pct"] == 25.0
    assert summary["profile"] == "release"
    assert summary["case_summary"]["case_count"] == 10
    assert summary["case_summary"]["covered_spec_refs"] == specs
    assert summary["case_summary"]["missing_spec_refs"] == []
    assert summary["release_gate"]["status"] == "pass"
    assert summary["release_gate"]["mode"] == "block"


def test_runtime_pack_case_validation_blocks_missing_spec_coverage(tmp_path):
    module = _load_module()
    catalog = tmp_path / "scenario_catalog.json"
    cases_dir = tmp_path / "cases"
    results_dir = tmp_path / "results"
    cases_dir.mkdir()
    results_dir.mkdir()

    catalog.write_text(
        json.dumps(
            {
                "catalog_version": "2026-04-10",
                "benchmark": "nexo_runtime_pack",
                "scope_note": "Operator runtime benchmark.",
                "scenarios": [{"id": "s1", "title": "Scenario 1", "detail_markdown": "benchmarks/scenarios/s1.md"}],
            }
        )
    )
    (cases_dir / "cases.json").write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_id": "case_01",
                        "version": "1",
                        "scenario_id": "s1",
                        "category": "memory",
                        "spec_refs": ["01"],
                        "weight": 1.0,
                        "fixture_kind": "synthetic",
                        "fixture_hash": "hash-01",
                        "setup_seed": "seed-01",
                        "prompt": "Prompt for 01",
                        "scorer": "exact_refs",
                        "rubric": ["has expected refs"],
                    }
                ]
            }
        )
    )
    (results_dir / "2026-04-10-example.json").write_text(
        json.dumps(
            {
                "run_id": "2026-04-10-example",
                "title": "Example Run",
                "date": "2026-04-10",
                "scenarios": ["s1"],
                "baselines": [{"id": "nexo", "label": "NEXO", "scenario_results": {"s1": "pass"}}],
            }
        )
    )

    summary = module.build_runtime_pack(catalog, results_dir, cases_dir=cases_dir, profile="release")

    assert summary["case_summary"]["missing_spec_refs"] == ["02", "03", "04", "05", "06", "07", "08", "09", "10"]
    assert summary["release_gate"]["status"] == "block"


def test_render_markdown_includes_runtime_pack_sections():
    module = _load_module()
    markdown = module.render_markdown(
        {
            "generated_at": "2026-04-10T12:00:00+00:00",
            "profile": "release",
            "scope_note": "Operator runtime benchmark.",
            "scenarios": [
                {"id": "s1", "title": "Scenario 1", "category": "memory", "detail_markdown": "benchmarks/scenarios/s1.md"},
                {"id": "s2", "title": "Scenario 2", "category": "workflow", "detail_markdown": "benchmarks/scenarios/s2.md"},
            ],
            "case_summary": {
                "case_count": 10,
                "case_set_hash": "abc123",
                "required_spec_refs": ["01", "02"],
                "covered_spec_refs": ["01", "02"],
                "missing_spec_refs": [],
                "failures": [],
            },
            "release_gate": {"status": "pass", "mode": "block"},
            "latest_run": {
                "run_id": "2026-04-10-example",
                "title": "Example Run",
                "date": "2026-04-10",
                "grading": "manual_rubric",
                "scenario_count": 1,
                "source_markdown": "benchmarks/results/example.md",
                "notes": ["Conservative manual run."],
                "baselines": [
                    {"label": "NEXO", "score_pct": 75.0, "pass_count": 1, "partial_count": 1, "fail_count": 0},
                ],
            },
        }
    )

    assert "# NEXO Runtime Benchmark Pack" in markdown
    assert "## Scenario catalog" in markdown
    assert "## Latest run" in markdown
    assert markdown.splitlines().count("## Latest run — Example Run") == 1
    assert "- Scenario count: 1" in markdown
    assert "| NEXO | 75.0 | 1 | 1 | 0 |" in markdown
    assert "### Latest run notes" in markdown
    assert "- Conservative manual run." in markdown
    assert "## Machine-readable case coverage" in markdown
    assert "- Release gate: `pass` (block)" in markdown
    assert "Grade scale: `pass = 1.0`, `partial = 0.5`, `fail = 0.0`." in markdown
