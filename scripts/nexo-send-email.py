#!/usr/bin/env python3
"""
Quick email sender for NEXO progress updates.

Usage:
  nexo-send-email.py <subject> <body> [--to addr] [--cc addr]

SMTP credentials are read from nexo.db (service='smtp').
Expected credential keys: host, port, user, password, from_email, from_name
"""
import argparse
import os
import smtplib
import sqlite3
import sys
from email.mime.text import MIMEText
from email.utils import formataddr, make_msgid
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_SRC = SCRIPT_DIR.parent / "src"
if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))

from email_sent_events import record_sent_email  # noqa: E402


def load_smtp_config():
    """Load SMTP credentials from nexo.db credential store."""
    nexo_home = Path(os.environ.get("NEXO_HOME", os.path.expanduser("~/.nexo"))).expanduser()
    candidates = [
        nexo_home / "runtime" / "data" / "nexo.db",
        nexo_home / "data" / "nexo.db",
    ]
    db_path = next((p for p in candidates if p.exists()), None)
    if db_path is None:
        return None
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT key, value FROM credentials WHERE service = 'smtp'"
        ).fetchall()
        conn.close()
        if not rows:
            return None
        return {r["key"]: r["value"] for r in rows}
    except Exception:
        return None


def send(subject, body, to, cc=None):
    config = load_smtp_config()
    if not config or "host" not in config or "user" not in config:
        print("SMTP not configured, skipping email")
        sys.exit(0)

    from_email = config.get("from_email", config["user"])
    from_name = config.get("from_name", "NEXO")

    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = formataddr((from_name, from_email))
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain=from_email.rsplit("@", 1)[-1] if "@" in from_email else None)

    port = int(config.get("port", "465"))
    smtp = smtplib.SMTP_SSL(config["host"], port)
    smtp.login(config["user"], config.get("password", ""))
    smtp.send_message(msg)
    smtp.quit()
    try:
        record_sent_email(
            message_id=str(msg["Message-ID"] or ""),
            sender=from_email,
            to_addrs=to,
            cc_addrs=cc or "",
            subject=subject,
            source="nexo-send-email",
            body_text=body,
            meta={"script": "scripts/nexo-send-email.py"},
        )
    except Exception as exc:
        print(f"WARN: sent email continuity tracking failed: {exc}", file=sys.stderr)
    print(f"OK — sent to {to}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NEXO quick email sender")
    parser.add_argument("subject", nargs="?", default="NEXO Update", help="Email subject")
    parser.add_argument("body", nargs="?", default="No body", help="Email body text")
    parser.add_argument("--to", required=True, help="Recipient email address")
    parser.add_argument("--cc", default=None, help="CC email address")
    args = parser.parse_args()
    send(args.subject, args.body, args.to, args.cc)
