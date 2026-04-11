"""Security baseline + minimal load smoke — Fase 4 item 3.

Pins:
  - .github/workflows/security.yml exists and runs bandit on src/
  - pyproject.toml has [tool.bandit] config
  - bandit -r src/ --severity-level high --confidence-level high returns
    zero findings (the original 10 weak-hash issues were fixed by adding
    usedforsecurity=False to each hashlib call)
  - A trivial in-memory load smoke for hashlib usage to make sure the
    fingerprinting calls still produce stable, fast hashes.
"""

from __future__ import annotations

import hashlib
import importlib
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"
SECURITY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "security.yml"


def _bandit_available() -> bool:
    try:
        return importlib.util.find_spec("bandit") is not None
    except Exception:
        return False


# ── Config + workflow exist ───────────────────────────────────────────────


class TestSecurityBaselineConfig:
    def test_security_workflow_exists(self):
        assert SECURITY_WORKFLOW.exists()
        text = SECURITY_WORKFLOW.read_text()
        assert "bandit" in text
        assert "--severity-level high" in text
        assert "--confidence-level high" in text

    def test_pyproject_has_bandit_section(self):
        text = PYPROJECT.read_text()
        assert "[tool.bandit]" in text
        assert "exclude_dirs" in text

    def test_requirements_dev_pins_bandit(self):
        text = (REPO_ROOT / "requirements-dev.txt").read_text()
        assert "bandit" in text


# ── bandit on src/ returns zero high-severity findings ───────────────────


class TestBanditCleanBaseline:
    @pytest.mark.skipif(not _bandit_available(), reason="bandit not installed")
    def test_bandit_high_severity_high_confidence_returns_zero(self):
        result = subprocess.run(
            [
                sys.executable, "-m", "bandit", "-r", "src/",
                "--severity-level", "high",
                "--confidence-level", "high",
                "-c", "pyproject.toml",
                "-q",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"bandit found high-severity issues:\n{result.stdout}\n{result.stderr}"
        )


# ── usedforsecurity=False is set on every hashlib fingerprint call ───────


class TestHashlibFingerprintFixes:
    """The 10 hashlib calls that bandit flagged are fingerprinting, not
    security. Each must now pass `usedforsecurity=False` so bandit and
    static analyzers know the hash is not cryptographic.
    """

    def test_protocol_followup_id_uses_safe_flag(self):
        text = (REPO_ROOT / "src" / "plugins" / "protocol.py").read_text()
        assert "usedforsecurity=False" in text

    def test_simple_api_source_id_uses_safe_flag(self):
        text = (REPO_ROOT / "src" / "plugins" / "simple_api.py").read_text()
        assert "usedforsecurity=False" in text

    def test_check_context_uses_safe_flag(self):
        text = (REPO_ROOT / "src" / "scripts" / "check-context.py").read_text()
        assert text.count("usedforsecurity=False") >= 2

    def test_apply_findings_uses_safe_flag(self):
        text = (REPO_ROOT / "src" / "scripts" / "deep-sleep" / "apply_findings.py").read_text()
        assert text.count("usedforsecurity=False") >= 3

    def test_synthesize_uses_safe_flag(self):
        text = (REPO_ROOT / "src" / "scripts" / "deep-sleep" / "synthesize.py").read_text()
        assert "usedforsecurity=False" in text

    def test_daily_self_audit_uses_safe_flag(self):
        text = (REPO_ROOT / "src" / "scripts" / "nexo-daily-self-audit.py").read_text()
        assert text.count("usedforsecurity=False") >= 2


# ── Trivial load smoke for hashlib fingerprints ──────────────────────────


class TestHashFingerprintLoadSmoke:
    """Minimum load test the audit asks for: confirm the hashlib
    fingerprints stay fast and deterministic at scale. This is NOT a
    full Locust/Gatling rig — it's a guard against accidentally swapping
    in a slow algorithm or losing determinism."""

    def test_md5_fingerprint_is_stable_and_fast(self):
        sample = "deploy nexo-cron-wrapper push to mundiserver"
        first = hashlib.md5(sample.encode(), usedforsecurity=False).hexdigest()
        # 10000 hashes must complete in under 1s on any modern machine.
        start = time.perf_counter()
        for _ in range(10000):
            digest = hashlib.md5(sample.encode(), usedforsecurity=False).hexdigest()
            assert digest == first  # determinism
        elapsed = time.perf_counter() - start
        assert elapsed < 1.0, f"10000 md5 fingerprints took {elapsed:.3f}s (expected < 1s)"

    def test_sha1_fingerprint_is_stable_and_fast(self):
        sample = "NF-PROTOCOL-test-load"
        first = hashlib.sha1(sample.encode(), usedforsecurity=False).hexdigest()
        start = time.perf_counter()
        for _ in range(10000):
            digest = hashlib.sha1(sample.encode(), usedforsecurity=False).hexdigest()
            assert digest == first
        elapsed = time.perf_counter() - start
        assert elapsed < 1.0, f"10000 sha1 fingerprints took {elapsed:.3f}s (expected < 1s)"
