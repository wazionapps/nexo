#!/usr/bin/env python3
"""
NEXO Immune System — Health monitor & auto-repair.

Runs every 30 minutes via LaunchAgent. Checks tokens, LaunchAgents, DBs,
scripts, logs, disk, and remote server crons. Auto-repairs what it can,
alerts via notification on NEW failures.

Zero external dependencies. Stdlib + sqlite3 + urllib only.
"""

import fcntl
import json
import os
import re
import shlex
import signal
import sqlite3
import ssl
import subprocess
import sys
import time
from datetime import datetime, date, timedelta
from pathlib import Path


try:
    from client_preferences import resolve_user_model as _resolve_user_model
    _USER_MODEL = _resolve_user_model()
except Exception:
    _USER_MODEL = ""

NEXO_HOME = Path(os.environ.get("NEXO_HOME", str(Path.home() / ".nexo")))
_script_dir = Path(__file__).resolve().parent
_repo_src = _script_dir.parent
NEXO_CODE = Path(os.environ.get("NEXO_CODE", str(_repo_src) if (_repo_src / "server.py").exists() else str(NEXO_HOME)))
if str(NEXO_CODE) not in sys.path:
    sys.path.insert(0, str(NEXO_CODE))

from agent_runner import AutomationBackendUnavailableError, run_automation_prompt
from constants import AUTOMATION_SUBPROCESS_TIMEOUT
import paths

from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

# ─── SSL context for macOS (certifi or system certs) ─────────────────────────
def _make_ssl_context():
    """Create an SSL context that works on macOS with Python.org Python."""
    # Try certifi first (pip-installed)
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
        return ctx
    except ImportError:
        pass
    # Try macOS system certificates
    for ca_path in [
        "/etc/ssl/cert.pem",
        "/usr/local/etc/openssl/cert.pem",
        "/usr/local/etc/openssl@3/cert.pem",
        "/opt/homebrew/etc/openssl@3/cert.pem",
    ]:
        if os.path.exists(ca_path):
            ctx = ssl.create_default_context(cafile=ca_path)
            return ctx
    # Last resort: unverified (still better than crashing)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

SSL_CTX = _make_ssl_context()

# ─── Paths ────────────────────────────────────────────────────────────────────
HOME = Path.home()
CLAUDE_DIR = paths.home()
COORD_DIR = paths.coordination_dir()
BRAIN_DIR = paths.brain_dir()
SCRIPTS_DIR = paths.core_scripts_dir()

IMMUNE_STATUS = COORD_DIR / "immune-status.json"
IMMUNE_LOG = COORD_DIR / "immune-log.json"
LOCK_FILE = COORD_DIR / "immune-process.lock"

# Configure your alert script here (optional)
# ALERT_SCRIPT = SCRIPTS_DIR / "my-notify.sh"

CLAUDE_MEM_DB = HOME / ".claude-mem" / "claude-mem.db"

LAUNCH_AGENTS_DIR = HOME / "Library" / "LaunchAgents"
CLAUDE_CLI = HOME / ".local" / "bin" / "claude"

NOW = datetime.now()
TODAY = date.today()

# ─── Config ───────────────────────────────────────────────────────────────────

# Token checks — configure for your services.
# Supported types: file_text (read file, optional test_url), json_field (check for refresh_token),
#                  service_account (check for private_key/client_email), hardcoded (direct URL test)
TOKEN_CHECKS = [
    # Example: uncomment and configure for your services
    # {
    #     "name": "My API",
    #     "path": "~/.nexo/my_api_token.txt",
    #     "type": "file_text",
    #     "test_url": "https://api.example.com/health?token={token}",
    # },
    # {
    #     "name": "My Service Account",
    #     "path": "~/.nexo/service-account.json",
    #     "type": "service_account",
    # },
]

EXPECTED_AGENTS = [
    "com.nexo.immune",
    "com.nexo.sleep",
    "com.nexo.synthesis",
]

# SSH check interval — only every 2 hours, not every 30 min
SSH_CHECK_INTERVAL_HOURS = 2

# Log size thresholds (bytes)
LOG_WARN_SIZE = 10 * 1024 * 1024   # 10 MB
LOG_FAIL_SIZE = 50 * 1024 * 1024   # 50 MB
LOG_TRUNCATE_SIZE = 50 * 1024 * 1024  # 50 MB — auto-truncate threshold

