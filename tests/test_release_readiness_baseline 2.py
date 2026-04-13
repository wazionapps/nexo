"""Pin Fase 4 items 4 + 5 — benchmark harness + release readiness gates.

Both items in the original audit roadmap turned out to be near-FPs:
the infrastructure already existed, only the CI gating on PRs was
missing. This file pins the structure so a refactor cannot silently
delete the benchmark harness or the release-contract format.

Item 4 (benchmarks reproducibles + thresholds): the benchmarks/ directory
exists with scenarios, results, runtime_pack, runtime_ablations, locomo.
We do NOT run the actual scoring here — that needs API credits and a
real LLM session. We pin the directory layout so a refactor cannot
accidentally remove the harness.

Item 5 (release gates with evidence): scripts/verify_release_readiness.py
already validates release-contracts/v5.0.x.json files including a `gates`
list with `evidence_required` per gate. The new release-readiness.yml
workflow now runs the verifier on every PR + push (it used to run only
on tag publish). Tests below pin the contract format and verifier shape.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BENCH_DIR = REPO_ROOT / "benchmarks"
CONTRACTS_DIR = REPO_ROOT / "release-contracts"
VERIFIER = REPO_ROOT / "scripts" / "verify_release_readiness.py"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release-readiness.yml"


# ── Item 4: benchmark harness ─────────────────────────────────────────────


class TestBenchmarkHarness:
    def test_benchmarks_directory_exists(self):
        assert BENCH_DIR.is_dir()

    def test_benchmark_readme_documents_repro_protocol(self):
        readme = BENCH_DIR / "README.md"
        assert readme.exists()
        text = readme.read_text()
        assert "Repro protocol" in text or "Reproducible" in text or "reproducible" in text
        # The README must reference the canonical baselines so a future
        # contributor cannot drift the comparison set without notice.
        assert "nexo_full_stack" in text
        assert "static_claude_md" in text

    def test_benchmark_scenarios_directory_has_scenarios(self):
        scenarios = BENCH_DIR / "scenarios"
        assert scenarios.is_dir()
        files = list(scenarios.glob("*.md"))
        # The audit roadmap calls out at least 8 outcome categories — we
        # require at least 5 scenario files so a refactor cannot empty
        # the directory unnoticed.
        assert len(files) >= 5

    def test_benchmark_runtime_pack_exists(self):
        runtime_pack = BENCH_DIR / "runtime_pack"
        assert runtime_pack.is_dir()

    def test_benchmark_results_directory_exists(self):
        results = BENCH_DIR / "results"
        assert results.is_dir()

    def test_build_runtime_benchmark_pack_script_exists(self):
        # The script lives at the top-level scripts/ dir (NOT src/scripts/)
        # because it builds artifacts that ship outside the runtime tree.
        script = REPO_ROOT / "scripts" / "build_runtime_benchmark_pack.py"
        assert script.exists()


# ── Item 5: release gates ─────────────────────────────────────────────────


class TestReleaseReadinessGates:
    def test_verifier_script_exists(self):
        assert VERIFIER.exists()
        text = VERIFIER.read_text()
        assert "def _check_contract" in text
        assert "def _check_protocol_closeout" in text
        assert "evidence_required" in text
        assert "--final-closeout" in text

    def test_release_contracts_directory_has_versions(self):
        assert CONTRACTS_DIR.is_dir()
        contracts = list(CONTRACTS_DIR.glob("v*.json"))
        # At least one contract per supported release line.
        assert len(contracts) >= 4

    def test_each_contract_has_required_keys(self):
        for path in CONTRACTS_DIR.glob("v*.json"):
            payload = json.loads(path.read_text())
            for key in ("release_line", "target_version", "distribution",
                        "required_repo_files", "required_website_files", "gates"):
                assert key in payload, f"{path.name} missing key {key!r}"

    def test_each_contract_gate_has_evidence_required(self):
        for path in CONTRACTS_DIR.glob("v*.json"):
            payload = json.loads(path.read_text())
            for gate in payload.get("gates", []):
                assert "id" in gate
                assert "title" in gate
                assert "status" in gate
                # The audit's "verifiable evidence" requirement maps to
                # this list — every gate must declare at least one
                # observable signal a reviewer can inspect.
                assert isinstance(gate.get("evidence_required"), list)
                assert len(gate["evidence_required"]) >= 1

    def test_release_readiness_workflow_exists_and_runs_verifier(self):
        assert WORKFLOW.exists()
        text = WORKFLOW.read_text()
        assert "verify_release_readiness.py" in text
        assert "--ci" in text
        # And it runs on PRs, not just tag publish (the original gap).
        assert "pull_request" in text

    def test_publish_workflow_still_runs_verifier_on_tag(self):
        publish = REPO_ROOT / ".github" / "workflows" / "publish.yml"
        text = publish.read_text()
        assert "verify_release_readiness.py" in text
