from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace


def _reload_auto_update(monkeypatch, home: Path):
    monkeypatch.setenv("NEXO_HOME", str(home))
    sys.modules.pop("auto_update", None)
    import auto_update as au

    return importlib.reload(au)


def test_skip_when_already_installed(monkeypatch, tmp_path):
    au = _reload_auto_update(monkeypatch, tmp_path)
    monkeypatch.setattr(
        au,
        "_probe_local_classifier_dependencies",
        lambda: (True, {"transformers": "9.9.9", "torch": "8.8.8"}, []),
    )
    monkeypatch.setattr(
        au.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("pip should not run")),
    )

    au._CLASSIFIER_INSTALL_THREAD = None
    au._maybe_install_local_classifier()

    payload = json.loads((tmp_path / "runtime" / "operations" / "classifier-install-state.json").read_text())
    assert payload["deps_ok"] is True
    assert payload["transformers_version"] == "9.9.9"
    assert payload["torch_version"] == "8.8.8"


def test_install_runs_when_missing(monkeypatch, tmp_path):
    au = _reload_auto_update(monkeypatch, tmp_path)
    calls: list[list[str]] = []
    probe_results = iter([
        (False, {}, ["transformers", "torch"]),
        (False, {}, ["transformers", "torch"]),
        (True, {"transformers": "5.3.0", "torch": "2.7.0"}, []),
    ])
    monkeypatch.setattr(au, "_probe_local_classifier_dependencies", lambda: next(probe_results))
    monkeypatch.setattr(au, "_download_local_classifier_model", lambda: (True, ""))

    def _fake_run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr(au.subprocess, "run", _fake_run)

    au._CLASSIFIER_INSTALL_THREAD = None
    au._maybe_install_local_classifier()

    assert calls
    assert calls[0] == [
        "pip3",
        "install",
        "--user",
        "transformers",
        "torch",
        "sentencepiece",
        "sentence-transformers",
    ]
    payload = json.loads((tmp_path / "runtime" / "operations" / "classifier-install-state.json").read_text())
    assert payload["deps_ok"] is True
    assert payload["transformers_version"] == "5.3.0"
    assert payload["torch_version"] == "2.7.0"


def test_opt_out_via_env(monkeypatch, tmp_path):
    monkeypatch.setenv("NEXO_LOCAL_CLASSIFIER", "off")
    au = _reload_auto_update(monkeypatch, tmp_path)
    monkeypatch.setattr(
        au.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("pip should not run on opt-out")),
    )

    au._CLASSIFIER_INSTALL_THREAD = None
    au._maybe_install_local_classifier()

    payload = json.loads((tmp_path / "runtime" / "operations" / "classifier-install-state.json").read_text())
    assert payload["opt_out"] is True
    assert payload["deps_ok"] is False


def test_pip_failure_no_crash(monkeypatch, tmp_path):
    au = _reload_auto_update(monkeypatch, tmp_path)
    probe_results = iter([
        (False, {}, ["transformers"]),
        (False, {}, ["transformers"]),
    ])
    monkeypatch.setattr(au, "_probe_local_classifier_dependencies", lambda: next(probe_results))
    monkeypatch.setattr(
        au.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr="offline"),
    )

    au._CLASSIFIER_INSTALL_THREAD = None
    au._maybe_install_local_classifier()

    payload = json.loads((tmp_path / "runtime" / "operations" / "classifier-install-state.json").read_text())
    assert payload["deps_ok"] is False
    assert payload["error"] == "offline"


def test_state_file_path_lives_in_runtime(monkeypatch, tmp_path):
    au = _reload_auto_update(monkeypatch, tmp_path)
    expected = tmp_path / "runtime" / "operations" / "classifier-install-state.json"
    assert au._classifier_install_state_path() == expected
