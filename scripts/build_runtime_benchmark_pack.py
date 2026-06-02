#!/usr/bin/env python3
"""Build the operator-focused runtime benchmark pack artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_PACK_DIR = ROOT / "benchmarks" / "runtime_pack"
CATALOG_PATH = RUNTIME_PACK_DIR / "scenario_catalog.json"
CASES_DIR = RUNTIME_PACK_DIR / "cases"
RESULTS_DIR = RUNTIME_PACK_DIR / "results"
SUMMARY_PATH = RESULTS_DIR / "latest_summary.json"
README_PATH = RUNTIME_PACK_DIR / "README.md"
VALID_GRADES = {"pass": 1.0, "partial": 0.5, "fail": 0.0}
VALID_PROFILES = {"catalog", "release", "full"}
VALID_FIXTURE_KINDS = {"synthetic", "redacted", "holdout"}
VALID_SCORERS = {"rubric", "exact_refs", "semantic_redacted", "manual"}
REQUIRED_SPEC_REFS = tuple(f"{index:02d}" for index in range(1, 11))


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


def _stable_json_hash(value) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _load_case_file(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("cases"), list):
        cases = payload["cases"]
    elif isinstance(payload, dict):
        cases = [payload]
    elif isinstance(payload, list):
        cases = payload
    else:
        raise ValueError(f"runtime benchmark case file must be object/list: {path}")
    return [dict(item) for item in cases]


def load_cases(cases_dir: Path = CASES_DIR) -> list[dict]:
    if not cases_dir.is_dir():
        return []
    cases = []
    for path in sorted(cases_dir.glob("*.json")):
        for item in _load_case_file(path):
            try:
                source_file = str(path.relative_to(ROOT))
            except ValueError:
                source_file = str(path)
            item.setdefault("_source_file", source_file)
            cases.append(item)
    return cases


def validate_cases(cases: list[dict], scenario_ids: set[str]) -> dict:
    seen = set()
    spec_refs = set()
    categories = {}
    failures = []
    rendered = []
    for item in cases:
        case_id = str(item.get("case_id", "") or "").strip()
        if not case_id:
            failures.append("case without case_id")
            continue
        if case_id in seen:
            failures.append(f"duplicate case_id: {case_id}")
        seen.add(case_id)

        scenario_id = str(item.get("scenario_id", "") or "").strip()
        if scenario_id not in scenario_ids:
            failures.append(f"case {case_id} references unknown scenario_id: {scenario_id}")

        raw_specs = item.get("spec_refs")
        if not isinstance(raw_specs, list) or not raw_specs:
            failures.append(f"case {case_id} missing non-empty spec_refs")
            raw_specs = []
        case_specs = {str(ref).zfill(2) for ref in raw_specs}
        unknown_specs = sorted(ref for ref in case_specs if ref not in REQUIRED_SPEC_REFS)
        if unknown_specs:
            failures.append(f"case {case_id} references unsupported spec_refs: {', '.join(unknown_specs)}")
        spec_refs.update(case_specs)

        fixture_kind = str(item.get("fixture_kind", "") or "").strip()
        if fixture_kind not in VALID_FIXTURE_KINDS:
            failures.append(f"case {case_id} has invalid fixture_kind: {fixture_kind}")

        fixture_hash = str(item.get("fixture_hash", "") or "").strip()
        if not fixture_hash:
            failures.append(f"case {case_id} missing fixture_hash")

        scorer = str(item.get("scorer", "") or "").strip()
        if scorer not in VALID_SCORERS:
            failures.append(f"case {case_id} has invalid scorer: {scorer}")

        category = str(item.get("category", "uncategorized") or "uncategorized").strip()
        categories[category] = categories.get(category, 0) + 1
        rendered.append(
            {
                "case_id": case_id,
                "version": str(item.get("version", "") or "").strip(),
                "scenario_id": scenario_id,
                "category": category,
                "spec_refs": sorted(case_specs),
                "weight": float(item.get("weight", 1.0) or 1.0),
                "fixture_kind": fixture_kind,
                "fixture_hash": fixture_hash,
                "scorer": scorer,
                "source_file": item.get("_source_file", ""),
            }
        )

    missing_specs = [ref for ref in REQUIRED_SPEC_REFS if ref not in spec_refs]
    if missing_specs:
        failures.append(f"missing spec coverage: {', '.join(missing_specs)}")

    rendered.sort(key=lambda item: item["case_id"])
    return {
        "case_count": len(rendered),
        "case_set_hash": _stable_json_hash(rendered),
        "required_spec_refs": list(REQUIRED_SPEC_REFS),
        "covered_spec_refs": sorted(spec_refs),
        "missing_spec_refs": missing_specs,
        "categories": categories,
        "cases": rendered,
        "failures": failures,
        "ok": not failures,
    }


def build_release_gate(case_summary: dict, *, profile: str) -> dict:
    mode = "report" if profile == "catalog" else "block"
    status = "pass" if case_summary.get("ok") else ("warn" if mode == "report" else "block")
    return {
        "id": "runtime_memory_benchmark",
        "mode": mode,
        "status": status,
        "required_spec_refs": list(REQUIRED_SPEC_REFS),
        "covered_spec_refs": case_summary.get("covered_spec_refs") or [],
        "missing_spec_refs": case_summary.get("missing_spec_refs") or [],
        "failure_count": len(case_summary.get("failures") or []),
        "failures": case_summary.get("failures") or [],
    }


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


def build_runtime_pack(
    catalog_path: Path = CATALOG_PATH,
    results_dir: Path = RESULTS_DIR,
    *,
    cases_dir: Path = CASES_DIR,
    profile: str = "catalog",
) -> dict:
    if profile not in VALID_PROFILES:
        raise ValueError(f"invalid runtime benchmark profile: {profile}")
    catalog = load_catalog(catalog_path)
    runs = load_runs(results_dir)
    catalog_scenarios = catalog["scenarios"]
    catalog_lookup = {item["id"]: item for item in catalog_scenarios}
    case_summary = validate_cases(load_cases(cases_dir), set(catalog_lookup))
    release_gate = build_release_gate(case_summary, profile=profile)

    rendered_runs = []
    for run in runs:
        run_scenario_ids = run.get("scenarios") or list(catalog_lookup)
        unknown_ids = [scenario_id for scenario_id in run_scenario_ids if scenario_id not in catalog_lookup]
        if unknown_ids:
            raise ValueError(f"run '{run.get('run_id', '')}' references unknown scenarios: {', '.join(unknown_ids)}")
        baselines = [_score_baseline(run, run_scenario_ids, baseline) for baseline in run.get("baselines") or []]
        baselines.sort(key=lambda item: (-item["score_pct"], item["label"]))
        rendered_runs.append(
            {
                "run_id": run.get("run_id", ""),
                "title": run.get("title", run.get("run_id", "")),
                "date": run.get("date", ""),
                "grading": run.get("grading", "manual_rubric"),
                "source_markdown": run.get("source_markdown", ""),
                "notes": run.get("notes") or [],
                "scenario_ids": run_scenario_ids,
                "scenario_count": len(run_scenario_ids),
                "scenarios": [catalog_lookup[scenario_id] for scenario_id in run_scenario_ids],
                "baselines": baselines,
            }
        )

    rendered_runs.sort(key=lambda item: item.get("date", ""), reverse=True)
    latest = rendered_runs[0]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "benchmark": catalog.get("benchmark", "nexo_runtime_pack"),
        "catalog_version": catalog.get("catalog_version", ""),
        "profile": profile,
        "scope_note": catalog.get("scope_note", ""),
        "catalog_scenario_count": len(catalog_scenarios),
        "scenarios": catalog_scenarios,
        "case_summary": case_summary,
        "release_gate": release_gate,
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

    case_summary = summary.get("case_summary") or {}
    release_gate = summary.get("release_gate") or {}
    lines.extend(
        [
            "",
            "## Machine-readable case coverage",
            "",
            f"- Profile: `{summary.get('profile', 'catalog')}`",
            f"- Cases: {case_summary.get('case_count', 0)}",
            f"- Case set hash: `{case_summary.get('case_set_hash', '')}`",
            f"- Required specs: {', '.join(case_summary.get('required_spec_refs') or [])}",
            f"- Covered specs: {', '.join(case_summary.get('covered_spec_refs') or [])}",
            f"- Missing specs: {', '.join(case_summary.get('missing_spec_refs') or []) or '(none)'}",
            f"- Release gate: `{release_gate.get('status', 'unknown')}` ({release_gate.get('mode', 'report')})",
        ]
    )
    failures = case_summary.get("failures") or []
    if failures:
        lines.extend(["", "### Case validation failures", ""])
        for failure in failures:
            lines.append(f"- {failure}")

    lines.extend(
        [
            "",
            f"## Latest run — {latest.get('title', latest.get('run_id', ''))}",
            "",
            f"- Date: {latest.get('date', 'unknown')}",
            f"- Grading: {latest.get('grading', 'unknown')}",
            f"- Scenario count: {latest.get('scenario_count', 0)}",
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

    notes = latest.get("notes") or []
    if notes:
        lines.extend(["", "### Latest run notes", ""])
        for note in notes:
            lines.append(f"- {note}")

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
            f"- Cases: `{CASES_DIR.relative_to(ROOT)}`",
            f"- Latest summary: `{SUMMARY_PATH.relative_to(ROOT)}`",
            f"- Latest run file: `{(RESULTS_DIR / (latest['run_id'] + '.json')).relative_to(ROOT) if (RESULTS_DIR / (latest['run_id'] + '.json')).exists() else latest.get('source_markdown', '')}`",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=sorted(VALID_PROFILES), default="catalog")
    args = parser.parse_args()
    summary = build_runtime_pack(profile=args.profile)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    README_PATH.write_text(render_markdown(summary))
    print(f"[runtime-pack] wrote {SUMMARY_PATH}")
    print(f"[runtime-pack] wrote {README_PATH}")


if __name__ == "__main__":
    main()
