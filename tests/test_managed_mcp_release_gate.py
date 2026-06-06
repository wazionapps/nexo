import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_managed_mcp_lock.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("verify_managed_mcp_lock", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _npm_payload(version="1.2.3"):
    return {
        "name": "chrome-devtools-mcp",
        "dist-tags": {"latest": version},
        "versions": {
            version: {
                "name": "chrome-devtools-mcp",
                "version": version,
                "dist": {
                    "integrity": "sha512-new",
                    "tarball": f"https://registry.npmjs.org/chrome-devtools-mcp/-/chrome-devtools-mcp-{version}.tgz",
                },
                "bin": {
                    "chrome-devtools-mcp": "build/src/bin/chrome-devtools-mcp.js",
                },
                "engines": {"node": ">=20"},
            }
        },
    }


def test_managed_mcp_release_gate_prefers_mcp_bin_when_package_has_multiple_bins():
    module = _load_module()
    payload = {
        "name": "open-computer-use",
        "dist-tags": {"latest": "0.1.52"},
        "versions": {
            "0.1.52": {
                "name": "open-computer-use",
                "version": "0.1.52",
                "dist": {
                    "integrity": "sha512-new",
                    "tarball": "https://registry.npmjs.org/open-computer-use/-/open-computer-use-0.1.52.tgz",
                },
                "bin": {
                    "open-computer-use": "bin/open-computer-use",
                    "open-computer-use-mcp": "bin/open-computer-use-mcp",
                },
            }
        },
    }

    expected = module.expected_lock_fields(payload)

    assert expected["bin"] == "open-computer-use-mcp"


def test_managed_mcp_release_gate_rejects_stale_lock():
    module = _load_module()
    lock = {
        "providers": {
            "chrome-devtools-mcp": {
                "source_type": "npm",
                "package": "chrome-devtools-mcp",
                "version": "1.0.0",
                "integrity": "sha512-old",
                "tarball": "https://registry.npmjs.org/chrome-devtools-mcp/-/chrome-devtools-mcp-1.0.0.tgz",
                "bin": "chrome-devtools-mcp",
            }
        }
    }

    errors, latest = module.check_lock(lock, fetcher=lambda package: _npm_payload("1.2.3"))

    assert latest["chrome-devtools-mcp"]["version"] == "1.2.3"
    assert any("version is '1.0.0'" in error for error in errors)
    assert any("integrity is 'sha512-old'" in error for error in errors)


def test_managed_mcp_release_gate_updates_lock_to_latest(tmp_path):
    module = _load_module()
    lock = {
        "schema": "nexo.managed_mcp.lock.v1",
        "providers": {
            "chrome-devtools-mcp": {
                "source_type": "npm",
                "package": "chrome-devtools-mcp",
                "version": "1.0.0",
                "integrity": "sha512-old",
                "tarball": "https://registry.npmjs.org/chrome-devtools-mcp/-/chrome-devtools-mcp-1.0.0.tgz",
                "bin": "chrome-devtools-mcp",
            }
        },
    }
    lock_path = tmp_path / "lock.json"
    module.write_lock(lock, lock_path)

    errors, latest = module.check_lock(lock, fetcher=lambda package: _npm_payload("1.2.3"))
    assert errors
    updated = module.update_lock_to_latest(lock, latest)
    module.write_lock(updated, lock_path)

    payload = json.loads(lock_path.read_text())
    provider = payload["providers"]["chrome-devtools-mcp"]
    assert provider["version"] == "1.2.3"
    assert provider["integrity"] == "sha512-new"
    assert provider["tarball"].endswith("chrome-devtools-mcp-1.2.3.tgz")
    assert provider["bin"] == "chrome-devtools-mcp"
    assert provider["engines"] == {"node": ">=20"}
