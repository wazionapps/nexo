from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def test_r23g_blocks_secret_env_assignment_visible_to_processes():
    from r23g_secrets_in_output import classify_secret_visibility_risk

    result = classify_secret_visibility_risk(
        "Bash",
        {"command": "OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz curl https://example.com"},
    )

    assert result is not None
    assert result["reason_code"] == "r23g_secret_env_visible"
    assert "sk-proj" not in result["safe_command"]


def test_r23g_blocks_bearer_env_ref_in_argv():
    from r23g_secrets_in_output import classify_secret_visibility_risk

    result = classify_secret_visibility_risk(
        "Bash",
        {"command": 'curl -H "Authorization: Bearer $SHOPIFY_TOKEN" https://example.com'},
    )

    assert result is not None
    assert result["reason_code"] == "r23g_secret_argv_visible"
    assert "process argv" in result["pattern"]


def test_r23g_blocks_ps_env_listing_without_redactor():
    from r23g_secrets_in_output import classify_secret_visibility_risk

    result = classify_secret_visibility_risk("Bash", {"command": "ps auxww | grep TOKEN"})

    assert result is not None
    assert result["reason_code"] == "r23g_process_env_listing"


def test_r23g_allows_safe_secret_view_helper():
    from r23g_secrets_in_output import classify_secret_visibility_risk

    result = classify_secret_visibility_risk(
        "Bash",
        {"command": "python3 scripts/nexo-safe-secret-view.py --file ~/.env.production"},
    )

    assert result is None
