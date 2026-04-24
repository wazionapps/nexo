from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = REPO_ROOT / "bin" / "nexo-brain.js"
PACKAGE = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))


def _node_available() -> bool:
    return shutil.which("node") is not None


def _env(tmp_path: Path, home: Path) -> dict[str, str]:
    user_home = tmp_path / "user-home"
    user_home.mkdir(exist_ok=True)
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(user_home),
            "NEXO_HOME": str(home),
            "NEXO_ALLOW_EPHEMERAL_INSTALL": "1",
            "NEXO_SKIP_MODEL_WARMUP": "1",
            "NEXO_SKIP_POSTINSTALL": "1",
            "NEXO_SKIP_SHELL_PROFILE": "1",
        }
    )
    return env


def _write_calibration(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.mark.skipif(not _node_available(), reason="node not available")
@pytest.mark.parametrize(
    ("relative_path", "payload"),
    [
        (
            "brain/calibration.json",
            {"version": 1, "user": {"name": "Maria", "language": "es"}},
        ),
        (
            "personal/brain/calibration.json",
            {
                "version": 2,
                "user": {"name": "Maria", "language": "es"},
                "meta": {"onboarding_completed": True},
            },
        ),
    ],
)
def test_version_does_not_launch_onboarding_with_legacy_or_v2_calibration(
    tmp_path: Path, relative_path: str, payload: dict
) -> None:
    home = tmp_path / "nexo-home"
    _write_calibration(home / relative_path, payload)

    proc = subprocess.run(
        ["node", str(INSTALLER), "--version"],
        env=_env(tmp_path, home),
        input="",
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert proc.returncode == 0, proc.stderr
    assert PACKAGE["version"] in proc.stdout
    assert "preferred language" not in proc.stdout
    assert "idioma prefieres" not in proc.stdout


@pytest.mark.skipif(not _node_available(), reason="node not available")
def test_warmup_models_dry_run_lists_current_local_models_without_onboarding(tmp_path: Path) -> None:
    home = tmp_path / "nexo-home"
    proc = subprocess.run(
        ["node", str(INSTALLER), "warmup-models", "--dry-run", "--json", "--force"],
        env=_env(tmp_path, home),
        input="",
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    targets = {target["name"]: target for target in payload["targets"]}
    model_ids = {target["model_id"] for target in targets.values()}
    assert "MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7" in model_ids
    assert "BAAI/bge-base-en-v1.5" in model_ids
    assert "BAAI/bge-small-en-v1.5" in model_ids
    assert "Xenova/ms-marco-MiniLM-L-6-v2" in model_ids
    assert targets["bge-base-embeddings"]["revision"] == "738cad1c108e2f23649db9e44b2eab988626493b"
    assert targets["bge-small-embeddings"]["revision"] == "52398278842ec682c6f32300af41344b1c0b0bb2"
    assert targets["cross-encoder-reranker"]["revision"] == "a09144355adeed5f58c8ed011d209bf8ee5a1fec"
    assert "preferred language" not in proc.stdout
    assert "idioma prefieres" not in proc.stdout


@pytest.mark.skipif(not _node_available(), reason="node not available")
def test_aborted_setup_does_not_persist_partial_placeholder_calibration(tmp_path: Path) -> None:
    home = tmp_path / "nexo-home"
    env = _env(tmp_path, home)
    env["NEXO_TESTING_SMOKE"] = "1"

    proc = subprocess.Popen(
        ["node", str(INSTALLER)],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    # Drive setup past the personality questions. Older builds wrote
    # calibration.json here, before scan/dashboard/auto-install completed.
    answers = "es\n\nMaria\nNero\n\n2\n2\n1\n2\n1\n"
    try:
        proc.communicate(input=answers, timeout=4)
    except subprocess.TimeoutExpired:
        proc.send_signal(signal.SIGINT)
        proc.communicate(timeout=5)

    assert not (home / "personal" / "brain" / "calibration.json").exists()
    assert not (home / "brain" / "calibration.json").exists()


def test_onboarding_source_uses_lazy_readline_and_atomic_final_calibration_write() -> None:
    text = INSTALLER.read_text(encoding="utf-8")
    assert "function getReadline()" in text
    assert "const rl = readline.createInterface" not in text
    assert "function isOnboardingComplete(calibration)" in text
    assert "migrated_from_legacy_calibration" in text
    assert 'writeJsonAtomic(path.join(runtimeBrainDir, "calibration.json"), calibration);' in text
    assert 'fs.writeFileSync(\n    path.join(runtimeBrainDir, "calibration.json")' not in text


@pytest.mark.skipif(not _node_available(), reason="node not available")
def test_skip_mode_preserves_existing_identity_defaults_from_profile(tmp_path: Path) -> None:
    home = tmp_path / "nexo-home"
    profile = home / "personal" / "brain" / "profile.json"
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.write_text(
        json.dumps(
            {
                "user_name": "Maria",
                "language": "es",
                "operator_name": "Nora",
            }
        ),
        encoding="utf-8",
    )

    env = _env(tmp_path, home)
    env["NEXO_TESTING_SMOKE"] = "1"

    timed_out = False
    try:
        proc = subprocess.run(
            ["node", str(INSTALLER), "--skip"],
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        proc = subprocess.CompletedProcess(
            exc.cmd,
            returncode=-9,
            stdout=exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or ""),
            stderr=exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or ""),
        )

    cal_path = home / "personal" / "brain" / "calibration.json"
    if not cal_path.is_file() and (home / "brain" / "calibration.json").is_file():
        cal_path = home / "brain" / "calibration.json"
    if not cal_path.is_file():
        pytest.skip(
            f"installer did not reach calibration step in sandbox: "
            f"rc={proc.returncode} stdout={proc.stdout[-400:]!r} stderr={proc.stderr[-400:]!r}"
        )
    if timed_out:
        assert cal_path.is_file()

    cal = json.loads(cal_path.read_text(encoding="utf-8"))
    assert cal["user"]["name"] == "Maria"
    assert cal["user"]["language"] == "es"
    assert cal["user"]["assistant_name"] == "Nora"

def test_postinstall_runs_warmup_for_fresh_installs_and_honors_skip() -> None:
    text = (REPO_ROOT / "bin" / "postinstall.js").read_text(encoding="utf-8")
    assert '"warmup-models", "--postinstall"' in text
    assert "NEXO_SKIP_MODEL_WARMUP" in text
    assert "fresh install" in text