# Disk thresholds (percentage used)
DISK_WARN_PCT = 85
DISK_FAIL_PCT = 95

# Quiet hours — no WhatsApp alerts
QUIET_START = 23  # 23:00
QUIET_END = 7     # 07:00

# Skip execution hours (deep night)
SKIP_START = 0    # 00:00
SKIP_END = 6      # 06:00

# Max entries in immune-log.json
MAX_LOG_ENTRIES = 500

# HTTP timeout for token checks
HTTP_TIMEOUT = 10

# SSH timeout
SSH_TIMEOUT = 15


# ─── Helpers ──────────────────────────────────────────────────────────────────

def load_json(path, default=None):
    if not path.exists():
        return default if default is not None else {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return default if default is not None else {}


def save_json(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def is_quiet_hours():
    """Check if within WhatsApp quiet hours (23:00 - 07:00)."""
    h = NOW.hour
    if QUIET_START > QUIET_END:
        return h >= QUIET_START or h < QUIET_END
    return QUIET_START <= h < QUIET_END


def is_skip_hours():
    """Check if within skip hours (00:00 - 06:00)."""
    return SKIP_START <= NOW.hour < SKIP_END


def send_alert(title, message):
    """Send alert notification if not in quiet hours.

    Configure ALERT_SCRIPT at the top of this file to enable.
    Override this function for custom alerting (email, Slack, etc.).
    """
    if is_quiet_hours():
        print(f"  [QUIET] Suppressed alert: {title}")
        return False
    # Default: log only. Configure ALERT_SCRIPT for active notifications.
    print(f"  [ALERT] {title}: {message}")
    return True


def http_get(url, headers=None, timeout=HTTP_TIMEOUT):
    """Simple HTTP GET, returns (status_code, body) or (0, error_string)."""
    try:
        req = Request(url)
        if headers:
            for k, v in headers.items():
                req.add_header(k, v)
        with urlopen(req, timeout=timeout, context=SSL_CTX) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, body
    except HTTPError as e:
        return e.code, str(e)
    except URLError as e:
        return 0, str(e.reason)
    except Exception as e:
        return 0, str(e)


def run_cmd(cmd, timeout=30):
    """Run a command without invoking a shell. Accepts string or argv list."""
    try:
        argv = shlex.split(cmd) if isinstance(cmd, str) else list(cmd)
        r = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout
        )
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    except Exception as e:
        return -1, "", str(e)


def pid_alive(pid):
    """Check if a PID is still running."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


# ─── Check Functions ──────────────────────────────────────────────────────────

def check_tokens():
    """Check all configured tokens. Returns list of result dicts."""
    results = []

    for tc in TOKEN_CHECKS:
        name = tc["name"]
        result = {"name": name, "status": "OK", "detail": ""}

        try:
            if tc["type"] == "file_text":
                path = Path(tc["path"]).expanduser()
                if not path.exists():
                    result["status"] = "FAIL"
                    result["detail"] = f"Token file missing: {path}"
                else:
                    token = path.read_text().strip()
                    if not token:
                        result["status"] = "FAIL"
                        result["detail"] = "Token file empty"
                    elif "test_url" in tc:
                        url = tc["test_url"].format(token=token)
                        code, body = http_get(url)
                        if code == 200:
                            result["detail"] = "HTTP 200 OK"
                        elif code == 190 or (isinstance(body, str) and "expired" in body.lower()):
                            result["status"] = "FAIL"
                            result["detail"] = f"Token expired (HTTP {code})"
                        else:
                            result["status"] = "FAIL"
                            result["detail"] = f"HTTP {code}: {body[:200]}"

            elif tc["type"] == "json_field":
                path = Path(tc["path"]).expanduser()
                if not path.exists():
                    result["status"] = "FAIL"
                    result["detail"] = f"Token file missing: {path}"
                else:
                    data = load_json(path, default=None)
                    if data is None:
                        result["status"] = "FAIL"
                        result["detail"] = "Invalid JSON"
                    elif "refresh_token" not in data:
                        result["status"] = "FAIL"
                        result["detail"] = "No refresh_token in JSON"
                    else:
                        result["detail"] = "refresh_token present"

            elif tc["type"] == "service_account":
                path = Path(tc["path"]).expanduser()
                if not path.exists():
                    result["status"] = "FAIL"
                    result["detail"] = f"Service account file missing: {path}"
                else:
                    data = load_json(path, default=None)
                    if data is None:
                        result["status"] = "FAIL"
                        result["detail"] = "Invalid JSON"
                    elif "private_key" not in data or "client_email" not in data:
                        result["status"] = "FAIL"
                        result["detail"] = "Missing private_key or client_email"
                    else:
                        result["detail"] = f"SA: {data.get('client_email', '?')[:40]}"

            elif tc["type"] == "hardcoded":
                url = tc["test_url"]
                headers = {tc["header"]: tc["token"]}
                code, body = http_get(url, headers=headers)
                if code == 200:
                    result["detail"] = "HTTP 200 OK"
                elif code == 401:
                    result["status"] = "FAIL"
                    result["detail"] = "Token unauthorized (401)"
                else:
                    result["status"] = "FAIL"
                    result["detail"] = f"HTTP {code}: {body[:200]}"

        except Exception as e:
            result["status"] = "FAIL"
            result["detail"] = f"Exception: {str(e)[:200]}"

        results.append(result)

    return results


def check_launch_agents():
    """Check that expected LaunchAgents are loaded. Auto-repair if not."""
    results = []

    # Get list of loaded agents
    rc, stdout, _ = run_cmd("launchctl list")
    loaded_labels = set()
    if rc == 0:
        for line in stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 3:
                loaded_labels.add(parts[2])

    for agent in EXPECTED_AGENTS:
        result = {"name": agent, "status": "OK", "detail": "", "repaired": False}

        if agent in loaded_labels:
            result["detail"] = "Loaded"
        else:
            # Try auto-repair
            plist = LAUNCH_AGENTS_DIR / f"{agent}.plist"
            if plist.exists():
                rc, out, err = run_cmd(f"launchctl load '{plist}'")
                if rc == 0:
                    result["status"] = "WARN"
                    result["detail"] = f"Was unloaded, auto-loaded successfully"
                    result["repaired"] = True
                else:
                    result["status"] = "FAIL"
                    result["detail"] = f"Unloaded, auto-load failed: {err[:100]}"
            else:
                result["status"] = "FAIL"
                result["detail"] = f"Unloaded, plist not found: {plist}"

        results.append(result)

    return results


def check_databases():
    """Run PRAGMA integrity_check on known databases."""
    results = []

    dbs = [
        ("nexo.db", paths.db_path()),
        ("cognitive.db", paths.data_dir() / "cognitive.db"),
        ("claude-mem.db", CLAUDE_MEM_DB),
    ]

    for name, path in dbs:
        result = {"name": name, "status": "OK", "detail": ""}

        if not path.exists():
            result["status"] = "FAIL"
            result["detail"] = f"File missing: {path}"
        else:
            try:
                conn = sqlite3.connect(str(path), timeout=5)
                cursor = conn.execute("PRAGMA integrity_check")
                check_result = cursor.fetchone()[0]
                conn.close()
                if check_result == "ok":
                    size_mb = path.stat().st_size / (1024 * 1024)
                    result["detail"] = f"Integrity OK ({size_mb:.1f} MB)"
                else:
                    result["status"] = "FAIL"
                    result["detail"] = f"Integrity failed: {check_result[:200]}"
            except Exception as e:
                result["status"] = "FAIL"
                result["detail"] = f"Error: {str(e)[:200]}"

        results.append(result)

    return results


def check_scripts():
    """Check stale lock files."""
    results = []

    # Stale lock files (PID dead)
    lock_files = list(COORD_DIR.glob("*.lock"))
    for lf in lock_files:
        if lf == LOCK_FILE:
            continue  # Skip our own lock
        result = {"name": f"lock:{lf.name}", "status": "OK", "detail": "", "repaired": False}
        try:
            content = lf.read_text().strip()
            if content and content.isdigit():
                pid = int(content)
                if pid_alive(pid):
                    result["detail"] = f"PID {pid} alive"
                else:
                    # Auto-repair: remove stale lock
                    lf.unlink()
                    result["status"] = "WARN"
                    result["detail"] = f"PID {pid} dead — lock removed"
                    result["repaired"] = True
            elif content:
                # Lock file has non-PID content — check if size 0 (normal flock pattern)
                if lf.stat().st_size == 0:
                    result["detail"] = "Empty lock (flock pattern)"
                else:
                    result["detail"] = f"Non-PID content: {content[:50]}"
            else:
                result["detail"] = "Empty lock file"
        except Exception as e:
            result["detail"] = f"Error checking: {e}"
        results.append(result)

    return results


def check_logs():
    """Check log file sizes. Auto-truncate if > 50 MB."""
    results = []

    # JSON logs to check
    json_logs = [
        COORD_DIR / "heartbeat-log.json",
        COORD_DIR / "reflection-log.json",
        COORD_DIR / "immune-log.json",
        COORD_DIR / "ops-board.json",
        COORD_DIR / "messages.json",
    ]

    # Text logs to check
    text_logs = [
        COORD_DIR / "heartbeat-stdout.log",
        COORD_DIR / "heartbeat-stderr.log",
        COORD_DIR / "reflection-stdout.log",
        COORD_DIR / "reflection-stderr.log",
        COORD_DIR / "immune-stdout.log",
        COORD_DIR / "immune-stderr.log",
    ]

    for log_path in json_logs + text_logs:
        if not log_path.exists():
            continue

        result = {"name": log_path.name, "status": "OK", "detail": "", "repaired": False}
        size = log_path.stat().st_size
        size_mb = size / (1024 * 1024)

        if size >= LOG_FAIL_SIZE:
            result["status"] = "FAIL"
            result["detail"] = f"{size_mb:.1f} MB — exceeds {LOG_FAIL_SIZE // (1024*1024)} MB"

            # Auto-truncate
            try:
                if log_path.suffix == ".json":
                    _truncate_json_log(log_path, keep_entries=200)
                else:
                    _truncate_text_log(log_path, keep_lines=1000)
                new_size = log_path.stat().st_size / (1024 * 1024)
                result["detail"] += f" -> truncated to {new_size:.1f} MB"
                result["repaired"] = True
            except Exception as e:
                result["detail"] += f" -> truncate failed: {e}"

        elif size >= LOG_WARN_SIZE:
            result["status"] = "WARN"
            result["detail"] = f"{size_mb:.1f} MB — approaching limit"
        else:
            result["detail"] = f"{size_mb:.2f} MB"

        results.append(result)

    return results


def _truncate_json_log(path, keep_entries=200):
    """Truncate a JSON log file to the last N entries."""
    data = load_json(path, default=[])
    if isinstance(data, list) and len(data) > keep_entries:
        data = data[-keep_entries:]
        save_json(path, data)
    elif isinstance(data, dict):
        # Some logs are dicts with a list value
        for key in data:
            if isinstance(data[key], list) and len(data[key]) > keep_entries:
                data[key] = data[key][-keep_entries:]
        save_json(path, data)


def _truncate_text_log(path, keep_lines=1000):
    """Truncate a text log to the last N lines."""
    lines = path.read_text().splitlines()
    if len(lines) > keep_lines:
        path.write_text("\n".join(lines[-keep_lines:]) + "\n")


def check_disk():
    """Check disk usage via os.statvfs."""
    results = []
    result = {"name": "disk:/", "status": "OK", "detail": ""}

    try:
        st = os.statvfs("/")
        total = st.f_frsize * st.f_blocks
        avail = st.f_frsize * st.f_bavail
        used = total - avail
        pct = (used / total) * 100 if total > 0 else 0

        avail_gb = avail / (1024 ** 3)
        total_gb = total / (1024 ** 3)

        if pct >= DISK_FAIL_PCT:
            result["status"] = "FAIL"
            result["detail"] = f"{pct:.1f}% used ({avail_gb:.1f} GB free of {total_gb:.0f} GB)"
        elif pct >= DISK_WARN_PCT:
            result["status"] = "WARN"
            result["detail"] = f"{pct:.1f}% used ({avail_gb:.1f} GB free of {total_gb:.0f} GB)"
        else:
            result["detail"] = f"{pct:.1f}% used ({avail_gb:.1f} GB free of {total_gb:.0f} GB)"
    except Exception as e:
        result["status"] = "FAIL"
        result["detail"] = f"Error: {e}"

    results.append(result)
    return results


def check_server_crons():
    """Check remote server crons via SSH. Only runs every 2 hours.

    Configure SSH_SERVER_CMD below with your server details if you want
    remote health checks. Leave empty to skip.
    """
    results = []
    result = {"name": "remote-server", "status": "OK", "detail": ""}

    # Configure your SSH health check command here (empty = skip)
    # Example: 'ssh -p 22 user@myserver.example.com "echo OK"'
    SSH_SERVER_CMD = ""

    if not SSH_SERVER_CMD:
        result["detail"] = "No remote server configured (SSH_SERVER_CMD empty)"
        results.append(result)
        return results, False

    # Check if we should run (every 2 hours based on last check)
    status = load_json(IMMUNE_STATUS)
    last_ssh_str = status.get("last_ssh_check", "")
    should_run = True

    if last_ssh_str:
        try:
            last_ssh = datetime.strptime(last_ssh_str, "%Y-%m-%d %H:%M")
            hours_ago = (NOW - last_ssh).total_seconds() / 3600
            if hours_ago < SSH_CHECK_INTERVAL_HOURS:
                result["detail"] = f"Skipped (last check {hours_ago:.1f}h ago, interval {SSH_CHECK_INTERVAL_HOURS}h)"
                should_run = False
        except Exception:
            pass

    if should_run:
        rc, stdout, stderr = run_cmd(SSH_SERVER_CMD, timeout=SSH_TIMEOUT)

        if rc == 0:
            result["detail"] = f"Server OK: {stdout[:100]}"
        else:
            result["status"] = "FAIL"
            err_short = (stderr or "unknown error")[:150]
            result["detail"] = f"SSH failed (rc={rc}): {err_short}"

    results.append(result)
    return results, should_run


# ─── Alerting ─────────────────────────────────────────────────────────────────

def get_system_uptime_minutes():
    """Get system uptime in minutes via sysctl."""
    try:
        r = subprocess.run(
            ["sysctl", "-n", "kern.boottime"],
            capture_output=True, text=True, timeout=5
        )
        if r.returncode == 0:
            # Format: { sec = 1709000000, usec = 0 } ...
            import re as _re
            m = _re.search(r'sec\s*=\s*(\d+)', r.stdout)
            if m:
                boot_ts = int(m.group(1))
                return (time.time() - boot_ts) / 60
    except Exception:
        pass
    return 9999  # Assume long uptime if we can't determine


def detect_new_failures(current_results, previous_status):
    """Compare current results with previous to find NEW failures.

    Includes debounce: SSH/server checks need 2 consecutive failures before alerting.
    Includes boot grace: suppresses all alerts within 10 min of system boot.
    """
    # Boot grace period — suppress alerts when network may still be settling
    uptime = get_system_uptime_minutes()
    if uptime < 10:
        print(f"  [GRACE] System uptime {uptime:.0f}min < 10min — suppressing alerts")
        return []

    prev_checks = {}
    for category in previous_status.get("checks", {}):
        for item in previous_status["checks"][category]:
            key = f"{category}:{item.get('name', '')}"
            prev_checks[key] = item.get("status", "OK")

    # Load consecutive failure counts for debounce
    consec_file = COORD_DIR / "immune-consecutive-failures.json"
    consec = load_json(consec_file, default={})

    new_failures = []
    for category, items in current_results.items():
        for item in items:
            key = f"{category}:{item.get('name', '')}"
            current_status = item.get("status", "OK")
            prev_stat = prev_checks.get(key, "OK")

            if current_status in ("FAIL", "WARN"):
                consec[key] = consec.get(key, 0) + 1
            else:
                consec[key] = 0

            # Debounce: server/SSH checks need 2+ consecutive failures
            is_server_check = category == "server" or "ssh" in key.lower()
            min_consecutive = 2 if is_server_check else 1

            if current_status == "FAIL" and prev_stat != "FAIL":
                if consec.get(key, 0) >= min_consecutive:
                    new_failures.append(item)
            elif current_status == "WARN" and prev_stat == "OK":
                if consec.get(key, 0) >= min_consecutive:
                    new_failures.append(item)

    save_json(consec_file, consec)
    return new_failures


def send_failure_alerts(new_failures):
    """Send WhatsApp alerts for new failures. Max 1 alert per 30 min."""
    if not new_failures:
        return

    # Global alert cooldown — max 1 WhatsApp alert per 30 minutes
    cooldown_file = COORD_DIR / "immune-last-alert.txt"
    if cooldown_file.exists():
        try:
            last_alert = datetime.strptime(cooldown_file.read_text().strip(), "%Y-%m-%d %H:%M")
            minutes_since = (NOW - last_alert).total_seconds() / 60
            if minutes_since < 30:
                print(f"  [COOLDOWN] Last alert {minutes_since:.0f}min ago — suppressing")
                return
        except Exception:
            pass

    fails = [f for f in new_failures if f["status"] == "FAIL"]
    warns = [f for f in new_failures if f["status"] == "WARN"]

    sent = False
    if fails:
        lines = [f"- {f['name']}: {f['detail']}" for f in fails[:5]]
        msg = "\n".join(lines)
        if len(fails) > 5:
            msg += f"\n... +{len(fails) - 5} more"
        sent = send_alert(
            "NEXO Immune FAIL",
            f"{len(fails)} new failure(s):\n{msg}"
        )

    if warns and not fails:
        lines = [f"- {f['name']}: {f['detail']}" for f in warns[:3]]
        msg = "\n".join(lines)
        sent = send_alert(
            "NEXO Immune WARN",
            f"{len(warns)} new warning(s):\n{msg}"
        )

    if sent:
        cooldown_file.write_text(NOW.strftime("%Y-%m-%d %H:%M"))


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{'='*60}")
    print(f"NEXO Immune System — {NOW.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    # Skip hours gate
    if is_skip_hours():
        print(f"[SKIP] Hour {NOW.hour} is within skip range ({SKIP_START}:00-{SKIP_END}:00). Exiting.")
        return

    # Ensure coordination directory exists
    COORD_DIR.mkdir(parents=True, exist_ok=True)

    # Process lock (fcntl)
    lock_fd = None
    try:
        lock_fd = open(LOCK_FILE, "w")
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, OSError):
        print("[LOCKED] Another immune instance is running. Exiting.")
        if lock_fd:
            lock_fd.close()
        return

    try:
        _run_checks(lock_fd)
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()
        except Exception:
            pass


def _run_checks(lock_fd):
    """Execute all checks and produce report."""
    previous_status = load_json(IMMUNE_STATUS)

    all_results = {}
    repairs = []

    # 1. Tokens
    print("\n[1/7] Checking tokens...")
    all_results["tokens"] = check_tokens()
    for r in all_results["tokens"]:
        icon = "OK" if r["status"] == "OK" else r["status"]
        print(f"  [{icon}] {r['name']}: {r['detail']}")

    # 2. LaunchAgents
    print("\n[2/7] Checking LaunchAgents...")
    all_results["agents"] = check_launch_agents()
    for r in all_results["agents"]:
        icon = "OK" if r["status"] == "OK" else r["status"]
        print(f"  [{icon}] {r['name']}: {r['detail']}")
        if r.get("repaired"):
            repairs.append(f"LaunchAgent {r['name']} reloaded")

    # 3. Databases
    print("\n[3/7] Checking databases...")
    all_results["databases"] = check_databases()
    for r in all_results["databases"]:
        icon = "OK" if r["status"] == "OK" else r["status"]
        print(f"  [{icon}] {r['name']}: {r['detail']}")

    # 4. Scripts & locks
    print("\n[4/7] Checking scripts & locks...")
    all_results["scripts"] = check_scripts()
    for r in all_results["scripts"]:
        icon = "OK" if r["status"] == "OK" else r["status"]
        print(f"  [{icon}] {r['name']}: {r['detail']}")
        if r.get("repaired"):
            repairs.append(f"Stale lock {r['name']} removed")

    # 5. Logs
    print("\n[5/7] Checking log sizes...")
    all_results["logs"] = check_logs()
    for r in all_results["logs"]:
        icon = "OK" if r["status"] == "OK" else r["status"]
        print(f"  [{icon}] {r['name']}: {r['detail']}")
        if r.get("repaired"):
            repairs.append(f"Log {r['name']} truncated")

    # 6. Disk
    print("\n[6/7] Checking disk usage...")
    all_results["disk"] = check_disk()
    for r in all_results["disk"]:
        icon = "OK" if r["status"] == "OK" else r["status"]
        print(f"  [{icon}] {r['name']}: {r['detail']}")

    # 7. Server crons
    print("\n[7/7] Checking server crons...")
    server_results, ssh_ran = check_server_crons()
    all_results["server"] = server_results
    for r in all_results["server"]:
        icon = "OK" if r["status"] == "OK" else r["status"]
        print(f"  [{icon}] {r['name']}: {r['detail']}")

    # ─── Summary ──────────────────────────────────────────────────────────
    counts = {"OK": 0, "WARN": 0, "FAIL": 0}
    for category_items in all_results.values():
        for item in category_items:
            s = item.get("status", "OK")
            if s in counts:
                counts[s] += 1

    total = sum(counts.values())

    print(f"\n{'─'*60}")
    print(f"SUMMARY: {total} checks — {counts['OK']} OK, {counts['WARN']} WARN, {counts['FAIL']} FAIL")
    if repairs:
        print(f"AUTO-REPAIRS: {len(repairs)}")
        for r in repairs:
            print(f"  - {r}")
    print(f"{'─'*60}\n")

    # ─── Detect new failures & alert ──────────────────────────────────────
    new_failures = detect_new_failures(all_results, previous_status)
    if new_failures:
        print(f"[ALERT] {len(new_failures)} new failure(s)/warning(s) detected:")
        for nf in new_failures:
            print(f"  - [{nf['status']}] {nf['name']}: {nf['detail']}")
        send_failure_alerts(new_failures)
    else:
        print("[OK] No new failures.")

    # ─── Save status ──────────────────────────────────────────────────────
    status = {
        "last_run": NOW.strftime("%Y-%m-%d %H:%M"),
        "counts": counts,
        "repairs": repairs,
        "new_failures": len(new_failures),
        "checks": all_results,
    }
    if ssh_ran:
        status["last_ssh_check"] = NOW.strftime("%Y-%m-%d %H:%M")
    elif "last_ssh_check" in previous_status:
        status["last_ssh_check"] = previous_status["last_ssh_check"]

    save_json(IMMUNE_STATUS, status)

    # ─── Append to log ────────────────────────────────────────────────────
    log_entry = {
        "ts": NOW.strftime("%Y-%m-%d %H:%M"),
        "ok": counts["OK"],
        "warn": counts["WARN"],
        "fail": counts["FAIL"],
        "repairs": len(repairs),
        "new_failures": len(new_failures),
    }

    log = load_json(IMMUNE_LOG, default=[])
    if not isinstance(log, list):
        log = []
    log.append(log_entry)
    if len(log) > MAX_LOG_ENTRIES:
        log = log[-MAX_LOG_ENTRIES:]
    save_json(IMMUNE_LOG, log)

    print(f"Status saved to {IMMUNE_STATUS}")
    print(f"Log appended to {IMMUNE_LOG} ({len(log)} entries)")

    # ─── Stage B: CLI interpretation (only when issues found) ────────────
    if counts["FAIL"] > 0 or counts["WARN"] > 2 or repairs:
        _run_cli_triage(all_results, repairs, counts)


def _run_cli_triage(all_results: dict, repairs: list, counts: dict):
    """Pass all findings to the configured automation backend for intelligent triage and recommendations."""
    triage_file = COORD_DIR / "immune-triage.md"
    findings_json = json.dumps({
        "timestamp": NOW.strftime("%Y-%m-%d %H:%M"),
        "counts": counts,
        "repairs": repairs,
        "checks": all_results,
    }, indent=2, default=str)

    prompt = f"""You are the NEXO Immune System triage analyst.

Below are the raw health check results from a scheduled scan. Your job:

1. Identify which failures are REAL problems vs transient/expected
2. Group related issues (e.g. SSH failure + server cron failure = same root cause)
3. Prioritize: what needs attention NOW vs can wait
4. For each real issue, suggest a specific remediation action
5. Note any patterns across recent runs if visible

Write a concise triage report to: {triage_file}

Format:
## Immune Triage — YYYY-MM-DD HH:MM

### Critical (act now)
- ...

### Monitor (watch next run)
- ...

### Resolved (auto-repaired)
- ...

### Patterns
- ...

Raw findings:
{findings_json}

Write the report. Be concise — max 40 lines."""

    print("\n[TRIAGE] Running CLI interpretation...")
    try:
        result = run_automation_prompt(
            prompt,
            caller="immune/scan",
            timeout=AUTOMATION_SUBPROCESS_TIMEOUT,
            output_format="text",
            allowed_tools="Read,Write,Edit,Glob,Grep,Bash,mcp__nexo__*",
        )
        if result.returncode == 0:
            print(f"[TRIAGE] Report written to {triage_file}")
        else:
            print(f"[TRIAGE] CLI exited {result.returncode}: {result.stderr[:200]}")
    except AutomationBackendUnavailableError as e:
        print(f"[TRIAGE] Skipping triage: {e}")
    except subprocess.TimeoutExpired:
        print("[TRIAGE] CLI timed out (120s)")
    except Exception as e:
        print(f"[TRIAGE] Error: {e}")


if __name__ == "__main__":
    main()
