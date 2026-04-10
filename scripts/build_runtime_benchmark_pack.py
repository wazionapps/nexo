#!/usr/bin/env python3
"""Build the operator-focused runtime benchmark pack artifacts."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_PACK_DIR = ROOT / "benchmarks" / "runtime_pack"
CATALOG_PATH = RUNTIME_PACK_DIR / "scenario_catalog.json"
RESULTS_DIR = RUNTIME_PACK_DIR / "results"
SUMMARY_PATH = RESULTS_DIR / "latest_summary.json"
README_PATH = RUNTIME_PACK_DIR / "README.md"
VALID_GRADES = {"pass": 1.0, "partial": 0.5, "fail": 0.0}


def load_catalog(path: Path = CATALOG_PATH) -> dict:
    payload = json.loads(path.read_text())
    scenarios = payload.get("scenarios") or []
    if not scenarios:
        raise ValueError("runtime benchmark catalog has no scenarios")
    return payload


def load_runs(results_dir: Path = RESULTS_DIR) -> list[dict]:
    runs = []
    for path in sorted(results_dir.glob("*.json")):
        if path.name == SUMMARY_PATH.name:
            continue
        runs.append(json.loads(path.read_text()))
    if not runs:
        raise ValueError(f"no runtime benchmark runs found in {results_dir}")
    return runs


def _score_baseline(run: dict, scenario_ids: list[str], baseline: dict) -> dict:
    scenario_results = baseline.get("scenario_results") or {}
    pass_count = 0
    partial_count = 0
    fail_count = 0
    weighted_total = 0.0
    rows = []
    for scenario_id in scenario_ids:
        grade = str(scenario_results.get(scenario_id, "fail")).strip().lower()
        if grade not in VALID_GRADES:
            raise ValueError(f"invalid grade '{grade}' for scenario '{scenario_id}' in baseline '{baseline.get('id', '')}'")
        weighted_total += VALID_GRADES[grade]
        if grade == "pass":
            pass_count += 1
        elif grade == "partial":
            partial_count += 1
        else:
            fail_count += 1
        rows.append({"scenario_id": scenario_id, "grade": grade})

    count = max(1, len(scenario_ids))
    return {
        "id": baseline.get("id", ""),
        "label": baseline.get("label", baseline.get("id", "")),
        "pass_count": pass_count,
        "partial_count": partial_count,
        "fail_count": fail_count,
        "weighted_score": round(weighted_total, 3),
        "score_pct": round((weighted_total / count) * 100, 1),
        "scenario_results": rows,
    }


def build_runtime_pack(catalog_path: Path = CATALOG_PATH, results_dir: Path = RESULTS_DIR) -> dict:
    catalog = load_catalog(catalog_path)
    runs = load_runs(results_dir)
    scenario_ids = [item["id"] for item in catalog["scenarios"]]

    rendered_runs = []
    for run in runs:
        baselines = [_score_baseline(run, scenario_ids, baseline) for baseline in run.get("baselines") or []]
        baselines.sort(key=lambda item: (-item["score_pct"], item["label"]))
        rendered_runs.append(
            {
                "run_id": run.get("run_id", ""),
                "title": run.get("title", run.get("run_id", "")),
                "date": run.get("date", ""),
                "grading": run.get("grading", "manual_rubric"),
                "source_markdown": run.get("source_markdown", ""),
                "scenario_count": len(scenario_ids),
                "baselines": baselines,
            }
        )

    rendered_runs.sort(key=lambda item: item.get("date", ""), reverse=True)
    latest = rendered_runs[0]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "benchmark": catalog.get("benchmark", "nexo_runtime_pack"),
        "catalog_version": catalog.get("catalog_version", ""),
        "scope_note": catalog.get("scope_note", ""),
        "scenarios": catalog.get("scenarios", []),
        "runs": rendered_runs,
        "latest_run": latest,
    }


def render_markdown(summary: dict) -> str:
    latest = summary["latest_run"]
    lines = [
        "# NEXO Runtime Benchmark Pack",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        "## What this is",
        "",
        f"- {summary.get('scope_note', '').strip()}",
        "- Deterministic aggregation over checked-in scenario definitions and scored run files.",
        "- A runtime-focused comparison against realistic local baselines, not a universal intelligence claim.",
        "",
        "## Scenario catalog",
        "",
    ]
    for scenario in summary.get("scenarios") or []:
        lines.append(f"- `{scenario['id']}` — {scenario['title']} ({scenario.get('category', 'uncategorized')})")
        lines.append(f"  - Detail: `{scenario.get('detail_markdown', '')}`")

    lines.extend(
        [
            "",
            f"## Latest run — {latest.get('title', latest.get('run_id', ''))}",
            "",
            f"- Date: {latest.get('date', 'unknown')}",
            f"- Grading: {latest.get('grading', 'unknown')}",
            f"- Source markdown: `{latest.get('source_markdown', '')}`",
            "",
            "| Baseline | Score % | Pass | Partial | Fail |",
            "|----------|---------|------|---------|------|",
        ]
    )
    for baseline in latest.get("baselines") or []:
        lines.append(
            f"| {baseline['label']} | {baseline['score_pct']} | {baseline['pass_count']} | {baseline['partial_count']} | {baseline['fail_count']} |"
        )

    lines.extend(
        [
            "",
            "## Methodology",
            "",
            "- Grade scale: `pass = 1.0`, `partial = 0.5`, `fail = 0.0`.",
            "- Reruns are reproducible because the scenario catalog and run JSON files are checked in.",
            "- This pack complements LoCoMo and the public scorecard by measuring runtime-backed operator workflows.",
            "",
            "## Artifacts",
            "",
            f"- Catalog: `{CATALOG_PATH.relative_to(ROOT)}`",
            f"- Latest summary: `{SUMMARY_PATH.relative_to(ROOT)}`",
            f"- Latest run file: `{(RESULTS_DIR / (latest['run_id'] + '.json')).relative_to(ROOT) if (RESULTS_DIR / (latest['run_id'] + '.json')).exists() else latest.get('source_markdown', '')}`",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def main() -> None:
    summary = build_runtime_pack()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    README_PATH.write_text(render_markdown(summary))
    print(f"[runtime-pack] wrote {SUMMARY_PATH}")
    print(f"[runtime-pack] wrote {README_PATH}")


if __name__ == "__main__":
    main()
