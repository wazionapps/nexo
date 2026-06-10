"""Phase 2.1/2.2 — runtime service generation isolation.

Live evidence (10/11-jun, operator machine): TWO Brain installs (managed
~/.nexo/core on Python 3.12 and a residual npm-global nexo-brain on Python
3.14) shared ONE runtime-service state file. ensure_runtime_service() from
each side saw a "stale_runtime" state and KILLED the other's resident in an
endless ping-pong: 1,314 resident restarts logged, every restart forcing the
next conversation to pay a cold Brain boot (measured 10-48s starts) and
expiring client sessions.

Contract pinned here:
- the service state file is PER RUNTIME GENERATION: two different runtimes
  never read each other's state, so they can never kill each other.
- a legacy shared state file is ignored by non-matching runtimes instead of
  triggering a kill.
"""

import importlib

import pytest


@pytest.fixture()
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXO_HOME", str(tmp_path))
    monkeypatch.delenv("NEXO_RUNTIME_PORT", raising=False)
    import paths
    import runtime_service
    importlib.reload(paths)
    importlib.reload(runtime_service)
    yield runtime_service
    importlib.reload(paths)
    importlib.reload(runtime_service)


def test_state_path_embeds_runtime_generation(svc):
    path = svc.service_state_path()
    generation = svc.current_runtime_identity().get("runtime_generation", "unknown")
    token = svc._generation_state_token(generation)
    assert token in path.name, f"state file {path.name} must embed the runtime generation token"


def test_different_generations_get_different_state_paths(svc, monkeypatch):
    path_a = svc.service_state_path()
    monkeypatch.setattr(
        svc,
        "current_runtime_identity",
        lambda: {
            "runtime_version": "9.9.9",
            "runtime_fingerprint": "ff" * 20,
            "runtime_generation": "totally-different-gen",
            "server_path": "/elsewhere/server.py",
        },
    )
    path_b = svc.service_state_path()
    assert path_a != path_b, "two runtime generations must never share a state file"


def test_write_and_read_state_roundtrip_within_generation(svc):
    svc.write_service_state({"pid": 12345, "port": 18999, "url": "http://127.0.0.1:18999/mcp"})
    state = svc.read_service_state()
    assert int(state.get("pid")) == 12345
    assert int(state.get("port")) == 18999
    assert svc.state_matches_current_runtime(state) is True


def test_foreign_legacy_state_is_invisible_not_killable(svc, tmp_path):
    # A legacy SHARED state file written by a different install must simply
    # be invisible to this runtime (its per-generation state does not exist),
    # never interpreted as "stale resident to kill".
    legacy = svc._legacy_service_state_path()
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(
        '{"pid": 99999, "url": "http://127.0.0.1:17872/mcp", '
        '"server_path": "/opt/homebrew/lib/node_modules/nexo-brain/src/server.py", '
        '"runtime_fingerprint": "aaaa", "runtime_generation": "foreign-gen"}',
        encoding="utf-8",
    )
    state = svc.read_service_state()
    assert not state or svc.state_matches_current_runtime(state), (
        "a foreign install's state must never surface as this runtime's resident"
    )


def test_obsolescence_detection_and_connection_count_fail_safe(svc, monkeypatch):
    # Same generation on disk -> never obsolete.
    boot_gen = svc.current_runtime_identity().get("runtime_generation", "unknown")
    assert svc._resident_is_obsolete(boot_gen) is False

    # Connection counting must fail SAFE (None) when tooling is missing.
    monkeypatch.setattr(svc.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("no lsof")))
    assert svc._count_established_connections(17872) is None
