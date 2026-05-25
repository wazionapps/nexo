import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


def test_provider_runtime_public_contract_maps_providers_to_clients():
    import provider_runtime

    assert provider_runtime.provider_to_client("anthropic") == "claude_code"
    assert provider_runtime.provider_to_client("openai") == "codex"
    assert provider_runtime.client_to_provider("claude_code") == "anthropic"
    assert provider_runtime.client_to_provider("codex") == "openai"


def test_provider_runtime_rejects_api_key_language_as_provider():
    import provider_runtime

    for value in ("byok", "api_key", "provider_proxy", "openai_key", "connections"):
        assert provider_runtime.normalize_provider_key(value) == ""
