import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIVE_SOURCE = ROOT / "scripts" / "nexo-live-source-preflight.py"
SECRET_VIEW = ROOT / "scripts" / "nexo-safe-secret-view.py"


def run_script(script: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script), *args],
        text=True,
        capture_output=True,
        check=False,
    )


def test_live_source_preflight_requires_complete_matrix(tmp_path):
    matrix = tmp_path / "matrix.json"
    matrix.write_text(json.dumps({"domains": {"wazion": {"repo": "/repo"}}}))
    result = run_script(LIVE_SOURCE, "--domain", "wazion", "--matrix-file", str(matrix))
    assert result.returncode == 2
    assert "missing required field 'branch'" in result.stderr


def test_live_source_preflight_accepts_expected_wazion_matrix():
    result = run_script(
        LIVE_SOURCE,
        "--domain",
        "wazion",
        "--repo",
        "/Users/franciscoc/Documents/_PhpstormProjects/WAzion/WAzion",
        "--path",
        "/Users/franciscoc/Documents/_PhpstormProjects/WAzion/WAzion + /opt/wazion-wa",
        "--branch",
        "main",
        "--credential-or-port",
        "bd-wazion-cloud-sql-proxy/* + gcloud recambiosyaccesoriosbmw/europe-west1-b",
        "--cloud-project",
        "recambiosyaccesoriosbmw",
        "--runtime-environment",
        "Cloud Run chrome-extension-op-google and PM2 on wazion-whatsapp-vps",
        "--table",
        "allinoneapp_main",
        "--minimum-smoke",
        "Cloud Run public health plus gcloud SSH pm2/x-proxy/nexo-alert-state check",
    )
    assert result.returncode == 0, result.stderr
    assert "OK: live-source matrix fixed domain=wazion" in result.stdout


def test_live_source_preflight_accepts_cloudflare_matrix():
    result = run_script(
        LIVE_SOURCE,
        "--domain",
        "cloudflare",
        "--repo",
        "Cloudflare account live API/dashboard",
        "--path",
        "zones/routes/rulesets/workers for the exact domain under diagnosis",
        "--branch",
        "live",
        "--server",
        "Cloudflare API for the target zone",
        "--credential-or-port",
        "cloudflare/* token with zone read scope",
        "--cloud-project",
        "none",
        "--runtime-environment",
        "Cloudflare edge live configuration",
        "--table",
        "zone DNS records, worker routes, rulesets and SSL/TLS settings",
        "--minimum-smoke",
        "query exact zone plus DNS/routes/rulesets and curl affected public URL",
        "--window",
        "current_live_state_plus_explicit_incident_window",
    )
    assert result.returncode == 0, result.stderr
    assert "OK: live-source matrix fixed domain=cloudflare" in result.stdout


def test_live_source_preflight_accepts_billing_matrix():
    result = run_script(
        LIVE_SOURCE,
        "--domain",
        "billing",
        "--repo",
        "/Users/franciscoc/Documents/_PhpstormProjects/nexo-desktop-web",
        "--path",
        "product catalog, pricing config, Stripe/provider dashboard and live DB records",
        "--branch",
        "main",
        "--server",
        "nexo-desktop.com production backend plus provider billing dashboard/API",
        "--credential-or-port",
        "nexo-desktop-web production credentials + provider billing credentials",
        "--cloud-project",
        "none",
        "--runtime-environment",
        "Laravel production billing backend and provider billing surface",
        "--table",
        "plans/prices/subscriptions/invoices/credits ledger",
        "--minimum-smoke",
        "read catalog/config and one live provider/DB record before quoting or diagnosing billing",
        "--window",
        "current_catalog_config_plus_explicit_invoice_window",
    )
    assert result.returncode == 0, result.stderr
    assert "OK: live-source matrix fixed domain=billing" in result.stdout


def test_safe_secret_view_masks_env_values(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=sk-test-secret-value\nDB_PASSWORD=short\n")
    result = run_script(SECRET_VIEW, "--file", str(env_file))
    assert result.returncode == 0, result.stderr
    assert "OPENAI_API_KEY=sk...ue <len=20 sha256=" in result.stdout
    assert "sk-test-secret-value" not in result.stdout
    assert "DB_PASSWORD=<masked len=5 sha256=" in result.stdout


def test_safe_secret_view_blocks_full_read_commands():
    result = run_script(SECRET_VIEW, "--check-command", "cat /srv/app/.env")
    assert result.returncode == 2
    assert "BLOCKED: cat would dump a sensitive file" in result.stderr


def test_safe_secret_view_allows_non_sensitive_cat():
    result = run_script(SECRET_VIEW, "--check-command", "cat README.md")
    assert result.returncode == 0
    assert "OK: command does not match" in result.stdout
