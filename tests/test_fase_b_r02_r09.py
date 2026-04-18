"""Tests for Fase B R02 (credential_create) + R09 (artifact_create) dedup."""
from __future__ import annotations

import importlib
import os
import sys

import pytest

sys.path.insert(
    0,
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")),
)


@pytest.fixture(autouse=True)
def r02_r09_runtime(isolated_db):
    import db._core as db_core
    import db._credentials as db_credentials
    import db
    import tools_credentials
    import plugins.artifact_registry as artifact_registry

    importlib.reload(db_core)
    importlib.reload(db_credentials)
    importlib.reload(db)
    importlib.reload(tools_credentials)
    importlib.reload(artifact_registry)
    yield


# ──────────────────────────────────────────────────────────────────────
# R02 — credential_create exact-match + force
# ──────────────────────────────────────────────────────────────────────


def test_r02_accepts_first_credential():
    from tools_credentials import handle_credential_create
    out = handle_credential_create(service="meta", key="api_key", value="sk-123")
    assert "created" in out.lower()
    assert "ERROR" not in out


def test_r02_rejects_exact_duplicate():
    from tools_credentials import handle_credential_create
    first = handle_credential_create(service="meta", key="api_key", value="sk-123")
    assert "ERROR" not in first
    second = handle_credential_create(service="meta", key="api_key", value="sk-456")
    assert "ERROR" in second
    assert "R02" in second
    assert "force='true'" in second
    assert "already exists" in second


def test_r02_allows_different_key_same_service():
    """Different key within same service is legitimate."""
    from tools_credentials import handle_credential_create
    first = handle_credential_create(service="meta", key="api_key", value="sk-a")
    second = handle_credential_create(service="meta", key="token_live", value="tkn-b")
    assert "ERROR" not in first
    assert "ERROR" not in second


def test_r02_allows_same_key_different_service():
    from tools_credentials import handle_credential_create
    first = handle_credential_create(service="meta", key="api_key", value="sk-a")
    second = handle_credential_create(service="stripe", key="api_key", value="sk-b")
    assert "ERROR" not in first
    assert "ERROR" not in second


def test_r02_force_override_replaces_value():
    from tools_credentials import handle_credential_create, handle_credential_get
    handle_credential_create(service="meta", key="api_key", value="old-value")
    out = handle_credential_create(service="meta", key="api_key", value="new-value", force="true")
    assert "ERROR" not in out
    got = handle_credential_get(service="meta", key="api_key")
    assert "new-value" in got
    assert "old-value" not in got


# ──────────────────────────────────────────────────────────────────────
# R09 — artifact_create dedup
# ──────────────────────────────────────────────────────────────────────


def test_r09_accepts_first_artifact():
    from plugins.artifact_registry import handle_artifact_create
    out = handle_artifact_create(
        kind="service",
        canonical_name="NEXO Dashboard",
        uri="localhost:6174",
        ports='[6174]',
        domain="nexo",
    )
    assert "ERROR" not in out
    assert "created" in out.lower()


def test_r09_rejects_same_canonical_name_same_domain():
    from plugins.artifact_registry import handle_artifact_create
    handle_artifact_create(kind="service", canonical_name="Dashboard", domain="nexo")
    second = handle_artifact_create(kind="service", canonical_name="Dashboard", domain="nexo")
    assert "ERROR" in second
    assert "R09" in second
    assert "same_canonical_name_in_domain" in second


def test_r09_allows_same_name_different_domain():
    """Same canonical_name in different domain is legitimate."""
    from plugins.artifact_registry import handle_artifact_create
    first = handle_artifact_create(kind="service", canonical_name="Dashboard", domain="nexo")
    second = handle_artifact_create(kind="service", canonical_name="Dashboard", domain="project-a")
    assert "ERROR" not in first
    assert "ERROR" not in second


def test_r09_rejects_same_uri_regardless_of_domain():
    """URI collision is domain-independent — only one process can bind a URL."""
    from plugins.artifact_registry import handle_artifact_create
    first = handle_artifact_create(
        kind="service", canonical_name="Foo", uri="localhost:6174", domain="nexo"
    )
    second = handle_artifact_create(
        kind="service", canonical_name="Bar", uri="localhost:6174", domain="project-a"
    )
    assert "ERROR" not in first
    assert "ERROR" in second
    assert "same_uri" in second


def test_r09_rejects_port_collision():
    from plugins.artifact_registry import handle_artifact_create
    first = handle_artifact_create(kind="service", canonical_name="Svc1", ports='[6174]')
    second = handle_artifact_create(kind="service", canonical_name="Svc2", ports='[6174, 7000]')
    assert "ERROR" not in first
    assert "ERROR" in second
    assert "port_collision" in second


def test_r09_rejects_path_collision():
    from plugins.artifact_registry import handle_artifact_create
    first = handle_artifact_create(
        kind="script", canonical_name="ScriptA",
        paths='["/Users/x/nexo/src/foo.py"]',
    )
    second = handle_artifact_create(
        kind="script", canonical_name="ScriptB",
        paths='["/Users/x/nexo/src/foo.py", "/Users/x/nexo/src/bar.py"]',
    )
    assert "ERROR" not in first
    assert "ERROR" in second
    assert "path_collision" in second


def test_r09_force_override_allows_duplicate():
    from plugins.artifact_registry import handle_artifact_create
    handle_artifact_create(kind="service", canonical_name="Dashboard", domain="nexo")
    out = handle_artifact_create(
        kind="service", canonical_name="Dashboard", domain="nexo", force="true"
    )
    assert "ERROR" not in out
